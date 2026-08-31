#!/usr/bin/env python3
"""
Component C (preprocessing, CLAUDE.md section 3): turn raw GTFS-RT vehicle
position snapshots into per-segment speed samples, then aggregate them into
a "typical weekday" median per D4 (median per segment+timeslot, Mon-Fri
observations only).

Algorithm (given, not reinvented — see the task write-up):
  1. trips.txt -> trip_id -> (route_id, shape_id); shapes.txt -> per-shape
     polyline with cumulative distance (haversine between consecutive shape
     points). direction_id is empty for every trip in this feed, so
     shape_id is what actually distinguishes direction and route variant
     (see CLAUDE.md's stated facts about the static feed) — there is no
     direction_id to fall back on, and none is used here.
  2. same-day snapshots grouped by (vehicle_id, trip_id), sorted by time.
  3. each raw lat/lon projected onto its trip's shape as a distance along
     the shape, searching only a window of segments around the previous
     match — a full rescan of a ~300-point shape on every one of ~2.4M
     snapshots does not finish in a reasonable time.
  4. speed = delta(distance along shape) / delta(time) between consecutive
     projected positions.
  5. segment = fixed 200 m bin of distance-along-shape, keyed by
     (shape_id, segment_index) — shape_id already carries direction, see (1).
  6. timeslot = 15-minute local-time bin (timezone from config.py, not
     hardcoded — CLAUDE.md D2).
  7. rejects are counted, never silently dropped — see REJECT_REASONS and
     the named thresholds below.
  8. "typical weekday" = median speed per (segment, timeslot), Mon-Fri only,
     with the sample count stored next to every median — a median of 2
     points must not look like a median of 200 (D4).
  9. validation: the feed's own speed reading is compared against the speed
     this script derives, as a free end-to-end sanity check of the whole
     pipeline. Both sides are compared in km/h: the feed reports km/h even
     though the field arrives named speed_ms (see METHODOLOGY.md).

Outputs (directory created if missing):
    <output-dir>/segment_speeds_<date>.jsonl  — one row per accepted sample
    <output-dir>/typical_weekday.json         — D4 aggregation + run metadata

Usage:
    python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip \\
        data/sofia --output-dir data/sofia/processed 2026-08-28 2026-08-31

    # no explicit dates: every <YYYY-MM-DD>.jsonl (or .jsonl.gz, transparently
    # decompressed — see deploy/sofia-compress.service) found in the data dir
    python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip data/sofia
"""

import argparse
import bisect
import csv
import io
import json
import math
import statistics
import sys
import time
import zipfile
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone as dt_timezone
from pathlib import Path
from typing import NamedTuple, Optional
from zoneinfo import ZoneInfo

# config.py at the repo root is the single source of truth for timezone,
# same pattern as scripts/generate_manifest.py — importing collect.py itself
# would pull in requests/protobuf this script never needs. find_day_files/
# resolve_day_file/open_maybe_gzip are the same day-file helpers
# generate_manifest.py uses, so a <date>.jsonl.gz produced by
# deploy/sofia-compress.service is read transparently here too.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import DEFAULT_TIMEZONE, date_from_path, find_day_files, open_maybe_gzip, resolve_day_file  # noqa: E402

# ─── Constants: segmentation & aggregation (task spec) ─────────────────────

SEGMENT_LENGTH_M = 200.0   # fixed-length bin along a shape's polyline
TIMESLOT_MINUTES = 15      # local-time bin width
EARTH_RADIUS_M = 6371000.0

# ─── Constants: rejection thresholds ────────────────────────────────────────
# Named here, not inlined, so every cutoff is visible and arguable in one
# place (task requirement).

# A gap this long between two snapshots of the same (vehicle, trip) means
# something happened in between that a straight-line speed can't represent
# (a layover, a missed poll run, a detour) — treat it as a break, not a slow
# stretch. Chosen as a round 10 minutes: several times collect.py's default
# 45s poll interval (config.DEFAULT_INTERVAL_SEC), well past normal jitter,
# still short enough that a real single hop across it would be plausible.
MAX_TIME_GAP_SEC = 600

