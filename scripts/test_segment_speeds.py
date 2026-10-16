"""Tests for segment_speeds: geometry, rejections, shapes and periods."""

import datetime as dt
import json
import math
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from types import MappingProxyType
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import segment_speeds as seg

SOFIA = ZoneInfo('Europe/Sofia')

# The unit the fixtures below write into the archive's `speed_ms` field, and
# the value cities/sofia.json carries: km/h, spec violation and all.
# process_day takes it as a required argument, so a test that generates m/s
# has to say so — see test_feed_speed_in_ms_is_converted_to_kmh.
FEED_SPEED_UNIT = 'kmh'

ZERO = float(0)
LAT_STEP_DEG = 0.001
LINE_VERTICES = 11
# A straight north-south line along a meridian, so consecutive-point
# haversine distances are hand-computable ground truth: a meridian arc of
# `d` degrees on a sphere of radius EARTH_RADIUS_M is exactly
# radians(d) * EARTH_RADIUS_M, no spherical trig needed since cos(dlon)
# term drops out entirely on a due-north/south line.
# lat 0.000..0.010, lon 0.0
STRAIGHT_LINE = tuple(
    (LAT_STEP_DEG * idx, ZERO) for idx in range(LINE_VERTICES)
)
# ~111.19m per 0.001 deg, exact for this haversine's sphere radius
STEP_M = math.radians(LAT_STEP_DEG) * seg.EARTH_RADIUS_M

# Same shape_id, geometry moved ~5.5km.
SHIFT_DEG = 0.05
SHIFTED_LINE = tuple((lat + SHIFT_DEG, lon) for lat, lon in STRAIGHT_LINE)

THIRD_BASE_DEG = 2.0
DEGREE_LAT_M = 111_320
DEGREE_LAT_TOLERANCE_M = 200
KMH_PER_MS = 3.6
POLL_SEC = 45
# Past the restart cutoff of half a poll interval, short of a full one.
SHORT_GAP_SEC = 30
FIRST_TS = 1000
SECOND_TS = FIRST_TS + POLL_SEC
# 1787864432 == 2026-08-27 21:00:32 UTC
# == 2026-08-28 00:00:32 Europe/Sofia (summer, UTC+3)
MIDNIGHT_SOFIA_TS = 1787864432

TOLERANCE_M = 1.0
OFF_AXIS_ALONG_TOLERANCE_M = 2.0
OFF_AXIS_PERP_TOLERANCE_M = 5.0
OFF_AXIS_PERP_M = 50
OFF_AXIS_LON_DEG = 0.00045
WINDOW_PROBE_LON_DEG = 0.0002
WINDOW_TOLERANCE = 1e-6
SPEED_TOLERANCE_MS = 0.05
DIFF_TOLERANCE_KMH = 0.01

DEFAULT_FEED_SPEED = 10.0
STALE_FEED_SPEED = 99.0
FEED_SPEED_KMH = 2.5
FEED_SPEED_AS_KMH = 9.0
FASTER_MS = 12.0
SUMMER_MS = 10.0
AUTUMN_MS = 4.0


def _line_shape() -> seg.Shape:
    """Build the Shape of the straight fixture line."""
    return seg.build_shape(list(STRAIGHT_LINE))


def _project_full(shape: seg.Shape, point: tuple) -> tuple:
    """Project `point` onto `shape` over every segment (brute force)."""
    return seg.project_point(shape, *point, 0, len(shape.xs) - 2)


def _sofia_ts(clock: str) -> int:
    """Epoch seconds of `clock` on 2026-08-28, Sofia local time."""
    local = dt.datetime.fromisoformat(f'2026-08-28T{clock}')
    return int(local.replace(tzinfo=SOFIA).timestamp())


def test_haversine_known_distance():
    """One degree of latitude comes out at the textbook ~111.32 km."""
    # 1 degree of latitude is ~111.32 km everywhere (a meridian arc), the
    # textbook approximation used to sanity-check any haversine implementation.
    distance = seg.haversine_m(ZERO, ZERO, 1.0, ZERO)
    assert abs(distance - DEGREE_LAT_M) < DEGREE_LAT_TOLERANCE_M


def test_cum_dist_matches_hand_computed_line():
    """Cumulative distance matches the hand-computed straight line."""
    shape = _line_shape()
    # 10 equal hops of ~111.32m each along a straight meridian line.
    assert shape.cum_dist[0] == pytest.approx(0, abs=0)
    for vertex, dist in enumerate(shape.cum_dist[1:], 1):
        expected = vertex * STEP_M
        assert abs(dist - expected) < TOLERANCE_M, (
            f'vertex {vertex}: {dist} vs {expected}'
        )


def test_project_on_vertex_gives_its_cum_dist():
    """A point on a vertex projects to that vertex's cumulative distance."""
    shape = _line_shape()
    # Sitting exactly on vertex 5 should project to ~5 hops along the line.
    idx, along, perp = _project_full(shape, STRAIGHT_LINE[5])
    assert abs(along - 5 * STEP_M) < TOLERANCE_M
    assert perp < TOLERANCE_M
    assert idx in {4, 5}  # vertex 5 is the shared endpoint of segments 4 and 5


def test_project_off_axis_perp_and_along():
    """An off-axis point gets the right perpendicular and along distance."""
    shape = _line_shape()
    # A point ~50m east of vertex 3 (roughly 0.00045 deg of longitude at the
    # equator, where a degree of longitude is also ~111.32km) should project
    # onto the line near 3 hops along, with ~50m perpendicular offset.
    point = (STRAIGHT_LINE[3][0], OFF_AXIS_LON_DEG)
    _, along, perp = _project_full(shape, point)
    assert abs(along - 3 * STEP_M) < OFF_AXIS_ALONG_TOLERANCE_M
    assert abs(perp - OFF_AXIS_PERP_M) < OFF_AXIS_PERP_TOLERANCE_M


def test_windowed_search_matches_full_search():
    """A window around the right answer finds what a full scan finds.

    Task-required test: a window built around the correct answer must find
    the same segment and along-distance as a brute-force full scan, on the
    same small polyline.
    """
    shape = _line_shape()
    # near vertex 7, slightly off-axis
    point = (STRAIGHT_LINE[7][0], WINDOW_PROBE_LON_DEG)

    full = _project_full(shape, point)

    # A window anchored on a nearby "previous match" distance (6 hops in),
    # narrow enough that it does NOT cover the whole shape, must still land
    # on the same segment/distance as the full scan.
    bounds = seg.window_bounds(
        shape,
        last_dist=6 * STEP_M,
        max_travel_m=2 * STEP_M,
    )
    assert (bounds[1] - bounds[0]) < (
        len(shape.xs) - 2
    )  # confirm the window is genuinely narrower than "full"
    windowed = seg.project_point(shape, *point, *bounds)

    assert windowed[0] == full[0]
    assert abs(windowed[1] - full[1]) < WINDOW_TOLERANCE
    assert abs(windowed[2] - full[2]) < WINDOW_TOLERANCE


def test_timeslot_bins_to_quarter_hours():
    """The slot of a just-after-midnight local time is 00:00."""
    assert seg.timeslot_for(MIDNIGHT_SOFIA_TS, SOFIA) == '00:00'


