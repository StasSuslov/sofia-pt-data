"""The two indexes a client fetches first: periods, and everything on disk."""

import datetime as dt
import sys
from pathlib import Path

from webexport.constants import FORMAT_VERSION, MANIFEST_NAME, TYPICAL_MODE
from webexport.fileio import read_json, write_pretty

_DAY_GLOB = '????-??-??'


def _rank(period: dict) -> tuple | None:
    days = period.get('days_in_median_mon_fri', [])
    if not days:
        return None
    return (len(days), max(days))


def current_period_key(schedule_periods: list) -> str | None:
    """Return the key of the period a client should open by default.

    That is the period whose median rests on the most weekdays.

    Not the newest, which is what this returned until 2026-09-09. A period
    starts on the day the timetable changes, so the newest one routinely
    holds a single weekday, and a median over one day is not the aggregate
    D4 asks for: it is that day. Measured when 2026-09-08 opened a period,
    the newest held 1 weekday and 11,649 segments while the period before it
    held 5 and 27,138, and the segments passing the slowdown gate fell from
    13,509 to 3,165. A reader opening the map would have got the thinner
    number under the same words.

    Ties go to the later period, so a new timetable takes over as soon as it
    is observed as widely as the old one rather than needing to beat it.
    Weekday days only: a weekend runs its own schedule and never enters the
    median (D4), so the newest weekend day says nothing about which weekday
    median is current.

    Args:
        schedule_periods: The periods of typical_weekday.json.

    Returns:
        The period_key of the winner, or None when no period has a weekday.
    """
    best = None
    for period in schedule_periods:
        rank = _rank(period)
        if rank is not None and (best is None or rank > best[0]):
            best = (rank, period['period_key'])
    return best[1] if best else None


def build_period_index(
    period_entries: list,
    current: str | None,
    days_excluded: list,
) -> dict:
    """Build the index of the schedule periods.

    It is the index a client fetches first: which timetables this archive has
    medians for, which one is current, and where each bundle lives. Without
    it a client would have to guess directory names built from a hash.

    days_excluded carries the archive days that entered no median at all,
    each with its reason (weekend, reduced_service, no_calendar_rows). A
    period bundle can only list its own days, so without this the web tree
    would say which days were averaged and never which ones were dropped —
    and a dropped public holiday is exactly the kind of omission a reader
    should be able to see rather than infer from a gap in the dates.

    Args:
        period_entries: One entry per period bundle.
        current: The key of the default period.
        days_excluded: Archive days that entered no median, with reasons.

    Returns:
        The index as a dict.
    """
    return {
        'format_version': FORMAT_VERSION,
        'generated_at': dt.datetime.now(dt.UTC).isoformat(),
        'mode': 'typical_weekday_index',
        'current_period': current,
        'period_count': len(period_entries),
        'periods': period_entries,
        'days_excluded_from_median': days_excluded,
    }


def _day_entries(output_root: Path) -> list:
    return [
        {'date': day.name, 'path': day.name}
        for day in sorted(output_root.glob(_DAY_GLOB))
        if (day / MANIFEST_NAME).exists()
    ]


def _typical_entry(output_root: Path) -> dict | None:
    typical_index = output_root / TYPICAL_MODE / MANIFEST_NAME
    if not typical_index.exists():
        return None
    return {
        'path': TYPICAL_MODE,
        'current_period': read_json(typical_index)['current_period'],
    }


def write_root_index(output_root: Path) -> dict:
    """Write web/index.json: which day bundles exist, and the typical tree.

    A static site (D7) cannot list a directory, so without this the
    day switcher (Component D feature 2) has nothing to enumerate.

    Built by *scanning output_root*, never from the days this run was asked
    to export — the same rule the schedule-period split already follows
    ("plan over the whole archive, process the requested subset"). An
    `--day 2026-09-03` run must not shrink the index to one day.

    Minimum fields on purpose: everything else a client could want about a
    day is already in that day's own manifest.json, one fetch away, and a
    second copy here would be a second thing to keep true.

    Args:
        output_root: The web tree.

    Returns:
        The index as written.
    """
    days = _day_entries(output_root)
    typical = _typical_entry(output_root)
    index = {
        'format_version': FORMAT_VERSION,
        'generated_at': dt.datetime.now(dt.UTC).isoformat(),
        'days': days,
        'typical_weekday': typical,
    }
    output_root.mkdir(parents=True, exist_ok=True)
    index_path = output_root / 'index.json'
    write_pretty(index_path, index)
    state = 'present' if typical else 'absent'
    sys.stdout.write(
        f'\n{index_path}: {len(days)} day bundle(s), typical_weekday {state}\n',
    )
    return index
