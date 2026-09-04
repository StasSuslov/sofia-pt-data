import json
import math
import sys
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from segment_speeds import (
    BACKWARD_TOLERANCE_M,
    EARTH_RADIUS_M,
    MAX_SPEED_MS,
    MAX_TIME_GAP_SEC,
    Shape,
    build_shape,
    build_shape_key,
    build_schedule_periods,
    build_typical_weekday,
    count_multi_geometry_shape_ids,
    count_multi_id_geometries,
    find_static_snapshots,
    next_snapshot_after,
    haversine_m,
    load_schedule_calendar,
    load_static,
    pick_snapshot_for_day,
    assign_schedule_periods,
    schedule_churn,
    schedule_signature,
    process_day,
    project_point,
    timeslot_for,
    window_bounds,
)

SOFIA = ZoneInfo("Europe/Sofia")

# A straight north-south line along a meridian, so consecutive-point
# haversine distances are hand-computable ground truth: a meridian arc of
# `d` degrees on a sphere of radius EARTH_RADIUS_M is exactly
# radians(d) * EARTH_RADIUS_M, no spherical trig needed since cos(dlon)
# term drops out entirely on a due-north/south line.
STRAIGHT_LINE = [(0.000 + 0.001 * i, 0.0) for i in range(11)]  # lat 0.000..0.010, lon 0.0
STEP_M = math.radians(0.001) * EARTH_RADIUS_M  # ~111.19m per 0.001 deg, exact for this haversine's sphere radius


def test_haversine_known_distance():
    # 1 degree of latitude is ~111.32 km everywhere (a meridian arc), the
    # textbook approximation used to sanity-check any haversine implementation.
    d = haversine_m(0.0, 0.0, 1.0, 0.0)
    assert abs(d - 111_320) < 200


def test_build_shape_cumulative_distance_matches_hand_computed_straight_line():
    shape = build_shape(STRAIGHT_LINE)
    # 10 equal hops of ~111.32m each along a straight meridian line.
    assert shape.cum_dist[0] == 0.0
    for i in range(1, 11):
        expected = i * STEP_M
        assert abs(shape.cum_dist[i] - expected) < 1.0, f"vertex {i}: {shape.cum_dist[i]} vs {expected}"


def test_project_point_on_a_vertex_gives_that_vertexs_cumulative_distance():
    shape = build_shape(STRAIGHT_LINE)
    # Sitting exactly on vertex 5 should project to ~5 hops along the line.
    lat, lon = STRAIGHT_LINE[5]
    idx, along, perp = project_point(shape, lat, lon, 0, len(shape.xs) - 2)
    assert abs(along - 5 * STEP_M) < 1.0
    assert perp < 1.0
    assert idx in (4, 5)  # vertex 5 is the shared endpoint of segments 4 and 5


def test_project_point_off_axis_measures_correct_perpendicular_and_along_distance():
    shape = build_shape(STRAIGHT_LINE)
    # A point ~50m east of vertex 3 (roughly 0.00045 deg of longitude at the
    # equator, where a degree of longitude is also ~111.32km) should project
    # onto the line near 3 hops along, with ~50m perpendicular offset.
    lat, lon = STRAIGHT_LINE[3][0], 0.00045
    idx, along, perp = project_point(shape, lat, lon, 0, len(shape.xs) - 2)
    assert abs(along - 3 * STEP_M) < 2.0
    assert abs(perp - 50) < 5.0


def test_windowed_search_matches_full_search_on_small_example():
    """
    Task-required test: a window built around the correct answer must find
    the same segment and along-distance as a brute-force full scan, on the
    same small polyline.
    """
    shape = build_shape(STRAIGHT_LINE)
    lat, lon = STRAIGHT_LINE[7][0], 0.0002  # near vertex 7, slightly off-axis

    full_idx, full_along, full_perp = project_point(shape, lat, lon, 0, len(shape.xs) - 2)

    # A window anchored on a nearby "previous match" distance (6 hops in),
    # narrow enough that it does NOT cover the whole shape, must still land
    # on the same segment/distance as the full scan.
    lo_idx, hi_idx = window_bounds(shape, last_dist=6 * STEP_M, max_travel_m=2 * STEP_M)
    assert (hi_idx - lo_idx) < (len(shape.xs) - 2)  # confirm the window is genuinely narrower than "full"
    win_idx, win_along, win_perp = project_point(shape, lat, lon, lo_idx, hi_idx)

    assert win_idx == full_idx
    assert abs(win_along - full_along) < 1e-6
    assert abs(win_perp - full_perp) < 1e-6


def test_timeslot_bins_to_nearest_15_minutes_local_time():
    # 1787864432 == 2026-08-27 21:00:32 UTC == 2026-08-28 00:00:32 Europe/Sofia (summer, UTC+3)
    assert timeslot_for(1787864432, SOFIA) == "00:00"


def test_timeslot_bin_boundaries():
    from datetime import datetime
    ts_1414 = int(datetime(2026, 8, 28, 14, 14, 0, tzinfo=SOFIA).timestamp())
    ts_1415 = int(datetime(2026, 8, 28, 14, 15, 0, tzinfo=SOFIA).timestamp())
    assert timeslot_for(ts_1414, SOFIA) == "14:00"
    assert timeslot_for(ts_1415, SOFIA) == "14:15"


# ─── process_day: one integration test per rejection reason ────────────────

TRIP_ID = "T1"
ROUTE_ID = "R1"
SHAPE_ID = "S1"
VEHICLE_ID = "V1"


