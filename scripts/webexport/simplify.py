"""Douglas-Peucker simplification in the local metre plane."""

from segment_speeds import project_onto_segment


def _farthest(points: list, low: int, high: int, tolerance_m: float) -> int:
    """Index of the vertex between `low` and `high` beyond the tolerance.

    The farthest from the chord wins, the first on a tie; -1 when none is.
    """
    start = points[low]
    best, best_dist = -1, tolerance_m
    for index in range(low + 1, high):
        _, dist = project_onto_segment(
            *points[index],
            *start,
            *points[high],
        )
        if dist > best_dist:
            best, best_dist = index, dist
    return best


def _kept_indices(points: list, tolerance_m: float) -> set:
    """Indices of the vertices Douglas-Peucker retains."""
    kept = {0, len(points) - 1}
    stack = [(0, len(points) - 1)]
    while stack:
        low, high = stack.pop()
        far = _farthest(points, low, high, tolerance_m)
        if far >= 0:
            kept.add(far)
            stack.extend(((low, far), (far, high)))
    return kept


def simplify_rdp(points: list, tolerance_m: float) -> list:
    """Douglas-Peucker over a list of (x, y) in *metres* (the local plane).

    Keeps the two endpoints and every vertex further than `tolerance_m`
    from the polyline that would replace it.

    Distance is measured to the retained *segment*, not to the infinite line
    through its endpoints. That is both the quantity the guarantee is about
    ("no dropped vertex ends up more than the tolerance from the drawn
    line") and the variant that stays finite when a sub-polyline closes on
    itself, which a shape with a turning loop inside one bin does.

    Iterative rather than recursive: the recursion depth of this algorithm is
    the vertex count in the worst case, and a dense shape's 200 m bin is not
    worth a stack limit.
    """
    if len(points) <= 2:
        return points
    kept = _kept_indices(points, tolerance_m)
    return [point for index, point in enumerate(points) if index in kept]
