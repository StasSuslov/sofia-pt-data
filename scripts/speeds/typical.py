"""The typical-weekday payload (D4): medians per period, segment, slot."""

import datetime as dt
import statistics
from collections import Counter

from speeds import constants as cfg


def _thresholds() -> dict:
    return {
        'segment_length_m': cfg.SEGMENT_LENGTH_M,
        'timeslot_minutes': cfg.TIMESLOT_MINUTES,
        'max_time_gap_sec': cfg.MAX_TIME_GAP_SEC,
        'min_time_gap_cadence_fraction': cfg.MIN_TIME_GAP_CADENCE_FRACTION,
        'max_speed_kmh': cfg.MAX_SPEED_KMH,
        'backward_tolerance_m': cfg.BACKWARD_TOLERANCE_M,
        'search_margin_m': cfg.SEARCH_MARGIN_M,
        'validation_diff_threshold_kmh': cfg.VALIDATION_DIFF_THRESHOLD_KMH,
        'schedule_period_tolerance_pct': cfg.PERIOD_TOLERANCE_PCT,
        'reduced_service_threshold_pct': cfg.REDUCED_SERVICE_PCT,
    }


def _bin_key(shape_key: str, segment_index: int, slot: str) -> str:
    # Deliberately still a 3-field "<field>|<segment_index>|<timeslot>"
    # string, shape_key occupying the first field — export_web.py does
    # key.split("|") and that call site keeps working untouched.
    # shape_key is 16 hex chars (see build_shape_key) and so contains no
    # "|", which is what keeps this from silently becoming a 4-field key.
    return f'{shape_key}|{segment_index}|{slot}'


def _period_segments(agg: dict) -> dict:
    return {
        _bin_key(*key): {
            'median_speed_ms': round(statistics.median(speeds), 3),
            'n_samples': len(speeds),
        }
        for key, speeds in agg.items()
    }


def _segments(agg_by_period: dict) -> dict:
    # One median set per schedule period (see assign_schedule_periods): the
    # timetable a day ran is part of what a "typical weekday" is, and a bin
    # from before a timetable change has no business being pooled with one
    # from after it. The period key is the outer level rather than a fourth
    # field in the bin key, so a reader (and export_web.py) can take one
    # period's whole payload without filtering the other periods out of it.
    return {
        period_key: _period_segments(agg)
        for period_key, agg in agg_by_period.items()
    }


def _excluded_days(day_breakdown: list) -> list:
    return [
        {'date': entry['date'], 'reason': entry.get('excluded_from_median')}
        for entry in day_breakdown
        if entry.get('excluded_from_median')
    ]


def _distinct_count(segments: dict) -> int:
    if not segments:
        return 0
    return len(set().union(*segments.values()))


def build_typical_weekday(  # noqa: WPS211  # signature fixed by the tests
    agg_by_period: dict,
    processed_days: list,
    weekday_days: list,
    static_feeds: list,
    day_breakdown: list,
    reject_totals: Counter,
    validation_summary: dict,
    multi_geometry_shape_id_count: int,
    multi_id_geometry_count: int,
    schedule_periods: list,
) -> dict:
    """Assemble the typical_weekday.json payload."""
    segments = _segments(agg_by_period)
    return {
        'generated_at': dt.datetime.now(dt.UTC).isoformat(),
        # Replaces the old scalar static_feed_file/version/valid fields: with
        # more than one static feed potentially in play across the days
        # processed, a single scalar can't describe them, and a run that
        # only ever loads one feed still gets a (one-element) list here
        # rather than two different shapes depending on how many were used.
        'static_feeds': static_feeds,
        'days_processed': processed_days,
        'days_in_median_mon_fri': weekday_days,
        # Days processed but deliberately kept out of the median, each with
        # its reason (see assign_schedule_periods). Weekends are the routine
        # case; a weekday here means a holiday timetable, or a date its own
        # snapshot has no calendar rows for.
        'days_excluded_from_median': _excluded_days(day_breakdown),
        # One entry per timetable observed across the days processed, each
        # naming the days that ran it and how many bins its median holds.
        # "segments" is keyed by the same period_key.
        'schedule_periods': schedule_periods,
        # Per-day breakdown, because a grand total hides exactly the signal
        # that matters here: a snapshot going stale shows up as *one day's*
        # trip_not_in_static climbing, which reject_counts_total below
        # averages away across every other day.
        'day_breakdown': day_breakdown,
        'thresholds': _thresholds(),
        'reject_counts_total': dict(reject_totals),
        'validation_vs_feed_speed_ms': validation_summary,
        # How many shape_ids carried more than one distinct geometry hash
        # across the feeds loaded this run — the number that surfaced the
        # 2026-08-31 republished-feed finding (see CLAUDE.md). Kept in the
        # output permanently rather than left as a one-off chat finding.
        'multi_geometry_shape_id_count': multi_geometry_shape_id_count,
        # The mirror image, and the 2026-09-02 finding: how many geometries
        # the agency published under more than one shape_id. Under the old
        # f"{shape_id}@{geom_hash}" key each of those was a permanent split
        # between two series describing one piece of road.
        'multi_id_geometry_count': multi_id_geometry_count,
        # Bins summed across periods: a bin observed under two timetables
        # counts twice here, because that is two medians, not one. Both
        # numbers are published because either alone reads as "how much data
        # is in this file" and they answer different questions — how many
        # medians it holds, and how much of the network it covers.
        'segment_count': sum(map(len, segments.values())),
        'distinct_segment_count': _distinct_count(segments),
        'segments': segments,
    }
