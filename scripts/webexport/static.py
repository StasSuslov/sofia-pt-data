"""Loading every static GTFS feed the export needs."""

import sys
import time
from collections import defaultdict
from pathlib import Path

from segment_speeds import find_static_snapshots, load_static

from webexport.routes import load_route_info


def _snapshot_paths(static_source: Path) -> list:
    """Return the zip(s) behind `static_source`: itself, or a directory's."""
    if not static_source.is_dir():
        return [static_source]
    snapshots = find_static_snapshots(static_source)
    if not snapshots:
        sys.stderr.write(
            f'No gtfs_<YYYY-MM-DD>.zip snapshots found in {static_source}\n',
        )
        sys.exit(1)
    return [path for _, path in snapshots]


class _StaticMerge:
    """The union of every static feed loaded so far."""

    def __init__(self) -> None:
        self.shapes_by_key = {}
        self.route_info = {}
        self.shape_ids_by_key = defaultdict(set)

    def add(self, path: Path) -> None:
        """Load one snapshot into the union."""
        sys.stdout.write(f'Loading static feed {path} ...\n')
        started = time.time()
        _, shapes, ids_by_key = load_static(path)
        self.shapes_by_key.update(shapes)
        self._union_ids(ids_by_key)
        self.route_info.update(load_route_info(path))
        elapsed = time.time() - started
        sys.stdout.write(f'  {len(shapes):,} shapes ({elapsed:.1f}s)\n')

    def _union_ids(self, ids_by_key: dict) -> None:
        for key, shape_ids in ids_by_key.items():
            self.shape_ids_by_key[key].update(shape_ids)


def load_static_sources(static_source: Path) -> tuple:
    """Merge every static feed relevant to `static_source` into one tuple.

    The tuple is (shapes_by_key, route_info, shape_ids_by_key, loaded_paths).

    A single zip behaves exactly as before (one feed). A directory loads
    every gtfs_<YYYY-MM-DD>.zip snapshot found in it (find_static_snapshots,
    shared with segment_speeds.py) and merges them: shapes_by_key is
    content-addressed (segment_speeds.build_shape_key), so the union is safe
    and self-deduplicating, and route_info is keyed by bare shape_id, which
    CLAUDE.md's 2026-08-31 finding confirms is stable across the known
    snapshots (routes.txt/trips.txt route associations didn't change, only
    32 shapes' geometry did) — a later snapshot's entry simply overwrites an
    identical earlier one.

    shape_ids_by_key is unioned rather than overwritten across snapshots.
    A shape_key is a geometry, and the agency has published one geometry
    under several ids (2026-09-02: A4500-A4503 duplicating
    A1192/A3949/A1221/A2710), so the ids that resolve route metadata for a
    geometry accumulate across feeds instead of the newest feed's naming
    hiding the older one's.

    Unlike segment_speeds.py, this script doesn't pick one snapshot per
    calendar day (that selection already happened upstream, producing the
    bins this script reads) — it only needs every shape_key that could
    appear in its input to resolve to a geometry and a route, so loading
    every available snapshot up front is simpler than trying to work out in
    advance which ones a given run's bins actually reference.
    """
    paths = _snapshot_paths(static_source)
    merge = _StaticMerge()
    for path in paths:
        merge.add(path)
    return (
        merge.shapes_by_key,
        merge.route_info,
        dict(merge.shape_ids_by_key),
        paths,
    )
