"""manifest.json: what an export contains, and what it cannot claim."""

import datetime as dt

from segment_speeds import (
    BACKWARD_TOLERANCE_M,
    MAX_SPEED_KMH,
    MAX_TIME_GAP_SEC,
    SEARCH_MARGIN_M,
    SEGMENT_LENGTH_M,
    TIMESLOT_MINUTES,
)

from webexport import manifest_text as text
from webexport.constants import (
    COORD_DECIMALS,
    FORMAT_VERSION,
    SIMPLIFY_TOLERANCE_M,
    SLOWDOWN_ABS_MIN_KMH,
    SLOWDOWN_RATIO_MAX,
    TYPICAL_MIN_SLOTS,
    TYPICAL_MODE,
)
from webexport.model import ManifestInputs


def _dropped_pct(before: int, dropped: int) -> float | None:
    if not before:
        return None
    return round(100 * dropped / before, 2)


def _slowdown_highlight() -> dict:
    return {
        'typical_speed_field': 'typical_kmh (geometry.json)',
        'typical_speed_formula': text.SLOWDOWN_FORMULA,
        'min_filled_slots': TYPICAL_MIN_SLOTS,
        'ratio_max': SLOWDOWN_RATIO_MAX,
        'absolute_min_kmh': SLOWDOWN_ABS_MIN_KMH,
        'predicate': text.SLOWDOWN_PREDICATE,
        'rationale': text.SLOWDOWN_RATIONALE,
    }


def _web_export(inputs: ManifestInputs) -> dict:
    bins_dropped = inputs.bins_before - inputs.bins_after
    segments_dropped = inputs.pairs_before - inputs.pairs_after
    web_export = {
        'min_samples_threshold': inputs.min_samples,
        'min_samples_rationale': text.MIN_SAMPLES_RATIONALE,
        'coordinate_decimal_places': COORD_DECIMALS,
        'coordinate_precision_rationale': text.COORD_RATIONALE,
        'geometry_simplification': 'douglas_peucker',
        'simplify_tolerance_m': SIMPLIFY_TOLERANCE_M,
        'simplify_tolerance_rationale': text.SIMPLIFY_RATIONALE,
        'bins_total_before_threshold': inputs.bins_before,
        'bins_retained': inputs.bins_after,
        'bins_dropped': bins_dropped,
        'bins_dropped_pct': _dropped_pct(inputs.bins_before, bins_dropped),
        'segments_total_before_threshold': inputs.pairs_before,
        'segments_retained': inputs.pairs_after,
        'segments_dropped_pct': _dropped_pct(
            inputs.pairs_before,
            segments_dropped,
        ),
        'segments_missing_from_static_feed': inputs.missing_shapes,
    }
    if inputs.mode == TYPICAL_MODE:
        web_export['slowdown_highlight'] = _slowdown_highlight()
    return web_export


def _limitations(inputs: ManifestInputs) -> list:
    limitations = [
        text.GEOMETRY_LIMIT,
        text.SPEED_LIMIT,
        text.shapes_limit(
            mode=inputs.mode,
            shapes_written=inputs.shapes_written,
            total_static_shapes=inputs.total_static_shapes,
            lost=inputs.shapes_observed - inputs.shapes_written,
            min_samples=inputs.min_samples,
        ),
        text.routes_limit(inputs.multi_route_shapes),
    ]
    if inputs.mode == TYPICAL_MODE:
        limitations.append(text.TYPICAL_LIMIT)
    # Whatever this particular city's feed does not cover — Sofia's metro, for
    # one — comes from its profile rather than from a list in this file, which
    # every city's export passes through.
    limitations.extend(inputs.city_limitations)
    if inputs.schedule_period:
        # Weekday count and the list, not the range alone: a timetable that
        # returns after a short variant rejoins its period (segment_speeds.py,
        # assign_schedule_periods), so the range can enclose another period's
        # days.
        limitations.append(
            text.period_limit(
                len(inputs.days_in_median),
                inputs.schedule_period,
            ),
        )
    return limitations


def _thresholds() -> dict:
    return {
        'segment_length_m': SEGMENT_LENGTH_M,
        'timeslot_minutes': TIMESLOT_MINUTES,
        'max_time_gap_sec': MAX_TIME_GAP_SEC,
        'max_speed_kmh': MAX_SPEED_KMH,
        'backward_tolerance_m': BACKWARD_TOLERANCE_M,
        'search_margin_m': SEARCH_MARGIN_M,
    }


def build_manifest(inputs: ManifestInputs) -> dict:
    """Assemble manifest.json for one bundle."""
    return {
        # 2: segment geometry is a simplified polyline in CSR layout
        # (lat/lon/point_offset), not four endpoint arrays of a chord.
        'format_version': FORMAT_VERSION,
        'generated_at': dt.datetime.now(dt.UTC).isoformat(),
        'mode': inputs.mode,  # "typical_weekday" or a specific "YYYY-MM-DD"
        # Which timetable this bundle's days ran, from typical_weekday.json's
        # schedule_periods; null for a single-day export, which needs no split.
        'schedule_period': inputs.schedule_period,
        # Who the data belongs to and under what licence, copied from the city
        # profile so the page can credit the operator whose feed it is showing.
        # null when the export could not resolve a city: no attribution is a
        # visible gap, while a default one would publish the wrong licence.
        'attribution': inputs.attribution,
        'source': inputs.source,
        'days_processed': inputs.days_processed,
        'days_in_median': inputs.days_in_median,
        'incomplete_days': inputs.incomplete_days,
        'preprocessing_thresholds': _thresholds(),
        'web_export': _web_export(inputs),
        'segment_count': inputs.pairs_after,
        'timeslot_count': len(inputs.timeslot_labels),
        'timeslots': sorted(inputs.timeslot_labels),
        'known_limitations': _limitations(inputs),
    }
