"""Unit tests for telemetry.py — event log helpers."""
from __future__ import annotations

import json
from pathlib import Path

from services.receptionist.telemetry import append_event, log_from_flow_manager


def test_append_event_writes_jsonl(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    append_event(log, "sess-1", "booking_done", confirmation_id="APT-1", patient_id="P001")
    append_event(log, "sess-1", "turn_latency", turn_id=3, turn_latency_ms=812.5)

    lines = log.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    assert first["event"] == "booking_done"
    assert first["session_id"] == "sess-1"
    assert first["confirmation_id"] == "APT-1"
    assert "timestamp" in first

    second = json.loads(lines[1])
    assert second["event"] == "turn_latency"
    assert second["turn_latency_ms"] == 812.5


def test_append_event_creates_parent_dir(tmp_path: Path):
    log = tmp_path / "nested" / "folder" / "events.jsonl"
    append_event(log, "sess-1", "cancel_done", confirmation_id="APT-9")
    assert log.exists()


def test_append_event_null_path_is_noop():
    # No log_path (unit tests / misconfigured session) — must not raise.
    append_event(None, "sess-1", "booking_done", confirmation_id="APT-1")


class _MockFM:
    def __init__(self, log_path, session_id="sess-42"):
        self.state = {"session_id": session_id, "event_log_path": log_path}


def test_log_from_flow_manager(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    fm = _MockFM(log_path=log)
    log_from_flow_manager(fm, "reschedule_done",
                          confirmation_id="APT-1", from_slot="a", to_slot="b")
    record = json.loads(log.read_text().strip())
    assert record["event"] == "reschedule_done"
    assert record["session_id"] == "sess-42"
    assert record["from_slot"] == "a"
    assert record["to_slot"] == "b"


def test_log_from_flow_manager_no_log_path_noop():
    fm = _MockFM(log_path=None)
    log_from_flow_manager(fm, "booking_done", confirmation_id="APT-X")
    # No exception = pass.


def test_log_from_flow_manager_missing_session_uses_default(tmp_path: Path):
    log = tmp_path / "events.jsonl"
    fm = _MockFM(log_path=log)
    fm.state.pop("session_id")
    log_from_flow_manager(fm, "booking_done", confirmation_id="APT-1")
    record = json.loads(log.read_text().strip())
    assert record["session_id"] == "unknown"
