#!/usr/bin/env python3
"""
Component D input (CLAUDE.md section 3; the last open item under section 8's
"Нарезка на временные окна"): turn segment_speeds.py's output into the
static files a frontend can actually fetch under D7 ("static site, no
backend"). No frontend code lives here or is written by this script.

Why this needed a decision at all: typical_weekday.json is one 52 MB JSON
object keyed by "shape_id|segment_index|timeslot" — every one of its 630k
entries pays for its own repeated shape_id string, and a browser has to
download and parse the whole thing before a timeline slider can show a
single instant. D7 ("static, no backend") was decided before this volume
was known; the format below is what makes that decision still work.

Format decided here:
  - Geometry (shape_id, segment_index -> two endpoint coordinates) is
    written once per export and referenced by *index* from every timeslot
    file, instead of repeating shape_id/segment_index/lat/lon in every bin.
    A segment's geometry is the straight chord between the along-shape
    positions at segment_index*200m and (segment_index+1)*200m — not the
    true polyline inside that 200m span. See known_limitations in the
    manifest this script writes.
  - One small JSON per 15-minute timeslot (Component D feature 1, the
    timeline slider), so scrubbing the timeline only fetches the slots it
    actually visits, not the whole corpus.
  - Parallel arrays, not arrays of objects: a timeslot file is three flat
    arrays of equal length (segment index, speed, sample count), so a bin
    doesn't pay for repeating its own key names.
  - Speed ships as an integer km/h — this is a map colour scale, not the
    scientific record. The float m/s and every sample it came from still
    live in segment_speeds_<date>.jsonl and typical_weekday.json, published
    as-is (D5).
  - Route metadata (route ids, short names, GTFS route_type) is stored once
    per shape rather than per segment, so the transport-type filter
    (Component D feature 3) has something to key on without inflating the
    per-segment arrays.
  - n_samples ships with every surviving bin, unconditionally. A threshold
    (MIN_SAMPLES_DEFAULT) already throws out the thinnest bins, but n=2 and
    n=48 still look identical on a coloured line unless the client can see
    the count — D4 and section 6's "name the limitation before someone else
    does", applied to this export rather than only to prose.

Two source shapes converge on one internal shape: `--day YYYY-MM-DD`
re-aggregates that day's own segment_speeds_<date>.jsonl (median per bin,
one day) instead of loading typical_weekday.json, for the day switcher
(Component D feature 2). Both paths produce the same
{(shape_key, segment_index, timeslot): (median_speed_ms, n_samples)} mapping
before threshold/geometry/writing — there is exactly one code path for
turning that mapping into files.

shape_key, not shape_id: segment_speeds.py keys everything by a
content-addressed shape_key (shape_id plus a geometry hash — see its
build_shape_key()) since a republished static feed can keep a shape_id
while changing its geometry, and this script inherits that identity so a
bin's geometry always resolves against the *correct* polyline version, not
whichever one happened to load last. Route metadata (routes.txt/trips.txt)
is still keyed by the bare shape_id, so build_geometry() converts back via
shape_id_from_key() at that one lookup, not by treating shape_key as if it
were the bare id.

Static feed argument: either one zip (one feed for every bin, unchanged
prior behaviour) or a directory of gtfs_<YYYY-MM-DD>.zip snapshots — see
load_static_sources(). Unlike segment_speeds.py this script doesn't need to
pick one snapshot per calendar day (that selection already happened
upstream, when segment_speeds.py produced the bins this script reads); it
only needs every shape_key that could appear in its input to resolve to a
geometry and a route, so a directory's snapshots are all loaded and merged
up front.

Usage:
    python3 scripts/export_web.py data/sofia/static/gtfs_2026-08-27.zip data/sofia
    python3 scripts/export_web.py data/sofia/static/gtfs_2026-08-27.zip data/sofia \\
        --day 2026-08-28

    # a directory of gtfs_<YYYY-MM-DD>.zip snapshots instead of one zip
    python3 scripts/export_web.py data/sofia/static data/sofia
"""

