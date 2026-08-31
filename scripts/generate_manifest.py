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
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# config.py at the repo root is the single source of truth for the poll
# cadence collect.py actually runs at. Importing collect.py itself would
# pull in requests and the protobuf bindings, which this file never needs.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DEFAULT_INTERVAL_SEC, DEFAULT_TIMEZONE  # noqa: E402


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def load_jsonl(path: Path) -> tuple[list[dict], int]:
    """
    Parse a newline-delimited JSON file, tolerating a malformed line instead
    of crashing on it. Returns (records, malformed_line_count).

    A torn trailing line is routine here, not exotic — scripts/fetch_data.sh
    can run via scheduled_fetch.sh every couple of hours while collect.py is
    still actively appending to the file being pulled, so an rsync can land
    mid-write. A manifest generator whose whole purpose is detecting
    corruption should report corruption, not die on it or silently drop it
    without a trace — the malformed count is surfaced in the manifest by
    build_manifest() below.
    """
    records = []
    malformed = 0
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                malformed += 1
    return records, malformed


def day_bounds(date_str: str, tz: ZoneInfo, now: datetime | None = None) -> tuple[int, int, bool]:
    """
    UTC epoch seconds for the local midnight-to-midnight span of `date_str`
    in `tz`, so they compare directly against snapshot_ts (UTC epoch).

    For the current in-progress local day, day_end is clamped to `now` and
    day_in_progress comes back True. Note for callers: a local rsync copy of
    *today's* file also lags the VPS by up to the fetch interval (see
    scripts/fetch_data.sh) — low coverage for today alone doesn't necessarily
    mean the collector is down, it may just mean the pull hasn't run yet.
    """
    local_date = date.fromisoformat(date_str)
    day_start = datetime(local_date.year, local_date.month, local_date.day, tzinfo=tz)
    day_end = day_start + timedelta(days=1)
    now = now or datetime.now(timezone.utc)

    day_in_progress = now < day_end
    if day_in_progress:
        day_end = now

    return int(day_start.timestamp()), int(day_end.timestamp()), day_in_progress


def analyze_gaps(
    timestamps: list[int],
    nominal_interval_sec: int,
    gap_threshold_multiplier: float,
    day_start_ts: int,
    day_end_ts: int,
) -> dict:
    """
    Coverage measured against calendar-day boundaries and the *configured*
    poll interval, never the interval observed in the data. A collector that
    quietly degrades from 45s to 90s polls would otherwise recalibrate its
    own "normal" from the degraded data and report ~0 gaps / ~100% coverage
    — see CLAUDE.md section 9, the coverage_pct defect this rewrite fixes.
    """
    day_seconds = day_end_ts - day_start_ts
    threshold = nominal_interval_sec * gap_threshold_multiplier

    # Diagnostic only, not used for coverage math: how far the actually
    # observed spacing between polls has drifted from nominal_interval_sec.
    observed_interval_sec = (
        statistics.median(b - a for a, b in zip(timestamps, timestamps[1:]))
        if len(timestamps) >= 2 else None
    )

    # Day boundaries count as poll opportunities too: a gap between
    # day_start and the first record, or the last record and day_end, is
    # exactly as real as one in the middle — and was invisible before. This
    # is precisely how a mid-day collector start hid as "97% coverage".
    bounded = [day_start_ts, *timestamps, day_end_ts]
    gaps = [
        {"after_ts": a, "before_ts": b, "gap_seconds": b - a}
        for a, b in zip(bounded, bounded[1:])
        if (b - a) > threshold
    ]

    expected_polls = (
        day_seconds / nominal_interval_sec if nominal_interval_sec and day_seconds > 0 else None
    )
    coverage_pct = (
        round(100 * len(timestamps) / expected_polls, 2) if expected_polls else None
    )

    return {
        "nominal_interval_sec": nominal_interval_sec,
        "observed_interval_sec": observed_interval_sec,
        "gap_count": len(gaps),
        "gaps": gaps,
        "day_seconds": day_seconds,
        "expected_polls": round(expected_polls, 1) if expected_polls else None,
        "coverage_pct": coverage_pct,
    }


def manifest_is_current(manifest_path: Path, data_path: Path, polls_path: Path) -> bool:
    """
    True when an existing manifest is already newer than every input that
    could change it. A closed past day's data and heartbeat files never
    change again, so re-hashing them (SHA256 over a file already 100+ MB,
    on a cadence that runs every couple of hours) is pure waste once this
    holds — see CLAUDE.md section on why generate_manifest.py needs wiring
    into the fetch pipeline without regenerating the whole archive each time.
    """
    if not manifest_path.exists():
        return False
    manifest_mtime = manifest_path.stat().st_mtime
    if data_path.stat().st_mtime > manifest_mtime:
        return False
    if polls_path.exists() and polls_path.stat().st_mtime > manifest_mtime:
        return False
    return True


def should_skip(manifest_path: Path, data_path: Path, polls_path: Path, force: bool) -> bool:
    return not force and manifest_is_current(manifest_path, data_path, polls_path)


