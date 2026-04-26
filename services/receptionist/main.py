"""
Dental Office Voice Receptionist — Pipecat pipeline entry point.

Two transports share the same pipeline:

    TRANSPORT=webrtc  (default)  → FastAPI + SmallWebRTC for the browser demo
                                   (``make dev`` → http://localhost:7860)

    TRANSPORT=sip                → asyncio TCP server on :8089 speaking
                                   Asterisk's AudioSocket protocol
                                   (real phone calls via FritzBox + Asterisk)

The middle of the pipeline — VAD, Whisper, LLM, Piper, FlowManager — is built
in ``_build_pipeline()`` and is identical between the two paths.
"""

import asyncio
import os
import time
import traceback
import uuid
import wave
from contextlib import asynccontextmanager
from pathlib import Path

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.frames.frames import (
    Frame,
    FunctionCallInProgressFrame,
    FunctionCallResultFrame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMTextFrame,
    TranscriptionFrame,
    TTSStartedFrame,
    TTSStoppedFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper.stt import Model, WhisperSTTService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat_flows import FlowManager

from .audiosocket_transport import AudioSocketParams, AudioSocketTransport
from .privacy import log_pii_enabled, redact
from .flows.nodes import (
    create_consent_node,
    create_set_language_schema,
)
from .processors import (
    HandoffEvaluator,
    LatencyEndMark,
    LatencyStartMark,
    LatencyTracker,
    WhisperSTTWithConfidence,
)
from .telemetry import log_call_end, log_call_start
from .tools.pms_mock import office_status_now
from .state import initial_state

# ---------------------------------------------------------------------------
# Debug frame logger — prints key pipeline events to stdout
# ---------------------------------------------------------------------------

_C = {          # ANSI colour codes
    "cyan":    "\033[36m",
    "green":   "\033[32m",
    "yellow":  "\033[33m",
    "magenta": "\033[35m",
    "blue":    "\033[34m",
    "reset":   "\033[0m",
}


class DebugFrameLogger(FrameProcessor):
    """Lightweight pipeline observer that prints key events.

    Inserted after TTS so it sees every frame flowing toward transport.output():
    VAD events, transcriptions, LLM text chunks, tool calls, TTS boundaries.
    """

    def __init__(self):
        super().__init__()
        self._llm_buf = ""

    def _check_started(self, frame: Frame) -> bool:
        # Metrics and urgent frames can arrive before StartFrame propagates
        # through the full pipeline. As a passthrough observer we allow all.
        return True

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        # super() handles StartFrame bookkeeping (sets internal __started flag).
        await super().process_frame(frame, direction)

        c = _C

        if isinstance(frame, VADUserStartedSpeakingFrame):
            print(f"{c['cyan']}[VAD ] user started speaking{c['reset']}", flush=True)

        elif isinstance(frame, VADUserStoppedSpeakingFrame):
            print(f"{c['cyan']}[VAD ] user stopped speaking{c['reset']}", flush=True)

        elif isinstance(frame, TranscriptionFrame):
            lang = frame.language or "?"
            if log_pii_enabled():
                stt_text = f'"{frame.text}"'
            else:
                stt_text = f'"{redact(frame.text)}"'
            print(f"{c['green']}[STT ] {stt_text}  lang={lang}{c['reset']}", flush=True)

        elif isinstance(frame, LLMFullResponseStartFrame):
            self._llm_buf = ""
            print(f"{c['yellow']}[LLM ] ", end="", flush=True)

        elif isinstance(frame, LLMTextFrame):
            self._llm_buf += frame.text
            print(frame.text, end="", flush=True)

        elif isinstance(frame, LLMFullResponseEndFrame):
            print(f"{c['reset']}", flush=True)

        elif isinstance(frame, FunctionCallInProgressFrame):
            print(
                f"{c['magenta']}[TOOL] → {frame.function_name}  args={frame.arguments}{c['reset']}",
                flush=True,
            )

        elif isinstance(frame, FunctionCallResultFrame):
            snippet = str(frame.result)[:120]
            print(
                f"{c['magenta']}[TOOL] ← {frame.function_name}  result={snippet}{c['reset']}",
                flush=True,
            )

        elif isinstance(frame, TTSStartedFrame):
            print(f"{c['blue']}[TTS ] speaking...{c['reset']}", flush=True)

        elif isinstance(frame, TTSStoppedFrame):
            print(f"{c['blue']}[TTS ] done{c['reset']}", flush=True)

        await self.push_frame(frame, direction)


load_dotenv()

_CONFIG_DIR = Path(__file__).parent / "config"
_STATIC_DIR = Path(__file__).parent / "static"

_bot_tasks: set[asyncio.Task] = set()

# ---------------------------------------------------------------------------
# Crash-recovery globals
# ---------------------------------------------------------------------------

_FALLBACK_AUDIO_PATH = Path(__file__).parent / "audio" / "fallback_de.wav"
_FALLBACK_PCM: bytes = b""          # raw SLIN16 8 kHz audio, loaded once at startup

# Monotonic timestamps (seconds) of recent SIP pipeline crashes.
_crash_times: list[float] = []
# Flipped to False after >3 crashes in 10 minutes; prevents a crash loop.
_accepting_sip_connections: bool = True

_CRASH_WINDOW_SECS = 600   # 10-minute rolling window
_CRASH_LIMIT = 3           # max crashes before rejecting new calls


def _load_fallback_audio() -> bytes:
    """Load fallback_de.wav and return raw PCM bytes (WAV header stripped).

    Returns empty bytes if the file does not exist yet (before gen-fallback
    has been run). The crash handler degrades gracefully to hangup-only.
    """
    if not _FALLBACK_AUDIO_PATH.exists():
        return b""
    try:
        with wave.open(str(_FALLBACK_AUDIO_PATH), "rb") as wf:
            return wf.readframes(wf.getnframes())
    except Exception as exc:
        print(f"[WARN] Could not load fallback audio: {exc!r}", flush=True)
        return b""


# After-hours entry-gate helpers live in .gate so tests can import them
# without paying the cost of main.py's pipecat / Whisper / Piper imports.
from .gate import is_enforced as _after_hours_enforced
from .gate import kickoff_frames as _after_hours_frames
from .gate import pipeline_params


def _record_crash() -> bool:
    """Record a crash timestamp. Returns True if the crash limit is exceeded."""
    global _accepting_sip_connections
    now = time.monotonic()
    _crash_times.append(now)
    cutoff = now - _CRASH_WINDOW_SECS
    while _crash_times and _crash_times[0] < cutoff:
        _crash_times.pop(0)
    if len(_crash_times) > _CRASH_LIMIT:
        _accepting_sip_connections = False
        print(
            "CRITICAL: >3 pipeline crashes in 10 minutes — "
            "refusing new SIP connections to prevent crash loop.",
            flush=True,
        )
        return True
    return False


# ---------------------------------------------------------------------------
# FastAPI app (WebRTC path)
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app):
    yield
    # Cancel all active bot tasks on shutdown so Ctrl+C exits cleanly
    for t in list(_bot_tasks):
        t.cancel()
    if _bot_tasks:
        await asyncio.gather(*_bot_tasks, return_exceptions=True)


