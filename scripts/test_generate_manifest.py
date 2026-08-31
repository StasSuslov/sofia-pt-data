import gzip
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from generate_manifest import (
    analyze_gaps,
    build_manifest,
    day_bounds,
    find_day_files,
    load_jsonl,
    manifest_is_current,
    should_skip,
)

SOFIA = ZoneInfo("Europe/Sofia")


def write_jsonl(path: Path, records: list[dict]) -> None:
    with path.open("w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def midnight_ts(date_str: str, tz: ZoneInfo) -> int:
    """Local midnight of `date_str` in `tz`, as a UTC epoch second — the same
    value day_bounds() computes for day_start_ts, kept independent here so
    fixtures don't rely on the function under test to build themselves."""
    y, m, d = (int(p) for p in date_str.split("-"))
    return int(datetime(y, m, d, tzinfo=tz).timestamp())


# ─── day_bounds ─────────────────────────────────────────────────────────────

def test_day_bounds_full_past_day_is_exactly_86400_seconds():
    now = datetime(2026, 8, 29, tzinfo=timezone.utc)  # well after the day in question
    day_start_ts, day_end_ts, day_in_progress = day_bounds("2026-08-27", SOFIA, now)

    assert day_in_progress is False
    assert day_end_ts - day_start_ts == 86400
    # 2026-08-27 00:00 Europe/Sofia == 2026-08-26 21:00 UTC (UTC+3 in August)
    assert datetime.fromtimestamp(day_start_ts, tz=timezone.utc) == datetime(2026, 8, 26, 21, 0, tzinfo=timezone.utc)


def test_day_bounds_clamps_in_progress_day_to_now():
    # 10:00 UTC on 2026-08-31 is still 2026-08-31 locally (UTC+3) and short of local midnight
    now = datetime(2026, 8, 31, 10, 0, tzinfo=timezone.utc)
    day_start_ts, day_end_ts, day_in_progress = day_bounds("2026-08-31", SOFIA, now)

    assert day_in_progress is True
    assert day_end_ts == int(now.timestamp())
    assert day_start_ts < day_end_ts


# ─── analyze_gaps ───────────────────────────────────────────────────────────

def test_analyze_gaps_no_gaps_at_regular_interval():
    timestamps = [1000, 1045, 1090, 1135, 1180]
    # day boundaries chosen so 5 polls at the nominal 45s interval exactly
    # fill the "day" (225s) — coverage_pct is a count-vs-expected ratio now,
    # not derived from the observed span.
    result = analyze_gaps(timestamps, nominal_interval_sec=45, gap_threshold_multiplier=3.0,
                           day_start_ts=1000, day_end_ts=1225)
    assert result["gap_count"] == 0
    assert result["observed_interval_sec"] == 45
    assert result["coverage_pct"] == 100.0


def test_analyze_gaps_detects_a_downtime_gap():
    # regular 45s polls, then a ~10 minute outage, then resumes
    timestamps = [1000, 1045, 1090, 1090 + 600, 1090 + 645]
    result = analyze_gaps(timestamps, nominal_interval_sec=45, gap_threshold_multiplier=3.0,
                           day_start_ts=1000, day_end_ts=1090 + 645 + 45)
    assert result["gap_count"] == 1
    assert result["gaps"][0]["gap_seconds"] == 600
    assert result["coverage_pct"] < 100.0


def test_analyze_gaps_empty_shows_zero_coverage_not_none():
    # Previously this returned coverage_pct=None, which reads as "not
    # applicable" and could hide a genuine full-day outage. A day with zero
    # polls against a real calendar span is 0% covered, not undefined.
    result = analyze_gaps([], nominal_interval_sec=45, gap_threshold_multiplier=3.0,
                           day_start_ts=0, day_end_ts=86400)
    assert result["coverage_pct"] == 0.0
    assert result["gap_count"] == 1
    assert result["gaps"][0]["gap_seconds"] == 86400
    assert result["observed_interval_sec"] is None


def test_analyze_gaps_single_point_gets_a_real_number_not_none():
    result = analyze_gaps([43200], nominal_interval_sec=45, gap_threshold_multiplier=3.0,
                           day_start_ts=0, day_end_ts=86400)
    assert result["coverage_pct"] == round(100 * 1 / (86400 / 45), 2)
    assert result["gap_count"] == 2  # day_start->point and point->day_end both exceed threshold
    assert result["observed_interval_sec"] is None  # can't compute a delta from one point


def test_analyze_gaps_degraded_interval_shows_about_50_pct_not_100_pct():
    """
    The regression this task exists to fix: a collector that quietly halves
    its polling rate (45s nominal -> 90s actual) for a full day. The old
    formula derived "nominal_interval" as the median of observed deltas, so
    it would recompute its baseline as 90s, find zero gaps exceeding 3x that,
    and report ~100% coverage. Measured against the *configured* interval,
    the same day is correctly ~50% covered.
    """
    day_start_ts, day_end_ts = 0, 86400
    timestamps = list(range(day_start_ts, day_end_ts, 90))

    result = analyze_gaps(timestamps, nominal_interval_sec=45, gap_threshold_multiplier=3.0,
                           day_start_ts=day_start_ts, day_end_ts=day_end_ts)

    assert result["observed_interval_sec"] == 90
    assert result["coverage_pct"] == 50.0
    # no single gap is >3x the *observed* 90s rate — every poll is uniformly
    # spaced — which is exactly why the old gap-count-based signal missed this
    assert result["gap_count"] == 0


# ─── build_manifest ─────────────────────────────────────────────────────────

def test_build_manifest_with_heartbeat_distinguishes_empty_from_error(tmp_path: Path):
    date_str = "2026-08-27"
    data_path = tmp_path / f"{date_str}.jsonl"
    polls_path = tmp_path / f"{date_str}.polls.jsonl"
    start = midnight_ts(date_str, SOFIA)

    write_jsonl(data_path, [
        {"snapshot_ts": start, "vehicle_id": "A1"},
        {"snapshot_ts": start, "vehicle_id": "A2"},
    ])
    write_jsonl(polls_path, [
        {"snapshot_ts": start, "fetch_ok": True, "vehicle_count": 2, "dropped_out_of_bbox": 1},
        {"snapshot_ts": start + 45, "fetch_ok": True, "vehicle_count": 0},   # genuinely empty, not downtime
        {"snapshot_ts": start + 90, "fetch_ok": False, "vehicle_count": 0},  # transient fetch error
    ])

    # Clamp "now" to just after the last poll so the trailing edge of the
    # (otherwise mostly-empty) day doesn't register as a downtime gap — this
    # test is about telling empty/error polls apart, not full-day coverage.
    now = datetime.fromtimestamp(start + 100, tz=timezone.utc)
    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0, tz=SOFIA, now=now)

    assert manifest["heartbeat_available"] is True
    assert manifest["day_in_progress"] is True
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
    date_str = "2026-08-28"
    data_path = tmp_path / f"{date_str}.jsonl"
    polls_path = tmp_path / f"{date_str}.polls.jsonl"
    start = midnight_ts(date_str, SOFIA)

    write_jsonl(data_path, [
        {"snapshot_ts": start, "vehicle_id": "A1"},          # midnight, no heartbeat yet
        {"snapshot_ts": start + 45, "vehicle_id": "A1"},
        {"snapshot_ts": start + 10000, "vehicle_id": "A1"},  # after the mid-day restart
    ])
    write_jsonl(polls_path, [
        {"snapshot_ts": start + 10000, "fetch_ok": True, "vehicle_count": 1},
        {"snapshot_ts": start + 10045, "fetch_ok": True, "vehicle_count": 1},
    ])

    now = datetime.fromtimestamp(start + 200_000, tz=timezone.utc)  # well after this day ended
    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0, tz=SOFIA, now=now)

    assert manifest["heartbeat_available"] is True
    assert manifest["heartbeat_partial"] is True
    assert manifest["day_in_progress"] is False
    assert "pre_heartbeat_note" in manifest

    # the jump from ts=start+45 to ts=start+10000 must still show up as a
    # gap — it must NOT be silently dropped just because heartbeat started after it
    mid_day_gaps = [g for g in manifest["gaps"] if g["gap_seconds"] == 10000 - 45]
    assert len(mid_day_gaps) == 1

    # NOTE (test expectation changed from the pre-rewrite version): collection
    # also never resumed after start+10045 for the rest of the day. The old
    # span-based formula only ever looked between the first and last record,
    # so this trailing silence was invisible and gap_count was 1. Measured
    # against the real calendar day, that trailing silence all the way to
    # local midnight is a second, equally real gap — gap_count is now 2.
    assert manifest["gap_count"] == 2
    assert manifest["coverage_pct"] < 10  # 5 polls total against a full day


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