def build_manifest(
    data_path: Path,
    gap_threshold_multiplier: float,
    nominal_interval_sec: int = DEFAULT_INTERVAL_SEC,
    tz: ZoneInfo = ZoneInfo(DEFAULT_TIMEZONE),
    now: datetime | None = None,
) -> dict:
    polls_path = data_path.with_name(f"{data_path.stem}.polls.jsonl")
    heartbeat_available = polls_path.exists()

    data_records, data_malformed_lines = load_jsonl(data_path)
    data_timestamps = sorted({r["snapshot_ts"] for r in data_records})

    day_start_ts, day_end_ts, day_in_progress = day_bounds(data_path.stem, tz, now)

    manifest = {
        "date": data_path.stem,
        "data_file": data_path.name,
        "data_sha256": sha256_of(data_path),
        "data_size_bytes": data_path.stat().st_size,
        "total_vehicle_records": len(data_records),
        # >0 usually means this file was checksummed mid-transfer (e.g. an
        # rsync pull racing a still-writing collector) rather than corrupt at
        # the source — the sha256 above is still an honest hash of what was
        # actually read, just not necessarily of a "final" file.
        "data_malformed_lines": data_malformed_lines,
        "heartbeat_available": heartbeat_available,
        # See day_bounds() docstring: for today's own file this being low
        # doesn't necessarily mean downtime, the local rsync copy lags too.
        "day_in_progress": day_in_progress,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    if heartbeat_available:
        polls, polls_malformed_lines = load_jsonl(polls_path)
        ok_polls = [p for p in polls if p["fetch_ok"]]
        error_polls = [p for p in polls if not p["fetch_ok"]]
        empty_polls = [p for p in ok_polls if p["vehicle_count"] == 0]
        heartbeat_timestamps = sorted(p["snapshot_ts"] for p in polls)

        # .get() defaults: polls logged before these fields existed don't have them.
        entities_total = sum(p.get("entities_total", 0) for p in polls)
        vehicles_with_position = sum(p.get("vehicles_with_position", 0) for p in polls)
        dropped_out_of_bbox = sum(p.get("dropped_out_of_bbox", 0) for p in polls)

        manifest.update({
            "polls_file": polls_path.name,
            "polls_sha256": sha256_of(polls_path),
            "polls_malformed_lines": polls_malformed_lines,
            "polls_logged": len(polls),
            "successful_polls": len(ok_polls),
            "empty_polls": len(empty_polls),
            "fetch_error_polls": len(error_polls),
            "entities_total": entities_total,
            "vehicles_with_position": vehicles_with_position,
            "dropped_out_of_bbox": dropped_out_of_bbox,
            # rate, not just a bare count, so a reader doesn't have to divide
            # two numbers from different rows to tell if this is significant
            "dropped_out_of_bbox_pct": (
                round(100 * dropped_out_of_bbox / vehicles_with_position, 3)
                if vehicles_with_position else None
            ),
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
            "polls_malformed_lines": None,
            "polls_logged": None,
            "successful_polls": None,
            "empty_polls": None,
            "fetch_error_polls": None,
            "entities_total": None,
            "vehicles_with_position": None,
            "dropped_out_of_bbox": None,
            "dropped_out_of_bbox_pct": None,
        })

    manifest.update(analyze_gaps(timestamps, nominal_interval_sec, gap_threshold_multiplier, day_start_ts, day_end_ts))
    return manifest


def main():
    parser = argparse.ArgumentParser(
        description="Generate checksummed, gap-annotated coverage manifests for collected GTFS-RT day files"
    )
    parser.add_argument("data_dir", type=Path, help="Directory containing <YYYY-MM-DD>.jsonl files")
    parser.add_argument(
        "--gap-threshold", type=float, default=3.0,
        help="Flag a gap when it exceeds this multiple of the *configured* poll interval, "
             "not the observed one (default: 3x)",
    )
    parser.add_argument(
        "--interval", type=int, default=DEFAULT_INTERVAL_SEC,
        help=f"Nominal collector poll interval in seconds, must match collect.py's --interval "
             f"for the run being audited (default: {DEFAULT_INTERVAL_SEC}, collect.py's own default)",
    )
    parser.add_argument(
        "--timezone", type=str, default=DEFAULT_TIMEZONE,
        help=f"Timezone for calendar-day boundaries, must match collect.py's --timezone "
             f"(default: {DEFAULT_TIMEZONE})",
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Recompute every manifest even if it's already newer than its data and "
             "heartbeat files (default: skip days whose manifest is already current)",
    )
    args = parser.parse_args()

    day_files = sorted(args.data_dir.glob("????-??-??.jsonl"))
    if not day_files:
        print(f"No day files found in {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    tz = ZoneInfo(args.timezone)
    for path in day_files:
        polls_path = path.with_name(f"{path.stem}.polls.jsonl")
        manifest_path = path.with_name(f"{path.stem}.manifest.json")

        if should_skip(manifest_path, path, polls_path, args.force):
            print(f"{path.name}: skipped (manifest already up to date)")
            continue

        manifest = build_manifest(path, args.gap_threshold, args.interval, tz)
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

        if not manifest["heartbeat_available"]:
            hb_note = "  [no heartbeat log — coverage inferred, less reliable]"
        elif manifest["heartbeat_partial"]:
            hb_note = "  [heartbeat started mid-day — coverage before that point is inferred, less reliable]"
        else:
            hb_note = ""

        malformed = manifest["data_malformed_lines"] + (manifest["polls_malformed_lines"] or 0)
        if malformed:
            hb_note += (
                f"  [WARNING: {malformed} malformed line(s) skipped — likely a mid-transfer pull; "
                "checksum reflects exactly what was read, not necessarily a complete file]"
            )

        print(
            f"{path.name}: {manifest['total_vehicle_records']:,} records | "
            f"coverage {manifest['coverage_pct']}% | {manifest['gap_count']} gap(s) | "
            f"sha256={manifest['data_sha256'][:12]}…{hb_note}"
        )


if __name__ == "__main__":
    main()