# Task-specified cutoff. Doubles as a wide margin over how fast a bus/tram/
# trolley actually moves anywhere in the network, even on the ring road.
MAX_SPEED_KMH = 100.0
MAX_SPEED_MS = MAX_SPEED_KMH / 3.6

# A vehicle's projected position along its own shape can wobble backward by
# a few tens of meters from GPS noise, or from snapping onto a slightly
# wrong segment near a shape self-crossing — that's noise, not the vehicle
# reversing. Beyond this, treat it as a real problem with the match (wrong
# branch, a pull-in/pull-out, a real reversal) and drop the pair.
BACKWARD_TOLERANCE_M = 50.0

# Slack added on both ends of the physically-plausible search window (see
# window_bounds()) to absorb GPS noise — without it, a vehicle sitting still
# right at a segment boundary, or one moving at exactly the noisy edge of
# its expected span, can fall just outside a window built from zero slack.
SEARCH_MARGIN_M = 300.0

# Task-specified validation cutoff: how far the derived speed must be from
# the feed's own speed_ms before it counts as a meaningful disagreement.
VALIDATION_DIFF_THRESHOLD_KMH = 20.0

REJECT_REASONS = (
    "trip_not_in_static",       # trip_id from the RT feed has no trips.txt row (~0.7% per CLAUDE.md)
    "shape_not_found",          # trip resolved, but its shape_id has <2 usable points in shapes.txt
    "non_positive_time_delta",  # duplicate/out-of-order snapshot_ts within a (vehicle, trip) group
    "gap_too_large",            # dt > MAX_TIME_GAP_SEC
    "moved_backward",           # along-shape distance dropped by more than BACKWARD_TOLERANCE_M
    "speed_too_high",           # derived speed > MAX_SPEED_MS
)


# ─── Geometry ────────────────────────────────────────────────────────────────

def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters — used for cumulative along-shape
    distance (task step 1 asks for haversine explicitly). Segment
    *projection* below uses a flat local-plane approximation instead; see
    Shape's docstring for why that's a different job."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


class Shape(NamedTuple):
    """
    A trip shape as a polyline in two parallel coordinate systems:
      - xs/ys: a local flat-plane (equirectangular) projection, referenced
        to the shape's own mean latitude and first longitude, used only to
        find the closest line *segment* to a raw GPS point — projecting a
        point onto a segment needs planar geometry, not great-circle math.
      - cum_dist: cumulative haversine distance (meters) at each vertex —
        this, not the planar coordinates, is what "distance along the
        shape" means for segmenting and speed, per task step 1. The
        flat-plane approximation is never used for a reported distance or
        speed, only for picking which segment a point is nearest to.
    A single city's extent (tens of km) makes the flat-plane distortion
    (~1% at the far edges of a shape) irrelevant for that one job.
    """
    xs: list
    ys: list
    cum_dist: list
    ref_lat_deg: float
    ref_lon: float


def build_shape(points: list) -> Shape:
    """`points` is a list of (lat, lon), already ordered by shape_pt_sequence."""
    lats = [p[0] for p in points]
    lons = [p[1] for p in points]
    ref_lat_deg = sum(lats) / len(lats)
    ref_lat_rad = math.radians(ref_lat_deg)
    ref_lon = lons[0]

    xs = [math.radians(lon - ref_lon) * math.cos(ref_lat_rad) * EARTH_RADIUS_M for lon in lons]
    ys = [math.radians(lat - ref_lat_deg) * EARTH_RADIUS_M for lat in lats]

    cum_dist = [0.0]
    for i in range(1, len(lats)):
        cum_dist.append(cum_dist[-1] + haversine_m(lats[i - 1], lons[i - 1], lats[i], lons[i]))

    return Shape(xs, ys, cum_dist, ref_lat_deg, ref_lon)