def test_build_manifest_late_start_shows_true_day_coverage_not_span_based(tmp_path: Path):
    """
    The exact real-world incident this task exists to fix: data/sofia/2026-08-27
    (VPS migration meant collection started at 11:07 local Sofia time). The old
    span-based formula (first-record to last-record) reported 97.78% coverage,
    hiding that the entire morning peak was missing. True calendar-day
    coverage was 53.7% — see CLAUDE.md section 9.
    """
    date_str = "2026-08-27"
    data_path = tmp_path / f"{date_str}.jsonl"
    start = midnight_ts(date_str, SOFIA)
    day_end = start + 86400
    data_start = start + 11 * 3600 + 7 * 60  # 11:07 local

    write_jsonl(data_path, [
        {"snapshot_ts": ts, "vehicle_id": "A1"} for ts in range(data_start, day_end, 45)
    ])

    now = datetime.fromtimestamp(day_end + 100_000, tz=timezone.utc)  # well after this day ended
    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0, tz=SOFIA, now=now)

    assert manifest["heartbeat_available"] is False
    assert manifest["day_in_progress"] is False
    assert 52 <= manifest["coverage_pct"] <= 55  # ~53.7% in the real incident, not ~97.78%

    # the missing morning must show up as an edge gap from local midnight to
    # the first record — invisible under the old span-based math
    assert manifest["gaps"][0]["after_ts"] == start
    assert manifest["gaps"][0]["gap_seconds"] > 10 * 3600


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