def test_timeslot_bin_boundaries():
    """A minute before a quarter hour stays in the previous slot."""
    assert seg.timeslot_for(_sofia_ts('14:14:00'), SOFIA) == '14:00'
    assert seg.timeslot_for(_sofia_ts('14:15:00'), SOFIA) == '14:15'


# ─── process_day: one integration test per rejection reason ────────────────

TRIP_ID = 'T1'
ROUTE_ID = 'R1'
SHAPE_ID = 'S1'
VEHICLE_ID = 'V1'


def _trip_map_and_shapes() -> tuple:
    """Return the (trip_map, shapes_by_id) pair of the straight line."""
    # Third field is the shape_key; these tests don't care what it hashes
    # to, only that it addresses the shape, so the bare id stands in.
    trip_map = {TRIP_ID: (ROUTE_ID, SHAPE_ID, SHAPE_ID)}
    shapes_by_id = {SHAPE_ID: _line_shape()}
    return trip_map, shapes_by_id


def _write_day(path: Path, records: list) -> None:
    """Write `records` as one JSON object per line."""
    with path.open('w', encoding='utf-8') as day_file:
        for record in records:
            day_file.write(f'{json.dumps(record)}\n')


def _rec(
    ts: int,
    lat: float,
    lon: float,
    speed_ms: float = DEFAULT_FEED_SPEED,
    trip_id: str = TRIP_ID,
    vehicle_id: str = VEHICLE_ID,
) -> dict:
    """Build one raw archive record."""
    return {
        'snapshot_ts': ts,
        'vehicle_id': vehicle_id,
        'route_id': ROUTE_ID,
        'trip_id': trip_id,
        'lat': lat,
        'lon': lon,
        'bearing': None,
        'speed_ms': speed_ms,
    }


def _read_samples(out_path: Path) -> list:
    """Read the samples process_day wrote, one JSON object per line."""
    lines = out_path.read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in lines]


def _run_day(
    tmp_path: Path,
    records: list,
    *,
    day: str = '2026-08-28',
    out: str = 'out.jsonl',
    feed: tuple | None = None,
    next_map: dict | None = None,
    unit: str = FEED_SPEED_UNIT,
    diffs: list | None = None,
) -> tuple:
    """Run process_day over `records`; return (stats, emitted samples)."""
    trip_map, shapes_by_key = _trip_map_and_shapes() if feed is None else feed
    day_path = tmp_path / f'{day}.jsonl'
    _write_day(day_path, records)
    out_path = tmp_path / out
    stats = seg.process_day(
        day_path,
        out_path,
        trip_map,
        shapes_by_key,
        SOFIA,
        defaultdict(list),
        [] if diffs is None else diffs,
        next_map,
        feed_speed_unit=unit,
        poll_interval_sec=POLL_SEC,
    )
    return stats, _read_samples(out_path)


def _pair(first: int, second: int, **second_fields: float) -> list:
    """Two records one poll apart, at straight-line vertices."""
    return [
        _rec(FIRST_TS, *STRAIGHT_LINE[first]),
        _rec(SECOND_TS, *STRAIGHT_LINE[second], **second_fields),
    ]


def test_accepts_normal_pair_with_plausible_speed(tmp_path: Path):
    """A normal pair is accepted with the hand-computed speed."""
    # one hop (~111.32m) further along, 45s apart, matches collector cadence
    stats, samples = _run_day(tmp_path, _pair(2, 3))
    sample = samples[0]
    assert stats.samples_emitted == 1
    assert sample['route_id'] == ROUTE_ID
    assert sample['shape_id'] == SHAPE_ID
    # ~111.32m / 45s =~ 2.47 m/s
    assert abs(sample['speed_ms'] - (STEP_M / POLL_SEC)) < SPEED_TOLERANCE_MS
    assert sum(stats.reject_counts.values()) == 0


def _assert_rejected(tmp_path: Path, records: list, reason: str) -> None:
    """Assert `records` yield exactly one rejection, `reason`, no sample."""
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts[reason] == 1
    assert not samples


def test_rejects_trip_not_in_static(tmp_path: Path):
    """A trip_id the feed does not know is rejected."""
    records = [_rec(FIRST_TS, ZERO, ZERO, trip_id='UNKNOWN_TRIP')]
    _assert_rejected(tmp_path, records, 'trip_not_in_static')


def test_rejects_gap_too_large(tmp_path: Path):
    """A gap beyond MAX_TIME_GAP_SEC is rejected."""
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[1]),
        _rec(FIRST_TS + seg.MAX_TIME_GAP_SEC + 1, *STRAIGHT_LINE[2]),
    ]
    _assert_rejected(tmp_path, records, 'gap_too_large')


def test_rejects_non_positive_time_delta(tmp_path: Path):
    """A duplicate snapshot_ts is rejected."""
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[1]),
        _rec(FIRST_TS, *STRAIGHT_LINE[2]),
    ]  # duplicate snapshot_ts
    _assert_rejected(tmp_path, records, 'non_positive_time_delta')


def test_rejects_moved_backward_beyond_tolerance(tmp_path: Path):
    """A move far back along the shape is rejected."""
    # STRAIGHT_LINE[1] is far behind vertex 5 along the line
    _assert_rejected(tmp_path, _pair(5, 1), 'moved_backward')


def test_rejects_speed_too_high(tmp_path: Path):
    """A hop that implies over 100 km/h is rejected."""
    # 10 hops (~1113m) in 30 seconds => about 134 km/h
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[0]),
        _rec(FIRST_TS + SHORT_GAP_SEC, *STRAIGHT_LINE[10]),
    ]
    _assert_rejected(tmp_path, records, 'speed_too_high')


def test_rejects_gap_too_short(tmp_path: Path):
    """A fix within half a poll interval is skipped, not paired."""
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[1]),
        _rec(FIRST_TS + 1, *STRAIGHT_LINE[1]),
    ]
    _assert_rejected(tmp_path, records, 'gap_too_short')


def test_gap_too_short_hop_runs_from_earlier_fix(tmp_path: Path):
    """After a skipped restart poll the next hop starts at the earlier one."""
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[1]),
        _rec(FIRST_TS + 1, *STRAIGHT_LINE[1]),
        _rec(FIRST_TS + 1 + POLL_SEC, *STRAIGHT_LINE[2]),
    ]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts['gap_too_short'] == 1
    assert stats.total_pairs == 2
    assert [(row['from_ts'], row['dt_sec']) for row in samples] == [
        (FIRST_TS, POLL_SEC + 1),
    ]


def test_gap_cutoff_sits_at_half_the_cadence(tmp_path: Path):
    """22 s is skipped and 23 s is kept at a 45 s poll interval."""
    below = int(POLL_SEC * seg.MIN_TIME_GAP_CADENCE_FRACTION)
    for gap, skipped in ((below, 1), (below + 1, 0)):
        stats, samples = _run_day(
            tmp_path,
            [
                _rec(FIRST_TS, *STRAIGHT_LINE[1]),
                _rec(FIRST_TS + gap, *STRAIGHT_LINE[2]),
            ],
            out=f'out_{gap}.jsonl',
        )
        assert stats.reject_counts['gap_too_short'] == skipped
        assert len(samples) == 1 - skipped