def _trip_map_and_shapes():
    # Third field is the shape_key; these tests don't care what it hashes
    # to, only that it addresses the shape, so the bare id stands in.
    trip_map = {TRIP_ID: (ROUTE_ID, SHAPE_ID, SHAPE_ID)}
    shapes_by_id = {SHAPE_ID: build_shape(STRAIGHT_LINE)}
    return trip_map, shapes_by_id


def _write_day(path: Path, records: list) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def _run_day(tmp_path: Path, records: list):
    trip_map, shapes_by_id = _trip_map_and_shapes()
    day_path = tmp_path / "2026-08-28.jsonl"
    _write_day(day_path, records)
    out_path = tmp_path / "out.jsonl"
    agg = defaultdict(list)
    diffs = []
    stats = process_day(day_path, out_path, trip_map, shapes_by_id, SOFIA, agg, diffs)
    samples = [json.loads(line) for line in out_path.read_text().splitlines()]
    return stats, samples


def _rec(ts, lat, lon, speed_ms=10.0, trip_id=TRIP_ID, vehicle_id=VEHICLE_ID):
    return {"snapshot_ts": ts, "vehicle_id": vehicle_id, "route_id": ROUTE_ID,
            "trip_id": trip_id, "lat": lat, "lon": lon, "bearing": None, "speed_ms": speed_ms}


def test_accepts_a_normal_pair_and_computes_plausible_speed(tmp_path: Path):
    lat0, lon0 = STRAIGHT_LINE[2]
    lat1, lon1 = STRAIGHT_LINE[3]  # one hop (~111.32m) further along
    records = [_rec(1000, lat0, lon0), _rec(1045, lat1, lon1)]  # 45s apart, matches collector cadence
    stats, samples = _run_day(tmp_path, records)
    assert stats.samples_emitted == 1
    assert samples[0]["route_id"] == ROUTE_ID
    assert samples[0]["shape_id"] == SHAPE_ID
    # ~111.32m / 45s =~ 2.47 m/s
    assert abs(samples[0]["speed_ms"] - (STEP_M / 45)) < 0.05
    assert sum(stats.reject_counts.values()) == 0


def test_rejects_trip_not_in_static(tmp_path: Path):
    records = [_rec(1000, 0.0, 0.0, trip_id="UNKNOWN_TRIP")]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts["trip_not_in_static"] == 1
    assert samples == []


def test_rejects_gap_too_large(tmp_path: Path):
    lat0, lon0 = STRAIGHT_LINE[1]
    lat1, lon1 = STRAIGHT_LINE[2]
    records = [_rec(1000, lat0, lon0), _rec(1000 + MAX_TIME_GAP_SEC + 1, lat1, lon1)]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts["gap_too_large"] == 1
    assert samples == []


def test_rejects_non_positive_time_delta(tmp_path: Path):
    lat0, lon0 = STRAIGHT_LINE[1]
    lat1, lon1 = STRAIGHT_LINE[2]
    records = [_rec(1000, lat0, lon0), _rec(1000, lat1, lon1)]  # duplicate snapshot_ts
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts["non_positive_time_delta"] == 1
    assert samples == []


def test_rejects_moved_backward_beyond_tolerance(tmp_path: Path):
    lat_fwd, lon_fwd = STRAIGHT_LINE[5]
    lat_back, lon_back = STRAIGHT_LINE[1]  # far behind vertex 5 along the line
    records = [_rec(1000, lat_fwd, lon_fwd), _rec(1045, lat_back, lon_back)]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts["moved_backward"] == 1
    assert samples == []


def test_rejects_speed_too_high(tmp_path: Path):
    lat0, lon0 = STRAIGHT_LINE[0]
    lat1, lon1 = STRAIGHT_LINE[10]  # 10 hops (~1113m) in 1 second => way over 100 km/h
    records = [_rec(1000, lat0, lon0), _rec(1001, lat1, lon1)]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts["speed_too_high"] == 1
    assert samples == []


def test_backward_tolerance_allows_small_gps_jitter(tmp_path: Path):
    # A tiny backward wobble (well under BACKWARD_TOLERANCE_M) must NOT be rejected.
    lat0, lon0 = STRAIGHT_LINE[5]
    jitter_deg = (BACKWARD_TOLERANCE_M / 2) / 111_320  # a fraction of the tolerance, in degrees latitude
    lat1, lon1 = STRAIGHT_LINE[5][0] - jitter_deg, 0.0
    records = [_rec(1000, lat0, lon0), _rec(1045, lat1, lon1)]
    stats, samples = _run_day(tmp_path, records)
    assert stats.reject_counts["moved_backward"] == 0
    assert len(samples) == 1


def test_feed_speed_carried_through_for_validation(tmp_path: Path):
    lat0, lon0 = STRAIGHT_LINE[2]
    lat1, lon1 = STRAIGHT_LINE[3]
    records = [_rec(1000, lat0, lon0, speed_ms=99.0), _rec(1045, lat1, lon1, speed_ms=2.5)]
    stats, samples = _run_day(tmp_path, records)
    assert samples[0]["feed_speed_kmh"] == 2.5  # the arrival snapshot's own feed speed, not the departure one


