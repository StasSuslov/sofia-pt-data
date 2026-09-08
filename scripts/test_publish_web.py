"""
Tests for the part of publish_web.py that decides what gets published.

Deliberately never runs npm and never runs git: build_staging_tree() takes
plain paths for exactly that reason, so what lands in the published tree can
be asserted on a fake export in a tmp_path in milliseconds. The npm and git
halves are one subprocess call each and are exercised by actually running
the script, not from here.

The load-bearing assertion is check_index_paths_resolve: an index listing a
day that was not copied is the specific failure this whole design exists to
prevent, and it looks perfectly healthy in the JSON.
"""

import json
import sys

import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from publish_web import build_staging_tree, select_day_bundles

PERIOD_KEY = "f67e7128747733b2"

# What export_web.py copies out of the city profile into every bundle
# manifest. publish_web.py refuses a bundle without it, so the fixtures carry
# it the way a real export does.
ATTRIBUTION = {
    "city": "Sofia",
    "operator": "CGM Sofia",
    "source_name": "urbandata.sofia.bg",
    "source_url": "https://urbandata.sofia.bg",
    "feed_description": "CGM Sofia GTFS/GTFS-RT",
    "licence": "CC BY 4.0",
}


def write_day_bundle(web_dir: Path, date_str: str, *, manifest: bool = True,
                     attribution: dict | None = None) -> Path:
    """A day bundle thin enough to copy fast but shaped like the real thing:
    manifest, geometry and one timeslot. `manifest=False` produces the
    half-written bundle an interrupted export leaves behind."""
    d = web_dir / date_str
    (d / "timeslots").mkdir(parents=True)
    (d / "timeslots" / "0000.json").write_text('{"segment_idx": [], "speed_kmh": []}')
    (d / "geometry.json").write_text('{"shape_keys": [], "lat": [], "lon": []}')
    attribution = ATTRIBUTION if attribution is None else attribution
    if manifest:
        (d / "manifest.json").write_text(json.dumps(
            {"format_version": 2, "mode": date_str, "attribution": attribution}))
    return d


def make_export(tmp_path: Path, dates: list[str], *, no_manifest: list[str] = ()) -> Path:
    web_dir = tmp_path / "web"
    for date_str in dates:
        write_day_bundle(web_dir, date_str, manifest=date_str not in no_manifest)

    period = web_dir / "typical_weekday" / PERIOD_KEY
    (period / "timeslots").mkdir(parents=True)
    (period / "timeslots" / "0800.json").write_text('{"segment_idx": [0], "speed_kmh": [17]}')
    (period / "manifest.json").write_text(json.dumps(
        {"format_version": 2, "mode": "typical_weekday", "attribution": ATTRIBUTION}))
    (web_dir / "typical_weekday" / "manifest.json").write_text(json.dumps({
        "format_version": 2,
        "mode": "typical_weekday_index",
        "current_period": PERIOD_KEY,
        "periods": [{"period_key": PERIOD_KEY}],
    }))

    # The source index lists every day on disk. Nothing should copy it: the
    # published one is rebuilt from what actually got copied.
    (web_dir / "index.json").write_text(json.dumps({"days": [{"date": d} for d in dates]}))
    return web_dir


