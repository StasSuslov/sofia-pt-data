"""Geometry: one simplified polyline per (shape_key, segment_index)."""

import bisect
import math

from segment_speeds import EARTH_RADIUS_M, SEGMENT_LENGTH_M, Shape

from webexport.constants import COORD_DECIMALS, SIMPLIFY_TOLERANCE_M, ZERO_M
from webexport.simplify import simplify_rdp


def _bracket(cum: list, dist_m: float) -> tuple:
    """Return the span index holding `dist_m` and the fraction along it."""
    index = bisect.bisect_right(cum, dist_m) - 1
    index = max(0, min(index, len(cum) - 2))
    span = cum[index + 1] - cum[index]
    fraction = ZERO_M
    if span > 0:
        fraction = (dist_m - cum[index]) / span
    return index, fraction


def _clamp(dist_m: float, top: float) -> float:
    """Clamp an along-shape distance into [0, top]."""
    return max(ZERO_M, min(dist_m, top))


def xy_along_shape(shape: Shape, dist_m: float) -> tuple:
    """Local-plane (x, y) metres at along-shape distance `dist_m`.

    Linear interpolation between the two shape vertices bracketing it, in
    the same projection segment_speeds.py's project_point() uses for
    matching. Distance past either end of the shape clamps to that end.
    """
    index, fraction = _bracket(
        shape.cum_dist,
        _clamp(dist_m, shape.cum_dist[-1]),
    )
    window = slice(index, index + 2)
    return tuple(
        start + fraction * (end - start)
        for start, end in (shape.xs[window], shape.ys[window])
    )


def xy_to_latlon(shape: Shape, x_m: float, y_m: float) -> tuple:
    """Inverse of speeds.geometry.to_plane(), rounded to COORD_DECIMALS."""
    ref_lat_rad = math.radians(shape.ref_lat_deg)
    lat = shape.ref_lat_deg + math.degrees(y_m / EARTH_RADIUS_M)
    lon = shape.ref_lon + math.degrees(
        x_m / (math.cos(ref_lat_rad) * EARTH_RADIUS_M),
    )
    return round(lat, COORD_DECIMALS), round(lon, COORD_DECIMALS)


def point_along_shape(shape: Shape, dist_m: float) -> tuple:
    """Return (lat, lon), rounded to COORD_DECIMALS, at distance `dist_m`."""
    return xy_to_latlon(shape, *xy_along_shape(shape, dist_m))


def _bin_points(shape: Shape, seg_idx: int) -> list:
    """Return the bin's start point, its inner vertices and its end point."""
    # Clamped the same way xy_along_shape clamps, so a bin running past the
    # end of its shape doesn't pick up the final vertex *and* the endpoint
    # interpolated onto it as two copies of one point.
    start_d = _clamp(seg_idx * SEGMENT_LENGTH_M, shape.cum_dist[-1])
    end_d = _clamp((seg_idx + 1) * SEGMENT_LENGTH_M, shape.cum_dist[-1])
    inner = slice(
        bisect.bisect_right(shape.cum_dist, start_d),
        bisect.bisect_left(shape.cum_dist, end_d),
    )
    return [
        xy_along_shape(shape, start_d),
        *zip(shape.xs[inner], shape.ys[inner], strict=True),
        xy_along_shape(shape, end_d),
    ]


def segment_polyline(
    shape: Shape,
    seg_idx: int,
    tolerance_m: float = SIMPLIFY_TOLERANCE_M,
) -> list:
    """The drawn path of one 200 m bin as [(lat, lon), ...].

    The bin's start point, every shapes.txt vertex strictly inside it, the
    bin's end point — Douglas-Peucker simplified at `tolerance_m` metres.

    "Strictly inside" because a vertex sitting exactly on a bin boundary is
    already the interpolated endpoint; including it would emit the same
    point twice. A bin past the end of its shape (possible only through the
    clamp in xy_along_shape) collapses to two identical points, which is
    what the chord did before and still draws nothing.
    """
    points = _bin_points(shape, seg_idx)
    return [
        xy_to_latlon(shape, *point)
        for point in simplify_rdp(points, tolerance_m)
    ]
