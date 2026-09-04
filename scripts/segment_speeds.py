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
     match. A full rescan of the whole shape on every position terminates;
     it is just wasteful. Measured on 200,000 real positions from
     2026-08-31, projected against the shapes of the 2026-08-31 snapshot
     (median 316 points per shape, longest 2,219): 177 us per position for
     the full rescan against 27 us windowed, 6.5x. Over the 2,625,503
     positions of the five-day run recorded in typical_weekday.json that is
     roughly 8 minutes of projection instead of roughly 70 seconds. The
     earlier text here claimed a full rescan "does not finish in a
     reasonable time", which was an impression rather than a measurement.
  4. speed = delta(distance along shape) / delta(time) between consecutive
     projected positions.
  5. segment = fixed 200 m bin of distance-along-shape, keyed by
     (shape_key, segment_index) — shape_key already carries direction, see
     (1) and (10) below.
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
  10. shape identity is the geometry and nothing else (build_shape_key()
      below), never the shape_id: the agency moves its identifiers in both
      directions. On 2026-08-31 it kept 32 shape_ids and changed the
      geometry underneath them, which would make (shape_id, segment_index)
      silently pool samples from two different stretches of road into one
      median; on 2026-09-02 it kept the geometry and changed the ids,
      republishing A4500-A4503 as byte-identical copies of A1192/A3949/
      A1221/A2710, which would split one unchanged road into two series
      that never merge. A hash of the geometry alone survives both: shapes
      whose points are unchanged (98.3% of them across the two 2026-08
      snapshots) keep pooling samples, and only a real geometry change
      starts a new series. Both movements are counted in the output, by
      count_multi_geometry_shape_ids() and count_multi_id_geometries().
      See build_shape_key()'s docstring for the collision analysis and
      JOURNAL.md's 2026-08-31 and 2026-09-03 entries for the findings.
  11. static feed selection: the static-feed argument is either one zip
      (one feed for every day processed, unchanged from before this fix) or
      a directory of gtfs_<YYYY-MM-DD>.zip snapshots, one selected per
      processed day by find_static_snapshots()/pick_snapshot_for_day() — the
      latest snapshot whose filename date is <= the day being processed.
      Trip ids that snapshot does not know are resolved against the next
      snapshot chronologically (next_snapshot_after()), because a feed the
      agency republishes during a day is not in that day's own capture; on
      2026-09-02 that was 10,166 records, 1.4% of the day.
      The filename date is used rather than feed_info.txt's feed_start_date
      because it is what this archive actually observed the agency serving,
      not the publisher's own claim about when a schedule took effect; both
      are recorded in the output so a reader can check one against the
      other rather than take either on trust.
  12. schedule periods: (8) pools every Mon-Fri day into one median, which
      silently averages two timetables together the moment the agency
      publishes a new one — the autumn timetable of 2026-09-04 adds bus
      routes 191 and 192, moves route 10's 58 daily trips onto a new route
      190 running its four shapes and takes tram 8 from 261 trips a day to
      213, all of it taking effect 2026-09-08. Each day is
      therefore signed by the routes and trip counts its own snapshot
      schedules for it (schedule_signature() below); consecutive weekdays
      whose signatures differ by less than PERIOD_TOLERANCE_PCT of the
      trips share a period, a bigger jump starts a new one, and a weekday
      running a holiday's service leaves the median altogether
      (assign_schedule_periods()). The aggregation groups on
      (period_key, segment, timeslot), so days running different
      timetables never merge into one median. D4's Mon-Fri rule is
      untouched, only the grouping is finer.

Outputs (directory created if missing):
    <output-dir>/segment_speeds_<date>.jsonl  — one row per accepted sample
    <output-dir>/typical_weekday.json         — D4 aggregation + run metadata,
        including which static feed(s) were used, a per-day breakdown (so a
        snapshot going stale shows up as one day's reject counts climbing,
        not smeared into a grand total), how many shape_ids were seen
        carrying more than one distinct geometry across the feeds loaded,
        and the schedule_periods list — one entry per timetable observed,
        with "segments" keyed by period so two timetables' medians stay
        apart (see (12)).

Usage:
    python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip \\
        data/sofia --output-dir data/sofia/processed 2026-08-28 2026-08-31

    # no explicit dates: every <YYYY-MM-DD>.jsonl (or .jsonl.gz, transparently
    # decompressed — see deploy/sofia-compress.service) found in the data dir
    python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip data/sofia

    # a directory of gtfs_<YYYY-MM-DD>.zip snapshots instead of one zip: each
    # day is matched against the latest snapshot dated on or before it
    python3 scripts/segment_speeds.py data/sofia/static data/sofia
