"""Prose of manifest.json: the limitations and rationales it states.

Every string here is published text, kept byte for byte. The numbers a
string quotes from the constants module are interpolated, so the text moves
with the value it describes.
"""

from webexport.constants import (
    SIMPLIFY_TOLERANCE_M,
    SLOWDOWN_ABS_MIN_KMH,
    SLOWDOWN_RATIO_MAX,
    TYPICAL_MIN_SLOTS,
)

_RATIO = format(SLOWDOWN_RATIO_MAX, 'g')
_TOLERANCE = format(SIMPLIFY_TOLERANCE_M, 'g')

GEOMETRY_LIMIT = (
    "Segment geometry is the shape's own polyline inside the 200 m bin, "
    f'simplified with Douglas-Peucker at {_TOLERANCE} m: every '
    f'dropped shapes.txt vertex lies within {_TOLERANCE} m of the '
    'line drawn in its place, so a turn or roundabout inside a segment is '
    'drawn, but not to the last metre. Through format_version 1 this was the '
    "straight chord between the bin's two ends, which departed from the true "
    'path by a median of 3.4 m and up to 90.6 m.'
)

SPEED_LIMIT = (
    'Speed is an integer km/h for map colouring, not the scientific record. '
    'The underlying float m/s and every individual sample it was built from '
    'live in segment_speeds_<date>.jsonl and typical_weekday.json, published '
    'as-is (D5).'
)

TYPICAL_LIMIT = (
    'typical_kmh is a baseline, not a verdict on one street. A segment is '
    'marked slower than usual when the slot shown is at or below '
    f'{_RATIO} of its typical_kmh and at least '
    f'{SLOWDOWN_ABS_MIN_KMH} km/h under it, and a single such mark reproduced '
    'between the two halves of the 31 August to 4 September 2026 period (five '
    'weekdays) in 36.9% of cases, measured on 2026-09-08 and not re-measured '
    'on other periods. Read the marks as where the network slows at peak, not '
    'as evidence about one segment; segments with fewer than '
    f'{TYPICAL_MIN_SLOTS} filled slots carry typical_kmh null and are never '
    'marked.'
)

PERIOD_LIMIT = (
    'This median covers only the days that ran one published timetable '
    '({days} weekdays from {first} to {last}, listed in days_in_median; '
    '{routes} routes and {trips} trips). A timetable the agency returns to '
    'after a variant rejoins its period, so a weekday inside that range can '
    'belong to another one. Days under a different timetable are exported as '
    'a separate period and are never averaged into this one. Two timetables '
    "that differ only in departure times, with every route's trip count "
    'unchanged, are indistinguishable to that split and would share one '
    'period.'
)

MIN_SAMPLES_RATIONALE = (
    'a median needs at least two independent observations to be an aggregate '
    'rather than a single relabeled raw sample; n_samples still ships with '
    'every surviving bin so a thin median stays visually distinguishable '
    'client-side instead of this cutoff hiding it (D4; CLAUDE.md section 6, '
    "'name the limitation before someone else does')"
)

COORD_RATIONALE = (
    '~1.1 m at this latitude, below the accuracy of the GPS units behind the '
    'underlying feed'
)

SIMPLIFY_RATIONALE = (
    'a segment ships the shapes.txt polyline inside its 200 m bin, simplified '
    'in metres on the same local plane the projection uses, not in degrees, '
    'where one tolerance would mean two distances depending on heading. '
    'Re-measured 2026-09-08 over the 27,138 segments the current schedule '
    'period then held: a bin holds 10.55 points with every vertex kept, and '
    'dropping all of them for the straight chord of format_version 1 (2.00 '
    'points) moved the drawn line off the true path by a median of 3.3 m, '
    '35.7 m at p90, 68.1 m at p99 and 90.6 m at worst, by more than 5 m on '
    "43.9% of bins. Douglas-Peucker's retention test is the distance from a "
    'dropped vertex to the line replacing it, so this tolerance is a bound on '
    'the residual error rather than an average of it (worst case measured on '
    'that archive: 4.999 m). 5 m is the coarsest tolerance still under a lane '
    'width plus the GPS error already in the source, so the simplification '
    'stays below the noise it sits on. Paid for in points and therefore file '
    'size: 2.72 points per bin instead of 2.00, and 345,488 B of gzipped '
    'geometry against the 251,598 B the same segments cost as chords in this '
    'same layout — the chord release served them from four endpoint arrays '
    'with no point_offset, a layout in which they would be 269,539 B, so the '
    'two releases are not a like-for-like pair. Keeping every vertex is not '
    'an option the 1 MB first-load budget leaves open: at 10.55 points per '
    'bin geometry.json alone gzips to 1,203,512 B'
)

