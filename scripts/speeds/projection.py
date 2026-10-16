"""Projecting GPS points onto a shape and bounding the search window."""

import bisect
import math
from itertools import pairwise

from speeds import geometry
from speeds.constants import BACKWARD_TOLERANCE_M, SEARCH_MARGIN_M


def _fraction(rel_x: float, rel_y: float, dx: float, dy: float) -> float:
    seg_len_sq = dx * dx + dy * dy
    if not seg_len_sq:
        return 0
    along = (rel_x * dx + rel_y * dy) / seg_len_sq
    return min(max(along, 0), 1)


def project_onto_segment(  # noqa: WPS211 (signature fixed by export_web)
    px: float,
    py: float,
    ax: float,
    ay: float,
    bx: float,
    by: float,
) -> tuple:
    """Standard point-onto-segment projection.

    Returns (t in [0,1], perpendicular distance).
    """
    dx, dy = bx - ax, by - ay
    frac = _fraction(px - ax, py - ay, dx, dy)
    foot = ax + frac * dx, ay + frac * dy
    return frac, math.dist((px, py), foot)


def _vertices(shape: geometry.Shape, lo_idx: int, hi_idx: int):
    span = slice(lo_idx, hi_idx + 2)
    coords = zip(shape.xs[span], shape.ys[span], strict=False)
    return pairwise(coords)


def _segment_hits(
    shape: geometry.Shape,
    point: tuple,
    lo_idx: int,
    hi_idx: int,
):
    """Yield (distance, segment index, t) for each segment in the range."""
    for idx, (start, end) in enumerate(
        _vertices(shape, lo_idx, hi_idx),
        lo_idx,
    ):
        frac, dist = project_onto_segment(*point, *start, *end)
        yield dist, idx, frac


def project_point(
    shape: geometry.Shape,
    lat: float,
    lon: float,
    lo_idx: int,
    hi_idx: int,
) -> tuple:
    """Closest-segment projection of (lat, lon) onto a range of segments.

    Searches shape segments [lo_idx..hi_idx] inclusive (segment i runs from
    vertex i to vertex i+1). Returns (best_segment_index,
    along_shape_distance_m, perpendicular_distance_m).

    Callers pick lo_idx/hi_idx: (0, len(shape.xs)-2) for a full search, or
    window_bounds()'s narrower range for the sliding-window search.
    """
    best_dist, best_i, best_t = min(
        _segment_hits(
            shape,
            geometry.to_plane(shape.ref_lat_deg, shape.ref_lon, lat, lon),
            lo_idx,
            hi_idx,
        ),
        default=(float('inf'), lo_idx, 0),
    )
    seg_start, seg_end = shape.cum_dist[best_i : best_i + 2]
    return best_i, seg_start + best_t * (seg_end - seg_start), best_dist


def window_bounds(
    shape: geometry.Shape,
    last_dist: float,
    max_travel_m: float,
) -> tuple:
    """Segment-index range to search around the previous match.

    Sized from how far the vehicle could plausibly have traveled
    (max_travel_m, normally MAX_SPEED_MS * dt) plus SEARCH_MARGIN_M slack,
    around the previous match's along-shape distance.

    Anchored on distance (via bisect over the monotonically increasing
    cum_dist) rather than a fixed count of vertices around the previous
    vertex index, because shape point density varies enormously across this
    network (median ~12m spacing, some stretches near 1km); a fixed vertex
    window would be wastefully wide on dense shapes or too narrow to contain
    a physically plausible match on sparse ones.
    """
    lo_dist = last_dist - BACKWARD_TOLERANCE_M - SEARCH_MARGIN_M
    hi_dist = last_dist + max_travel_m + SEARCH_MARGIN_M
    lo_idx = max(0, bisect.bisect_left(shape.cum_dist, lo_dist) - 1)
    hi_idx = min(
        len(shape.cum_dist) - 2,
        bisect.bisect_right(shape.cum_dist, hi_dist),
    )
    return lo_idx, max(hi_idx, lo_idx)