def test_validation_compares_both_sides_in_kmh(tmp_path: Path):
    """
    The feed reports km/h in a field the raw archive calls speed_ms. Comparing
    it against a derived m/s value (and scaling the difference afterwards)
    produced a number in no unit at all — a 48 km/h median disagreement that
    was really 9. A vehicle whose derived speed matches the feed exactly must
    come out as a zero difference, not as a 2.6x discrepancy.
    """
    trip_map, shapes_by_id = _trip_map_and_shapes()
    lat0, lon0 = STRAIGHT_LINE[0]
    lat1, lon1 = STRAIGHT_LINE[1]
    day_path = tmp_path / "2026-08-28.jsonl"

    # derived speed comes out of the fixture geometry; feed value is that same
    # speed expressed in km/h, i.e. perfect agreement
    _write_day(day_path, [_rec(1000, lat0, lon0), _rec(1045, lat1, lon1, speed_ms=0.0)])
    diffs: list = []
    process_day(day_path, tmp_path / "probe.jsonl", trip_map, shapes_by_id, SOFIA, defaultdict(list), diffs)
    derived_kmh = json.loads((tmp_path / "probe.jsonl").read_text().splitlines()[0])["speed_ms"] * 3.6

    day_path2 = tmp_path / "2026-08-29.jsonl"
    _write_day(day_path2, [_rec(1000, lat0, lon0), _rec(1045, lat1, lon1, speed_ms=round(derived_kmh, 3))])
    diffs = []
    process_day(day_path2, tmp_path / "out2.jsonl", trip_map, shapes_by_id, SOFIA, defaultdict(list), diffs)

    assert len(diffs) == 1
    assert diffs[0] < 0.01, f"agreeing readings must diff by ~0 km/h, got {diffs[0]}"


# ─── Content-addressed shape identity (2026-08-31 republished-feed bug) ────

def _write_static_zip(tmp_path: Path, filename: str, points: list,
                      shape_id: str = SHAPE_ID) -> Path:
    """Minimal single-shape/single-trip GTFS zip, parametrized on the
    shape's own points and its id -- same fixture shape as
    test_export_web.py's _write_static_zip, but built here so two zips can
    share a shape_id while differing only in geometry (the 2026-08-31
    scenario) or share the geometry under two ids (the 2026-09-02 one)."""
    routes = ["route_id,route_short_name,route_type", f"{ROUTE_ID},9,3"]
    trips = ["trip_id,route_id,service_id,shape_id", f"{TRIP_ID},{ROUTE_ID},SVC,{shape_id}"]
    shapes = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"]
    shapes += [f"{shape_id},{lat},{lon},{i}" for i, (lat, lon) in enumerate(points)]

    path = tmp_path / filename
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("routes.txt", "\n".join(routes) + "\n")
        z.writestr("trips.txt", "\n".join(trips) + "\n")
        z.writestr("shapes.txt", "\n".join(shapes) + "\n")
    return path


def test_load_static_identical_geometry_across_zips_produces_the_same_shape_key(tmp_path: Path):
    """The 98.3% case: two feed snapshots that happen to describe the same
    shape identically must still produce the same shape_key, or every
    unchanged shape would needlessly split on every republish."""
    zip_a = _write_static_zip(tmp_path, "gtfs_a.zip", STRAIGHT_LINE)
    zip_b = _write_static_zip(tmp_path, "gtfs_b.zip", list(STRAIGHT_LINE))  # same points, different list object

    trip_map_a, shapes_a, _ = load_static(zip_a)
    trip_map_b, shapes_b, _ = load_static(zip_b)

    _, _, key_a = trip_map_a[TRIP_ID]
    _, _, key_b = trip_map_b[TRIP_ID]
    assert key_a == key_b
    assert set(shapes_a) == set(shapes_b) == {key_a}


def test_load_static_different_geometry_same_shape_id_produces_different_keys(tmp_path: Path):
    """
    Regression test for the actual bug: a shape_id kept across a republish
    while its geometry changed underneath it must produce two distinct
    shape_keys, not one. Against the pre-fix aggregation key
    (shape_id, segment_index) this shape_id would silently pool samples from
    two different physical roads into one median with no counter ever
    firing -- that is exactly what this must prevent.
    """
    zip_a = _write_static_zip(tmp_path, "gtfs_a.zip", STRAIGHT_LINE)
    shifted = [(lat + 0.05, lon) for lat, lon in STRAIGHT_LINE]  # same shape_id, geometry moved ~5.5km
    zip_b = _write_static_zip(tmp_path, "gtfs_b.zip", shifted)

    trip_map_a, shapes_a, ids_a = load_static(zip_a)
    trip_map_b, shapes_b, ids_b = load_static(zip_b)

    _, _, key_a = trip_map_a[TRIP_ID]
    _, _, key_b = trip_map_b[TRIP_ID]
    assert key_a != key_b
    assert ids_a[key_a] == ids_b[key_b] == {SHAPE_ID}
    # Merging both feeds' shapes (as segment_speeds.py's main() does across
    # snapshots) must keep both geometries addressable, never overwrite one.
    merged = {**shapes_a, **shapes_b}
    assert len(merged) == 2


