#!/usr/bin/env python3
"""
1-page call quality summary from logs/events.jsonl.

Usage:
    make metrics
    # or directly:
    python scripts/metrics_report.py [path/to/events.jsonl]
"""

import json
import sys
from collections import defaultdict
from pathlib import Path


def main():
    log_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/events.jsonl")
    if not log_path.exists():
        print(f"No event log found at {log_path}")
        sys.exit(0)

    events = []
    with log_path.open() as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass

    if not events:
        print("Event log is empty.")
        sys.exit(0)

    # Group by type
    by_event: dict[str, list[dict]] = defaultdict(list)
    for e in events:
        by_event[e.get("event", "unknown")].append(e)

    calls_started = len(by_event["call_start"])
    calls_ended = len(by_event["call_end"])
    crashes = len(by_event["crash"])

    # Latency stats
    latencies = [
        e["turn_latency_ms"]
        for e in by_event["turn_latency"]
        if "turn_latency_ms" in e
    ]
    latencies.sort()

    def p(data, pct):
        if not data:
            return float("nan")
        idx = int(len(data) * pct / 100)
        return data[min(idx, len(data) - 1)]

    mean_lat = sum(latencies) / len(latencies) if latencies else float("nan")

    # STT confidence
    confidences = [
        e["confidence"]
        for e in by_event["stt_utterance"]
        if e.get("confidence") is not None
    ]
    confidences.sort()
    mean_conf = sum(confidences) / len(confidences) if confidences else float("nan")
    pct_above_70 = (
        sum(1 for c in confidences if c >= 0.70) / len(confidences) * 100
        if confidences
        else float("nan")
    )

    # Consent stats
    consents = by_event["consent"]
    consent_given = sum(1 for e in consents if e.get("consent_given"))
    consent_declined = sum(1 for e in consents if not e.get("consent_given"))

    # Outcomes
    handoffs = sum(1 for e in by_event["call_end"] if e.get("handoff"))
    bookings = len(by_event["booking_done"])
    reschedules = len(by_event["reschedule_done"])
    cancels = len(by_event["cancel_done"])
    auto_handoffs = len(by_event["auto_handoff"])
    llm_handoffs = len(by_event["llm_handoff"])

    # Histogram buckets for STT confidence
    buckets = [0] * 5  # [0,0.6), [0.6,0.7), [0.7,0.8), [0.8,0.9), [0.9,1.0]
    for c in confidences:
        if c < 0.6:
            buckets[0] += 1
        elif c < 0.7:
            buckets[1] += 1
        elif c < 0.8:
            buckets[2] += 1
        elif c < 0.9:
            buckets[3] += 1
        else:
            buckets[4] += 1

    print("=" * 60)
    print("  CALL QUALITY REPORT")
    print("=" * 60)
    print(f"  Events in log:       {len(events)}")
    print(f"  Calls started:       {calls_started}")
    print(f"  Calls ended:         {calls_ended}")
    print(f"  Crashes:             {crashes}")
    print()
    print("  CONSENT")
    print(f"    Given:             {consent_given}")
    print(f"    Declined:          {consent_declined}")
    print()
    print("  TURN LATENCY  (TranscriptionFrame → TTSStarted)")
    if latencies:
        print(f"    Turns measured:    {len(latencies)}")
        print(f"    Mean:              {mean_lat:.0f} ms")
        print(f"    P50:               {p(latencies, 50):.0f} ms")
        print(f"    P95:               {p(latencies, 95):.0f} ms")
    else:
        print("    No latency data.")
    print()
    print("  STT CONFIDENCE  (language_probability)")
    if confidences:
        print(f"    Utterances:        {len(confidences)}")
        print(f"    Mean confidence:   {mean_conf:.2f}")
        print(f"    ≥ 0.70:            {pct_above_70:.0f}%")
        print(f"    Histogram:")
        labels = ["< 0.60", "0.60–0.69", "0.70–0.79", "0.80–0.89", "0.90+"]
        for label, count in zip(labels, buckets):
            bar = "█" * min(count, 40)
            print(f"      {label}: {bar} {count}")
    else:
        print("    No confidence data.")
    print()
    print("  OUTCOMES")
    print(f"    Bookings:          {bookings}")
    print(f"    Reschedules:       {reschedules}")
    print(f"    Cancellations:     {cancels}")
    print(f"    Handoffs (total):  {handoffs}")
    print(f"      Auto-handoffs:   {auto_handoffs}")
    print(f"      LLM-handoffs:    {llm_handoffs}")
    print("=" * 60)


if __name__ == "__main__":
    main()