import argparse
import bisect
import csv
import gzip
import io
import json
import math
import shutil
import statistics
import sys
import time
import zipfile
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# segment_speeds.py lives next to this file; reuse its shape-loading and
# projection geometry instead of re-deriving it (it's already reviewed and
# tested). Explicit path insert so this also works under pytest, which
# doesn't always add a script's own directory to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from segment_speeds import (  # noqa: E402
    BACKWARD_TOLERANCE_M,
    EARTH_RADIUS_M,
    MAX_SPEED_KMH,
    MAX_TIME_GAP_SEC,
    SEARCH_MARGIN_M,
    SEGMENT_LENGTH_M,
    TIMESLOT_MINUTES,
    Shape,
    find_static_snapshots,
    load_static,
    shape_id_from_key,
)

# ─── Constants: web export decisions (this file's job to pick, per task) ───

# A bin built from a single sample isn't a median, it's one raw pairwise
# speed reading relabeled as an aggregate — there is nothing for a median to
# be robust *to* with one point, which is the entire justification D4 gives
# for using a median at all. n>=2 is the smallest cutoff that means "more
# than one observation agreed", not the smallest cutoff that looks tidy.
# The cost of raising it is worth stating with the archive it was measured
# against, because it moves as weekdays accumulate. Counted 2026-09-01 over
# the 687,014 bins in data/sofia/processed/typical_weekday.json, built from
# the three weekdays 2026-08-27, 2026-08-28 and 2026-08-31: n>=2 keeps
# 422,687 bins (61.5%), n>=3 keeps 220,975 (32.2%), n>=5 keeps 72,027
# (10.5%). n>=3 is the smallest count where a median actually insulates
# against either extreme, and both it and n>=5 are defensible choices, but
# on this little history they discard most of the network. Re-count before
# quoting these; an earlier version of this comment carried 26.5% and 7.6%
# from a smaller archive and drifted out of date without saying so.
# n_samples still ships with every surviving bin (see module docstring), so
# a thin median is the client's problem to render distinctly, not this
# cutoff's problem to hide. Revisit the default as more weekdays accumulate;
# nothing about the file format below needs to change to raise it later.
MIN_SAMPLES_DEFAULT = 2

# ~1.11 m of latitude and ~0.82 m of longitude at Sofia's ~42.7 deg N — below
# the accuracy of the consumer GPS units that produced the underlying feed,
# so a 6th decimal would double the digit-string cost for precision the
# source data doesn't have. A 4th decimal (~11 m) is comparable to a lane
# width and would visibly misplace a segment against its own street.
COORD_DECIMALS = 5

# Below this calendar-day coverage_pct (from generate_manifest.py's output),
# or if the day was still in progress when its manifest was built, a day
# going into the median gets flagged as incomplete in this export's own
# manifest. CLAUDE.md section 9 notes coverage_pct can be inflated at day
# boundaries by an open defect in analyze_gaps/build_manifest — a day
# flagged here is genuinely thin; a day *not* flagged is not proof of a
# full day, only the best signal currently on disk.
INCOMPLETE_COVERAGE_PCT = 99.0


# ─── Loading the two possible sources into one shape ────────────────────────

def load_typical_weekday_bins(path: Path) -> tuple:
    """typical_weekday.json's "shape_key|segment_index|timeslot" string keys
    -> {(shape_key, segment_index, timeslot): (median_speed_ms, n_samples)}.
    shape_key contains "@" (see segment_speeds.build_shape_key) but never
    "|", so split("|") still yields exactly 3 fields. Returns (bins,
    raw_json) so the caller can also pull provenance fields (days_processed,
    static_feeds, ...) out of the same file."""
    raw = json.loads(path.read_text(encoding="utf-8"))
    bins = {}
    for key, v in raw["segments"].items():
        shape_key, seg_idx, slot = key.split("|")
        bins[(shape_key, int(seg_idx), slot)] = (v["median_speed_ms"], v["n_samples"])
    return bins, raw


def aggregate_day_bins(segment_speeds_path: Path) -> dict:
    """Median speed_ms + sample count per (shape_key, segment_index,
    timeslot) for one day's own raw samples. segment_speeds.py only ever
    pools Mon-Fri days into typical_weekday.json — a single day's own
    aggregate doesn't exist on disk anywhere, so the day switcher (Component
    D feature 2) needs this computed here. Keyed on shape_key, not the
    bare shape_id also present in each row: the day switcher must not
    re-introduce the exact bug typical_weekday.json's aggregation was fixed
    for by pooling two different geometries under one shape_id."""
    agg = defaultdict(list)
    with segment_speeds_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue  # torn line from a mid-write rsync pull, same tolerance as the rest of the pipeline
            key = (rec["shape_key"], rec["segment_index"], rec["timeslot"])
            agg[key].append(rec["speed_ms"])
    return {k: (statistics.median(v), len(v)) for k, v in agg.items()}


