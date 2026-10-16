"""The per-timeslot files and the typical-speed baseline built from them."""

import statistics
from collections import defaultdict

from webexport.constants import MS_TO_KMH, TYPICAL_MIN_SLOTS


def _new_slot() -> dict:
    return {'segment_idx': [], 'speed_kmh': [], 'n_samples': []}


def _add_bin(by_slot: dict, index_of: dict, key: tuple, stats: tuple) -> None:
    """File one bin under its slot, unless geometry has no index for it."""
    shape_key, seg_idx, slot = key
    position = index_of.get((shape_key, seg_idx))
    if position is None:
        return
    column = by_slot[slot]
    column['segment_idx'].append(position)
    column['speed_kmh'].append(round(stats[0] * MS_TO_KMH))
    column['n_samples'].append(stats[1])


def build_timeslot_files(bins: dict, index_of: dict) -> dict:
    """Map a slot to its parallel arrays, one entry per surviving bin.

    The payload is {"timeslot": slot, "segment_idx": [...], "speed_kmh":
    [...], "n_samples": [...]}.
    A bin whose (shape_id, segment_index) isn't in index_of (see
    build_geometry's `missing` count) is skipped: there's no index to
    reference it by.
    """
    by_slot = defaultdict(_new_slot)
    for key, stats in bins.items():
        _add_bin(by_slot, index_of, key, stats)
    return {
        slot: {'timeslot': slot, **column} for slot, column in by_slot.items()
    }


def _typical(speeds: list) -> int | None:
    """Median of a segment's slot speeds, or None below the filled-slot gate."""
    if len(speeds) < TYPICAL_MIN_SLOTS:
        return None
    return round(statistics.median(speeds))


def build_typical_kmh(timeslot_files: dict, segment_count: int) -> list:
    """One entry per geometry segment: the median of its own per-slot medians.

    Integer km/h, or None below TYPICAL_MIN_SLOTS filled
    slots. Parallel to geometry's shape_idx/segment_index arrays.

    Built from `timeslot_files`, i.e. from the same rounded integers that
    ship in timeslots/HHMM.json, and not from the pre-threshold bins: a
    published baseline that cannot be recomputed from the published files is
    a number the reader has to take on trust.

    Median of medians, and named as such — the measurement does not choose
    the formula (mean, p60 and p75 land within a few points of it with no
    stable winner); it is a median because D4 already made the typical
    weekday a median.
    """
    by_segment = defaultdict(list)
    for payload in timeslot_files.values():
        for position, kmh in zip(
            payload['segment_idx'],
            payload['speed_kmh'],
            strict=True,
        ):
            by_segment[position].append(kmh)
    return [
        _typical(by_segment.get(index, ())) for index in range(segment_count)
    ]
