"""Per-day records shared by the sample walker and the day processor."""

import datetime as dt
from collections import Counter
from dataclasses import dataclass, field
from typing import TextIO
from zoneinfo import ZoneInfo

from speeds.constants import TIMESLOT_MINUTES
from speeds.geometry import Shape


def timeslot_for(snapshot_ts: int, tz: ZoneInfo) -> str:
    """15-minute local-time bin, formatted HH:MM (bin start)."""
    local = dt.datetime.fromtimestamp(snapshot_ts, tz)
    bin_minute = (local.minute // TIMESLOT_MINUTES) * TIMESLOT_MINUTES
    return local.replace(minute=bin_minute).strftime('%H:%M')


@dataclass
class DayStats:
    """Counters for one processed day."""

    total_records: int = 0
    total_pairs: int = 0
    samples_emitted: int = 0
    reject_counts: Counter = field(default_factory=Counter)
    # Records (and distinct trips) the day's own static feed could not
    # resolve and the next captured feed could — a mid-day republish, see
    # next_snapshot_after(). Not a reject reason: these records are kept.
    # Worth its own field because a silently rising number means the agency
    # is republishing inside the collection day often enough that the
    # archive cadence in deploy/sofia-static-archive.timer is too slow.
    records_from_next_feed: int = 0
    trips_from_next_feed: set = field(default_factory=set)


@dataclass(frozen=True)
class Trip:
    """Identity and geometry of one (vehicle, trip) group."""

    shape: Shape
    route_id: str
    shape_id: str
    shape_key: str
    vehicle_id: str
    trip_id: str


@dataclass(frozen=True)
class DayContext:
    """Everything a day's walkers write into or read from.

    `feed_speed_unit` ("kmh" or "ms") says what the feed's own speed field
    carries and comes from the city profile; it has no default because the
    two readings differ by a factor of 3.6 and both look plausible. The raw
    archive keeps the field under the name the feed gave it (`speed_ms`);
    everything written from here on is km/h.

    `min_gap_sec` is the shortest hop a walker accepts, derived from the
    city's poll interval for the same reason.
    """

    tz: ZoneInfo
    out_f: TextIO
    agg: dict | None
    diff_kmh_all: list
    stats: DayStats
    date_str: str
    feed_speed_unit: str
    min_gap_sec: float
