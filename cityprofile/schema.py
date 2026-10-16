"""Profile file reading and the checks on its top-level shape."""

import json
from pathlib import Path

# A feed a city cannot be collected without. Everything else in "feeds"
# (trip_updates, alerts) is optional: section 3 of CLAUDE.md records that
# only the congestion layer strictly needs realtime, so a static-only city
# is a real configuration, but one with no static feed is not.
REQUIRED_FEEDS = ('vehicle_positions', 'static')

# What the feed's speed field actually carries, regardless of what GTFS-RT
# says it should or what the raw archive named it. Sofia's is "kmh" (see
# cities/sofia.json). Reading a spec-compliant feed as if it were Sofia's,
# or the reverse, is a factor-of-3.6 error in a published number.
SPEED_FIELD_UNITS = ('kmh', 'ms')

REQUIRED_KEYS = (
    'name',
    'timezone',
    'poll_interval_sec',
    'bbox',
    'feeds',
    'attribution',
)


class CityProfileError(ValueError):
    """A city profile is missing, malformed, or names a city not there."""


def read_profile(path: Path, slugs: list[str]) -> dict:
    """Read the profile file at `path`; `slugs` names the profiles on hand."""
    if not path.exists():
        known = ', '.join(slugs) or 'none'
        raise CityProfileError(
            f'no city profile at {path} (have: {known})',
        )
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as error:
        raise CityProfileError(f'{path} is not valid JSON: {error}') from error


def check_header(path: Path, profile: dict, slug: str) -> None:
    """Check that the profile names its own slug and has every top key."""
    declared = profile.get('slug')
    if declared != slug:
        raise CityProfileError(
            f'{path} declares slug {declared!r}, '
            f'but the file is named {slug!r}',
        )
    for key in REQUIRED_KEYS:
        if key not in profile:
            raise CityProfileError(f'{path} is missing required key {key!r}')
