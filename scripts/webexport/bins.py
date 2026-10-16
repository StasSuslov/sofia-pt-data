"""Loading the two possible sources of speed bins into one shape."""

import json
import statistics
from collections import defaultdict
from pathlib import Path

from webexport.constants import UTF8
from webexport.fileio import read_json

_TORN = object()


def _bin_key(key: str) -> tuple:
    """Split a `shape_key|segment_index|timeslot` key into a tuple."""
    shape_key, seg_idx, slot = key.split('|')
    return shape_key, int(seg_idx), slot


def _period_bins(segments: dict) -> dict:
    """Split the `shape|segment|slot` keys of one period into tuple keys."""
    return {
        _bin_key(key): (entry['median_speed_ms'], entry['n_samples'])
        for key, entry in segments.items()
    }


def load_typical_weekday_bins(path: Path) -> tuple:
    """Load typical_weekday.json's segments by schedule period.

    The result is
    {schedule_period_key: {(shape_key, segment_index, timeslot):
    (median_speed_ms, n_samples)}}.

    segment_speeds.py aggregates one median set per schedule period (see its
    assign_schedule_periods), because pooling a day from before a timetable
    change with a day from after it averages two different services into one
    number. Each period is exported as its own bundle, so the outer key
    stays a period here rather than being flattened away.

    The inner key is still 3 fields: shape_key is bare hex (see
    segment_speeds.build_shape_key) and never contains "|". Returns
    (bins_by_period, raw_json) so the caller can also pull provenance fields
    (days_processed, schedule_periods, static_feeds, ...) out of the same
    file.
    """
    raw = read_json(path)
    bins_by_period = {
        period_key: _period_bins(segments)
        for period_key, segments in raw['segments'].items()
    }
    return bins_by_period, raw


def _decode(line: str) -> object:
    """Parse one JSONL line; a torn line comes back as the _TORN marker."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # torn line from a mid-write rsync pull, same tolerance as the rest
        # of the pipeline
        return _TORN


def _read_records(path: Path):
    """Yield the parsed records of a JSONL file, skipping blank/torn lines."""
    with path.open(encoding=UTF8) as stream:
        stripped = (line.strip() for line in stream)
        decoded = (_decode(line) for line in stripped if line)
        yield from (record for record in decoded if record is not _TORN)


def aggregate_day_bins(segment_speeds_path: Path) -> dict:
    """Median speed_ms + sample count per bin for one day's own raw samples.

    The bin is (shape_key, segment_index, timeslot). segment_speeds.py only
    ever pools Mon-Fri days into typical_weekday.json — a single day's own
    aggregate doesn't exist on disk anywhere, so the day switcher (Component
    D feature 2) needs this computed here. Keyed on shape_key, not the
    bare shape_id also present in each row: the day switcher must not
    re-introduce the exact bug typical_weekday.json's aggregation was fixed
    for by pooling two different geometries under one shape_id.
    """
    samples = defaultdict(list)
    for record in _read_records(segment_speeds_path):
        key = (record['shape_key'], record['segment_index'], record['timeslot'])
        samples[key].append(record['speed_ms'])
    return {
        key: (statistics.median(speeds), len(speeds))
        for key, speeds in samples.items()
    }