def test_process_day_never_pools_samples_across_geometry_versions(tmp_path: Path):
    """
    End-to-end version of the regression above: run process_day() against
    each of the two feeds independently and confirm the emitted samples
    carry the same bare shape_id and the same segment_index (so a
    bare-shape_id aggregation key would have pooled them) but different
    shape_key -- the field build_typical_weekday() actually aggregates on.
    """
    zip_a = _write_static_zip(tmp_path, "gtfs_a.zip", STRAIGHT_LINE)
    shifted = [(lat + 0.05, lon) for lat, lon in STRAIGHT_LINE]
    zip_b = _write_static_zip(tmp_path, "gtfs_b.zip", shifted)

    trip_map_a, shapes_a, _ = load_static(zip_a)
    trip_map_b, shapes_b, _ = load_static(zip_b)

    lat0, lon0 = STRAIGHT_LINE[2]
    lat1, lon1 = STRAIGHT_LINE[3]
    day_a = tmp_path / "2026-08-27.jsonl"
    _write_day(day_a, [_rec(1000, lat0, lon0), _rec(1045, lat1, lon1)])
    out_a = tmp_path / "out_a.jsonl"
    process_day(day_a, out_a, trip_map_a, shapes_a, SOFIA, defaultdict(list), [])
    sample_a = json.loads(out_a.read_text().splitlines()[0])

    lat0b, lon0b = shifted[2]
    lat1b, lon1b = shifted[3]
    day_b = tmp_path / "2026-08-28.jsonl"
    _write_day(day_b, [_rec(1000, lat0b, lon0b), _rec(1045, lat1b, lon1b)])
    out_b = tmp_path / "out_b.jsonl"
    process_day(day_b, out_b, trip_map_b, shapes_b, SOFIA, defaultdict(list), [])
    sample_b = json.loads(out_b.read_text().splitlines()[0])

    assert sample_a["shape_id"] == sample_b["shape_id"] == SHAPE_ID
    assert sample_a["segment_index"] == sample_b["segment_index"]
    assert sample_a["shape_key"] != sample_b["shape_key"]


def test_renumbered_shape_id_on_identical_geometry_keeps_one_key(tmp_path: Path):
    """
    The 2026-09-02 finding: the agency kept the geometry and changed the
    shape_id, publishing A4500-A4503 as byte-identical copies of
    A1192/A3949/A1221/A2710. Under the old shape_id@hash key that split
    every affected segment into two series that could never merge again.
    The key is the geometry alone, so it must not move.
    """
    zip_a = _write_static_zip(tmp_path, "gtfs_a.zip", STRAIGHT_LINE)
    zip_b = _write_static_zip(tmp_path, "gtfs_b.zip", STRAIGHT_LINE, shape_id="S9")

    trip_map_a, _, _ = load_static(zip_a)
    trip_map_b, _, ids_b = load_static(zip_b)

    _, shape_id_a, key_a = trip_map_a[TRIP_ID]
    _, shape_id_b, key_b = trip_map_b[TRIP_ID]
    assert shape_id_a != shape_id_b
    assert key_a == key_b
    assert ids_b[key_b] == {"S9"}


def test_count_multi_geometry_shape_ids_counts_only_ids_with_more_than_one_geometry():
    key_a1 = build_shape_key([(0.0, 0.0), (0.001, 0.0)])
    key_a2 = build_shape_key([(1.0, 1.0), (1.001, 1.0)])
    key_b = build_shape_key([(2.0, 2.0), (2.001, 2.0)])
    # "A" published under two geometries, "B" under one.
    shape_ids_by_key = {key_a1: {"A"}, key_a2: {"A"}, key_b: {"B"}}
    assert count_multi_geometry_shape_ids(shape_ids_by_key) == 1


def test_count_multi_id_geometries_counts_only_geometries_with_more_than_one_id():
    key_shared = build_shape_key([(0.0, 0.0), (0.001, 0.0)])
    key_single = build_shape_key([(2.0, 2.0), (2.001, 2.0)])
    # One geometry republished under a second id (A4500 over A1192), one not.
    shape_ids_by_key = {key_shared: {"A1192", "A4500"}, key_single: {"B"}}
    assert count_multi_id_geometries(shape_ids_by_key) == 1


# ─── Per-day static snapshot selection ─────────────────────────────────────

def test_find_static_snapshots_ignores_unpacked_dir_and_dotfiles(tmp_path: Path):
    (tmp_path / "gtfs_2026-08-27.zip").write_bytes(b"")
    (tmp_path / "gtfs_2026-08-31.zip").write_bytes(b"")
    (tmp_path / "gtfs_2026-08-27").mkdir()  # unpacked sibling directory, same stem
    (tmp_path / ".DS_Store").write_bytes(b"")

    snapshots = find_static_snapshots(tmp_path)
    assert [d for d, _ in snapshots] == ["2026-08-27", "2026-08-31"]


def test_pick_snapshot_for_day_strictly_between_two_snapshots_uses_the_earlier_one():
    snapshots = [("2026-08-27", Path("a.zip")), ("2026-08-31", Path("b.zip"))]
    snap_date, path, is_fallback = pick_snapshot_for_day(snapshots, "2026-08-29")
    assert (snap_date, path.name, is_fallback) == ("2026-08-27", "a.zip", False)


def test_pick_snapshot_for_day_on_a_snapshots_own_date_uses_that_snapshot():
    snapshots = [("2026-08-27", Path("a.zip")), ("2026-08-31", Path("b.zip"))]
    snap_date, path, is_fallback = pick_snapshot_for_day(snapshots, "2026-08-31")
    assert (snap_date, path.name, is_fallback) == ("2026-08-31", "b.zip", False)


def test_pick_snapshot_for_day_after_the_last_snapshot_uses_the_latest_one():
    snapshots = [("2026-08-27", Path("a.zip")), ("2026-08-31", Path("b.zip"))]
    snap_date, path, is_fallback = pick_snapshot_for_day(snapshots, "2026-09-05")
    assert (snap_date, path.name, is_fallback) == ("2026-08-31", "b.zip", False)


