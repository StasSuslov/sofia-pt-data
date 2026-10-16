"""Grouping weekdays into schedule periods, one median per timetable."""

import statistics

from speeds.constants import PERIOD_TOLERANCE_PCT, REDUCED_SERVICE_PCT
from speeds.timetable import schedule_churn

COUNTS = 'counts'
DATE = 'date'
PERIOD_KEY = 'period_key'
EXCLUDED = 'excluded_from_median'


def trips(day: dict) -> int:
    """Return the trips a day's timetable schedules."""
    return sum(day[COUNTS].values())


def _baseline_level(days: list):
    """Median trip count over the weekdays that scheduled any."""
    # Zero-trip weekdays are holes in the archive, not the city running
    # fewer buses, and they are kept out of the baseline: enough of them and
    # the median reaches zero, at which point `level` goes falsy and the
    # reduced-service test below silently stops firing for every day. A gap
    # in the static snapshots is exactly the failure this archive has already
    # lived through once (2026-08-28/29/30), so it must not disarm the rule.
    totals = sorted(
        filter(None, (trips(day) for day in days if day['is_weekday'])),
    )
    return statistics.median(totals) if totals else 0


def _exclusion(day: dict, level, reduced_service_pct: float):
    """Return why a day stays out of the median, or None if it enters."""
    total = trips(day)
    if not day['is_weekday']:
        return 'weekend'
    if not total:
        # Not a holiday: the snapshot this day was read against carries
        # no calendar rows for the date at all. Keeping the two apart
        # matters — one is the city running fewer buses, the other is a
        # hole in the archive.
        return 'no_calendar_rows'
    if level and total < level * reduced_service_pct / 100:
        return 'reduced_service'
    return None


def _within(churn: int, ref: dict, tolerance_pct: float) -> bool:
    return churn <= trips(ref) * tolerance_pct / 100


def _rejoin(day: dict, refs: list, churn: int, tolerance_pct: float) -> tuple:
    """Return the (reference, churn) of the closest earlier period."""
    ref = None
    # Opening order, so `<=` hands a tie to the later period.
    for candidate in refs:
        cand_churn = schedule_churn(day[COUNTS], candidate[COUNTS])
        close = _within(cand_churn, candidate, tolerance_pct)
        if close and (ref is None or cand_churn <= churn):
            ref, churn = candidate, cand_churn
    return ref, churn


def _place(day: dict, ref, refs: list, tolerance_pct: float) -> dict:
    """Put a median-bound day into a period; return the current reference."""
    churn = schedule_churn(day[COUNTS], ref[COUNTS]) if ref else 0
    if ref is not None and not _within(churn, ref, tolerance_pct):
        ref, churn = _rejoin(day, refs, churn, tolerance_pct)
    if ref is None:
        ref, churn = day, 0
        refs.append(day)
        day[PERIOD_KEY] = day['signature_key']
    else:
        day[PERIOD_KEY] = ref[PERIOD_KEY]
    day['churn_vs_reference'] = churn
    day['period_reference_date'] = ref[DATE]
    return ref


def assign_schedule_periods(
    days: list,
    tolerance_pct: float = PERIOD_TOLERANCE_PCT,
    reduced_service_pct: float = REDUCED_SERVICE_PCT,
) -> list:
    """Give every day a "period_key" and an "excluded_from_median" reason.

    Give every day in `days` a "period_key" and an "excluded_from_median"
    reason, in place, and return the list. Each day is a dict carrying
    "date", "is_weekday", "signature_key" and "counts"; `days` is in date
    order.

    Weekends never enter the median (D4) and take no period. Of the
    weekdays, one scheduling less than reduced_service_pct of the median
    weekday's trips is dropped as reduced service: a holiday timetable is
    not a typical weekday, and it differs from its neighbours by enough that
    letting it through would found a period holding exactly one day.

    The survivors are walked in date order. The first is its period's
    reference and lends the period its signature_key; each following day
    joins while its churn against that reference stays within tolerance_pct
    of the reference's trips. Measured against the reference rather than the
    previous day, so a slow drift cannot walk a period arbitrarily far from
    the timetable its key names, one tolerated step at a time.

    A day past the tolerance is then held against the reference of every
    earlier period, and rejoins the one it sits closest to in trips (a tie
    goes to the period opened later) if any is within that period's own
    tolerance; that period becomes the current one, so the days after it are
    measured against its reference again. Only a day no earlier timetable
    accounts for starts a new period as the new reference. From 5 to 7
    October 2026 the feed ran a three-day variant and on 8 October returned
    to the signature 29 September had carried, 37 trips (0.22%) from the
    14 September reference. Compared only with the variant, 8 October opened
    a period of its own: one weekday, 12,778 segments exported against
    34,303 for the same timetable, 69.71% of its bins resting on a single
    observation. A period can therefore hold days that are not contiguous,
    and the days in it, not its first and last date, say which ones it is.
    It also keeps two periods from sharing one key: a returning day whose
    signature equals an earlier reference's used to found a period under
    that same key and pool into the old median unannounced.
    """
    level = _baseline_level(days)
    refs = []  # every period's reference day, in the order the periods opened
    ref = None
    for day in days:
        day[EXCLUDED] = _exclusion(day, level, reduced_service_pct)
        if day[EXCLUDED]:
            day[PERIOD_KEY] = None
        else:
            ref = _place(day, ref, refs, tolerance_pct)
    return days
