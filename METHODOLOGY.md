# Methodology

This document describes the data collection and analysis method, published
ahead of any specific findings. The intent is to fix the method first, so
later results can be checked against a method that didn't change to fit
them.

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
  key and keep pooling samples across them: 98.3% of shapes, across the
  two snapshots collected so far. The 32 that changed produce different
  keys instead, starting a new series that never merges with the old one.
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
- GTFS Static is republished by the agency from time to time, and
  `feed_version` reads `1.0` in every snapshot collected so far, so it
  cannot be used to tell one version from another. Each processed day is
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
  the median built from 2026-08-28 and 2026-08-31. This is not a
  hypothetical edge case in the days collected so far. Per-day reject
  counts are kept in the output rather than only an archive-wide total, so
  a stale snapshot shows up as one day's count climbing instead of being
  averaged away against every other day.
- Capturing GTFS Static snapshots on a schedule is not yet automated;
  every snapshot in the archive so far has been taken by hand. Until that
  changes, a feed republish that nobody notices means a day can get
  processed against a superseded snapshot. That is visible in principle,
  since that day's rejected-record count in the per-day breakdown climbs,
  but not yet caught automatically.

## Versioning

Archive releases follow a fixed cadence rather than being tied to specific
findings: an initial release, a quarterly release, and monthly releases
thereafter, each versioned and citable independently of the accompanying
analysis.