def test_backward_tolerance_allows_gps_jitter(tmp_path: Path):
    """A wobble well under BACKWARD_TOLERANCE_M is not rejected."""
    # A tiny backward wobble (well under BACKWARD_TOLERANCE_M) must NOT be
    # rejected.
    # a fraction of the tolerance, in degrees latitude
    jitter_deg = (seg.BACKWARD_TOLERANCE_M / 2) / DEGREE_LAT_M
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[5]),
        _rec(SECOND_TS, STRAIGHT_LINE[5][0] - jitter_deg, ZERO),
    ]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts['moved_backward'] == 0
    assert len(samples) == 1


def test_feed_speed_carried_through(tmp_path: Path):
    """The arrival record's own feed speed is carried for validation."""
    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[2], speed_ms=STALE_FEED_SPEED),
        _rec(SECOND_TS, *STRAIGHT_LINE[3], speed_ms=FEED_SPEED_KMH),
    ]
    _, samples = _run_day(tmp_path, records)
    assert samples[0]['feed_speed_kmh'] == pytest.approx(
        FEED_SPEED_KMH,
        abs=0,
    )  # the arrival snapshot's own feed speed, not the departure one


def test_feed_speed_in_ms_is_converted_to_kmh(tmp_path: Path):
    """A feed that sends m/s is converted to km/h.

    A spec-compliant feed sends m/s, and its profile says so. The emitted
    field is km/h for every city, so the same raw 2.5 that stays 2.5 for
    Sofia becomes 9.0 here — the 3.6x that makes a compliant feed read as a
    crawling one if the unit is assumed instead of configured.
    """
    _, samples = _run_day(
        tmp_path,
        _pair(2, 3, speed_ms=FEED_SPEED_KMH),
        unit='ms',
    )
    assert samples[0]['feed_speed_kmh'] == pytest.approx(
        FEED_SPEED_AS_KMH,
        abs=0,
    )


def _derived_kmh(tmp_path: Path) -> float:
    """Derive the speed of the first fixture hop, in km/h."""
    # derived speed comes out of the fixture geometry; feed value is that same
    # speed expressed in km/h, i.e. perfect agreement
    _, samples = _run_day(
        tmp_path,
        _pair(0, 1, speed_ms=ZERO),
        out='probe.jsonl',
    )
    return samples[0]['speed_ms'] * KMH_PER_MS


def test_validation_compares_both_sides_in_kmh(tmp_path: Path):
    """Derived and feed speeds are compared in the same unit.

    The feed reports km/h in a field the raw archive calls speed_ms. Comparing
    it against a derived m/s value (and scaling the difference afterwards)
    produced a number in no unit at all — a 48 km/h median disagreement that
    was really 9. A vehicle whose derived speed matches the feed exactly must
    come out as a zero difference, not as a 2.6x discrepancy.
    """
    derived_kmh = _derived_kmh(tmp_path)

    diffs: list = []
    _run_day(
        tmp_path,
        _pair(0, 1, speed_ms=round(derived_kmh, 3)),
        day='2026-08-29',
        out='out2.jsonl',
        diffs=diffs,
    )

    assert len(diffs) == 1
    assert diffs[0] < DIFF_TOLERANCE_KMH, (
        f'agreeing readings must diff by ~0 km/h, got {diffs[0]}'
    )


# ─── Content-addressed shape identity (2026-08-31 republished-feed bug) ────


def _text(rows: list) -> str:
    """Join `rows` into newline-terminated file text."""
    return '\n'.join([*rows, ''])


def _write_zip(path: Path, members: dict) -> Path:
    """Write a zip of `members` (name -> list of text rows) at `path`."""
    with zipfile.ZipFile(path, 'w') as archive:
        for name, rows in members.items():
            archive.writestr(name, _text(rows))
    return path


def _write_static_zip(
    tmp_path: Path,
    filename: str,
    points: tuple | list,
    shape_id: str = SHAPE_ID,
) -> Path:
    """Write a minimal single-shape, single-trip GTFS zip.

    Minimal single-shape/single-trip GTFS zip, parametrized on the
    shape's own points and its id -- same fixture shape as
    test_export_web.py's _write_static_zip, but built here so two zips can
    share a shape_id while differing only in geometry (the 2026-08-31
    scenario) or share the geometry under two ids (the 2026-09-02 one).
    """
    shapes = ['shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence']
    shapes += [
        f'{shape_id},{point[0]},{point[1]},{idx}'
        for idx, point in enumerate(points)
    ]
    return _write_zip(
        tmp_path / filename,
        {
            'routes.txt': [
                'route_id,route_short_name,route_type',
                f'{ROUTE_ID},9,3',
            ],
            'trips.txt': [
                'trip_id,route_id,service_id,shape_id',
                f'{TRIP_ID},{ROUTE_ID},SVC,{shape_id}',
            ],
            'shapes.txt': shapes,
        },
    )


class _Feed(NamedTuple):
    """What load_static returns for one fixture zip, trip resolved."""

    trip_map: dict
    shapes: dict
    ids: dict
    shape_id: str
    key: str


def _loaded(
    tmp_path: Path,
    filename: str,
    points: tuple | list,
    shape_id: str = SHAPE_ID,
) -> _Feed:
    """Write a fixture zip, load it, and resolve the fixture trip."""
    zip_path = _write_static_zip(tmp_path, filename, points, shape_id)
    trip_map, shapes, ids = seg.load_static(zip_path)
    return _Feed(trip_map, shapes, ids, *trip_map[TRIP_ID][1:])


def test_same_geometry_across_zips_same_key(tmp_path: Path):
    """Identical geometry in two zips gives the same shape_key.

    The 98.3% case: two feed snapshots that happen to describe the same
    shape identically must still produce the same shape_key, or every
    unchanged shape would needlessly split on every republish.
    """
    feed_a = _loaded(tmp_path, 'gtfs_a.zip', STRAIGHT_LINE)
    feed_b = _loaded(
        tmp_path,
        'gtfs_b.zip',
        list(STRAIGHT_LINE),
    )  # same points, different list object

    assert feed_a.key == feed_b.key
    assert set(feed_a.shapes) == set(feed_b.shapes) == {feed_a.key}


def test_changed_geometry_same_id_new_key(tmp_path: Path):
    """A shape_id kept across a republish with new geometry splits.

    Regression test for the actual bug: a shape_id kept across a republish
    while its geometry changed underneath it must produce two distinct
    shape_keys, not one. Against the pre-fix aggregation key
    (shape_id, segment_index) this shape_id would silently pool samples from
    two different physical roads into one median with no counter ever
    firing -- that is exactly what this must prevent.
    """
    feed_a = _loaded(tmp_path, 'gtfs_a.zip', STRAIGHT_LINE)
    feed_b = _loaded(tmp_path, 'gtfs_b.zip', SHIFTED_LINE)

    assert feed_a.key != feed_b.key
    ids_a = feed_a.ids[feed_a.key]
    ids_b = feed_b.ids[feed_b.key]
    assert ids_a == ids_b == {SHAPE_ID}
    # Merging both feeds' shapes (as segment_speeds.py's main() does across
    # snapshots) must keep both geometries addressable, never overwrite one.
    merged = {**feed_a.shapes, **feed_b.shapes}
    assert len(merged) == 2


