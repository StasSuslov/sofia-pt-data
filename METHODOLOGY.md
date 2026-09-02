# Methodology

This document describes the data collection and analysis method, published
ahead of any specific findings. The intent is to fix the method first, so
later results can be checked against a method that didn't change to fit
them.

This method is archived on Zenodo alongside the code that implements it:
[10.5281/zenodo.22256653](https://doi.org/10.5281/zenodo.22256653). Changes to the
method arrive as new versions of that record, so a result can always be read
against the method as it stood when the result was produced.

## Scope

An independent archive of Sofia's public transport GTFS-RT and GTFS Static
feeds, plus a reproducible pipeline for turning that archive into a small
set of recurring analyses (congestion, schedule reliability, network
coverage). Multi-city by design: every city-specific value (feed URLs,
bounding box, timezone, district boundaries) lives in configuration, not in
code, so the pipeline can run against a second city without a rewrite.

## Data sources

- **GTFS-RT vehicle positions** — polled every 30–60 seconds from Sofia's
  public transport open data portal, [urbandata.sofia.bg](https://urbandata.sofia.bg)
  (Level 1, CC BY 4.0). Snapshot schema: `snapshot_ts, vehicle_id, route_id,
  trip_id, lat, lon, bearing, speed_ms, vehicle_ts`.
- **GTFS Static** — routes, stops, schedules, and shapes, from the same
  portal. Used to derive the collection bounding box and to unlock analyses
  that don't require real-time data (route/stop coverage, accessibility).
- **OpenStreetMap**, via the Overpass API — district boundaries and road
  network, for accessibility analysis.

No data is entered manually, and no schedule or network fact is assumed
without confirmation from a feed.

## Collection method

- Polling interval: 30–60 seconds, continuous, with per-poll heartbeat
  logging independent of whether the poll returned any vehicle records —
  a fetch error, an empty response, and a successful-but-quiet feed are
  each recorded distinctly.
- **Bounding-box filter.** GTFS-RT feeds are known to occasionally emit
  coordinate "teleports" — a vehicle position far outside the plausible
  network. Records outside a bounding box are dropped at collection time.
  The box is derived directly from the static feed's `stops.txt` and
  `shapes.txt` (see `scripts/derive_bbox.py`), not hand-picked, and is
  sized to include the full extent of the network's routes and stops. A
  bbox that clips real outlying routes would silently misrepresent them
  as data gaps, so the derivation script is intended to be re-run whenever
  the static feed is refreshed, and the box re-checked against it.
- Vehicle speed between consecutive snapshots is interpolated from
  position and elapsed time, not measured directly by the feed.

## Preprocessing

Two scripts stand between the raw archive and anything a reader sees.
`scripts/segment_speeds.py` turns vehicle positions into speed samples
attached to a piece of network; `scripts/export_web.py` turns the
aggregate into the files a map fetches. Every threshold named below is
written into the output itself (`thresholds` in `typical_weekday.json`,
`preprocessing_thresholds` in the web export's `manifest.json`), so the
figures a reader checks come from the run rather than from this document.

**Pairing.** Records are grouped by `(vehicle_id, trip_id)` and sorted by
`snapshot_ts`. A speed sample always comes from two consecutive positions
within one such group, never from two vehicles and never across two
separate runs of the same vehicle over the same road.

**Projection.** `trips.txt` maps `trip_id` to a `shape_id`, and `shapes.txt`
gives that shape as an ordered polyline carrying the cumulative haversine
distance at every vertex. A raw position is projected onto the nearest
polyline segment and recorded as a distance along the shape. That
nearest-segment search runs in a local equirectangular projection, because
projecting a point onto a line segment needs planar geometry; every distance
that reaches the output is the cumulative haversine value, so the planar
approximation only decides which segment is closest, never how far a vehicle
travelled.

**Sliding window.** The search is confined to the vertices around the
previous match: from 350 m behind it (a 50 m backward tolerance plus a 300 m
margin) to `100 km/h × dt` ahead of it plus the same margin, located by
binary search over the cumulative distances. The window is anchored on
distance rather than on a count of vertices, because vertex spacing in this
feed is uneven: a median of 12.7 m and a 99th percentile of 174.6 m in the
2026-08-27 snapshot. A fixed vertex count would be wastefully wide on the
dense stretches and too narrow to contain a plausible match on the sparse
ones.

The window exists for cost. The 2026-08-27 snapshot carries 785,972 shape
points across 1,868 shapes, a median of 316 points per shape, and a position
with no previous match to search around has to be tested against its own
shape end to end. Measured over 200,000 positions from 2026-08-31 projected
against the 2026-08-31 snapshot, the full scan cost 180 µs per position
against 28 µs for the windowed search on the same records, a factor of 6.5.
The full scan is still used in the three cases where a window is
unavailable or meaningless: the first position of a group, a pair whose
timestamps do not advance, and a pair separated by more than the maximum
gap.

**Sample.** Speed is the change in along-shape distance divided by the
change in timestamp. The sample is attributed to the 200 m bin the later of
the two positions falls in (`distance // 200`), and to the 15-minute
local-time slot of the later timestamp. Every accepted sample is written to
`segment_speeds_<date>.jsonl` with both timestamps, the distance and the
elapsed time behind it, so a reader can recompute the number rather than
accept it.

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
that a straight line drawn across it would more likely hide a layover, a
missed poll run or a detour than describe a slow stretch. The 50 m backward
tolerance absorbs GPS wobble and a position snapping onto the wrong branch
near a shape self-crossing, while a larger reversal is treated as a failed
match. 100 km/h is a wide margin over anything this network runs.

The two denominators are kept apart, since totalling the six counters
against one of them would misstate every rate in the table. The run on
record covers five days and 2,804,654 records, of which 7,348 (0.262%) were
rejected as `trip_not_in_static` and none as `shape_not_found`. It yielded
2,742,870 consecutive pairs, of which 2,736,587 became samples and 6,283
(0.229%) were rejected: `moved_backward` 5,209, `speed_too_high` 608,
`gap_too_large` 466, `non_positive_time_delta` none.

The distribution matters more than the total. Of the 7,348 unmatched trips,
7,284 fall on 2026-08-29 and 2026-08-30, the two days processed against a
static snapshot two and three days old by then. 2026-08-27 and 2026-08-28,
processed against the 2026-08-27 snapshot, contribute none; 2026-08-31,
processed against a snapshot of its own date, contributes 64. A stale
snapshot surfaces as one day's counter climbing, which is why the per-day
breakdown sits next to the total instead of being averaged into it.

Those 7,284 records are not spread across the network. Every one of them
belongs to one of two bus routes, `A109` (short name 30) and `A75` (short
name 10), and on both days they account for 100% of those two routes'
records: 2,367 and 1,266 on 2026-08-29, 2,403 and 1,248 on 2026-08-30, over
77 and 78 distinct `trip_id`s. They spread evenly across the service day,
roughly 200 records an hour from 05:00 to 23:00 local, so nothing about the
pattern points at a peak-hour or school-hour addition. No other route
contributes a single unmatched record on either day.

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
it cannot be traced the same way: the identifier it broadcast, `21012561760`,
appears in no snapshot this archive holds. In the 2026-08-27 snapshot that
route's weekend service is `22609987460` covering both days; by the
2026-08-31 snapshot the same identifier begins on 20260905 instead. The
build that carried `21012561760` was one of 2026-08-28, 2026-08-29 or
2026-08-30, none of which was captured (see Known limitations), so which one
introduced it cannot be recovered.

Weekday service identifiers for the same two routes, `34695955721` and
`41453276541`, are identical in all three snapshots, which is why 2026-08-27
and 2026-08-28 reject nothing while the two weekend days reject 0.778% and
0.788%. The counter is therefore measuring snapshot age, not a defect in the
real-time feed and not anything specific to weekends: the feed was internally
consistent with the static build in force on each of those days, and this
archive is missing that build. A reader should treat routes 30 and 10 as
absent from the processed output for 2026-08-29 and 2026-08-30 rather than as
having run at an unusual speed. Both days are weekends and so fall outside
the Monday to Friday median in any case.

**Export.** `scripts/export_web.py` writes segment geometry once and
references it by index from one file per 15-minute slot, so a timeline
scrubs without downloading the whole corpus. Bins below the `--min-samples`
threshold (default 2, the smallest count at which a median is an aggregate
rather than one relabelled raw reading) are dropped: on the current archive
that retains 458,260 of 718,042 bins. `n_samples` ships with every surviving
bin. Speed ships as an integer km/h for map colouring, with the float m/s
and every sample behind it left in `segment_speeds_<date>.jsonl` and
`typical_weekday.json`. A segment's drawn geometry is the straight chord
between its two 200 m endpoints, not the true curve inside the bin.

## Aggregation

- A "typical weekday" is defined as the median value per network segment
  and time slot, computed across Monday–Friday observations. Median rather
  than mean, for robustness to feed dropouts and one-off anomalies.
- A segment is a fixed 200 m bin of distance travelled along a trip's GTFS
  Static shape, not a stretch of physical road: each raw vehicle position
  is projected onto its trip's shape polyline, and segment identity is
  that shape plus `distance along it // 200 m`. A time slot is a
  15-minute local-time bin. `direction_id` is empty for every trip in this
  feed, so `shape_id`, distinct per direction and route variant, is what
  separates two directions of the same route; there is no `direction_id`
  to fall back on.
- Shape identity used for aggregation is content-addressed, not the bare
  GTFS `shape_id`: a shape's key is its `shape_id` plus the first 8 hex
  characters of a SHA256 hash over its ordered points. The agency
  republished GTFS Static on 2026-08-31, and 32 `shape_id`s kept their
  identifier while the geometry underneath them changed (see Known
  limitations). Keying purely on `shape_id` would let two feed versions of
  the same segment index silently pool speed samples from two different
  stretches of road into one median. With the geometry hash folded in,
  shapes whose points are unchanged between feed versions produce the same
  key and keep pooling samples across them: 1,836 of the 1,868 shapes in
  the 2026-08-27 snapshot, 98.3%, hash identically to their counterpart in
  the 2026-08-31 one. The 32 that changed produce different keys instead,
  starting a new series that never merges with the old one. The pattern
  recurs rather than being confined to that one republish: between the
  2026-08-31 and 2026-09-01 snapshots, another 9 `shape_id`s kept their
  identifier and changed geometry underneath it.
- Every median ships with the number of samples it was computed from
  (`n_samples`), in `typical_weekday.json` and in the web export alike. A
  median of two observations and a median of two hundred are otherwise
  indistinguishable once rendered as one coloured line.
- Raw daily observations are published alongside the median, not replaced
  by it, to preserve real day-to-day variability.

## Integrity and provenance

Each day's archive file ships with a manifest (`scripts/generate_manifest.py`)
containing SHA256 checksums of the data and heartbeat files, a breakdown of
successful/empty/error polls, and a gap analysis. This lets a reader verify
that a published file matches what was collected, and see where and why
coverage is incomplete, rather than take completeness on trust.

## Known limitations

Stated here rather than left for a reader to discover independently:

- The "typical weekday" currently rests on four weekdays, one of them a
  partial day. The aggregation on record covers 2026-08-27, 2026-08-28,
  2026-08-31 and 2026-09-01; the first covers 52.45% of its calendar day,
  because collection began at 11:07 local. That day is flagged as
  incomplete in the web export's own `manifest.json`, rather than only
  here. Days still being collected are left out of the aggregate entirely:
  their local copy reaches only as far as the last pull, so folding one in
  would give a median that changes under a reader who re-runs the pipeline
  an hour later. The median spans 787,433 (segment, timeslot) bins over
  28,285 distinct segments and 96 time slots, and 212,309 of those bins,
  26.96%, rest on a single observation; 49.52% have three or more. This is
  an archive early in its life, and the numbers above should be read as a
  working pipeline's output rather than as a description of how Sofia's
  network behaves.
- The GTFS-RT feed does not include Sofia's metro — findings from this
  archive describe surface transport only.
- Interpolated speed carries uncertainty proportional to the polling
  interval; short, sharp speed changes between two snapshots are not
  resolved.
- The feed has occasional gaps (dropped connections, empty responses).
  These are logged and reported, not filled in or estimated over.
- The bounding-box filter is a source of possible data loss at its edges
  by construction, even though it is derived from the network's actual
  extent rather than chosen arbitrarily. Any observed drop-out-of-bbox
  rate is published in each day's manifest rather than assumed to be zero.
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
  already lets a reader see this; the scale of it was not previously
  named.
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
  km/h. This is an inference from the data, not a statement from the feed
  publisher.
- Speed derived from consecutive positions and the feed's own speed reading
  disagree by a median of 9.3 km/h, with 15% of samples differing by more
  than 20 km/h. The two measure different things (an average over the
  polling interval against an instantaneous reading), so the archive
  publishes both rather than reconciling them.
- The feed populates no bearing field at all, so direction of travel comes
  from the trip's shape_id in GTFS Static. direction_id, the usual field
  for this, is empty for every trip in the feed.
- GTFS Static is rebuilt by the agency every day, not occasionally. The
  member timestamps inside the zip put the build at 03:33 local time,
  identical to the second in the snapshots dated 2026-08-27, 2026-08-31 and
  2026-09-01, and each snapshot's `feed_start_date` equals its own build
  date. The contents differ substantively from one day to the next, not
  only in the dates: between 2026-08-31 and 2026-09-01, `trips.txt`,
  `stop_times.txt`, `shapes.txt`, `routes.txt`, `stops.txt` and
  `calendar_dates.txt` all changed, while `agency.txt`, `transfers.txt`,
  `translations.txt`, `pathways.txt`, `levels.txt` and `fare_attributes.txt`
  were byte-identical. `feed_version` reads `1.0` in every snapshot
  collected so far, so it cannot be used to tell one version from another. Each processed day is
  matched to the latest static snapshot dated on or before it; that date
  reflects when this archive observed the agency serving the feed, not the
  publisher's own `feed_start_date`, which is recorded alongside it so the
  two can be checked against each other. No single snapshot fits the whole
  archive: processing 2026-08-31 against the snapshot dated 2026-08-27
  rejects 26,289 records (7.013%) as unmatched to any known trip; against
  the snapshot dated 2026-08-31 itself, 64 records (0.017%). Both figures
  come from the same copy of that day's file, taken while collection for
  the day was still running, so the percentages are of a partial day.
  Against that same 2026-08-31 snapshot, 2026-08-27 and 2026-08-28, both
  complete days, together lose 2.342%.
  Of the 32 `shape_id`s the 2026-08-31 republish changed underneath (see
  Aggregation), 20 were actually observed under two distinct geometries in
  the median built from 2026-08-27, 2026-08-28 and 2026-08-31. This is not a
  hypothetical edge case in the days collected so far. Per-day reject
  counts are kept in the output rather than only an archive-wide total, so
  a stale snapshot shows up as one day's count climbing instead of being
  averaged away against every other day.
- Snapshots are captured on a schedule from 2026-09-01
  (`scripts/archive_static_feed.py`, daily at 05:00 UTC, after the agency's
  03:33 build), saving a dated copy only when the contents changed. Before
  that, snapshots were taken by hand, and the archive holds none for
  2026-08-28, 2026-08-29 or 2026-08-30. The agency serves only the current
  build and no history, so those three cannot be recovered: each of those
  days is processed against the snapshot dated 2026-08-27, and any geometry
  or schedule change the agency made inside that window is attributed to the
  older shape. Of the 32 `shape_id`s whose geometry differs between the
  2026-08-27 and 2026-08-31 snapshots, an unknown share changed during those
  three unobserved days rather than on the 31st. The gap costs more than
  geometry: the 7,284 records rejected on 2026-08-29 and 2026-08-30 carry
  trip identifiers minted by a build in that window and held by no snapshot
  here, which removes routes 30 and 10 from the processed output for both
  days (see Preprocessing for the full trace).

## Versioning

Archive releases follow a fixed cadence rather than being tied to specific
findings: an initial release, a quarterly release, and monthly releases
thereafter, each versioned and citable independently of the accompanying
analysis.
