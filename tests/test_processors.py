"""Unit tests for processors.py — HandoffEvaluator and WhisperSTTWithConfidence."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pipecat.frames.frames import TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection

from services.receptionist.processors import HandoffEvaluator
from services.receptionist.state import HandoffReason


# ---------------------------------------------------------------------------
# Mock FlowManager that records set_node_from_config calls
# ---------------------------------------------------------------------------

class _MockFlowManager:
    def __init__(self, current_node: str = "collect_info"):
        self.state: dict = {}
        self._node = current_node
        self.set_node_from_config = AsyncMock()

    @property
    def current_node(self) -> str:
        return self._node


def _make_frame(text: str, lang_prob: float | None = None) -> TranscriptionFrame:
    result = None
    if lang_prob is not None:
        result = {"language_probability": lang_prob}
    return TranscriptionFrame(text, "user-1", "2026-04-20T10:00:00", None, result=result)


async def _process(proc: HandoffEvaluator, frame: TranscriptionFrame) -> None:
    """StartFrame-free harness: the processor's super().process_frame requires
    __started to be True, but for unit tests we just set it manually."""
    proc._FrameProcessor__started = True  # name-mangled flag
    await proc.process_frame(frame, FrameDirection.DOWNSTREAM)


# ---------------------------------------------------------------------------
# Trigger firing — forces transition to handoff node
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance, expected", [
    ("Can I speak to a human please", HandoffReason.CALLER_REQUESTED),
    ("I think I need antibiotics for this pain", HandoffReason.MEDICAL_QUESTION),
    ("My insurance denied the claim, I want to dispute the invoice", HandoffReason.BILLING_DISPUTE),
])
async def test_trigger_fires_transitions_to_handoff(utterance, expected):
    fm = _MockFlowManager(current_node="collect_info")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame(utterance))

    assert fm.state["handoff_reason"] == expected.value
    fm.set_node_from_config.assert_awaited_once()
    node_arg = fm.set_node_from_config.await_args.args[0]
    assert node_arg.get("name") == "handoff"


async def test_no_trigger_in_greeting_node():
    fm = _MockFlowManager(current_node="greeting")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("I need to speak with a human"))

    fm.set_node_from_config.assert_not_awaited()
    assert "handoff_reason" not in fm.state


async def test_no_trigger_in_hours_check_node():
    fm = _MockFlowManager(current_node="hours_check")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("Can I talk to a person"))

    fm.set_node_from_config.assert_not_awaited()


async def test_no_trigger_in_handoff_node():
    fm = _MockFlowManager(current_node="handoff")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("I want to speak to a human"))

    fm.set_node_from_config.assert_not_awaited()


async def test_trigger_in_intent_node():
    fm = _MockFlowManager(current_node="intent")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("I have symptoms of an infection"))

    assert fm.state["handoff_reason"] == HandoffReason.MEDICAL_QUESTION.value
    fm.set_node_from_config.assert_awaited_once()


async def test_trigger_in_slot_proposal_node():
    fm = _MockFlowManager(current_node="slot_proposal")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("Ich möchte mit einem Mitarbeiter sprechen"))

    assert fm.state["handoff_reason"] == HandoffReason.CALLER_REQUESTED.value


# ---------------------------------------------------------------------------
# Language probability threshold
# ---------------------------------------------------------------------------

async def test_low_language_probability_fires_after_two_turns():
    fm = _MockFlowManager(current_node="collect_info")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    # Turn 1 — below threshold; no trigger yet
    await _process(proc, _make_frame("mumble mumble", lang_prob=0.20))
    fm.set_node_from_config.assert_not_awaited()
    assert fm.state["stt_low_confidence_count"] == 1

    # Turn 2 — below threshold; now it fires
    await _process(proc, _make_frame("umm umm", lang_prob=0.30))
    fm.set_node_from_config.assert_awaited_once()
    assert fm.state["handoff_reason"] == HandoffReason.LOW_STT_CONFIDENCE.value


async def test_high_language_probability_does_not_fire():
    fm = _MockFlowManager(current_node="collect_info")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("I would like an appointment please", lang_prob=0.99))
    await _process(proc, _make_frame("Tomorrow at ten would be great", lang_prob=0.98))

    fm.set_node_from_config.assert_not_awaited()


async def test_confidence_counter_resets_after_good_turn():
    fm = _MockFlowManager(current_node="collect_info")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame("garbled", lang_prob=0.20))
    assert fm.state["stt_low_confidence_count"] == 1
    await _process(proc, _make_frame("My name is Anna Schmidt", lang_prob=0.95))
    assert fm.state["stt_low_confidence_count"] == 0


# ---------------------------------------------------------------------------
# Passthrough and safety
# ---------------------------------------------------------------------------

async def test_processor_without_flow_manager_passes_through():
    # Unbound evaluator must not crash; just a no-op.
    proc = HandoffEvaluator()
    # Deliberately do NOT attach a flow_manager.
    await _process(proc, _make_frame("I want to speak to a human"))
    # If we got here, no exception raised.


async def test_empty_transcription_ignored():
    fm = _MockFlowManager(current_node="collect_info")
    proc = HandoffEvaluator()
    proc.flow_manager = fm

    await _process(proc, _make_frame(""))
    fm.set_node_from_config.assert_not_awaited()


async def test_on_trigger_callback_fired():
    fm = _MockFlowManager(current_node="collect_info")
    calls: list = []
    proc = HandoffEvaluator(on_trigger=lambda reason, text: calls.append((reason, text)))
    proc.flow_manager = fm

    await _process(proc, _make_frame("Please connect me with a real person"))

    assert len(calls) == 1
    assert calls[0][0] == HandoffReason.CALLER_REQUESTED


# ---------------------------------------------------------------------------
# LatencyStartMark / LatencyEndMark
# ---------------------------------------------------------------------------

import json
from pathlib import Path

from pipecat.frames.frames import TTSStartedFrame

from services.receptionist.processors import (
    LatencyEndMark,
    LatencyStartMark,
    LatencyTracker,
)


async def test_latency_records_round_trip(tmp_path: Path):
    log_path = tmp_path / "latency.jsonl"
    tracker = LatencyTracker(session_id="sess-42", log_path=log_path)
    start = LatencyStartMark(tracker)
    end = LatencyEndMark(tracker)

    # Mark StartFrame bookkeeping manually so process_frame accepts.
    start._FrameProcessor__started = True
    end._FrameProcessor__started = True

    await start.process_frame(_make_frame("I would like an appointment"), FrameDirection.DOWNSTREAM)
    assert tracker.armed is True
    assert tracker.turn_idx == 1

    await end.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)
    assert tracker.armed is False

    assert log_path.exists()
    record = json.loads(log_path.read_text().strip())
    assert record["session_id"] == "sess-42"
    assert record["turn_id"] == 1
    assert record["turn_latency_ms"] >= 0
    assert "appointment" in record["stt_text_preview"]


async def test_latency_tts_without_transcription_is_ignored(tmp_path: Path):
    log_path = tmp_path / "latency.jsonl"
    tracker = LatencyTracker(session_id="sess-1", log_path=log_path)
    end = LatencyEndMark(tracker)
    end._FrameProcessor__started = True

    # TTSStartedFrame arriving with no armed tracker must not create a record.
    await end.process_frame(TTSStartedFrame(), FrameDirection.DOWNSTREAM)

    assert not log_path.exists()
