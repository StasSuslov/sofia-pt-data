"""Named thresholds and unit constants for the segment-speed pipeline."""

# ─── Constants: segmentation & aggregation (task spec) ─────────────────────

SEGMENT_LENGTH_M = 200.0  # fixed-length bin along a shape's polyline
TIMESLOT_MINUTES = 15  # local-time bin width
EARTH_RADIUS_M = 6371000.0

# ─── Constants: rejection thresholds ────────────────────────────────────────
# Named here, not inlined, so every cutoff is visible and arguable in one
# place (task requirement).

# A gap this long between two snapshots of the same (vehicle, trip) means
# something happened in between that a straight-line speed can't represent
# (a layover, a missed poll run, a detour) — treat it as a break, not a slow
# stretch. Chosen as a round 10 minutes: several times the 45s poll interval
# Sofia is collected at, well past normal jitter,
# still short enough that a real single hop across it would be plausible.
# (collect.py's cadence is per city now: cities/<slug>.json, poll_interval_sec.)
MAX_TIME_GAP_SEC = 600

# A poll that lands within half the city's poll interval of the previous one
# comes from a collector restart, not from the cadence: the feed has not
# refreshed in between, so a hop across it turns a second or two into a
# near-zero or inflated speed. On 2026-10-16 a restart put two polls 1 s
# apart, and 569 of 681 vehicles carried the same vehicle_ts in both. The
# later fix is skipped and the next hop runs from the earlier one. Over the
# heartbeat to 2026-10-16 the ordinary gaps are 45 s (94,211) and 46 s (27),
# one is 41 s, and eight restart gaps run 1-38 s. The raw file of 2026-08-27,
# which predates the heartbeat, adds a ninth of 12 s at 13:31:05 UTC. Half the
# cadence catches seven of the nine and no ordinary gap. The interval itself
# is per city: cities/<slug>.json, poll_interval_sec.
MIN_TIME_GAP_CADENCE_FRACTION = 0.5

# Task-specified cutoff. Doubles as a wide margin over how fast a bus/tram/
# trolley actually moves anywhere in the network, even on the ring road.
MAX_SPEED_KMH = 100.0
KMH_PER_MS = 3.6  # unit factor: the feed and thresholds speak km/h
MAX_SPEED_MS = MAX_SPEED_KMH / KMH_PER_MS

# A vehicle's projected position along its own shape can wobble backward by
# a few tens of meters from GPS noise, or from snapping onto a slightly
# wrong segment near a shape self-crossing — that's noise, not the vehicle
# reversing. Beyond this, treat it as a real problem with the match (wrong
# branch, a pull-in/pull-out, a real reversal) and drop the pair.
BACKWARD_TOLERANCE_M = 50.0

# Slack added on both ends of the physically-plausible search window (see
# window_bounds()) to absorb GPS noise — without it, a vehicle sitting still
# right at a segment boundary, or one moving at exactly the noisy edge of
# its expected span, can fall just outside a window built from zero slack.
SEARCH_MARGIN_M = 300.0

# Task-specified validation cutoff: how far the derived speed must be from
# the feed's own speed_ms before it counts as a meaningful disagreement.
VALIDATION_DIFF_THRESHOLD_KMH = 20.0

