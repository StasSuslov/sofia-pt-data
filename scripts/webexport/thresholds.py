"""The minimum-sample cut and the segments that survive it."""


def segment_pairs(bins: dict) -> set:
    """Return the (shape_key, segment_index) pairs referenced by `bins`.

    shape_key, not the bare shape_id, so two geometry versions of what was
    once one shape_id stay two distinct segments here too.
    """
    return {(shape_key, seg_idx) for shape_key, seg_idx, _ in bins}


def apply_threshold(bins: dict, min_samples: int) -> tuple:
    """Return (retained_bins, n_before, n_after)."""
    retained = {
        key: stats for key, stats in bins.items() if stats[1] >= min_samples
    }
    return retained, len(bins), len(retained)
