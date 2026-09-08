"""
Tests for config.py's per-city profile loader (cities/<slug>.json).

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

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
from config import (
    REQUIRED_FEEDS,
    SPEED_FIELD_UNITS,
    CityProfileError,
    city_from_path,
    load_city,
    load_city_for_path,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
SOFIA_PROFILE_PATH = REPO_ROOT / "cities" / "sofia.json"


def load_sofia_dict() -> dict:
    """A fresh copy of the real sofia.json, for rejection tests that mutate
    exactly one field and reload it from a throwaway cities dir. Read by
    absolute path rather than through config.CITIES_DIR, so it still finds
    the real file in tests below that monkeypatch CITIES_DIR to a tmp_path."""
    return json.loads(SOFIA_PROFILE_PATH.read_text(encoding="utf-8"))


def write_profile(cities_dir: Path, filename: str, profile: dict) -> None:
    (cities_dir / filename).write_text(json.dumps(profile), encoding="utf-8")


# ─── every shipped profile validates ───────────────────────────────────────
# The guard that a newly added cities/<x>.json can't be half-filled: it must
# pass the same checks sofia.json does, or CI catches it before it ever
# reaches collect.py.

@pytest.mark.parametrize("slug", config.city_slugs())
def test_every_shipped_city_profile_loads_without_raising(slug):
    load_city(slug)


# ─── sofia.json reproduces the pre-refactor pipeline constants ─────────────

def test_sofia_profile_matches_the_deployed_pipeline_constants_to_the_digit():
    profile = load_city("sofia")

    assert profile["timezone"] == "Europe/Sofia"
    assert profile["poll_interval_sec"] == 45

    # All four edges, checked individually: a bbox bug is almost always one
    # wrong edge, not all four, and a tuple-equality assert would still pass
    # if the diff happened to cancel out in aggregate stats.
    assert profile["bbox"]["lat_min"] == 42.45
    assert profile["bbox"]["lat_max"] == 42.90
    assert profile["bbox"]["lon_min"] == 23.03
    assert profile["bbox"]["lon_max"] == 23.66

    # Derived from the slug by load_city, never stored in the profile itself
    # -- config.py: "the string an agency reads in its logs cannot drift
    # from the city it names".
    assert profile["user_agent"] == "sofia-transport-research/1.0"

    assert profile["speed_field_unit"] == "kmh"

    assert profile["attribution"]["licence"] == "CC BY 4.0"
    assert profile["attribution"]["source_url"] == "https://urbandata.sofia.bg"

    # Written as literals here, not read back from cities/sofia.json, so a
    # silent edit to the profile (e.g. someone "fixing" a URL by hand) fails
    # this test instead of sailing through unnoticed. A feed URL must never
    # be constructed from a base URL plus a path template -- CLAUDE.md
    # section 7 forbids building a specific URL by pattern from a domain,
    # and cities/sofia.json stores these as whole strings for exactly that
    # reason.
    assert profile["feeds"]["vehicle_positions"] == "https://gtfs.sofiatraffic.bg/api/v1/vehicle-positions"
    assert profile["feeds"]["static"] == "https://gtfs.sofiatraffic.bg/api/v1/static"


# ─── rejection cases ────────────────────────────────────────────────────────
# Each copies the real sofia profile, breaks exactly one thing, writes it to
# a tmp_path cities dir, and points config.CITIES_DIR at that dir so
# load_city("sofia") actually reads the broken copy instead of the real one.

def test_load_city_rejects_a_slug_that_does_not_match_the_filename(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    profile["slug"] = "plovdiv"  # file is still named sofia.json below
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


def test_load_city_rejects_a_missing_required_top_level_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    del profile["timezone"]
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


def test_load_city_rejects_bbox_with_min_at_or_above_max(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    profile["bbox"]["lat_min"] = profile["bbox"]["lat_max"]  # min == max, not < max
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


def test_load_city_rejects_bbox_missing_an_edge(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    del profile["bbox"]["lon_max"]
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


@pytest.mark.parametrize("feed", REQUIRED_FEEDS)
def test_load_city_rejects_a_missing_required_feed(feed, tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    del profile["feeds"][feed]
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


def test_load_city_rejects_a_speed_field_unit_outside_the_known_set(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    assert "mph" not in SPEED_FIELD_UNITS  # SPEED_FIELD_UNITS is ("kmh", "ms")
    profile["speed_field_unit"] = "mph"
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


def test_load_city_rejects_attribution_without_a_licence(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    del profile["attribution"]["licence"]
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


def test_load_city_rejects_an_auth_block_with_no_key_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CITIES_DIR", tmp_path)
    profile = load_sofia_dict()
    profile["auth"] = {"type": "api_key"}  # no key_env: a profile names the env var, never the key itself
    write_profile(tmp_path, "sofia.json", profile)

    with pytest.raises(CityProfileError):
        load_city("sofia")


# ─── city_from_path / load_city_for_path ───────────────────────────────────

def test_city_from_path_reads_the_city_off_a_local_data_directory():
    assert city_from_path(Path("data/sofia")) == "sofia"


def test_city_from_path_reads_the_city_off_the_deployed_vps_layout():
    # This is what keeps the live systemd ExecStart lines on the VPS valid
    # without an added --city flag: /opt/sofia-pt/data/sofia resolves the
    # same way as the local data/sofia checkout.
    assert city_from_path(Path("/opt/sofia-pt/data/sofia")) == "sofia"


def test_city_from_path_returns_none_when_no_component_names_a_city():
    assert city_from_path(Path("/opt/sofia-pt/data/plovdiv")) is None


def test_load_city_for_path_raises_instead_of_silently_defaulting_to_sofia(tmp_path):
    # The whole point of load_city_for_path: a path that names no known city
    # must not fall back to any particular one. One city's bbox, licence, or
    # speed unit must never end up stamped on another city's data.
    with pytest.raises(CityProfileError):
        load_city_for_path(tmp_path / "data" / "unknown-city")


def test_load_city_for_path_explicit_slug_wins_over_the_path():
    # An explicit --city overrides whatever (or nothing) the path resolves to.
    profile = load_city_for_path(Path("/some/other/city/dir"), slug="sofia")
    assert profile["slug"] == "sofia"