"""

import argparse
import bisect
import csv
import hashlib
import io
import json
import math
import re
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

# How far two weekdays' timetables may differ and still pool into one median.
# The measure is churn (schedule_churn): per route, the difference between the
# two days' scheduled trip counts, summed as absolute values, as a share of
# the reference day's trips. A weekday here publishes around 15,000 trips, so
# 0.5% is 75 trips of movement.
#
# What the archive shows is a gap, not a fitted line, and the honest version
# says so. Read each of the 267 weekdays from 2026-08-27 to 2027-09-04 out of
# the snapshot in force on it, which is what the pipeline does — the eight
# archived days by their own snapshot, the 259 later ones by 2026-09-04's,
# the eleven holiday weekdays below taken out — and consecutive weekdays
# produce four churn values and nothing else:
#   every day inside a period                        0 trips   0%
#   the autumn weekday timetable, 2026-09-08       470 trips   3.15%
#   the step to the school year, 2026-09-14        582 trips   3.88%
#   three trams starting and 10TM ending, 08-31    595 trips   4.07%
# So the feed bounds this threshold from above and says nothing about where
# inside (0, 3.15%) it belongs: no boundary it has published sits under
# 3.15%, and no day inside a period sits above 0. 0.5% is a sixth of the
# smallest real boundary, which leaves room for a feed that shifts a handful
# of trips without changing the timetable. On this archive it changes no
# grouping at all — exact signatures would give the same periods — so it is
# insurance against a feed that drifts, and METHODOLOGY.md calls it that
# rather than dressing it up as a value the data picked.
#
# Which snapshot answers for a past date decides two of those four numbers.
# Read the same 267 weekdays out of the 2026-09-04 snapshot alone and two
# more boundaries appear, 9.42% at the end of August and 0.60% between 2 and
# 3 September, both of them artefacts: the agency erodes calendar rows for
# dates already past, and that snapshot no longer carries 1,066 of the trips
# 2026-08-27 ran or 89 of 2026-09-02's (all of the 89 on route A53), which
# both days' own snapshots do carry. A pipeline reading history out of the
# latest feed would see a 0.60% step between two days that ran the same
# timetable, and 0.5% would split them. This one reads each day against the
# feed it started under, so the step never arises.
PERIOD_TOLERANCE_PCT = 0.5

# A weekday scheduling fewer trips than this share of the median weekday is
# running a holiday's service, not a new timetable. 2026-09-07 is a Monday
# carrying the same signature as the weekend either side of it: 10,149 trips
# against 14,907 on the Thursday before, 35% churn, where a real timetable
# change moves 3-4%. Over the same 267 weekdays eleven look like this, and
# their dates line up with Bulgarian public holidays. This line is pinned
# where the churn one is not: against a median of 15,595 trips the heaviest
# such day reaches 65.90% and the lightest ordinary weekday 93.67%, so any
# threshold between those two selects the same eleven days. Both edges are
# the pipeline's own reading; the upper one falls to 86.84% if 2026-08-27 is
# read out of the 2026-09-04 snapshot instead, for the erosion reason above.
# 80% sits inside the band either way. Such a day leaves the median entirely — D4 asks for a
# typical weekday — rather than founding a period of its own, which is what
# the threshold alone would have handed it: one day, and a median over one
# day is that day.
REDUCED_SERVICE_PCT = 80.0

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

def build_shape_key(points: list) -> str:
    """
    Content-addressed shape identity: the first 16 hex chars of a sha256
    over `points` (ordered (lat, lon) pairs), each formatted
    "{lat:.6f},{lon:.6f}" and joined with ";". The geometry alone, with no
    shape_id in it.

    Two republished-feed bugs shaped this key, one from each direction.
    2026-08-31: the agency kept 32 shape_ids and changed the geometry
    underneath them, so a plain shape_id pooled two different stretches of
    road into one median. Hashing the geometry fixed that. 2026-09-02: the
    agency kept the geometry and changed the shape_id, publishing A4500-
    A4503 as byte-identical copies of A1192/A3949/A1221/A2710 while leaving
    the originals in the feed. A key of f"{shape_id}@{geom_hash}" splits
    that into two series that describe one piece of asphalt and never
    merge, which is the same silent corruption as 2026-08-31 with the
    operands swapped. Only the geometry survives both: the publisher's
    identifiers are theirs to churn, the road is not.

    A bare hash now carries the whole identity, so it is 16 hex chars (64
    bits), not the 8 it was when a shape_id stood beside it: at ~1,900
    shapes per feed an 8-char key already sat at a ~4e-4 birthday collision
    probability with the shape_id there to catch it, and nothing catches it
    now.

    6 decimals (~11 cm at these latitudes) is far below any real geometry
    change, so it can't split one physical road into two keys just because a
    feed reformats its numbers, and it's far finer than the accuracy of the
    GPS-derived shape points themselves, so it can't fail to notice an
    actual change either.
    """
    digest = hashlib.sha256(
        ";".join(f"{lat:.6f},{lon:.6f}" for lat, lon in points).encode("utf-8")
    ).hexdigest()
    return digest[:16]


def load_static(gtfs_zip_path: Path) -> tuple:
    """
    Three maps: trip_id -> (route_id, shape_id, shape_key) from trips.txt,
    shape_key -> Shape from shapes.txt, and shape_key -> {shape_id, ...}.

    direction_id is blank for every trip in this feed (see CLAUDE.md) so it
    plays no role here — the shape geometry is the only thing that tells two
    directions or route variants apart, per task step 1.

    The third map exists because shape_key stopped carrying a shape_id (see
    build_shape_key): routes.txt/trips.txt still key route metadata by the
    bare id, and two ids can now legitimately land on one key — that is what
    the 2026-09-02 republish did to four routes. It is a set, not a single
    id, so nothing downstream has to pick a winner between them.
    """
    shape_points = defaultdict(list)

    with zipfile.ZipFile(gtfs_zip_path) as z:
        with z.open("trips.txt") as f:
            trip_rows = list(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")))
        with z.open("shapes.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")):
                shape_points[row["shape_id"]].append(
                    (int(row["shape_pt_sequence"]), float(row["shape_pt_lat"]), float(row["shape_pt_lon"]))
                )

    # Every shape_id referenced anywhere gets a key, even one with <2 points
    # or one missing from shapes.txt entirely (empty coords) — both cases
    # existed before this change too, and both must still surface downstream
    # as process_day's "shape_not_found" reject reason rather than silently
    # changing which counter fires.
    key_by_shape_id = {}
    shapes_by_key = {}
    shape_ids_by_key = defaultdict(set)
    for shape_id, pts in shape_points.items():
        pts.sort(key=lambda p: p[0])
        coords = [(lat, lon) for _, lat, lon in pts]
        key = build_shape_key(coords)
        key_by_shape_id[shape_id] = key
        shape_ids_by_key[key].add(shape_id)
        if len(coords) >= 2:
            shapes_by_key[key] = build_shape(coords)

    trip_map = {}
    for row in trip_rows:
        shape_id = row["shape_id"]
        if shape_id:
            if shape_id not in key_by_shape_id:
                # A shape_id referenced by a trip but absent from shapes.txt.
                # Every such id hashes the empty point list to one shared key
                # with no Shape behind it, which still surfaces downstream as
                # process_day's "shape_not_found" reject rather than a
                # different counter — the pre-existing behaviour.
                key_by_shape_id[shape_id] = build_shape_key([])
                shape_ids_by_key[key_by_shape_id[shape_id]].add(shape_id)
            trip_map[row["trip_id"]] = (row["route_id"], shape_id, key_by_shape_id[shape_id])

    return trip_map, shapes_by_key, dict(shape_ids_by_key)


def load_feed_info(gtfs_zip_path: Path) -> dict:
    """Best-effort feed_info.txt row, for stamping run metadata — absent in
    some GTFS feeds, so this must not be load-bearing for anything else."""
    try:
        with zipfile.ZipFile(gtfs_zip_path) as z:
            with z.open("feed_info.txt") as f:
                return next(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")), {})
    except (KeyError, zipfile.BadZipFile, StopIteration):
        return {}


# ─── Schedule periods (which timetable a day ran) ───────────────────────────

def load_schedule_calendar(gtfs_zip_path: Path) -> tuple:
    """
    (services_by_date, trip_services) for one static snapshot:
    "YYYYMMDD" -> {service_id, ...} from calendar_dates.txt, and every
    trips.txt row as a (service_id, route_id) pair.

    This feed publishes no calendar.txt — checked against every snapshot in
    the archive — so calendar_dates.txt is not a list of exceptions to a
    weekly pattern, it is the entire calendar, one row per service per date
    (499,841 rows over 1,119 dates in the 2026-09-04 snapshot). Only
    exception_type "1" (service added) is read; "2" removes a service from a
    base calendar that does not exist here, and no snapshot has ever carried
    one.
    """
    services_by_date = defaultdict(set)
    with zipfile.ZipFile(gtfs_zip_path) as z:
        with z.open("calendar_dates.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                if row["exception_type"] == "1":
                    services_by_date[row["date"]].add(row["service_id"])
        with z.open("trips.txt") as f:
            trip_services = [
                (row["service_id"], row["route_id"])
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            ]
    return dict(services_by_date), trip_services


def schedule_signature(services_by_date: dict, trip_services: list, date_str: str) -> tuple:
    """
    (signature_key, counts) for one calendar date read out of one static
    snapshot — the day's own snapshot (pick_snapshot_for_day), never the
    next_snapshot_after() fallback: the question here is which timetable the
    day ran under, and that is the feed it started under.

    counts is route_id -> trips scheduled that date. signature_key is the
    first 16 hex chars of a sha256 over the sorted (route_id, trip_count)
    pairs, the same content-addressing convention as build_shape_key(). It
    names exactly what the agency published that day, with no tolerance:
    which of these signatures are near enough to share a median is
    assign_schedule_periods()'s decision, and the raw key stays in the
    output per day so a reader sees the observation the grouping was made
    from, not only its result.

    Content-addressed for the same reason shape identity is: the agency
    renumbers trip_ids and service_ids between nightly rebuilds (2026-09-02,
    157 trips of four routes at 11:00 local) and a key built from those
    identifiers would declare a new timetable every time it did. Route ids
    and per-route trip counts sit still through a renumbering.

    ponytail: the ceiling is a pure retiming — the agency moves departure
    times but keeps every route's trip count identical — which hashes to the
    same key, and those two timetables would merge into one median.
    Detecting it needs stop_times.txt, 45.8 MB unpacked against trips.txt's
    3.2 MB, re-read for every archived day on every scheduled fetch. Not worth paying until a retiming-only
    change is actually observed; it is named as a limitation in
    METHODOLOGY.md instead.
    """
    active = services_by_date.get(date_str.replace("-", ""), frozenset())
    counts = Counter(route_id for service_id, route_id in trip_services if service_id in active)
    pairs = sorted(counts.items())
    key = hashlib.sha256(
        ";".join(f"{route_id},{n}" for route_id, n in pairs).encode("utf-8")
    ).hexdigest()[:16]
    return key, dict(pairs)


def schedule_churn(counts: dict, ref_counts: dict) -> int:
    """Trips separating two days' timetables: per route, the absolute
    difference in scheduled trips, summed over every route either day runs.
    A route that appears or vanishes contributes its whole trip count, so
    dropping a route is never free."""
    return sum(abs(counts.get(r, 0) - ref_counts.get(r, 0))
               for r in set(counts) | set(ref_counts))


def assign_schedule_periods(days: list,
                            tolerance_pct: float = PERIOD_TOLERANCE_PCT,
                            reduced_service_pct: float = REDUCED_SERVICE_PCT) -> list:
    """Give every day in `days` a "period_key" and an "excluded_from_median"
    reason, in place, and return the list. Each day is a dict carrying
    "date", "is_weekday", "signature_key" and "counts"; `days` is in date
    order.

    Weekends never enter the median (D4) and take no period. Of the
    weekdays, one scheduling less than reduced_service_pct of the median
    weekday's trips is dropped as reduced service: a holiday timetable is
    not a typical weekday, and it differs from its neighbours by enough that
    letting it through would found a period holding exactly one day.

    The survivors are walked in date order. The first is its period's
    reference and lends the period its signature_key; each following day
    joins while its churn against that reference stays within tolerance_pct
    of the reference's trips, and otherwise starts a new period as the new
    reference. Measured against the reference rather than the previous day,
    so a slow drift cannot walk a period arbitrarily far from the timetable
    its key names, one tolerated step at a time.
    """
    # Zero-trip weekdays are holes in the archive, not the city running
    # fewer buses, and they are kept out of the baseline: enough of them and
    # the median reaches zero, at which point `level` goes falsy and the
    # reduced-service test below silently stops firing for every day. A gap
    # in the static snapshots is exactly the failure this archive has already
    # lived through once (2026-08-28/29/30), so it must not disarm the rule.
    weekday_totals = sorted(t for t in (sum(d["counts"].values())
                                        for d in days if d["is_weekday"]) if t)
    level = statistics.median(weekday_totals) if weekday_totals else 0

    ref = None
    for d in days:
        total = sum(d["counts"].values())
        if not d["is_weekday"]:
            d["excluded_from_median"] = "weekend"
        elif not total:
            # Not a holiday: the snapshot this day was read against carries
            # no calendar rows for the date at all. Keeping the two apart
            # matters — one is the city running fewer buses, the other is a
            # hole in the archive.
            d["excluded_from_median"] = "no_calendar_rows"
        elif level and total < level * reduced_service_pct / 100:
            d["excluded_from_median"] = "reduced_service"
        else:
            d["excluded_from_median"] = None

        if d["excluded_from_median"]:
            d["period_key"] = None
            continue

        churn = schedule_churn(d["counts"], ref["counts"]) if ref else 0
        if ref is None or churn > sum(ref["counts"].values()) * tolerance_pct / 100:
            ref, churn = d, 0
            d["period_key"] = d["signature_key"]
        else:
            d["period_key"] = ref["period_key"]
        d["churn_vs_reference"] = churn
        d["period_reference_date"] = ref["date"]
    return days


def build_schedule_periods(days: list, bin_counts: dict) -> list:
    """
    One entry per period over days already run through
    assign_schedule_periods(), with `bin_counts` as period_key -> number of
    (segment, timeslot) bins in that period's median.

    Only days that entered the median carry a period_key, so only they
    appear here; a weekend or a reduced-service day names its reason in
    day_breakdown instead. reference_date is the day whose signature the
    period is named after, which a run processing part of the archive can
    have assigned against without processing (see main()); route_count and
    trip_count then describe the earliest day of the period this run did
    process, which the tolerance holds within PERIOD_TOLERANCE_PCT of it.
    max_churn_vs_reference says how far the loosest day in the period sat
    from the reference, so a reader can see how tight the grouping actually
    was rather than trusting the threshold.
    """
    by_key = {}
    for d in days:
        key = d.get("period_key")
        if key is None:
            continue
        entry = by_key.get(key)
        if entry is None:
            entry = by_key[key] = {
                "period_key": key,
                "reference_date": d.get("period_reference_date", d["date"]),
                "days_in_median_mon_fri": [],
                "first_date": d["date"],
                "last_date": d["date"],
                "route_count": len(d["counts"]),
                "trip_count": sum(d["counts"].values()),
                "max_churn_vs_reference": 0,
                "bin_count": bin_counts.get(key, 0),
            }
        entry["days_in_median_mon_fri"].append(d["date"])
        entry["first_date"] = min(entry["first_date"], d["date"])
        entry["last_date"] = max(entry["last_date"], d["date"])
        entry["max_churn_vs_reference"] = max(entry["max_churn_vs_reference"],
                                              d.get("churn_vs_reference", 0))
    return sorted(by_key.values(), key=lambda e: (e["first_date"], e["period_key"]))


def sha256_of_file(path: Path) -> str:
    """Plain sha256 of a static feed zip. Not generate_manifest.py's
    sha256_of(): that one exists to hash a day file transparently through
    optional gzip compression (deploy/sofia-compress.service); a static zip
    is never stored gzipped, so that indirection has nothing to do here."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ─── Static snapshot selection (per-processed-day feed matching) ───────────

