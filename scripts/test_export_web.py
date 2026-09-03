import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from export_web import (  # noqa: E402
    COORD_DECIMALS,
    load_route_info,
    aggregate_day_bins,
    apply_threshold,
    build_geometry,
    build_manifest,
    build_timeslot_files,
    point_along_shape,
    segment_pairs,
)
from segment_speeds import build_shape, load_static  # noqa: E402

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
