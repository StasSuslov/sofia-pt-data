import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from export_web import (  # noqa: E402
    COORD_DECIMALS,
    SIMPLIFY_TOLERANCE_M,
    load_route_info,
    aggregate_day_bins,
    apply_threshold,
    build_geometry,
    build_manifest,
    build_period_index,
    build_timeslot_files,
    current_period_key,
    load_typical_weekday_bins,
    main,
    point_along_shape,
    segment_pairs,
    simplify_rdp,
)
from segment_speeds import _project_onto_segment, build_shape, load_static  # noqa: E402

SHAPE_ID = "S1"
ROUTE_ID = "R_BUS"


def _write_static_zip(tmp_path: Path, extra_route: tuple | None = None) -> Path:
    """Minimal GTFS zip: one shape, one bus route, optionally a second route
    on the same shape so a mixed route_type can be exercised."""
    import zipfile

    routes = [("route_id,route_short_name,route_type"), f"{ROUTE_ID},9,3"]
    trips = ["trip_id,route_id,service_id,shape_id", f"T1,{ROUTE_ID},SVC,{SHAPE_ID}"]
    if extra_route:
        rid, rtype = extra_route
        routes.append(f"{rid},11,{rtype}")
        trips.append(f"T2,{rid},SVC,{SHAPE_ID}")

    shapes = ["shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence"]
    shapes += [f"{SHAPE_ID},{lat},{lon},{i}" for i, (lat, lon) in enumerate(STRAIGHT_LINE)]

    path = tmp_path / "gtfs.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("routes.txt", "\n".join(routes) + "\n")
        z.writestr("trips.txt", "\n".join(trips) + "\n")
        z.writestr("shapes.txt", "\n".join(shapes) + "\n")
    return path


# Same fixture idea as test_segment_speeds.py: a straight north-south line,
# so "distance along the shape increases" trivially means "latitude increases".
STRAIGHT_LINE = [(0.000 + 0.001 * i, 0.0) for i in range(11)]


def test_point_along_shape_rounding_preserves_point_order():
    shape = build_shape(STRAIGHT_LINE)
    start = point_along_shape(shape, 0.0)
    end = point_along_shape(shape, 200.0)
    # 5 decimals (~1.1m) is far finer than a 200m segment -- rounding must
    # never flip which endpoint comes first along a straight line.
    assert end[0] > start[0]
    assert start[0] == round(start[0], COORD_DECIMALS)
    assert start[1] == round(start[1], COORD_DECIMALS)


def test_point_along_shape_clamps_past_shape_end():
    shape = build_shape(STRAIGHT_LINE)
    far_past_end = point_along_shape(shape, shape.cum_dist[-1] + 10_000)
    at_end = point_along_shape(shape, shape.cum_dist[-1])
    assert far_past_end == at_end


def test_simplify_drops_near_collinear_points_but_keeps_a_real_corner():
    """Metres in the local plane build_geometry simplifies in: 100 m of
    straight run whose vertices wander under a metre off the line, then a
    right-angle turn. The wander is what a 200 m bin is full of and what
    format_version 1 was right to drop; the corner is what it was wrong to."""
    points = [(0.0, 0.0), (25.0, 0.4), (50.0, -0.6), (75.0, 0.9),
              (100.0, 0.0), (100.0, 50.0), (100.0, 100.0)]
    simplified = simplify_rdp(points, SIMPLIFY_TOLERANCE_M)
    assert simplified == [(0.0, 0.0), (100.0, 0.0), (100.0, 100.0)]

    # The guarantee the manifest states: no dropped vertex ends up further
    # than the tolerance from the line drawn in its place.
    for px, py in points:
        assert min(
            _project_onto_segment(px, py, *simplified[i], *simplified[i + 1])[1]
            for i in range(len(simplified) - 1)
        ) <= SIMPLIFY_TOLERANCE_M


def test_threshold_drops_only_bins_below_cutoff_and_counts_correctly():
    bins = {
        ("S1", 0, "00:00"): (5.0, 1),
        ("S1", 1, "00:00"): (5.0, 2),
        ("S1", 2, "00:00"): (5.0, 5),
    }
    retained, n_before, n_after = apply_threshold(bins, min_samples=2)
    assert n_before == 3
    assert n_after == 2
    assert ("S1", 0, "00:00") not in retained
    assert ("S1", 1, "00:00") in retained
    assert ("S1", 2, "00:00") in retained