SLOWDOWN_FORMULA = (
    "median of the segment's per-slot medians over its filled slots"
)

SLOWDOWN_PREDICATE = (
    'a segment is marked slower than usual in a slot when slot_kmh <= '
    f'{_RATIO} * typical_kmh AND typical_kmh - slot_kmh >= '
    f'{SLOWDOWN_ABS_MIN_KMH} km/h; both conditions, not either. Evaluated '
    'client-side against the arrays published here, so the same numbers '
    'reproduce the marks on the map'
)

SLOWDOWN_RATIONALE = (
    'the absolute colour scale answers whether a street is slow, which is not '
    'the question of whether it is slower than it usually is: of the 1,209 '
    'segments below 10 km/h at 08:00 in the current period, 55.4% are still '
    'below it at 19:00 and 48.1% at 22:00, so one colour was carrying two '
    'meanings. The mark is drawn over the absolute colour rather than '
    'replacing it, so a permanently congested segment stays dark. The '
    f'{SLOWDOWN_ABS_MIN_KMH} km/h floor is the number the measurement '
    'chooses: separation between segments that ease off by evening and '
    'segments that are always stopped peaks at 4 km/h in eight of the nine '
    f'ratio/gate configurations measured. The {_RATIO} ratio is '
    'not chosen by the data and is named as insurance rather than a fitted '
    'value, the same way the 0.5% schedule-period churn tolerance is: nothing '
    'in the archive bounds it from below. What bounds it from above is '
    'day-to-day spread — a single day sits below 0.80 of its own across-day '
    'median in 20.2% of (segment, slot) cells, so a looser ratio would mark '
    f'ordinary variation between weekdays. The {TYPICAL_MIN_SLOTS}-slot gate '
    'is not a cliff in the false-flag rate either (19.3% at 15 slots against '
    '26.0% at 10 and 10.7% at 30, no break between them); it is where the '
    'baseline stops being made of night traffic, which is the free-flow '
    'reading a daytime slot would then be marked against: at 1-4 filled slots '
    '19.8% of them are night slots, at 15 or more none are. The formula for '
    'the baseline is not chosen by the data at all — median, mean, p60 and '
    'p75 differ by 1 to 4 points of separation with no stable winner — and it '
    'is a median because D4 already defines the typical weekday as one'
)


def shapes_limit(
    *,
    mode: str,
    shapes_written: int,
    total_static_shapes: int,
    lost: int,
    min_samples: int,
) -> str:
    """Say how many shapes appear, and how many kept no bin."""
    # The number the reader checks against geometry.json is the number of
    # shapes written, not the number observed: observation is counted before
    # the min_samples threshold, and shapes that lose every bin to it never
    # reach the file. Naming the observed count as what "appears here"
    # reports one object and points at another.
    line = (
        'Only shapes actually observed in the source archive appear here: '
        f"{shapes_written} of the static feed's {total_static_shapes} shapes "
        f'for this export ({mode}).'
    )
    if lost <= 0:
        return line
    plural = '' if lost == 1 else 's'
    verb = 'was' if lost == 1 else 'were'
    return ''.join((
        line,
        f' {lost} further shape{plural} {verb} observed but kept no ',
        f'bin at the {min_samples}-sample threshold.',
    ))


def routes_limit(multi_route_shapes: int) -> str:
    """Say how many shapes serve more than one route."""
    plural = '' if multi_route_shapes == 1 else 's'
    verb = 'serves' if multi_route_shapes == 1 else 'serve'
    return (
        'Route metadata is per shape, not per segment. '
        f'{multi_route_shapes} shape{plural} in this export {verb} more '
        'than one route, so shape_route_ids is a list; no shape here mixes '
        'route types, so shape_route_type is a single GTFS value (0 tram, '
        '1 metro, 3 bus, 11 trolleybus) and is null if that ever stops '
        'holding.'
    )


def period_limit(days: int, period: dict) -> str:
    """Describe the timetable period a typical-weekday median covers."""
    return PERIOD_LIMIT.format(
        days=days,
        first=period['first_date'],
        last=period['last_date'],
        routes=period['route_count'],
        trips=period['trip_count'],
    )
