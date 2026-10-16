"""Tests of export_web.py: bundles, indexes, geometry and the manifest."""

import itertools
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import export_web as web
from segment_speeds import build_shape, load_static, project_onto_segment
from webexport.model import ManifestInputs

SHAPE_ID = 'S1'
ROUTE_ID = 'R_BUS'
UTF8 = 'utf-8'
BUS_ROUTE_TYPE = 3
LAT_STEP = 0.001
LINE_POINTS = 11
ZERO = float(0)
MS_TO_KMH = 3.6
PAST_END_M = 10_000
SEGMENT_END_M = 200.0
SLOT_MIDNIGHT = '00:00'
SLOT_MORNING = '08:00'
SUMMER = 'summer00000000'
AUTUMN = 'autumn00000000'
GTFS_NAME = 'gtfs_2026-09-03.zip'
DAY = '2026-09-08'
BINS_DROPPED_PCT = 40.0
SEGMENTS_DROPPED_PCT = 25.0
SUMMER_ROUTES = 135
SUMMER_TRIPS = 14907
AUTUMN_ROUTES = 138
AUTUMN_TRIPS = 15600
SUMMER_KMH = 36
AUTUMN_KMH = 14

# Same fixture idea as test_segment_speeds.py: a straight north-south line,
# so "distance along the shape increases" trivially means "latitude increases".
STRAIGHT_LINE = tuple((LAT_STEP * step, ZERO) for step in range(LINE_POINTS))

# Bins of one segment, one thin, one at the cutoff and one well above it.
BIN_THIN = (SHAPE_ID, 0, SLOT_MIDNIGHT)
BIN_AT_CUTOFF = (SHAPE_ID, 1, SLOT_MIDNIGHT)
BIN_THICK = (SHAPE_ID, 2, SLOT_MIDNIGHT)

# 100 m of straight run whose vertices wander under a metre off the line,
# then a right-angle turn.
WANDERING_RUN = (
    (ZERO, ZERO),
    (25.0, 0.4),
    (50.0, -0.6),
    (75.0, 0.9),
    (100.0, ZERO),
    (100.0, 50.0),
    (100.0, 100.0),
)
RUN_CORNERS = ((ZERO, ZERO), (100.0, ZERO), (100.0, 100.0))

# Segment 0 gets 15 slots (the gate exactly) of 10,11,...,24 km/h, with one
# outlier added so that the median and the mean disagree.
SPEEDS_WITH_OUTLIER = (*range(10, 24), 90)
OUTLIER_MEDIAN = 17
FLAT_KMH = 20
SECOND_SEGMENT_KMH = 30
SPEED_MS_AT_SLOT = 5.0


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding=UTF8))


def _route_rows(extra_route: tuple | None) -> tuple:
    routes = ['route_id,route_short_name,route_type', f'{ROUTE_ID},9,3']
    trips = [
        'trip_id,route_id,service_id,shape_id',
        f'T1,{ROUTE_ID},SVC,{SHAPE_ID}',
    ]
    if extra_route:
        route_id, route_type = extra_route
        routes.append(f'{route_id},11,{route_type}')
        trips.append(f'T2,{route_id},SVC,{SHAPE_ID}')
    return routes, trips


def _shape_rows() -> list:
    header = 'shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence'
    points = enumerate(STRAIGHT_LINE)
    return [header] + [
        f'{SHAPE_ID},{lat},{lon},{seq}' for seq, (lat, lon) in points
    ]


def _gtfs_tables(extra_route: tuple | None) -> dict:
    routes, trips = _route_rows(extra_route)
    return {
        'routes.txt': routes,
        'trips.txt': trips,
        'shapes.txt': _shape_rows(),
    }


def _write_static_zip(tmp_path: Path, extra_route: tuple | None = None) -> Path:
    """Write a minimal GTFS zip: one shape, one bus route.

    A second route on the same shape is added when `extra_route` is given, so
    a mixed route_type can be exercised.
    """
    path = tmp_path / 'gtfs.zip'
    with zipfile.ZipFile(path, 'w') as archive:
        for name, lines in _gtfs_tables(extra_route).items():
            archive.writestr(name, '\n'.join([*lines, '']))
    return path


def _line() -> list:
    return list(STRAIGHT_LINE)