def _sample_on(tmp_path: Path, feed: _Feed, line: tuple, day: str) -> dict:
    """Run process_day on `line` under `feed`; return its first sample."""
    records = [
        _rec(FIRST_TS, *line[2]),
        _rec(SECOND_TS, *line[3]),
    ]
    _, samples = _run_day(
        tmp_path,
        records,
        day=day,
        out=f'out_{day}.jsonl',
        feed=(feed.trip_map, feed.shapes),
    )
    return samples[0]


def test_changed_geometry_never_pools_samples(tmp_path: Path):
    """Samples of two geometry versions never share a shape_key.

    End-to-end version of the regression above: run process_day(,
    feed_speed_unit=FEED_SPEED_UNIT) against each of the two feeds
    independently and confirm the emitted samples carry the same bare
    shape_id and the same segment_index (so a bare-shape_id aggregation key
    would have pooled them) but different shape_key -- the field
    build_typical_weekday() actually aggregates on.
    """
    feed_a = _loaded(tmp_path, 'gtfs_a.zip', STRAIGHT_LINE)
    feed_b = _loaded(tmp_path, 'gtfs_b.zip', SHIFTED_LINE)

    sample_a = _sample_on(tmp_path, feed_a, STRAIGHT_LINE, '2026-08-27')
    sample_b = _sample_on(tmp_path, feed_b, SHIFTED_LINE, '2026-08-28')

    assert sample_a['shape_id'] == sample_b['shape_id'] == SHAPE_ID
    assert sample_a['segment_index'] == sample_b['segment_index']
    assert sample_a['shape_key'] != sample_b['shape_key']


def test_renumbered_id_same_geometry_same_key(tmp_path: Path):
    """A renumbered shape_id on identical geometry keeps one key.

    The 2026-09-02 finding: the agency kept the geometry and changed the
    shape_id, publishing A4500-A4503 as byte-identical copies of
    A1192/A3949/A1221/A2710. Under the old shape_id@hash key that split
    every affected segment into two series that could never merge again.
    The key is the geometry alone, so it must not move.
    """
    feed_a = _loaded(tmp_path, 'gtfs_a.zip', STRAIGHT_LINE)
    feed_b = _loaded(tmp_path, 'gtfs_b.zip', STRAIGHT_LINE, shape_id='S9')

    assert feed_a.shape_id != feed_b.shape_id
    assert feed_a.key == feed_b.key
    assert feed_b.ids[feed_b.key] == {'S9'}


def _key_at(base: float) -> str:
    """Shape key of a two-point line starting at (base, base)."""
    return seg.build_shape_key([
        (base, base),
        (base + LAT_STEP_DEG, base),
    ])


def test_count_multi_geometry_shape_ids():
    """Only ids published under more than one geometry are counted."""
    key_a1 = _key_at(ZERO)
    key_a2 = _key_at(1.0)
    key_b = _key_at(THIRD_BASE_DEG)
    # "A" published under two geometries, "B" under one.
    shape_ids_by_key = {key_a1: {'A'}, key_a2: {'A'}, key_b: {'B'}}
    assert seg.count_multi_geometry_shape_ids(shape_ids_by_key) == 1


def test_count_multi_id_geometries():
    """Only geometries published under more than one id are counted."""
    key_shared = _key_at(ZERO)
    key_single = _key_at(THIRD_BASE_DEG)
    # One geometry republished under a second id (A4500 over A1192), one not.
    shape_ids_by_key = {key_shared: {'A1192', 'A4500'}, key_single: {'B'}}
    assert seg.count_multi_id_geometries(shape_ids_by_key) == 1


# ─── Per-day static snapshot selection ─────────────────────────────────────

EARLY = ('2026-08-27', Path('a.zip'))
LATE = ('2026-08-31', Path('b.zip'))
MORNING = ('2026-09-10', Path('gtfs_2026-09-10.zip'))
MIDDAY = ('2026-09-10', Path('gtfs_2026-09-10T1100.zip'))


def _picked(snapshots: list, day: str) -> tuple:
    """Return (snapshot date, file name, is_fallback) picked for `day`."""
    snap_date, path, is_fallback = seg.pick_snapshot_for_day(snapshots, day)
    return snap_date, path.name, is_fallback


def test_find_snapshots_ignores_dir_and_dotfiles(tmp_path: Path):
    """Unpacked directories and dotfiles are not snapshots."""
    (tmp_path / 'gtfs_2026-08-27.zip').write_bytes(b'')
    (tmp_path / 'gtfs_2026-08-31.zip').write_bytes(b'')
    (
        tmp_path / 'gtfs_2026-08-27'
    ).mkdir()  # unpacked sibling directory, same stem
    (tmp_path / '.DS_Store').write_bytes(b'')

    snapshots = seg.find_static_snapshots(tmp_path)
    assert [snap_date for snap_date, _ in snapshots] == [
        '2026-08-27',
        '2026-08-31',
    ]


def test_pick_snapshot_between_uses_earlier():
    """A day strictly between two snapshots uses the earlier one."""
    assert _picked([EARLY, LATE], '2026-08-29') == (
        '2026-08-27',
        'a.zip',
        False,
    )


def test_pick_snapshot_on_own_date():
    """A day on a snapshot's own date uses that snapshot."""
    assert _picked([EARLY, LATE], '2026-08-31') == (
        '2026-08-31',
        'b.zip',
        False,
    )


def test_pick_snapshot_after_last():
    """A day after the last snapshot uses the latest one."""
    assert _picked([EARLY, LATE], '2026-09-05') == (
        '2026-08-31',
        'b.zip',
        False,
    )


def test_find_snapshots_orders_intra_day_capture(tmp_path: Path):
    """An intra-day capture sorts after its plain sibling."""
    (tmp_path / 'gtfs_2026-09-10.zip').write_bytes(b'')
    (tmp_path / 'gtfs_2026-09-10T1100.zip').write_bytes(b'')
    (tmp_path / 'gtfs_2026-09-11.zip').write_bytes(b'')

    snapshots = seg.find_static_snapshots(tmp_path)
    assert [path.name for _, path in snapshots] == [
        'gtfs_2026-09-10.zip',
        'gtfs_2026-09-10T1100.zip',
        'gtfs_2026-09-11.zip',
    ]


def test_intra_day_day_uses_morning_snapshot():
    """A day with an intra-day capture is scored against its morning one.

    Once the archive captures hourly, the day of a mid-day republish owns two
    snapshots. The day's own feed is the morning one -- the feed it started
    under -- and the intra-day sibling has to stay reachable as the fallback,
    or every record from before the republish would be scored against a feed
    that did not exist yet while the morning capture sat behind the fallback
    pointer where nothing reads it.
    """
    snapshots = [
        MORNING,
        MIDDAY,
        ('2026-09-11', Path('gtfs_2026-09-11.zip')),
    ]

    snap_date, path, is_fallback = seg.pick_snapshot_for_day(
        snapshots,
        '2026-09-10',
    )
    assert (snap_date, path.name, is_fallback) == (
        '2026-09-10',
        'gtfs_2026-09-10.zip',
        False,
    )
    next_path = seg.next_snapshot_after(snapshots, path)
    assert next_path.name == 'gtfs_2026-09-10T1100.zip'