def test_find_static_snapshots_orders_an_intra_day_capture_after_its_plain_sibling(tmp_path: Path):
    (tmp_path / "gtfs_2026-09-10.zip").write_bytes(b"")
    (tmp_path / "gtfs_2026-09-10T1100.zip").write_bytes(b"")
    (tmp_path / "gtfs_2026-09-11.zip").write_bytes(b"")

    snapshots = find_static_snapshots(tmp_path)
    assert [p.name for _, p in snapshots] == [
        "gtfs_2026-09-10.zip", "gtfs_2026-09-10T1100.zip", "gtfs_2026-09-11.zip",
    ]


def test_a_day_with_an_intra_day_capture_is_scored_against_its_morning_snapshot():
    """
    Once the archive captures hourly, the day of a mid-day republish owns two
    snapshots. The day's own feed is the morning one -- the feed it started
    under -- and the intra-day sibling has to stay reachable as the fallback,
    or every record from before the republish would be scored against a feed
    that did not exist yet while the morning capture sat behind the fallback
    pointer where nothing reads it.
    """
    snapshots = [("2026-09-10", Path("gtfs_2026-09-10.zip")),
                 ("2026-09-10", Path("gtfs_2026-09-10T1100.zip")),
                 ("2026-09-11", Path("gtfs_2026-09-11.zip"))]

    snap_date, path, is_fallback = pick_snapshot_for_day(snapshots, "2026-09-10")
    assert (snap_date, path.name, is_fallback) == ("2026-09-10", "gtfs_2026-09-10.zip", False)
    assert next_snapshot_after(snapshots, path).name == "gtfs_2026-09-10T1100.zip"


def test_a_later_day_uses_the_last_capture_of_the_preceding_date():
    # The mirror case: by 2026-09-11 every republish of the 10th has already
    # happened, so the intra-day capture is the current feed, not the morning
    # one.
    snapshots = [("2026-09-10", Path("gtfs_2026-09-10.zip")),
                 ("2026-09-10", Path("gtfs_2026-09-10T1100.zip"))]
    snap_date, path, is_fallback = pick_snapshot_for_day(snapshots, "2026-09-11")
    assert (snap_date, path.name, is_fallback) == ("2026-09-10", "gtfs_2026-09-10T1100.zip", False)


def test_next_snapshot_after_returns_the_following_one_and_none_at_the_end():
    snapshots = [("2026-09-02", Path("b.zip")), ("2026-09-03", Path("c.zip"))]
    assert next_snapshot_after(snapshots, Path("b.zip")).name == "c.zip"
    assert next_snapshot_after(snapshots, Path("c.zip")) is None


def test_a_trip_the_days_own_feed_does_not_know_resolves_against_the_next_feed(tmp_path: Path):
    """
    The 2026-09-02 recovery: the agency renumbered 157 trips at midday, the
    RT stream switched to the new trip_ids at once, and the day's own
    snapshot -- taken that morning -- knew none of them. They are all in the
    next morning's capture, so the day's records must resolve against it
    instead of being rejected as trip_not_in_static.
    """
    trip_map, shapes_by_id = _trip_map_and_shapes()
    renumbered = "T_NEW"
    next_trip_map = {renumbered: (ROUTE_ID, SHAPE_ID, SHAPE_ID)}

    lat0, lon0 = STRAIGHT_LINE[2]
    lat1, lon1 = STRAIGHT_LINE[3]
    day_path = tmp_path / "2026-09-02.jsonl"
    _write_day(day_path, [_rec(1000, lat0, lon0, trip_id=renumbered),
                          _rec(1045, lat1, lon1, trip_id=renumbered)])
    out_path = tmp_path / "out.jsonl"

    stats = process_day(day_path, out_path, trip_map, shapes_by_id, SOFIA,
                        defaultdict(list), [], next_trip_map)

    assert stats.reject_counts["trip_not_in_static"] == 0
    assert stats.records_from_next_feed == 2
    assert stats.trips_from_next_feed == {renumbered}
    assert len(out_path.read_text().splitlines()) == 1


def test_the_days_own_feed_wins_when_both_know_the_trip(tmp_path: Path):
    # The next feed is a fallback, never an override: a day stays scored
    # against the feed it was actually collected under wherever that feed
    # has an answer.
    trip_map, shapes_by_id = _trip_map_and_shapes()
    next_trip_map = {TRIP_ID: ("OTHER_ROUTE", SHAPE_ID, SHAPE_ID)}

    lat0, lon0 = STRAIGHT_LINE[2]
    lat1, lon1 = STRAIGHT_LINE[3]
    day_path = tmp_path / "2026-09-02.jsonl"
    _write_day(day_path, [_rec(1000, lat0, lon0), _rec(1045, lat1, lon1)])
    out_path = tmp_path / "out.jsonl"

    stats = process_day(day_path, out_path, trip_map, shapes_by_id, SOFIA,
                        defaultdict(list), [], next_trip_map)

    assert stats.records_from_next_feed == 0
    assert json.loads(out_path.read_text().splitlines()[0])["route_id"] == ROUTE_ID


