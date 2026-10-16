"""The --day branch: one day's own median."""

import sys
import time

from webexport.bins import aggregate_day_bins
from webexport.daynotes import incomplete_day_notes
from webexport.errors import fail
from webexport.indexes import write_root_index
from webexport.model import Bundle, Run
from webexport.writer import write_export


def export_day(run: Run, day: str) -> None:
    """Export `day` as its own bundle and refresh the root index."""
    # The day switcher exports one day's own median. A single day ran a
    # single timetable, so nothing here splits by schedule period.
    speeds_path = run.processed_dir / f'segment_speeds_{day}.jsonl'
    if not speeds_path.exists():
        fail(f'{speeds_path}: not found')
    started = time.time()
    bins = aggregate_day_bins(speeds_path)
    seconds = format(time.time() - started, '.1f')
    sys.stdout.write(
        f'  aggregated {speeds_path.name}: {len(bins):,} bins ({seconds}s)\n',
    )
    bundle = Bundle(
        mode=day,
        source={
            'segment_speeds_file': speeds_path.name,
            'static_feeds_used': run.static_feed_names,
        },
        days_processed=[day],
        days_in_median=[day],
        incomplete_days=incomplete_day_notes(run.data_dir, [day]),
    )
    write_export(run.output_root / day, bins, run.shared, bundle)
    write_root_index(run.output_root)