def test_later_day_uses_last_capture():
    """A later day uses the last capture of the preceding date."""
    # The mirror case: by 2026-09-11 every republish of the 10th has already
    # happened, so the intra-day capture is the current feed, not the morning
    # one.
    assert _picked([MORNING, MIDDAY], '2026-09-11') == (
        '2026-09-10',
        'gtfs_2026-09-10T1100.zip',
        False,
    )


def test_next_snapshot_after_returns_next_or_none():
    """The following snapshot is returned, and None at the end."""
    snapshots = [('2026-09-02', Path('b.zip')), ('2026-09-03', Path('c.zip'))]
    assert seg.next_snapshot_after(snapshots, Path('b.zip')).name == 'c.zip'
    assert seg.next_snapshot_after(snapshots, Path('c.zip')) is None


def test_unknown_trip_resolves_against_next_feed(tmp_path: Path):
    """A trip the day's own feed lacks resolves against the next feed.

    The 2026-09-02 recovery: the agency renumbered 157 trips at midday, the
    RT stream switched to the new trip_ids at once, and the day's own
    snapshot -- taken that morning -- knew none of them. They are all in the
    next morning's capture, so the day's records must resolve against it
    instead of being rejected as trip_not_in_static.
    """
    renumbered = 'T_NEW'
    next_trip_map = {renumbered: (ROUTE_ID, SHAPE_ID, SHAPE_ID)}

    records = [
        _rec(FIRST_TS, *STRAIGHT_LINE[2], trip_id=renumbered),
        _rec(SECOND_TS, *STRAIGHT_LINE[3], trip_id=renumbered),
    ]
    stats, samples = _run_day(
        tmp_path,
        records,
        day='2026-09-02',
        next_map=next_trip_map,
    )

    assert stats.reject_counts['trip_not_in_static'] == 0
    assert stats.records_from_next_feed == 2
    assert stats.trips_from_next_feed == {renumbered}
    assert len(samples) == 1


def test_own_feed_wins_when_both_know_trip(tmp_path: Path):
    """The next feed is a fallback, never an override."""
    # The next feed is a fallback, never an override: a day stays scored
    # against the feed it was actually collected under wherever that feed
    # has an answer.
    next_trip_map = {TRIP_ID: ('OTHER_ROUTE', SHAPE_ID, SHAPE_ID)}

    stats, samples = _run_day(
        tmp_path,
        _pair(2, 3),
        day='2026-09-02',
        next_map=next_trip_map,
    )

    assert stats.records_from_next_feed == 0
    assert samples[0]['route_id'] == ROUTE_ID


def test_pick_snapshot_before_earliest_falls_back():
    """A day before the archive falls back and says so."""
    # There is no feed on record from before the archive started; the
    # earliest available snapshot stands in, but is_fallback must say so
    # rather than let this look like a genuine date match.
    assert _picked([EARLY, LATE], '2026-08-01') == (
        '2026-08-27',
        'a.zip',
        True,
    )


# ─── Emitted bin key contract (export_web.py depends on key.split("|")) ────


def _typical(
    agg: dict,
    dates: list,
    medians: list,
    *,
    day_breakdown: list | None = None,
    periods: list | None = None,
) -> dict:
    """Run build_typical_weekday with the counters left empty."""
    return seg.build_typical_weekday(
        agg,
        dates,
        medians,
        [],
        [] if day_breakdown is None else day_breakdown,
        Counter(),
        {},
        0,
        0,
        [] if periods is None else periods,
    )


def test_segment_key_splits_into_three_fields():
    """The emitted bin key still splits into exactly three fields."""
    # shape_key is bare hex and must never contain "|" -- this is what lets
    # export_web.py's key.split("|") keep working untouched across both
    # changes to the key's contents.
    shape_key = _key_at(ZERO)
    agg = {'period0': {(shape_key, 7, '11:00'): [SUMMER_MS, FASTER_MS]}}
    typical = _typical(agg, ['2026-08-27'], ['2026-08-27'])

    # The period key is the outer level, so the bin key itself is untouched.
    assert list(typical['segments']) == ['period0']
    key = next(iter(typical['segments']['period0']))
    parts = key.split('|')
    assert len(parts) == 3
    assert parts == [shape_key, '7', '11:00']


# ─── Schedule periods (which timetable a day ran) ──────────────────────────


def _trip_rows(trips: list) -> list:
    """Rows of trips.txt for (trip_id, route_id, service_id) triples."""
    rows = ['trip_id,route_id,service_id,shape_id']
    rows += [
        f'{trip},{route},{service},{SHAPE_ID}' for trip, route, service in trips
    ]
    return rows


def _calendar_rows(dates: dict) -> list:
    """Rows of calendar_dates.txt, every exception_type 1."""
    rows = ['service_id,date,exception_type']
    rows += [
        f'{service},{date},1'
        for date, services in dates.items()
        for service in services
    ]
    return rows


def _write_schedule_zip(
    tmp_path: Path,
    filename: str,
    trips: list,
    dates: dict,
) -> Path:
    """Write a GTFS zip with only the files schedule_signature() reads.

    A GTFS zip carrying only what schedule_signature() reads: trips.txt
    (as (trip_id, route_id, service_id) triples) and calendar_dates.txt (as
    "YYYYMMDD" -> [service_id, ...], all exception_type 1, which is the only
    type this feed ever publishes).
    """
    return _write_zip(
        tmp_path / filename,
        {
            'trips.txt': _trip_rows(trips),
            'calendar_dates.txt': _calendar_rows(dates),
        },
    )


def _calendar(service: str, *dates: str) -> dict:
    """Map each `date` ("YYYYMMDD") to the one `service` running on it."""
    return {date: [service] for date in dates}


def _signature(zip_path: Path, date_str: str) -> tuple:
    """Return (signature key, route counts) of `date_str` in `zip_path`."""
    return seg.schedule_signature(
        *seg.load_schedule_calendar(zip_path),
        date_str,
    )


def _day(
    date_str: str,
    counts: MappingProxyType,
    *,
    is_weekday: bool = True,
    key: str | None = None,
) -> dict:
    """Build a day in the shape assign_schedule_periods() takes it.

    signature_key stands in for the sha256 the pipeline computes from the
    same counts.
    """
    return {
        'date': date_str,
        'is_weekday': is_weekday,
        'counts': dict(counts),
        'signature_key': key or f'sig_{date_str}',
    }


TRIP_FIRST = ('T1', 'R1', 'WD')
TRIP_SECOND = ('T2', 'R2', 'WD')
TRIP_THIRD = ('T3', 'R3', 'WD')


