"""Paired same-segment comparisons: the numbers the reports quote."""

import statistics

from dailyprofile.profile import window_median


def _read(speeds: dict, spec) -> float | None:
    """A slot value when spec is a slot string, else a window median."""
    return (
        speeds.get(spec)
        if isinstance(spec, str)
        else window_median(speeds, spec)
    )


def _segment_pair(key, speeds: dict, other: dict | None, specs: tuple):
    """(value at first, value at second) for one segment, or None."""
    speeds_other = speeds if other is None else other.get(key)
    if speeds_other is None:
        return None
    return _read(speeds, specs[0]), _read(speeds_other, specs[1])


def pairs(by_segment: dict, first, second, other: dict | None = None) -> tuple:
    """Collect value pairs over the segments that carry both sides.

    [(value at a, value at b)] over segments carrying both, plus the count
    dropped for reading exactly 0.

    a and b are either a slot ("08:00") or a window ("07:00", "18:45").

    With other, a is read from by_segment and b from other: the same segment at
    the same time of day under two schedule periods. Segment keys address
    geometry by content, so a key that survives a timetable change is the same
    piece of street; a shape the new period does not run simply drops out.

    Both tables in the report draw their population from here, so they cover
    the same segments and a reader comparing them compares like with like.

    A median of exactly 0 km/h over five weekdays says the segment produced no
    movement at all in that slot, which describes a vehicle sitting at a
    terminus or a position that never updated, not the speed of a street. They
    are dropped and counted, never dropped in silence.
    """
    found = (
        _segment_pair(key, speeds, other, (first, second))
        for key, speeds in by_segment.items()
    )
    both = [pair for pair in found if pair and None not in pair]
    kept = [pair for pair in both if 0 not in pair]
    return kept, len(both) - len(kept)


def _quartiles(diffs: list) -> tuple:
    size = len(diffs)
    return diffs[size // 4], diffs[3 * size // 4]


def paired(by_segment: dict, first, second, other: dict | None = None) -> dict:
    """Per-segment difference a minus b, over segments carrying both."""
    kept, zeroes = pairs(by_segment, first, second, other)
    if not kept:
        return {'n': 0, 'zeroes': zeroes}
    diffs = sorted(left - right for left, right in kept)
    return {
        'n': len(diffs),
        'zeroes': zeroes,
        'median_diff': statistics.median(diffs),
        'share_a_slower': sum(1 for diff in diffs if diff < 0) / len(diffs),
        'iqr': _quartiles(diffs),
    }


def persistence(
    by_segment: dict,
    cut_kmh: float,
    first: str,
    second: str,
) -> dict:
    """Of the segments under cut_kmh at a, how many are still under it at b.

    This is the report's second claim without the ratio threshold the first
    draft leaned on. A ratio of peak to midday needs a cutoff nothing in the
    data picks: the distribution of that ratio runs smooth from p10 0.73 to
    p90 1.17, so any line through it is mine, not the city's. A plain speed
    cut is a stated choice a reader can move, and the answer barely moves
    with it.
    """
    kept, zeroes = pairs(by_segment, first, second)
    slow_at_a = [pair for pair in kept if pair[0] < cut_kmh]
    still = sum(1 for _, later in slow_at_a if later < cut_kmh)
    return {
        'population': len(kept),
        'zeroes': zeroes,
        'slow_at_a': len(slow_at_a),
        'still_slow': still,
        'share': still / max(len(slow_at_a), 1),
    }
