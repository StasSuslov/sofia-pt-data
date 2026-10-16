"""Accumulators of the day loop and the per-day breakdown entry."""

from collections import Counter, defaultdict
from dataclasses import dataclass, field

from speeds.models import DayStats
from speeds.periods import COUNTS, DATE, EXCLUDED, PERIOD_KEY


def _breakdown(entry: dict, stats: DayStats) -> dict:
    next_path = entry['next_path']
    return {
        'date': entry[DATE],
        'static_feed_file': entry['static_path'].name,
        'static_feed_is_fallback': entry['is_fallback'],
        # The feed captured after this day's own, consulted only for
        # trips the day's feed doesn't know (next_snapshot_after). Both
        # counts are reported even when zero, so "the agency didn't
        # republish mid-day" and "this run never had a later feed to
        # ask" stay distinguishable in the output.
        'next_static_feed_file': next_path.name if next_path else None,
        # Two different facts, both recorded for every day including
        # the ones the median skips. schedule_signature_key is the raw
        # observation: exactly which routes ran how many trips that
        # date, hashed. schedule_period_key is the median it was pooled
        # into, null for a day left out — see excluded_from_median.
        'schedule_signature_key': entry['signature_key'],
        'schedule_period_key': entry[PERIOD_KEY],
        'excluded_from_median': entry[EXCLUDED],
        'scheduled_trip_count': sum(entry[COUNTS].values()),
        'trips_resolved_from_next_feed': len(stats.trips_from_next_feed),
        'records_resolved_from_next_feed': stats.records_from_next_feed,
        'records': stats.total_records,
        'samples_emitted': stats.samples_emitted,
        'reject_counts': dict(stats.reject_counts),
    }


def _new_aggregate() -> defaultdict:
    return defaultdict(list)


@dataclass
class RunState:
    """Accumulators the day loop fills and the final report reads."""

    # period_key -> {(shape_key, segment_index, timeslot): [speed, ...]}. One
    # aggregate per timetable, so days running different schedules never
    # merge into one median (see assign_schedule_periods).
    agg_by_period: defaultdict = field(
        default_factory=lambda: defaultdict(_new_aggregate),
    )
    diff_kmh_all: list = field(default_factory=list)
    reject_totals: Counter = field(default_factory=Counter)
    processed_days: list = field(default_factory=list)
    weekday_days: list = field(default_factory=list)
    day_breakdown: list = field(default_factory=list)
    # path -> [date_str, ...]
    feed_days_used: defaultdict = field(
        default_factory=lambda: defaultdict(list),
    )

    def aggregate_for(self, entry: dict) -> dict | None:
        """The period's aggregate, or None for a day kept out of it."""
        period_key = entry[PERIOD_KEY]
        return self.agg_by_period[period_key] if period_key else None

    def record(self, entry: dict, stats: DayStats) -> None:
        """Fold one processed day into the accumulators."""
        date_str = entry[DATE]
        self.processed_days.append(date_str)
        if entry[PERIOD_KEY]:
            self.weekday_days.append(date_str)
        self.reject_totals.update(stats.reject_counts)
        self.feed_days_used[entry['static_path']].append(date_str)
        self.day_breakdown.append(_breakdown(entry, stats))
