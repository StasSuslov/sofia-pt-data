"""Tests for config.py's per-city profile loader (cities/<slug>.json).

Section 2's literal assertions below are not arbitrary: they are the live
collector's configuration before every per-city constant moved out of the
code (see CLAUDE.md D2/D6 and config.py's NETWORK_BBOX docstring). A wrong
bbox here silently drops real peripheral routes at collection time, with no
record left on disk to catch it afterwards -- that's the failure mode the
old hand-drawn bbox actually caused. These numbers are asserted to the digit,
never recomputed or softened with pytest.approx.
"""

import json
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from config import (  # noqa: E402  # needs the sys.path.insert above
    REQUIRED_FEEDS,
    SPEED_FIELD_UNITS,
    CityProfileError,
    city_from_path,
    city_slugs,
    load_city,
    load_city_for_path,
)

SOFIA_PROFILE_PATH = REPO_ROOT / 'cities' / 'sofia.json'
POLL_INTERVAL_SEC = 45
EXPECTED_BBOX = MappingProxyType({
    'lat_min': 42.45,
    'lat_max': 42.9,
    'lon_min': 23.03,
    'lon_max': 23.66,
})
EXPECTED_FEED_LICENCES = MappingProxyType({
    'vehicle_positions': 'CC BY 4.0',
    'static': 'CC BY-SA 4.0',
    'trip_updates': 'CC BY-SA 4.0',
    'alerts': 'CC BY-SA 4.0',
})


def load_sofia_dict() -> dict:
    """Return a fresh copy of the real sofia.json.

    For rejection tests that mutate exactly one field and reload it from a
    throwaway cities dir. Read by absolute path rather than through
    config.CITIES_DIR, so it still finds the real file in tests below that
    monkeypatch CITIES_DIR to a tmp_path.
    """
    return json.loads(SOFIA_PROFILE_PATH.read_text(encoding='utf-8'))


def write_profile(cities_dir: Path, filename: str, profile: dict) -> None:
    """Write `profile` as JSON to `filename` inside `cities_dir`."""
    (cities_dir / filename).write_text(json.dumps(profile), encoding='utf-8')


# ─── every shipped profile validates ───────────────────────────────────────
# The guard that a newly added cities/<x>.json can't be half-filled: it must
# pass the same checks sofia.json does, or CI catches it before it ever
# reaches collect.py.


@pytest.mark.parametrize('slug', city_slugs())
def test_every_shipped_profile_loads(slug):
    """Each shipped profile loads without raising."""
    load_city(slug)


# ─── sofia.json reproduces the pre-refactor pipeline constants ─────────────


@pytest.fixture(scope='module')
def sofia_profile() -> dict:
    """The real Sofia profile as load_city returns it."""
    return load_city('sofia')


def test_sofia_timezone_interval_and_unit(sofia_profile):
    """The timezone, cadence and speed unit match the deployed pipeline."""
    assert sofia_profile['timezone'] == 'Europe/Sofia'
    assert sofia_profile['poll_interval_sec'] == POLL_INTERVAL_SEC
    assert sofia_profile['speed_field_unit'] == 'kmh'


def test_sofia_bbox_edges_match_to_the_digit(sofia_profile):
    """All four bbox edges match exactly.

    Compared edge by edge as a mapping: a bbox bug is almost always one
    wrong edge, not all four, and a tuple-equality assert would still pass
    if the diff happened to cancel out in aggregate stats.
    """
    bbox = sofia_profile['bbox']
    assert {edge: bbox[edge] for edge in EXPECTED_BBOX} == EXPECTED_BBOX


def test_sofia_user_agent(sofia_profile):
    """The user agent is derived from the slug, not stored in the profile.

    Derived by load_city, never stored in the profile itself
    -- config.py: "the string an agency reads in its logs cannot drift
    from the city it names".
    """
    assert sofia_profile['user_agent'] == 'sofia-transport-research/1.0'


def test_sofia_attribution(sofia_profile):
    """The licences are share-alike, not the portal's default.

    Share-alike, not the portal's default: gtfs-static overrides the
    platform terms, and the geometry this project publishes derives from it.
    """
    attribution = sofia_profile['attribution']
    assert attribution['licence'] == 'CC BY-SA 4.0'
    assert attribution['feed_licences'] == EXPECTED_FEED_LICENCES
    assert attribution['source_url'] == 'https://urbandata.sofia.bg'


def test_sofia_feed_urls(sofia_profile):
    """The feed URLs are whole literal strings.

    Written as literals here, not read back from cities/sofia.json, so a
    silent edit to the profile (e.g. someone "fixing" a URL by hand) fails
    this test instead of sailing through unnoticed. A feed URL must never
    be constructed from a base URL plus a path template -- CLAUDE.md
    section 7 forbids building a specific URL by pattern from a domain,
    and cities/sofia.json stores these as whole strings for exactly that
    reason.
    """
    feeds = sofia_profile['feeds']
    assert (
        feeds['vehicle_positions']
        == 'https://gtfs.sofiatraffic.bg/api/v1/vehicle-positions'
    )
    assert feeds['static'] == 'https://gtfs.sofiatraffic.bg/api/v1/static'


