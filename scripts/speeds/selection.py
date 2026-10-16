"""Which day files a run processes, and which it plans around."""

import datetime as dt
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from config import date_from_path, find_day_files, resolve_day_file


def _today(tz: ZoneInfo) -> str:
    return dt.datetime.now(tz).date().isoformat()


def _closed_days(data_dir: Path, tz: ZoneInfo) -> list:
    # <date>.jsonl or <date>.jsonl.gz, deduplicated by date, sorted
    day_files = find_day_files(data_dir)
    # Today's file is still being written and its local copy only reaches
    # the last rsync, so folding it into the median produces an aggregate
    # that changes under a reader who re-runs this an hour later. The
    # first typical_weekday.json was built this way, over 551,016 of the
    # 730,167 records its own day eventually held. Name a date explicitly
    # to process the day in progress anyway.
    today = _today(tz)
    in_progress = filter(
        lambda day_file: date_from_path(day_file) >= today,
        day_files,
    )
    for skipped in in_progress:
        sys.stderr.write(
            f'{skipped.name}: skipped, the day is still in progress '
            f'(pass {date_from_path(skipped)} explicitly to include it)\n',
        )
    return [
        day_file for day_file in day_files if date_from_path(day_file) < today
    ]


def select_day_files(data_dir: Path, dates: list, tz: ZoneInfo) -> list:
    """Day files to process; exits 1 when there are none."""
    if dates:
        # resolve_day_file() picks whichever of <date>.jsonl / <date>.jsonl.gz
        # actually exists (uncompressed preferred), same as find_day_files()
        # below does for the no-explicit-dates case. Sorted here too, to
        # match find_day_files()'s ordering -- a day_breakdown or a snapshot
        # cache shouldn't depend on the order dates were typed on the CLI.
        day_files = [
            resolve_day_file(data_dir, date_str, '.jsonl')
            for date_str in sorted(dates)
        ]
    else:
        day_files = _closed_days(data_dir, tz)
    if not day_files:
        sys.stderr.write(f'No day files found in {data_dir}\n')
        sys.exit(1)
    return day_files


def requested_dates(day_files: list) -> set:
    """Dates of the days this run was asked to process."""
    return {date_from_path(day_file) for day_file in day_files}


def plan_files(day_files: list, data_dir: Path, tz: ZoneInfo) -> list:
    """Every closed day on disk plus the days named explicitly.

    Which timetable a day ran is a fact about the archive, not about this
    invocation. The period key names a directory in the web export and
    travels into published JSON, so it has to come out the same however the
    run was called: the plan below spans every closed day on disk plus
    anything named explicitly, and the loop after it processes only the days
    actually requested. Without this, `segment_speeds.py static data
    2026-09-01` makes 09-01 the reference of its own period and hands the
    same timetable a different key than the full run does.
    """
    requested = requested_dates(day_files)
    today = _today(tz)
    every_day = sorted(
        set(day_files) | set(find_day_files(data_dir)),
        key=date_from_path,
    )
    return [
        day_file
        for day_file in every_day
        if date_from_path(day_file) < today
        or date_from_path(day_file) in requested
    ]
