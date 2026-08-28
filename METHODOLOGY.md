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
- Coverage percentages in early manifests can be inflated if collection
  started partway through a calendar day; a manifest should be read
  alongside its underlying start/end timestamps, not as a single number
  in isolation.

## Versioning

Archive releases follow a fixed cadence rather than being tied to specific
findings: an initial release, a quarterly release, and monthly releases
thereafter, each versioned and citable independently of the accompanying
analysis.
