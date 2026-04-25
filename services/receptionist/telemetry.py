"""
Per-session event logger. Appends JSONL records to logs/events.jsonl so the
evaluation script can derive booking completion rate, handoff accuracy, and
latency percentiles from one file.

Event types written:
  call_start      — session_id, caller_id_hash
  call_end        — duration_secs, intent, handoff, tool_errors
  consent         — consent_given, caller_id
  stt_utterance   — confidence, language
  llm_turn        — latency_ms
  turn_latency    — stt_text_preview, turn_latency_ms (legacy; kept for compatibility)
  auto_handoff    — reason, utterance
  llm_handoff     — reason
  booking_done    — confirmation_id, patient_id
  reschedule_done — confirmation_id, old_slot, new_slot
  cancel_done     — confirmation_id
  crash           — exc_type, traceback_hash

The log path is set once per session into `flow_manager.state["event_log_path"]`
by `run_bot()` in main.py, along with `state["session_id"]`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pipecat.utils.time import time_now_iso8601

from .privacy import log_pii_enabled, redact

# Fields whose values are treated as free-form transcript text and must be
# redacted before being written to the event log.
_TRANSCRIPT_FIELDS = {"stt_text_preview", "utterance", "transcript"}


def append_event(
    log_path: Optional[Path],
    session_id: str,
    event: str,
    **payload: Any,
) -> None:
    """Append one event record to the session log. No-op if log_path is None
    (e.g., during unit tests that don't care about telemetry)."""
    if log_path is None:
        return
    if not log_pii_enabled():
        payload = {
            k: redact(v) if k in _TRANSCRIPT_FIELDS and isinstance(v, str) else v
            for k, v in payload.items()
        }
    record = {
        "event": event,
        "session_id": session_id,
        "timestamp": time_now_iso8601(),
        **payload,
    }
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("a") as f:
        f.write(json.dumps(record) + "\n")


def log_from_flow_manager(flow_manager, event: str, **payload: Any) -> None:
    """Convenience wrapper for node handlers: pull session_id + log_path out of
    flow_manager.state and write an event."""
    append_event(
        log_path=flow_manager.state.get("event_log_path"),
        session_id=flow_manager.state.get("session_id", "unknown"),
        event=event,
        **payload,
    )


# ---------------------------------------------------------------------------
# Typed helpers for M2 extended event types
# ---------------------------------------------------------------------------

def log_call_start(
    log_path: Optional[Path],
    session_id: str,
    caller_id: Optional[str] = None,
) -> None:
    import hashlib
    caller_id_hash = (
        hashlib.sha256(caller_id.encode()).hexdigest()[:16] if caller_id else None
    )
    append_event(log_path, session_id, "call_start", caller_id_hash=caller_id_hash)


def log_call_end(
    log_path: Optional[Path],
    session_id: str,
    duration_secs: float,
    intent: Optional[str] = None,
    handoff: bool = False,
    tool_errors: int = 0,
) -> None:
    append_event(
        log_path,
        session_id,
        "call_end",
        duration_secs=round(duration_secs, 1),
        intent=intent,
        handoff=handoff,
        tool_errors=tool_errors,
    )


def log_stt_utterance(
    log_path: Optional[Path],
    session_id: str,
    confidence: Optional[float],
    language: Optional[str],
) -> None:
    append_event(
        log_path,
        session_id,
        "stt_utterance",
        confidence=round(confidence, 3) if confidence is not None else None,
        language=language,
    )


def log_llm_turn(
    log_path: Optional[Path],
    session_id: str,
    latency_ms: float,
) -> None:
    append_event(
        log_path,
        session_id,
        "llm_turn",
        latency_ms=round(latency_ms, 1),
    )
