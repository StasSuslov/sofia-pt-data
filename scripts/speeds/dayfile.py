"""Writing one collected day's speed samples."""

import operator
from pathlib import Path
from zoneinfo import ZoneInfo

from config import date_from_path
from speeds.dayread import read_groups
from speeds.models import DayContext, DayStats, Trip
from speeds.walker import TripWalker


def _trip(key: tuple, trip_maps: tuple, shapes_by_key: dict) -> Trip | None:
    """The group's trip with its shape, or None when the shape is missing."""
    vehicle_id, trip_id = key
    resolved = trip_maps[0].get(trip_id) or trip_maps[1][trip_id]
    shape = shapes_by_key.get(resolved[2])
    if shape is None:
        return None
    return Trip(shape, *resolved, vehicle_id, trip_id)


def _walk_group(
    key: tuple,
    recs: list,
    trip_maps: tuple,
    shapes_by_key: dict,
    ctx: DayContext,
) -> None:
    trip = _trip(key, trip_maps, shapes_by_key)
    if trip is None:
        ctx.stats.reject_counts['shape_not_found'] += len(recs)
        return
    recs.sort(key=operator.itemgetter(0))
    TripWalker(trip, ctx).walk(recs)


def _walk_all(
    groups: dict,
    trip_maps: tuple,
    shapes_by_key: dict,
    ctx: DayContext,
) -> None:
    for key, recs in groups.items():
        _walk_group(key, recs, trip_maps, shapes_by_key, ctx)


def process_day(  # noqa: WPS211  # signature fixed by tests and main
    day_path: Path,
    out_path: Path,
    trip_map: dict,
    shapes_by_key: dict,
    tz: ZoneInfo,
    agg: dict | None,
    diff_kmh_all: list,
    next_trip_map: dict | None = None,
    *,
    feed_speed_unit: str,
) -> DayStats:
    """Write one day's accepted speed samples and return its counters."""
    stats = DayStats()
    # Consulted only for trip_ids the day's own feed doesn't know — see
    # next_snapshot_after(). Empty when there is no later snapshot, which
    # makes the lookup below a no-op rather than a special case.
    trip_maps = trip_map, next_trip_map or {}
    groups = read_groups(day_path, trip_maps, stats)
    with out_path.open('w', encoding='utf-8') as out_f:
        ctx = DayContext(
            tz,
            out_f,
            agg,
            diff_kmh_all,
            stats,
            date_from_path(day_path),
            feed_speed_unit,
        )
        _walk_all(groups, trip_maps, shapes_by_key, ctx)
    return stats