def test_segment_pairs_dedupes_across_timeslots():
    bins = {
        ("S1", 0, "00:00"): (5.0, 2),
        ("S1", 0, "00:15"): (6.0, 2),
        ("S2", 3, "00:00"): (2.0, 2),
    }
    assert segment_pairs(bins) == {("S1", 0), ("S2", 3)}


def test_timeslot_segment_index_resolves_back_to_same_shape_and_segment():
    shape = build_shape(STRAIGHT_LINE)
    shapes_by_id = {"S1": shape}
    bins = {
        ("S1", 0, "00:00"): (5.0, 3),
        ("S1", 3, "00:15"): (2.0, 4),
    }
    pairs = segment_pairs(bins)
    geometry, index_of, missing = build_geometry(pairs, shapes_by_id)
    assert missing == 0
    # format_version 2 CSR: one offset per segment plus a closing one, and
    # every segment's slice holds at least the two bin endpoints.
    assert len(geometry["point_offset"]) == len(geometry["segment_index"]) + 1
    assert geometry["point_offset"][-1] == len(geometry["lat"]) == len(geometry["lon"])
    assert all(b - a >= 2 for a, b in zip(geometry["point_offset"], geometry["point_offset"][1:]))

    timeslot_files = build_timeslot_files(bins, index_of)
    assert set(timeslot_files) == {"00:00", "00:15"}

    for slot, payload in timeslot_files.items():
        assert payload["timeslot"] == slot
        for pos, speed_kmh, n in zip(payload["segment_idx"], payload["speed_kmh"], payload["n_samples"]):
            shape_key = geometry["shape_keys"][geometry["shape_idx"][pos]]
            seg_idx = geometry["segment_index"][pos]
            key = (shape_key, seg_idx, slot)
            assert key in bins
            assert bins[key][1] == n
            assert speed_kmh == round(bins[key][0] * 3.6)


def test_build_geometry_counts_missing_shapes_instead_of_crashing():
    geometry, index_of, missing = build_geometry({("GHOST", 0)}, shapes_by_key={})
    assert missing == 1
    assert geometry["shape_ids"] == []
    assert index_of == {}


def test_build_timeslot_files_skips_bins_geometry_could_not_resolve():
    # index_of has nothing for ("S1", 0) -- as if its shape were missing from
    # the static feed passed to build_geometry -- so the bin must be dropped,
    # not crash or reference a nonexistent index.
    bins = {("S1", 0, "00:00"): (5.0, 3)}
    timeslot_files = build_timeslot_files(bins, index_of={})
    assert timeslot_files == {}


def test_manifest_has_required_fields_and_correct_derived_counts():
    manifest = build_manifest(
        mode="typical_weekday",
        min_samples=2,
        bins_before=10,
        bins_after=6,
        pairs_before=4,
        pairs_after=3,
        missing_shapes=0,
        timeslot_labels=["00:15", "00:00"],
        source={"typical_weekday_file": "typical_weekday.json"},
        days_processed=["2026-08-27"],
        days_in_median=["2026-08-27"],
        incomplete_days={"2026-08-27": "only 52.45% coverage of the calendar day"},
        shapes_observed=3,
        shapes_written=3,
        total_static_shapes=10,
    )
    for key in (
        "format_version", "generated_at", "mode", "source", "days_processed",
        "days_in_median", "incomplete_days", "preprocessing_thresholds",
        "web_export", "segment_count", "timeslot_count", "timeslots",
        "known_limitations",
    ):
        assert key in manifest, f"missing required manifest field: {key}"

    assert manifest["web_export"]["min_samples_threshold"] == 2
    assert manifest["web_export"]["bins_dropped"] == 4
    assert manifest["web_export"]["bins_dropped_pct"] == 40.0
    assert manifest["web_export"]["segments_dropped_pct"] == 25.0
    assert manifest["segment_count"] == 3
    assert manifest["timeslots"] == ["00:00", "00:15"]  # sorted, regardless of input order