app = FastAPI(lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_webrtc_handler = SmallWebRTCRequestHandler()


def _load_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


def _resolve_language() -> str:
    """Language is pinned at startup via OFFICE_LOCALE ("de" default)."""
    locale = os.environ.get("OFFICE_LOCALE", "de").lower()
    return "de" if locale == "de" else "en"


# ---------------------------------------------------------------------------
# Shared pipeline builder — transport-agnostic
# ---------------------------------------------------------------------------


async def _build_pipeline(
    transport: BaseTransport,
    session_id: str,
    lang: str,
    *,
    allow_interruptions: bool = True,
) -> tuple[PipelineTask, FlowManager]:
    """Assemble the receptionist pipeline around a given transport.

    Returns the PipelineTask (pass to a PipelineRunner) and the FlowManager
    (call ``initialize()`` on it once the transport is ready).
    """
    settings = _load_settings()

    # --- VAD ---
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(stop_secs=settings["vad"]["stop_secs"])
        )
    )

    # --- STT: faster-whisper with language-probability forwarding ---
    _model_raw = os.environ.get("WHISPER_MODEL", settings["stt"]["model"])
    _model_key = _model_raw.upper().replace("-", "_")
    _whisper_model_str = Model[_model_key].value
    # Lock Whisper to the call's configured language (from OFFICE_LOCALE).
    # Auto-detection on 8 kHz phone audio is unreliable — a caller saying "Ich
    # möchte einen Termin" gets mis-detected as English and the word "Termin"
    # comes out as "termine" / "Kermin". Pinning the language is a free win
    # because the locale is already fixed at startup.
    whisper_language = "de" if lang == "de" else "en"
    stt = WhisperSTTWithConfidence(
        settings=WhisperSTTService.Settings(
            model=_whisper_model_str,
            language=whisper_language,
            no_speech_prob=settings["stt"]["no_speech_prob"],
        ),
        device=settings["stt"]["device"],
        compute_type=settings["stt"]["compute_type"],
    )

    # --- LLM: OpenAI-compatible endpoint (Groq or local Ollama) ---
    # Ollama doesn't need a real key; fall back to "ollama" so the env var stays optional.
    llm = OpenAILLMService(
        api_key=os.environ.get("GROQ_API_KEY", "ollama"),
        base_url=settings["llm"]["base_url"],
        settings=OpenAILLMService.Settings(
            model=settings["llm"]["model"],
            temperature=settings["llm"]["temperature"],
            max_tokens=settings["llm"]["max_tokens"],
        ),
    )

    # --- TTS: Piper — voice chosen from OFFICE_LOCALE at startup ---
    tts = PiperTTSService(
        settings=PiperTTSService.Settings(voice=settings["tts"][lang]["voice"]),
        download_dir=Path(__file__).parent / "models" / "piper",
    )

    # --- LLM context ---
    context = LLMContext()
    context_aggregator = LLMContextAggregatorPair(context)

    # --- Debug observer ---
    debug = DebugFrameLogger()

    # --- Handoff evaluator (flow_manager attached after construction) ---
    def _log_handoff(reason, text: str) -> None:
        print(
            f"{_C['magenta']}[AUTO-HANDOFF] reason={reason.value}  "
            f"text=\"{text[:80]}\"{_C['reset']}",
            flush=True,
        )

    handoff_eval = HandoffEvaluator(on_trigger=_log_handoff)

    # --- Event log (turn latency, handoffs, completions → logs/events.jsonl) ---
    event_log_path = Path(__file__).resolve().parents[2] / "logs" / "events.jsonl"
    latency_tracker = LatencyTracker(
        session_id=session_id,
        log_path=event_log_path,
    )
    latency_start = LatencyStartMark(latency_tracker)
    latency_end = LatencyEndMark(latency_tracker)

    pipeline = Pipeline([
        transport.input(),
        vad,
        stt,
        latency_start,
        handoff_eval,
        context_aggregator.user(),
        llm,
        tts,
        latency_end,
        debug,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=allow_interruptions,
            enable_metrics=True,
        ),
        idle_timeout_secs=300,
    )

    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        global_functions=[create_set_language_schema()],
    )
    state = initial_state(session_id)
    state["language"] = lang
    state["event_log_path"] = event_log_path
    flow_manager.state.update(state)

    handoff_eval.flow_manager = flow_manager

    return task, flow_manager


