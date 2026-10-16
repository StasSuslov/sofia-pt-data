"""Turn the profile into runs of equally weighted line edges."""

from itertools import pairwise, starmap

from dailyprofile import svgkit as kit
from dailyprofile.constants import SOLID_MIN_SEGMENTS
from dailyprofile.furniture import ALL_SLOTS


def _edge(start: tuple, end: tuple) -> tuple:
    """(solid, [(x, y), (x, y)]) for the edge between two points."""
    solid = start[2] and end[2]
    return solid, [start[:2], end[:2]]


def edge_runs(points: list) -> list:
    """Group consecutive edges of equal weight into (solid, [(x, y), ...])."""
    runs = []
    for solid, edge in starmap(_edge, pairwise(points)):
        if runs and runs[-1][0] == solid:
            runs[-1][1].append(edge[1])
        else:
            runs.append((solid, edge))
    return runs


def _point(prof: dict, index: int, slot: str) -> tuple:
    kmh, count = prof[slot]
    return kit.x_of(index), kit.y_of(kmh), count >= SOLID_MIN_SEGMENTS


def points(prof: dict) -> list:
    """Return (x, y, solid) for every slot present in the profile."""
    # Weight belongs to the edge, not to the point. A piece of line joining a
    # well-observed slot to a thin one rests on the thin one, so it is drawn
    # thin. Weighting by the point drew the crossing edge solid going into the
    # night and dashed coming back out, which is one edge weighted two ways.
    return [
        _point(prof, index, slot)
        for index, slot in enumerate(ALL_SLOTS)
        if slot in prof
    ]
