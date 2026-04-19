"""
Phase 0 POC — Dental Office Voice Receptionist
Pipecat pipeline: Daily WebRTC → Silero VAD → Whisper STT → Claude LLM → Piper TTS → Daily

Usage:
    python -m services.receptionist.main

Or via the Makefile:
    make dev
"""

import asyncio
import os
import uuid
from pathlib import Path

import yaml
from dotenv import load_dotenv

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.openai_llm_context import OpenAILLMContext
from pipecat.services.anthropic.llm import AnthropicLLMService
from pipecat.services.piper.tts import PiperTTSService
from pipecat.services.whisper import WhisperSTTService, Model
from pipecat.transports.services.daily import DailyParams, DailyTransport
from pipecat_flows import FlowManager

from .flows.nodes import build_flow_config
from .state import initial_state
from .tools.schemas import TTS_VOICES

load_dotenv()

_CONFIG_DIR = Path(__file__).parent / "config"


def _load_settings() -> dict:
    with open(_CONFIG_DIR / "settings.yaml") as f:
        return yaml.safe_load(f)


async def run_bot(room_url: str, token: str) -> None:
    settings = _load_settings()
    session_id = str(uuid.uuid4())

    # --- Transport (Daily WebRTC) ---
    transport = DailyTransport(
        room_url,
        token,
        "Lena (Receptionist)",
        params=DailyParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_user_tracks=True,
            # Do NOT set transcription_enabled=True — we use WhisperSTTService instead.
            vad_enabled=True,
            vad_analyzer=SileroVADAnalyzer(
                params=VADParams(stop_secs=settings["vad"]["stop_secs"])
            ),
        ),
    )

    # --- STT: faster-whisper, multilingual (language=None → auto-detect) ---
    stt = WhisperSTTService(
        model=Model.LARGE_V3_TURBO,
        device=settings["stt"]["device"],
        compute_type=settings["stt"]["compute_type"],
        no_speech_prob=settings["stt"]["no_speech_prob"],
    )

    # --- LLM: Claude with prompt caching ---
    llm = AnthropicLLMService(
        api_key=os.environ["ANTHROPIC_API_KEY"],
        model=settings["llm"]["model"],
        max_tokens=settings["llm"]["max_tokens"],
        enable_prompt_caching=settings["llm"]["enable_prompt_caching"],
        params=AnthropicLLMService.InputParams(
            temperature=settings["llm"]["temperature"],
        ),
    )

    # --- TTS: Piper, starts with English; switches to DE via TTSUpdateSettingsFrame ---
    tts = PiperTTSService(
        voice=settings["tts"]["en"]["voice"],
        download_dir=str(Path(__file__).parent / "models" / "piper"),
    )

    # --- LLM context ---
    context = OpenAILLMContext()
    context_aggregator = llm.create_context_aggregator(context)

    # --- Pipeline ---
    pipeline = Pipeline([
        transport.input(),
        stt,
        context_aggregator.user(),
        llm,
        tts,
        transport.output(),
        context_aggregator.assistant(),
    ])

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            allow_interruptions=True,
            enable_metrics=True,
        ),
        idle_timeout_secs=300,  # end call after 5 min of silence
    )

    # --- FlowManager ---
    flow_config = build_flow_config()
    flow_manager = FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        flow_config=flow_config,
    )

    # Store session state on the flow_manager for handler access
    flow_manager.state = initial_state(session_id)

    # --- Event handlers ---

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport, participant):
        await transport.capture_participant_transcription(participant["id"])
        await flow_manager.initialize()

    @transport.event_handler("on_participant_left")
    async def on_participant_left(transport, participant, reason):
        await task.cancel()

    @transport.event_handler("on_call_state_updated")
    async def on_call_state_updated(transport, state):
        if state == "left":
            await task.cancel()

    # --- Run ---
    runner = PipelineRunner()
    await runner.run(task)


def main():
    """Entry point: reads DAILY_ROOM_URL and DAILY_TOKEN from environment."""
    room_url = os.environ.get("DAILY_ROOM_URL", "")
    token = os.environ.get("DAILY_TOKEN", "")

    if not room_url:
        _create_daily_room_and_run()
        return

    asyncio.run(run_bot(room_url, token))


def _create_daily_room_and_run():
    """Create a temporary Daily room via the REST API and print the URL."""
    import aiohttp

    async def _create_and_run():
        api_key = os.environ.get("DAILY_API_KEY", "")
        if not api_key:
            raise SystemExit(
                "Set DAILY_ROOM_URL or DAILY_API_KEY in .env\n"
                "  cp .env.example .env && fill in your keys"
            )

        async with aiohttp.ClientSession() as session:
            async with session.post(
                "https://api.daily.co/v1/rooms",
                headers={"Authorization": f"Bearer {api_key}"},
                json={"properties": {"exp": 3600}},
            ) as resp:
                data = await resp.json()

        room_url = data["url"]
        print(f"\n  Room URL: {room_url}")
        print("  Open the URL in a browser to test the agent.\n")
        await run_bot(room_url, "")

    asyncio.run(_create_and_run())


if __name__ == "__main__":
    main()