def test_aggregate_day_bins_computes_median_and_count(tmp_path):
    path = tmp_path / "segment_speeds_2026-08-28.jsonl"
    rows = [
        {"shape_id": "S1", "shape_key": "S1@abc12345", "segment_index": 0, "timeslot": "00:00", "speed_ms": 1.0},
        {"shape_id": "S1", "shape_key": "S1@abc12345", "segment_index": 0, "timeslot": "00:00", "speed_ms": 3.0},
    ]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    bins = aggregate_day_bins(path)
    assert bins == {("S1@abc12345", 0, "00:00"): (2.0, 2)}


def test_aggregate_day_bins_tolerates_torn_trailing_line(tmp_path):
    path = tmp_path / "segment_speeds_2026-08-28.jsonl"
    good = json.dumps({"shape_id": "S1", "shape_key": "S1@abc12345", "segment_index": 0, "timeslot": "00:00", "speed_ms": 4.0})
    path.write_text(good + "\n" + '{"shape_id": "S1", "segment_index"', encoding="utf-8")
    bins = aggregate_day_bins(path)
    assert bins == {("S1@abc12345", 0, "00:00"): (4.0, 1)}


def test_geometry_carries_route_metadata_per_shape(tmp_path: Path):
    """
    Feature 3 (transport-type filter) needs to know a tram from a bus, and
    shape_id alone cannot say. Metadata rides once per shape, aligned with
    geometry["shape_ids"], so a filter is one hop through shape_idx.
    """
    static_zip = _write_static_zip(tmp_path)
    route_info = load_route_info(static_zip)
    _, shapes_by_key, shape_ids_by_key = load_static(static_zip)
    shape_key = next(iter(shapes_by_key))  # only one shape in this fixture
    pairs = {(shape_key, 0), (shape_key, 1)}

    geometry, index_of, missing = build_geometry(pairs, shapes_by_key, route_info,
                                                  shape_ids_by_key)

    assert missing == 0
    pos = geometry["shape_keys"].index(shape_key)
    assert geometry["shape_ids"][pos] == [SHAPE_ID]
    assert geometry["shape_route_ids"][pos] == [ROUTE_ID]
    assert geometry["shape_route_type"][pos] == 3  # bus, from routes.txt
    assert len(geometry["shape_route_type"]) == len(geometry["shape_ids"])


def test_route_type_left_unset_when_a_shape_mixes_types(tmp_path: Path):
    """
    A shape served by both a tram and a bus route cannot answer a type filter,
    so the field is null rather than resolved by picking whichever route came
    first — a filter that quietly guesses is worse than one that shows nothing.
    """
    static_zip = _write_static_zip(tmp_path, extra_route=("R_TRAM", "0"))
    info = load_route_info(static_zip)
    assert info[SHAPE_ID]["route_type"] is None
    assert info[SHAPE_ID]["route_ids"] == sorted([ROUTE_ID, "R_TRAM"])


# ─── Schedule periods: one bundle each, plus an index over them ────────────

