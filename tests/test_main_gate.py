"""Unit tests for the after-hours entry gate (services/receptionist/gate.py).

The gate runs before the LLM pipeline is initialized — env-var check plus
the frame list queued at kickoff. The kickoff frame list is the regression
site for the "EndFrame vs EndTaskFrame" bug: a raw EndFrame races the TTS
audio out the transport, so the caller hears nothing of the announcement.
"""
from unittest.mock import patch

from pipecat.frames.frames import EndTaskFrame, TTSSpeakFrame

from services.receptionist.gate import is_enforced, kickoff_frames, pipeline_params

# Aliases under the names the bug-comment text references, so failures
# point readers at the right concept rather than internal helper names.
_after_hours_enforced = is_enforced
_after_hours_frames = kickoff_frames


# ---------------------------------------------------------------------------
# _after_hours_frames — the kickoff frames queued when the gate trips
# ---------------------------------------------------------------------------

def test_after_hours_frames_are_speak_then_end_task():
    frames = _after_hours_frames("Closed. Emergency: 999.")
    assert len(frames) == 2
    assert isinstance(frames[0], TTSSpeakFrame)
    assert frames[0].text == "Closed. Emergency: 999."
    # Critical: must be EndTaskFrame, not EndFrame. EndFrame propagates
    # downstream and stops the WebRTC/SIP transport before the audio drains.
    # EndTaskFrame is intercepted at the task level, which waits for the
    # pipeline to drain before pushing EndFrame on the downstream side.
    assert isinstance(frames[1], EndTaskFrame)


def test_after_hours_frames_passes_message_through_unchanged():
    text = "Multi-sentence message. With punctuation. And digits 1, 2, 3."
    frames = _after_hours_frames(text)
    assert frames[0].text == text


# ---------------------------------------------------------------------------
# _after_hours_enforced — env-var parsing for the gate toggle
# ---------------------------------------------------------------------------

def test_enforce_office_hours_default_true():
    with patch.dict("os.environ", {}, clear=False) as env:
        env.pop("ENFORCE_OFFICE_HOURS", None)
        assert _after_hours_enforced() is True


def test_enforce_office_hours_explicit_true():
    for value in ("true", "True", "TRUE", "1", "yes", "YES"):
        with patch.dict("os.environ", {"ENFORCE_OFFICE_HOURS": value}, clear=False):
            assert _after_hours_enforced() is True, f"Expected True for {value!r}"


def test_enforce_office_hours_explicit_false():
    for value in ("false", "False", "FALSE", "0", "no", "NO", ""):
        with patch.dict("os.environ", {"ENFORCE_OFFICE_HOURS": value}, clear=False):
            assert _after_hours_enforced() is False, f"Expected False for {value!r}"


# ---------------------------------------------------------------------------
# pipeline_params — gate decision must disable interruptions on after-hours
# ---------------------------------------------------------------------------

def test_pipeline_params_after_hours_disables_interruptions():
    """When the gate trips, the pipeline MUST run with allow_interruptions=False.

    Otherwise the caller's mic picks up echo / background noise within the
    first second and pipecat's default interruption logic cancels the
    in-flight TTS — the announcement never reaches them.
    """
    assert pipeline_params(after_hours=True) == {"allow_interruptions": False}


def test_pipeline_params_office_hours_keeps_interruptions():
    """Inside office hours, callers should be able to interrupt the bot
    naturally — that's how a normal phone conversation works."""
    assert pipeline_params(after_hours=False) == {"allow_interruptions": True}