def test_pick_snapshot_for_day_before_the_earliest_snapshot_falls_back_and_says_so():
    # There is no feed on record from before the archive started; the
    # earliest available snapshot stands in, but is_fallback must say so
    # rather than let this look like a genuine date match.
    snapshots = [("2026-08-27", Path("a.zip")), ("2026-08-31", Path("b.zip"))]
    snap_date, path, is_fallback = pick_snapshot_for_day(snapshots, "2026-08-01")
    assert (snap_date, path.name, is_fallback) == ("2026-08-27", "a.zip", True)


# ─── Emitted bin key contract (export_web.py depends on key.split("|")) ────

def test_typical_weekday_segment_key_still_splits_into_exactly_three_fields():
    # shape_key is bare hex and must never contain "|" -- this is what lets
    # export_web.py's key.split("|") keep working untouched across both
    # changes to the key's contents.
    shape_key = build_shape_key([(0.0, 0.0), (0.001, 0.0)])
    agg = {"period0": {(shape_key, 7, "11:00"): [10.0, 12.0]}}
    result = build_typical_weekday(agg, ["2026-08-27"], ["2026-08-27"], [], [], Counter(), {}, 0, 0, [])

    # The period key is the outer level, so the bin key itself is untouched.
    assert list(result["segments"]) == ["period0"]
    key = next(iter(result["segments"]["period0"]))
    parts = key.split("|")
    assert len(parts) == 3
    assert parts == [shape_key, "7", "11:00"]


# ─── Schedule periods (which timetable a day ran) ──────────────────────────

def _write_schedule_zip(tmp_path: Path, filename: str, trips: list, dates: dict) -> Path:
    """A GTFS zip carrying only what schedule_signature() reads: trips.txt
    (as (trip_id, route_id, service_id) triples) and calendar_dates.txt (as
    "YYYYMMDD" -> [service_id, ...], all exception_type 1, which is the only
    type this feed ever publishes)."""
    trip_rows = ["trip_id,route_id,service_id,shape_id"]
    trip_rows += [f"{t},{r},{s},{SHAPE_ID}" for t, r, s in trips]
    cal_rows = ["service_id,date,exception_type"]
    for date, services in dates.items():
        cal_rows += [f"{s},{date},1" for s in services]

    path = tmp_path / filename
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("trips.txt", "\n".join(trip_rows) + "\n")
        z.writestr("calendar_dates.txt", "\n".join(cal_rows) + "\n")
    return path


def _signature(zip_path: Path, date_str: str):
    return schedule_signature(*load_schedule_calendar(zip_path), date_str)


def _day(date_str: str, counts: dict, is_weekday: bool = True, key: str = None) -> dict:
    """A day in the shape assign_schedule_periods() takes it. signature_key
    stands in for the sha256 the pipeline computes from the same counts."""
    return {"date": date_str, "is_weekday": is_weekday, "counts": counts,
            "signature_key": key or f"sig_{date_str}"}


def test_added_route_puts_two_days_in_different_schedule_periods(tmp_path: Path):
    """The 2026-09-04 autumn timetable in miniature: a route appears, so the
    two days ran different schedules and must not pool into one median."""
    before = _write_schedule_zip(
        tmp_path, "gtfs_before.zip",
        [("T1", "R1", "WD"), ("T2", "R2", "WD")],
        {"20260903": ["WD"]},
    )
    after = _write_schedule_zip(
        tmp_path, "gtfs_after.zip",
        [("T1", "R1", "WD"), ("T2", "R2", "WD"), ("T3", "R3", "WD")],
        {"20260908": ["WD"]},
    )
    key_before, counts_before = _signature(before, "2026-09-03")
    key_after, counts_after = _signature(after, "2026-09-08")

    assert key_before != key_after
    assert counts_before == {"R1": 1, "R2": 1}
    assert counts_after == {"R1": 1, "R2": 1, "R3": 1}


def test_changed_trip_count_alone_splits_the_period(tmp_path: Path):
    # Tram 8 going 399 -> 617 trips is the same route list, a different
    # timetable. Route ids alone would miss it; the counts are in the key.
    fewer = _write_schedule_zip(tmp_path, "gtfs_fewer.zip",
                                [("T1", "R1", "WD")], {"20260903": ["WD"]})
    more = _write_schedule_zip(tmp_path, "gtfs_more.zip",
                               [("T1", "R1", "WD"), ("T2", "R1", "WD")], {"20260908": ["WD"]})
    assert _signature(fewer, "2026-09-03")[0] != _signature(more, "2026-09-08")[0]


def test_renumbered_trip_ids_keep_the_same_schedule_period(tmp_path: Path):
    """2026-09-02: the agency renumbered 157 trip_ids (and their service_ids)
    mid-day without changing the timetable. A key built from those
    identifiers would declare a new timetable; the content-addressed one
    must not."""
    original = _write_schedule_zip(
        tmp_path, "gtfs_orig.zip",
        [("T1", "R1", "WD"), ("T2", "R1", "WD"), ("T3", "R2", "WD")],
        {"20260902": ["WD"], "20260903": ["WD"]},
    )
    renumbered = _write_schedule_zip(
        tmp_path, "gtfs_renum.zip",
        [("T900001", "R1", "SVC_7788"), ("T900002", "R1", "SVC_7788"), ("T900003", "R2", "SVC_7788")],
        {"20260902": ["SVC_7788"], "20260903": ["SVC_7788"]},
    )
    assert _signature(original, "2026-09-02")[0] == _signature(renumbered, "2026-09-02")[0]


