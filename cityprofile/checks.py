"""Checks on the inside of a profile: bbox, feeds, units, licences, auth."""

from pathlib import Path

from cityprofile.schema import (
    REQUIRED_FEEDS,
    SPEED_FIELD_UNITS,
    CityProfileError,
    check_header,
)

BBOX_EDGES = ('lat_min', 'lat_max', 'lon_min', 'lon_max')


def _check_bbox(path: Path, bbox: dict) -> None:
    for edge in BBOX_EDGES:
        if edge not in bbox:
            raise CityProfileError(f'{path} bbox is missing {edge!r}')
    lat_ok = bbox['lat_min'] < bbox['lat_max']
    if not (lat_ok and bbox['lon_min'] < bbox['lon_max']):
        raise CityProfileError(f'{path} bbox has a min at or above its max')


def _check_feeds(path: Path, profile: dict) -> None:
    for feed in REQUIRED_FEEDS:
        if not profile['feeds'].get(feed):
            raise CityProfileError(f'{path} has no {feed!r} feed URL')
    unit = profile.get('speed_field_unit')
    if unit not in SPEED_FIELD_UNITS:
        raise CityProfileError(
            f'{path} has speed_field_unit {unit!r}, '
            f'expected one of {SPEED_FIELD_UNITS}',
        )


def _check_attribution(path: Path, attribution: dict) -> None:
    for key in ('source_name', 'licence'):
        if not attribution.get(key):
            raise CityProfileError(f'{path} attribution is missing {key!r}')
    # One licence per feed, stated, never inherited. A portal's default terms
    # are a claim about the portal: Sofia's realtime feed takes that default
    # (CC BY 4.0) while its static feed overrides it with share-alike, and a
    # single "licence" field hid that difference for four weeks. Same D12
    # mechanic as bbox and speed unit — no default means the second city
    # cannot silently publish under the first city's terms.
    feed_licences = attribution.get('feed_licences')
    if not isinstance(feed_licences, dict):
        raise CityProfileError(f"{path} attribution has no 'feed_licences' map")
    for feed in REQUIRED_FEEDS:
        if not feed_licences.get(feed):
            raise CityProfileError(
                f'{path} feed_licences states no licence for {feed!r}',
            )


def _check_auth(path: Path, profile: dict) -> None:
    auth = profile.get('auth')
    if auth is not None and not auth.get('key_env'):
        raise CityProfileError(
            f'{path} has an auth block with no key_env: a profile names '
            f'the environment variable holding the key, never the key itself',
        )


def validate_profile(path: Path, profile: dict, slug: str) -> None:
    """Raise CityProfileError unless `profile` has every field the code reads.

    Validation is here rather than at each call site because a missing feed
    URL or a wrong speed unit surfaces as a plausible number downstream, not
    as a crash: the profile is checked once, on the way in.
    """
    check_header(path, profile, slug)
    _check_bbox(path, profile['bbox'])
    _check_feeds(path, profile)
    _check_attribution(path, profile['attribution'])
    _check_auth(path, profile)