def segment_pairs(bins: dict) -> set:
    """(shape_key, segment_index) pairs referenced by `bins` — shape_key, not
    the bare shape_id, so two geometry versions of what was once one
    shape_id stay two distinct segments here too."""
    return {(shape_key, seg_idx) for shape_key, seg_idx, _ in bins}


def apply_threshold(bins: dict, min_samples: int) -> tuple:
    """Returns (retained_bins, n_before, n_after)."""
    retained = {k: v for k, v in bins.items() if v[1] >= min_samples}
    return retained, len(bins), len(retained)


def incomplete_day_notes(data_dir: Path, days: list) -> dict:
    """day -> human-readable reason, for every day in `days` whose own
    <date>.manifest.json (generate_manifest.py's output) shows it wasn't a
    complete, closed calendar day. Says nothing about a day whose manifest
    is missing, rather than guessing."""
    notes = {}
    for d in days:
        manifest_path = data_dir / f"{d}.manifest.json"
        if not manifest_path.exists():
            continue
        m = json.loads(manifest_path.read_text(encoding="utf-8"))
        if m.get("day_in_progress"):
            notes[d] = "day was still in progress when its own manifest was generated, not a closed calendar day"
        elif m.get("coverage_pct") is not None and m["coverage_pct"] < INCOMPLETE_COVERAGE_PCT:
            notes[d] = f"only {m['coverage_pct']}% coverage of the calendar day"
    return notes


# ─── Geometry: one chord per (shape_key, segment_index) ─────────────────────

def point_along_shape(shape: Shape, dist_m: float) -> tuple:
    """(lat, lon), rounded to COORD_DECIMALS, at along-shape distance
    `dist_m` — linear interpolation between the two shape vertices bracketing
    it in the same planar projection segment_speeds.py's project_point()
    uses for matching. Good enough for a 200m-resolution segment endpoint;
    not a substitute for the true curve inside a segment (see this module's
    docstring and the known_limitations this script writes to manifest.json).
    """
    cum = shape.cum_dist
    dist_m = max(0.0, min(dist_m, cum[-1]))
    i = bisect.bisect_right(cum, dist_m) - 1
    i = max(0, min(i, len(cum) - 2))
    span = cum[i + 1] - cum[i]
    t = 0.0 if span <= 0 else (dist_m - cum[i]) / span
    x = shape.xs[i] + t * (shape.xs[i + 1] - shape.xs[i])
    y = shape.ys[i] + t * (shape.ys[i + 1] - shape.ys[i])
    # Inverse of segment_speeds.to_local_xy().
    ref_lat_rad = math.radians(shape.ref_lat_deg)
    lat = shape.ref_lat_deg + math.degrees(y / EARTH_RADIUS_M)
    lon = shape.ref_lon + math.degrees(x / (math.cos(ref_lat_rad) * EARTH_RADIUS_M))
    return round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)


def load_route_info(gtfs_zip_path: Path) -> dict:
    """
    shape_id -> {"route_ids": [...], "route_names": [...], "route_type": int}

    Feature 3 in CLAUDE.md's frontend priorities is a transport-type filter,
    and shape_id alone cannot answer "is this a tram or a bus". Twelve shapes
    in this feed are served by more than one route, so route_ids is a list
    rather than a single value; no shape mixes route types (checked against
    the feed), so route_type stays a single int and a filter can key on it
    directly. GTFS route_type: 0 tram, 1 metro, 3 bus, 11 trolleybus.
    """
    with zipfile.ZipFile(gtfs_zip_path) as z:
        with z.open("routes.txt") as f:
            routes = {
                row["route_id"]: (row.get("route_short_name") or row["route_id"], row["route_type"])
                for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig"))
            }
        shape_routes = defaultdict(set)
        with z.open("trips.txt") as f:
            for row in csv.DictReader(io.TextIOWrapper(f, encoding="utf-8-sig")):
                if row["shape_id"] and row["route_id"] in routes:
                    shape_routes[row["shape_id"]].add(row["route_id"])

    info = {}
    for shape_id, route_ids in shape_routes.items():
        ids = sorted(route_ids)
        types = {routes[r][1] for r in ids}
        info[shape_id] = {
            "route_ids": ids,
            "route_names": sorted({routes[r][0] for r in ids}),
            # A mixed-type shape would make a type filter lie, so it is left
            # unset rather than resolved by picking a winner.
            "route_type": int(types.pop()) if len(types) == 1 else None,
        }
    return info


