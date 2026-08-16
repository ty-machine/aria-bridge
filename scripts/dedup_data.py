#!/usr/bin/env python3
"""
One-off maintenance: rewrite readings.jsonl/readings.csv with duplicate
measurements removed, and regenerate latest.json / latest_<person>.json
from the deduped data.

Needed because aria_interceptor.py didn't dedup resent measurements
until 2026-08-16 (the scale resends its whole local cache on every
upload) -- this cleans up what accumulated on disk before that fix.
Going forward the running server prevents new duplicates on its own;
this script doesn't need to be run regularly.

Uses the same dedup key as aria_interceptor.py: (mac, user_id,
timestamp, weight_g). Backs up the existing files before writing.
Rewrites readings.jsonl sorted by the measurement's own scale-side
timestamp (previously it was in server-arrival order) since that's more
useful for a cleaned-up historical log.

Run with the service stopped, as the same user that owns the data dir:
    ARIA_DATA_DIR=/opt/aria_bridge/data python3 scripts/dedup_data.py
"""
import csv
import json
import os
import shutil
import time
from pathlib import Path

DATA_DIR = Path(os.environ.get("ARIA_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
JSONL_LOG = DATA_DIR / "readings.jsonl"
CSV_LOG = DATA_DIR / "readings.csv"
LATEST_FILE = DATA_DIR / "latest.json"


def measurement_key(record: dict) -> tuple:
    return (record["mac"], record["user_id"], record["timestamp"], record["weight_g"])


def main():
    if not JSONL_LOG.exists():
        print(f"No {JSONL_LOG} found, nothing to do.")
        return

    records = []
    with open(JSONL_LOG) as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    print(f"Read {len(records)} total records from {JSONL_LOG}.")

    seen = set()
    deduped = []
    for r in records:
        key = measurement_key(r)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)

    removed = len(records) - len(deduped)
    print(f"Removed {removed} duplicate(s); keeping {len(deduped)} unique measurement(s).")
    if removed == 0:
        print("Nothing to clean up.")
        return

    backup_dir = DATA_DIR / f"pre_dedup_backup_{time.strftime('%Y%m%d_%H%M%S')}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for f in [JSONL_LOG, CSV_LOG, LATEST_FILE, *DATA_DIR.glob("latest_*.json")]:
        if f.exists():
            shutil.copy2(f, backup_dir / f.name)
    print(f"Backed up existing files to {backup_dir}")

    deduped.sort(key=lambda r: r["timestamp"])

    with open(JSONL_LOG, "w") as f:
        for r in deduped:
            f.write(json.dumps(r) + "\n")

    fieldnames = list(deduped[0].keys())
    with open(CSV_LOG, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(deduped)

    overall_latest = max(deduped, key=lambda r: r["timestamp"])
    LATEST_FILE.write_text(json.dumps(overall_latest, indent=2))

    by_person = {}
    for r in deduped:
        p = r.get("person", "unknown")
        if p not in by_person or r["timestamp"] > by_person[p]["timestamp"]:
            by_person[p] = r
    for person, r in by_person.items():
        (DATA_DIR / f"latest_{person}.json").write_text(json.dumps(r, indent=2))
    print(f"Rewrote latest.json and latest_<person>.json for: {', '.join(sorted(by_person))}")

    print("Done.")


if __name__ == "__main__":
    main()
