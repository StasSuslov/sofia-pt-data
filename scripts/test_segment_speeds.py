import json
import math
import sys
from collections import defaultdict
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
    haversine_m,
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
    trip_map = {TRIP_ID: (ROUTE_ID, SHAPE_ID)}
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
