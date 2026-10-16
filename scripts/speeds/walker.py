"""Walking one trip's position fixes into accepted speed samples."""

import json
from typing import NamedTuple

from speeds.constants import (
    BACKWARD_TOLERANCE_M,
    KMH_PER_MS,
    MAX_SPEED_MS,
    MAX_TIME_GAP_SEC,
    SEGMENT_LENGTH_M,
)
from speeds.models import DayContext, Trip, timeslot_for
from speeds.projection import project_point, window_bounds


class Hop(NamedTuple):
    """Two consecutive projected positions of one trip instance."""

    from_ts: int
    to_ts: int
    dist: float
    delta: float
    feed_kmh: float | None

    @property
    def gap_sec(self) -> int:
        """Seconds between the two fixes."""
        return self.to_ts - self.from_ts

    @property
    def speed(self) -> float:
        """Derived speed in m/s."""
        return self.delta / self.gap_sec


def _gap_reason(gap_sec: int) -> str | None:
    if gap_sec <= 0:
        return 'non_positive_time_delta'
    if gap_sec > MAX_TIME_GAP_SEC:
        return 'gap_too_large'
    return None


def _too_soon(gap_sec: int, ctx: DayContext) -> bool:
    """Count a fix polled too soon after the previous one as skipped.

    See MIN_TIME_GAP_CADENCE_FRACTION for why such a fix is a restart.
    """
    if not 0 < gap_sec < ctx.min_gap_sec:
        return False
    ctx.stats.total_pairs += 1
    ctx.stats.reject_counts['gap_too_short'] += 1
    return True


def _feed_kmh(feed_speed: float | None, feed_speed_unit: str) -> float | None:
    """The feed's number in km/h whatever unit it arrived in.

    The comparison against the derived speed and the emitted field then mean
    one thing per city.
    """
    if feed_speed is None:
        return None
    if feed_speed_unit == 'kmh':
        return feed_speed
    return round(feed_speed * KMH_PER_MS, 3)


def _sample_row(trip: Trip, ctx: DayContext, hop: Hop, slot: str) -> dict:
    return {
        'date': ctx.date_str,
        'route_id': trip.route_id,
        'trip_id': trip.trip_id,
        'vehicle_id': trip.vehicle_id,
        # Bare shape_id: a human reading the raw archive
        # shouldn't have to strip a geometry hash to
        # recognize a shape. shape_key is the same value
        # the aggregation below actually groups on.
        'shape_id': trip.shape_id,
        'shape_key': trip.shape_key,
        'segment_index': int(hop.dist // SEGMENT_LENGTH_M),
        'timeslot': slot,
        'speed_ms': round(hop.speed, 3),
        'dist_m': round(hop.delta, 1),
        'dt_sec': hop.gap_sec,
        'feed_speed_kmh': hop.feed_kmh,
        'from_ts': hop.from_ts,
        'to_ts': hop.to_ts,
    }


class TripWalker:
    """Projects one (vehicle, trip) group's fixes and emits samples.

    `recs` are the group's snapshots for the day, sorted by snapshot_ts
    ascending, as (ts, lat, lon, feed_speed) tuples.
    """

    def __init__(self, trip: Trip, ctx: DayContext) -> None:
        """Start a walk with no previous match."""
        self.trip = trip
        self.ctx = ctx
        self.prev_ts = None
        self.last_dist = None

    def walk(self, recs: list) -> None:
        """Process every fix of the group in order."""
        for fix in recs:
            self._advance(*fix)

    def _full_search(self, lat: float, lon: float) -> float:
        shape = self.trip.shape
        last_idx = len(shape.xs) - 2
        return project_point(shape, lat, lon, 0, last_idx)[1]

    def _advance(
        self,
        ts: int,
        lat: float,
        lon: float,
        feed_speed: float | None,
    ) -> None:
        if self.prev_ts is None:
            # First point for this trip instance: no prior match to window
            # around, so this is the one full search per group (task step 3
            # only forbids full search *per record*).
            self.last_dist = self._full_search(lat, lon)
        elif _too_soon(ts - self.prev_ts, self.ctx):
            # prev_ts and last_dist stay on the earlier fix, so the next hop
            # spans a full poll interval from it.
            return
        else:
            self.last_dist = self._pair(ts, lat, lon, feed_speed)
        self.prev_ts = ts

    def _pair(
        self,
        ts: int,
        lat: float,
        lon: float,
        feed_speed: float | None,
    ) -> float:
        """Handle a fix with a predecessor; return its along-shape distance."""
        gap_sec = ts - self.prev_ts
        self.ctx.stats.total_pairs += 1
        reason = _gap_reason(gap_sec)
        if reason:
            self.ctx.stats.reject_counts[reason] += 1
            # Position may have moved anywhere in the elapsed time (or the
            # clock ran backward) — a window built from MAX_SPEED_MS * dt
            # would be enormous anyway, so re-bootstrap with a full search
            # instead.
            return self._full_search(lat, lon)
        lo_idx, hi_idx = window_bounds(
            self.trip.shape,
            self.last_dist,
            MAX_SPEED_MS * gap_sec,
        )
        dist = project_point(self.trip.shape, lat, lon, lo_idx, hi_idx)[1]
        self._judge(
            Hop(
                self.prev_ts,
                ts,
                dist,
                dist - self.last_dist,
                _feed_kmh(feed_speed, self.ctx.feed_speed_unit),
            ),
        )
        return dist

    def _judge(self, hop: Hop) -> None:
        reject_counts = self.ctx.stats.reject_counts
        if hop.delta < -BACKWARD_TOLERANCE_M:
            reject_counts['moved_backward'] += 1
        elif hop.speed > MAX_SPEED_MS:
            reject_counts['speed_too_high'] += 1
        else:
            self._emit(hop)

    def _emit(self, hop: Hop) -> None:
        ctx = self.ctx
        slot = timeslot_for(hop.to_ts, ctx.tz)
        row = _sample_row(self.trip, ctx, hop, slot)
        line = json.dumps(row, ensure_ascii=False)
        ctx.out_f.write(f'{line}\n')
        ctx.stats.samples_emitted += 1
        if ctx.agg is not None:
            key = self.trip.shape_key, row['segment_index'], slot
            ctx.agg[key].append(hop.speed)
        if hop.feed_kmh is not None:
            # Both sides in km/h. Sofia's feed field is `speed`
            # in GTFS-RT and lands in the raw archive as
            # `speed_ms`, but its values are km/h: a median of 17
            # and a p99 of 57 over the archive to 2026-10-08,
            # which as m/s would be a 61 km/h median and a
            # 205 km/h p99 for a city bus. Subtracting a km/h reading
            # from an m/s one and scaling the result was this
            # comparison's original bug — it reported a 48 km/h
            # median disagreement where the real one is 9. Which
            # of the two a city's feed does is recorded in its
            # profile, not assumed here.
            ctx.diff_kmh_all.append(abs(hop.speed * KMH_PER_MS - hop.feed_kmh))
