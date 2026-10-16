"""Reading one collected day into per-trip groups of position fixes."""

import json
import operator
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path

from config import open_maybe_gzip
from speeds.models import DayStats

_POSITION = operator.itemgetter('snapshot_ts', 'lat', 'lon')


def _parse(line: str) -> dict | None:
    """Parse one JSON line; blank and torn lines give None."""
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        # torn line from a mid-write rsync pull — same tolerance as
        # generate_manifest.py
        return None


def iter_records(day_path: Path) -> Iterator[dict]:
    """Yield the day's parsed records, skipping blank and torn lines."""
    with open_maybe_gzip(day_path, 'rt', encoding='utf-8') as day_file:
        for raw_line in day_file:
            rec = _parse(raw_line.strip())
            if rec is not None:
                yield rec


def _resolve(trip_id: str | None, trip_maps: tuple, stats: DayStats):
    """Look the trip up in the day's feed, then in the next one."""
    route_shape = trip_maps[0].get(trip_id) if trip_id else None
    if route_shape is None and trip_id:
        route_shape = trip_maps[1].get(trip_id)
        if route_shape is not None:
            stats.records_from_next_feed += 1
            stats.trips_from_next_feed.add(trip_id)
    return route_shape


def _is_trip_known(rec: dict, trip_maps: tuple, stats: DayStats) -> bool:
    """Count the record and say whether its trip resolves to a shape."""
    stats.total_records += 1
    route_shape = _resolve(rec.get('trip_id'), trip_maps, stats)
    if route_shape is None or not rec.get('vehicle_id'):
        stats.reject_counts['trip_not_in_static'] += 1
        return False
    return True


def _fix(rec: dict) -> tuple:
    return *_POSITION(rec), rec.get('speed_ms')


def _key(rec: dict) -> tuple:
    return rec.get('vehicle_id'), rec.get('trip_id')


def read_groups(
    day_path: Path,
    trip_maps: tuple,
    stats: DayStats,
) -> dict:
    """Group the day's resolvable records by (vehicle, trip)."""
    groups = defaultdict(list)
    for rec in iter_records(day_path):
        if _is_trip_known(rec, trip_maps, stats):
            groups[_key(rec)].append(_fix(rec))
    return groups
