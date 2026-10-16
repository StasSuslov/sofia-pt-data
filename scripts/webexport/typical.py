"""The default branch: one bundle per schedule period of the typical weekday."""

import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from webexport.bins import load_typical_weekday_bins
from webexport.constants import MANIFEST_NAME, TYPICAL_MODE
from webexport.daynotes import incomplete_day_notes
from webexport.errors import fail, warn
from webexport.fileio import write_pretty
from webexport.indexes import (
    build_period_index,
    current_period_key,
    write_root_index,
)
from webexport.model import Bundle, Run
from webexport.writer import write_export

PERIOD_KEY = 'period_key'


@dataclass(frozen=True)
class _Loaded:
    """typical_weekday.json and the bins it yields, by period."""

    path: Path
    bins_by_period: dict
    document: dict


def _load(run: Run) -> _Loaded:
    path = run.processed_dir / 'typical_weekday.json'
    if not path.exists():
        fail(f'{path}: not found -- run segment_speeds.py first')
    bins_by_period, document = load_typical_weekday_bins(path)
    if 'schedule_periods' not in document:
        fail(
            f'{path}: no schedule_periods -- it predates the schedule-period '
            'split, re-run segment_speeds.py',
        )
    return _Loaded(path=path, bins_by_period=bins_by_period, document=document)


def _warn_missing_feeds(document: dict, loaded_names: list) -> None:
    # typical_weekday.json records a list of feeds (one per day it
    # potentially spans, see segment_speeds.py's day_breakdown) rather
    # than one scalar file -- warn only if this run's loaded feeds don't
    # cover everything segment_speeds.py actually used, since a
    # directory source naturally loads every snapshot anyway.
    recorded_feeds = {feed['file'] for feed in document.get('static_feeds', [])}
    missing_feeds = recorded_feeds - set(loaded_names)
    if missing_feeds:
        warn(
            f'WARNING: typical_weekday.json was built using '
            f'{sorted(missing_feeds)}, not loaded by this run '
            f'({loaded_names}) -- some shape_ids may not resolve',
        )


def _index_entry(period: dict, manifest: dict) -> dict:
    web_export = manifest['web_export']
    return {
        'period_key': period[PERIOD_KEY],
        'path': period[PERIOD_KEY],
        'first_date': period['first_date'],
        'last_date': period['last_date'],
        'days_in_median': period.get('days_in_median_mon_fri', []),
        'route_count': period['route_count'],
        'trip_count': period['trip_count'],
        'bins_total_before_threshold': web_export[
            'bins_total_before_threshold'
        ],
        'bins_retained': web_export['bins_retained'],
        'segments_retained': manifest['segment_count'],
        'timeslot_count': manifest['timeslot_count'],
    }


def _write_period(run: Run, root: Path, loaded: _Loaded, period: dict) -> dict:
    key = period[PERIOD_KEY]
    days = period.get('days_in_median_mon_fri', [])
    bundle = Bundle(
        mode=TYPICAL_MODE,
        source={
            'typical_weekday_file': loaded.path.name,
            'static_feeds_used': run.static_feed_names,
        },
        days_processed=days,
        days_in_median=days,
        incomplete_days=incomplete_day_notes(run.data_dir, days),
        schedule_period=period,
    )
    manifest = write_export(
        root / key,
        loaded.bins_by_period[key],
        run.shared,
        bundle,
    )
    return _index_entry(period, manifest)


def _write_index(index_path: Path, index: dict) -> None:
    write_pretty(index_path, index)
    entries = index['periods']
    sys.stdout.write(
        f'\n{index_path}: {len(entries)} schedule period(s), '
        f'current {index["current_period"]}\n',
    )
    for entry in entries:
        days = len(entry['days_in_median'])
        sys.stdout.write(
            f'  {entry["period_key"]}  '
            f'{entry["first_date"]}..{entry["last_date"]}  '
            f'{days} weekday day(s), '
            f'{entry["bins_retained"]:,} bins retained\n',
        )


def export_typical(run: Run) -> None:
    """Export every schedule period, its index, and the root index."""
    loaded = _load(run)
    _warn_missing_feeds(loaded.document, run.static_feed_names)

    # One bundle per schedule period under typical_weekday/, plus an index
    # naming them. The whole tree is rebuilt so a period that has left the
    # archive cannot linger as a directory the index no longer mentions.
    root = run.output_root / TYPICAL_MODE
    shutil.rmtree(root, ignore_errors=True)
    root.mkdir(parents=True)

    periods = [
        period
        for period in loaded.document['schedule_periods']
        if period[PERIOD_KEY] in loaded.bins_by_period
    ]
    entries = [_write_period(run, root, loaded, period) for period in periods]
    index = build_period_index(
        entries,
        current_period_key(periods),
        loaded.document.get('days_excluded_from_median', []),
    )
    _write_index(root / MANIFEST_NAME, index)
    write_root_index(run.output_root)