def test_services_not_running_on_the_day_stay_out_of_its_key(tmp_path: Path):
    # A weekend service sitting in the same feed must not leak into the
    # weekday key -- the key is per date, not per feed.
    z = _write_schedule_zip(
        tmp_path, "gtfs_mixed.zip",
        [("T1", "R1", "WD"), ("T2", "R2", "WD"), ("T3", "R9", "WE")],
        {"20260903": ["WD"], "20260905": ["WE"]},
    )
    weekday_key, weekday_counts = _signature(z, "2026-09-03")
    weekend_key, weekend_counts = _signature(z, "2026-09-05")
    assert weekday_key != weekend_key
    assert weekday_counts == {"R1": 1, "R2": 1}
    assert weekend_counts == {"R9": 1}


def test_a_date_the_feed_does_not_cover_hashes_to_the_empty_signature(tmp_path: Path):
    # Retrospective calendar erosion is real (newer snapshots drop rows for
    # past dates), so an absent date must degrade to an empty, obviously
    # distinct signature rather than raise.
    z = _write_schedule_zip(tmp_path, "gtfs_thin.zip",
                            [("T1", "R1", "WD")], {"20260903": ["WD"]})
    key, counts = _signature(z, "2026-12-25")
    assert counts == {}
    assert key != _signature(z, "2026-09-03")[0]


# The August archive in miniature: ~15,000 trips a weekday, so the 0.5%
# tolerance is 75 trips of movement.
BASE = {"R1": 10000, "R2": 5000}


def test_a_handful_of_added_trips_stays_in_one_period():
    """Three trips moving between two tram routes: 0.02% of a weekday, the
    same timetable by any honest reading, and hashing the signature alone
    would call it a new one. The feed has not published a boundary this
    small — the smallest it shows is 3.15% — so this is the case the
    threshold exists to absorb rather than one it was fitted to."""
    days = assign_schedule_periods([
        _day("2026-08-27", BASE),
        _day("2026-08-28", {"R1": 10002, "R2": 5001}),
    ])
    assert days[0]["period_key"] == days[1]["period_key"] == "sig_2026-08-27"
    assert days[1]["churn_vs_reference"] == 3


def test_a_material_timetable_change_starts_a_new_period():
    # Route 10TM's 148 trips ending, part of the 595-trip 4.07% step the feed
    # publishes at the end of August: over the threshold, and the two medians
    # must stay apart.
    days = assign_schedule_periods([
        _day("2026-08-28", BASE),
        _day("2026-08-31", {"R1": 9852, "R2": 5000}),
    ])
    assert days[1]["period_key"] == "sig_2026-08-31"
    assert days[0]["period_key"] != days[1]["period_key"]


def test_drift_is_measured_against_the_reference_not_the_previous_day():
    """Two steps of 70 trips each are inside the tolerance one at a time but
    140 apart end to end. Compared against the previous day the period would
    walk away from the timetable its key names."""
    days = assign_schedule_periods([
        _day("2026-08-27", BASE),
        _day("2026-08-28", {"R1": 10070, "R2": 5000}),
        _day("2026-08-31", {"R1": 10140, "R2": 5000}),
    ])
    assert days[1]["period_key"] == days[0]["period_key"]
    assert days[2]["period_key"] == "sig_2026-08-31"


def test_a_weekday_on_holiday_service_leaves_the_median_instead_of_founding_a_period():
    """2026-09-07, a Monday running 10,149 trips against 15,013 on the
    weekdays around it. Let through, it would be a period of one day; the
    median of one day is that day."""
    days = assign_schedule_periods([
        _day("2026-09-03", BASE),
        _day("2026-09-04", BASE),
        _day("2026-09-07", {"R1": 7000, "R2": 3149}),
        _day("2026-09-08", BASE),
    ])
    assert days[2]["excluded_from_median"] == "reduced_service"
    assert days[2]["period_key"] is None
    # And it did not become the reference: the weekday after it stays in the
    # period it would have interrupted.
    assert days[3]["period_key"] == days[0]["period_key"]


def test_a_gap_in_the_calendar_cannot_disarm_the_holiday_rule():
    """Zero-trip weekdays used to count towards the median that the
    reduced-service test measures against. Enough of them (a run of static
    snapshots missing the dates they cover, which this archive has already
    hit once) drags that median to zero, and a zero baseline turns the whole
    test into a no-op: the holiday sails into the median and founds a period
    of its own."""
    days = assign_schedule_periods(
        [_day(f"2026-10-{d:02d}", {}) for d in (5, 6, 7, 8, 9, 12)]
        + [_day(f"2026-10-{d:02d}", BASE) for d in (13, 14, 15, 16, 19)]
        + [_day("2026-10-20", {"R1": 7000, "R2": 3149})]
    )
    holiday = days[-1]
    assert holiday["excluded_from_median"] == "reduced_service"
    assert holiday["period_key"] is None
    assert all(d["excluded_from_median"] == "no_calendar_rows" for d in days[:6])


def test_a_date_the_snapshot_has_no_calendar_rows_for_is_not_called_a_holiday():
    # Zero trips is a hole in the archive, not the city running fewer buses.
    # Both leave the median; only one of them is a fact about the city.
    days = assign_schedule_periods([_day("2026-08-27", BASE), _day("2026-12-25", {})])
    assert days[1]["excluded_from_median"] == "no_calendar_rows"


def test_weekends_take_no_period_at_all():
    days = assign_schedule_periods([
        _day("2026-08-28", BASE),
        _day("2026-08-29", {"R1": 6000, "R2": 3800}, is_weekday=False),
    ])
    assert days[1]["excluded_from_median"] == "weekend"
    assert days[1]["period_key"] is None


