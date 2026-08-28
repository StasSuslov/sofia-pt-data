#!/usr/bin/env python3
"""
Generate a per-day integrity/coverage manifest for collected GTFS-RT data.

For each <YYYY-MM-DD>.jsonl in a data directory, writes a <YYYY-MM-DD>.manifest.json
with:
  - SHA256 checksums of the data file and its heartbeat log (tamper-evidence —
    anyone re-checksumming a published file can confirm it matches).
  - Poll-level completeness derived from the <date>.polls.jsonl heartbeat log
    written by collect.py: successful / empty / fetch-error polls, and any
    gaps in the heartbeat itself (a real gap means the collector was down —
    an empty poll or a transient fetch error still leaves a heartbeat entry).
  - For days collected before the heartbeat log existed, falls back to
    inferring coverage from the data file's own snapshot_ts values (can only
    see polls that found >=1 vehicle — flagged via "heartbeat_available").

Usage:
    python scripts/generate_manifest.py data/sofia
    python scripts/generate_manifest.py data/sofia --gap-threshold 3
"""

import argparse
import hashlib
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path: Path) -> list[dict]:
    records = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                records.append(json.loads(line))
    return records


def analyze_gaps(timestamps: list[int], gap_threshold_multiplier: float) -> dict:
    """Gap/coverage stats derived purely from a sorted, deduplicated timestamp series."""
    if len(timestamps) < 2:
        return {
            "nominal_interval_sec": None,
            "gap_count": 0,
            "gaps": [],
            "span_seconds": 0,
            "coverage_pct": None,
        }

    deltas = [b - a for a, b in zip(timestamps, timestamps[1:])]
    nominal_interval = statistics.median(deltas)
    threshold = nominal_interval * gap_threshold_multiplier
    gaps = [
        {"after_ts": a, "before_ts": b, "gap_seconds": b - a}
        for a, b in zip(timestamps, timestamps[1:])
        if (b - a) > threshold
    ]
    span_seconds = timestamps[-1] - timestamps[0]
    downtime_seconds = sum(g["gap_seconds"] - nominal_interval for g in gaps)
    coverage_pct = round(100 * (1 - downtime_seconds / span_seconds), 2) if span_seconds > 0 else None

    return {
        "nominal_interval_sec": nominal_interval,
        "gap_count": len(gaps),
        "gaps": gaps,
        "span_seconds": span_seconds,
        "coverage_pct": coverage_pct,
    }


def build_manifest(data_path: Path, gap_threshold_multiplier: float) -> dict:
    polls_path = data_path.with_name(f"{data_path.stem}.polls.jsonl")
    heartbeat_available = polls_path.exists()

    data_records = load_jsonl(data_path)
    data_timestamps = sorted({r["snapshot_ts"] for r in data_records})

    manifest = {
        "date": data_path.stem,
        "data_file": data_path.name,
        "data_sha256": sha256_of(data_path),
        "data_size_bytes": data_path.stat().st_size,
        "total_vehicle_records": len(data_records),
        "heartbeat_available": heartbeat_available,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if heartbeat_available:
        polls = load_jsonl(polls_path)
        ok_polls = [p for p in polls if p["fetch_ok"]]
        error_polls = [p for p in polls if not p["fetch_ok"]]
        empty_polls = [p for p in ok_polls if p["vehicle_count"] == 0]
        heartbeat_timestamps = sorted(p["snapshot_ts"] for p in polls)

        manifest.update({
            "polls_file": polls_path.name,
            "polls_sha256": sha256_of(polls_path),
            "polls_logged": len(polls),
            "successful_polls": len(ok_polls),
            "empty_polls": len(empty_polls),
            "fetch_error_polls": len(error_polls),
        })

        # collect.py may be deployed/restarted mid-day, so the heartbeat log
        # can start well after the data file's first record. Treating the
        # heartbeat's own (short) span as "the day" would silently claim
        # 100% coverage while ignoring the un-instrumented hours before it
        # existed — exactly the false confidence this manifest exists to
        # avoid. Detect that gap and fall back to data-derived timestamps
        # (with an explicit caveat) for the period heartbeat didn't cover.
        heartbeat_start = heartbeat_timestamps[0]
        data_start = data_timestamps[0] if data_timestamps else heartbeat_start
        heartbeat_partial = (heartbeat_start - data_start) > 120
        manifest["heartbeat_partial"] = heartbeat_partial

        if heartbeat_partial:
            manifest["pre_heartbeat_note"] = (
                f"Heartbeat logging only starts at snapshot_ts {heartbeat_start} "
                f"({polls_path.name} was deployed partway through the day). Coverage "
                "before that point is inferred from the data file alone and cannot "
                "distinguish a genuinely empty poll, a fetch error, or real downtime."
            )
            pre_heartbeat_timestamps = [t for t in data_timestamps if t < heartbeat_start]
            timestamps = sorted(set(pre_heartbeat_timestamps) | set(heartbeat_timestamps))
        else:
            timestamps = heartbeat_timestamps
    else:
        # No ground truth for attempted-but-empty/failed polls — the only
        # signal left is which timestamps made it into the data file at all.
        timestamps = data_timestamps
        manifest.update({
            "polls_file": None,
            "polls_sha256": None,
            "polls_logged": None,
            "successful_polls": None,
            "empty_polls": None,
            "fetch_error_polls": None,
        })

    manifest.update(analyze_gaps(timestamps, gap_threshold_multiplier))
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Generate checksummed, gap-annotated coverage manifests for collected GTFS-RT day files"
    )
    parser.add_argument("data_dir", type=Path, help="Directory containing <YYYY-MM-DD>.jsonl files")
    parser.add_argument(
        "--gap-threshold", type=float, default=3.0,
        help="Flag a gap when it exceeds this multiple of the nominal poll interval (default: 3x)",
    )
    args = parser.parse_args()

    day_files = sorted(args.data_dir.glob("????-??-??.jsonl"))
    if not day_files:
        print(f"No day files found in {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    for path in day_files:
        manifest = build_manifest(path, args.gap_threshold)
        manifest_path = path.with_name(f"{path.stem}.manifest.json")
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        if not manifest["heartbeat_available"]:
            hb_note = "  [no heartbeat log — coverage inferred, less reliable]"
        elif manifest["heartbeat_partial"]:
            hb_note = "  [heartbeat started mid-day — coverage before that point is inferred, less reliable]"
        else:
            hb_note = ""
        print(
            f"{path.name}: {manifest['total_vehicle_records']:,} records | "
            f"coverage {manifest['coverage_pct']}% | {manifest['gap_count']} gap(s) | "
            f"sha256={manifest['data_sha256'][:12]}…{hb_note}"
        )


if __name__ == "__main__":
    main()