def test_added_route_splits_schedule_period(tmp_path: Path):
    """A route appearing puts two days in different schedule periods.

    The 2026-09-04 autumn timetable in miniature: a route appears, so the
    two days ran different schedules and must not pool into one median.
    """
    before = _write_schedule_zip(
        tmp_path,
        'gtfs_before.zip',
        [TRIP_FIRST, TRIP_SECOND],
        _calendar('WD', '20260903'),
    )
    after = _write_schedule_zip(
        tmp_path,
        'gtfs_after.zip',
        [TRIP_FIRST, TRIP_SECOND, TRIP_THIRD],
        _calendar('WD', '20260908'),
    )
    sig_before = _signature(before, '2026-09-03')
    sig_after = _signature(after, '2026-09-08')

    assert sig_before[0] != sig_after[0]
    assert sig_before[1] == {'R1': 1, 'R2': 1}
    assert sig_after[1] == {'R1': 1, 'R2': 1, 'R3': 1}


def test_changed_trip_count_splits_period(tmp_path: Path):
    """A changed trip count alone splits the period."""
    # Tram 8 going 399 -> 617 trips is the same route list, a different
    # timetable. Route ids alone would miss it; the counts are in the key.
    fewer = _write_schedule_zip(
        tmp_path,
        'gtfs_fewer.zip',
        [TRIP_FIRST],
        _calendar('WD', '20260903'),
    )
    more = _write_schedule_zip(
        tmp_path,
        'gtfs_more.zip',
        [TRIP_FIRST, ('T2', 'R1', 'WD')],
        _calendar('WD', '20260908'),
    )
    assert (
        _signature(fewer, '2026-09-03')[0] != _signature(more, '2026-09-08')[0]
    )


def test_renumbered_trip_ids_keep_period(tmp_path: Path):
    """Renumbered trip_ids keep the same schedule period.

    2026-09-02: the agency renumbered 157 trip_ids (and their service_ids)
    mid-day without changing the timetable. A key built from those
    identifiers would declare a new timetable; the content-addressed one
    must not.
    """
    original = _write_schedule_zip(
        tmp_path,
        'gtfs_orig.zip',
        [TRIP_FIRST, ('T2', 'R1', 'WD'), ('T3', 'R2', 'WD')],
        _calendar('WD', '20260902', '20260903'),
    )
    renumbered = _write_schedule_zip(
        tmp_path,
        'gtfs_renum.zip',
        [
            ('T900001', 'R1', 'SVC_7788'),
            ('T900002', 'R1', 'SVC_7788'),
            ('T900003', 'R2', 'SVC_7788'),
        ],
        _calendar('SVC_7788', '20260902', '20260903'),
    )
    assert (
        _signature(original, '2026-09-02')[0]
        == _signature(renumbered, '2026-09-02')[0]
    )


def test_services_off_the_day_stay_out_of_key(tmp_path: Path):
    """Services not running on the day stay out of its key."""
    # A weekend service sitting in the same feed must not leak into the
    # weekday key -- the key is per date, not per feed.
    mixed = _write_schedule_zip(
        tmp_path,
        'gtfs_mixed.zip',
        [TRIP_FIRST, TRIP_SECOND, ('T3', 'R9', 'WE')],
        {**_calendar('WD', '20260903'), **_calendar('WE', '20260905')},
    )
    weekday = _signature(mixed, '2026-09-03')
    weekend = _signature(mixed, '2026-09-05')
    assert weekday[0] != weekend[0]
    assert weekday[1] == {'R1': 1, 'R2': 1}
    assert weekend[1] == {'R9': 1}


def test_uncovered_date_has_empty_signature(tmp_path: Path):
    """A date the feed does not cover hashes to the empty signature."""
    # Retrospective calendar erosion is real (newer snapshots drop rows for
    # past dates), so an absent date must degrade to an empty, obviously
    # distinct signature rather than raise.
    thin = _write_schedule_zip(
        tmp_path,
        'gtfs_thin.zip',
        [TRIP_FIRST],
        _calendar('WD', '20260903'),
    )
    key, counts = _signature(thin, '2026-12-25')
    assert not counts
    assert key != _signature(thin, '2026-09-03')[0]


# The August archive in miniature: ~15,000 trips a weekday, so the 0.5%
# tolerance is 75 trips of movement.
BASE = MappingProxyType({'R1': 10000, 'R2': 5000})
BASE_TRIPS = 15000
BASE_PLUS_THREE = MappingProxyType({'R1': 10002, 'R2': 5001})
TEN_TM_ENDED = MappingProxyType({'R1': 9852, 'R2': 5000})
DRIFT_ONE_STEP = MappingProxyType({'R1': 10070, 'R2': 5000})
DRIFT_TWO_STEPS = MappingProxyType({'R1': 10140, 'R2': 5000})
VARIANT = MappingProxyType({
    'R1': 10000,
    'R2': 5000,
    'R3': 100,
})  # 100 trips past a 75-trip limit
RETURN_FIRST = MappingProxyType({'R1': 10037, 'R2': 5000})
RETURN_SECOND = MappingProxyType({'R1': 10000, 'R2': 5000, 'R3': 60})
AUTUMN = MappingProxyType({
    'R1': 9800,
    'R2': 5000,
})  # 200 from BASE, 300 from the variant
NEWER = MappingProxyType({
    'R1': 10100,
    'R2': 5000,
})  # 100 from older: its own period
FAR = MappingProxyType({'R1': 9000, 'R2': 5000})  # 1,000+ from both
HOLIDAY = MappingProxyType({'R1': 7000, 'R2': 3149})
WEEKEND = MappingProxyType({'R1': 6000, 'R2': 3800})
CLOSER_TO_OLDER = MappingProxyType({'R1': 10030, 'R2': 5000})
TIE_COUNTS = MappingProxyType({'R1': 10050, 'R2': 5000})
TEN_TM_NEW_ROUTE = MappingProxyType({'R1': 9852, 'R2': 5000, 'R3': 55})
AUTUMN_NEW_ROUTE = MappingProxyType({'R1': 10000, 'R2': 5000, 'R3': 300})
NO_ROUTES = MappingProxyType({})
DROPPED_ROUTE_TRIPS = 40
CHURN_RETURN_FIRST = 37
CHURN_RETURN_SECOND = 60
CHURN_CLOSE = 30


def test_handful_of_added_trips_stays_one_period():
    """Three trips moving between two routes stay in one period.

    Three trips moving between two tram routes: 0.02% of a weekday, the
    same timetable by any honest reading, and hashing the signature alone
    would call it a new one. The feed has not published a boundary this
    small — the smallest it shows is 3.15% — so this is the case the
    threshold exists to absorb rather than one it was fitted to.
    """
    days = seg.assign_schedule_periods([
        _day('2026-08-27', BASE),
        _day('2026-08-28', BASE_PLUS_THREE),
    ])
    first, second = days
    assert first['period_key'] == second['period_key'] == 'sig_2026-08-27'
    assert second['churn_vs_reference'] == 3


def test_material_change_starts_new_period():
    """A material timetable change starts a new period."""
    # Route 10TM's 148 trips ending, part of the 595-trip 4.07% step the feed
    # publishes at the end of August: over the threshold, and the two medians
    # must stay apart.
    days = seg.assign_schedule_periods([
        _day('2026-08-28', BASE),
        _day('2026-08-31', TEN_TM_ENDED),
    ])
    first, second = days
    assert second['period_key'] == 'sig_2026-08-31'
    assert first['period_key'] != second['period_key']


