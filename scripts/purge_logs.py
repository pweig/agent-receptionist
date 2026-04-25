#!/usr/bin/env python3
"""
Log retention purge script.

Reads LOG_RETENTION_DAYS (default 30) and CAPTURE_RETENTION_DAYS (default 7)
from the environment and removes stale records / files.

  events.jsonl  — lines older than LOG_RETENTION_DAYS are filtered out in-place.
  logs/captures/*.raw  — files with mtime older than CAPTURE_RETENTION_DAYS are deleted.

Usage:
    make purge-old-logs
    # or directly:
    LOG_RETENTION_DAYS=14 python scripts/purge_logs.py
"""

import json
import os
import sys
import time
from pathlib import Path


def main():
    repo_root = Path(__file__).resolve().parents[1]
    logs_dir = repo_root / "logs"
    events_path = logs_dir / "events.jsonl"
    captures_dir = logs_dir / "captures"

    log_retention_days = int(os.environ.get("LOG_RETENTION_DAYS", 30))
    capture_retention_days = int(os.environ.get("CAPTURE_RETENTION_DAYS", 7))

    now = time.time()
    log_cutoff = now - log_retention_days * 86400
    capture_cutoff = now - capture_retention_days * 86400

    # --- Purge old lines from events.jsonl ---
    if events_path.exists():
        kept = []
        removed = 0
        with events_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                    ts_str = record.get("timestamp", "")
                    if ts_str:
                        from datetime import datetime, timezone
                        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                        ts_epoch = ts.timestamp()
                        if ts_epoch < log_cutoff:
                            removed += 1
                            continue
                except (json.JSONDecodeError, ValueError):
                    pass  # keep malformed lines to avoid data loss
                kept.append(line)
        with events_path.open("w") as f:
            for line in kept:
                f.write(line + "\n")
        print(
            f"events.jsonl: kept {len(kept)} lines, removed {removed} "
            f"(older than {log_retention_days} days)"
        )
    else:
        print(f"events.jsonl not found at {events_path} — nothing to purge.")

    # --- Delete stale capture files ---
    if captures_dir.exists():
        deleted = 0
        for raw_file in captures_dir.glob("*.raw"):
            if raw_file.stat().st_mtime < capture_cutoff:
                raw_file.unlink()
                deleted += 1
                print(f"  deleted {raw_file.name}")
        print(
            f"captures/: deleted {deleted} .raw files "
            f"(older than {capture_retention_days} days)"
        )
    else:
        print("logs/captures/ does not exist — nothing to purge.")


if __name__ == "__main__":
    main()
