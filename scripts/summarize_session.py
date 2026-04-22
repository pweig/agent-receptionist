"""
Summarize logs/events.jsonl into the baseline metrics needed by
docs/phase0_baseline.json:

- turn latency P50 / P95 / min / max
- booking / reschedule / cancel / handoff counts
- completion rate = (bookings + reschedules + cancellations) / completed_sessions
- handoff rate per session

Usage:
    python -m scripts.summarize_session [path-to-jsonl]
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path


def _percentile(sorted_samples: list[float], p: float) -> float:
    if not sorted_samples:
        return 0.0
    idx = min(len(sorted_samples) - 1, int(p * len(sorted_samples)))
    return sorted_samples[idx]


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/events.jsonl")
    if not path.exists():
        print(f"No event log found at {path}", file=sys.stderr)
        return 1

    latencies: list[float] = []
    # Per-session: which terminal event fired?
    session_outcome: dict[str, str] = {}
    # Auto-handoff reasons / LLM handoff reasons
    auto_handoff_reasons: Counter = Counter()
    llm_handoff_reasons: Counter = Counter()
    sessions: set[str] = set()
    per_session_turns: dict[str, int] = defaultdict(int)

    terminal_events = {"booking_done", "reschedule_done", "cancel_done",
                       "llm_handoff", "auto_handoff"}

    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = rec.get("event")
            session_id = rec.get("session_id", "unknown")
            sessions.add(session_id)

            if event == "turn_latency":
                latency = rec.get("turn_latency_ms")
                if isinstance(latency, (int, float)):
                    latencies.append(float(latency))
                per_session_turns[session_id] += 1

            elif event == "auto_handoff":
                auto_handoff_reasons[rec.get("reason", "unknown")] += 1
                # Only the first terminal event counts per session.
                session_outcome.setdefault(session_id, event)

            elif event == "llm_handoff":
                llm_handoff_reasons[rec.get("reason", "unknown")] += 1
                session_outcome.setdefault(session_id, event)

            elif event in terminal_events:
                session_outcome.setdefault(session_id, event)

    latencies.sort()

    n_sessions = len(sessions)
    outcomes: Counter = Counter(session_outcome.values())
    completed = outcomes["booking_done"] + outcomes["reschedule_done"] + outcomes["cancel_done"]
    handed_off = outcomes["auto_handoff"] + outcomes["llm_handoff"]
    no_outcome = n_sessions - completed - handed_off

    print(f"file:                  {path}")
    print(f"sessions:              {n_sessions}")
    print()
    print("latency (ms):")
    if latencies:
        print(f"  samples:             {len(latencies)}")
        print(f"  min / p50 / p95 / max: "
              f"{latencies[0]:.1f} / {_percentile(latencies, 0.50):.1f} / "
              f"{_percentile(latencies, 0.95):.1f} / {latencies[-1]:.1f}")
    else:
        print("  (no latency samples)")
    print()
    print("outcomes:")
    print(f"  booking_done:        {outcomes['booking_done']}")
    print(f"  reschedule_done:     {outcomes['reschedule_done']}")
    print(f"  cancel_done:         {outcomes['cancel_done']}")
    print(f"  auto_handoff:        {outcomes['auto_handoff']}")
    print(f"  llm_handoff:         {outcomes['llm_handoff']}")
    print(f"  no terminal event:   {no_outcome}  (session dropped / still open)")
    print()

    if n_sessions:
        print("rates (of total sessions):")
        print(f"  completion rate:     {completed / n_sessions:.1%}  "
              f"(booking + reschedule + cancel)")
        print(f"  handoff rate:        {handed_off / n_sessions:.1%}  "
              f"(auto + llm)")

    if auto_handoff_reasons:
        print()
        print("auto_handoff reasons:")
        for reason, count in auto_handoff_reasons.most_common():
            print(f"  {reason:<20} {count}")

    if llm_handoff_reasons:
        print()
        print("llm_handoff reasons:")
        for reason, count in llm_handoff_reasons.most_common():
            print(f"  {reason:<20} {count}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
