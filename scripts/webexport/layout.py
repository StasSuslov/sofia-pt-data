"""Turning the retained segments into geometry.json."""

from segment_speeds import SEGMENT_LENGTH_M

from webexport.geometry import segment_polyline
from webexport.model import SegmentColumns


def _union(group: list, name: str) -> list:
    """Sorted union of the list-valued field `name` over route infos."""
    return sorted({element for entry in group for element in entry[name]})


def _single_type(group: list) -> int | None:
    # One type or nothing, same rule load_route_info() applies per id: a
    # geometry served by both a tram and a bus can't answer a type
    # filter, and guessing which to show would be worse than a null.
    types = {
        entry['route_type']
        for entry in group
        if entry['route_type'] is not None
    }
    return types.pop() if len(types) == 1 else None


def _shape_table(keys: list, shape_ids_by_key: dict, route_info: dict) -> dict:
    """The per-shape columns: keys, the agency's ids, route metadata.

    Route metadata is stored per shape (hundreds of entries), not per
    segment (tens of thousands): a type filter needs one lookup hop through
    shape_idx, and the file stays small enough to keep loading once.
    """
    ids_per_key = [sorted(shape_ids_by_key.get(key, ())) for key in keys]
    infos = [
        [route_info[shape_id] for shape_id in ids if shape_id in route_info]
        for ids in ids_per_key
    ]
    return {
        'shape_keys': keys,
        'shape_ids': ids_per_key,
        'shape_route_ids': [_union(group, 'route_ids') for group in infos],
        'shape_route_names': [_union(group, 'route_names') for group in infos],
        'shape_route_type': [_single_type(group) for group in infos],
    }


def _fill_columns(ordered: list, shapes_by_key: dict, keys: list):
    """Build the CSR columns of every segment in `ordered`.

    CSR: one flat lat[] and lon[] for every point of every segment, plus
    point_offset[] of length len(segments)+1 — segment i's points are the
    slice [point_offset[i], point_offset[i+1]). A segment carries a
    variable number of points now (format_version 2), so four fixed
    endpoint arrays can no longer hold it, and an array of per-segment
    arrays would pay for one JSON bracket pair per segment.
    """
    shape_pos = dict(zip(keys, range(len(keys)), strict=True))
    columns = SegmentColumns()
    for shape_key, seg_idx in ordered:
        columns.add(
            (shape_key, seg_idx),
            shape_pos[shape_key],
            segment_polyline(shapes_by_key[shape_key], seg_idx),
        )
    return columns


def build_geometry(
    retained_pairs: set,
    shapes_by_key: dict,
    route_info: dict | None = None,
    shape_ids_by_key: dict | None = None,
) -> tuple:
    """Return (geometry_dict, index_of, missing_count).

    index_of maps (shape_key, segment_index) -> its position in geometry's
    parallel arrays, so build_timeslot_files() can turn a bin into an index
    reference. Pairs whose shape_key isn't in `shapes_by_key` (this run's
    static feed(s) don't cover whatever produced the speed data) are
    counted and skipped, never silently dropped without a trace.

    One row of shape metadata per shape_key, which is one geometry. The
    agency's own shape_ids ride along as a *list* per row, because a
    geometry can carry more than one of them: on 2026-09-02 it republished
    A1192/A3949/A1221/A2710 as byte-identical A4500-A4503 and kept both.
    routes.txt/trips.txt still key route associations by the bare id, so
    the route metadata for a row is the union over that row's ids.
    """
    ordered = sorted(
        pair for pair in retained_pairs if pair[0] in shapes_by_key
    )
    keys = sorted({shape_key for shape_key, _ in ordered})
    columns = _fill_columns(ordered, shapes_by_key, keys)
    geometry = {
        'segment_length_m': SEGMENT_LENGTH_M,
        **_shape_table(keys, shape_ids_by_key or {}, route_info or {}),
        **columns.as_geometry(),
    }
    return geometry, columns.index_of, len(retained_pairs) - len(ordered)