SNAPSHOT_NAME_RE = re.compile(r"^gtfs_(\d{4}-\d{2}-\d{2})(?:T\d{4})?\.zip$")


def find_static_snapshots(static_dir: Path) -> list:
    """[(snapshot_date, path), ...] in chronological order, for every file
    directly under static_dir matching gtfs_<YYYY-MM-DD>.zip or
    gtfs_<YYYY-MM-DD>T<HHMM>.zip exactly. A full-match regex (not a glob or
    a prefix check) is what keeps this from picking up the unpacked
    gtfs_2026-08-27/ sibling directory or .DS_Store that live alongside the
    zips on disk.

    The optional T<HHMM> is the second and later capture of one local day
    (archive_static_feed.py), added after the agency republished mid-day on
    2026-09-02 and the daily-only archive missed it for 21 hours. Sorting by
    filename rather than by the parsed date is what orders those correctly:
    "." (0x2E) sorts before "T" (0x54), so the plain morning snapshot always
    precedes that day's intra-day ones."""
    snapshots = []
    for p in static_dir.iterdir():
        if not p.is_file():
            continue
        m = SNAPSHOT_NAME_RE.match(p.name)
        if m:
            snapshots.append((m.group(1), p))
    snapshots.sort(key=lambda pair: pair[1].name)
    return snapshots


