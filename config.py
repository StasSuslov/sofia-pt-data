#!/usr/bin/env python3
"""
Collector cadence constants, shared by collect.py (which writes the data) and
scripts/generate_manifest.py (which audits it against these same numbers).

Deliberately dependency-free: importing collect.py to reach these values would
drag requests and the protobuf bindings into every tool that only reads files
off disk, so a manifest could not be generated on a machine without the
collector's runtime installed.

Every fact that belongs to a city rather than to the pipeline lives in
cities/<slug>.json and reaches the code through load_city() below: feed
URLs, bounding box, timezone, poll cadence, attribution, and the units the
feed's own speed field actually uses. Constants here held exactly one city's
values, so a second city could only arrive by editing them; a profile is
data, so it can be copied into the export as provenance and diffed.

Also holds the day-file helpers shared by generate_manifest.py and
segment_speeds.py: both need to find a <date>.jsonl day file and its
<date>.polls.jsonl heartbeat companion whether or not
deploy/sofia-compress.service has gzipped it by the time
scripts/fetch_data.sh pulls it down from the VPS. gzip and pathlib are
stdlib, so this stays as dependency-free as the constants above.
"""

import gzip
import json
from pathlib import Path

DEFAULT_HOURS = 24

CITIES_DIR = Path(__file__).resolve().parent / "cities"

# A feed a city cannot be collected without. Everything else in "feeds"
# (trip_updates, alerts) is optional: section 3 of CLAUDE.md records that
# only the congestion layer strictly needs realtime, so a static-only city
# is a real configuration, but one with no static feed is not.
REQUIRED_FEEDS = ("vehicle_positions", "static")

# What the feed's speed field actually carries, regardless of what GTFS-RT
# says it should or what the raw archive named it. Sofia's is "kmh" (see
# cities/sofia.json). Reading a spec-compliant feed as if it were Sofia's,
# or the reverse, is a factor-of-3.6 error in a published number.
SPEED_FIELD_UNITS = ("kmh", "ms")

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


class CityProfileError(ValueError):
    """A city profile is missing, malformed, or names a city that isn't there."""


def city_slugs() -> list[str]:
    """Every city this checkout has a profile for."""
    return sorted(p.stem for p in CITIES_DIR.glob("*.json"))


def load_city(slug: str) -> dict:
    """
    Read cities/<slug>.json and validate the fields the pipeline reads.

    Validation is here rather than at each call site because a missing feed
    URL or a wrong speed unit surfaces as a plausible number downstream, not
    as a crash: the profile is checked once, on the way in.

    user_agent is derived from the slug rather than stored, so the string an
    agency reads in its logs cannot drift from the city it names.
    """
    path = CITIES_DIR / f"{slug}.json"
    if not path.exists():
        raise CityProfileError(
            f"no city profile at {path} (have: {', '.join(city_slugs()) or 'none'})"
        )
    try:
        profile = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise CityProfileError(f"{path} is not valid JSON: {e}") from e

    if profile.get("slug") != slug:
        raise CityProfileError(
            f"{path} declares slug {profile.get('slug')!r}, but the file is named {slug!r}"
        )
    for key in ("name", "timezone", "poll_interval_sec", "bbox", "feeds", "attribution"):
        if key not in profile:
            raise CityProfileError(f"{path} is missing required key {key!r}")
    for edge in ("lat_min", "lat_max", "lon_min", "lon_max"):
        if edge not in profile["bbox"]:
            raise CityProfileError(f"{path} bbox is missing {edge!r}")
    if profile["bbox"]["lat_min"] >= profile["bbox"]["lat_max"] or \
            profile["bbox"]["lon_min"] >= profile["bbox"]["lon_max"]:
        raise CityProfileError(f"{path} bbox has a min at or above its max")
    for feed in REQUIRED_FEEDS:
        if not profile["feeds"].get(feed):
            raise CityProfileError(f"{path} has no {feed!r} feed URL")
    unit = profile.get("speed_field_unit")
    if unit not in SPEED_FIELD_UNITS:
        raise CityProfileError(
            f"{path} has speed_field_unit {unit!r}, expected one of {SPEED_FIELD_UNITS}"
        )
    for key in ("source_name", "licence"):
        if not profile["attribution"].get(key):
            raise CityProfileError(f"{path} attribution is missing {key!r}")
    auth = profile.get("auth")
    if auth is not None and not auth.get("key_env"):
        raise CityProfileError(
            f"{path} has an auth block with no key_env: a profile names the environment "
            f"variable holding the key, never the key itself"
        )

    profile["user_agent"] = f"{slug}-transport-research/1.0"
    return profile


def city_from_path(path: Path) -> str | None:
    """
    The city whose data lives at `path`, read off the path itself.

    Data is laid out as data/<city>/... (and data/<city>/static, .../web,
    /opt/<something>/data/<city> on the collector host), so the city is
    already written down in every path the pipeline is given; the deepest
    component matching a profile in cities/ wins. package_dataset.py already
    derives its archive prefix this way. Returns None when no component
    names a city, leaving the caller to demand --city rather than guessing.
    """
    known = set(city_slugs())
    resolved = Path(path).resolve()
    for candidate in (resolved, *resolved.parents):
        if candidate.name in known:
            return candidate.name
    return None


def load_city_for_path(path: Path, slug: str | None = None) -> dict:
    """
    Profile for a pipeline invocation, from an explicit --city or from the
    data path. Raises rather than falling back to any particular city: a
    silent default is how one city's bbox, licence or speed unit ends up
    stamped on another's data.
    """
    if slug:
        return load_city(slug)
    found = city_from_path(path)
    if found is None:
        raise CityProfileError(
            f"no city profile matches any component of {path} "
            f"(have: {', '.join(city_slugs()) or 'none'}); pass --city explicitly"
        )
    return load_city(found)


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