def test_drift_measured_against_reference():
    """Drift is measured against the reference, not the previous day.

    Two steps of 70 trips each are inside the tolerance one at a time but
    140 apart end to end. Compared against the previous day the period would
    walk away from the timetable its key names.
    """
    days = seg.assign_schedule_periods([
        _day('2026-08-27', BASE),
        _day('2026-08-28', DRIFT_ONE_STEP),
        _day('2026-08-31', DRIFT_TWO_STEPS),
    ])
    assert days[1]['period_key'] == days[0]['period_key']
    assert days[2]['period_key'] == 'sig_2026-08-31'


def _returning_timetable_days() -> list:
    """Days of the October variant that returns to September's timetable."""
    return seg.assign_schedule_periods([
        _day('2026-09-14', BASE),
        _day('2026-09-15', BASE),
        _day('2026-10-05', VARIANT),
        _day('2026-10-06', VARIANT),
        _day('2026-10-08', RETURN_FIRST),
        # 60 trips from 14 September, 40 from the variant: inside both
        # tolerances. Measured against the current period first, it stays
        # with 14 September only if the return made that period current.
        _day('2026-10-09', RETURN_SECOND),
    ])


def test_returning_timetable_rejoins_period():
    """A timetable that comes back rejoins its earlier period.

    Finding 21 in miniature: 5-7 October 2026 ran a variant past the
    tolerance, and 8 October went back to the 14 September timetable, 37
    trips off its reference. Compared only with the variant, 8 October
    founded a one-day period; it belongs to 14 September's, and from then on
    14 September's reference is the one the following days answer to.
    """
    days = _returning_timetable_days()
    keys = [day['period_key'] for day in days]
    assert keys == [
        'sig_2026-09-14',
        'sig_2026-09-14',
        'sig_2026-10-05',
        'sig_2026-10-05',
        'sig_2026-09-14',
        'sig_2026-09-14',
    ]
    assert [day['period_reference_date'] for day in days[4:]] == [
        '2026-09-14',
        '2026-09-14',
    ]
    assert [day['churn_vs_reference'] for day in days[4:]] == [
        CHURN_RETURN_FIRST,
        CHURN_RETURN_SECOND,
    ]


def test_rejoined_period_lists_noncontiguous_days():
    """The rejoined period lists its days even when not contiguous."""
    periods = seg.build_schedule_periods(
        _returning_timetable_days(),
        {'sig_2026-09-14': 1, 'sig_2026-10-05': 1},
    )
    september = next(
        period for period in periods if period['period_key'] == 'sig_2026-09-14'
    )
    # Not contiguous: the range bounds the period, the list fills it.
    assert september['days_in_median_mon_fri'] == [
        '2026-09-14',
        '2026-09-15',
        '2026-10-08',
        '2026-10-09',
    ]
    assert (september['first_date'], september['last_date']) == (
        '2026-09-14',
        '2026-10-09',
    )
    assert september['max_churn_vs_reference'] == CHURN_RETURN_SECOND
    assert len(periods) == 2


def test_unaccounted_timetable_opens_new_period():
    """A timetable no earlier period accounts for opens a new one."""
    days = seg.assign_schedule_periods([
        _day('2026-09-14', BASE),
        _day('2026-10-05', VARIANT),
        _day('2026-10-12', AUTUMN),
        _day('2026-10-13', AUTUMN, key='sig_2026-10-12'),
    ])
    assert days[2]['period_key'] == 'sig_2026-10-12'
    assert days[2]['period_reference_date'] == '2026-10-12'
    assert days[2]['churn_vs_reference'] == 0
    assert days[3]['period_key'] == 'sig_2026-10-12'
    assert len({day['period_key'] for day in days}) == 3


def _returning(counts: MappingProxyType) -> dict:
    """Assign periods to a day returning after three distinct ones."""
    return seg.assign_schedule_periods([
        _day('2026-09-01', BASE),
        _day('2026-09-02', NEWER),
        _day('2026-09-03', FAR),
        _day('2026-09-04', counts),
    ])[-1]


def test_day_joins_closer_of_two_earlier_periods():
    """A day within tolerance of two earlier periods joins the closer one.

    Two earlier periods 100 trips apart, both within reach of a day the
    current period rejects. Lowest churn wins whichever period is older; a
    tie goes to the one opened later, as current_period_key() breaks ties.
    """
    # older = BASE: 15,000 trips, limit 75
    closer_to_older = _returning(CLOSER_TO_OLDER)  # 30 vs 70
    assert closer_to_older['period_key'] == 'sig_2026-09-01'
    assert closer_to_older['churn_vs_reference'] == CHURN_CLOSE

    closer_to_newer = _returning(DRIFT_ONE_STEP)  # 70 vs 30
    assert closer_to_newer['period_key'] == 'sig_2026-09-02'
    assert closer_to_newer['churn_vs_reference'] == CHURN_CLOSE

    tie = _returning(TIE_COUNTS)  # 50 vs 50
    assert tie['period_key'] == 'sig_2026-09-02'


def test_holiday_weekday_leaves_the_median():
    """A weekday on holiday service leaves the median, founds no period.

    2026-09-07, a Monday running 10,149 trips against 15,013 on the
    weekdays around it. Let through, it would be a period of one day; the
    median of one day is that day.
    """
    days = seg.assign_schedule_periods([
        _day('2026-09-03', BASE),
        _day('2026-09-04', BASE),
        _day('2026-09-07', HOLIDAY),
        _day('2026-09-08', BASE),
    ])
    assert days[2]['excluded_from_median'] == 'reduced_service'
    assert days[2]['period_key'] is None
    # And it did not become the reference: the weekday after it stays in the
    # period it would have interrupted.
    assert days[3]['period_key'] == days[0]['period_key']


def test_calendar_gap_cannot_disarm_holiday_rule():
    """A gap in the calendar cannot disarm the holiday rule.

    Zero-trip weekdays used to count towards the median that the
    reduced-service test measures against. Enough of them (a run of static
    snapshots missing the dates they cover, which this archive has already
    hit once) drags that median to zero, and a zero baseline turns the whole
    test into a no-op: the holiday sails into the median and founds a period
    of its own.
    """
    empty_dates = (
        '2026-10-05',
        '2026-10-06',
        '2026-10-07',
        '2026-10-08',
        '2026-10-09',
        '2026-10-12',
    )
    full_dates = (
        '2026-10-13',
        '2026-10-14',
        '2026-10-15',
        '2026-10-16',
        '2026-10-19',
    )
    days = seg.assign_schedule_periods(
        [_day(date, NO_ROUTES) for date in empty_dates]
        + [_day(date, BASE) for date in full_dates]
        + [_day('2026-10-20', HOLIDAY)],
    )
    holiday = days[-1]
    assert holiday['excluded_from_median'] == 'reduced_service'
    assert holiday['period_key'] is None
    assert all(
        day['excluded_from_median'] == 'no_calendar_rows'
        for day in days[: len(empty_dates)]
    )


