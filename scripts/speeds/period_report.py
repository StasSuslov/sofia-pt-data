"""Describing each schedule period once days have been assigned to one."""

from operator import itemgetter

from speeds.periods import COUNTS, DATE, PERIOD_KEY, trips

FIRST_DATE = 'first_date'


def _new_entry(day: dict, key: str, bin_counts: dict) -> dict:
    return {
        PERIOD_KEY: key,
        'reference_date': day.get('period_reference_date', day[DATE]),
        'days_in_median_mon_fri': [],
        FIRST_DATE: day[DATE],
        'last_date': day[DATE],
        'route_count': len(day[COUNTS]),
        'trip_count': trips(day),
        'max_churn_vs_reference': 0,
        'bin_count': bin_counts.get(key, 0),
    }


def _absorb(entry: dict, day: dict) -> None:
    entry['days_in_median_mon_fri'].append(day[DATE])
    entry[FIRST_DATE] = min(entry[FIRST_DATE], day[DATE])
    entry['last_date'] = max(entry['last_date'], day[DATE])
    entry['max_churn_vs_reference'] = max(
        entry['max_churn_vs_reference'],
        day.get('churn_vs_reference', 0),
    )


def build_schedule_periods(days: list, bin_counts: dict) -> list:
    """One entry per schedule period over days already assigned a period.

    One entry per period over days already run through
    assign_schedule_periods(), with `bin_counts` as period_key -> number of
    (segment, timeslot) bins in that period's median.

    Only days that entered the median carry a period_key, so only they
    appear here; a weekend or a reduced-service day names its reason in
    day_breakdown instead. reference_date is the day whose signature the
    period is named after, which a run processing part of the archive can
    have assigned against without processing (see main()); route_count and
    trip_count then describe the earliest day of the period this run did
    process, which the tolerance holds within PERIOD_TOLERANCE_PCT of it.
    max_churn_vs_reference says how far the loosest day in the period sat
    from the reference, so a reader can see how tight the grouping actually
    was rather than trusting the threshold.

    first_date and last_date bound the period, they do not fill it: a
    timetable that returns after a variant rejoins its old period, so weekdays
    between the two dates can sit in another one (14 September to 8 October
    2026, with 5 to 7 October elsewhere). days_in_median_mon_fri is the list.
    """
    by_key = {}
    for day in days:
        key = day.get(PERIOD_KEY)
        if key is None:
            continue
        if key not in by_key:
            by_key[key] = _new_entry(day, key, bin_counts)
        _absorb(by_key[key], day)
    return sorted(
        by_key.values(),
        key=itemgetter(FIRST_DATE, PERIOD_KEY),
    )