def test_a_period_names_the_reference_day_even_when_the_run_skipped_it():
    """A run given an explicit subset of dates still assigns periods over the
    whole archive (see main()), so a period's reference day can be one this
    run did not process. The entry has to name that day rather than the
    earliest day it happens to hold, or the same timetable would be
    described differently depending on how the run was called."""
    days = assign_schedule_periods([
        _day("2026-08-27", BASE),
        _day("2026-08-28", {"R1": 10002, "R2": 5001}),
    ])
    assert [d["period_reference_date"] for d in days] == ["2026-08-27", "2026-08-27"]

    periods = build_schedule_periods(days[1:], {"sig_2026-08-27": 7})
    assert len(periods) == 1
    assert periods[0]["period_key"] == "sig_2026-08-27"
    assert periods[0]["reference_date"] == "2026-08-27"
    assert periods[0]["days_in_median_mon_fri"] == ["2026-08-28"]


def test_schedule_churn_counts_a_dropped_route_in_full():
    assert schedule_churn({"R1": 100}, {"R1": 100, "R2": 40}) == 40
    assert schedule_churn({}, {}) == 0


def test_build_schedule_periods_describes_each_period_by_its_reference_day():
    days = assign_schedule_periods([
        _day("2026-08-27", BASE),
        _day("2026-08-28", {"R1": 10002, "R2": 5001}),
        _day("2026-08-29", {"R1": 6000, "R2": 3800}, is_weekday=False),
        _day("2026-08-31", {"R1": 9852, "R2": 5000, "R3": 55}),
    ])
    periods = build_schedule_periods(days, {"sig_2026-08-27": 10, "sig_2026-08-31": 20})
    by_key = {p["period_key"]: p for p in periods}

    assert [p["period_key"] for p in periods] == ["sig_2026-08-27", "sig_2026-08-31"]
    first = by_key["sig_2026-08-27"]
    assert first["days_in_median_mon_fri"] == ["2026-08-27", "2026-08-28"]
    assert (first["first_date"], first["last_date"]) == ("2026-08-27", "2026-08-28")
    assert first["reference_date"] == "2026-08-27"
    assert (first["route_count"], first["trip_count"]) == (2, 15000)  # the reference day
    assert first["max_churn_vs_reference"] == 3
    assert first["bin_count"] == 10
    # The weekend day is in no period; day_breakdown carries its reason.
    assert sum(len(p["days_in_median_mon_fri"]) for p in periods) == 3
    assert by_key["sig_2026-08-31"]["route_count"] == 3


def test_bins_from_two_periods_never_merge_into_one_median():
    """The bug this whole split exists for: the same segment and timeslot
    observed under two timetables must yield two medians, not one average
    of both."""
    shape_key = build_shape_key([(0.0, 0.0), (0.001, 0.0)])
    bin_key = (shape_key, 3, "08:00")
    agg = {
        "summer": {bin_key: [10.0, 10.0]},
        "autumn": {bin_key: [4.0, 4.0]},
    }
    days = assign_schedule_periods([
        _day("2026-09-03", BASE, key="summer"),
        _day("2026-09-08", {"R1": 10000, "R2": 5000, "R3": 300}, key="autumn"),
    ])
    periods = build_schedule_periods(days, {"summer": 1, "autumn": 1})
    result = build_typical_weekday(
        agg, ["2026-09-03", "2026-09-08"], ["2026-09-03", "2026-09-08"],
        [], [], Counter(), {}, 0, 0, periods,
    )

    flat = f"{shape_key}|3|08:00"
    assert result["segments"]["summer"][flat]["median_speed_ms"] == 10.0
    assert result["segments"]["autumn"][flat]["median_speed_ms"] == 4.0
    # Pooled, the median would have been 7.0 and n_samples 4.
    assert result["segments"]["summer"][flat]["n_samples"] == 2
    assert result["segments"]["autumn"][flat]["n_samples"] == 2
    assert result["segment_count"] == 2          # one bin per period, counted per period
    assert result["distinct_segment_count"] == 1  # and one piece of road, counted once
    assert [p["period_key"] for p in result["schedule_periods"]] == ["summer", "autumn"]


def test_day_breakdown_names_the_timetable_and_the_reason_for_every_day(tmp_path: Path):
    # Not a pipeline run -- just the contract that the field is present and
    # weekends are not blanked out.
    day_breakdown = [
        {"date": "2026-08-29", "schedule_signature_key": "wknd_sig",
         "schedule_period_key": None, "excluded_from_median": "weekend"},
        {"date": "2026-09-07", "schedule_signature_key": "hol_sig",
         "schedule_period_key": None, "excluded_from_median": "reduced_service"},
        {"date": "2026-08-31", "schedule_signature_key": "bbb",
         "schedule_period_key": "bbb", "excluded_from_median": None},
    ]
    result = build_typical_weekday(
        {"bbb": {}}, ["2026-08-29", "2026-09-07", "2026-08-31"], ["2026-08-31"], [],
        day_breakdown, Counter(), {}, 0, 0, [],
    )
    # Every day says which timetable it ran, whether or not it fed a median.
    assert all(d["schedule_signature_key"] for d in result["day_breakdown"])
    assert result["days_excluded_from_median"] == [
        {"date": "2026-08-29", "reason": "weekend"},
        {"date": "2026-09-07", "reason": "reduced_service"},
    ]
