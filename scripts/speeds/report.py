"""End of run: validation summary, typical_weekday.json and its summary."""

import json
import statistics

from speeds.cli import Setup
from speeds.constants import VALIDATION_DIFF_THRESHOLD_KMH
from speeds.feedcache import FeedCache
from speeds.feedmeta import (
    count_multi_geometry_shape_ids,
    count_multi_id_geometries,
    sha256_of_file,
)
from speeds.period_report import build_schedule_periods
from speeds.runstate import RunState
from speeds.summary import print_summary
from speeds.typical import build_typical_weekday

# Percent is a share of 100.
PERCENT = 100


def _pct_over_threshold(diffs: list) -> float | None:
    if not diffs:
        return None
    over = sum(diff > VALIDATION_DIFF_THRESHOLD_KMH for diff in diffs)
    return round(PERCENT * over / len(diffs), 3)


def _validation_summary(city: dict, diffs: list) -> dict:
    return {
        # Named so nobody downstream repeats the m/s-vs-km/h mistake this
        # comparison was originally computed with — see METHODOLOGY.md. Both
        # the unit and why it is believed come from the city profile, because
        # "this feed violates the spec" is a claim about one city's feed.
        'comparison_unit': 'km/h',
        'feed_speed_field_unit': city['speed_field_unit'],
        'feed_speed_field_provenance': city.get('speed_field_provenance'),
        'n_compared': len(diffs),
        'median_abs_diff_kmh': (
            round(statistics.median(diffs), 3) if diffs else None
        ),
        'pct_over_threshold': _pct_over_threshold(diffs),
    }


def _static_feeds_meta(cache: FeedCache, state: RunState) -> list:
    return [
        {
            'file': path.name,
            'sha256': sha256_of_file(path),
            'feed_version': feed_info.get('feed_version'),
            'feed_start_date': feed_info.get('feed_start_date'),
            'feed_end_date': feed_info.get('feed_end_date'),
            'days_processed': state.feed_days_used[path],
        }
        for path, (_, _, feed_info) in cache.feeds.items()
    ]


def _schedule_periods(day_plan: list, state: RunState) -> list:
    return build_schedule_periods(
        [entry for entry in day_plan if entry['processed']],
        {key: len(agg) for key, agg in state.agg_by_period.items()},
    )


def _typical(
    setup: Setup,
    cache: FeedCache,
    day_plan: list,
    state: RunState,
) -> dict:
    ids_by_key = cache.shape_ids_by_key
    return build_typical_weekday(
        state.agg_by_period,
        state.processed_days,
        state.weekday_days,
        _static_feeds_meta(cache, state),
        state.day_breakdown,
        state.reject_totals,
        _validation_summary(setup.city, state.diff_kmh_all),
        count_multi_geometry_shape_ids(ids_by_key),
        count_multi_id_geometries(ids_by_key),
        _schedule_periods(day_plan, state),
    )


def finish(
    setup: Setup,
    cache: FeedCache,
    day_plan: list,
    state: RunState,
) -> None:
    """Write typical_weekday.json and print the run summary."""
    typical = _typical(setup, cache, day_plan, state)
    typical_path = setup.output_dir / 'typical_weekday.json'
    body = json.dumps(typical, indent=2, ensure_ascii=False)
    typical_path.write_text(f'{body}\n', encoding='utf-8')
    print_summary(typical_path, typical)
