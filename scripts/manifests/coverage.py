"""Calendar-day boundaries and poll-gap coverage arithmetic."""

import datetime as dt
import statistics
from itertools import pairwise
from typing import NamedTuple
from zoneinfo import ZoneInfo


class DayWindow(NamedTuple):
    """UTC epoch span of one local day and whether it is still running."""

    start_ts: int
    end_ts: int
    in_progress: bool = False


def day_bounds(
    date_str: str,
    tz: ZoneInfo,
    now: dt.datetime | None = None,
) -> DayWindow:
    """Return the UTC epoch window of `date_str` in `tz`.

    The window is the local midnight-to-midnight span, so it compares directly
    against snapshot_ts (UTC epoch).

    For the current in-progress local day, day_end is clamped to `now` and
    in_progress comes back True. Note for callers: a local rsync copy of
    *today's* file also lags the VPS by up to the fetch interval (see
    scripts/fetch_data.sh), so `now` is later than the last poll on disk even
    when the collector never missed one. analyze_gaps() takes the window's
    in_progress for exactly that reason and reports the remainder as
    local_lag_sec.

    Args:
        date_str: Local calendar day, YYYY-MM-DD.
        tz: Timezone whose midnights bound the day.
        now: Clock override for tests; defaults to the current time.

    Returns:
        The day's start, end and in-progress flag.
    """
    local_date = dt.date.fromisoformat(date_str)
    day_start = dt.datetime(
        local_date.year,
        local_date.month,
        local_date.day,
        tzinfo=tz,
    )
    day_end = day_start + dt.timedelta(days=1)
    now = now or dt.datetime.now(dt.UTC)

    day_in_progress = now < day_end
    if day_in_progress:
        day_end = now

    return DayWindow(
        int(day_start.timestamp()),
        int(day_end.timestamp()),
        day_in_progress,
    )


def _trim_to_last_poll(
    timestamps: list[int],
    nominal_interval_sec: int,
    window: DayWindow,
) -> tuple[int, int | None]:
    # On a day still in progress, everything after the last poll we hold is
    # the age of this local copy, not a hole in the collector's coverage:
    # fetch_data.sh pulls on a 2-hour cadence, so today's file always ends
    # where the last rsync landed. Counting that stretch as a gap reported
    # 32 minutes of "downtime" on 2026-09-01 while the VPS journal showed an
    # unbroken 45s cadence and healthchecks.io stayed green. Measure against
    # the last poll and name the remainder local_lag_sec. Closed days keep
    # the calendar boundary, where a trailing gap is real downtime.
    if window.in_progress and timestamps:
        # +interval because the last poll covers its own interval; ending the
        # window on the poll itself leaves N polls in N-1 intervals and reads
        # back as 100.01% coverage.
        return (
            timestamps[-1] + nominal_interval_sec,
            window.end_ts - timestamps[-1],
        )
    return window.end_ts, None


def _observed_interval(timestamps: list[int]) -> float | None:
    # Diagnostic only, not used for coverage math: how far the actually
    # observed spacing between polls has drifted from nominal_interval_sec.
    # float() because statistics.median() returns whichever type the middle
    # of the sample happens to be: an int for an odd count of intervals, a
    # float for an even one, so the same unchanged 45 s cadence serialised
    # as 45 on one day and 45.0 on the next purely from parity, and every
    # manifest diff carried that noise. The honest type is the float: this
    # is a measured median of durations and a genuinely fractional value
    # (45.5) is a normal outcome, not a rounding artifact to be hidden.
    if len(timestamps) < 2:
        return None
    return float(
        statistics.median(
            later - earlier for earlier, later in pairwise(timestamps)
        ),
    )


def _find_gaps(
    timestamps: list[int],
    day_start_ts: int,
    day_end_ts: int,
    threshold: float,
) -> list[dict]:
    # Day boundaries count as poll opportunities too: a gap between
    # day_start and the first record, or the last record and day_end, is
    # exactly as real as one in the middle — and was invisible before. This
    # is precisely how a mid-day collector start hid as "97% coverage".
    bounded = [day_start_ts, *timestamps, day_end_ts]
    return [
        {
            'after_ts': earlier,
            'before_ts': later,
            'gap_seconds': later - earlier,
        }
        for earlier, later in pairwise(bounded)
        if (later - earlier) > threshold
    ]


def _poll_expectation(
    poll_count: int,
    nominal_interval_sec: int,
    day_seconds: int,
) -> dict:
    expected_polls = (
        day_seconds / nominal_interval_sec
        if nominal_interval_sec and day_seconds > 0
        else None
    )
    return {
        'expected_polls': round(expected_polls, 1) if expected_polls else None,
        'coverage_pct': (
            round(100 * poll_count / expected_polls, 2)
            if expected_polls
            else None
        ),
    }


def analyze_gaps(
    timestamps: list[int],
    nominal_interval_sec: int,
    gap_threshold_multiplier: float,
    window: DayWindow,
) -> dict:
    """Measure poll coverage against the calendar day and configured cadence.

    Coverage is measured against calendar-day boundaries and the *configured*
    poll interval, never the interval observed in the data. A collector that
    quietly degrades from 45s to 90s polls would otherwise recalibrate its
    own "normal" from the degraded data and report ~0 gaps / ~100% coverage
    — see CLAUDE.md section 9, the coverage_pct defect this rewrite fixes.

    Args:
        timestamps: Sorted poll snapshot_ts values.
        nominal_interval_sec: The configured poll interval.
        gap_threshold_multiplier: A gap is flagged above this many intervals.
        window: The day's boundaries and in-progress flag.

    Returns:
        The coverage fields merged into the manifest.
    """
    day_end_ts, local_lag_sec = _trim_to_last_poll(
        timestamps,
        nominal_interval_sec,
        window,
    )
    gaps = _find_gaps(
        timestamps,
        window.start_ts,
        day_end_ts,
        nominal_interval_sec * gap_threshold_multiplier,
    )
    day_seconds = day_end_ts - window.start_ts
    return {
        'nominal_interval_sec': nominal_interval_sec,
        'observed_interval_sec': _observed_interval(timestamps),
        'gap_count': len(gaps),
        'gaps': gaps,
        'day_seconds': day_seconds,
        # How stale this local copy is: seconds between its last poll and
        # generation time. None on a closed day. Not a coverage defect —
        # whether the collector is alive is the dead-man's switch's job.
        'local_lag_sec': local_lag_sec,
        **_poll_expectation(len(timestamps), nominal_interval_sec, day_seconds),
    }