def test_rounding_keeps_point_order():
    """Rounding to COORD_DECIMALS never flips the order of two endpoints."""
    shape = build_shape(_line())
    start = web.point_along_shape(shape, ZERO)
    end = web.point_along_shape(shape, SEGMENT_END_M)
    # 5 decimals (~1.1m) is far finer than a 200m segment -- rounding must
    # never flip which endpoint comes first along a straight line.
    assert end[0] > start[0]
    assert start[0] == round(start[0], web.COORD_DECIMALS)
    assert start[1] == round(start[1], web.COORD_DECIMALS)


def test_point_along_shape_clamps_past_end():
    """A distance beyond the shape's end resolves to the end point."""
    shape = build_shape(_line())
    far_past_end = web.point_along_shape(shape, shape.cum_dist[-1] + PAST_END_M)
    at_end = web.point_along_shape(shape, shape.cum_dist[-1])
    assert far_past_end == at_end


def test_simplify_keeps_a_real_corner():
    """Near-collinear points drop, a right-angle corner stays.

    Metres in the local plane build_geometry simplifies in: 100 m of
    straight run whose vertices wander under a metre off the line, then a
    right-angle turn. The wander is what a 200 m bin is full of and what
    format_version 1 was right to drop; the corner is what it was wrong to.
    """
    simplified = web.simplify_rdp(WANDERING_RUN, web.SIMPLIFY_TOLERANCE_M)
    assert simplified == list(RUN_CORNERS)


def _gap_to_polyline(point: tuple, polyline: list) -> float:
    return min(
        project_onto_segment(*point, *start, *end)[1]
        for start, end in itertools.pairwise(polyline)
    )


def test_simplify_never_drifts_beyond_tolerance():
    """No dropped vertex ends up further than the tolerance from the line."""
    simplified = web.simplify_rdp(WANDERING_RUN, web.SIMPLIFY_TOLERANCE_M)
    # The guarantee the manifest states: no dropped vertex ends up further
    # than the tolerance from the line drawn in its place.
    for point in WANDERING_RUN:
        assert _gap_to_polyline(point, simplified) <= web.SIMPLIFY_TOLERANCE_M


def _bin(count: int) -> tuple:
    return (5.0, count)


def test_threshold_drops_bins_below_cutoff():
    """Only the bin under the cutoff goes, and the counts say so."""
    bins = {BIN_THIN: _bin(1), BIN_AT_CUTOFF: _bin(2)}
    bins[BIN_THICK] = _bin(5)
    retained, n_before, n_after = web.apply_threshold(bins, min_samples=2)
    assert n_before == len(bins)
    assert n_after == len(bins) - 1
    assert BIN_THIN not in retained
    assert BIN_AT_CUTOFF in retained
    assert BIN_THICK in retained


def test_segment_pairs_dedupes_across_timeslots():
    """A segment seen in two slots is one pair."""
    bins = {
        (SHAPE_ID, 0, SLOT_MIDNIGHT): (5.0, 2),
        (SHAPE_ID, 0, '00:15'): (6.0, 2),
        ('S2', 3, SLOT_MIDNIGHT): (2.0, 2),
    }
    assert web.segment_pairs(bins) == {(SHAPE_ID, 0), ('S2', 3)}


def _two_bins() -> tuple:
    bins = {
        (SHAPE_ID, 0, SLOT_MIDNIGHT): (5.0, 3),
        (SHAPE_ID, 3, '00:15'): (2.0, 4),
    }
    geometry, index_of, missing = web.build_geometry(
        web.segment_pairs(bins),
        {SHAPE_ID: build_shape(_line())},
    )
    assert missing == 0
    return bins, geometry, index_of


def test_geometry_is_csr_with_two_points_each():
    """Every segment's slice of the CSR arrays holds the two bin endpoints."""
    _, geometry, _ = _two_bins()
    offsets = geometry['point_offset']
    # format_version 2 CSR: one offset per segment plus a closing one, and
    # every segment's slice holds at least the two bin endpoints.
    assert len(offsets) == len(geometry['segment_index']) + 1
    assert offsets[-1] == len(geometry['lat'])
    assert len(geometry['lat']) == len(geometry['lon'])
    widths = [end - start for start, end in itertools.pairwise(offsets)]
    assert all(width >= 2 for width in widths)


def _bin_key(geometry: dict, pos: int, slot: str) -> tuple:
    shape_key = geometry['shape_keys'][geometry['shape_idx'][pos]]
    return (shape_key, geometry['segment_index'][pos], slot)


