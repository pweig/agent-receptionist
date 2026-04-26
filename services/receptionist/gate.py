"""After-hours entry gate — runs before the LLM pipeline is initialized.

Kept separate from main.py so unit tests can import these helpers without
triggering the heavy pipecat / Whisper / Piper / WebRTC import chain.
"""

import os

from pipecat.frames.frames import EndTaskFrame, Frame, TTSSpeakFrame


def is_enforced() -> bool:
    """True when the entry gate should reject after-hours calls.

    Default true. Set ENFORCE_OFFICE_HOURS=false in dev to bypass and
    exercise the LLM flow regardless of the wall clock.
    """
    return os.environ.get("ENFORCE_OFFICE_HOURS", "true").lower() in ("1", "true", "yes")


def kickoff_frames(text: str) -> list[Frame]:
    """Frames queued when the after-hours gate trips.

    EndTaskFrame (not EndFrame!) is critical: EndTaskFrame is intercepted by
    the pipeline task, which waits for in-flight frames (the TTS audio going
    to the transport) to drain before it pushes EndFrame downstream. A raw
    EndFrame here propagates alongside the audio frames and the transport
    stops outputting before the audio reaches the browser/SIP peer.
    """
    return [TTSSpeakFrame(text), EndTaskFrame()]


def pipeline_params(after_hours: bool) -> dict:
    """Per-call pipeline-builder kwargs based on the gate decision.

    After-hours announcements MUST run with allow_interruptions=False:
    when the caller's mic picks up echo / background noise / them saying
    "hello?" because they don't yet hear the bot, pipecat's default
    interruption logic cancels the in-flight TTS — the announcement
    never reaches them. There is nothing useful the caller can say at
    that point anyway.
    """
    return {"allow_interruptions": not after_hours}