def to_local_xy(shape: Shape, lat: float, lon: float) -> tuple:
    ref_lat_rad = math.radians(shape.ref_lat_deg)
    x = math.radians(lon - shape.ref_lon) * math.cos(ref_lat_rad) * EARTH_RADIUS_M
    y = math.radians(lat - shape.ref_lat_deg) * EARTH_RADIUS_M
    return x, y


def _project_onto_segment(px, py, ax, ay, bx, by) -> tuple:
    """Standard point-onto-segment projection; returns (t in [0,1], perpendicular distance)."""
    dx, dy = bx - ax, by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        t = 0.0
    else:
        t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
    cx, cy = ax + t * dx, ay + t * dy
    return t, math.hypot(px - cx, py - cy)


def project_point(shape: Shape, lat: float, lon: float, lo_idx: int, hi_idx: int) -> tuple:
    """
    Closest-segment projection of (lat, lon) onto shape segments
    [lo_idx..hi_idx] inclusive (segment i runs from vertex i to vertex i+1).
    Returns (best_segment_index, along_shape_distance_m, perpendicular_distance_m).

    Callers pick lo_idx/hi_idx: (0, len(shape.xs)-2) for a full search, or
    window_bounds()'s narrower range for the sliding-window search.
    """
    px, py = to_local_xy(shape, lat, lon)
    best_i, best_t, best_dist = lo_idx, 0.0, float("inf")
    for i in range(lo_idx, hi_idx + 1):
        t, dist = _project_onto_segment(px, py, shape.xs[i], shape.ys[i], shape.xs[i + 1], shape.ys[i + 1])
        if dist < best_dist:
            best_i, best_t, best_dist = i, t, dist
    seg_start, seg_end = shape.cum_dist[best_i], shape.cum_dist[best_i + 1]
    along = seg_start + best_t * (seg_end - seg_start)
    return best_i, along, best_dist


def window_bounds(shape: Shape, last_dist: float, max_travel_m: float) -> tuple:
    """
    Segment-index range to search around the *previous match's* along-shape
    distance, sized from how far the vehicle could plausibly have traveled
    (max_travel_m, normally MAX_SPEED_MS * dt) plus SEARCH_MARGIN_M slack.

    Anchored on distance (via bisect over the monotonically increasing
    cum_dist) rather than a fixed count of vertices around the previous
    vertex index, because shape point density varies enormously across this
    network (median ~12m spacing, some stretches near 1km) — a fixed vertex
    window would be wastefully wide on dense shapes or too narrow to contain
    a physically plausible match on sparse ones.
    """
    lo_dist = last_dist - BACKWARD_TOLERANCE_M - SEARCH_MARGIN_M
    hi_dist = last_dist + max_travel_m + SEARCH_MARGIN_M
    lo_idx = max(0, bisect.bisect_left(shape.cum_dist, lo_dist) - 1)
    hi_idx = min(len(shape.cum_dist) - 2, bisect.bisect_right(shape.cum_dist, hi_dist))
    if hi_idx < lo_idx:
        hi_idx = lo_idx
    return lo_idx, hi_idx


# ─── Static feed loading ────────────────────────────────────────────────────

