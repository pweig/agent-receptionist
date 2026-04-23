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
import uuid
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
from .flows.nodes import (
    create_get_office_hours_schema,
    create_greeting_node,
    create_set_language_schema,
)
from .processors import (
    HandoffEvaluator,
    LatencyEndMark,
    LatencyStartMark,
    LatencyTracker,
    WhisperSTTWithConfidence,
)
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
            print(f"{c['green']}[STT ] \"{frame.text}\"  lang={lang}{c['reset']}", flush=True)

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
            allow_interruptions=True,
            enable_metrics=True,
        ),
        idle_timeout_secs=300,
    )

    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        global_functions=[create_set_language_schema(), create_get_office_hours_schema()],
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

    task, flow_manager = await _build_pipeline(transport, session_id, lang)

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await flow_manager.initialize(create_greeting_node(lang))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.cancel()

    # handle_sigint=False: let uvicorn own SIGINT. Multiple runners each
    # installing their own handler fight with uvicorn and block Ctrl+C.
    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


# ---------------------------------------------------------------------------
# SIP path (Asterisk AudioSocket)
# ---------------------------------------------------------------------------


async def run_bot_sip(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
) -> None:
    lang = _resolve_language()
    session_id = str(uuid.uuid4())

    transport = AudioSocketTransport(
        reader,
        writer,
        params=AudioSocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    task, flow_manager = await _build_pipeline(transport, session_id, lang)

    async def _kickoff():
        # The SIP "client" is already on the other end of the TCP socket when
        # we get here — there is no on_client_connected event like on WebRTC.
        # Wait until the pipeline has processed its StartFrame, then push the
        # greeting so Piper actually has a live transport to speak through.
        await transport.wait_until_pipeline_started()
        await flow_manager.initialize(create_greeting_node(lang))

    async def _watch_disconnect():
        await transport.wait_until_disconnected()
        await task.cancel()

    kickoff_task = asyncio.create_task(_kickoff())
    disconnect_task = asyncio.create_task(_watch_disconnect())

    runner = PipelineRunner(handle_sigint=False)
    try:
        await runner.run(task)
    finally:
        for t in (kickoff_task, disconnect_task):
            if not t.done():
                t.cancel()


async def _sip_server_main() -> None:
    host = os.environ.get("SIP_LISTEN_HOST", "0.0.0.0")
    port = int(os.environ.get("SIP_LISTEN_PORT", 8089))

    async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        peer = writer.get_extra_info("peername")
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
