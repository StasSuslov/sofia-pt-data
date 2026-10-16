"""Read one schedule period out of the typical-weekday aggregate."""

import json
import sys
from collections import defaultdict
from pathlib import Path

from export_web import current_period_key  # same rule the map opens with

from dailyprofile.constants import MIN_SAMPLES, MS_TO_KMH


def _split_bin(bin_key: str) -> tuple:
    shape_key, seg_idx, slot = bin_key.rsplit('|', 2)
    return (shape_key, seg_idx), slot


def _segment_table(bins: dict) -> dict:
    """Map (shape_key, segment_index) -> {slot: km/h}."""
    # {(shape_key, segment_index): {slot: kmh}} over bins that clear the sample
    # threshold, so the figure rests on the same bins the map publishes.
    by_segment = defaultdict(dict)
    for bin_key, cell in bins.items():
        if cell['n_samples'] < MIN_SAMPLES:
            continue
        segment, slot = _split_bin(bin_key)
        by_segment[segment][slot] = cell['median_speed_ms'] * MS_TO_KMH
    return dict(by_segment)


def load_period(path: Path, period_key: str | None) -> tuple:
    """Return (period record, by_segment table) for `period_key`.

    Without a key the period the map opens with is used.
    """
    doc = json.loads(path.read_text(encoding='utf-8'))
    key = period_key or current_period_key(doc['schedule_periods'])
    if key is None:
        sys.exit('no schedule period holds a weekday median')
    period = next(
        record
        for record in doc['schedule_periods']
        if record['period_key'] == key
    )
    return period, _segment_table(doc['segments'][key])