def load_static_sources(static_source: Path) -> tuple:
    """Merges every static feed relevant to `static_source` into one
    (shapes_by_key, route_info, loaded_paths) triple.

    A single zip behaves exactly as before (one feed). A directory loads
    every gtfs_<YYYY-MM-DD>.zip snapshot found in it (find_static_snapshots,
    shared with segment_speeds.py) and merges them: shapes_by_key is
    content-addressed (segment_speeds.build_shape_key), so the union is safe
    and self-deduplicating, and route_info is keyed by bare shape_id, which
    CLAUDE.md's 2026-08-31 finding confirms is stable across the known
    snapshots (routes.txt/trips.txt route associations didn't change, only
    32 shapes' geometry did) — a later snapshot's entry simply overwrites an
    identical earlier one.

    Unlike segment_speeds.py, this script doesn't pick one snapshot per
    calendar day (that selection already happened upstream, producing the
    bins this script reads) — it only needs every shape_key that could
    appear in its input to resolve to a geometry and a route, so loading
    every available snapshot up front is simpler than trying to work out in
    advance which ones a given run's bins actually reference.
    """
    if static_source.is_dir():
        snapshots = find_static_snapshots(static_source)
        if not snapshots:
            print(f"No gtfs_<YYYY-MM-DD>.zip snapshots found in {static_source}", file=sys.stderr)
            sys.exit(1)
        paths = [p for _, p in snapshots]
    else:
        paths = [static_source]

    shapes_by_key = {}
    route_info = {}
    for path in paths:
        print(f"Loading static feed {path} ...")
        t0 = time.time()
        _, shapes = load_static(path)
        shapes_by_key.update(shapes)
        route_info.update(load_route_info(path))
        print(f"  {len(shapes):,} shapes ({time.time() - t0:.1f}s)")

    return shapes_by_key, route_info, paths


def build_geometry(retained_pairs: set, shapes_by_key: dict, route_info: dict | None = None) -> tuple:
    """Returns (geometry_dict, index_of, missing_count).

    index_of maps (shape_key, segment_index) -> its position in geometry's
    parallel arrays, so build_timeslot_files() can turn a bin into an index
    reference. Pairs whose shape_key isn't in `shapes_by_key` (this run's
    static feed(s) don't cover whatever produced the speed data) are
    counted and skipped, never silently dropped without a trace.

    Route metadata (shape_ids/shape_route_*) is grouped by the *bare*
    shape_id (via shape_id_from_key), not the full shape_key:
    routes.txt/trips.txt know nothing about a geometry hash, and two
    geometry versions of the same shape_id are genuinely the same route
    (CLAUDE.md's 2026-08-31 finding: the republished feed changed geometry,
    not route associations) — treating them as separate route-metadata rows
    would only inflate the export for no benefit. Segment *coordinates*
    still resolve through the full shape_key below, so a bin is never drawn
    against the wrong geometry version even when it shares a route-metadata
    row with one that came from a different snapshot.
    """
    ordered = sorted(p for p in retained_pairs if p[0] in shapes_by_key)
    missing = sum(1 for p in retained_pairs if p[0] not in shapes_by_key)

    bare_ids = sorted({shape_id_from_key(shape_key) for shape_key, _ in ordered})
    shape_pos = {bare_id: i for i, bare_id in enumerate(bare_ids)}

    shape_idx, segment_index, start_lat, start_lon, end_lat, end_lon = [], [], [], [], [], []
    index_of = {}
    for i, (shape_key, seg_idx) in enumerate(ordered):
        shape = shapes_by_key[shape_key]
        slat, slon = point_along_shape(shape, seg_idx * SEGMENT_LENGTH_M)
        elat, elon = point_along_shape(shape, (seg_idx + 1) * SEGMENT_LENGTH_M)
        shape_idx.append(shape_pos[shape_id_from_key(shape_key)])
        segment_index.append(seg_idx)
        start_lat.append(slat)
        start_lon.append(slon)
        end_lat.append(elat)
        end_lon.append(elon)
        index_of[(shape_key, seg_idx)] = i

    # Route metadata is stored per shape (hundreds of entries), not per
    # segment (tens of thousands): a type filter needs one lookup hop through
    # shape_idx, and the file stays small enough to keep loading once.
    route_info = route_info or {}
    shape_route_ids = [route_info.get(sid, {}).get("route_ids", []) for sid in bare_ids]
    shape_route_names = [route_info.get(sid, {}).get("route_names", []) for sid in bare_ids]
    shape_route_type = [route_info.get(sid, {}).get("route_type") for sid in bare_ids]

    geometry = {
        "segment_length_m": SEGMENT_LENGTH_M,
        "shape_ids": bare_ids,
        "shape_route_ids": shape_route_ids,
        "shape_route_names": shape_route_names,
        "shape_route_type": shape_route_type,
        "shape_idx": shape_idx,
        "segment_index": segment_index,
        "start_lat": start_lat,
        "start_lon": start_lon,
        "end_lat": end_lat,
        "end_lon": end_lon,
    }
    return geometry, index_of, missing