def test_no_calendar_rows_is_not_a_holiday():
    """A date without calendar rows is not called a holiday."""
    # Zero trips is a hole in the archive, not the city running fewer buses.
    # Both leave the median; only one of them is a fact about the city.
    days = seg.assign_schedule_periods([
        _day('2026-08-27', BASE),
        _day('2026-12-25', NO_ROUTES),
    ])
    assert days[1]['excluded_from_median'] == 'no_calendar_rows'


def test_weekends_take_no_period():
    """Weekends take no period at all."""
    days = seg.assign_schedule_periods([
        _day('2026-08-28', BASE),
        _day('2026-08-29', WEEKEND, is_weekday=False),
    ])
    weekend = days[1]
    assert weekend['excluded_from_median'] == 'weekend'
    assert weekend['period_key'] is None


def test_period_names_reference_day_when_skipped():
    """A period names its reference day even when the run skipped it.

    A run given an explicit subset of dates still assigns periods over the
    whole archive (see main()), so a period's reference day can be one this
    run did not process. The entry has to name that day rather than the
    earliest day it happens to hold, or the same timetable would be
    described differently depending on how the run was called.
    """
    days = seg.assign_schedule_periods([
        _day('2026-08-27', BASE),
        _day('2026-08-28', BASE_PLUS_THREE),
    ])
    assert [day['period_reference_date'] for day in days] == [
        '2026-08-27',
        '2026-08-27',
    ]

    periods = seg.build_schedule_periods(days[1:], {'sig_2026-08-27': 7})
    assert len(periods) == 1
    assert periods[0]['period_key'] == 'sig_2026-08-27'
    assert periods[0]['reference_date'] == '2026-08-27'
    assert periods[0]['days_in_median_mon_fri'] == ['2026-08-28']


def test_schedule_churn_counts_dropped_route():
    """A dropped route counts in full."""
    dropped = {'R1': 100, 'R2': DROPPED_ROUTE_TRIPS}
    assert seg.schedule_churn({'R1': 100}, dropped) == DROPPED_ROUTE_TRIPS
    assert seg.schedule_churn({}, {}) == 0


def _two_described_periods() -> list:
    """Periods built from two timetables plus a weekend day."""
    days = seg.assign_schedule_periods([
        _day('2026-08-27', BASE),
        _day('2026-08-28', BASE_PLUS_THREE),
        _day('2026-08-29', WEEKEND, is_weekday=False),
        _day('2026-08-31', TEN_TM_NEW_ROUTE),
    ])
    return seg.build_schedule_periods(
        days,
        {'sig_2026-08-27': 10, 'sig_2026-08-31': 20},
    )


def test_build_periods_describe_each_period_days():
    """Each period is described by its days and reference day."""
    periods = _two_described_periods()

    assert [period['period_key'] for period in periods] == [
        'sig_2026-08-27',
        'sig_2026-08-31',
    ]
    first = periods[0]
    assert first['days_in_median_mon_fri'] == ['2026-08-27', '2026-08-28']
    assert (first['first_date'], first['last_date']) == (
        '2026-08-27',
        '2026-08-28',
    )
    assert first['reference_date'] == '2026-08-27'


def test_build_periods_count_trips_and_bins():
    """Period route, trip and bin counts come from the reference day."""
    periods = _two_described_periods()
    first, second = periods

    assert (first['route_count'], first['trip_count']) == (
        2,
        BASE_TRIPS,
    )  # the reference day
    assert first['max_churn_vs_reference'] == 3
    assert first['bin_count'] == 10
    # The weekend day is in no period; day_breakdown carries its reason.
    assert sum(len(period['days_in_median_mon_fri']) for period in periods) == 3
    assert second['route_count'] == 3


SPLIT_KEY = _key_at(ZERO)
SPLIT_BIN = f'{SPLIT_KEY}|3|08:00'


def _split_by_period() -> dict:
    """Typical weekday of one bin observed under two timetables."""
    bin_key = (SPLIT_KEY, 3, '08:00')
    agg = {
        'summer': {bin_key: [SUMMER_MS, SUMMER_MS]},
        'autumn': {bin_key: [AUTUMN_MS, AUTUMN_MS]},
    }
    days = seg.assign_schedule_periods([
        _day('2026-09-03', BASE, key='summer'),
        _day('2026-09-08', AUTUMN_NEW_ROUTE, key='autumn'),
    ])
    periods = seg.build_schedule_periods(days, {'summer': 1, 'autumn': 1})
    return _typical(
        agg,
        ['2026-09-03', '2026-09-08'],
        ['2026-09-03', '2026-09-08'],
        periods=periods,
    )


def test_two_periods_keep_separate_medians():
    """The same bin under two timetables yields two medians.

    The bug this whole split exists for: the same segment and timeslot
    observed under two timetables must yield two medians, not one average
    of both.
    """
    segments = _split_by_period()['segments']

    assert segments['summer'][SPLIT_BIN]['median_speed_ms'] == pytest.approx(
        SUMMER_MS,
        abs=0,
    )
    assert segments['autumn'][SPLIT_BIN]['median_speed_ms'] == pytest.approx(
        AUTUMN_MS,
        abs=0,
    )
    # Pooled, the median would have been 7.0 and n_samples 4.
    assert segments['summer'][SPLIT_BIN]['n_samples'] == 2
    assert segments['autumn'][SPLIT_BIN]['n_samples'] == 2


def test_two_periods_count_bins_per_period():
    """Bins are counted per period, the road once."""
    typical = _split_by_period()

    assert (
        typical['segment_count'] == 2
    )  # one bin per period, counted per period
    assert (
        typical['distinct_segment_count'] == 1
    )  # and one piece of road, counted once
    assert [period['period_key'] for period in typical['schedule_periods']] == [
        'summer',
        'autumn',
    ]


def test_day_breakdown_names_timetable_and_reason():
    """Every day names its timetable and the reason it was excluded."""
    # Not a pipeline run -- just the contract that the field is present and
    # weekends are not blanked out.
    day_breakdown = [
        {
            'date': '2026-08-29',
            'schedule_signature_key': 'wknd_sig',
            'schedule_period_key': None,
            'excluded_from_median': 'weekend',
        },
        {
            'date': '2026-09-07',
            'schedule_signature_key': 'hol_sig',
            'schedule_period_key': None,
            'excluded_from_median': 'reduced_service',
        },
        {
            'date': '2026-08-31',
            'schedule_signature_key': 'bbb',
            'schedule_period_key': 'bbb',
            'excluded_from_median': None,
        },
    ]
    typical = _typical(
        {'bbb': {}},
        ['2026-08-29', '2026-09-07', '2026-08-31'],
        ['2026-08-31'],
        day_breakdown=day_breakdown,
    )
    # Every day says which timetable it ran, whether or not it fed a median.
    assert all(
        day['schedule_signature_key'] for day in typical['day_breakdown']
    )
    assert typical['days_excluded_from_median'] == [
        {'date': '2026-08-29', 'reason': 'weekend'},
        {'date': '2026-09-07', 'reason': 'reduced_service'},
    ]
