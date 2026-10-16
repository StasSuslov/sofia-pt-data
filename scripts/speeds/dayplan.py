"""The pre-pass: which timetable each requested or closed day ran."""

import datetime as dt
import sys
from pathlib import Path
from typing import NamedTuple

from config import date_from_path
from speeds.cli import Setup
from speeds.feedcache import FeedCache
from speeds.periods import COUNTS, DATE, EXCLUDED, assign_schedule_periods
from speeds.snapshots import next_snapshot_after, pick_snapshot_for_day
from speeds.timetable import schedule_signature

# Monday to Friday are the first five weekday() values, per D4.
WEEKDAYS = 5


class StaticChoice(NamedTuple):
    """The static feed answering for a day, and the one after it."""

    path: Path
    is_fallback: bool
    next_path: Path | None


def _static_for(setup: Setup, date_str: str) -> StaticChoice:
    if setup.snapshots is None:
        return StaticChoice(
            path=setup.args.static_source,
            is_fallback=False,
            next_path=None,
        )
    _, static_path, is_fallback = pick_snapshot_for_day(
        setup.snapshots,
        date_str,
    )
    if is_fallback:
        sys.stderr.write(
            f'{date_str}: precedes every static snapshot on record, '
            f'falling back to the earliest one ({static_path.name})\n',
        )
    return StaticChoice(
        static_path,
        is_fallback,
        next_snapshot_after(setup.snapshots, static_path),
    )


def _plan_day(
    setup: Setup,
    cache: FeedCache,
    day_path: Path,
    requested: set,
) -> dict:
    date_str = date_from_path(day_path)
    static = _static_for(setup, date_str)
    # The day's own snapshot, never next_path: which timetable the day
    # ran is a fact about the feed it started under.
    signature_key, counts = schedule_signature(
        *cache.load_calendar(static.path),
        date_str,
    )
    return {
        DATE: date_str,
        'day_path': day_path,
        'static_path': static.path,
        'is_fallback': static.is_fallback,
        'next_path': static.next_path,
        'is_weekday': dt.date.fromisoformat(date_str).weekday() < WEEKDAYS,
        'signature_key': signature_key,
        COUNTS: counts,
        'processed': date_str in requested,
    }


def _report_excluded(day_plan: list) -> None:
    for entry in day_plan:
        if entry[EXCLUDED] and entry['is_weekday']:
            total = sum(entry[COUNTS].values())
            sys.stderr.write(
                f'{entry[DATE]}: weekday scheduling {total:,} trips '
                f'({entry[EXCLUDED]}), kept out of the median\n',
            )


def build_day_plan(
    setup: Setup,
    cache: FeedCache,
    files: list,
    requested: set,
) -> list:
    """Plan every existing day and settle its schedule period.

    Which timetable each day ran is settled before any day is processed:
    dropping a reduced-service weekday needs the median trip count over
    every weekday in the archive, so no day can be routed to an aggregate
    until all of them have been read. Cheap — the calendar is cached per
    snapshot and several days usually share one.
    """
    day_plan = []
    for day_path in files:
        if day_path.exists():
            day_plan.append(_plan_day(setup, cache, day_path, requested))
        else:
            sys.stderr.write(f'{day_path.name}: not found, skipping\n')
    assign_schedule_periods(day_plan)
    _report_excluded(day_plan)
    return day_plan