def pick_snapshot_for_day(snapshots: list, date_str: str) -> tuple:
    """Returns (snapshot_date, path, is_fallback) for the snapshot that
    should stand in for `date_str`: the latest one dated on or before it —
    the filename date is when this archive actually observed the agency
    serving that feed, which feed_start_date only asserts. `snapshots` must
    already be sorted by date (find_static_snapshots() guarantees this).

    is_fallback is True only when date_str precedes every snapshot — there
    is no feed on record from before the archive started, so the earliest
    available one is used, but the caller must record that as a fallback
    rather than let it look like a real date match.

    One date can hold more than one snapshot since archive_static_feed.py
    started capturing hourly (gtfs_<date>.zip plus gtfs_<date>T<HHMM>.zip),
    and which of them is "the day's own feed" depends on whose date it is.
    For the day itself it is the first capture of that morning: that is the
    feed the day started under, and a republish later the same day belongs
    in the intra-day sibling, which next_snapshot_after() then hands to
    process_day() as the fallback for exactly the trips the morning feed
    cannot resolve. Taking the intra-day capture as the primary instead
    would score the whole morning against a feed that did not exist yet and
    leave the morning's own snapshot behind the fallback pointer, where
    nothing would ever read it. For an earlier date it is the last capture
    of that date, because by the processed day every republish of it had
    already happened.
    """
    candidates = [s for s in snapshots if s[0] <= date_str]
    if candidates:
        latest_date = candidates[-1][0]
        same_date = [s for s in candidates if s[0] == latest_date]
        snap_date, path = same_date[0] if latest_date == date_str else same_date[-1]
        return snap_date, path, False
    snap_date, path = snapshots[0]
    return snap_date, path, True


