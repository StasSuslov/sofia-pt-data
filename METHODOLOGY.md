# Methodology

I wrote this down before I had any findings to report, so that a later
result can be checked against a method that had no chance to bend around
it.

The method is archived on Zenodo next to the code that implements it:
[10.5281/zenodo.22256653](https://doi.org/10.5281/zenodo.22256653). When I
change the method, the change goes out as a new version of that record, so
you can always read a result against the method as it stood on the day the
result was produced.

The data this method runs on is archived as its own record under CC BY 4.0:
[10.5281/zenodo.22285128](https://doi.org/10.5281/zenodo.22285128). Cite
both when you cite a result.

## Scope

I archive Sofia's public transport GTFS-RT and GTFS Static feeds and run a
repeatable pipeline over the result. The analyses I mean to keep repeating
are congestion, schedule reliability and network coverage. Every
city-specific value (feed URLs, bounding box, timezone, district
boundaries) lives in configuration, so the same pipeline can run against a
second city without a rewrite.

## Data sources

- **GTFS-RT vehicle positions.** Polled every 30–60 seconds from Sofia's
  public transport open data portal,
  [urbandata.sofia.bg](https://urbandata.sofia.bg) (Level 1, CC BY 4.0).
  Snapshot schema: `snapshot_ts, vehicle_id, route_id, trip_id, lat, lon,
  bearing, speed_ms, vehicle_ts`.
- **GTFS Static.** Routes, stops, schedules and shapes, from the same
  portal. It gives me the collection bounding box and everything that works
  without real-time data (route and stop coverage, accessibility).
- **OpenStreetMap**, through the Overpass API. District boundaries and road
  network, for the accessibility analysis.

I enter nothing by hand, and I assume no schedule or network fact that a
feed has not confirmed.

## Collection method

- Polling interval: 30–60 seconds, continuous. Every poll writes a
  heartbeat line whether or not it returned vehicle records, and the line
  says which of three things happened: a fetch error, an empty response, or
  a feed that answered with no vehicles to report. Without that line, a
  dead collector and a quiet feed leave the same trace in the archive.
- **Bounding-box filter.** GTFS-RT feeds emit coordinate "teleports" from
  time to time, a vehicle position far outside any plausible network.
  Records outside a bounding box are dropped at collection time.
  `scripts/derive_bbox.py` derives the box from the static feed's
  `stops.txt` and `shapes.txt`, and sizes it to hold the full extent of the
  network's routes and stops. A box that clips real outlying routes turns
  them into what a reader sees as a data gap, with nothing in the output to
  say otherwise, so the derivation has to be re-run against each refreshed
  static feed and the box re-checked against it.
- Speed between consecutive snapshots is my own figure, interpolated from
  position and elapsed time. The feed's own speed field is a separate
  reading, published next to it (see Known limitations).

## Preprocessing

Two scripts stand between the raw archive and anything a reader sees.
`scripts/segment_speeds.py` turns vehicle positions into speed samples
attached to a piece of network; `scripts/export_web.py` turns the aggregate
into the files a map fetches. Every threshold named below is written into
the output itself (`thresholds` in `typical_weekday.json`,
`preprocessing_thresholds` in the web export's `manifest.json`), so the
figures you check come out of the run rather than out of this document.

**Pairing.** Records are grouped by `(vehicle_id, trip_id)` and sorted by
`snapshot_ts`. A speed sample always comes from two consecutive positions
within one such group, never from two vehicles and never across two
separate runs of the same vehicle over the same road.

**Projection.** `trips.txt` maps `trip_id` to a `shape_id`, and `shapes.txt`
gives that shape as an ordered polyline carrying the cumulative haversine
distance at every vertex. A raw position is projected onto the nearest
polyline segment and recorded as a distance along the shape. That
nearest-segment search runs in a local equirectangular projection, because
projecting a point onto a line segment needs planar geometry. Every distance
that reaches the output is the cumulative haversine value, so the planar
approximation decides which segment is closest and nothing more; how far a
vehicle travelled never passes through it.

**Sliding window.** The search is confined to the vertices around the
previous match: from 350 m behind it (a 50 m backward tolerance plus a 300 m
margin) to `100 km/h × dt` ahead of it plus the same margin, located by
binary search over the cumulative distances. The window is anchored on
distance rather than on a count of vertices, because vertex spacing in this
feed is uneven: a median of 12.7 m and a 99th percentile of 174.6 m in the
2026-08-27 snapshot. A fixed vertex count would be too wide on the dense
stretches and too narrow to contain a plausible match on the sparse ones.

The window exists for cost. The 2026-08-27 snapshot carries 785,972 shape
points across 1,868 shapes, a median of 316 points per shape, and a position
with no previous match to search around has to be tested against its own
shape end to end. Measured over 200,000 positions from 2026-08-31 projected
against the 2026-08-31 snapshot, the full scan cost 180 µs per position
against 28 µs for the windowed search on the same records, a factor of 6.5.
The full scan still runs where a window is unavailable or meaningless: at
the first position of a group, on a pair whose timestamps do not advance,
and on a pair separated by more than the maximum gap.

**Sample.** Speed is the change in along-shape distance divided by the
change in timestamp. The sample is attributed to the 200 m bin the later of
the two positions falls in (`distance // 200`), and to the 15-minute
local-time slot of the later timestamp. Every accepted sample is written to
`segment_speeds_<date>.jsonl` with both timestamps, the distance and the
elapsed time behind it, so you can recompute the number instead of taking
mine.

**Rejections.** Six named counters, reported per day and in total in
`typical_weekday.json`. No record is discarded without one of them moving:

| Counter | Meaning |
|---|---|
| `trip_not_in_static` | the record's `trip_id` has no row in the matched snapshot's `trips.txt` |
| `shape_not_found` | the trip resolves, but its shape has fewer than two usable points |
| `non_positive_time_delta` | duplicate or out-of-order timestamps inside one group |
| `gap_too_large` | more than 600 s between the two positions |
| `moved_backward` | along-shape distance fell by more than 50 m |
| `speed_too_high` | derived speed above 100 km/h |

The first two count records, the other four count consecutive pairs. The
600 s gap cutoff is several times the 45 s poll interval, far enough out
that a straight line drawn across it would more likely hide a layover or a
missed run of polls than describe a slow stretch. The 50 m backward
tolerance absorbs GPS wobble and a position snapping onto the wrong branch
near a shape self-crossing, while a larger reversal is treated as a failed
match. 100 km/h is a wide margin over anything this network runs.

I keep the two denominators apart: totalling the six counters against one of
them would misstate every rate in the table. The run on record covers seven
days and 4,273,952 records, of which 2,578 (0.060%) were rejected as
`trip_not_in_static` and none as `shape_not_found`. It yielded 4,189,084
consecutive pairs, of which 4,179,545 became samples and 9,539 (0.228%) were
rejected: `moved_backward` 8,014, `speed_too_high` 989, `gap_too_large` 536,
`non_positive_time_delta` none.

The distribution matters more than the total. All 2,578 unmatched trips fall
on 2026-08-29, 2026-08-30 and 2026-08-31, and every one of them belongs to a
single bus route, `A75` (short name 10): 1,266 and 1,248 records on the two
weekend days, plus 64 on the 31st. The other four days contribute none. A
stale snapshot surfaces as one day's counter climbing, which is why the
per-day breakdown sits next to the total instead of being averaged into it.

Before 2026-09-03 that counter read 7,348, and the extra 4,770 belonged to a
second route, `A109` (short name 30): 2,367 records on 2026-08-29 and 2,403
on 2026-08-30, over 48 distinct `trip_id`s, 100% of that route's records on
both days. They spread across the service day at about 200 records an hour
from 05:00 to 23:00 local, so the pattern looked nothing like a peak-hour or
a school-hour addition. Those identifiers all appear in the 2026-08-31
snapshot, and the pipeline now resolves them against it (see Known
limitations). `A75`'s do not appear in any snapshot this archive holds, so
they stay unmatched.

The mechanism is the agency's `trip_id` format. A `trip_id` reads
`<route_id>-<shape_id>-<direction>-<sequence>-<service_id>`, so renumbering
a `service_id` renames every trip that uses it, and the agency renumbers
service identifiers between its nightly rebuilds when it re-plans a route.
Both routes were being re-planned that week: their `route_long_name` differs
across all three snapshots held. For `A109` the change is visible end to end.
Its weekend service is `3059595690` in the 2026-08-27 snapshot and
`30231557250` from the 2026-08-31 snapshot onward, both covering 20260829 and
20260830, and all 48 `trip_id`s the real-time feed broadcast on those two
days appear verbatim in the 2026-08-31 and 2026-09-01 snapshots. For `A75`
I cannot trace it the same way: the identifier it broadcast, `21012561760`,
appears in no snapshot this archive holds. In the 2026-08-27 snapshot that
route's weekend service is `22609987460` covering both days; by the
2026-08-31 snapshot the same identifier begins on 20260905 instead. The
build that carried `21012561760` was one of 2026-08-28, 2026-08-29 or
2026-08-30, none of which was captured (see Known limitations), so which one
introduced it cannot be recovered.

Weekday service identifiers for the same two routes, `34695955721` and
`41453276541`, are identical in all three snapshots, which is why 2026-08-27
and 2026-08-28 reject nothing while the two weekend days reject 0.271% and
0.269%. So the counter measures the age of my snapshots. The feed was
internally consistent with the static build in force on each of those days,
and this archive is missing that build. Route 30 is recovered from the
2026-08-31 snapshot, which holds its identifiers verbatim; read route 10 as
absent from the processed output for 2026-08-29 and 2026-08-30, with no
claim about the speed it ran at. Both days are weekends and fall outside the
Monday to Friday median in any case.

**Export.** `scripts/export_web.py` writes segment geometry once and
references it by index from one file per 15-minute slot, so a timeline
scrubs without downloading the whole corpus. Bins below the `--min-samples`
threshold (default 2, the smallest count at which a median aggregates
anything at all) are dropped, per schedule period: on the current archive
that retains 287,024 of 572,109 bins in the two-day period and 609,461 of
799,934 in the four-day one. `n_samples` ships with every surviving bin. Speed ships as an
integer km/h for map colouring, with the float m/s and every sample behind
it left in `segment_speeds_<date>.jsonl` and `typical_weekday.json`. The
drawn geometry of a segment is the shape's own polyline inside the 200 m
bin, simplified with the Douglas-Peucker algorithm at a 5 m tolerance
applied in the local metric plane, not in degrees. Every `shapes.txt`
vertex the simplification drops lies within 5 m of the line drawn in its
place, so a turn or a roundabout inside a segment is drawn rather than cut
across, but not to the last metre. Earlier versions of this export drew the
straight chord between the bin's two endpoints instead, which put the line
a median of 3.4 m from the true path, 90.6 m away at worst, and more than
5 m away on 44.0% of the 26,111 segments in the current schedule period.
Keeping every vertex is what the page-load budget rules out: at 10.59
points per bin `geometry.json` alone gzips to 1,135 KB, past the 1 MB
budgeted for a first load, against 327 KB for the simplified file and
256 KB for the chord it replaced. `web/index.json` lists the day bundles
and names the current schedule period, since a static site cannot list a
directory.

## Aggregation

- A "typical weekday" is the median value per network segment and time
  slot, computed across Monday–Friday observations. I take the median
  because a mean follows every feed dropout and one-off anomaly.
- A segment is a fixed 200 m bin of distance travelled along a trip's GTFS
  Static shape. Two shapes running down the same street are two different
  segments. Each raw vehicle position is projected onto its trip's shape
  polyline, and segment identity is that shape plus `distance along it //
  200 m`. A time slot is a 15-minute local-time bin. `direction_id` is
  empty for every trip in this feed, so `shape_id`, distinct per direction
  and route variant, is what separates two directions of the same route.
- For aggregation I address a shape by its content alone: the key is the
  first 16 hex characters of a SHA256 over its ordered points, with no
  `shape_id` in it. The publisher's identifiers move in both directions and
  neither direction is safe to key on. The agency republished GTFS Static on
  2026-08-31 with 32 `shape_id`s keeping their identifier while the geometry
  underneath them changed, and again on 2026-09-02 keeping the geometry and
  changing the identifier, shipping `A4500`–`A4503` as byte-identical copies
  of `A1192`, `A3949`, `A1221` and `A2710` while leaving the originals in
  the feed (see Known limitations). Keying on the bare `shape_id` would pool
  speed samples from two different stretches of road into one median; keying
  on `shape_id` plus the hash, which is what this pipeline did until
  2026-09-03, splits one unchanged stretch of road into two series that
  never merge again the moment the agency renumbers it. A bare geometry hash
  survives both: shapes whose points are unchanged between feed versions
  produce the same key and keep pooling samples across them (1,836 of the
  1,868 shapes in the 2026-08-27 snapshot, 98.3%, hash identically to their
  counterpart in the 2026-08-31 one), and a renumbered shape keeps the key
  it already had. Both movements are counted in `typical_weekday.json`:
  `multi_geometry_shape_id_count` is how many `shape_id`s appeared under
  more than one geometry across the feeds loaded in a run, and
  `multi_id_geometry_count` how many geometries appeared under more than one
  `shape_id`. On the run of record, 52 and 19.
- The median is split by schedule period, so days running different
  timetables never pool into one number. On 2026-09-04 the agency published
  its autumn timetable: bus routes 191 and 192 appear with 100 trips a day
  each, route 10's 58 daily trips move onto a new route 190 running the same
  four shapes, and tram 8 goes from 261 trips a day to 213. The
  weekend pattern takes over on 5 September and runs through Monday
  7 September, a public holiday; the new weekday pattern starts on
  8 September. One Monday-to-Friday median would average the old service and
  the new one together and label the result typical.
- Each archived day is signed by what its own snapshot schedules for it: the
  sorted list of (route id, trip count) pairs over the services GTFS Static
  says run on that date, hashed to the first 16 hex characters of a SHA256.
  The signature is content-addressed for the same reason the shape key is.
  The agency renumbers trip ids and service ids between nightly rebuilds, and
  a key built from those identifiers would announce a new timetable every
  time it did, while route ids and per-route trip counts sit still through a
  renumbering. Reading each day from the snapshot in force on that day also
  means a later snapshot cannot rewrite which timetable a past day ran, and
  that matters: this feed quietly drops calendar rows for dates once they
  have passed (1,281 services on 2026-08-27 as the 08-27 snapshot has it,
  1,271 as the 09-04 one does).
- Signatures are not compared for equality. Two consecutive weekdays share a
  period while the trips separating them stay under 0.5% of the first day's
  total, and a larger jump starts a new period. The difference is counted per
  route and summed as absolute values, so one route appearing and another
  disappearing cannot cancel out. Days are compared against the day that
  opened the period rather than against yesterday, so a slow drift cannot
  walk a period away from the timetable its key names, one tolerated step at
  a time. Read each of the 267 weekdays from 27 August 2026 to 4 September
  2027 out of the snapshot in force on it, which is what the pipeline does,
  and what the feed shows is a gap rather than a fitted line: consecutive
  weekdays differ by 0% inside a period, by 3.15% on 8 September, 3.88% on
  14 September and 4.07% at the end of August, and by nothing in between. The
  data therefore bounds this threshold from above and is silent on where
  inside it belongs; 0.5% is a sixth of the smallest boundary the agency has
  published. On this archive it changes no grouping — comparing signatures
  for equality would produce the same periods — so it is insurance against a
  feed that shifts a handful of trips without changing its timetable, and I
  would rather name it that than present it as a value the data chose.
- Which snapshot answers for a past date decides two of those four numbers.
  Reading the same 267 weekdays out of the 2026-09-04 snapshot alone adds two
  boundaries, 9.42% at the end of August and 0.60% between 2 and 3 September,
  and both are artefacts of the archive rather than timetable changes: the
  agency erodes calendar rows for dates already past, so that snapshot no
  longer carries 1,066 of the trips 27 August ran or 89 of 2 September's, all
  89 of them on route A53, while both days' own snapshots still carry them. A
  pipeline reading history out of the newest feed would see a 0.60% step
  between two days that ran the same timetable, and a 0.5% threshold would
  split them. This one reads each day against the feed it started under.
- A weekday scheduling less than 80% of the median weekday's trips leaves the
  median rather than opening a period of its own. Sofia runs a weekend
  timetable on public holidays: Monday 7 September 2026 schedules 10,149
  trips where the Thursday before schedules 14,907, a 35% difference against
  the 3 to 4% of a real timetable change. Eleven weekdays in the year the
  2026-09-04 snapshot publishes forward look like this, and their dates line
  up with Bulgarian public holidays. A holiday is not a typical weekday, and
  a single day is not enough to take a median over.
- Aggregation groups on (schedule period, segment, time slot), and the web
  export writes one bundle per period under
  `web/typical_weekday/<period_key>/`, with an index at
  `web/typical_weekday/manifest.json` naming every period and which one is
  current. Days the median leaves out keep both their signature and the
  reason they were left out in `typical_weekday.json`, so a reader can still
  see which schedule was in force on a date the median skips.
- The archive of record holds two weekday periods, not one. 27 and 28 August
  ran 133 routes and 14,608 trips; from 31 August it is 135 routes and 14,907
  trips. Reading both dates out of the single 2026-08-27 snapshot reproduces
  the split, so it is a timetable change rather than an artifact of comparing
  two feed versions: read that way, route A145's service ends on 30 August
  (148 trips) while TM58 and TM24 each gain one, 151 trips of churn against
  14,608, 1.03%. The pipeline's own comparison is larger, because it reads
  each day against the snapshot in force on it and the 08-27 feed does not
  yet know the three tram routes that start on 31 August: 595 trips, 4.07%
  (TM40 +173, TM34 +167, TM33 +104, A145 -148, TM24 +2, TM58 +1). Either way
  the boundary is real and clears the threshold. The two medians are
  published side by side; neither is folded into the other.
- Every median ships with the number of samples it was computed from
  (`n_samples`), in `typical_weekday.json` and in the web export alike. A
  median of two observations and a median of two hundred are otherwise
  indistinguishable once rendered as one coloured line.
- I publish the raw daily observations next to the median, so the real
  day-to-day variability stays visible.

## Integrity and provenance

Each day's archive file ships with a manifest (`scripts/generate_manifest.py`)
carrying SHA256 checksums of the data and heartbeat files, a breakdown of
successful, empty and failed polls, and a gap analysis. With it you can check
that a published file matches what the collector wrote, and see where
coverage is incomplete and why, instead of taking completeness on trust.

## Known limitations

I would rather name these myself than leave you to find them:

- The "typical weekday" rests on six weekdays split across two schedule
  periods, one of them a partial day. The aggregation on record covers
  2026-08-27 and 2026-08-28 in the first period, 2026-08-31 through
  2026-09-03 in the second; the first of all of them covers 52.45% of its
  calendar day, because collection began at 11:07 local. That day is flagged
  as incomplete in the web export's own `manifest.json`, so a reader meets the
  caveat there too. Days still being collected stay out of the aggregate:
  their local copy reaches only as far as the last pull, so folding one in
  would give a median that changes under a reader who re-runs the pipeline an
  hour later. The two-day period spans 572,109 (segment, timeslot) bins over
  24,365 segments, and 49.83% of those bins rest on a single observation; the
  four-day period spans 799,934 bins over 28,559 segments, 23.81% of them on
  a single observation and 55.61% on three or more. Splitting by period buys
  medians that mean something at the cost of thinner ones, and the two-day
  period is the visible price. This archive is a week old. Read the numbers
  above as the output of a working pipeline. They do not yet describe how
  Sofia's network behaves.
- The 0.5% and 80% thresholds rest on one feed and one year of its published
  calendar, and they do not rest on it equally. The 80% one is pinned: against
  a median of 15,595 trips the heaviest reduced-service weekday reaches
  65.90% and the lightest ordinary weekday 93.67%, so any threshold between
  those two selects the same eleven days. Both edges are the pipeline's own
  reading, each day against the snapshot in force on it; the upper one falls
  to 86.84% if 27 August is read out of the 2026-09-04 snapshot instead, for
  the erosion reason above. 80% sits inside the band either way. The 0.5% one
  is not pinned. Every boundary this
  agency has published sits at 3.15% or above and every day inside a period
  at 0, so the evidence rules out a threshold above 3.15% and says nothing
  about the choice within it — on this archive the split would be identical
  with no threshold at all. It is there for a feed that moves a few trips
  without changing its timetable, which this one has not yet done. An agency
  publishing timetable changes in small continuous steps would need a
  different rule, not a different number. Both thresholds are recorded in
  `typical_weekday.json` next to the results they produced.
- The schedule period key cannot see a pure retiming. Two timetables that
  move departure times around while leaving every route's trip count
  unchanged hash to the same key, so their days would pool into one median
  the way every day did before the split existed. Catching that needs
  `stop_times.txt`, 45.8 MB unpacked against `trips.txt`'s 3.2 MB, re-read for
  every archived day on every scheduled fetch, an order of magnitude more
  work than the `trips.txt` read the current key costs. I have not observed
  a retiming-only change in this feed. If one turns up, the key gets
  extended; until then this is the boundary of what the split detects.
- The GTFS-RT feed carries no metro, so findings from this archive describe
  surface transport.
- Interpolated speed carries uncertainty proportional to the polling
  interval. A short, sharp speed change between two snapshots is lost.
- The feed has gaps from time to time (dropped connections, empty
  responses). I log them and leave them as gaps, with nothing filled in or
  estimated over.
- The bounding-box filter can lose data at its edges by construction, even
  with the box derived from the network's own extent. Each day's manifest
  publishes the observed drop-out-of-bbox rate, so the loss is a number you
  can read. So far that number is zero: of the 5,098,818 vehicle positions
  the feed reported between the corrected box coming into force
  (2026-08-28 17:04 local) and 2026-09-05, not one fell outside it. The
  filter is insurance against a documented failure mode of GTFS-RT feeds,
  and on this feed it has not yet had anything to catch.
- That risk was realised at the start of the archive. The box in force
  until 2026-08-28 17:04 local time was hand-picked and narrower than the
  network on all four sides: checked against the 2026-08-27 static
  snapshot, it excluded 281 of the feed's 4,468 stops (6.29%) and 83,664 of
  its 785,972 shape points (10.64%), so vehicles serving the outlying
  settlements were discarded as the teleport artifact the filter exists to
  catch. The filter drops a record before it reaches disk, so nothing
  survives to reconstruct: 2026-08-27, and 2026-08-28 up to that hour, are
  incomplete at the edges of the network and cannot be repaired. The box in
  use since (lat 42.45 to 42.90, lon 23.03 to 23.66) contains every stop and
  shape point in that snapshot, whose own extent is lat 42.4788 to 42.8546,
  lon 23.0778 to 23.6075.
  The bias does not stop at those two raw days: the median itself carries
  it forward. Of the 26,046 segments in the current typical-weekday web
  export, 1,803 (6.92%) have at least one endpoint outside the old box.
  Those segments could only be sampled on the days collected under the
  wider one: 2026-08-31, 2026-09-01, and the part of 2026-08-28 after
  17:04 local. The remaining 24,243 segments could draw on all four
  weekdays behind the median. Their bins carry fewer samples as a result,
  a median n_samples of 2 against 3 for segments inside the old box (mean
  2.46 against 4.11), across 15,167 of the export's 575,124 bins (2.64%).
  The gap is real but short of the clean half a four-versus-two split
  would suggest, consistent with the bbox transition falling mid-day on
  2026-08-28 rather than on a day boundary. The per-bin `n_samples` field
  has always let a reader see this. I had not measured the size of it
  before now.
- Coverage is measured against calendar-day boundaries in local time,
  using the collector's configured poll interval as the denominator rather
  than the interval observed in the data. An earlier version measured from
  the first record to the last one and derived its own baseline interval,
  which inflated coverage on days where collection started late and could
  not have detected a collector polling at half its configured rate.
- The feed reports vehicle speed in km/h, in a field that GTFS-RT specifies
  as metres per second. The values are whole numbers with a median of 17
  and a maximum of 87, which as m/s would put a city bus at a 61 km/h
  median and a 313 km/h maximum. Published figures treat the field as
  km/h. That reading comes from the data; the feed publisher has stated
  nothing about it.
- Speed derived from consecutive positions and the feed's own speed reading
  disagree by a median of 9.3 km/h, with 15% of samples differing by more
  than 20 km/h. The two measure different things, an average over the
  polling interval against an instantaneous reading, so the archive
  publishes both and reconciles neither.
- The feed populates no bearing field at all, so direction of travel comes
  from the trip's shape_id in GTFS Static. direction_id, the usual field
  for this, is empty for every trip in the feed.
- The agency rebuilds GTFS Static every day. The member timestamps inside
  the zip put the build at 03:33 local time, identical to the second in the
  snapshots dated 2026-08-27, 2026-08-31 and 2026-09-01, and each
  snapshot's `feed_start_date` equals its own build date. The contents
  differ from one day to the next well past the dates: between 2026-08-31
  and 2026-09-01, `trips.txt`, `stop_times.txt`, `shapes.txt`, `routes.txt`,
  `stops.txt` and `calendar_dates.txt` all changed, while `agency.txt`,
  `transfers.txt`, `translations.txt`, `pathways.txt`, `levels.txt` and
  `fare_attributes.txt` were byte-identical. `feed_version` reads `1.0` in
  every snapshot collected so far, which makes it useless for telling one
  version from another. Each processed day is matched to the latest static
  snapshot dated on or before it, and where that date is the day's own and
  holds several captures, to the first of them — the feed the day started
  under, with the later captures reachable as the fallback described below.
  That date reflects when this archive observed the agency serving the feed,
  and the publisher's own `feed_start_date` is recorded alongside it so the
  two can be checked against each other. No single snapshot fits the whole archive: processing
  2026-08-31 against the snapshot dated 2026-08-27 rejects 26,289 records
  (7.013%) as unmatched to any known trip; against the snapshot dated
  2026-08-31 itself, 64 records (0.017%). Both figures come from the same
  copy of that day's file, taken while collection for the day was still
  running, so the percentages are of a partial day. Against that same
  2026-08-31 snapshot, 2026-08-27 and 2026-08-28, both complete days,
  together lose 2.342%.
  Of the 32 `shape_id`s the 2026-08-31 republish changed underneath (see
  Aggregation), 20 were observed under two distinct geometries in the median
  built from 2026-08-27, 2026-08-28 and 2026-08-31. The edge case is already
  here in the days collected so far. Per-day reject counts are kept in the
  output next to the archive-wide total, so a stale snapshot shows up as one
  day's count climbing instead of being averaged away against every other
  day.
- Snapshots are captured on a schedule from 2026-09-01
  (`scripts/archive_static_feed.py`, hourly since 2026-09-03 and daily at
  05:00 UTC before that), saving a dated copy only when the contents
  changed, so the runs between midnight and the agency's 03:33 build write
  nothing. Before that I
  took snapshots by hand, and the archive holds none for 2026-08-28,
  2026-08-29 or 2026-08-30. The agency serves the current build and keeps no
  history, so those three cannot be recovered: each of those days is
  processed against the snapshot dated 2026-08-27, and any geometry or
  schedule change the agency made inside that window is attributed to the
  older shape. Of the 32 `shape_id`s whose geometry differs between the
  2026-08-27 and 2026-08-31 snapshots, an unknown share changed during those
  three unobserved days rather than on the 31st. The gap costs more than
  geometry: the records rejected on 2026-08-29 and 2026-08-30 carry trip
  identifiers minted by a build in that window. Where the next snapshot on
  record turned out to hold them after all, they now resolve against it
  (see the next bullet), which recovers route 30; route 10's identifiers
  appear in no snapshot this archive holds and it stays absent from the
  processed output for both days (see Preprocessing for the full trace).
- A feed republished in the middle of a day is not in that day's own
  snapshot. On 2026-09-02 the agency renumbered 157 trips of four routes at
  around 11:00 local, hours after that day's capture, and the real-time
  stream switched to the new identifiers at once: 10,166 records, 1.4% of
  the day, matched no trip in the feed the day is scored against. For trip
  identifiers the day's own snapshot does not know, the pipeline now falls
  back to the next snapshot chronologically, and only for those; wherever
  the day's own feed has an answer, that answer stands. The day breakdown
  in `typical_weekday.json` records which snapshot was used as the fallback
  and how many trips and records came from it, so a day resolved partly
  against a later feed says so. This does not make the fallback feed the
  right one for that day — it is the nearest evidence of a build this
  archive never captured, and a renumbering it does not carry stays
  unresolved.

## Versioning

Archive releases run on a fixed cadence, decoupled from findings: a first
release, a quarterly one, then monthly. Each is versioned and citable on its
own, apart from whatever analysis cites it.
