"""Assemble one day's manifest from its data file and heartbeat log."""

import datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

from config import date_from_path
from manifests.coverage import analyze_gaps, day_bounds
from manifests.hashing import load_jsonl, sha256_of
from manifests.polls import poll_section, polls_path_of
from manifests.staleness import MANIFEST_SCHEMA_VERSION


def _data_section(data_path: Path) -> tuple[dict, list[int]]:
    date_str = date_from_path(data_path)
    records, malformed_lines = load_jsonl(data_path)
    data_sha256, data_size_bytes = sha256_of(data_path)
    return {
        'schema_version': MANIFEST_SCHEMA_VERSION,
        'date': date_str,
        # Always the uncompressed name, regardless of what's actually on disk
        # locally — see "compressed" below for the storage detail, and
        # CLAUDE.md's manifest-describes-uncompressed-bytes invariant.
        'data_file': f'{date_str}.jsonl',
        'data_sha256': data_sha256,
        'data_size_bytes': data_size_bytes,
        'compressed': data_path.suffix == '.gz',
        'total_vehicle_records': len(records),
        # >0 usually means this file was checksummed mid-transfer (e.g. an
        # rsync pull racing a still-writing collector) rather than corrupt at
        # the source — the sha256 above is still an honest hash of what was
        # actually read, just not necessarily of a "final" file.
        'data_malformed_lines': malformed_lines,
    }, sorted({record['snapshot_ts'] for record in records})


def build_manifest(
    data_path: Path,
    gap_threshold_multiplier: float,
    nominal_interval_sec: int,
    tz: ZoneInfo,
    now: dt.datetime | None = None,
) -> dict:
    """Build the manifest of one day file, with its heartbeat when present.

    Args:
        data_path: The day file, plain or gzipped.
        gap_threshold_multiplier: A gap is flagged above this many intervals.
        nominal_interval_sec: The configured poll interval.
        tz: Timezone whose midnights bound the day.
        now: Clock override for tests; defaults to the current time.

    Returns:
        The manifest, fields in their published order.
    """
    data_fields, data_timestamps = _data_section(data_path)
    poll_fields, timestamps = poll_section(data_path, data_timestamps)
    window = day_bounds(date_from_path(data_path), tz, now)
    return {
        **data_fields,
        'heartbeat_available': polls_path_of(data_path).exists(),
        # See day_bounds() docstring: for today's own file this being low
        # doesn't necessarily mean downtime, the local rsync copy lags too.
        'day_in_progress': window.in_progress,
        'generated_at': dt.datetime.now(dt.UTC).isoformat(),
        **poll_fields,
        **analyze_gaps(
            timestamps,
            nominal_interval_sec,
            gap_threshold_multiplier,
            window,
        ),
    }