def next_snapshot_after(snapshots: list, path: Path):
    """The snapshot immediately after `path` in `snapshots` (which
    find_static_snapshots() returns in chronological order), or None if
    `path` is the newest one on record.

    A day is matched to the newest feed captured on or before it, so a feed
    the agency republishes *during* that day is not in the day's own
    snapshot — it is in the next one. On 2026-09-02 that cost 10,166 records
    (1.4% of the day), rejected as trip_not_in_static: the agency renumbered
    157 trips of four routes around 11:00 local, the RT stream started
    emitting the new trip_ids immediately, and every one of them turned up
    in the next morning's capture. process_day() resolves against this
    second feed only for trip_ids the day's own feed does not know, so the
    day is still scored against the feed it was actually collected under
    wherever that feed has an answer."""
    for i, (_, p) in enumerate(snapshots):
        if p == path:
            return snapshots[i + 1][1] if i + 1 < len(snapshots) else None
    return None


def count_multi_geometry_shape_ids(shape_ids_by_key: dict) -> int:
    """How many distinct bare shape_ids appear under more than one distinct
    geometry key among the feeds loaded in one run. This is the number that
    produced the 2026-08-31 finding — 32 shape_ids reused across a
    republished feed with different geometry underneath them — and it
    belongs in typical_weekday.json permanently, not only in a one-off
    investigation."""
    keys_by_shape_id = defaultdict(set)
    for key, shape_ids in shape_ids_by_key.items():
        for shape_id in shape_ids:
            keys_by_shape_id[shape_id].add(key)
    return sum(1 for keys in keys_by_shape_id.values() if len(keys) > 1)


def count_multi_id_geometries(shape_ids_by_key: dict) -> int:
    """The mirror of count_multi_geometry_shape_ids(): how many distinct
    geometries are published under more than one shape_id. This is the
    2026-09-02 finding — A4500-A4503 shipped as byte-identical copies of
    A1192/A3949/A1221/A2710 with the originals left in the feed — and it is
    exactly what the shape_id in the old aggregation key turned into a
    permanent split. Zero here means the publisher's ids and its geometries
    agree; a rising number means it is churning identifiers again."""
    return sum(1 for shape_ids in shape_ids_by_key.values() if len(shape_ids) > 1)


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
    # Records (and distinct trips) the day's own static feed could not
    # resolve and the next captured feed could — a mid-day republish, see
    # next_snapshot_after(). Not a reject reason: these records are kept.
    # Worth its own field because a silently rising number means the agency
    # is republishing inside the collection day often enough that the
    # archive cadence in deploy/sofia-static-archive.timer is too slow.
    records_from_next_feed: int = 0
    trips_from_next_feed: set = field(default_factory=set)


def process_group(
    recs: list,
    shape: Shape,
    route_id: str,
    shape_id: str,
    shape_key: str,
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
                            # Bare shape_id: a human reading the raw archive
                            # shouldn't have to strip a geometry hash to
                            # recognize a shape. shape_key is the same value
                            # the aggregation below actually groups on.
                            "shape_id": shape_id,
                            "shape_key": shape_key,
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
                            agg[(shape_key, segment_index, slot)].append(speed)
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
    shapes_by_key: dict,
    tz: ZoneInfo,
    agg: Optional[dict],
    diff_kmh_all: list,
    next_trip_map: Optional[dict] = None,
) -> DayStats:
    stats = DayStats()
    groups = defaultdict(list)
    # Consulted only for trip_ids the day's own feed doesn't know — see
    # next_snapshot_after(). Empty when there is no later snapshot, which
    # makes the lookup below a no-op rather than a special case.
    next_trip_map = next_trip_map or {}

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
            if route_shape is None and trip_id:
                route_shape = next_trip_map.get(trip_id)
                if route_shape is not None:
                    stats.records_from_next_feed += 1
                    stats.trips_from_next_feed.add(trip_id)

            if route_shape is None or not vehicle_id:
                stats.reject_counts["trip_not_in_static"] += 1
                continue

            groups[(vehicle_id, trip_id)].append(
                (rec["snapshot_ts"], rec["lat"], rec["lon"], rec.get("speed_ms"))
            )

    date_str = date_from_path(day_path)
    with out_path.open("w", encoding="utf-8") as out_f:
        for (vehicle_id, trip_id), recs in groups.items():
            route_id, shape_id, shape_key = trip_map.get(trip_id) or next_trip_map[trip_id]
            shape = shapes_by_key.get(shape_key)
            if shape is None:
                stats.reject_counts["shape_not_found"] += len(recs)
                continue
            recs.sort(key=lambda r: r[0])
            process_group(recs, shape, route_id, shape_id, shape_key, vehicle_id, trip_id, tz, out_f, agg, diff_kmh_all, stats, date_str)

    return stats