# ---------------------------------------------------------------------------
# WebRTC path (browser demo)
# ---------------------------------------------------------------------------


async def run_bot(webrtc_connection: SmallWebRTCConnection) -> None:
    lang = _resolve_language()
    session_id = str(uuid.uuid4())

    transport = SmallWebRTCTransport(
        webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    after_hours_text: str | None = None
    if _after_hours_enforced():
        status = office_status_now()
        if not status["open"]:
            after_hours_text = status["message"]

    task, flow_manager = await _build_pipeline(
        transport, session_id, lang,
        **pipeline_params(after_hours_text is not None),
    )
    event_log_path = flow_manager.state.get("event_log_path")

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        if after_hours_text is not None:
            # Skip the LLM flow entirely — speak the prepared announcement and end.
            await task.queue_frames(_after_hours_frames(after_hours_text))
        else:
            await flow_manager.initialize(create_consent_node(lang))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.cancel()

    log_call_start(event_log_path, session_id)
    call_start_mono = time.monotonic()

    # handle_sigint=False: let uvicorn own SIGINT. Multiple runners each
    # installing their own handler fight with uvicorn and block Ctrl+C.
    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    finally:
        log_call_end(
            event_log_path,
            session_id,
            duration_secs=time.monotonic() - call_start_mono,
            intent="after_hours_block" if after_hours_text else flow_manager.state.get("intent"),
            handoff=flow_manager.state.get("handoff_reason") is not None,
            tool_errors=flow_manager.state.get("tool_error_count", 0),
        )


# ---------------------------------------------------------------------------
# SIP path (Asterisk AudioSocket)
# ---------------------------------------------------------------------------


async def run_bot_sip(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    lang = _resolve_language()
    session_id = str(uuid.uuid4())

    capture_path = None
    if os.environ.get("CAPTURE_CALLS", "false").lower() in ("1", "true", "yes"):
        captures_dir = Path(__file__).resolve().parents[2] / "logs" / "captures"
        captures_dir.mkdir(parents=True, exist_ok=True)
        capture_path = str(captures_dir / f"session_{session_id}.raw")

    transport = AudioSocketTransport(
        reader,
        writer,
        params=AudioSocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            capture_path=capture_path,
        ),
    )

    after_hours_text: str | None = None
    if _after_hours_enforced():
        status = office_status_now()
        if not status["open"]:
            after_hours_text = status["message"]
            print(f"[GATE] after-hours: session {session_id} (lang={lang})", flush=True)

    # See run_bot above for why interruptions are disabled when the gate trips.
    task, flow_manager = await _build_pipeline(
        transport, session_id, lang,
        **pipeline_params(after_hours_text is not None),
    )

    async def _kickoff():
        # The SIP "client" is already on the other end of the TCP socket when
        # we get here — there is no on_client_connected event like on WebRTC.
        # Wait until the pipeline has processed its StartFrame, then push the
        # greeting so Piper actually has a live transport to speak through.
        await transport.wait_until_pipeline_started()
        if after_hours_text is not None:
            await task.queue_frames(_after_hours_frames(after_hours_text))
        else:
            await flow_manager.initialize(create_consent_node(lang))

    async def _watch_disconnect():
        await transport.wait_until_disconnected()
        await task.cancel()

    kickoff_task = asyncio.create_task(_kickoff())
    disconnect_task = asyncio.create_task(_watch_disconnect())

    event_log_path = flow_manager.state.get("event_log_path")
    log_call_start(event_log_path, session_id)
    call_start_mono = time.monotonic()

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        tb_hash = hash(traceback.format_exc()) & 0xFFFFFFFF
        print(
            f"[CRASH] session={session_id} exc={exc!r} tb_hash={tb_hash:#010x}",
            flush=True,
        )
        traceback.print_exc()
        from .telemetry import append_event
        append_event(
            log_path=event_log_path,
            session_id=session_id,
            event="crash",
            exc_type=type(exc).__name__,
            traceback_hash=f"{tb_hash:#010x}",
        )
        _record_crash()
        if _FALLBACK_PCM:
            await transport.play_fallback_and_hangup(_FALLBACK_PCM)
    finally:
        log_call_end(
            event_log_path,
            session_id,
            duration_secs=time.monotonic() - call_start_mono,
            intent="after_hours_block" if after_hours_text else flow_manager.state.get("intent"),
            handoff=flow_manager.state.get("handoff_reason") is not None,
            tool_errors=flow_manager.state.get("tool_error_count", 0),
        )
        for t in (kickoff_task, disconnect_task):
            if not t.done():
                t.cancel()


async def _sip_server_main() -> None:
    global _FALLBACK_PCM
    _FALLBACK_PCM = _load_fallback_audio()
    if _FALLBACK_PCM:
        print(
            f"  Fallback audio loaded: {len(_FALLBACK_PCM)} bytes "
            f"({len(_FALLBACK_PCM) / 16000:.1f}s at 8 kHz)",
            flush=True,
        )
    else:
        print(
            "  [WARN] fallback_de.wav not found — run 'make gen-fallback' to generate it.",
            flush=True,
        )
    # Listen on both IPv4 and IPv6: host.docker.internal can resolve to either
    # depending on Docker Desktop's DNS query order. If we only bind 0.0.0.0
    # (IPv4) and Asterisk's AudioSocket app connects via IPv6, the connection
    # is silently refused and the caller hears dead air.
    host = os.environ.get("SIP_LISTEN_HOST", "")  # "" → all interfaces, both families
    port = int(os.environ.get("SIP_LISTEN_PORT", 8089))

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
        if not _accepting_sip_connections:
            print(
                f"[AudioSocket] rejecting {peer} — crash limit reached; restart the process.",
                flush=True,
            )
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            return
        print(f"[AudioSocket] connection from {peer}", flush=True)
        bot_task = asyncio.create_task(run_bot_sip(reader, writer))
        _bot_tasks.add(bot_task)
        bot_task.add_done_callback(_bot_tasks.discard)
        try:
            await bot_task
        except Exception as exc:
            print(f"[AudioSocket] pipeline error: {exc!r}", flush=True)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            print(f"[AudioSocket] connection from {peer} closed", flush=True)

    server = await asyncio.start_server(_handle, host=host, port=port)
    print(f"  AudioSocket listening on {host}:{port}\n", flush=True)
    async with server:
        await server.serve_forever()


# ---------------------------------------------------------------------------
# FastAPI routes — WebRTC signaling
# ---------------------------------------------------------------------------


@app.post("/api/offer")
async def offer(request: SmallWebRTCRequest):
    async def _start_bot(connection: SmallWebRTCConnection):
        task = asyncio.create_task(run_bot(connection))
        _bot_tasks.add(task)
        task.add_done_callback(_bot_tasks.discard)

    return await _webrtc_handler.handle_web_request(
        request=request,
        webrtc_connection_callback=_start_bot,
    )


@app.patch("/api/offer")
async def ice_candidate(request: SmallWebRTCPatchRequest):
    await _webrtc_handler.handle_patch_request(request)
    return {"status": "success"}


@app.get("/")
async def index():
    return HTMLResponse((_STATIC_DIR / "index.html").read_text())


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main():
    transport_mode = os.environ.get("TRANSPORT", "webrtc").lower()

    if transport_mode == "sip":
        print("  Starting in SIP / AudioSocket mode.", flush=True)
        asyncio.run(_sip_server_main())
        return

    import uvicorn

    port = int(os.environ.get("PORT", 7860))
    print(f"\n  Open http://localhost:{port} in a browser to test the agent.\n")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