def test_load_jsonl_skips_a_malformed_trailing_line_instead_of_crashing(tmp_path: Path):
    # Simulates rsync pulling a data file mid-write: the last line is torn.
    path = tmp_path / "torn.jsonl"
    path.write_text('{"snapshot_ts": 1000, "vehicle_id": "A1"}\n{"snapshot_ts": 1045, "vehic', encoding="utf-8")

    records, malformed = load_jsonl(path)

    assert records == [{"snapshot_ts": 1000, "vehicle_id": "A1"}]
    assert malformed == 1


# ─── gzip transparency (deploy/sofia-compress.service on the VPS) ──────────

def gzip_copy(src: Path, dst: Path) -> None:
    with src.open("rb") as f_in, gzip.open(dst, "wb") as f_out:
        f_out.write(f_in.read())


def test_gzipped_day_file_matches_its_uncompressed_twin(tmp_path: Path):
    """
    The provenance invariant this whole feature exists to hold: a manifest
    must describe the same uncompressed bytes whether the local copy is
    plain or gzipped. Two directories, same content, one plain and one
    gzipped after the fact — the resulting manifests must be identical
    except for the one field that's allowed (required, even) to differ.
    """
    date_str = "2026-08-29"
    plain_dir, gz_dir = tmp_path / "plain", tmp_path / "gz"
    plain_dir.mkdir()
    gz_dir.mkdir()

    start = midnight_ts(date_str, SOFIA)
    records = [{"snapshot_ts": start, "vehicle_id": "A1"}, {"snapshot_ts": start + 45, "vehicle_id": "A1"}]
    polls = [{"snapshot_ts": start, "fetch_ok": True, "vehicle_count": 1},
             {"snapshot_ts": start + 45, "fetch_ok": True, "vehicle_count": 1}]

    data_path = plain_dir / f"{date_str}.jsonl"
    polls_path = plain_dir / f"{date_str}.polls.jsonl"
    write_jsonl(data_path, records)
    write_jsonl(polls_path, polls)

    gzip_copy(data_path, gz_dir / f"{date_str}.jsonl.gz")
    gzip_copy(polls_path, gz_dir / f"{date_str}.polls.jsonl.gz")

    now = datetime.fromtimestamp(start + 200_000, tz=timezone.utc)
    plain_manifest = build_manifest(data_path, gap_threshold_multiplier=3.0, tz=SOFIA, now=now)
    gz_manifest = build_manifest(gz_dir / f"{date_str}.jsonl.gz", gap_threshold_multiplier=3.0, tz=SOFIA, now=now)

    assert plain_manifest["compressed"] is False
    assert gz_manifest["compressed"] is True
    # data_sha256/data_size_bytes in particular must be over the UNCOMPRESSED
    # bytes — this is where a naive .stat().st_size on the .gz would show up.
    assert gz_manifest["data_sha256"] == plain_manifest["data_sha256"]
    assert gz_manifest["data_size_bytes"] == plain_manifest["data_size_bytes"]
    assert gz_manifest["data_file"] == plain_manifest["data_file"] == f"{date_str}.jsonl"

    ignore = {"compressed", "generated_at"}
    assert {k: v for k, v in gz_manifest.items() if k not in ignore} == \
           {k: v for k, v in plain_manifest.items() if k not in ignore}


