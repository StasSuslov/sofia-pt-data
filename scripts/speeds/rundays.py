"""The main loop: process each requested day and record its breakdown."""

import operator
import sys
import time

from speeds.cli import Setup
from speeds.dayfile import process_day
from speeds.feedcache import FeedCache
from speeds.models import DayStats
from speeds.periods import DATE
from speeds.runstate import RunState


def _feeds(entry: dict, cache: FeedCache) -> tuple:
    """The day's trip map and shapes, plus the next feed's trip map."""
    trip_map, shapes_by_key, _ = cache.load_feed(entry['static_path'])
    next_trip_map = {}
    if entry['next_path'] is not None:
        next_trip_map, next_shapes, _ = cache.load_feed(entry['next_path'])
        # Merged, not substituted: a trip resolved from the later feed
        # still has to find its geometry, and shapes_by_key is
        # content-addressed so the union can't collide (build_shape_key).
        shapes_by_key = {**next_shapes, **shapes_by_key}
    return trip_map, shapes_by_key, next_trip_map


def _process(
    entry: dict,
    setup: Setup,
    state: RunState,
    feeds: tuple,
) -> DayStats:
    trip_map, shapes_by_key, next_trip_map = feeds
    return process_day(
        entry['day_path'],
        setup.output_dir / f'segment_speeds_{entry[DATE]}.jsonl',
        trip_map,
        shapes_by_key,
        setup.tz,
        state.aggregate_for(entry),
        state.diff_kmh_all,
        next_trip_map,
        feed_speed_unit=setup.city['speed_field_unit'],
    )


def _report(entry: dict, stats: DayStats, elapsed: float) -> None:
    date_str = entry[DATE]
    static_name = entry['static_path'].name
    if stats.records_from_next_feed:
        sys.stdout.write(
            f'{date_str}: {stats.records_from_next_feed:,} records on '
            f'{len(stats.trips_from_next_feed)} trip(s) unknown to '
            f'{static_name}, resolved from {entry["next_path"].name} '
            f'(feed republished during the day)\n',
        )
    kind = 'weekday' if entry['is_weekday'] else 'weekend'
    rejected = sum(stats.reject_counts.values())
    sys.stdout.write(
        f'{date_str} ({kind}, static={static_name}): '
        f'{stats.total_records:,} records | {stats.total_pairs:,} pairs | '
        f'{stats.samples_emitted:,} samples emitted | {rejected:,} rejected | '
        f'{elapsed:.1f}s\n',
    )


def run_days(
    setup: Setup,
    cache: FeedCache,
    day_plan: list,
    state: RunState,
) -> None:
    """Process every planned day that was asked for."""
    for entry in filter(operator.itemgetter('processed'), day_plan):
        feeds = _feeds(entry, cache)
        started = time.time()
        stats = _process(entry, setup, state, feeds)
        elapsed = time.time() - started
        state.record(entry, stats)
        _report(entry, stats, elapsed)
