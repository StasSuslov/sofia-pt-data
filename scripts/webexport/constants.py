"""Constants of the web export: the decisions this export has to pick."""

# ─── Constants: web export decisions (this file's job to pick, per task) ───

# A bin built from a single sample isn't a median, it's one raw pairwise
# speed reading relabeled as an aggregate — there is nothing for a median to
# be robust *to* with one point, which is the entire justification D4 gives
# for using a median at all. n>=2 is the smallest cutoff that means "more
# than one observation agreed", not the smallest cutoff that looks tidy.
# The cost of raising it is worth stating with the archive it was measured
# against, because it moves as weekdays accumulate. Counted 2026-09-01 over
# the 687,014 bins in data/sofia/processed/typical_weekday.json, built from
# the three weekdays 2026-08-27, 2026-08-28 and 2026-08-31: n>=2 keeps
# 422,687 bins (61.5%), n>=3 keeps 220,975 (32.2%), n>=5 keeps 72,027
# (10.5%). n>=3 is the smallest count where a median actually insulates
# against either extreme, and both it and n>=5 are defensible choices, but
# on this little history they discard most of the network. Re-count before
# quoting these; an earlier version of this comment carried 26.5% and 7.6%
# from a smaller archive and drifted out of date without saying so.
# n_samples still ships with every surviving bin (see module docstring), so
# a thin median is the client's problem to render distinctly, not this
# cutoff's problem to hide. Revisit the default as more weekdays accumulate;
# nothing about the file format below needs to change to raise it later.
MIN_SAMPLES_DEFAULT = 2

# ~1.11 m of latitude and ~0.82 m of longitude at Sofia's ~42.7 deg N — below
# the accuracy of the consumer GPS units that produced the underlying feed,
# so a 6th decimal would double the digit-string cost for precision the
# source data doesn't have. A 4th decimal (~11 m) is comparable to a lane
# width and would visibly misplace a segment against its own street.
COORD_DECIMALS = 5

# Douglas-Peucker tolerance for a segment's drawn polyline, in metres of the
# same local plane point_along_shape() interpolates in — not degrees, where
# one unit is ~111 km of latitude and ~82 km of longitude at Sofia's ~42.7
# deg N and a single number would mean two different distances depending on
# the heading.
#
# Why simplify at all. Measured 2026-09-04 over the 26,111 segments this
# export writes for the current schedule period, against the six snapshots
# in data/sofia/static: a 200 m bin holds 10.59 points once its own
# shapes.txt vertices are kept. format_version 1 threw all of them away and
# drew the chord between the bin's two ends, 2.00 points, which put the line
# a median of 3.4 m from the true path, 36.0 m at p90, 68.1 m at p99 and
# 90.6 m at worst, off by more than 5 m on 44.0% of bins. At Sofia's block
# scale that is the difference between a line on the street and a line
# through the building beside it.
#
# Why 5 m. Douglas-Peucker's retention test is the distance from a dropped
# vertex to the line drawn in its place, so the tolerance is a bound on the
# residual error rather than an average of it — measured worst case on the
# archive above, 4.999 m. 5 m is the coarsest tolerance still under one lane
# width (~3.5 m) plus the GPS error already in the source: past it the
# simplification would be the largest term in a drawn position instead of a
# term under the noise. What it is paid with is points, and points are file
# size — re-measured 2026-09-08 over the 27,138 segments the current period
# then held: 2.72 points per bin instead of 2.00, geometry.json 345,488 B
# gzipped against the 251,598 B the same segments cost as chords in this same
# CSR layout, under a 1 MB first-load budget. Not simplifying at all is what
# the budget rules out, not what it merely makes expensive — keeping all
# 10.55 vertices puts geometry.json alone at 1,203,512 B gzipped, over the
# budget before a single timeslot is fetched.
#
# Both numbers are cheap to re-measure and will drift as the network
# changes; re-count before quoting them.
SIMPLIFY_TOLERANCE_M = 5.0

# ─── Slowdown highlight: "slower than this segment usually is" ─────────────
#
# A typical-weekday bundle's geometry.json carries typical_kmh: per segment,
# the median of its own per-slot medians over the slots it has, in the same
# integer km/h the timeslot files publish. The absolute colour scale answers
# "is this street slow", which is a different question from "is this street
# slower than usual right now" — measured 2026-09-08 on the current period,
# of the 1,209 segments below 10 km/h at 08:00, 55.4% are still below it at
# 19:00 and 48.1% at 22:00, so one colour was answering both questions and a
# reader could not tell the halves apart. The base colour stays absolute and
# a segment is marked *additionally*, so a permanently congested street does
# not disappear from the map when it is congested on schedule.
#
# The predicate is the frontend's to evaluate; the two numbers it needs are
# published here (manifest web_export.slowdown_highlight) so a reader of the
# export can check the map against them. A segment is marked when BOTH hold:
#     (1) slot_kmh <= SLOWDOWN_RATIO_MAX * typical_kmh
#     (2) typical_kmh - slot_kmh >= SLOWDOWN_ABS_MIN_KMH
#
# 4 km/h is the one number the data picks: separation between segments that
# ease off by evening and segments that are always stopped peaks at 4 in
# eight of the nine ratio/gate configurations measured. 0.70 is not — the
# data leaves it unbounded from below, exactly as PERIOD_TOLERANCE_PCT's
# 0.5% is unbounded, and it is insurance rather than a fitted value. What
# bounds it from above is day-to-day spread: a single day sits below 0.80 of
# its own across-day median in 20.2% of (segment, slot) cells, so a looser
# ratio would mark ordinary variation between weekdays.
SLOWDOWN_RATIO_MAX = 0.7
SLOWDOWN_ABS_MIN_KMH = 4

# Segments with fewer filled slots than this get typical_kmh = null: the rule
# knows nothing about them. Not a cliff in the false-flag rate (19.3% at 15
# against 26.0% at 10 and 10.7% at 30) but the point where the baseline stops
# being made of night: at 1-4 filled slots, 19.8% of them are night slots; at
# >= 15, none are. A baseline that is half free-flow night traffic would mark
# a segment for being busy in daylight.
TYPICAL_MIN_SLOTS = 15


# Below this calendar-day coverage_pct (from generate_manifest.py's output),
# or if the day was still in progress when its manifest was built, a day
# going into the median gets flagged as incomplete in this export's own
# manifest. CLAUDE.md section 9 notes coverage_pct can be inflated at day
# boundaries by an open defect in analyze_gaps/build_manifest — a day
# flagged here is genuinely thin; a day *not* flagged is not proof of a
# full day, only the best signal currently on disk.
INCOMPLETE_COVERAGE_PCT = 99.0

# Written into every manifest and index.
FORMAT_VERSION = 2

# The mode name of the typical-weekday bundles; a day bundle's mode is its date.
TYPICAL_MODE = 'typical_weekday'

UTF8 = 'utf-8'
MANIFEST_NAME = 'manifest.json'

# GTFS-RT speeds are m/s, the published colour scale is km/h.
MS_TO_KMH = 3.6

# A float zero that is a distance, kept apart from the integer 0 of an index.
ZERO_M = float(0)