def _assert_slot_resolves(slot: str, payload: dict, bins: dict, geometry: dict):
    assert payload['timeslot'] == slot
    for pos, speed_kmh, count in zip(
        payload['segment_idx'],
        payload['speed_kmh'],
        payload['n_samples'],
        strict=True,
    ):
        key = _bin_key(geometry, pos, slot)
        assert key in bins
        assert bins[key][1] == count
        assert speed_kmh == round(bins[key][0] * MS_TO_KMH)


def test_timeslot_index_resolves_to_segment():
    """A timeslot's segment index resolves back to its shape and segment."""
    bins, geometry, index_of = _two_bins()
    timeslot_files = web.build_timeslot_files(bins, index_of)
    assert set(timeslot_files) == {SLOT_MIDNIGHT, '00:15'}
    for slot_and_payload in timeslot_files.items():
        _assert_slot_resolves(*slot_and_payload, bins, geometry)


def test_build_geometry_counts_missing_shapes():
    """A shape absent from the feed is counted, not a crash."""
    geometry, index_of, missing = web.build_geometry(
        {('GHOST', 0)},
        shapes_by_key={},
    )
    assert missing == 1
    assert geometry['shape_ids'] == []
    assert index_of == {}


def test_timeslot_files_skip_unresolved_bins():
    """A bin geometry could not resolve is dropped, not referenced."""
    # index_of has nothing for ("S1", 0) -- as if its shape were missing from
    # the static feed passed to build_geometry -- so the bin must be dropped,
    # not crash or reference a nonexistent index.
    bins = {(SHAPE_ID, 0, SLOT_MIDNIGHT): (5.0, 3)}
    timeslot_files = web.build_timeslot_files(bins, index_of={})
    assert timeslot_files == {}


def _manifest(**changes: object) -> dict:
    """Build a manifest from the usual inputs with `changes` applied."""
    inputs = {
        'mode': DAY,
        'min_samples': 2,
        'bins_before': 10,
        'bins_after': 6,
        'pairs_before': 4,
        'pairs_after': 3,
        'missing_shapes': 0,
        'timeslot_labels': [SLOT_MORNING],
        'source': {},
        'days_processed': [],
        'days_in_median': [],
        'incomplete_days': {},
        'shapes_observed': 3,
        'shapes_written': 3,
        'total_static_shapes': 10,
        'multi_route_shapes': 1,
    }
    return web.build_manifest(ManifestInputs(**{**inputs, **changes}))


def _counting_manifest() -> dict:
    return _manifest(
        mode='typical_weekday',
        timeslot_labels=['00:15', SLOT_MIDNIGHT],
        source={'typical_weekday_file': 'typical_weekday.json'},
        days_processed=['2026-08-27'],
        days_in_median=['2026-08-27'],
        incomplete_days={
            '2026-08-27': 'only 52.45% coverage of the calendar day',
        },
    )


REQUIRED_MANIFEST_FIELDS = (
    'format_version',
    'generated_at',
    'mode',
    'source',
    'days_processed',
    'days_in_median',
    'incomplete_days',
    'preprocessing_thresholds',
    'web_export',
    'segment_count',
    'timeslot_count',
    'timeslots',
    'known_limitations',
)


@pytest.mark.parametrize('key', REQUIRED_MANIFEST_FIELDS)
def test_manifest_has_required_field(key):
    """Every field the page reads is present in the manifest."""
    assert key in _counting_manifest(), (
        f'missing required manifest field: {key}'
    )


def test_manifest_derived_counts():
    """Counts derived from the inputs are right."""
    manifest = _counting_manifest()
    assert manifest['web_export']['min_samples_threshold'] == 2
    assert manifest['web_export']['bins_dropped'] == 4
    assert manifest['segment_count'] == 3
    assert manifest['timeslots'] == [
        SLOT_MIDNIGHT,
        '00:15',
    ]  # sorted, regardless of input order


def test_manifest_dropped_percentages():
    """Dropped bins and segments are reported as exact percentages."""
    web_export = _counting_manifest()['web_export']
    assert web_export['bins_dropped_pct'] == pytest.approx(
        BINS_DROPPED_PCT,
        abs=0,
    )
    assert web_export['segments_dropped_pct'] == pytest.approx(
        SEGMENTS_DROPPED_PCT,
        abs=0,
    )