def test_gzipped_heartbeat_is_found_and_parsed(tmp_path: Path):
    date_str = "2026-08-30"
    start = midnight_ts(date_str, SOFIA)
    data_path = tmp_path / f"{date_str}.jsonl"
    write_jsonl(data_path, [{"snapshot_ts": start, "vehicle_id": "A1"}])

    polls_path_gz = tmp_path / f"{date_str}.polls.jsonl.gz"
    with gzip.open(polls_path_gz, "wt", encoding="utf-8") as f:
        f.write(json.dumps({"snapshot_ts": start, "fetch_ok": True, "vehicle_count": 1}) + "\n")

    now = datetime.fromtimestamp(start + 100, tz=timezone.utc)
    manifest = build_manifest(data_path, gap_threshold_multiplier=3.0, tz=SOFIA, now=now)

    assert manifest["heartbeat_available"] is True
    assert manifest["polls_logged"] == 1
    assert manifest["polls_file"] == f"{date_str}.polls.jsonl"  # always the uncompressed name
    assert manifest["polls_sha256"] is not None


def test_find_day_files_prefers_uncompressed_and_dedupes_by_date(tmp_path: Path):
    (tmp_path / "2026-08-27.jsonl").write_text("{}\n", encoding="utf-8")
    (tmp_path / "2026-08-27.jsonl.gz").write_bytes(gzip.compress(b"{}\n"))  # both exist: plain wins
    (tmp_path / "2026-08-28.jsonl.gz").write_bytes(gzip.compress(b"{}\n"))  # gz-only day

    found = [p.name for p in find_day_files(tmp_path)]

    assert found == ["2026-08-27.jsonl", "2026-08-28.jsonl.gz"]


# ─── manifest_is_current / should_skip (fetch-pipeline wiring) ─────────────
# scripts/fetch_data.sh calls generate_manifest.py after every rsync, on a
# cadence as tight as every couple of hours. A closed past day's data and
# heartbeat files never change again, so re-hashing a 100+ MB archive that
# hasn't moved is pure waste this logic exists to skip.

