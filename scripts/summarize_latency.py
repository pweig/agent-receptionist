"""
Summarize turn-latency records in logs/latency.jsonl.

Usage:
    python -m scripts.summarize_latency [path-to-jsonl]

Outputs count, P50, P95, min, max in milliseconds.
Paste the P50/P95 numbers into docs/phase0_baseline.json.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path


def main() -> int:
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("logs/latency.jsonl")
    if not path.exists():
        print(f"No latency log found at {path}", file=sys.stderr)
        return 1

    samples: list[float] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            latency = rec.get("turn_latency_ms")
            if isinstance(latency, (int, float)):
                samples.append(float(latency))

    if not samples:
        print(f"No latency samples found in {path}", file=sys.stderr)
        return 1

    samples.sort()
    p50 = statistics.median(samples)
    p95 = samples[min(len(samples) - 1, int(0.95 * len(samples)))]

    print(f"file:    {path}")
    print(f"samples: {len(samples)}")
    print(f"min:     {samples[0]:.1f} ms")
    print(f"p50:     {p50:.1f} ms")
    print(f"p95:     {p95:.1f} ms")
    print(f"max:     {samples[-1]:.1f} ms")
    return 0


if __name__ == "__main__":
    sys.exit(main())
