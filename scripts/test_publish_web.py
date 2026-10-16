"""Tests for the part of publish_web.py that decides what gets published.

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
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from publish_web import build_staging_tree, select_day_bundles

PERIOD_KEY = 'f67e7128747733b2'
DAYS_IN_WINDOW = 3
MANIFEST = 'manifest.json'
TYPICAL = 'typical_weekday'
NOT_EMPTY = 'not empty'


def attribution_for_sofia() -> dict:
    """What export_web.py copies out of the city profile into each manifest.

    publish_web.py refuses a bundle without it, so the fixtures carry it the
    way a real export does.

    Returns:
        A fresh attribution mapping.
    """
    return {
        'city': 'Sofia',
        'operator': 'CGM Sofia',
        'source_name': 'urbandata.sofia.bg',
        'source_url': 'https://urbandata.sofia.bg',
        'feed_description': 'CGM Sofia GTFS/GTFS-RT',
        'licence': 'CC BY-SA 4.0',
        'feed_licences': {
            'vehicle_positions': 'CC BY 4.0',
            'static': 'CC BY-SA 4.0',
        },
    }


DATES = (
    '2026-08-30',
    '2026-08-31',
    '2026-09-01',
    '2026-09-02',
    '2026-09-03',
)


def write_manifest(directory: Path, mode: str, attribution: dict | None):
    """Write a bundle manifest carrying `attribution`."""
    (directory / MANIFEST).write_text(
        json.dumps({
            'format_version': 2,
            'mode': mode,
            'attribution': attribution,
        }),
    )


def write_day_bundle(
    web_dir: Path,
    date_str: str,
    *,
    manifest: bool = True,
) -> Path:
    """A day bundle thin enough to copy fast but shaped like the real thing.

    It holds a manifest, geometry and one timeslot. `manifest=False`
    produces the half-written bundle an interrupted export leaves behind.

    Args:
        web_dir: The export directory.
        date_str: The day, which names the bundle.
        manifest: Whether to write the manifest.

    Returns:
        The bundle directory.
    """
    bundle = web_dir / date_str
    (bundle / 'timeslots').mkdir(parents=True)
    (bundle / 'timeslots' / '0000.json').write_text(
        '{"segment_idx": [], "speed_kmh": []}',
    )
    (bundle / 'geometry.json').write_text(
        '{"shape_keys": [], "lat": [], "lon": []}',
    )
    if manifest:
        write_manifest(bundle, date_str, attribution_for_sofia())
    return bundle


def make_export(
    tmp_path: Path,
    dates: tuple[str, ...],
    *,
    no_manifest: tuple[str, ...] = (),
) -> Path:
    """A fake web export with day bundles and one typical-weekday period.

    Args:
        tmp_path: The test's scratch directory.
        dates: The day bundles to create.
        no_manifest: Days whose manifest is left out.

    Returns:
        The export directory.
    """
    web_dir = tmp_path / 'web'
    for day in dates:
        write_day_bundle(web_dir, day, manifest=day not in no_manifest)

    period = web_dir / TYPICAL / PERIOD_KEY
    (period / 'timeslots').mkdir(parents=True)
    (period / 'timeslots' / '0800.json').write_text(
        '{"segment_idx": [0], "speed_kmh": [17]}',
    )
    write_manifest(period, TYPICAL, attribution_for_sofia())
    (web_dir / TYPICAL / MANIFEST).write_text(
        json.dumps({
            'format_version': 2,
            'mode': 'typical_weekday_index',
            'current_period': PERIOD_KEY,
            'periods': [{'period_key': PERIOD_KEY}],
        }),
    )

    # The source index lists every day on disk. Nothing should copy it: the
    # published one is rebuilt from what actually got copied.
    (web_dir / 'index.json').write_text(
        json.dumps({'days': [{'date': date_str} for date_str in dates]}),
    )
    return web_dir


def make_dist(tmp_path: Path) -> Path:
    """A built frontend, including the stale data/ directory.

    Vite leaves that behind by dereferencing frontend/public/data at build
    time.

    Args:
        tmp_path: The test's scratch directory.

    Returns:
        The dist directory.
    """
    dist = tmp_path / 'dist'
    (dist / 'assets').mkdir(parents=True)
    (dist / 'index.html').write_text(
        '<!doctype html><script src=/sofia-pt-web/assets/main.js></script>',
    )
    (dist / 'assets' / 'main.js').write_text('// built bundle')
    stale = dist / 'data' / '1999-01-01'
    stale.mkdir(parents=True)
    (stale / MANIFEST).write_text('{}')
    return dist


@pytest.fixture
def web_dir(tmp_path):
    """The fake export holding all of DATES."""
    return make_export(tmp_path, DATES)


@pytest.fixture
def dist(tmp_path):
    """The fake built frontend."""
    return make_dist(tmp_path)


@pytest.fixture
def staging(tmp_path):
    """A staging path that does not exist yet."""
    return tmp_path / 'staging'


@pytest.fixture
def report(web_dir, dist, staging):
    """The report of a successful build with a three-day window."""
    return build_staging_tree(web_dir, dist, staging, days=DAYS_IN_WINDOW)


def test_window_publishes_newest_days(report, staging):
    """Only the newest days are copied and the rest are reported skipped."""
    staging_data = staging / 'data'
    on_disk = sorted(path.name for path in staging_data.glob('????-??-??'))
    assert on_disk == list(DATES[-DAYS_IN_WINDOW:])
    assert report['days_published'] == list(DATES[-DAYS_IN_WINDOW:])
    assert report['days_skipped'] == list(DATES[:2])
    for date_str in DATES[:2]:
        assert not (staging_data / date_str).exists()


def test_published_bundle_comes_across_whole(report, staging):
    """The bundle is copied whole, not just its manifest."""
    bundle = staging / 'data' / '2026-09-03'
    assert (bundle / 'timeslots' / '0000.json').exists()
    assert (bundle / 'geometry.json').exists()


def test_index_lists_published_days(report, staging):
    """The index describes exactly the days that were copied."""
    index = json.loads((staging / 'data' / 'index.json').read_text())
    assert [day['date'] for day in index['days']] == list(
        DATES[-DAYS_IN_WINDOW:],
    )
    assert index == report['index']


def test_every_index_path_exists(report, staging):
    """The one that catches publishing an index pointing at 404s.

    A copied source index would pass every other assertion here and fail
    this.
    """
    staging_data = staging / 'data'
    index = json.loads((staging_data / 'index.json').read_text())
    for day in index['days']:
        assert (staging_data / day['path'] / MANIFEST).exists(), day['path']
    typical_path = staging_data / index[TYPICAL]['path']
    assert (typical_path / MANIFEST).exists()


def test_typical_weekday_ships_whole(report, staging):
    """Every period ships whole, with its current period named."""
    period = staging / 'data' / TYPICAL / PERIOD_KEY
    assert (period / 'timeslots' / '0800.json').exists()
    assert report['period_dirs'] == [PERIOD_KEY]
    assert report['current_period'] == PERIOD_KEY
    assert report['index'][TYPICAL]['current_period'] == PERIOD_KEY


def test_bundle_without_manifest_skipped(tmp_path, dist, staging):
    """A half-written bundle is neither selected nor copied."""
    web_dir = make_export(tmp_path, DATES, no_manifest=('2026-09-03',))
    published, _ = select_day_bundles(web_dir, days=DAYS_IN_WINDOW)
    assert [bundle.name for bundle in published] == [
        '2026-08-31',
        '2026-09-01',
        '2026-09-02',
    ]

    build_staging_tree(web_dir, dist, staging, days=DAYS_IN_WINDOW)
    assert not (staging / 'data' / '2026-09-03').exists()


def test_stale_dist_data_not_copied(report, staging):
    """Vite dereferences frontend/public/data into dist/.

    So dist carries a full unwindowed copy of the export. It must not reach
    the staging tree: write_root_index() would happily index those days too.
    """
    assert not (staging / 'data' / '1999-01-01').exists()
    assert (staging / 'assets' / 'main.js').exists()
    assert (staging / 'index.html').exists()
    assert (staging / '.nojekyll').exists()


def test_readme_names_the_exported_city(report, staging):
    """The README is written from the export's own attribution.

    Not from a constant that would keep saying Sofia over anyone else's
    data.
    """
    readme = (staging / 'README.md').read_text()
    assert 'zenodo' in readme
    assert '# Sofia public transport' in readme
    assert 'urbandata.sofia.bg' in readme


def test_readme_states_each_feed_licence(report, staging):
    """The two feeds arrive under different terms.

    A README stating one licence for the pair publishes the wrong terms for
    one of them. That is the mistake this site shipped for four weeks.
    """
    readme = (staging / 'README.md').read_text()
    assert 'vehicle positions: CC BY 4.0' in readme
    assert 'static: CC BY-SA 4.0' in readme
    assert 'everything published here CC BY-SA 4.0' in readme


def test_bundle_without_attribution_refused(web_dir, dist, staging):
    """export_web.py writes attribution: null when it cannot resolve a city.

    That export must stop here: a published map states whose data it shows
    and under what licence, and the alternative to no attribution is the
    wrong one.
    """
    write_manifest(web_dir / '2026-09-03', '2026-09-03', None)
    with pytest.raises(ValueError, match='no attribution'):
        build_staging_tree(web_dir, dist, staging, days=DAYS_IN_WINDOW)


def test_two_cities_in_one_export_refused(web_dir, dist, staging):
    """One site publishes one city's data.

    Two attributions in one tree means one of them would be printed over the
    other's segments.
    """
    other = dict(
        attribution_for_sofia(),
        city='Plovdiv',
        source_name='example.test',
        licence='CC0 1.0',
    )
    write_manifest(web_dir / '2026-09-03', '2026-09-03', other)
    with pytest.raises(ValueError, match='different attributions'):
        build_staging_tree(web_dir, dist, staging, days=DAYS_IN_WINDOW)


def test_staging_dir_holding_a_repo_refused(tmp_path, web_dir, dist):
    """The guard standing in front of the one irreversible step.

    Publishing force-pushes the staging directory as a repository built from
    scratch, so building into a directory that already holds something is
    how a real repository, or a day from a window that has since moved on,
    reaches the public site.
    """
    holds_a_repo = tmp_path / 'already-a-repo'
    (holds_a_repo / '.git').mkdir(parents=True)
    with pytest.raises(ValueError, match=NOT_EMPTY):
        build_staging_tree(web_dir, dist, holds_a_repo, days=DAYS_IN_WINDOW)


def test_reused_staging_dir_refused(tmp_path, web_dir, dist):
    """A second run into the same --staging is refused.

    Nothing in build_staging_tree deletes, so without the guard 2026-09-01
    would survive from the first run into an index rebuilt for a one-day
    window.
    """
    reused = tmp_path / 'reused'
    build_staging_tree(web_dir, dist, reused, days=DAYS_IN_WINDOW)
    with pytest.raises(ValueError, match=NOT_EMPTY):
        build_staging_tree(web_dir, dist, reused, days=1)