# ─── rejection cases ────────────────────────────────────────────────────────
# Each copies the real sofia profile, breaks exactly one thing, writes it to
# a tmp_path cities dir, and points config.CITIES_DIR at that dir so
# load_city("sofia") actually reads the broken copy instead of the real one.


@pytest.fixture
def cities_dir(tmp_path, monkeypatch) -> Path:
    """A throwaway cities dir that config.CITIES_DIR points at."""
    monkeypatch.setattr('config.CITIES_DIR', tmp_path)
    return tmp_path


def _assert_rejected(cities_dir: Path, mutate) -> None:
    """Break the Sofia profile with `mutate` and expect load_city to refuse."""
    profile = load_sofia_dict()
    mutate(profile)
    write_profile(cities_dir, 'sofia.json', profile)

    with pytest.raises(CityProfileError):
        load_city('sofia')


def _rename_slug(profile: dict) -> None:
    profile['slug'] = 'plovdiv'  # file is still named sofia.json below


def _drop_timezone(profile: dict) -> None:
    profile.pop('timezone')


def _collapse_lat_range(profile: dict) -> None:
    profile['bbox']['lat_min'] = profile['bbox'][
        'lat_max'
    ]  # min == max, not < max


def _drop_lon_max(profile: dict) -> None:
    profile['bbox'].pop('lon_max')


def _set_mph(profile: dict) -> None:
    profile['speed_field_unit'] = 'mph'


def _drop_licence(profile: dict) -> None:
    profile['attribution'].pop('licence')


def _drop_static_licence(profile: dict) -> None:
    profile['attribution']['feed_licences'].pop('static')


def _auth_without_key_env(profile: dict) -> None:
    profile['auth'] = {
        'type': 'api_key',
    }  # no key_env: a profile names the env var, never the key itself


def test_rejects_slug_not_matching_filename(cities_dir):
    """The slug inside the file must match the file name."""
    _assert_rejected(cities_dir, _rename_slug)


def test_rejects_missing_required_top_key(cities_dir):
    """A missing required top-level key is an error."""
    _assert_rejected(cities_dir, _drop_timezone)


def test_rejects_bbox_min_at_or_above_max(cities_dir):
    """A bbox with min equal to max is an error."""
    _assert_rejected(cities_dir, _collapse_lat_range)


def test_rejects_bbox_missing_an_edge(cities_dir):
    """A bbox without one of its four edges is an error."""
    _assert_rejected(cities_dir, _drop_lon_max)


@pytest.mark.parametrize('feed', REQUIRED_FEEDS)
def test_rejects_a_missing_required_feed(feed, cities_dir):
    """Each required feed must be present."""
    _assert_rejected(cities_dir, lambda profile: profile['feeds'].pop(feed))


def test_rejects_unknown_speed_field_unit(cities_dir):
    """A speed unit outside the known set is an error."""
    assert 'mph' not in SPEED_FIELD_UNITS  # SPEED_FIELD_UNITS is ("kmh", "ms")
    _assert_rejected(cities_dir, _set_mph)


def test_rejects_attribution_without_licence(cities_dir):
    """Attribution must name a licence."""
    _assert_rejected(cities_dir, _drop_licence)


def test_rejects_a_required_feed_with_no_licence(cities_dir):
    """A feed's terms are stated per feed, never inherited from a sibling.

    Sofia's own feeds disagree: the realtime positions take the portal's
    default CC BY 4.0 and the schedule overrides it with share-alike. A
    profile that names one of them and stays silent about the other would
    let the silent one be published under the wrong terms.
    """
    _assert_rejected(cities_dir, _drop_static_licence)


def test_rejects_auth_block_with_no_key_env(cities_dir):
    """An auth block must name the env var that holds the key."""
    _assert_rejected(cities_dir, _auth_without_key_env)


# ─── city_from_path / load_city_for_path ───────────────────────────────────


def test_city_from_local_data_directory():
    """The city is read off a local data directory."""
    assert city_from_path(Path('data/sofia')) == 'sofia'


def test_city_from_deployed_vps_layout():
    """The city is read off the deployed VPS layout."""
    # This is what keeps the live systemd ExecStart lines on the VPS valid
    # without an added --city flag: /opt/sofia-pt/data/sofia resolves the
    # same way as the local data/sofia checkout.
    assert city_from_path(Path('/opt/sofia-pt/data/sofia')) == 'sofia'


def test_city_from_path_none_without_city():
    """No component naming a known city gives None."""
    assert city_from_path(Path('/opt/sofia-pt/data/plovdiv')) is None


def test_load_for_path_never_defaults_to_sofia(tmp_path):
    """A path naming no known city raises instead of defaulting to Sofia."""
    # The whole point of load_city_for_path: a path that names no known city
    # must not fall back to any particular one. One city's bbox, licence, or
    # speed unit must never end up stamped on another city's data.
    with pytest.raises(CityProfileError):
        load_city_for_path(tmp_path / 'data' / 'unknown-city')


def test_load_for_path_explicit_slug_wins():
    """An explicit slug beats the path."""
    # An explicit --city overrides whatever (or nothing) the path resolves to.
    profile = load_city_for_path(Path('/some/other/city/dir'), slug='sofia')
    assert profile['slug'] == 'sofia'
