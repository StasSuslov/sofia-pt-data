"""Tests for generate_manifest: windows, gaps, manifests, staleness."""

import datetime as dt
import gzip
import json
import os
import sys
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import generate_manifest as gm
from generate_manifest import (
    MANIFEST_SCHEMA_VERSION,
    DayWindow,
    analyze_gaps,
    build_manifest,
    carry_remote_verification,
    day_bounds,
)

SOFIA = ZoneInfo('Europe/Sofia')

# The cadence the fixtures below write their polls at. build_manifest takes
# it as an argument with no default — it belongs to the run being audited,
# and a manifest is only as honest about coverage as the interval it measures
# against — so every test states the cadence it actually generated.
NOMINAL_INTERVAL_SEC = 45
GAP_THRESHOLD_MULTIPLIER = 3.0
DEGRADED_INTERVAL_SEC = 90
SECONDS_PER_HOUR = 3600
SECONDS_PER_DAY = 86400
FIRST_POLL_TS = 1000
SECOND_POLL_TS = FIRST_POLL_TS + NOMINAL_INTERVAL_SEC
POLL_COUNT = 5
REGULAR_DAY_END = FIRST_POLL_TS + POLL_COUNT * NOMINAL_INTERVAL_SEC
OUTAGE_SEC = 600
LAG_SEC = 1800
LONG_AFTER_SEC = 200_000
RESTART_OFFSET_SEC = 10_000
LATE_START_HOUR = 11
LATE_START_OFFSET_SEC = LATE_START_HOUR * SECONDS_PER_HOUR + 7 * 60
MORNING_GAP_MIN_SEC = 10 * SECONDS_PER_HOUR
LATE_START_COVERAGE_MIN = 52
LATE_START_COVERAGE_MAX = 55
BBOX_DROPPED_PCT = 75.0
FIRST_DAY = '2026-08-27'
FULL_DAY_UTC_START = dt.datetime.fromisoformat('2026-08-26T21:00:00+00:00')
MORNING_NOW = dt.datetime.fromisoformat('2026-08-31T10:00:00+00:00')
FAR_FUTURE_NOW = dt.datetime.fromisoformat('2026-08-29T00:00:00+00:00')
FULL_COVERAGE_PCT = 100.0
HALF_COVERAGE_PCT = FULL_COVERAGE_PCT / 2
LONG_DAY_AFTER_SEC = 100_000
IGNORED_TWIN_KEYS = frozenset(('compressed', 'generated_at'))

# mtimes for the staleness tests: only their order matters.
MTIME_EARLIEST = 90
MTIME_BASE = 100
MTIME_POLLS = 110
MTIME_NEWER = 150
MTIME_MANIFEST = 200


def write_jsonl(path: Path, records: list[dict]) -> None:
    """Write one JSON object per line."""
    with path.open('w', encoding='utf-8') as out:
        for record in records:
            out.write(f'{json.dumps(record)}\n')


def midnight_ts(date_str: str, tz: ZoneInfo) -> int:
    """Return local midnight of `date_str` in `tz` as a UTC epoch second.

    The same value day_bounds() computes for day_start_ts, kept independent
    here so fixtures don't rely on the function under test to build
    themselves.
    """
    year, month, day = (int(part) for part in date_str.split('-'))
    return int(dt.datetime(year, month, day, tzinfo=tz).timestamp())


def _poll_line(ts: int) -> bytes:
    record = {'snapshot_ts': ts, 'fetch_ok': True, 'vehicle_count': 1}
    return f'{json.dumps(record)}\n'.encode()


