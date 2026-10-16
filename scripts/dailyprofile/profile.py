"""Per-slot profile and per-segment window medians."""

import datetime as dt
import statistics
from collections import defaultdict
from itertools import product, starmap

from dailyprofile.constants import CORE_WINDOW

HOURS_PER_DAY = 24
SLOT_MINUTES = (0, 15, 30, 45)


def slot_label(hour: int, minute: int) -> str:
    """'HH:MM' for a slot start."""
    return dt.time(hour, minute).isoformat(timespec='minutes')


def slots() -> list:
    """The 96 quarter-hour slot labels of a day, 00:00 to 23:45."""
    return list(
        starmap(slot_label, product(range(HOURS_PER_DAY), SLOT_MINUTES)),
    )


def _by_slot(by_segment: dict) -> dict:
    per_slot = defaultdict(list)
    for speeds in by_segment.values():
        for slot, kmh in speeds.items():
            per_slot[slot].append(kmh)
    return per_slot


def profile(by_segment: dict) -> dict:
    """Slot -> (median km/h over segments observed in that slot, segment count).

    The count moves with the slot by a factor of sixteen between 03:00 and
    08:00, which is why the line alone is not the whole story and the report
    prints the counts next to it.
    """
    per_slot = _by_slot(by_segment)
    return {
        slot: (statistics.median(found), len(found))
        for slot, found in per_slot.items()
    }


def window_median(speeds: dict, window: tuple) -> float | None:
    """A segment's median over the slots it actually has inside the window."""
    lo, hi = window
    inside = [kmh for slot, kmh in speeds.items() if lo <= slot <= hi]
    return statistics.median(inside) if inside else None


def core_points(prof: dict) -> list:
    """Return [(slot, km/h)] inside CORE_WINDOW, in time order."""
    lo, hi = CORE_WINDOW
    return [
        (slot, prof[slot][0])
        for slot in slots()
        if slot in prof and lo <= slot <= hi
    ]


def observed_in_window(by_segment: dict, window: tuple) -> int:
    """How many segments carry a median inside the window."""
    return sum(
        1
        for speeds in by_segment.values()
        if window_median(speeds, window) is not None
    )