def _bin_row(speed_ms: float) -> dict:
    return {
        'shape_id': SHAPE_ID,
        'shape_key': 'S1@abc12345',
        'segment_index': 0,
        'timeslot': SLOT_MIDNIGHT,
        'speed_ms': speed_ms,
    }


PAIR_SPEEDS = (1.0, 3.0)
SINGLE_SPEED = 4.0


def _jsonl_rows(*speeds: float) -> str:
    return ''.join(f'{json.dumps(_bin_row(speed))}\n' for speed in speeds)


def test_aggregate_day_bins_median_and_count(tmp_path):
    """A bin's speed is the median of its rows, with the row count."""
    path = tmp_path / 'segment_speeds_2026-08-28.jsonl'
    path.write_text(_jsonl_rows(*PAIR_SPEEDS), encoding=UTF8)
    bins = web.aggregate_day_bins(path)
    assert bins == {('S1@abc12345', 0, SLOT_MIDNIGHT): (2.0, 2)}


def test_aggregate_day_bins_tolerates_torn_line(tmp_path):
    """A half-written trailing line is skipped."""
    path = tmp_path / 'segment_speeds_2026-08-28.jsonl'
    good = json.dumps(_bin_row(SINGLE_SPEED))
    torn = '{"shape_id": "S1", "segment_index"'
    path.write_text(f'{good}\n{torn}', encoding=UTF8)
    bins = web.aggregate_day_bins(path)
    assert bins == {('S1@abc12345', 0, SLOT_MIDNIGHT): (SINGLE_SPEED, 1)}


def _route_geometry(tmp_path: Path) -> tuple:
    static_zip = _write_static_zip(tmp_path)
    _, shapes_by_key, shape_ids_by_key = load_static(static_zip)
    shape_key = next(iter(shapes_by_key))  # only one shape in this fixture
    built = web.build_geometry(
        {(shape_key, 0), (shape_key, 1)},
        shapes_by_key,
        web.load_route_info(static_zip),
        shape_ids_by_key,
    )
    assert built[2] == 0
    return built[0], shape_key


def test_geometry_carries_route_metadata(tmp_path: Path):
    """Route metadata rides once per shape, aligned with shape_ids.

    Feature 3 (transport-type filter) needs to know a tram from a bus, and
    shape_id alone cannot say. Metadata rides once per shape, aligned with
    geometry["shape_ids"], so a filter is one hop through shape_idx.
    """
    geometry, shape_key = _route_geometry(tmp_path)
    pos = geometry['shape_keys'].index(shape_key)
    assert geometry['shape_ids'][pos] == [SHAPE_ID]
    assert geometry['shape_route_ids'][pos] == [ROUTE_ID]
    assert geometry['shape_route_type'][pos] == BUS_ROUTE_TYPE  # routes.txt
    assert len(geometry['shape_route_type']) == len(geometry['shape_ids'])


def test_route_type_unset_for_mixed_types(tmp_path: Path):
    """A shape served by a tram and a bus route has a null route type.

    A shape served by both a tram and a bus route cannot answer a type filter,
    so the field is null rather than resolved by picking whichever route came
    first — a filter that quietly guesses is worse than one that shows nothing.
    """
    static_zip = _write_static_zip(tmp_path, extra_route=('R_TRAM', '0'))
    route_info = web.load_route_info(static_zip)
    assert route_info[SHAPE_ID]['route_type'] is None
    assert route_info[SHAPE_ID]['route_ids'] == sorted([ROUTE_ID, 'R_TRAM'])


# ─── Schedule periods: one bundle each, plus an index over them ────────────


def _period(key: str, day: str, routes: int, trips: int) -> dict:
    return {
        'period_key': key,
        'days': [day],
        'days_in_median_mon_fri': [day],
        'first_date': day,
        'last_date': day,
        'route_count': routes,
        'trip_count': trips,
        'bin_count': 2,
    }


SUMMER_BINS = ((10.0, 5), (9.0, 5))
AUTUMN_BINS = ((4.0, 3), (5.0, 3))


def _bin_stats(speed_ms: float, count: int) -> dict:
    return {'median_speed_ms': speed_ms, 'n_samples': count}


