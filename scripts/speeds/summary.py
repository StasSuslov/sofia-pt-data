"""The run summary printed after typical_weekday.json is written."""

import sys
from pathlib import Path


def _period_fields(period: dict) -> list:
    days = period['days_in_median_mon_fri']
    bin_count = period['bin_count']
    churn = period['max_churn_vs_reference']
    listed = ', '.join(days)
    return [
        period['period_key'],
        f'{period["first_date"]}..{period["last_date"]}',
        f'{period["route_count"]} routes / {period["trip_count"]:,} trips',
        f'{len(days)} weekday day(s), {bin_count:,} bins',
        f'max churn {churn:,} trips',
        f'[{listed}]',
    ]


def _period_line(period: dict) -> str:
    joined = '  '.join(_period_fields(period))
    return f'  {joined}\n'


def _heading(typical_path: Path, typical: dict) -> str:
    distinct = typical['distinct_segment_count']
    counted = typical['segment_count']
    weekday_days = len(typical['days_in_median_mon_fri'])
    periods = len(typical['schedule_periods'])
    return (
        f'\n{typical_path}: {distinct:,} distinct (segment, timeslot) '
        f'bins, {counted:,} counted per period, '
        f'from {weekday_days} weekday day(s) in {periods} '
        f'schedule period(s)\n'
    )


def _warn_multi_geometry(typical: dict) -> None:
    multi_geometry = typical['multi_geometry_shape_id_count']
    if multi_geometry:
        feeds = len(typical['static_feeds'])
        sys.stderr.write(
            f'WARNING: {multi_geometry} shape_id(s) carried more than one '
            f'distinct geometry across the {feeds} static feed(s) loaded '
            f'this run\n',
        )


def print_summary(typical_path: Path, typical: dict) -> None:
    """Print the bin counts, one line per period and the validation."""
    sys.stdout.write(_heading(typical_path, typical))
    sys.stdout.writelines(map(_period_line, typical['schedule_periods']))
    validation = typical['validation_vs_feed_speed_ms']
    sys.stdout.write(f'Validation vs feed speed_ms: {validation}\n')
    _warn_multi_geometry(typical)
