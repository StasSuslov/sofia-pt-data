#!/usr/bin/env python3
r"""Turn segment_speeds.py output into static files for the web frontend.

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

Format decided here (format_version 2):
  - Geometry (shape_key, segment_index -> the drawn path of that bin) is
    written once per export and referenced by *index* from every timeslot
    file, instead of repeating shape_id/segment_index/lat/lon in every bin.
    A segment's path is the shape's own polyline between the along-shape
    positions at segment_index*200m and (segment_index+1)*200m, simplified
    with Douglas-Peucker at SIMPLIFY_TOLERANCE_M metres — see that constant
    for what the chord format_version 1 drew instead cost in accuracy.
  - Those points ship CSR-style: flat lat[] and lon[] over every point of
    every segment, plus point_offset[] of length len(segments)+1, so
    segment i is the slice [point_offset[i], point_offset[i+1]). A segment
    holds a variable number of points, which the four endpoint arrays of
    format_version 1 could not express.
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
  - typical_kmh, one per segment, in a typical-weekday bundle only: the
    median of that segment's own per-slot medians, so the client can mark a
    segment that is slower than itself right now without refetching every
    slot. Null below TYPICAL_MIN_SLOTS filled slots — see that constant.
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

shape_key, not shape_id: segment_speeds.py keys everything by the shape
geometry itself (see its build_shape_key()), because a republished static
feed can keep a shape_id while changing its geometry *and* keep a geometry
while changing its shape_id — both happened, a fortnight apart. This script
inherits that identity, so a bin's geometry always resolves against the
correct polyline and two ids for one road stay one row. Route metadata
(routes.txt/trips.txt) is still keyed by the bare shape_id, so
build_geometry() resolves it through the shape_ids load_static() reports
for each key, which is a set rather than one id for exactly that reason.

One bundle per schedule period: segment_speeds.py splits the typical
weekday by the timetable each day ran under (its schedule_period_key), so
this script writes web/typical_weekday/<period_key>/ for each of them —
geometry, timeslots and manifest exactly as before, one median per
timetable — plus web/typical_weekday/manifest.json, an index naming every
period, the weekdays it holds (not always contiguous: a timetable that
returns rejoins its period) and which one is current. A client fetches the index
first and follows current_period. The --day switcher is untouched: one day
ran one timetable, so it needs no split.

One index above all of it: web/index.json names every day bundle on disk
and points at the typical-weekday tree, because a static site (D7) cannot
list a directory and the day switcher has to enumerate days from somewhere.
It is rebuilt from what is actually in the output directory, not from the
days this run was asked to export — see write_root_index().

Static feed argument: either one zip (one feed for every bin, unchanged
prior behaviour) or a directory of gtfs_<YYYY-MM-DD>.zip snapshots — see
load_static_sources(). Unlike segment_speeds.py this script doesn't need to
pick one snapshot per calendar day (that selection already happened
upstream, when segment_speeds.py produced the bins this script reads); it
only needs every shape_key that could appear in its input to resolve to a
geometry and a route, so a directory's snapshots are all loaded and merged
up front.

Usage:
    python3 scripts/export_web.py \
        data/sofia/static/gtfs_2026-08-27.zip data/sofia
    python3 scripts/export_web.py \
        data/sofia/static/gtfs_2026-08-27.zip data/sofia --day 2026-08-28

    # a directory of gtfs_<YYYY-MM-DD>.zip snapshots instead of one zip
    python3 scripts/export_web.py data/sofia/static data/sofia
"""

import sys
from pathlib import Path

# segment_speeds.py lives next to this file; reuse its shape-loading and
# projection geometry instead of re-deriving it (it's already reviewed and
# tested). Explicit path insert so this also works under pytest, which
# doesn't always add a script's own directory to sys.path.
sys.path.insert(0, str(Path(__file__).resolve().parent))
# sys.path[0] is now the scripts directory; its parent is the repo root.
sys.path.insert(0, f'{Path(sys.path[0]).parent}')
from webexport import cli
from webexport.bins import aggregate_day_bins, load_typical_weekday_bins
from webexport.constants import (
    COORD_DECIMALS,
    MIN_SAMPLES_DEFAULT,
    SIMPLIFY_TOLERANCE_M,
    SLOWDOWN_ABS_MIN_KMH,
    SLOWDOWN_RATIO_MAX,
    TYPICAL_MIN_SLOTS,
)
from webexport.indexes import (
    build_period_index,
    current_period_key,
    write_root_index,
)
from webexport.reexports import (
    build_geometry,
    build_manifest,
    load_route_info,
    point_along_shape,
    simplify_rdp,
)
from webexport.thresholds import apply_threshold, segment_pairs
from webexport.timeslots import build_timeslot_files, build_typical_kmh

# Names other modules and the tests import from here; the implementation
# lives in the webexport package.
__all__ = (  # noqa: WPS410  # re-export list: tests, publish_web, plotting
    'COORD_DECIMALS',
    'MIN_SAMPLES_DEFAULT',
    'SIMPLIFY_TOLERANCE_M',
    'SLOWDOWN_ABS_MIN_KMH',
    'SLOWDOWN_RATIO_MAX',
    'TYPICAL_MIN_SLOTS',
    'aggregate_day_bins',
    'apply_threshold',
    'build_geometry',
    'build_manifest',
    'build_period_index',
    'build_timeslot_files',
    'build_typical_kmh',
    'current_period_key',
    'load_route_info',
    'load_typical_weekday_bins',
    'main',
    'point_along_shape',
    'segment_pairs',
    'simplify_rdp',
    'write_root_index',
)


def main() -> None:
    """Run the export with this module's docstring as the --help text."""
    cli.main(__doc__)


if __name__ == '__main__':
    main()
