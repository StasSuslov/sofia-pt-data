#!/usr/bin/env python3
"""
Collector cadence constants, shared by collect.py (which writes the data) and
scripts/generate_manifest.py (which audits it against these same numbers).

Deliberately dependency-free: importing collect.py to reach these values would
drag requests and the protobuf bindings into every tool that only reads files
off disk, so a manifest could not be generated on a machine without the
collector's runtime installed.

BASE_URL moved here from collect.py so scripts/archive_static_feed.py can
build the static feed's URL without importing collect.py and dragging in
requests and the protobuf bindings, the same reasoning as the day-file
helpers below. NETWORK_BBOX followed it on 2026-09-01, for the separate
reason that the bounds are configuration for whichever city the pipeline is
pointed at, not collector logic.

Also holds the day-file helpers shared by generate_manifest.py and
segment_speeds.py: both need to find a <date>.jsonl day file and its
<date>.polls.jsonl heartbeat companion whether or not
deploy/sofia-compress.service has gzipped it by the time
scripts/fetch_data.sh pulls it down from the VPS. gzip and pathlib are
stdlib, so this stays as dependency-free as the constants above.
"""

import gzip
from pathlib import Path

DEFAULT_INTERVAL_SEC = 45   # poll every 45 seconds
DEFAULT_HOURS = 24
DEFAULT_TIMEZONE = "Europe/Sofia"
BASE_URL = "https://gtfs.sofiatraffic.bg"

# Bounding box of the configured city's network (currently Sofia). Coordinates
# outside it are discarded at collection time (known GTFS-RT teleportation bug
# where vehicles appear far outside the service area, e.g. the Black Sea).
# Derived 2026-08-28 from the actual GTFS Static network extent
# (data/sofia/static/gtfs_2026-08-27.zip: stops.txt + shapes.txt combined give
# lat 42.4788-42.8546, lon 23.0778-23.6075) plus a margin for GPS drift near
# the edges, not hand-picked; scripts/derive_bbox.py re-runs that derivation.
# The original bbox (lat 42.57-42.80, lon 23.15-23.55) was narrower than the
# real network on all four sides and silently discarded ~11% of routes serving
# peripheral settlements (e.g. Kurilo, Zhelyava, Yana, Klisura) as if they
# were teleportation artifacts, see METHODOLOGY.md.
NETWORK_BBOX = {
    "lat_min": 42.45,
    "lat_max": 42.90,
    "lon_min": 23.03,
    "lon_max": 23.66,
}


def date_from_path(path: Path) -> str:
    """
    The YYYY-MM-DD a day file (or its .polls.jsonl companion) belongs to,
    gzipped or not. Path.stem only strips one suffix, so
    "2026-08-29.jsonl.gz".stem is "2026-08-29.jsonl", not the date this is
    actually needed for — strip the optional .gz first, then take everything
    before the first remaining dot.
    """
    name = path.name
    if name.endswith(".gz"):
        name = name[: -len(".gz")]
    return name.split(".", 1)[0]


def resolve_day_file(data_dir: Path, date_str: str, suffix: str) -> Path:
    """
    Path to a per-day file that may be stored gzipped on disk (see
    deploy/sofia-compress.service), preferring the uncompressed copy when
    both exist. `suffix` is e.g. ".jsonl" for the data file itself or
    ".polls.jsonl" for its heartbeat companion. What this resolves to is a
    pure storage detail — manifests always describe the uncompressed bytes
    regardless (see CLAUDE.md's provenance invariant for gzipped archives).

    Returns the uncompressed path even when neither exists, so callers can
    still call .exists() on the result and get a sane "not found".
    """
    plain = data_dir / f"{date_str}{suffix}"
    if plain.exists():
        return plain
    gz = data_dir / f"{date_str}{suffix}.gz"
    return gz if gz.exists() else plain


def find_day_files(data_dir: Path) -> list[Path]:
    """
    One Path per calendar day found under data_dir, whether stored as
    <date>.jsonl or <date>.jsonl.gz. When both exist for the same date,
    resolve_day_file()'s uncompressed-first preference wins, so callers never
    have to think about compression twice.
    """
    dates = {date_from_path(p) for p in data_dir.glob("????-??-??.jsonl")}
    dates |= {date_from_path(p) for p in data_dir.glob("????-??-??.jsonl.gz")}
    return [resolve_day_file(data_dir, d, ".jsonl") for d in sorted(dates)]


def open_maybe_gzip(path: Path, mode: str = "rb", encoding: str | None = None):
    """
    Open a day file (or heartbeat log) transparently whether it's gzipped on
    disk or not. Text-mode callers pass mode="rt", encoding="utf-8"; binary
    callers (sha256 hashing) use the defaults.
    """
    opener = gzip.open if path.suffix == ".gz" else open
    return opener(path, mode, encoding=encoding)
