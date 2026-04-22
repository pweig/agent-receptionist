"""
Phase 0 POC — Dental Office Voice Receptionist
Pipecat pipeline: SmallWebRTC (browser) → VAD → Whisper STT → Groq LLM → Piper TTS → SmallWebRTC

No external accounts needed — runs entirely on localhost.

Usage:
    make dev        # starts on http://localhost:7860
    Open http://localhost:7860 in a browser, click "Start Call"
"""

import asyncio
import os
import uuid
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
from pipecat.services.whisper.stt import WhisperSTTService, Model
from pipecat.transports.base_transport import TransportParams
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.request_handler import (
    SmallWebRTCPatchRequest,
    SmallWebRTCRequest,
    SmallWebRTCRequestHandler,
)
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat_flows import FlowManager

from .flows.nodes import create_get_office_hours_schema, create_greeting_node, create_set_language_schema
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


from contextlib import asynccontextmanager


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


async def run_bot(webrtc_connection: SmallWebRTCConnection) -> None:
    settings = _load_settings()
    session_id = str(uuid.uuid4())

    # Language is determined by OFFICE_LOCALE at startup ("de" or anything else → "en").
    # The TTS voice, initial state, and greeting are all set from this value.
    _locale = os.environ.get("OFFICE_LOCALE", "de").lower()
    lang = "de" if _locale == "de" else "en"

    # --- Transport (SmallWebRTC — no external account needed) ---
    transport = SmallWebRTCTransport(
        webrtc_connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
        ),
    )

    # --- VAD (pipeline processor; not built into SmallWebRTC like it was in Daily) ---
    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(
            params=VADParams(stop_secs=settings["vad"]["stop_secs"])
        )
    )

    # --- STT: faster-whisper. Subclass forwards info.language_probability
    # via TranscriptionFrame.result so HandoffEvaluator can trigger on LOW_STT_CONFIDENCE.
    _model_raw = os.environ.get("WHISPER_MODEL", settings["stt"]["model"])
    _model_key = _model_raw.upper().replace("-", "_")
    _whisper_model_str = Model[_model_key].value
    stt = WhisperSTTWithConfidence(
        settings=WhisperSTTService.Settings(
            model=_whisper_model_str,
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

    # --- Debug observer (always on for POC; remove in production) ---
    debug = DebugFrameLogger()

    # --- Handoff evaluator ---
    # Runs evaluate_handoff() on every caller transcription. On a trigger match
    # (medical question, billing dispute, low STT confidence for 2+ turns, etc.)
    # it forces a transition to the handoff node — the "eager handoff" safety net
    # from Phase 1 of the brief. flow_manager is attached after construction
    # because it depends on PipelineTask.
    def _log_handoff(reason, text: str) -> None:
        print(
            f"{_C['magenta']}[AUTO-HANDOFF] reason={reason.value}  "
            f"text=\"{text[:80]}\"{_C['reset']}",
            flush=True,
        )

    handoff_eval = HandoffEvaluator(on_trigger=_log_handoff)

    # --- Event log (turn_latency + handoffs + completions — writes logs/events.jsonl) ---
    event_log_path = Path(__file__).resolve().parents[2] / "logs" / "events.jsonl"
    latency_tracker = LatencyTracker(
        session_id=session_id,
        log_path=event_log_path,
    )
    latency_start = LatencyStartMark(latency_tracker)
    latency_end = LatencyEndMark(latency_tracker)

    # --- Pipeline ---
    # debug sits after TTS so it sees every downstream frame:
    # VAD events, STT transcriptions, LLM text/tool calls, TTS boundaries.
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

    # --- FlowManager ---
    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        global_functions=[create_set_language_schema(), create_get_office_hours_schema()],
    )
    state = initial_state(session_id)
    state["language"] = lang
    # Expose telemetry hook to node handlers via flow_manager.state.
    state["event_log_path"] = event_log_path
    flow_manager.state.update(state)

    # Now that flow_manager exists, wire the handoff evaluator to it.
    handoff_eval.flow_manager = flow_manager

    # --- Event handlers ---
    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        await flow_manager.initialize(create_greeting_node(lang))

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        await task.cancel()

    # --- Run ---
    # handle_sigint=False: let uvicorn own SIGINT. Multiple runners each
    # installing their own handler fight with uvicorn and block Ctrl+C.
    runner = PipelineRunner(handle_sigint=False)
    await runner.run(task)


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


def main():
    import uvicorn

    port = int(os.environ.get("PORT", 7860))
    print(f"\n  Open http://localhost:{port} in a browser to test the agent.\n")
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