# How far two weekdays' timetables may differ and still pool into one median.
# The measure is churn (schedule_churn): per route, the difference between the
# two days' scheduled trip counts, summed as absolute values, as a share of
# the reference day's trips: 73 trips of movement against 27 August 2026's
# 14,608, 83.1 against 14 September's 16,628.
#
# Measured on the archive to 2026-10-08, each weekday read out of the
# snapshot in force on it (what the pipeline does) and held against the first
# day of the period it would join, the feed pins this threshold from both
# sides. From above, every boundary it has published, each against the
# outgoing period's first day:
#   a three-day variant, 2026-10-05 (A117 added, TM22 and TM56 cut)   2.18%
#   the autumn weekday timetable, 2026-09-08             448 trips    3.01%
#   three trams starting and 10TM ending, 2026-08-31                  4.07%
#   the step to the school year, 2026-09-14            1,727 trips   11.52%
# and 2026-10-08 at 1.94% against the 5 October variant. That one is no
# longer a boundary — it returns to the timetable of 29 September, 37 trips
# (0.22%) from the 14 September reference, and assign_schedule_periods()
# now rejoins it there — but a threshold above 1.94% would pool it with the
# variant instead, so it still bounds from above. From below, the school
# timetable moved in small steps through September: against 14 September
# later weekdays differ by 34 to 82 trips, and 18 September by 82 of 16,628
# (0.49%) against a limit of 83.1. Any threshold under 0.49% splits that
# period on 18 September, and exact signatures would cut 14 September to
# 2 October into seven periods.
#
# So the evidence puts the threshold inside (0.49%, 1.94%), and 0.5% clears
# the lower edge by about one trip. It was set before September supplied that
# edge, when no day inside a period had moved at all and the value was
# insurance against a drifting feed, and it has not been moved since the edge
# appeared: an agency adjusting its timetable in steps this size, this often,
# needs a different rule, not a different number. METHODOLOGY.md says so
# rather than presenting 0.5% as a value the data picked.
#
# Which snapshot answers for a past date matters as well. Read the 267
# weekdays from 2026-08-27 to 2027-09-04 out of the 2026-09-04 snapshot alone
# and two boundaries appear that the feed never published, 9.42% at the end of
# August and 0.60% between 2 and 3 September: the agency erodes calendar rows
# for dates already past, and that snapshot no longer carries 1,066 of the
# trips 2026-08-27 ran or 89 of 2026-09-02's (all of the 89 on route A53),
# which both days' own snapshots do carry. A pipeline reading history out of
# the latest feed would see a 0.60% step between two days that ran the same
# timetable, and 0.5% would split them. This one reads each day against the
# feed it started under, so the step never arises.
PERIOD_TOLERANCE_PCT = 0.5

# A weekday scheduling fewer trips than this share of the median weekday is
# running a holiday's service, not a new timetable. 2026-09-07 is a Monday
# carrying the same signature as the weekend either side of it: 10,149 trips
# against 14,907 on the Thursday before, 35% churn, where the timetable
# changes on record move 2.18% to 11.52%. Over the same 267 weekdays eleven
# look like this, and their dates line up with Bulgarian public holidays.
# This line has room the churn one lacks: against a median of 15,595 trips
# the heaviest such day reaches 65.90% and the lightest ordinary weekday
# 93.67%, so any threshold between those two selects the same eleven days.
# Both edges are the pipeline's own reading; the upper one falls to 86.84% if
# 2026-08-27 is read out of the 2026-09-04 snapshot instead, for the erosion
# reason above. The archive itself, 32 weekdays to 2026-10-09 each read
# against its own snapshot, has a median of 16,662 now that the school
# timetable runs: its two holidays reach 60.85% (2026-09-07) and 63.84%
# (2026-09-22), and the lightest ordinary weekday 87.67% (2026-08-27).
# 80% sits inside every one of these bands. Such a day leaves the median
# entirely (D4 asks for a typical weekday) rather than founding a period of
# its own, which is what the threshold alone would have handed it: one day,
# and a median over one day is that day.
REDUCED_SERVICE_PCT = 80.0

REJECT_REASONS = (
    # trip_id from the RT feed has no trips.txt row (~0.7% per CLAUDE.md)
    'trip_not_in_static',
    # trip resolved, but its shape_id has <2 usable points in shapes.txt
    'shape_not_found',
    # duplicate/out-of-order snapshot_ts within a (vehicle, trip) group
    'non_positive_time_delta',
    # dt below poll_interval_sec * MIN_TIME_GAP_CADENCE_FRACTION
    'gap_too_short',
    'gap_too_large',  # dt > MAX_TIME_GAP_SEC
    # along-shape distance dropped by more than BACKWARD_TOLERANCE_M
    'moved_backward',
    'speed_too_high',  # derived speed > MAX_SPEED_MS
)