def build_timeslot_files(bins: dict, index_of: dict) -> dict:
    """slot -> {"timeslot": slot, "segment_idx": [...], "speed_kmh": [...],
    "n_samples": [...]} — parallel arrays, one entry per surviving bin.
    A bin whose (shape_id, segment_index) isn't in index_of (see
    build_geometry's `missing` count) is skipped: there's no index to
    reference it by."""
    by_slot = defaultdict(lambda: {"segment_idx": [], "speed_kmh": [], "n_samples": []})
    for (shape_id, seg_idx, slot), (median_ms, n) in bins.items():
        pos = index_of.get((shape_id, seg_idx))
        if pos is None:
            continue
        d = by_slot[slot]
        d["segment_idx"].append(pos)
        d["speed_kmh"].append(round(median_ms * 3.6))
        d["n_samples"].append(n)
    return {slot: {"timeslot": slot, **d} for slot, d in by_slot.items()}


# ─── Manifest ────────────────────────────────────────────────────────────────

def build_manifest(
    *,
    mode: str,
    min_samples: int,
    bins_before: int,
    bins_after: int,
    pairs_before: int,
    pairs_after: int,
    missing_shapes: int,
    timeslot_labels: list,
    source: dict,
    days_processed: list,
    days_in_median: list,
    incomplete_days: dict,
    shapes_observed: int,
    total_static_shapes: int,
) -> dict:
    bins_dropped = bins_before - bins_after
    segments_dropped = pairs_before - pairs_after
    return {
        "format_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": mode,  # "typical_weekday" or a specific "YYYY-MM-DD"
        "source": source,
        "days_processed": days_processed,
        "days_in_median": days_in_median,
        "incomplete_days": incomplete_days,
        "preprocessing_thresholds": {
            "segment_length_m": SEGMENT_LENGTH_M,
            "timeslot_minutes": TIMESLOT_MINUTES,
            "max_time_gap_sec": MAX_TIME_GAP_SEC,
            "max_speed_kmh": MAX_SPEED_KMH,
            "backward_tolerance_m": BACKWARD_TOLERANCE_M,
            "search_margin_m": SEARCH_MARGIN_M,
        },
        "web_export": {
            "min_samples_threshold": min_samples,
            "min_samples_rationale": (
                "a median needs at least two independent observations to be an aggregate "
                "rather than a single relabeled raw sample; n_samples still ships with every "
                "surviving bin so a thin median stays visually distinguishable client-side "
                "instead of this cutoff hiding it (D4; CLAUDE.md section 6, 'name the "
                "limitation before someone else does')"
            ),
            "coordinate_decimal_places": COORD_DECIMALS,
            "coordinate_precision_rationale": (
                "~1.1 m at this latitude, below the accuracy of the GPS units behind the "
                "underlying feed"
            ),
            "bins_total_before_threshold": bins_before,
            "bins_retained": bins_after,
            "bins_dropped": bins_dropped,
            "bins_dropped_pct": round(100 * bins_dropped / bins_before, 2) if bins_before else None,
            "segments_total_before_threshold": pairs_before,
            "segments_retained": pairs_after,
            "segments_dropped_pct": round(100 * segments_dropped / pairs_before, 2) if pairs_before else None,
            "segments_missing_from_static_feed": missing_shapes,
        },
        "segment_count": pairs_after,
        "timeslot_count": len(timeslot_labels),
        "timeslots": sorted(timeslot_labels),
        "known_limitations": [
            "Segment geometry is a straight chord between the two ends of a 200 m bin, not "
            "the true polyline inside it — a sharp turn or roundabout within one segment "
            "renders as a straight line, not the vehicle's actual path.",
            "Speed is an integer km/h for map colouring, not the scientific record. The "
            "underlying float m/s and every individual sample it was built from live in "
            "segment_speeds_<date>.jsonl and typical_weekday.json, published as-is (D5).",
            f"Only shapes actually observed in the source archive appear here: "
            f"{shapes_observed} of the static feed's {total_static_shapes} shapes for this "
            f"export ({mode}).",
            "The GTFS-RT feed carries no Sofia metro vehicles; this export describes surface "
            "transport only.",
            "Route metadata is per shape, not per segment. Twelve shapes in this feed serve "
            "more than one route, so shape_route_ids is a list; no shape mixes route types, "
            "so shape_route_type is a single GTFS value (0 tram, 1 metro, 3 bus, "
            "11 trolleybus) and is null if that ever stops holding.",
        ],
    }