def _write_typical_weekday(path: Path, shape_key: str) -> None:
    """Write two schedule periods over the same segment.

    The summer timetable and the autumn one that replaced it. Both cover the
    same (segment, timeslot) bins, which is exactly the case a single pooled
    median would blur.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    document = {
        'days_processed': ['2026-09-03', DAY],
        'days_in_median_mon_fri': ['2026-09-03', DAY],
        'static_feeds': [],
        'schedule_periods': [
            _period(SUMMER, '2026-09-03', SUMMER_ROUTES, SUMMER_TRIPS),
            _period(AUTUMN, DAY, AUTUMN_ROUTES, AUTUMN_TRIPS),
        ],
        'segments': {
            SUMMER: {
                f'{shape_key}|0|{SLOT_MORNING}': _bin_stats(*SUMMER_BINS[0]),
                f'{shape_key}|1|{SLOT_MORNING}': _bin_stats(*SUMMER_BINS[1]),
            },
            AUTUMN: {
                f'{shape_key}|0|{SLOT_MORNING}': _bin_stats(*AUTUMN_BINS[0]),
                f'{shape_key}|0|08:15': _bin_stats(*AUTUMN_BINS[1]),
            },
        },
    }
    path.write_text(json.dumps(document), encoding=UTF8)


def test_typical_bins_keep_each_period_apart(tmp_path: Path):
    """Bins of different schedule periods are never pooled."""
    path = tmp_path / 'typical_weekday.json'
    _write_typical_weekday(path, 'abc123')
    bins_by_period, raw = web.load_typical_weekday_bins(path)

    assert set(bins_by_period) == {SUMMER, AUTUMN}
    assert bins_by_period[SUMMER]['abc123', 0, SLOT_MORNING] == SUMMER_BINS[0]
    assert bins_by_period[AUTUMN]['abc123', 0, SLOT_MORNING] == AUTUMN_BINS[0]
    assert len(raw['schedule_periods']) == 2


def test_current_period_is_best_observed():
    """The period with most weekdays wins, not the newest timetable."""
    periods = [
        {
            'period_key': 'old',
            'days_in_median_mon_fri': ['2026-08-27', '2026-08-28'],
        },
        # Newer, and the timetable actually in force, but one day of samples
        # is not a median over weekdays (D4). It wins once it is observed as
        # widely as the period it replaced, not before.
        {'period_key': 'new', 'days_in_median_mon_fri': [DAY]},
        # A weekend-only period is never the current weekday median (D4),
        # even when its dates are the most recent in the archive.
        {'period_key': 'weekend', 'days_in_median_mon_fri': []},
    ]
    weekend_only = [{'period_key': 'weekend', 'days_in_median_mon_fri': []}]
    assert web.current_period_key(periods) == 'old'
    assert web.current_period_key([]) is None
    assert web.current_period_key(weekend_only) is None


def test_current_period_tie_goes_to_newer():
    """Equal weekday counts resolve to the later period."""
    periods = [
        {
            'period_key': 'old',
            'days_in_median_mon_fri': ['2026-08-27', '2026-08-28'],
        },
        {
            'period_key': 'new',
            'days_in_median_mon_fri': [DAY, '2026-09-09'],
        },
    ]
    assert web.current_period_key(periods) == 'new'


REQUIRED_INDEX_FIELDS = (
    'format_version',
    'generated_at',
    'mode',
    'current_period',
    'period_count',
    'periods',
    'days_excluded_from_median',
)


def test_period_index_names_the_current_one():
    """The index counts the periods, names the current and the excluded days."""
    entries = [
        {'period_key': 'old', 'path': 'old'},
        {'period_key': 'new', 'path': 'new'},
    ]
    # A bundle can only list its own days, so the days that entered no median
    # at all are named here or nowhere in the web tree.
    excluded = [
        {'date': '2026-09-05', 'reason': 'weekend'},
        {'date': '2026-09-07', 'reason': 'reduced_service'},
    ]
    period_index = web.build_period_index(entries, 'new', excluded)
    for key in REQUIRED_INDEX_FIELDS:
        assert key in period_index, f'missing required index field: {key}'
    assert period_index['mode'] == 'typical_weekday_index'
    assert period_index['period_count'] == len(entries)
    assert period_index['current_period'] == 'new'
    assert period_index['days_excluded_from_median'] == excluded


def _prepare_export(tmp_path: Path) -> tuple:
    """Lay out a static dir and a data dir holding the two-period fixture."""
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    _write_static_zip(static_dir).rename(static_dir / GTFS_NAME)
    data_dir = tmp_path / 'data'
    _, shapes_by_key, _ = load_static(static_dir / GTFS_NAME)
    shape_key = next(iter(shapes_by_key))
    _write_typical_weekday(
        data_dir / 'processed' / 'typical_weekday.json',
        shape_key,
    )
    return static_dir, data_dir, shape_key


def _run_export(monkeypatch, *args: str) -> None:
    monkeypatch.setattr(sys, 'argv', ['export_web.py', *args])
    web.main()


def _bundle_manifest(bundle: Path) -> dict:
    return _read_json(bundle / 'manifest.json')


def _assert_period_bundle(root: Path, period_key: str, by_key: dict):
    bundle = root / period_key
    assert (bundle / 'geometry.json').exists()
    manifest = _bundle_manifest(bundle)
    assert manifest['mode'] == 'typical_weekday'
    assert manifest['schedule_period']['period_key'] == period_key
    assert by_key[period_key]['path'] == period_key
    slots = sorted(path.stem for path in (bundle / 'timeslots').glob('*.json'))
    assert slots == [label.replace(':', '') for label in manifest['timeslots']]


def _first_speed(root: Path, period_key: str) -> int:
    slot_file = root / period_key / 'timeslots' / '0800.json'
    return json.loads(slot_file.read_text())['speed_kmh'][0]


def _seed_earlier_day(data_dir: Path) -> None:
    """Add a day bundle from an earlier run.

    This typical-weekday-only run is never told about it: web/index.json is
    rebuilt by scanning the output directory, so it has to keep listing the
    day anyway.
    """
    earlier_day = data_dir / 'web' / '2026-08-27'
    earlier_day.mkdir(parents=True)
    (earlier_day / 'manifest.json').write_text('{}', encoding=UTF8)


def _period_entries(root: Path, data_dir: Path) -> dict:
    root_index = _read_json(data_dir / 'web' / 'index.json')
    assert root_index['days'] == [{'date': '2026-08-27', 'path': '2026-08-27'}]
    assert root_index['typical_weekday']['current_period'] == AUTUMN

    period_index = _bundle_manifest(root)
    # Both fixture periods hold one weekday, so the tie breaks to the later.
    assert period_index['current_period'] == AUTUMN
    return {entry['period_key']: entry for entry in period_index['periods']}


def _assert_two_medians(root: Path):
    # The two periods really are two medians of the same segment, not one.
    assert _first_speed(root, SUMMER) == SUMMER_KMH  # 10.0 m/s
    assert _first_speed(root, AUTUMN) == AUTUMN_KMH  # 4.0 m/s
    # 08:15 exists only under the autumn timetable, so only its bundle has it.
    assert (root / AUTUMN / 'timeslots' / '0815.json').exists()
    assert not (root / SUMMER / 'timeslots' / '0815.json').exists()


def test_export_writes_bundle_per_period(
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    """Each schedule period gets a bundle, and an index lists them all."""
    static_dir, data_dir, _ = _prepare_export(tmp_path)
    _seed_earlier_day(data_dir)

    _run_export(
        monkeypatch,
        str(static_dir),
        str(data_dir),
        '--min-samples',
        '2',
    )

    root = data_dir / 'web' / 'typical_weekday'
    by_key = _period_entries(root, data_dir)
    assert set(by_key) == {SUMMER, AUTUMN}
    assert by_key[SUMMER]['days_in_median'] == ['2026-09-03']
    assert by_key[AUTUMN]['route_count'] == AUTUMN_ROUTES

    for period_key in (SUMMER, AUTUMN):
        _assert_period_bundle(root, period_key, by_key)
    _assert_two_medians(root)


def _autumn_manifest(data_dir: Path) -> dict:
    return _bundle_manifest(data_dir / 'web' / 'typical_weekday' / AUTUMN)


def _assert_sofia_attribution(manifest: dict):
    attribution = manifest['attribution']
    assert attribution['licence'] == 'CC BY-SA 4.0'
    # Only the feeds this export read. The profile also states terms for
    # trip_updates and alerts, and a manifest carrying those would credit
    # data no bundle here shows.
    assert attribution['feed_licences'] == {
        'vehicle_positions': 'CC BY 4.0',
        'static': 'CC BY-SA 4.0',
    }
    assert attribution['source_url'] == 'https://urbandata.sofia.bg'
    assert any(
        METRO_LIMITATION in line for line in manifest['known_limitations']
    )


METRO_LIMITATION = 'no Sofia metro vehicles'


def test_export_carries_city_attribution(
    tmp_path: Path,
    monkeypatch,
):
    """The licence and the operator travel from the profile to the manifest.

    The licence and the operator's name travel profile -> manifest -> page, so
    the site credits the feed it is actually showing. An export that cannot
    name its city carries attribution: null instead of someone else's licence.
    """
    static_dir, data_dir, shape_key = _prepare_export(tmp_path)

    _run_export(monkeypatch, str(static_dir), str(data_dir), '--city', 'sofia')
    _assert_sofia_attribution(_autumn_manifest(data_dir))

    # Same export, city unresolved: no attribution rather than a wrong one.
    anon_dir = tmp_path / 'anon'
    _write_typical_weekday(
        anon_dir / 'processed' / 'typical_weekday.json',
        shape_key,
    )
    _run_export(monkeypatch, str(static_dir), str(anon_dir))
    anon = _autumn_manifest(anon_dir)
    assert anon['attribution'] is None
    assert not any(
        METRO_LIMITATION in line for line in anon['known_limitations']
    )


def test_export_drops_stale_period_directory(
    tmp_path: Path,
    monkeypatch,
):
    """The tree is rebuilt each run, so a stale bundle cannot survive.

    A stale period bundle cannot survive as a directory the index no longer
    mentions.
    """
    static_dir, data_dir, _ = _prepare_export(tmp_path)
    stale = data_dir / 'web' / 'typical_weekday' / 'gone000000000000'
    stale.mkdir(parents=True)
    (stale / 'manifest.json').write_text('{}', encoding=UTF8)

    _run_export(monkeypatch, str(static_dir), str(data_dir))

    assert not stale.exists()


def test_manifest_names_period_as_limitation():
    """A period export names its weekdays; a day export names no timetable."""
    period = {
        'period_key': 'abc',
        'first_date': DAY,
        'last_date': '2026-09-11',
        'route_count': AUTUMN_ROUTES,
        'trip_count': AUTUMN_TRIPS,
    }
    # Not contiguous: 09-10 ran another timetable (a returning timetable
    # rejoins its period), so the range alone would overstate it.
    with_period = _manifest(
        mode='typical_weekday',
        days_in_median=[DAY, '2026-09-09', '2026-09-11'],
        schedule_period=period,
    )
    without = _manifest()
    assert with_period['schedule_period'] == period
    assert any(
        '3 weekdays from 2026-09-08 to 2026-09-11' in line
        for line in with_period['known_limitations']
    )
    assert not any(
        'published timetable' in line for line in without['known_limitations']
    )
    # The day switcher exports one day, which ran one timetable -- no period
    # field, no extra caveat.
    assert without['schedule_period'] is None


def test_manifest_counts_shapes_written():
    """The shapes sentence names the count geometry.json holds."""
    # Observation is counted before the min_samples threshold; geometry.json
    # holds only what survived it. The sentence must name the file's own count.
    manifest = _manifest(shapes_observed=5)
    line = next(
        text for text in manifest['known_limitations'] if 'appear here' in text
    )
    assert "3 of the static feed's 10 shapes" in line
    assert '2 further shapes were observed' in line

    one = _manifest(shapes_observed=4)
    assert any(
        '1 further shape was observed' in text
        for text in one['known_limitations']
    )


# ─── typical_kmh: a segment's own baseline, for the slowdown highlight ──────


def _slot_label(hour: int) -> str:
    padded = str(hour).zfill(2)
    return f'{padded}:00'


def _slots(*per_slot: list) -> dict:
    """Build timeslot_files as build_timeslot_files() returns them.

    Takes a list of (segment_idx, speed_kmh) pairs per slot.
    """
    return {
        _slot_label(hour): {
            'timeslot': _slot_label(hour),
            'segment_idx': [pos for pos, _ in pairs],
            'speed_kmh': [kmh for _, kmh in pairs],
            'n_samples': list(itertools.repeat(5, len(pairs))),
        }
        for hour, pairs in enumerate(per_slot)
    }


def test_typical_kmh_is_median_of_slot_medians():
    """The baseline is the median of the slot medians, not their mean."""
    # Segment 0 gets 15 slots (the gate exactly) of 10,11,...,24 km/h: the
    # median of those is 17, and it must be that and not the mean (17.0) by
    # accident -- one outlier is added to make the two disagree.
    timeslot_files = _slots(*[[(0, kmh)] for kmh in SPEEDS_WITH_OUTLIER])
    typical = web.build_typical_kmh(timeslot_files, segment_count=1)
    assert typical == [OUTLIER_MEDIAN]
    assert len(SPEEDS_WITH_OUTLIER) == web.TYPICAL_MIN_SLOTS


def test_typical_kmh_is_null_below_slot_gate():
    """A segment seen in 14 slots gets no baseline.

    At that fill the baseline is partly night traffic, and marking a daytime
    slot against it would be marking it for being daytime.
    """
    gate = web.TYPICAL_MIN_SLOTS
    short = gate - 1
    below = _slots(*[[(0, FLAT_KMH)] for _ in range(short)])
    assert web.build_typical_kmh(below, segment_count=1) == [None]

    at_gate = _slots(*[[(0, FLAT_KMH)] for _ in range(gate)])
    assert web.build_typical_kmh(at_gate, segment_count=1) == [FLAT_KMH]


def test_typical_kmh_parallels_geometry_arrays():
    """The array has one entry per geometry segment, in geometry order."""
    # Segment 1 is filled, segments 0 and 2 are not: the array still has one
    # entry per geometry segment, in geometry order, or the frontend would
    # read another segment's baseline.
    gate = web.TYPICAL_MIN_SLOTS
    timeslot_files = _slots(*[[(1, SECOND_SEGMENT_KMH)] for _ in range(gate)])
    typical = web.build_typical_kmh(timeslot_files, segment_count=3)
    assert typical == [None, SECOND_SEGMENT_KMH, None]


def _write_day_speeds(path: Path, shape_key: str) -> None:
    rows = [
        json.dumps({
            'shape_id': SHAPE_ID,
            'shape_key': shape_key,
            'segment_index': 0,
            'timeslot': _slot_label(hour),
            'speed_ms': SPEED_MS_AT_SLOT,
        })
        for hour in range(24)
        for _ in range(2)
    ]
    path.write_text('\n'.join([*rows, '']), encoding=UTF8)


TYPICAL_LIMITATION = (
    '31 August to 4 September 2026 period (five weekdays) in 36.9% of cases,'
    ' measured on 2026-09-08 and not re-measured on other periods'
)


def _assert_highlight(period_manifest: dict):
    highlight = period_manifest['web_export']['slowdown_highlight']
    assert highlight['ratio_max'] == web.SLOWDOWN_RATIO_MAX
    assert highlight['absolute_min_kmh'] == web.SLOWDOWN_ABS_MIN_KMH
    assert highlight['min_filled_slots'] == web.TYPICAL_MIN_SLOTS
    assert any(
        TYPICAL_LIMITATION in line
        for line in period_manifest['known_limitations']
    )


def _assert_period_geometry(period: Path):
    geometry = _read_json(period / 'geometry.json')
    assert 'typical_kmh' in geometry
    assert len(geometry['typical_kmh']) == len(geometry['shape_idx'])
    # That fixture period has two slots, well under the gate.
    assert set(geometry['typical_kmh']) == {None}


def _assert_day_has_no_baseline(day_dir: Path):
    assert 'typical_kmh' not in _read_json(day_dir / 'geometry.json')
    day_manifest = _bundle_manifest(day_dir)
    assert 'slowdown_highlight' not in day_manifest['web_export']
    assert not any(
        'typical_kmh' in line for line in day_manifest['known_limitations']
    )


def test_typical_kmh_ships_only_in_period_bundle(
    tmp_path: Path,
    monkeypatch,
):
    """A day bundle carries no baseline at all.

    The thresholds it is read against were measured on a period's medians.
    A day bundle is a different object, so it carries no baseline at all
    rather than one the rule was never calibrated for.
    """
    static_dir, data_dir, shape_key = _prepare_export(tmp_path)
    _write_day_speeds(
        data_dir / 'processed' / f'segment_speeds_{DAY}.jsonl',
        shape_key,
    )

    _run_export(monkeypatch, str(static_dir), str(data_dir))
    _run_export(monkeypatch, str(static_dir), str(data_dir), '--day', DAY)

    period = data_dir / 'web' / 'typical_weekday' / AUTUMN
    _assert_period_geometry(period)
    _assert_highlight(_bundle_manifest(period))
    _assert_day_has_no_baseline(data_dir / 'web' / DAY)
