#!/usr/bin/env python3
r"""Segment speeds and the typical weekday.

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
      schedules for it (schedule_signature() below); a weekday whose
      signature differs from its period's first day by less than
      PERIOD_TOLERANCE_PCT of the trips shares that period, a bigger jump
      rejoins an earlier period if it is a timetable coming back and starts
      a new one if it is not, and a weekday running a holiday's service
      leaves the median altogether (assign_schedule_periods()), so a period
      can hold days that are not contiguous. The aggregation groups on
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
    python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip \
        data/sofia --output-dir data/sofia/processed 2026-08-28 2026-08-31

    # no explicit dates: every <YYYY-MM-DD>.jsonl (or .jsonl.gz, transparently
    # decompressed — see deploy/sofia-compress.service) found in the data dir
    python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip \
        data/sofia

    # a directory of gtfs_<YYYY-MM-DD>.zip snapshots instead of one zip: each
    # day is matched against the latest snapshot dated on or before it
    python3 scripts/segment_speeds.py data/sofia/static data/sofia
"""

import sys
from pathlib import Path

# config.py at the repo root loads the city profile, which is the single
# source of truth for timezone and for what unit the feed's speed field
# really carries, same pattern as scripts/generate_manifest.py — importing
# collect.py itself would pull in requests/protobuf this script never needs.
# find_day_files/resolve_day_file/open_maybe_gzip are the same day-file
# helpers generate_manifest.py uses, so a <date>.jsonl.gz produced by
# deploy/sofia-compress.service is read transparently here too.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: WPS221  # fixed bootstrap line shared by every script
# The implementation lives in the speeds package; export_web.py and the tests
# keep importing these names from here.
from speeds.cli import parse_setup
from speeds.constants import (
    BACKWARD_TOLERANCE_M,
    EARTH_RADIUS_M,
    MAX_SPEED_KMH,
    MAX_TIME_GAP_SEC,
    SEARCH_MARGIN_M,
    SEGMENT_LENGTH_M,
    TIMESLOT_MINUTES,
)
from speeds.exports_days import (
    assign_schedule_periods,
    build_schedule_periods,
    build_typical_weekday,
    load_schedule_calendar,
    process_day,
    schedule_churn,
    schedule_signature,
    timeslot_for,
)
from speeds.exports_feed import (
    build_shape_key,
    count_multi_geometry_shape_ids,
    count_multi_id_geometries,
    find_static_snapshots,
    load_static,
    next_snapshot_after,
    pick_snapshot_for_day,
)
from speeds.exports_geometry import (
    Shape,
    build_shape,
    haversine_m,
    project_onto_segment,
    project_point,
    window_bounds,
)
from speeds.pipeline import run

# Re-exports: listing them keeps the imports above from reading as unused.
__all__ = (  # noqa: WPS410  # re-exports for export_web.py and the tests
    'BACKWARD_TOLERANCE_M',
    'EARTH_RADIUS_M',
    'MAX_SPEED_KMH',
    'MAX_TIME_GAP_SEC',
    'SEARCH_MARGIN_M',
    'SEGMENT_LENGTH_M',
    'TIMESLOT_MINUTES',
    'Shape',
    'assign_schedule_periods',
    'build_schedule_periods',
    'build_shape',
    'build_shape_key',
    'build_typical_weekday',
    'count_multi_geometry_shape_ids',
    'count_multi_id_geometries',
    'find_static_snapshots',
    'haversine_m',
    'load_schedule_calendar',
    'load_static',
    'next_snapshot_after',
    'pick_snapshot_for_day',
    'process_day',
    'project_onto_segment',
    'project_point',
    'schedule_churn',
    'schedule_signature',
    'timeslot_for',
    'window_bounds',
)


def main() -> None:
    """Run the pipeline for the command line in sys.argv."""
    run(parse_setup(__doc__))


if __name__ == '__main__':
    main()