def make_dist(tmp_path: Path) -> Path:
    """A built frontend, including the stale data/ directory Vite leaves
    behind by dereferencing frontend/public/data at build time."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><script src=/sofia-pt-web/assets/main.js></script>")
    (dist / "assets" / "main.js").write_text("// built bundle")
    stale = dist / "data" / "1999-01-01"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text("{}")
    return dist


DATES = ["2026-08-30", "2026-08-31", "2026-09-01", "2026-09-02", "2026-09-03"]


def test_window_publishes_only_the_newest_days(tmp_path):
    web_dir = make_export(tmp_path, DATES)
    report = build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)

    staging_data = tmp_path / "staging" / "data"
    on_disk = sorted(p.name for p in staging_data.glob("????-??-??"))
    assert on_disk == DATES[-3:]
    assert report["days_published"] == DATES[-3:]
    assert report["days_skipped"] == DATES[:2]
    for date_str in DATES[:2]:
        assert not (staging_data / date_str).exists()

    # And the bundle came across whole, not just its manifest.
    assert (staging_data / "2026-09-03" / "timeslots" / "0000.json").exists()
    assert (staging_data / "2026-09-03" / "geometry.json").exists()


def test_index_lists_exactly_the_published_days(tmp_path):
    web_dir = make_export(tmp_path, DATES)
    report = build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)

    index = json.loads((tmp_path / "staging" / "data" / "index.json").read_text())
    assert [d["date"] for d in index["days"]] == DATES[-3:]
    assert index == report["index"]


def test_every_path_in_the_index_exists_on_disk(tmp_path):
    """The one that catches publishing an index pointing at 404s — a copied
    source index would pass every other assertion here and fail this."""
    web_dir = make_export(tmp_path, DATES)
    build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)

    staging_data = tmp_path / "staging" / "data"
    index = json.loads((staging_data / "index.json").read_text())
    for day in index["days"]:
        assert (staging_data / day["path"] / "manifest.json").exists(), day["path"]
    assert (staging_data / index["typical_weekday"]["path"] / "manifest.json").exists()


def test_typical_weekday_ships_whole_with_its_current_period(tmp_path):
    web_dir = make_export(tmp_path, DATES)
    report = build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)

    period = tmp_path / "staging" / "data" / "typical_weekday" / PERIOD_KEY
    assert (period / "timeslots" / "0800.json").exists()
    assert report["period_dirs"] == [PERIOD_KEY]
    assert report["current_period"] == PERIOD_KEY
    assert report["index"]["typical_weekday"]["current_period"] == PERIOD_KEY


def test_bundle_without_a_manifest_is_not_published(tmp_path):
    web_dir = make_export(tmp_path, DATES, no_manifest=["2026-09-03"])
    published, _ = select_day_bundles(web_dir, days=3)
    assert [p.name for p in published] == ["2026-08-31", "2026-09-01", "2026-09-02"]

    build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)
    assert not (tmp_path / "staging" / "data" / "2026-09-03").exists()


def test_stale_data_dir_from_the_build_is_not_copied(tmp_path):
    """Vite dereferences frontend/public/data into dist/, so dist carries a
    full unwindowed copy of the export. It must not reach the staging tree
    — write_root_index() would happily index those days too."""
    web_dir = make_export(tmp_path, DATES)
    build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)

    staging = tmp_path / "staging"
    assert not (staging / "data" / "1999-01-01").exists()
    assert (staging / "assets" / "main.js").exists()
    assert (staging / "index.html").exists()
    assert (staging / ".nojekyll").exists()
    readme = (staging / "README.md").read_text()
    assert "zenodo" in readme
    # The README is written from the export's own attribution, not from a
    # constant that would keep saying Sofia over anyone else's data.
    assert "# Sofia public transport" in readme
    assert "urbandata.sofia.bg" in readme
    assert "CC BY 4.0" in readme


def test_a_bundle_without_attribution_is_not_published(tmp_path):
    """export_web.py writes attribution: null when it cannot resolve a city.
    That export must stop here: a published map states whose data it shows
    and under what licence, and the alternative to no attribution is the
    wrong one."""
    web_dir = make_export(tmp_path, DATES)
    anon = web_dir / "2026-09-03" / "manifest.json"
    anon.write_text(json.dumps({"format_version": 2, "mode": "2026-09-03", "attribution": None}))
    with pytest.raises(ValueError, match="no attribution"):
        build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)


def test_two_cities_in_one_export_are_refused(tmp_path):
    """One site publishes one city's data. Two attributions in one tree means
    one of them would be printed over the other's segments."""
    web_dir = make_export(tmp_path, DATES)
    other = dict(ATTRIBUTION, city="Plovdiv", source_name="example.test", licence="CC0 1.0")
    (web_dir / "2026-09-03" / "manifest.json").write_text(json.dumps(
        {"format_version": 2, "mode": "2026-09-03", "attribution": other}))
    with pytest.raises(ValueError, match="different attributions"):
        build_staging_tree(web_dir, make_dist(tmp_path), tmp_path / "staging", days=3)


def test_non_empty_staging_dir_is_refused(tmp_path):
    """The guard standing in front of the one irreversible step. Publishing
    force-pushes the staging directory as a repository built from scratch,
    so building into a directory that already holds something is how a real
    repository — or a day from a window that has since moved on — reaches
    the public site."""
    web_dir = make_export(tmp_path, DATES)
    dist = make_dist(tmp_path)

    holds_a_repo = tmp_path / "already-a-repo"
    (holds_a_repo / ".git").mkdir(parents=True)
    with pytest.raises(ValueError, match="not empty"):
        build_staging_tree(web_dir, dist, holds_a_repo, days=3)

    # Second run into the same --staging. Nothing in build_staging_tree
    # deletes, so without the guard 2026-09-01 would survive from the first
    # run into an index rebuilt for a one-day window.
    reused = tmp_path / "reused"
    build_staging_tree(web_dir, dist, reused, days=3)
    with pytest.raises(ValueError, match="not empty"):
        build_staging_tree(web_dir, dist, reused, days=1)
