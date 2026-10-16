"""Reading a static GTFS zip: shapes keyed by geometry, trips, feed info."""

import hashlib
from collections import defaultdict
from itertools import starmap
from pathlib import Path

from speeds.geometry import build_shape
from speeds.gtfs_zip import read_static_tables

POINT_FORMAT = '{:.6f},{:.6f}'  # lat,lon at 6 decimals, see build_shape_key
KEY_HEX_CHARS = 16  # 64 bits of sha256, see build_shape_key's docstring


def build_shape_key(points: list) -> str:
    """Content-addressed shape identity (16 hex chars of a sha256).

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
    text = ';'.join(starmap(POINT_FORMAT.format, points))
    digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
    return digest[:KEY_HEX_CHARS]


def _keys_by_id(coords_by_id: dict) -> dict:
    return {
        shape_id: build_shape_key(points)
        for shape_id, points in coords_by_id.items()
    }


def _group_ids(key_by_shape_id: dict) -> dict:
    """Invert shape_id -> key into key -> {shape_id, ...}."""
    shape_ids_by_key = defaultdict(set)
    for shape_id, key in key_by_shape_id.items():
        shape_ids_by_key[key].add(shape_id)
    return shape_ids_by_key


def _shapes_by_key(coords_by_id: dict, key_by_shape_id: dict) -> dict:
    return {
        key_by_shape_id[shape_id]: build_shape(coords)
        for shape_id, coords in coords_by_id.items()
        if len(coords) >= 2
    }


def _trip_map(
    trip_rows: list,
    key_by_shape_id: dict,
    shape_ids_by_key: dict,
) -> dict:
    trip_map = {}
    for row in trip_rows:
        shape_id = row['shape_id']
        if not shape_id:
            continue
        if shape_id not in key_by_shape_id:
            # A shape_id referenced by a trip but absent from shapes.txt.
            # Every such id hashes the empty point list to one shared key
            # with no Shape behind it, which still surfaces downstream as
            # process_day's "shape_not_found" reject rather than a
            # different counter — the pre-existing behaviour.
            key_by_shape_id[shape_id] = build_shape_key([])
            shape_ids_by_key[key_by_shape_id[shape_id]].add(shape_id)
        trip_map[row['trip_id']] = (
            row['route_id'],
            shape_id,
            key_by_shape_id[shape_id],
        )
    return trip_map


def load_static(gtfs_zip_path: Path) -> tuple:
    """Load the trip, shape and shape-id maps of one static feed.

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
    trip_rows, coords = read_static_tables(gtfs_zip_path)
    # Every shape_id referenced anywhere gets a key, even one with <2 points
    # or one missing from shapes.txt entirely (empty coords) — both cases
    # existed before this change too, and both must still surface downstream
    # as process_day's "shape_not_found" reject reason rather than silently
    # changing which counter fires.
    key_by_shape_id = _keys_by_id(coords)
    shape_ids_by_key = _group_ids(key_by_shape_id)
    return (
        _trip_map(trip_rows, key_by_shape_id, shape_ids_by_key),
        _shapes_by_key(coords, key_by_shape_id),
        dict(shape_ids_by_key),
    )