def touch(path: Path, mtime: float) -> None:
    path.write_text("x", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_manifest_is_current_true_when_manifest_newer_than_data_and_polls(tmp_path: Path):
    data_path = tmp_path / "2026-08-27.jsonl"
    polls_path = tmp_path / "2026-08-27.polls.jsonl"
    manifest_path = tmp_path / "2026-08-27.manifest.json"
    touch(data_path, 100)
    touch(polls_path, 110)
    touch(manifest_path, 200)  # generated after both inputs last changed

    assert manifest_is_current(manifest_path, data_path, polls_path) is True


def test_manifest_is_current_false_when_data_file_is_newer(tmp_path: Path):
    # The stale case this whole thing exists for: the data file grew (still
    # being collected, or a fresh rsync pull) after the last manifest run.
    data_path = tmp_path / "2026-08-31.jsonl"
    polls_path = tmp_path / "2026-08-31.polls.jsonl"
    manifest_path = tmp_path / "2026-08-31.manifest.json"
    touch(manifest_path, 100)
    touch(polls_path, 90)
    touch(data_path, 150)  # data appended after the manifest was written

    assert manifest_is_current(manifest_path, data_path, polls_path) is False


def test_manifest_is_current_false_when_polls_file_is_newer(tmp_path: Path):
    data_path = tmp_path / "2026-08-31.jsonl"
    polls_path = tmp_path / "2026-08-31.polls.jsonl"
    manifest_path = tmp_path / "2026-08-31.manifest.json"
    touch(data_path, 90)
    touch(manifest_path, 100)
    touch(polls_path, 150)  # heartbeat log grew after the manifest was written

    assert manifest_is_current(manifest_path, data_path, polls_path) is False


def test_manifest_is_current_false_when_manifest_missing(tmp_path: Path):
    data_path = tmp_path / "2026-08-31.jsonl"
    polls_path = tmp_path / "2026-08-31.polls.jsonl"
    manifest_path = tmp_path / "2026-08-31.manifest.json"  # never written
    touch(data_path, 100)
    touch(polls_path, 100)

    assert manifest_is_current(manifest_path, data_path, polls_path) is False


def test_manifest_is_current_ignores_missing_polls_file(tmp_path: Path):
    # Pre-heartbeat days (or days without a heartbeat log at all) have no
    # .polls.jsonl — its absence must not force a permanent recompute.
    data_path = tmp_path / "2026-08-20.jsonl"
    polls_path = tmp_path / "2026-08-20.polls.jsonl"  # deliberately not created
    manifest_path = tmp_path / "2026-08-20.manifest.json"
    touch(data_path, 100)
    touch(manifest_path, 200)

    assert manifest_is_current(manifest_path, data_path, polls_path) is True


def test_should_skip_true_for_a_current_manifest_without_force(tmp_path: Path):
    data_path = tmp_path / "2026-08-27.jsonl"
    polls_path = tmp_path / "2026-08-27.polls.jsonl"
    manifest_path = tmp_path / "2026-08-27.manifest.json"
    touch(data_path, 100)
    touch(polls_path, 100)
    touch(manifest_path, 200)

    assert should_skip(manifest_path, data_path, polls_path, force=False) is True


def test_should_skip_false_for_a_stale_manifest(tmp_path: Path):
    data_path = tmp_path / "2026-08-31.jsonl"
    polls_path = tmp_path / "2026-08-31.polls.jsonl"
    manifest_path = tmp_path / "2026-08-31.manifest.json"
    touch(manifest_path, 100)
    touch(polls_path, 100)
    touch(data_path, 150)

    assert should_skip(manifest_path, data_path, polls_path, force=False) is False


def test_should_skip_false_when_forced_even_if_manifest_is_current(tmp_path: Path):
    data_path = tmp_path / "2026-08-27.jsonl"
    polls_path = tmp_path / "2026-08-27.polls.jsonl"
    manifest_path = tmp_path / "2026-08-27.manifest.json"
    touch(data_path, 100)
    touch(polls_path, 100)
    touch(manifest_path, 200)  # would be skipped without --force

    assert should_skip(manifest_path, data_path, polls_path, force=True) is False
