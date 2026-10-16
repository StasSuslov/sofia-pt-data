"""Shape polylines: haversine distance and the local flat-plane frame."""

import math
from itertools import accumulate, pairwise
from typing import NamedTuple

from speeds.constants import EARTH_RADIUS_M


def _hav(angle_rad: float) -> float:
    return math.sin(angle_rad / 2) ** 2


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters.

    Used for cumulative along-shape distance (task step 1 asks for
    haversine explicitly). Segment *projection* below uses a flat
    local-plane approximation instead; see Shape's docstring for why that's
    a different job.
    """
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    cos_term = math.cos(phi1) * math.cos(phi2)
    lat_term = _hav(math.radians(lat2 - lat1))
    lon_term = cos_term * _hav(math.radians(lon2 - lon1))
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(lat_term + lon_term))


class Shape(NamedTuple):
    """A trip shape as a polyline in two parallel coordinate systems.

    - xs/ys: a local flat-plane (equirectangular) projection, referenced
      to the shape's own mean latitude and first longitude, used only to
      find the closest line *segment* to a raw GPS point; projecting a
      point onto a segment needs planar geometry, not great-circle math.
    - cum_dist: cumulative haversine distance (meters) at each vertex;
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


def to_plane(
    ref_lat_deg: float,
    ref_lon: float,
    lat: float,
    lon: float,
) -> tuple:
    """Equirectangular (east, north) meters of a point around a reference."""
    east = (
        math.radians(lon - ref_lon)
        * math.cos(math.radians(ref_lat_deg))
        * EARTH_RADIUS_M
    )
    north = math.radians(lat - ref_lat_deg) * EARTH_RADIUS_M
    return east, north


def _plane_xy(points: list, ref_lat_deg: float, ref_lon: float) -> tuple:
    plane = [to_plane(ref_lat_deg, ref_lon, lat, lon) for lat, lon in points]
    return [east for east, _ in plane], [north for _, north in plane]


def _cumulative_dist(points: list) -> list:
    return list(
        accumulate(
            (haversine_m(*start, *end) for start, end in pairwise(points)),
            initial=0,
        ),
    )


def build_shape(points: list) -> Shape:
    """Build a Shape from `points`, a list of (lat, lon) in sequence order."""
    ref_lat_deg = sum(lat for lat, _ in points) / len(points)
    ref_lon = points[0][1]
    xs, ys = _plane_xy(points, ref_lat_deg, ref_lon)
    return Shape(xs, ys, _cumulative_dist(points), ref_lat_deg, ref_lon)
