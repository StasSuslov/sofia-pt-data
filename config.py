"""Collector cadence constants, shared by collect.py and generate_manifest.py.

collect.py writes the data and scripts/generate_manifest.py audits it against
these same numbers.

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

Also re-exports the day-file helpers shared by generate_manifest.py and
segment_speeds.py. Their implementation, like the profile validation, lives
in the stdlib-only cityprofile package, so this stays as dependency-free as
the constants above. Paths are resolved here, at call time, so tests can
point CITIES_DIR somewhere else.
"""

from pathlib import Path
from types import MappingProxyType

from cityprofile.checks import validate_profile
from cityprofile.dayfiles import (
    date_from_path,
    find_day_files,
    open_maybe_gzip,
    resolve_day_file,
)
from cityprofile.schema import (
    REQUIRED_FEEDS,
    SPEED_FIELD_UNITS,
    CityProfileError,
    read_profile,
)

# Names other modules import from here; the implementations live in
# cityprofile/. The noqa is for wemake banning __all__ outright, while the
# list is how these re-exports stay explicit (and F401-clean).
__all__ = (  # noqa: WPS410
    'CITIES_DIR',
    'DEFAULT_HOURS',
    'NETWORK_BBOX',
    'REQUIRED_FEEDS',
    'SPEED_FIELD_UNITS',
    'CityProfileError',
    'city_from_path',
    'city_slugs',
    'date_from_path',
    'find_day_files',
    'load_city',
    'load_city_for_path',
    'open_maybe_gzip',
    'resolve_day_file',
)

DEFAULT_HOURS = 24

CITIES_DIR = Path(__file__).resolve().parent / 'cities'

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
NETWORK_BBOX = MappingProxyType({
    'lat_min': 42.45,
    'lat_max': 42.9,
    'lon_min': 23.03,
    'lon_max': 23.66,
})


def city_slugs() -> list[str]:
    """Every city this checkout has a profile for."""
    return sorted(entry.stem for entry in CITIES_DIR.glob('*.json'))


def load_city(slug: str) -> dict:
    """Read cities/<slug>.json and validate the fields the pipeline reads.

    Validation is here rather than at each call site because a missing feed
    URL or a wrong speed unit surfaces as a plausible number downstream, not
    as a crash: the profile is checked once, on the way in.

    user_agent is derived from the slug rather than stored, so the string an
    agency reads in its logs cannot drift from the city it names.
    """
    path = CITIES_DIR / f'{slug}.json'
    profile = read_profile(path, city_slugs())
    validate_profile(path, profile, slug)
    profile['user_agent'] = f'{slug}-transport-research/1.0'
    return profile


def city_from_path(path: Path) -> str | None:
    """Return the city whose data lives at `path`, read off the path itself.

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
    """Return the profile for a pipeline invocation.

    It comes from an explicit --city or from the data path. Raises rather
    than falling back to any particular city: a silent default is how one
    city's bbox, licence or speed unit ends up stamped on another's data.
    """
    if slug:
        return load_city(slug)
    found = city_from_path(path)
    if found is None:
        known = ', '.join(city_slugs()) or 'none'
        raise CityProfileError(
            f'no city profile matches any component of {path} '
            f'(have: {known}); pass --city explicitly',
        )
    return load_city(found)