def _now_after(start: int, offset_sec: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(start + offset_sec, tz=dt.UTC)


def _build(data_path: Path, now: dt.datetime | None = None) -> dict:
    return build_manifest(
        data_path,
        gap_threshold_multiplier=GAP_THRESHOLD_MULTIPLIER,
        tz=SOFIA,
        now=now,
        nominal_interval_sec=NOMINAL_INTERVAL_SEC,
    )


def _gaps(
    timestamps: list[int],
    start: int,
    end: int,
    *,
    in_progress: bool = False,
) -> dict:
    return analyze_gaps(
        timestamps,
        nominal_interval_sec=NOMINAL_INTERVAL_SEC,
        gap_threshold_multiplier=GAP_THRESHOLD_MULTIPLIER,
        window=DayWindow(start, end, in_progress),
    )


def _regular_polls() -> list[int]:
    return list(
        range(
            FIRST_POLL_TS,
            REGULAR_DAY_END,
            NOMINAL_INTERVAL_SEC,
        ),
    )


def _assert_flags(manifest: dict, expected: dict) -> None:
    for key, wanted in expected.items():
        assert manifest[key] is wanted, key


def _day_files(tmp_path: Path, date_str: str) -> tuple[Path, Path, int]:
    data_path = tmp_path / f'{date_str}.jsonl'
    polls_path = tmp_path / f'{date_str}.polls.jsonl'
    return data_path, polls_path, midnight_ts(date_str, SOFIA)


# ─── day_bounds ─────────────────────────────────────────────────────────────


def test_day_bounds_finished_day_spans_a_day():
    """A finished day spans exactly 86400 seconds."""
    day_start_ts, day_end_ts, day_in_progress = day_bounds(
        FIRST_DAY,
        SOFIA,
        FAR_FUTURE_NOW,
    )

    assert day_in_progress is False
    assert day_end_ts - day_start_ts == SECONDS_PER_DAY
    # 2026-08-27 00:00 Europe/Sofia == 2026-08-26 21:00 UTC (UTC+3 in August)
    start = dt.datetime.fromtimestamp(day_start_ts, tz=dt.UTC)
    assert start == FULL_DAY_UTC_START


def test_day_bounds_clamps_in_progress_day_to_now():
    """A running day ends at `now`."""
    # 10:00 UTC on 2026-08-31 is still 2026-08-31 locally (UTC+3) and short
    # of local midnight
    day_start_ts, day_end_ts, day_in_progress = day_bounds(
        '2026-08-31',
        SOFIA,
        MORNING_NOW,
    )

    assert day_in_progress is True
    assert day_end_ts == int(MORNING_NOW.timestamp())
    assert day_start_ts < day_end_ts


# ─── analyze_gaps ───────────────────────────────────────────────────────────


def test_analyze_gaps_no_gaps_at_regular_interval():
    """Polls at the nominal cadence leave no gap and full coverage."""
    # day boundaries chosen so 5 polls at the nominal 45s interval exactly
    # fill the "day" (225s) — coverage_pct is a count-vs-expected ratio now,
    # not derived from the observed span.
    gaps = _gaps(_regular_polls(), FIRST_POLL_TS, REGULAR_DAY_END)

    assert gaps['gap_count'] == 0
    assert gaps['observed_interval_sec'] == NOMINAL_INTERVAL_SEC
    assert gaps['coverage_pct'] == pytest.approx(FULL_COVERAGE_PCT, abs=0)


def test_analyze_gaps_detects_a_downtime_gap():
    """A ~10 minute outage between polls is one gap."""
    # regular 45s polls, then a ~10 minute outage, then resumes
    resumed = FIRST_POLL_TS + 2 * NOMINAL_INTERVAL_SEC
    timestamps = [
        FIRST_POLL_TS,
        SECOND_POLL_TS,
        resumed,
        resumed + OUTAGE_SEC,
        resumed + OUTAGE_SEC + NOMINAL_INTERVAL_SEC,
    ]

    gaps = _gaps(
        timestamps,
        FIRST_POLL_TS,
        timestamps[-1] + NOMINAL_INTERVAL_SEC,
    )

    assert gaps['gap_count'] == 1
    assert gaps['gaps'][0]['gap_seconds'] == OUTAGE_SEC
    assert gaps['coverage_pct'] < FULL_COVERAGE_PCT


def test_analyze_gaps_in_progress_is_lag():
    """The tail of a running day is local lag, not an outage."""
    # Today's file ends where the last rsync landed; the manifest is
    # generated 30 minutes later. That stretch is the age of the local copy,
    # not an outage — 2026-09-01 reported it as a 1954s gap while the VPS
    # journal showed an unbroken 45s cadence.
    timestamps = _regular_polls()

    gaps = _gaps(
        timestamps,
        FIRST_POLL_TS,
        timestamps[-1] + LAG_SEC,
        in_progress=True,
    )

    assert gaps['gap_count'] == 0
    assert gaps['local_lag_sec'] == LAG_SEC
    assert gaps['coverage_pct'] == pytest.approx(FULL_COVERAGE_PCT, abs=0)


def test_analyze_gaps_closed_trailing_gap():
    """On a closed day the silence before midnight stays a gap."""
    # Same shape of input, but the day is over: silence between the last
    # poll and midnight is real downtime and must stay a gap.
    timestamps = _regular_polls()

    gaps = _gaps(timestamps, FIRST_POLL_TS, timestamps[-1] + LAG_SEC)

    assert gaps['gap_count'] == 1
    assert gaps['gaps'][0]['gap_seconds'] == LAG_SEC
    assert gaps['local_lag_sec'] is None


def test_analyze_gaps_empty_shows_zero_coverage():
    """A day with zero polls is 0% covered, not undefined."""
    # Previously this returned coverage_pct=None, which reads as "not
    # applicable" and could hide a genuine full-day outage. A day with zero
    # polls against a real calendar span is 0% covered, not undefined.
    gaps = _gaps([], 0, SECONDS_PER_DAY)

    assert gaps['coverage_pct'] == pytest.approx(0, abs=0)
    assert gaps['gap_count'] == 1
    assert gaps['gaps'][0]['gap_seconds'] == SECONDS_PER_DAY
    assert gaps['observed_interval_sec'] is None


def test_analyze_gaps_single_point_gets_a_number():
    """One poll still yields a real coverage number."""
    gaps = _gaps([SECONDS_PER_DAY // 2], 0, SECONDS_PER_DAY)

    expected_polls = SECONDS_PER_DAY / NOMINAL_INTERVAL_SEC
    assert gaps['coverage_pct'] == round(FULL_COVERAGE_PCT / expected_polls, 2)
    # day_start->point and point->day_end both exceed threshold
    assert gaps['gap_count'] == 2
    # can't compute a delta from one point
    assert gaps['observed_interval_sec'] is None


def test_analyze_gaps_halved_polling_rate():
    """A halved polling rate shows ~50% coverage, not ~100%.

    The regression this task exists to fix: a collector that quietly halves
    its polling rate (45s nominal -> 90s actual) for a full day. The old
    formula derived "nominal_interval" as the median of observed deltas, so
    it would recompute its baseline as 90s, find zero gaps exceeding 3x that,
    and report ~100% coverage. Measured against the *configured* interval,
    the same day is correctly ~50% covered.
    """
    day_start_ts, day_end_ts = 0, SECONDS_PER_DAY
    timestamps = list(range(day_start_ts, day_end_ts, DEGRADED_INTERVAL_SEC))

    gaps = _gaps(timestamps, day_start_ts, day_end_ts)

    assert gaps['observed_interval_sec'] == DEGRADED_INTERVAL_SEC
    assert gaps['coverage_pct'] == pytest.approx(HALF_COVERAGE_PCT, abs=0)
    # no single gap is >3x the *observed* 90s rate — every poll is uniformly
    # spaced — which is exactly why the old gap-count-based signal missed this
    assert gaps['gap_count'] == 0


# ─── build_manifest ─────────────────────────────────────────────────────────


def test_build_manifest_empty_vs_error(tmp_path: Path):
    """Empty and failed polls are counted apart."""
    data_path, polls_path, start = _day_files(tmp_path, FIRST_DAY)
    write_jsonl(
        data_path,
        [
            {'snapshot_ts': start, 'vehicle_id': 'A1'},
            {'snapshot_ts': start, 'vehicle_id': 'A2'},
        ],
    )
    write_jsonl(
        polls_path,
        [
            {
                'snapshot_ts': start,
                'fetch_ok': True,
                'vehicle_count': 2,
                'dropped_out_of_bbox': 1,
            },
            {
                'snapshot_ts': start + NOMINAL_INTERVAL_SEC,
                'fetch_ok': True,
                'vehicle_count': 0,
            },  # genuinely empty, not downtime
            {
                'snapshot_ts': start + 2 * NOMINAL_INTERVAL_SEC,
                'fetch_ok': False,
                'vehicle_count': 0,
            },  # transient fetch error
        ],
    )

    # Clamp "now" to just after the last poll so the trailing edge of the
    # (otherwise mostly-empty) day doesn't register as a downtime gap — this
    # test is about telling empty/error polls apart, not full-day coverage.
    manifest = _build(data_path, _now_after(start, 100))

    _assert_flags(
        manifest,
        {'heartbeat_available': True, 'day_in_progress': True},
    )
    assert {
        key: manifest[key]
        for key in (
            'polls_logged',
            'successful_polls',
            'empty_polls',
            'fetch_error_polls',
            'total_vehicle_records',
            'gap_count',
        )
    } == {
        'polls_logged': 3,
        'successful_polls': 2,
        'empty_polls': 1,
        'fetch_error_polls': 1,
        'total_vehicle_records': 2,
        # all three heartbeats are on schedule, no real downtime
        'gap_count': 0,
    }
    # sums across polls, defaulting to 0 for older heartbeat lines without
    # the field
    assert manifest['dropped_out_of_bbox'] == 1


def test_build_manifest_heartbeat_mid_day(tmp_path: Path):
    """Heartbeat logging that starts mid-day is flagged as partial."""
    # data file has records from before heartbeat logging was ever deployed,
    # plus records once collect.py restarted with heartbeat support
    data_path, polls_path, start = _day_files(tmp_path, '2026-08-28')
    restart = start + RESTART_OFFSET_SEC
    write_jsonl(
        data_path,
        [
            {
                'snapshot_ts': start,
                'vehicle_id': 'A1',
            },  # midnight, no heartbeat yet
            {'snapshot_ts': start + NOMINAL_INTERVAL_SEC, 'vehicle_id': 'A1'},
            {
                'snapshot_ts': restart,
                'vehicle_id': 'A1',
            },  # after the mid-day restart
        ],
    )
    write_jsonl(
        polls_path,
        [
            {'snapshot_ts': restart, 'fetch_ok': True, 'vehicle_count': 1},
            {
                'snapshot_ts': restart + NOMINAL_INTERVAL_SEC,
                'fetch_ok': True,
                'vehicle_count': 1,
            },
        ],
    )

    # well after this day ended
    manifest = _build(data_path, _now_after(start, LONG_AFTER_SEC))

    _assert_flags(
        manifest,
        {
            'heartbeat_available': True,
            'heartbeat_partial': True,
            'day_in_progress': False,
        },
    )
    assert 'pre_heartbeat_note' in manifest

    # the jump from ts=start+45 to ts=start+10000 must still show up as a
    # gap — it must NOT be silently dropped just because heartbeat started
    # after it
    assert [
        gap['gap_seconds'] == RESTART_OFFSET_SEC - NOMINAL_INTERVAL_SEC
        for gap in manifest['gaps']
    ].count(True) == 1

    # NOTE (test expectation changed from the pre-rewrite version): collection
    # also never resumed after start+10045 for the rest of the day. The old
    # span-based formula only ever looked between the first and last record,
    # so this trailing silence was invisible and gap_count was 1. Measured
    # against the real calendar day, that trailing silence all the way to
    # local midnight is a second, equally real gap — gap_count is now 2.
    assert manifest['gap_count'] == 2
    assert manifest['coverage_pct'] < 10  # 5 polls total against a full day


def test_build_manifest_no_heartbeat(tmp_path: Path):
    """Without a polls file the manifest falls back to the data file."""
    data_path = tmp_path / '2026-08-20.jsonl'
    write_jsonl(
        data_path,
        [
            {'snapshot_ts': FIRST_POLL_TS, 'vehicle_id': 'A1'},
            {'snapshot_ts': SECOND_POLL_TS, 'vehicle_id': 'A1'},
        ],
    )

    manifest = _build(data_path)

    assert manifest['heartbeat_available'] is False
    assert manifest['polls_logged'] is None
    assert manifest['total_vehicle_records'] == 2
    assert 'data_sha256' in manifest


def test_build_manifest_late_start_coverage(tmp_path: Path):
    """A late start reports calendar-day coverage, not span-based.

    The exact real-world incident this task exists to fix:
    data/sofia/2026-08-27 (VPS migration meant collection started at 11:07
    local Sofia time). The old span-based formula (first-record to
    last-record) reported 97.78% coverage, hiding that the entire morning
    peak was missing. True calendar-day coverage was 53.7% — see CLAUDE.md
    section 9.
    """
    data_path, _polls_path, start = _day_files(tmp_path, FIRST_DAY)
    day_end = start + SECONDS_PER_DAY
    write_jsonl(
        data_path,
        [
            {'snapshot_ts': ts, 'vehicle_id': 'A1'}
            for ts in range(
                start + LATE_START_OFFSET_SEC,
                day_end,
                NOMINAL_INTERVAL_SEC,
            )
        ],
    )

    # well after this day ended
    manifest = _build(data_path, _now_after(day_end, LONG_DAY_AFTER_SEC))

    _assert_flags(
        manifest,
        {'heartbeat_available': False, 'day_in_progress': False},
    )
    # ~53.7% in the real incident, not ~97.78%
    assert (
        LATE_START_COVERAGE_MIN
        <= manifest['coverage_pct']
        <= LATE_START_COVERAGE_MAX
    )

    # the missing morning must show up as an edge gap from local midnight to
    # the first record — invisible under the old span-based math
    assert manifest['gaps'][0]['after_ts'] == start
    assert manifest['gaps'][0]['gap_seconds'] > MORNING_GAP_MIN_SEC


def test_build_manifest_reports_malformed_lines(tmp_path: Path):
    """Torn lines are counted, not fatal."""
    data_path, polls_path, _start = _day_files(tmp_path, '2026-08-29')
    data_path.write_text(
        '{"snapshot_ts": 1000, "vehicle_id": "A1"}\nnot valid json at all',
        encoding='utf-8',
    )
    write_jsonl(
        polls_path,
        [
            {
                'snapshot_ts': FIRST_POLL_TS,
                'fetch_ok': True,
                'vehicle_count': 1,
                'entities_total': 1,
                'vehicles_with_position': 1,
                'dropped_out_of_bbox': 0,
            },
        ],
    )

    manifest = _build(data_path)  # must not raise

    assert manifest['data_malformed_lines'] == 1
    assert manifest['polls_malformed_lines'] == 0
    assert manifest['total_vehicle_records'] == 1
    # the checksum still gets computed over exactly what's on disk, torn
    # line included
    assert 'data_sha256' in manifest


def test_build_manifest_aggregates_bbox_counts(tmp_path: Path):
    """The bbox pipeline counters are summed over the polls."""
    data_path, polls_path, _start = _day_files(tmp_path, '2026-08-30')
    write_jsonl(data_path, [{'snapshot_ts': 1000, 'vehicle_id': 'A1'}])
    write_jsonl(
        polls_path,
        [
            {
                'snapshot_ts': FIRST_POLL_TS,
                'fetch_ok': True,
                'vehicle_count': 1,
                'entities_total': 5,
                'vehicles_with_position': 4,
                'dropped_out_of_bbox': 3,
            },
        ],
    )

    manifest = _build(data_path)

    assert manifest['entities_total'] == 5
    assert manifest['vehicles_with_position'] == 4
    assert manifest['dropped_out_of_bbox'] == 3
    # 3 of 4 positioned vehicles dropped
    assert manifest['dropped_out_of_bbox_pct'] == BBOX_DROPPED_PCT


def test_load_jsonl_skips_bad_trailing_line(tmp_path: Path):
    """A torn last line is skipped and counted."""
    # Simulates rsync pulling a data file mid-write: the last line is torn.
    path = tmp_path / 'torn.jsonl'
    path.write_text(
        '{"snapshot_ts": 1000, "vehicle_id": "A1"}\n'
        '{"snapshot_ts": 1045, "vehic',
        encoding='utf-8',
    )

    records, malformed = gm.load_jsonl(path)

    assert records == [{'snapshot_ts': FIRST_POLL_TS, 'vehicle_id': 'A1'}]
    assert malformed == 1


# ─── gzip transparency (deploy/sofia-compress.service on the VPS) ──────────


def gzip_copy(src: Path, dst: Path) -> None:
    """Write a gzipped copy of src at dst."""
    with gzip.open(dst, 'wb') as out:
        out.write(src.read_bytes())


def _write_twin_days(tmp_path: Path) -> tuple[Path, Path]:
    """Write one day plain and gzipped; return the plain and gz data paths."""
    plain_dir, gz_dir = tmp_path / 'plain', tmp_path / 'gz'
    plain_dir.mkdir()
    gz_dir.mkdir()
    data_path, polls_path, start = _day_files(plain_dir, '2026-08-29')
    write_jsonl(
        data_path,
        [
            {'snapshot_ts': start, 'vehicle_id': 'A1'},
            {'snapshot_ts': start + NOMINAL_INTERVAL_SEC, 'vehicle_id': 'A1'},
        ],
    )
    write_jsonl(
        polls_path,
        [
            {'snapshot_ts': start, 'fetch_ok': True, 'vehicle_count': 1},
            {
                'snapshot_ts': start + NOMINAL_INTERVAL_SEC,
                'fetch_ok': True,
                'vehicle_count': 1,
            },
        ],
    )
    gzip_copy(data_path, gz_dir / f'{data_path.name}.gz')
    gzip_copy(polls_path, gz_dir / f'{polls_path.name}.gz')
    return data_path, gz_dir / f'{data_path.name}.gz'


def _without_ignored(manifest: dict) -> dict:
    return {
        key: entry
        for key, entry in manifest.items()
        if key not in IGNORED_TWIN_KEYS
    }


def test_gzipped_day_matches_plain_twin(tmp_path: Path):
    """A manifest describes the same bytes plain or gzipped.

    The provenance invariant this whole feature exists to hold: a manifest
    must describe the same uncompressed bytes whether the local copy is
    plain or gzipped. Two directories, same content, one plain and one
    gzipped after the fact — the resulting manifests must be identical
    except for the one field that's allowed (required, even) to differ.
    """
    plain_path, gz_path = _write_twin_days(tmp_path)
    now = _now_after(midnight_ts('2026-08-29', SOFIA), LONG_AFTER_SEC)

    plain_manifest = _build(plain_path, now)
    gz_manifest = _build(gz_path, now)

    assert plain_manifest['compressed'] is False
    assert gz_manifest['compressed'] is True
    # data_sha256/data_size_bytes in particular must be over the UNCOMPRESSED
    # bytes — this is where a naive .stat().st_size on the .gz would show up.
    assert (gz_manifest['data_sha256'], gz_manifest['data_size_bytes']) == (
        plain_manifest['data_sha256'],
        plain_manifest['data_size_bytes'],
    )
    assert (
        gz_manifest['data_file']
        == plain_manifest['data_file']
        == '2026-08-29.jsonl'
    )
    assert _without_ignored(gz_manifest) == _without_ignored(plain_manifest)


def test_gzipped_heartbeat_is_found_and_parsed(tmp_path: Path):
    """A gzipped polls file is found and parsed like a plain one."""
    data_path, _polls_path, start = _day_files(tmp_path, '2026-08-30')
    write_jsonl(data_path, [{'snapshot_ts': start, 'vehicle_id': 'A1'}])
    polls_path_gz = tmp_path / '2026-08-30.polls.jsonl.gz'
    polls_path_gz.write_bytes(gzip.compress(_poll_line(start)))

    manifest = _build(data_path, _now_after(start, 100))

    assert manifest['heartbeat_available'] is True
    assert manifest['polls_logged'] == 1
    # always the uncompressed name
    assert manifest['polls_file'] == '2026-08-30.polls.jsonl'
    assert manifest['polls_sha256'] is not None


def test_find_day_files_prefers_plain_and_dedupes(tmp_path: Path):
    """Plain files win over gz twins and each date appears once."""
    (tmp_path / '2026-08-27.jsonl').write_text('{}\n', encoding='utf-8')
    # both exist: plain wins
    (tmp_path / '2026-08-27.jsonl.gz').write_bytes(gzip.compress(b'{}\n'))
    # gz-only day
    (tmp_path / '2026-08-28.jsonl.gz').write_bytes(gzip.compress(b'{}\n'))

    found = [path.name for path in gm.find_day_files(tmp_path)]

    assert found == ['2026-08-27.jsonl', '2026-08-28.jsonl.gz']


# ─── manifest_is_current / should_skip (fetch-pipeline wiring) ─────────────
# scripts/fetch_data.sh calls generate_manifest.py after every rsync, on a
# cadence as tight as every couple of hours. A closed past day's data and
# heartbeat files never change again, so re-hashing a 100+ MB archive that
# hasn't moved is pure waste this logic exists to skip.


class DayTrio(NamedTuple):
    """Paths of a day's data, heartbeat and manifest files."""

    data_path: Path
    polls_path: Path
    manifest_path: Path


def _trio(tmp_path: Path, date_str: str) -> DayTrio:
    return DayTrio(
        tmp_path / f'{date_str}.jsonl',
        tmp_path / f'{date_str}.polls.jsonl',
        tmp_path / f'{date_str}.manifest.json',
    )


def _trio_with_data(tmp_path: Path, date_str: str) -> DayTrio:
    trio = _trio(tmp_path, date_str)
    touch(trio.data_path, MTIME_BASE)
    return trio


def _assert_current(trio: DayTrio) -> None:
    assert _is_current(trio) is True


def _assert_stale(trio: DayTrio) -> None:
    assert _is_current(trio) is False


def _is_current(trio: DayTrio) -> bool:
    return gm.manifest_is_current(
        trio.manifest_path,
        trio.data_path,
        trio.polls_path,
    )


def _skips(trio: DayTrio, *, force: bool) -> bool:
    return gm.should_skip(
        trio.manifest_path,
        trio.data_path,
        trio.polls_path,
        force=force,
    )


def touch(path: Path, mtime: float) -> None:
    """Create path with placeholder text and set its mtime."""
    path.write_text('x', encoding='utf-8')
    os.utime(path, (mtime, mtime))


def touch_manifest(
    path: Path,
    mtime: float,
    version=MANIFEST_SCHEMA_VERSION,
    **extra,
) -> None:
    """Write a real manifest JSON carrying a schema_version, with an mtime.

    mtime is not the only thing gm.manifest_is_current() reads any more. Pass a
    different `version` to stand in for one written by an older format.
    """
    stamp = {} if version is None else {'schema_version': version}
    payload = stamp | extra
    path.write_text(json.dumps(payload), encoding='utf-8')
    os.utime(path, (mtime, mtime))


def test_manifest_current_when_newer(tmp_path: Path):
    """A manifest newer than data and polls is current."""
    trio = _trio_with_data(tmp_path, FIRST_DAY)
    touch(trio.polls_path, MTIME_POLLS)
    # generated after both inputs last changed
    touch_manifest(trio.manifest_path, MTIME_MANIFEST)

    _assert_current(trio)


def test_manifest_stale_data_newer(tmp_path: Path):
    """A data file newer than the manifest makes it stale."""
    # The stale case this whole thing exists for: the data file grew (still
    # being collected, or a fresh rsync pull) after the last manifest run.
    trio = _trio(tmp_path, '2026-08-31')
    touch_manifest(trio.manifest_path, MTIME_BASE)
    touch(trio.polls_path, MTIME_EARLIEST)
    touch(trio.data_path, MTIME_NEWER)  # data appended after the manifest

    _assert_stale(trio)


def test_manifest_stale_polls_newer(tmp_path: Path):
    """A polls file newer than the manifest makes it stale."""
    trio = _trio(tmp_path, '2026-08-31')
    touch(trio.data_path, MTIME_EARLIEST)
    touch_manifest(trio.manifest_path, MTIME_BASE)
    # heartbeat log grew after the manifest was written
    touch(trio.polls_path, MTIME_NEWER)

    _assert_stale(trio)


def test_manifest_stale_remote_failed(tmp_path: Path):
    """A recorded remote-check failure overrides the mtime order."""
    # Finding 19: rsync -a restores the server's (older) mtime on the
    # repaired file, so the manifest looks newer than its inputs. A recorded
    # failure must override that.
    trio = _trio_with_data(tmp_path, '2026-09-23')
    touch(trio.polls_path, MTIME_POLLS)
    touch_manifest(trio.manifest_path, MTIME_MANIFEST, remote_verified=False)

    _assert_stale(trio)


def test_manifest_current_remote_passed(tmp_path: Path):
    """A passed remote check leaves the manifest current."""
    trio = _trio_with_data(tmp_path, '2026-09-23')
    touch(trio.polls_path, MTIME_POLLS)
    touch_manifest(trio.manifest_path, MTIME_MANIFEST, remote_verified=True)

    _assert_current(trio)


def test_manifest_is_stale_when_manifest_missing(tmp_path: Path):
    """A day without any manifest is stale."""
    trio = _trio_with_data(tmp_path, '2026-08-31')  # manifest never written
    touch(trio.polls_path, MTIME_BASE)

    _assert_stale(trio)


def test_manifest_current_no_polls(tmp_path: Path):
    """A missing polls file does not force a recompute."""
    # Pre-heartbeat days (or days without a heartbeat log at all) have no
    # .polls.jsonl — its absence must not force a permanent recompute.
    trio = _trio_with_data(
        tmp_path,
        '2026-08-20',
    )  # polls deliberately not created
    touch_manifest(trio.manifest_path, MTIME_MANIFEST)

    _assert_current(trio)


def test_manifest_stale_older_schema(tmp_path: Path):
    """An older stored schema_version makes the manifest stale."""
    # The defect the schema version exists for: gzip preserves the day file's
    # original mtime, so a day compressed on the VPS looks untouched forever
    # and every field added to the manifest format after it closed misses it.
    # Here the mtimes say "current" and only the stored version disagrees.
    trio = _trio_with_data(tmp_path, FIRST_DAY)
    touch(trio.polls_path, MTIME_BASE)
    touch_manifest(
        trio.manifest_path,
        MTIME_MANIFEST,
        version=MANIFEST_SCHEMA_VERSION - 1,
    )

    _assert_stale(trio)
    assert _skips(trio, force=False) is False


def test_manifest_stale_unversioned(tmp_path: Path):
    """A manifest with no version field is rewritten once."""
    # Every manifest written before MANIFEST_SCHEMA_VERSION existed: no
    # version field at all, so it can never match and is rewritten once.
    trio = _trio_with_data(tmp_path, '2026-08-28')
    touch(trio.polls_path, MTIME_BASE)
    touch_manifest(trio.manifest_path, MTIME_MANIFEST, version=None)

    _assert_stale(trio)


def test_manifest_is_stale_when_it_is_unreadable(tmp_path: Path):
    """A manifest that does not parse is stale."""
    # A manifest that won't parse can't state its version either. Treating it
    # as current would leave the corruption on disk indefinitely.
    trio = _trio_with_data(tmp_path, '2026-08-29')
    touch(trio.polls_path, MTIME_BASE)
    touch(trio.manifest_path, MTIME_MANIFEST)  # writes "x", not JSON

    _assert_stale(trio)


def test_should_skip_true_for_current_manifest(tmp_path: Path):
    """A current manifest is skipped without --force."""
    trio = _trio_with_data(tmp_path, FIRST_DAY)
    touch(trio.polls_path, MTIME_BASE)
    touch_manifest(trio.manifest_path, MTIME_MANIFEST)

    assert _skips(trio, force=False) is True


def test_should_skip_false_for_a_stale_manifest(tmp_path: Path):
    """A stale manifest is never skipped."""
    trio = _trio(tmp_path, '2026-08-31')
    touch_manifest(trio.manifest_path, MTIME_BASE)
    touch(trio.polls_path, MTIME_BASE)
    touch(trio.data_path, MTIME_NEWER)

    assert _skips(trio, force=False) is False


# ─── carry_remote_verification (survives a schema-driven rewrite) ──────────
# A version bump rewrites closed days, and verify_remote_checksums.py's
# fields live only in the file being replaced. Losing them would silently
# discard a recorded VPS comparison and make it look never done.


def test_carry_verification_keeps_on_match():
    """A passed check is carried over when the hashes are unchanged."""
    previous = {
        'data_sha256': 'aa',
        'polls_sha256': 'bb',
        'remote_verified': True,
        'remote_verified_at': '2026-08-31T11:06:33+00:00',
    }
    fresh = {'data_sha256': 'aa', 'polls_sha256': 'bb'}

    carried = carry_remote_verification(fresh, previous)

    assert carried['remote_verified'] is True
    assert carried['remote_verified_at'] == '2026-08-31T11:06:33+00:00'


def test_carry_verification_drops_failure():
    """A recorded failure is dropped even when the hashes match."""
    # Same bytes, still false: carrying it would leave verify (which skips any
    # manifest holding a value) never to re-read the VPS for this day.
    previous = {
        'data_sha256': 'aa',
        'polls_sha256': 'bb',
        'remote_verified': False,
        'remote_verified_at': '2026-09-24T08:00:00+00:00',
        'remote_verify_note': 'data_sha256 local=aa remote=zz',
    }
    fresh = {'data_sha256': 'aa', 'polls_sha256': 'bb'}

    assert carry_remote_verification(fresh, previous) == {
        'data_sha256': 'aa',
        'polls_sha256': 'bb',
    }


def test_carry_verification_drops_moved():
    """A record about other bytes is dropped when a hash changed."""
    # "matches the VPS" is a claim about specific bytes. If the hash moved,
    # the claim is about bytes this manifest no longer describes, so drop it
    # and let the next verify run answer again for the new content.
    previous = {
        'data_sha256': 'aa',
        'polls_sha256': 'bb',
        'remote_verified': True,
    }
    fresh = {'data_sha256': 'cc', 'polls_sha256': 'bb'}

    assert 'remote_verified' not in carry_remote_verification(fresh, previous)


def test_carry_verification_no_previous():
    """With no previous manifest the fresh one is returned as is."""
    fresh = {'data_sha256': 'aa', 'polls_sha256': None}

    assert carry_remote_verification(fresh, None) == fresh


def test_should_skip_false_when_forced(tmp_path: Path):
    """--force rebuilds even a current manifest."""
    trio = _trio_with_data(tmp_path, FIRST_DAY)
    touch(trio.polls_path, MTIME_BASE)
    # would be skipped without --force
    touch_manifest(trio.manifest_path, MTIME_MANIFEST)

    assert _skips(trio, force=True) is False
