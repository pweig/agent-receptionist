"""
Per-session event logger. Appends JSONL records to logs/events.jsonl so the
evaluation script can derive booking completion rate, handoff accuracy, and
latency percentiles from one file.

Event types currently written:
- turn_latency     — stt_text_preview + turn_latency_ms (from LatencyEndMark)
- auto_handoff     — reason + utterance (from HandoffEvaluator)
- llm_handoff      — reason (from _handle_transfer_to_human)
- booking_done     — confirmation_id + patient_id (from _handle_send_confirmation)
- reschedule_done  — confirmation_id + old_slot + new_slot (from _handle_confirm_reschedule_slot)
- cancel_done      — confirmation_id (from _handle_cancel_appointment)

The log path is set once per session into `flow_manager.state["event_log_path"]`
by `run_bot()` in main.py, along with `state["session_id"]`.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pipecat.utils.time import time_now_iso8601


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