def load_static(gtfs_zip_path: Path) -> tuple:
    """
    trip_id -> (route_id, shape_id) from trips.txt, and shape_id -> Shape
    from shapes.txt. direction_id is blank for every trip in this feed (see
    CLAUDE.md) so it plays no role here — shape_id is the only thing that
    tells two directions or route variants apart, per task step 1.
    """
    trip_map = {}
    shape_points = defaultdict(list)

    with zipfile.ZipFile(gtfs_zip_path) as z:
        with z.open("trips.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                if row["shape_id"]:
                    trip_map[row["trip_id"]] = (row["route_id"], row["shape_id"])
        with z.open("shapes.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                shape_points[row["shape_id"]].append(
                    (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
                )

    shapes_by_id = {}
    for shape_id, pts in shape_points.items():
        pts.sort(key=lambda p: p[0])
        coords = [(lat, lon) for _, lat, lon in pts]
        if len(coords) >= 2:
            shapes_by_id[shape_id] = build_shape(coords)

    return trip_map, shapes_by_id


def load_feed_info(gtfs_zip_path: Path) -> dict:
    """Best-effort feed_info.txt row, for stamping run metadata — absent in
    some GTFS feeds, so this must not be load-bearing for anything else."""
    try:
        with zipfile.ZipFile(gtfs_zip_path) as z:
            with z.open("feed_info.txt") as f:
                return next(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")), {})
    except (KeyError, zipfile.BadZipFile, StopIteration):
        return {}


# ─── Time helpers ───────────────────────────────────────────────────────────

def timeslot_for(snapshot_ts: int, tz: ZoneInfo) -> str:
    """15-minute local-time bin, formatted HH:MM (bin start)."""
    local = datetime.fromtimestamp(snapshot_ts, tz)
    bin_minute = (local.minute // TIMESLOT_MINUTES) * TIMESLOT_MINUTES
    return f"{local.hour:02d}:{bin_minute:02d}"


# ─── Per-day processing ─────────────────────────────────────────────────────

@dataclass
class DayStats:
    total_records: int = 0
    total_pairs: int = 0
    samples_emitted: int = 0
    reject_counts: Counter = field(default_factory=Counter)


def process_group(
    recs: list,
    shape: Shape,
    route_id: str,
    shape_id: str,
    vehicle_id: str,
    trip_id: str,
    tz: ZoneInfo,
    out_f,
    agg: Optional[dict],
    diff_kmh_all: list,
    stats: DayStats,
    date_str: str,
) -> None:
    """`recs`: one (vehicle_id, trip_id)'s snapshots for the day, sorted by
    snapshot_ts ascending, as (ts, lat, lon, feed_speed_ms) tuples."""
    prev_ts = None
    last_dist = None
    full_range = (0, len(shape.xs) - 2)

    for ts, lat, lon, feed_speed in recs:
        if prev_ts is None:
            # First point for this trip instance: no prior match to window
            # around, so this is the one full search per group (task step 3
            # only forbids full search *per record*).
            _, dist, _ = project_point(shape, lat, lon, *full_range)
        else:
            dt = ts - prev_ts
            stats.total_pairs += 1

            if dt <= 0:
                stats.reject_counts["non_positive_time_delta"] += 1
                _, dist, _ = project_point(shape, lat, lon, *full_range)
            elif dt > MAX_TIME_GAP_SEC:
                stats.reject_counts["gap_too_large"] += 1
                # Position may have moved anywhere in the elapsed time — a
                # window built from MAX_SPEED_MS * dt would be enormous
                # anyway, so re-bootstrap with a full search instead.
                _, dist, _ = project_point(shape, lat, lon, *full_range)
            else:
                lo_idx, hi_idx = window_bounds(shape, last_dist, MAX_SPEED_MS * dt)
                _, dist, _ = project_point(shape, lat, lon, lo_idx, hi_idx)
                delta = dist - last_dist

                if delta < -BACKWARD_TOLERANCE_M:
                    stats.reject_counts["moved_backward"] += 1
                else:
                    speed = delta / dt
                    if speed > MAX_SPEED_MS:
                        stats.reject_counts["speed_too_high"] += 1
                    else:
                        segment_index = int(dist // SEGMENT_LENGTH_M)
                        slot = timeslot_for(ts, tz)
                        out_f.write(json.dumps({
                            "date": date_str,
                            "route_id": route_id,
                            "trip_id": trip_id,
                            "vehicle_id": vehicle_id,
                            "shape_id": shape_id,
                            "segment_index": segment_index,
                            "timeslot": slot,
                            "speed_ms": round(speed, 3),
                            "dist_m": round(delta, 1),
                            "dt_sec": dt,
                            "feed_speed_kmh": feed_speed,
                            "from_ts": prev_ts,
                            "to_ts": ts,
                        }, ensure_ascii=False) + "\n")
                        stats.samples_emitted += 1
                        if agg is not None:
                            agg[(shape_id, segment_index, slot)].append(speed)
                        if feed_speed is not None:
                            # Both sides in km/h. The feed's field is `speed` in
                            # GTFS-RT and lands in the raw archive as `speed_ms`,
                            # but the values are km/h, not m/s: they are whole
                            # numbers with a median of 17 and a maximum of 87,
                            # which as m/s would be a 61 km/h median and a 313
                            # km/h top speed for a city bus. Subtracting a km/h
                            # reading from an m/s one and scaling the result was
                            # this comparison's original bug — it reported a 48
                            # km/h median disagreement where the real one is 9.
                            diff_kmh_all.append(abs(speed * 3.6 - feed_speed))

        last_dist = dist
        prev_ts = ts


def process_day(
    day_path: Path,
    out_path: Path,
    trip_map: dict,
    shapes_by_id: dict,
    tz: ZoneInfo,
    agg: Optional[dict],
    diff_kmh_all: list,
) -> DayStats:
    stats = DayStats()
    groups = defaultdict(list)

    with open_maybe_gzip(day_path, "rt", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn line from a mid-write rsync pull — same tolerance as generate_manifest.py

            stats.total_records += 1
            trip_id = rec.get("trip_id")
            vehicle_id = rec.get("vehicle_id")
            route_shape = trip_map.get(trip_id) if trip_id else None

            if route_shape is None or not vehicle_id:
                stats.reject_counts["trip_not_in_static"] += 1
                continue

            groups[(vehicle_id, trip_id)].append(
                (rec["snapshot_ts"], rec["lat"], rec["lon"], rec.get("speed_ms"))
            )

    date_str = date_from_path(day_path)
    with out_path.open("w", encoding="utf-8") as out_f:
        for (vehicle_id, trip_id), recs in groups.items():
            route_id, shape_id = trip_map[trip_id]
            shape = shapes_by_id.get(shape_id)
            if shape is None:
                stats.reject_counts["shape_not_found"] += len(recs)
                continue
            recs.sort(key=lambda r: r[0])
            process_group(recs, shape, route_id, shape_id, vehicle_id, trip_id, tz, out_f, agg, diff_kmh_all, stats, date_str)

    return stats


# ─── Aggregation (D4) ────────────────────────────────────────────────────────

def build_typical_weekday(
    agg: dict,
    processed_days: list,
    weekday_days: list,
    static_feed_name: str,
    feed_info: dict,
    reject_totals: Counter,
    validation_summary: dict,
) -> dict:
    segments = {}
    for (shape_id, segment_index, slot), speeds in agg.items():
        segments[f"{shape_id}|{segment_index}|{slot}"] = {
            "median_speed_ms": round(statistics.median(speeds), 3),
            "n_samples": len(speeds),
        }

    return {
        "generated_at": datetime.now(dt_timezone.utc).isoformat(),
        "static_feed_file": static_feed_name,
        "static_feed_version": feed_info.get("feed_version"),
        "static_feed_valid": {
            "start": feed_info.get("feed_start_date"),
            "end": feed_info.get("feed_end_date"),
        },
        "days_processed": processed_days,
        "days_in_median_mon_fri": weekday_days,
        "thresholds": {
            "segment_length_m": SEGMENT_LENGTH_M,
            "timeslot_minutes": TIMESLOT_MINUTES,
            "max_time_gap_sec": MAX_TIME_GAP_SEC,
            "max_speed_kmh": MAX_SPEED_KMH,
            "backward_tolerance_m": BACKWARD_TOLERANCE_M,
            "search_margin_m": SEARCH_MARGIN_M,
            "validation_diff_threshold_kmh": VALIDATION_DIFF_THRESHOLD_KMH,
        },
        "reject_counts_total": dict(reject_totals),
        "validation_vs_feed_speed_ms": validation_summary,
        "segment_count": len(segments),
        "segments": segments,
    }


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("static_zip", type=Path, help="GTFS static zip (needs trips.txt, shapes.txt, feed_info.txt)")
    parser.add_argument("data_dir", type=Path, help="Directory containing <YYYY-MM-DD>.jsonl RT snapshot files")
    parser.add_argument("dates", nargs="*", help="Specific YYYY-MM-DD days to process (default: every "
                                                   "<YYYY-MM-DD>.jsonl file found in data_dir)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Where to write outputs (default: <data_dir>/processed)")
    parser.add_argument("--timezone", type=str, default=DEFAULT_TIMEZONE,
                        help=f"Timezone for timeslot bins and weekday classification (default: {DEFAULT_TIMEZONE})")
    args = parser.parse_args()

    output_dir = args.output_dir or (args.data_dir / "processed")
    output_dir.mkdir(parents=True, exist_ok=True)
    tz = ZoneInfo(args.timezone)

    print(f"Loading static feed {args.static_zip} ...")
    t0 = time.time()
    trip_map, shapes_by_id = load_static(args.static_zip)
    feed_info = load_feed_info(args.static_zip)
    print(f"  {len(trip_map):,} trips, {len(shapes_by_id):,} shapes ({time.time() - t0:.1f}s)")

    if args.dates:
        # resolve_day_file() picks whichever of <date>.jsonl / <date>.jsonl.gz
        # actually exists (uncompressed preferred), same as find_day_files()
        # below does for the no-explicit-dates case.
        day_files = [resolve_day_file(args.data_dir, d, ".jsonl") for d in args.dates]
    else:
        day_files = find_day_files(args.data_dir)  # <date>.jsonl or <date>.jsonl.gz, deduplicated by date
    if not day_files:
        print(f"No day files found in {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    agg = defaultdict(list)
    diff_kmh_all = []
    reject_totals = Counter()
    processed_days = []
    weekday_days = []

    for day_path in day_files:
        if not day_path.exists():
            print(f"{day_path.name}: not found, skipping", file=sys.stderr)
            continue

        date_str = date_from_path(day_path)
        is_weekday = date.fromisoformat(date_str).weekday() < 5  # Mon-Fri, per D4
        out_path = output_dir / f"segment_speeds_{date_str}.jsonl"

        t0 = time.time()
        stats = process_day(day_path, out_path, trip_map, shapes_by_id, tz,
                             agg if is_weekday else None, diff_kmh_all)
        elapsed = time.time() - t0

        processed_days.append(date_str)
        if is_weekday:
            weekday_days.append(date_str)
        reject_totals.update(stats.reject_counts)

        rejected = sum(stats.reject_counts.values())
        print(
            f"{date_str} ({'weekday' if is_weekday else 'weekend'}): "
            f"{stats.total_records:,} records | {stats.total_pairs:,} pairs | "
            f"{stats.samples_emitted:,} samples emitted | {rejected:,} rejected | "
            f"{elapsed:.1f}s"
        )

    validation_summary = {
        # Named so nobody downstream repeats the m/s-vs-km/h mistake this
        # comparison was originally computed with — see METHODOLOGY.md.
        "feed_speed_unit": "km/h despite the field being named speed_ms in the raw archive",
        "n_compared": len(diff_kmh_all),
        "median_abs_diff_kmh": round(statistics.median(diff_kmh_all), 3) if diff_kmh_all else None,
        "pct_over_threshold": (
            round(100 * sum(1 for d in diff_kmh_all if d > VALIDATION_DIFF_THRESHOLD_KMH) / len(diff_kmh_all), 3)
            if diff_kmh_all else None
        ),
    }

    typical = build_typical_weekday(
        agg, processed_days, weekday_days, args.static_zip.name, feed_info, reject_totals, validation_summary,
    )
    typical_path = output_dir / "typical_weekday.json"
    typical_path.write_text(json.dumps(typical, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n{typical_path}: {typical['segment_count']:,} (segment, timeslot) bins "
          f"from {len(weekday_days)} weekday day(s)")
    print(f"Validation vs feed speed_ms: {validation_summary}")


if __name__ == "__main__":
    main()
