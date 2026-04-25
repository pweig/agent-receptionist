"""
Custom pipeline processors.

- WhisperSTTWithConfidence: subclass of the built-in WhisperSTTService that
  exposes faster-whisper's `language_probability` on every TranscriptionFrame
  (via the `result` field). The built-in service discards the `info` object,
  so downstream processors cannot see STT confidence otherwise.

- HandoffEvaluator: FrameProcessor that runs evaluate_handoff() on every caller
  transcription. On a trigger match it forces a transition to the handoff node.
  This is the "eager handoff" safety net from the Phase 1 brief — it fires even
  when the LLM would not have called transfer_to_human itself.

- LatencyStartMark / LatencyEndMark: paired FrameProcessors that measure
  end-to-end turn latency (TranscriptionFrame → first TTSStartedFrame) and
  append records to logs/latency.jsonl. Place Start after STT, End after TTS.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import AsyncGenerator, Optional

import numpy as np
from pipecat.frames.frames import ErrorFrame, Frame, TranscriptionFrame, TTSStartedFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.whisper.stt import WhisperSTTService
from pipecat.utils.time import time_now_iso8601
from pipecat_flows import FlowManager

from .handoff import evaluate_handoff
from .state import HandoffReason
from .telemetry import append_event, log_llm_turn, log_stt_utterance


# Nodes where an auto-handoff trigger should force a transition. Excludes
# greeting (language not yet set), hours_check (transient), and handoff/closing
# (already terminal).
_HANDOFF_ELIGIBLE_NODES = {
    "intent",
    "collect_info",
    "slot_proposal",
    "confirmation",
    "manage_appointment",
    "reschedule_slot_proposal",
}


class WhisperSTTWithConfidence(WhisperSTTService):
    """WhisperSTTService that forwards language_probability via TranscriptionFrame.result."""

    async def run_stt(self, audio: bytes) -> AsyncGenerator[Frame, None]:
        if not self._model:
            yield ErrorFrame("Whisper model not available")
            return

        await self.start_processing_metrics()

        audio_float = np.frombuffer(audio, dtype=np.int16).astype(np.float32) / 32768.0

        segments, info = await asyncio.to_thread(
            self._model.transcribe, audio_float, language=self._settings.language
        )
        text = ""
        for segment in segments:
            if segment.no_speech_prob < self._settings.no_speech_prob:
                text += f"{segment.text} "

        await self.stop_processing_metrics()

        if text:
            await self._handle_transcription(text, True, self._settings.language)
            yield TranscriptionFrame(
                text,
                self._user_id,
                time_now_iso8601(),
                self._settings.language,
                result={
                    "language_probability": float(info.language_probability),
                    "detected_language": info.language,
                },
            )


class HandoffEvaluator(FrameProcessor):
    """Runs evaluate_handoff() on caller transcriptions; forces handoff node on trigger.

    The flow_manager reference is set after construction because FlowManager
    depends on the PipelineTask, which cannot exist until this processor has
    been placed into the Pipeline. Assign `handoff_eval.flow_manager = fm`
    once both objects exist.
    """

    def __init__(self, on_trigger=None):
        super().__init__()
        self.flow_manager: Optional[FlowManager] = None
        self._on_trigger = on_trigger  # optional callback(reason, text) for logging/testing

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)

        if (
            self.flow_manager is not None
            and isinstance(frame, TranscriptionFrame)
            and frame.text
        ):
            await self._maybe_trigger_handoff(frame)

        await self.push_frame(frame, direction)

    async def _maybe_trigger_handoff(self, frame: TranscriptionFrame) -> None:
        assert self.flow_manager is not None  # guarded by caller
        if self.flow_manager.current_node not in _HANDOFF_ELIGIBLE_NODES:
            return

        lang_prob: Optional[float] = None
        if isinstance(frame.result, dict):
            lang_prob = frame.result.get("language_probability")

        reason: Optional[HandoffReason] = evaluate_handoff(
            self.flow_manager.state, frame.text, lang_prob
        )
        if reason is None:
            return

        self.flow_manager.state["handoff_reason"] = reason.value
        if self._on_trigger is not None:
            self._on_trigger(reason, frame.text)

        append_event(
            log_path=self.flow_manager.state.get("event_log_path"),
            session_id=self.flow_manager.state.get("session_id", "unknown"),
            event="auto_handoff",
            reason=reason.value,
            utterance=frame.text.strip()[:200],
            from_node=self.flow_manager.current_node,
        )

        # Late import avoids a circular dependency between processors and flows.
        from .flows.nodes import create_handoff_node
        await self.flow_manager.set_node_from_config(create_handoff_node())


# ---------------------------------------------------------------------------
# Latency instrumentation
# ---------------------------------------------------------------------------

@dataclass
class LatencyTracker:
    """Shared state between LatencyStartMark and LatencyEndMark.

    Only the most recent turn is tracked. If a new transcription arrives before
    the previous turn has emitted a TTSStartedFrame (e.g. caller barge-in), the
    previous turn is overwritten and its latency record is lost — acceptable
    for barge-in cases since the agent never finished replying.
    """
    session_id: str
    log_path: Path
    turn_idx: int = 0
    turn_start_ns: int = 0
    armed: bool = False  # True between STT end and first TTSStarted
    _turn_text: str = field(default="")


def _append_latency_record(tracker: LatencyTracker, delta_ms: float) -> None:
    append_event(
        log_path=tracker.log_path,
        session_id=tracker.session_id,
        event="turn_latency",
        turn_id=tracker.turn_idx,
        stt_text_preview=tracker._turn_text[:80],
        turn_latency_ms=round(delta_ms, 1),
    )


class LatencyStartMark(FrameProcessor):
    """Marks the end of STT on each caller turn. Place right after STT."""

    def __init__(self, tracker: LatencyTracker):
        super().__init__()
        self._t = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TranscriptionFrame) and frame.text:
            self._t.turn_idx += 1
            self._t.turn_start_ns = time.monotonic_ns()
            self._t._turn_text = frame.text
            self._t.armed = True
            confidence: Optional[float] = None
            if isinstance(frame.result, dict):
                confidence = frame.result.get("language_probability")
            log_stt_utterance(
                log_path=self._t.log_path,
                session_id=self._t.session_id,
                confidence=confidence,
                language=frame.language,
            )
        await self.push_frame(frame, direction)


class LatencyEndMark(FrameProcessor):
    """Records delta when the first TTSStartedFrame follows a tracked turn.

    Place between TTS and transport.output() so the stopwatch stops when
    synthesized audio is about to leave the server.
    """

    def __init__(self, tracker: LatencyTracker):
        super().__init__()
        self._t = tracker

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        if isinstance(frame, TTSStartedFrame) and self._t.armed:
            elapsed_ms = (time.monotonic_ns() - self._t.turn_start_ns) / 1_000_000
            _append_latency_record(self._t, elapsed_ms)
            log_llm_turn(
                log_path=self._t.log_path,
                session_id=self._t.session_id,
                latency_ms=elapsed_ms,
            )
            self._t.armed = False
        await self.push_frame(frame, direction)
