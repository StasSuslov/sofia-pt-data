import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from generate_manifest import analyze_gaps, build_manifest, load_jsonl


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def test_analyze_gaps_no_gaps_at_regular_interval():
    timestamps = [1000, 1045, 1090, 1135, 1180]
    result = analyze_gaps(timestamps, gap_threshold_multiplier=3.0)
    assert result["gap_count"] == 0
    assert result["nominal_interval_sec"] == 45
    assert result["coverage_pct"] == 100.0


def test_analyze_gaps_detects_a_downtime_gap():
    # regular 45s polls, then a ~10 minute outage, then resumes
    timestamps = [1000, 1045, 1090, 1090 + 600, 1090 + 645]
    result = analyze_gaps(timestamps, gap_threshold_multiplier=3.0)
    assert result["gap_count"] == 1
    assert result["gaps"][0]["gap_seconds"] == 600
    assert result["coverage_pct"] < 100.0


def test_analyze_gaps_empty_and_single_point():
    assert analyze_gaps([], 3.0)["coverage_pct"] is None
    assert analyze_gaps([1000], 3.0)["coverage_pct"] is None


def test_build_manifest_with_heartbeat_distinguishes_empty_from_error(tmp_path: Path):
    data_path = tmp_path / "2026-08-27.jsonl"
    polls_path = tmp_path / "2026-08-27.polls.jsonl"

    write_jsonl(data_path, [
        {"snapshot_ts": 1000, "vehicle_id": "A1"},
        {"snapshot_ts": 1000, "vehicle_id": "A2"},
    ])
    write_jsonl(polls_path, [
        {"snapshot_ts": 1000, "fetch_ok": True, "vehicle_count": 2, "dropped_out_of_bbox": 1},
        {"snapshot_ts": 1045, "fetch_ok": True, "vehicle_count": 0},   # genuinely empty, not downtime
        {"snapshot_ts": 1090, "fetch_ok": False, "vehicle_count": 0},  # transient fetch error
    ])

    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0)

    assert manifest["heartbeat_available"] is True
    assert manifest["polls_logged"] == 3
    assert manifest["successful_polls"] == 2
    assert manifest["empty_polls"] == 1
    assert manifest["fetch_error_polls"] == 1
    assert manifest["total_vehicle_records"] == 2
    assert manifest["gap_count"] == 0  # all three heartbeats are on schedule, no real downtime
    # sums across polls, defaulting to 0 for older heartbeat lines without the field
    assert manifest["dropped_out_of_bbox"] == 1


def test_build_manifest_flags_heartbeat_deployed_mid_day(tmp_path: Path):
    # data file has records from before heartbeat logging was ever deployed,
    # plus records once collect.py restarted with heartbeat support
    data_path = tmp_path / "2026-08-28.jsonl"
    polls_path = tmp_path / "2026-08-28.polls.jsonl"

    write_jsonl(data_path, [
        {"snapshot_ts": 0, "vehicle_id": "A1"},        # midnight, no heartbeat yet
        {"snapshot_ts": 45, "vehicle_id": "A1"},
        {"snapshot_ts": 10000, "vehicle_id": "A1"},    # after the mid-day restart
    ])
    write_jsonl(polls_path, [
        {"snapshot_ts": 10000, "fetch_ok": True, "vehicle_count": 1},
        {"snapshot_ts": 10045, "fetch_ok": True, "vehicle_count": 1},
    ])

    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0)

    assert manifest["heartbeat_available"] is True
    assert manifest["heartbeat_partial"] is True
    assert "pre_heartbeat_note" in manifest
    # the huge jump from ts=45 to ts=10000 must still show up as a gap —
    # it must NOT be silently dropped just because heartbeat started after it
    assert manifest["gap_count"] == 1
    assert manifest["gaps"][0]["gap_seconds"] == 10000 - 45


def test_build_manifest_without_heartbeat_falls_back_to_data_file(tmp_path: Path):
    data_path = tmp_path / "2026-08-20.jsonl"
    write_jsonl(data_path, [
        {"snapshot_ts": 1000, "vehicle_id": "A1"},
        {"snapshot_ts": 1045, "vehicle_id": "A1"},
    ])

    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0)

    assert manifest["heartbeat_available"] is False
    assert manifest["polls_logged"] is None
    assert manifest["total_vehicle_records"] == 2
    assert "data_sha256" in manifest


def test_load_jsonl_skips_a_malformed_trailing_line_instead_of_crashing(tmp_path: Path):
    # Simulates rsync pulling a data file mid-write: the last line is torn.
    path = tmp_path / "torn.jsonl"
    path.write_text('{"snapshot_ts": 1000, "vehicle_id": "A1"}\n{"snapshot_ts": 1045, "vehic', encoding="utf-8")

    records, malformed = load_jsonl(path)

    assert records == [{"snapshot_ts": 1000, "vehicle_id": "A1"}]
    assert malformed == 1


def test_build_manifest_reports_malformed_lines_instead_of_crashing(tmp_path: Path):
    data_path = tmp_path / "2026-08-29.jsonl"
    polls_path = tmp_path / "2026-08-29.polls.jsonl"

    data_path.write_text('{"snapshot_ts": 1000, "vehicle_id": "A1"}\nnot valid json at all', encoding="utf-8")
    write_jsonl(polls_path, [
        {"snapshot_ts": 1000, "fetch_ok": True, "vehicle_count": 1,
         "entities_total": 1, "vehicles_with_position": 1, "dropped_out_of_bbox": 0},
    ])

    # must not raise
    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0)

    assert manifest["data_malformed_lines"] == 1
    assert manifest["polls_malformed_lines"] == 0
    assert manifest["total_vehicle_records"] == 1
    # the checksum still gets computed over exactly what's on disk, torn line included
    assert "data_sha256" in manifest


def test_build_manifest_aggregates_bbox_pipeline_counts(tmp_path: Path):
    data_path = tmp_path / "2026-08-30.jsonl"
    polls_path = tmp_path / "2026-08-30.polls.jsonl"

    write_jsonl(data_path, [{"snapshot_ts": 1000, "vehicle_id": "A1"}])
    write_jsonl(polls_path, [
        {"snapshot_ts": 1000, "fetch_ok": True, "vehicle_count": 1,
         "entities_total": 5, "vehicles_with_position": 4, "dropped_out_of_bbox": 3},
    ])

    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0)

    assert manifest["entities_total"] == 5
    assert manifest["vehicles_with_position"] == 4
    assert manifest["dropped_out_of_bbox"] == 3
    assert manifest["dropped_out_of_bbox_pct"] == 75.0  # 3 of 4 positioned vehicles dropped