# ─── Size reporting (measure the budget, don't assume it) ──────────────────

def gzip_size(path: Path) -> int:
    return len(gzip.compress(path.read_bytes(), compresslevel=9))


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("static_source", type=Path,
                        help="GTFS static zip (needs shapes.txt), or a directory of gtfs_<YYYY-MM-DD>.zip "
                             "snapshots -- every snapshot found is loaded and merged (see load_static_sources)")
    parser.add_argument("data_dir", type=Path, help="Directory with <YYYY-MM-DD>.jsonl/.manifest.json and segment_speeds.py's processed/ output")
    parser.add_argument("--processed-dir", type=Path, default=None,
                        help="Where segment_speeds.py wrote its output (default: <data_dir>/processed)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Where to write the web export (default: <data_dir>/web)")
    parser.add_argument("--day", type=str, default=None,
                        help="Export one specific YYYY-MM-DD day's own median instead of typical_weekday.json "
                             "(Component D feature 2, the day switcher)")
    parser.add_argument("--min-samples", type=int, default=MIN_SAMPLES_DEFAULT,
                        help=f"Drop (segment, timeslot) bins with fewer observations than this (default: {MIN_SAMPLES_DEFAULT})")
    args = parser.parse_args()

    processed_dir = args.processed_dir or (args.data_dir / "processed")
    output_root = args.output_dir or (args.data_dir / "web")

    shapes_by_key, route_info, static_paths = load_static_sources(args.static_source)
    static_feed_names = [p.name for p in static_paths]

    if args.day:
        mode = args.day
        segment_speeds_path = processed_dir / f"segment_speeds_{args.day}.jsonl"
        if not segment_speeds_path.exists():
            print(f"{segment_speeds_path}: not found", file=sys.stderr)
            sys.exit(1)
        t0 = time.time()
        bins = aggregate_day_bins(segment_speeds_path)
        print(f"  aggregated {segment_speeds_path.name}: {len(bins):,} bins ({time.time() - t0:.1f}s)")
        source = {"segment_speeds_file": segment_speeds_path.name, "static_feeds_used": static_feed_names}
        days_processed = [args.day]
        days_in_median = [args.day]
    else:
        mode = "typical_weekday"
        typical_path = processed_dir / "typical_weekday.json"
        if not typical_path.exists():
            print(f"{typical_path}: not found -- run segment_speeds.py first", file=sys.stderr)
            sys.exit(1)
        bins, typical_data = load_typical_weekday_bins(typical_path)
        # typical_weekday.json now records a list of feeds (one per day it
        # potentially spans, see segment_speeds.py's day_breakdown) rather
        # than one scalar file -- warn only if this run's loaded feeds don't
        # cover everything segment_speeds.py actually used, since a
        # directory source naturally loads every snapshot anyway.
        recorded_feeds = {f["file"] for f in typical_data.get("static_feeds", [])}
        missing_feeds = recorded_feeds - set(static_feed_names)
        if missing_feeds:
            print(f"WARNING: typical_weekday.json was built using {sorted(missing_feeds)}, "
                  f"not loaded by this run ({static_feed_names}) -- some shape_ids may not resolve",
                  file=sys.stderr)
        source = {"typical_weekday_file": typical_path.name, "static_feeds_used": static_feed_names}
        days_processed = typical_data.get("days_processed", [])
        days_in_median = typical_data.get("days_in_median_mon_fri", [])

    incomplete = incomplete_day_notes(args.data_dir, days_in_median)
    out_dir = output_root / mode

    pairs_before = segment_pairs(bins)
    retained, n_before, n_after = apply_threshold(bins, args.min_samples)
    pairs_after = segment_pairs(retained)

    geometry, index_of, missing_shapes = build_geometry(pairs_after, shapes_by_key, route_info)
    timeslot_files = build_timeslot_files(retained, index_of)

    # This script owns everything under out_dir once it exists, so rebuild
    # it from scratch rather than overwrite in place — a rerun with a
    # tighter --min-samples, or a day that lost coverage since the last
    # export, would otherwise leave stale timeslot files on disk that the
    # freshly written manifest.json's "timeslots" list doesn't mention.
    shutil.rmtree(out_dir, ignore_errors=True)
    slots_dir = out_dir / "timeslots"
    slots_dir.mkdir(parents=True)

    for slot, payload in timeslot_files.items():
        fname = slot.replace(":", "") + ".json"
        (slots_dir / fname).write_text(
            json.dumps(payload, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
        )

    geometry_path = out_dir / "geometry.json"
    geometry_path.write_text(
        json.dumps(geometry, separators=(",", ":"), ensure_ascii=False), encoding="utf-8"
    )

    manifest = build_manifest(
        mode=mode,
        min_samples=args.min_samples,
        bins_before=n_before,
        bins_after=n_after,
        pairs_before=len(pairs_before),
        pairs_after=len(pairs_after),
        missing_shapes=missing_shapes,
        timeslot_labels=list(timeslot_files.keys()),
        source=source,
        days_processed=days_processed,
        days_in_median=days_in_median,
        incomplete_days=incomplete,
        shapes_observed=len({sid for sid, _ in pairs_before}),
        total_static_shapes=len(shapes_by_key),
    )
    manifest_path = out_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # --- size report: measure the budget, don't assume it holds ---
    slot_paths = sorted(slots_dir.glob("*.json"))
    slot_sizes = [(p.stat().st_size, gzip_size(p)) for p in slot_paths]
    first_slot_gz = slot_sizes[0][1] if slot_sizes else 0
    avg_gz = sum(g for _, g in slot_sizes) / len(slot_sizes) if slot_sizes else 0
    max_gz = max((g for _, g in slot_sizes), default=0)

    manifest_gz = gzip_size(manifest_path)
    geometry_gz = gzip_size(geometry_path)
    first_load_gz = manifest_gz + geometry_gz + first_slot_gz

    print(f"\n{out_dir}")
    print(f"  manifest.json  {manifest_path.stat().st_size:,}B raw / {manifest_gz:,}B gz")
    print(f"  geometry.json  {geometry_path.stat().st_size:,}B raw / {geometry_gz:,}B gz  "
          f"({len(geometry['shape_idx']):,} segments, {len(geometry['shape_ids']):,} shapes)")
    print(f"  {len(slot_paths)} timeslot files, avg {avg_gz:,.0f}B gz, largest {max_gz:,}B gz")
    print(f"  first load (manifest + geometry + 1 slot): {first_load_gz:,}B gz  (budget: ~1,000,000B)")
    print(f"  bins: {n_before:,} -> {n_after:,} retained (min_samples={args.min_samples}), "
          f"segments: {len(pairs_before):,} -> {len(pairs_after):,}")
    if missing_shapes:
        print(f"  WARNING: {missing_shapes} (shape,segment) pair(s) referenced a shape_key absent "
              f"from {static_feed_names} -- dropped from geometry and every timeslot file", file=sys.stderr)


if __name__ == "__main__":
    main()