def _write_typical_weekday(path: Path, shape_key: str) -> None:
    """Two schedule periods over the same segment: the summer timetable and
    the autumn one that replaced it. Both cover the same (segment, timeslot)
    bins, which is exactly the case a single pooled median would blur."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "days_processed": ["2026-09-03", "2026-09-08"],
        "days_in_median_mon_fri": ["2026-09-03", "2026-09-08"],
        "static_feeds": [],
        "schedule_periods": [
            {"period_key": "summer00000000", "days": ["2026-09-03"],
             "days_in_median_mon_fri": ["2026-09-03"], "first_date": "2026-09-03",
             "last_date": "2026-09-03", "route_count": 135, "trip_count": 14907,
             "bin_count": 2},
            {"period_key": "autumn00000000", "days": ["2026-09-08"],
             "days_in_median_mon_fri": ["2026-09-08"], "first_date": "2026-09-08",
             "last_date": "2026-09-08", "route_count": 138, "trip_count": 15600,
             "bin_count": 2},
        ],
        "segments": {
            "summer00000000": {
                f"{shape_key}|0|08:00": {"median_speed_ms": 10.0, "n_samples": 5},
                f"{shape_key}|1|08:00": {"median_speed_ms": 9.0, "n_samples": 5},
            },
            "autumn00000000": {
                f"{shape_key}|0|08:00": {"median_speed_ms": 4.0, "n_samples": 3},
                f"{shape_key}|0|08:15": {"median_speed_ms": 5.0, "n_samples": 3},
            },
        },
    }), encoding="utf-8")


def test_load_typical_weekday_bins_keeps_each_period_separate(tmp_path: Path):
    path = tmp_path / "typical_weekday.json"
    _write_typical_weekday(path, "abc123")
    bins_by_period, raw = load_typical_weekday_bins(path)

    assert set(bins_by_period) == {"summer00000000", "autumn00000000"}
    assert bins_by_period["summer00000000"][("abc123", 0, "08:00")] == (10.0, 5)
    assert bins_by_period["autumn00000000"][("abc123", 0, "08:00")] == (4.0, 3)
    assert len(raw["schedule_periods"]) == 2


def test_current_period_is_the_one_with_the_newest_weekday():
    periods = [
        {"period_key": "old", "days_in_median_mon_fri": ["2026-08-27", "2026-08-28"]},
        {"period_key": "new", "days_in_median_mon_fri": ["2026-09-08"]},
        # A weekend-only period is never the current weekday median (D4),
        # even when its dates are the most recent in the archive.
        {"period_key": "weekend", "days_in_median_mon_fri": []},
    ]
    assert current_period_key(periods) == "new"
    assert current_period_key([]) is None


def test_period_index_lists_every_period_and_names_the_current_one():
    entries = [{"period_key": "old", "path": "old"}, {"period_key": "new", "path": "new"}]
    # A bundle can only list its own days, so the days that entered no median
    # at all are named here or nowhere in the web tree.
    excluded = [{"date": "2026-09-05", "reason": "weekend"},
                {"date": "2026-09-07", "reason": "reduced_service"}]
    index = build_period_index(entries, "new", excluded)
    for key in ("format_version", "generated_at", "mode", "current_period",
                "period_count", "periods", "days_excluded_from_median"):
        assert key in index, f"missing required index field: {key}"
    assert index["mode"] == "typical_weekday_index"
    assert index["period_count"] == 2
    assert index["current_period"] == "new"
    assert index["days_excluded_from_median"] == excluded


def test_export_writes_one_bundle_per_period_plus_an_index(tmp_path: Path, monkeypatch, capsys):
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    _write_static_zip(static_dir).rename(static_dir / "gtfs_2026-09-03.zip")

    data_dir = tmp_path / "data"
    _, shapes_by_key, _ = load_static(static_dir / "gtfs_2026-09-03.zip")
    shape_key = next(iter(shapes_by_key))
    _write_typical_weekday(data_dir / "processed" / "typical_weekday.json", shape_key)

    # A day bundle from an earlier run, which this typical-weekday-only run
    # is never told about: web/index.json is rebuilt by scanning the output
    # directory, so it has to keep listing the day anyway.
    earlier_day = data_dir / "web" / "2026-08-27"
    earlier_day.mkdir(parents=True)
    (earlier_day / "manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["export_web.py", str(static_dir), str(data_dir),
                                      "--min-samples", "2"])
    main()

    root_index = json.loads((data_dir / "web" / "index.json").read_text(encoding="utf-8"))
    assert root_index["days"] == [{"date": "2026-08-27", "path": "2026-08-27"}]
    assert root_index["typical_weekday"]["current_period"] == "autumn00000000"

    root = data_dir / "web" / "typical_weekday"
    index = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    assert index["current_period"] == "autumn00000000"  # newest weekday, 2026-09-08
    assert {p["period_key"] for p in index["periods"]} == {"summer00000000", "autumn00000000"}
    by_key = {p["period_key"]: p for p in index["periods"]}
    assert by_key["summer00000000"]["days_in_median"] == ["2026-09-03"]
    assert by_key["autumn00000000"]["route_count"] == 138

    for period_key in ("summer00000000", "autumn00000000"):
        bundle = root / period_key
        assert (bundle / "geometry.json").exists()
        manifest = json.loads((bundle / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["mode"] == "typical_weekday"
        assert manifest["schedule_period"]["period_key"] == period_key
        assert by_key[period_key]["path"] == period_key
        slots = sorted(f.stem for f in (bundle / "timeslots").glob("*.json"))
        assert slots == [t.replace(":", "") for t in manifest["timeslots"]]

    # The two periods really are two medians of the same segment, not one.
    summer = json.loads((root / "summer00000000" / "timeslots" / "0800.json").read_text())
    autumn = json.loads((root / "autumn00000000" / "timeslots" / "0800.json").read_text())
    assert summer["speed_kmh"][0] == 36  # 10.0 m/s
    assert autumn["speed_kmh"][0] == 14  # 4.0 m/s
    # 08:15 exists only under the autumn timetable, so only its bundle has it.
    assert (root / "autumn00000000" / "timeslots" / "0815.json").exists()
    assert not (root / "summer00000000" / "timeslots" / "0815.json").exists()


def test_export_drops_a_period_directory_that_left_the_archive(tmp_path: Path, monkeypatch):
    """The tree is rebuilt each run, so a stale period bundle cannot survive
    as a directory the index no longer mentions."""
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    _write_static_zip(static_dir).rename(static_dir / "gtfs_2026-09-03.zip")

    data_dir = tmp_path / "data"
    _, shapes_by_key, _ = load_static(static_dir / "gtfs_2026-09-03.zip")
    _write_typical_weekday(data_dir / "processed" / "typical_weekday.json",
                           next(iter(shapes_by_key)))
    stale = data_dir / "web" / "typical_weekday" / "gone000000000000"
    stale.mkdir(parents=True)
    (stale / "manifest.json").write_text("{}", encoding="utf-8")

    monkeypatch.setattr(sys, "argv", ["export_web.py", str(static_dir), str(data_dir)])
    main()

    assert not stale.exists()


def test_manifest_names_the_schedule_period_as_a_limitation():
    period = {"period_key": "abc", "first_date": "2026-09-08", "last_date": "2026-09-12",
              "route_count": 138, "trip_count": 15600}
    with_period = build_manifest(
        mode="typical_weekday", min_samples=2, bins_before=10, bins_after=6,
        pairs_before=4, pairs_after=3, missing_shapes=0, timeslot_labels=["08:00"],
        source={}, days_processed=[], days_in_median=[], incomplete_days={},
        shapes_observed=3, shapes_written=3, total_static_shapes=10, schedule_period=period,
    )
    without = build_manifest(
        mode="2026-09-08", min_samples=2, bins_before=10, bins_after=6,
        pairs_before=4, pairs_after=3, missing_shapes=0, timeslot_labels=["08:00"],
        source={}, days_processed=[], days_in_median=[], incomplete_days={},
        shapes_observed=3, shapes_written=3, total_static_shapes=10,
    )
    assert with_period["schedule_period"] == period
    assert len(with_period["known_limitations"]) == len(without["known_limitations"]) + 1
    assert any("2026-09-08 to 2026-09-12" in line for line in with_period["known_limitations"])
    # The day switcher exports one day, which ran one timetable -- no period
    # field, no extra caveat.
    assert without["schedule_period"] is None


def test_manifest_limitation_counts_shapes_written_not_observed():
    # Observation is counted before the min_samples threshold; geometry.json holds
    # only what survived it. The sentence must name the file's own count.
    manifest = build_manifest(
        mode="2026-09-08", min_samples=2, bins_before=10, bins_after=6,
        pairs_before=4, pairs_after=3, missing_shapes=0, timeslot_labels=["08:00"],
        source={}, days_processed=[], days_in_median=[], incomplete_days={},
        shapes_observed=5, shapes_written=3, total_static_shapes=10,
    )
    line = next(l for l in manifest["known_limitations"] if "appear here" in l)
    assert "3 of the static feed's 10 shapes" in line
    assert "2 further shapes were observed" in line

    one = build_manifest(
        mode="2026-09-08", min_samples=2, bins_before=10, bins_after=6,
        pairs_before=4, pairs_after=3, missing_shapes=0, timeslot_labels=["08:00"],
        source={}, days_processed=[], days_in_median=[], incomplete_days={},
        shapes_observed=4, shapes_written=3, total_static_shapes=10,
    )
    assert any("1 further shape was observed" in l for l in one["known_limitations"])