# ─── Aggregation (D4) ────────────────────────────────────────────────────────

def build_typical_weekday(
    agg_by_period: dict,
    processed_days: list,
    weekday_days: list,
    static_feeds: list,
    day_breakdown: list,
    reject_totals: Counter,
    validation_summary: dict,
    multi_geometry_shape_id_count: int,
    multi_id_geometry_count: int,
    schedule_periods: list,
) -> dict:
    # One median set per schedule period (see assign_schedule_periods): the
    # timetable a day ran is part of what a "typical weekday" is, and a bin
    # from before a timetable change has no business being pooled with one
    # from after it. The period key is the outer level rather than a fourth
    # field in the bin key, so a reader (and export_web.py) can take one
    # period's whole payload without filtering the other periods out of it.
    segments = {}
    for period_key, agg in agg_by_period.items():
        period_segments = {}
        for (shape_key, segment_index, slot), speeds in agg.items():
            # Deliberately still a 3-field "<field>|<segment_index>|<timeslot>"
            # string, shape_key occupying the first field — export_web.py does
            # key.split("|") and that call site keeps working untouched.
            # shape_key is 16 hex chars (see build_shape_key) and so contains no
            # "|", which is what keeps this from silently becoming a 4-field key.
            period_segments[f"{shape_key}|{segment_index}|{slot}"] = {
                "median_speed_ms": round(statistics.median(speeds), 3),
                "n_samples": len(speeds),
            }
        segments[period_key] = period_segments

    return {
        "generated_at": datetime.now(dt_timezone.utc).isoformat(),
        # Replaces the old scalar static_feed_file/version/valid fields: with
        # more than one static feed potentially in play across the days
        # processed, a single scalar can't describe them, and a run that
        # only ever loads one feed still gets a (one-element) list here
        # rather than two different shapes depending on how many were used.
        "static_feeds": static_feeds,
        "days_processed": processed_days,
        "days_in_median_mon_fri": weekday_days,
        # Days processed but deliberately kept out of the median, each with
        # its reason (see assign_schedule_periods). Weekends are the routine
        # case; a weekday here means a holiday timetable, or a date its own
        # snapshot has no calendar rows for.
        "days_excluded_from_median": [
            {"date": e["date"], "reason": e.get("excluded_from_median")}
            for e in day_breakdown if e.get("excluded_from_median")
        ],
        # One entry per timetable observed across the days processed, each
        # naming the days that ran it and how many bins its median holds.
        # "segments" is keyed by the same period_key.
        "schedule_periods": schedule_periods,
        # Per-day breakdown, because a grand total hides exactly the signal
        # that matters here: a snapshot going stale shows up as *one day's*
        # trip_not_in_static climbing, which reject_counts_total below
        # averages away across every other day.
        "day_breakdown": day_breakdown,
        "thresholds": {
            "segment_length_m": SEGMENT_LENGTH_M,
            "timeslot_minutes": TIMESLOT_MINUTES,
            "max_time_gap_sec": MAX_TIME_GAP_SEC,
            "max_speed_kmh": MAX_SPEED_KMH,
            "backward_tolerance_m": BACKWARD_TOLERANCE_M,
            "search_margin_m": SEARCH_MARGIN_M,
            "validation_diff_threshold_kmh": VALIDATION_DIFF_THRESHOLD_KMH,
            "schedule_period_tolerance_pct": PERIOD_TOLERANCE_PCT,
            "reduced_service_threshold_pct": REDUCED_SERVICE_PCT,
        },
        "reject_counts_total": dict(reject_totals),
        "validation_vs_feed_speed_ms": validation_summary,
        # How many shape_ids carried more than one distinct geometry hash
        # across the feeds loaded this run — the number that surfaced the
        # 2026-08-31 republished-feed finding (see CLAUDE.md). Kept in the
        # output permanently rather than left as a one-off chat finding.
        "multi_geometry_shape_id_count": multi_geometry_shape_id_count,
        # The mirror image, and the 2026-09-02 finding: how many geometries
        # the agency published under more than one shape_id. Under the old
        # f"{shape_id}@{geom_hash}" key each of those was a permanent split
        # between two series describing one piece of road.
        "multi_id_geometry_count": multi_id_geometry_count,
        # Bins summed across periods: a bin observed under two timetables
        # counts twice here, because that is two medians, not one. Both
        # numbers are published because either alone reads as "how much data
        # is in this file" and they answer different questions — how many
        # medians it holds, and how much of the network it covers.
        "segment_count": sum(len(s) for s in segments.values()),
        "distinct_segment_count": len(set().union(*segments.values())) if segments else 0,
        "segments": segments,
    }


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("static_source", type=Path,
                        help="GTFS static zip (one feed for every day processed), or a directory of "
                             "gtfs_<YYYY-MM-DD>.zip snapshots to pick from per day (see pick_snapshot_for_day)")
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

    # None (single zip, same feed for every day -- unchanged prior behaviour)
    # or a date-sorted [(snapshot_date, path), ...] to pick from per day.
    if args.static_source.is_dir():
        snapshots = find_static_snapshots(args.static_source)
        if not snapshots:
            print(f"No gtfs_<YYYY-MM-DD>.zip snapshots found in {args.static_source}", file=sys.stderr)
            sys.exit(1)
    else:
        snapshots = None

    # Cache keyed by path rather than reloaded per day. shapes_by_key is
    # content-addressed (build_shape_key), so merging several feeds' shapes
    # into one dict is safe and self-deduplicating -- two feeds sharing 98.3%
    # of their geometry (the observed 2026-08-27/2026-08-31 case) cost barely
    # more to hold in memory than one.
    loaded_feeds = {}         # path -> (trip_map, shapes_by_key, feed_info)
    loaded_calendars = {}     # path -> (services_by_date, trip_services)
    all_shapes_by_key = {}    # union across every feed loaded this run
    all_shape_ids_by_key = defaultdict(set)

    def load_feed(path: Path) -> tuple:
        if path not in loaded_feeds:
            print(f"Loading static feed {path} ...")
            t0 = time.time()
            trip_map, shapes_by_key, shape_ids_by_key = load_static(path)
            feed_info = load_feed_info(path)
            print(f"  {len(trip_map):,} trips, {len(shapes_by_key):,} shapes ({time.time() - t0:.1f}s)")
            loaded_feeds[path] = (trip_map, shapes_by_key, feed_info)
            all_shapes_by_key.update(shapes_by_key)
            for key, shape_ids in shape_ids_by_key.items():
                all_shape_ids_by_key[key].update(shape_ids)
        return loaded_feeds[path]

    def load_calendar(path: Path) -> tuple:
        # Cached per feed, not per day: calendar_dates.txt is half a million
        # rows and several days usually share one snapshot.
        if path not in loaded_calendars:
            loaded_calendars[path] = load_schedule_calendar(path)
        return loaded_calendars[path]

    if args.dates:
        # resolve_day_file() picks whichever of <date>.jsonl / <date>.jsonl.gz
        # actually exists (uncompressed preferred), same as find_day_files()
        # below does for the no-explicit-dates case. Sorted here too, to
        # match find_day_files()'s ordering -- a day_breakdown or a snapshot
        # cache shouldn't depend on the order dates were typed on the CLI.
        day_files = [resolve_day_file(args.data_dir, d, ".jsonl") for d in sorted(args.dates)]
    else:
        day_files = find_day_files(args.data_dir)  # <date>.jsonl or <date>.jsonl.gz, deduplicated by date, sorted
        # Today's file is still being written and its local copy only reaches
        # the last rsync, so folding it into the median produces an aggregate
        # that changes under a reader who re-runs this an hour later. The
        # first typical_weekday.json was built this way, over 551,016 of the
        # 730,167 records its own day eventually held. Name a date explicitly
        # to process the day in progress anyway.
        today = datetime.now(tz).date().isoformat()
        skipped = [p for p in day_files if date_from_path(p) >= today]
        day_files = [p for p in day_files if date_from_path(p) < today]
        for p in skipped:
            print(f"{p.name}: skipped, the day is still in progress "
                  f"(pass {date_from_path(p)} explicitly to include it)", file=sys.stderr)
    if not day_files:
        print(f"No day files found in {args.data_dir}", file=sys.stderr)
        sys.exit(1)

    # Which timetable a day ran is a fact about the archive, not about this
    # invocation. The period key names a directory in the web export and
    # travels into published JSON, so it has to come out the same however the
    # run was called: the plan below spans every closed day on disk plus
    # anything named explicitly, and the loop after it processes only the days
    # actually requested. Without this, `segment_speeds.py static data
    # 2026-09-01` makes 09-01 the reference of its own period and hands the
    # same timetable a different key than the full run does.
    requested = {date_from_path(p) for p in day_files}
    plan_files = sorted(set(day_files) | set(find_day_files(args.data_dir)),
                        key=date_from_path)
    today = datetime.now(tz).date().isoformat()
    plan_files = [p for p in plan_files
                  if date_from_path(p) < today or date_from_path(p) in requested]

    # period_key -> {(shape_key, segment_index, timeslot): [speed, ...]}. One
    # aggregate per timetable, so days running different schedules never
    # merge into one median (see assign_schedule_periods).
    agg_by_period = defaultdict(lambda: defaultdict(list))
    diff_kmh_all = []
    reject_totals = Counter()
    processed_days = []
    weekday_days = []
    day_breakdown = []
    day_plan = []                       # one dict per day, filled by the pre-pass below
    feed_days_used = defaultdict(list)  # path -> [date_str, ...]

    # Which timetable each day ran is settled before any day is processed:
    # dropping a reduced-service weekday needs the median trip count over
    # every weekday in the archive, so no day can be routed to an aggregate
    # until all of them have been read. Cheap — the calendar is cached per
    # snapshot and several days usually share one.
    for day_path in plan_files:
        if not day_path.exists():
            print(f"{day_path.name}: not found, skipping", file=sys.stderr)
            continue

        date_str = date_from_path(day_path)

        if snapshots is None:
            static_path, is_fallback, next_path = args.static_source, False, None
        else:
            _, static_path, is_fallback = pick_snapshot_for_day(snapshots, date_str)
            if is_fallback:
                print(f"{date_str}: precedes every static snapshot on record, "
                      f"falling back to the earliest one ({static_path.name})", file=sys.stderr)
            next_path = next_snapshot_after(snapshots, static_path)

        # The day's own snapshot, never next_path: which timetable the day
        # ran is a fact about the feed it started under.
        signature_key, counts = schedule_signature(*load_calendar(static_path), date_str)
        day_plan.append({
            "date": date_str,
            "day_path": day_path,
            "static_path": static_path,
            "is_fallback": is_fallback,
            "next_path": next_path,
            "is_weekday": date.fromisoformat(date_str).weekday() < 5,  # Mon-Fri, per D4
            "signature_key": signature_key,
            "counts": counts,
            "processed": date_str in requested,
        })

    assign_schedule_periods(day_plan)
    for d in day_plan:
        if d["excluded_from_median"] and d["is_weekday"]:
            print(f"{d['date']}: weekday scheduling {sum(d['counts'].values()):,} trips "
                  f"({d['excluded_from_median']}), kept out of the median", file=sys.stderr)

    for d in day_plan:
        if not d["processed"]:
            continue
        date_str, static_path, next_path = d["date"], d["static_path"], d["next_path"]
        period_key = d["period_key"]

        trip_map, shapes_by_key, _ = load_feed(static_path)
        next_trip_map = {}
        if next_path is not None:
            next_trip_map, next_shapes, _ = load_feed(next_path)
            # Merged, not substituted: a trip resolved from the later feed
            # still has to find its geometry, and shapes_by_key is
            # content-addressed so the union can't collide (build_shape_key).
            shapes_by_key = {**next_shapes, **shapes_by_key}

        out_path = output_dir / f"segment_speeds_{date_str}.jsonl"

        t0 = time.time()
        stats = process_day(d["day_path"], out_path, trip_map, shapes_by_key, tz,
                            agg_by_period[period_key] if period_key else None, diff_kmh_all,
                            next_trip_map)
        elapsed = time.time() - t0

        processed_days.append(date_str)
        if period_key:
            weekday_days.append(date_str)
        reject_totals.update(stats.reject_counts)
        feed_days_used[static_path].append(date_str)
        day_breakdown.append({
            "date": date_str,
            "static_feed_file": static_path.name,
            "static_feed_is_fallback": d["is_fallback"],
            # The feed captured after this day's own, consulted only for
            # trips the day's feed doesn't know (next_snapshot_after). Both
            # counts are reported even when zero, so "the agency didn't
            # republish mid-day" and "this run never had a later feed to
            # ask" stay distinguishable in the output.
            "next_static_feed_file": next_path.name if next_path else None,
            # Two different facts, both recorded for every day including
            # the ones the median skips. schedule_signature_key is the raw
            # observation: exactly which routes ran how many trips that
            # date, hashed. schedule_period_key is the median it was pooled
            # into, null for a day left out — see excluded_from_median.
            "schedule_signature_key": d["signature_key"],
            "schedule_period_key": period_key,
            "excluded_from_median": d["excluded_from_median"],
            "scheduled_trip_count": sum(d["counts"].values()),
            "trips_resolved_from_next_feed": len(stats.trips_from_next_feed),
            "records_resolved_from_next_feed": stats.records_from_next_feed,
            "records": stats.total_records,
            "samples_emitted": stats.samples_emitted,
            "reject_counts": dict(stats.reject_counts),
        })

        rejected = sum(stats.reject_counts.values())
        if stats.records_from_next_feed:
            print(f"{date_str}: {stats.records_from_next_feed:,} records on "
                  f"{len(stats.trips_from_next_feed)} trip(s) unknown to {static_path.name}, "
                  f"resolved from {next_path.name} (feed republished during the day)")
        print(
            f"{date_str} ({'weekday' if d['is_weekday'] else 'weekend'}, static={static_path.name}): "
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

    static_feeds_meta = []
    for path, (_, _, feed_info) in loaded_feeds.items():
        static_feeds_meta.append({
            "file": path.name,
            "sha256": sha256_of_file(path),
            "feed_version": feed_info.get("feed_version"),
            "feed_start_date": feed_info.get("feed_start_date"),
            "feed_end_date": feed_info.get("feed_end_date"),
            "days_processed": feed_days_used[path],
        })

    multi_geometry_shape_id_count = count_multi_geometry_shape_ids(all_shape_ids_by_key)
    multi_id_geometry_count = count_multi_id_geometries(all_shape_ids_by_key)

    schedule_periods = build_schedule_periods(
        [d for d in day_plan if d["processed"]],
        {k: len(a) for k, a in agg_by_period.items()},
    )

    typical = build_typical_weekday(
        agg_by_period, processed_days, weekday_days, static_feeds_meta, day_breakdown,
        reject_totals, validation_summary, multi_geometry_shape_id_count,
        multi_id_geometry_count, schedule_periods,
    )
    typical_path = output_dir / "typical_weekday.json"
    typical_path.write_text(json.dumps(typical, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"\n{typical_path}: {typical['distinct_segment_count']:,} distinct (segment, timeslot) "
          f"bins, {typical['segment_count']:,} counted per period, "
          f"from {len(weekday_days)} weekday day(s) in {len(schedule_periods)} schedule period(s)")
    for p in schedule_periods:
        print(f"  {p['period_key']}  {p['first_date']}..{p['last_date']}  "
              f"{p['route_count']} routes / {p['trip_count']:,} trips  "
              f"{len(p['days_in_median_mon_fri'])} weekday day(s), {p['bin_count']:,} bins  "
              f"max churn {p['max_churn_vs_reference']:,} trips  "
              f"[{', '.join(p['days_in_median_mon_fri'])}]")
    print(f"Validation vs feed speed_ms: {validation_summary}")
    if multi_geometry_shape_id_count:
        print(f"WARNING: {multi_geometry_shape_id_count} shape_id(s) carried more than one distinct "
              f"geometry across the {len(loaded_feeds)} static feed(s) loaded this run", file=sys.stderr)


if __name__ == "__main__":
    main()
