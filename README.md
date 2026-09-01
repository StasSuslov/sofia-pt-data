# Sofia PT Data

An independent, open-data archive and analysis pipeline for Sofia's public
transport network. The goal is a reproducible dataset and methodology — not
a product — built entirely from openly licensed sources.

See [METHODOLOGY.md](METHODOLOGY.md) for the data model, known limitations,
and analytical approach.

## What's here

- `collect.py` — polls the GTFS-RT vehicle-positions feed at a fixed
  interval and appends validated snapshots to a JSONL archive, filtered to
  Sofia's network bounding box.
- `scripts/generate_manifest.py` — produces a SHA256 + coverage manifest for
  a day's archive file, so a published dataset can be checked for
  completeness and integrity rather than taken on trust.
- `scripts/verify_remote_checksums.py` — reads each closed day's checksum
  off the collector host over `ssh` and records the comparison in that day's
  manifest, so the archive is checked against what the collector actually
  wrote rather than only against itself after `rsync`. Each file is checked
  once; a mismatch exits with its own code, an unreachable host does not.
- `scripts/derive_bbox.py` — recomputes the network bounding box from a
  GTFS Static feed's `stops.txt`/`shapes.txt`, so the bbox used for
  filtering is reproducible from data rather than hand-picked.
- `scripts/archive_static_feed.py` — downloads the GTFS Static feed on a
  schedule and saves a dated snapshot only when the feed's contents changed,
  comparing member contents rather than the zip's bytes so a rebuild of
  identical data is not mistaken for a republish.
- `scripts/segment_speeds.py` — projects raw GTFS-RT vehicle positions onto
  GTFS Static shapes, derives a speed sample per 200 m segment, and
  aggregates Monday–Friday samples into a "typical weekday" median per
  segment and 15-minute time slot.
- `scripts/export_web.py` — turns that aggregation into the small,
  timeslot-sliced static JSON files a browser fetches directly, with no
  backend.
- `scripts/fetch_data.sh` / `scripts/scheduled_fetch.sh` — pull the archive
  from a remote collector host via `rsync`.
- `deploy/` — `systemd` units and a `launchd` template for running the
  collector continuously, capturing a GTFS Static snapshot when the feed
  changes, gzipping closed day files on the collector host so its disk
  doesn't fill, and syncing the output on a schedule. Day files are
  read transparently in either form; a manifest always describes the
  uncompressed bytes, so compression never changes a recorded checksum.

## Data sources

- **GTFS-RT and GTFS Static** — published by Sofia's public transport
  operator (ЦГМ) via the [urbandata.sofia.bg](https://urbandata.sofia.bg)
  open data portal, Level 1, CC BY 4.0, no registration required.
- **OpenStreetMap** (via the Overpass API) — district boundaries and road
  network, used for planned accessibility analysis.

No manually entered data and no assumptions not confirmed by a feed.

## Running the collector

```bash
pip install -r requirements.txt

# probe which endpoints are live
python3 collect.py --discover

# continuous collection with daily file rotation (Europe/Sofia local day)
python3 collect.py --output-dir data/sofia --hours 0
```

Key flags (`python3 collect.py --help` for the full list):

| Flag | Purpose |
|---|---|
| `--output-dir DIR` | Daily-rotated files, `DIR/<YYYY-MM-DD>.jsonl` |
| `--output FILE` | Single file, no rotation |
| `--hours N` | Stop after N hours; `0` or negative runs until `SIGINT`/`SIGTERM` |
| `--interval N` | Poll interval in seconds |
| `--healthcheck-url URL` | Ping a dead-man's-switch URL (e.g. healthchecks.io) periodically |

Each poll is logged to a companion `<date>.polls.jsonl` heartbeat file
(success, empty response, or fetch error) regardless of whether any
vehicle records were written, so a silent feed outage is distinguishable
from a silent collector outage.

## Verifying an archive file

```bash
python3 scripts/generate_manifest.py data/sofia/2026-08-27.jsonl
```

Produces a manifest with file checksums and a gap analysis derived from the
heartbeat log (or, for files collected before heartbeat logging existed,
from the data file alone — marked as lower-confidence).

## Preprocessing

Two scripts turn a day's raw vehicle-position archive into what a map can
render.

`scripts/segment_speeds.py` projects each vehicle position onto its trip's
GTFS Static shape, turns consecutive same-trip positions into a speed
sample for a 200 m segment of that shape, and aggregates Monday–Friday
samples into a per-segment, per-15-minute-timeslot median (the "typical
weekday", see METHODOLOGY.md's Aggregation section). Nothing is dropped
silently: six named counters (`trip_not_in_static`, `shape_not_found`,
`non_positive_time_delta`, `gap_too_large`, `moved_backward`,
`speed_too_high`) are reported per day and in total, next to the thresholds
that produced them. METHODOLOGY.md's Preprocessing section explains the
projection, the thresholds and what each counter means.

```bash
# one static feed for every day, three specific days
python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip \
    data/sofia --output-dir data/sofia/processed 2026-08-28 2026-08-31

# no explicit dates: every day file found in data/sofia
python3 scripts/segment_speeds.py data/sofia/static/gtfs_2026-08-27.zip data/sofia

# a directory of gtfs_<YYYY-MM-DD>.zip snapshots instead of one file: each
# day is matched to the latest snapshot dated on or before it
python3 scripts/segment_speeds.py data/sofia/static data/sofia
```

Writes `segment_speeds_<date>.jsonl` (one row per accepted sample) and
`typical_weekday.json` (the median aggregation, with a per-day reject-count
breakdown and which static snapshot matched which day) under `--output-dir`
(default `<data_dir>/processed`).

`scripts/export_web.py` reads that output and writes the static files a
browser fetches directly: one small JSON per 15-minute time slot,
referencing a shared `geometry.json` by index rather than repeating
coordinates in every bin.

```bash
python3 scripts/export_web.py data/sofia/static/gtfs_2026-08-27.zip data/sofia

# one specific day's own median instead of the Monday-Friday typical weekday
python3 scripts/export_web.py data/sofia/static/gtfs_2026-08-27.zip data/sofia \
    --day 2026-08-28

# a directory of snapshots: every one found is loaded and merged
python3 scripts/export_web.py data/sofia/static data/sofia
```

Writes `geometry.json`, one `timeslots/<HHMM>.json` per surviving time
slot, and a `manifest.json` (thresholds applied, bins dropped, and the
export's own known limitations) under `--output-dir` (default
`<data_dir>/web/<mode>`, where `<mode>` is `typical_weekday` or the `--day`
value).

`--min-samples N` (default 2) drops bins built from fewer than N
observations before anything is written. The count survives the threshold:
`n_samples` ships with every retained bin, so a thin median stays visible to
the client instead of being hidden by the cutoff. `--processed-dir` points
the export at `segment_speeds.py`'s output if it was written somewhere other
than `<data_dir>/processed`; `segment_speeds.py` itself takes `--timezone`
(default `Europe/Sofia`), which fixes both the time-slot bins and which days
count as Monday to Friday.

Both scripts accept either a single GTFS Static zip (one feed for every day
or bin processed) or a directory of `gtfs_<YYYY-MM-DD>.zip` snapshots. The
directory form exists because the agency republishes the static feed from
time to time, and a stale snapshot degrades preprocessing silently.
`scripts/archive_static_feed.py` keeps that directory populated. See
METHODOLOGY.md's Aggregation and Known limitations sections for what a stale
snapshot did to one 2026-08-31 run and how the fix works.

## Tests

```bash
python3 -m pytest
```

## Known limitations

- The GTFS-RT feed does not include Sofia's metro.
- Speeds between consecutive snapshots are interpolated, not measured
  directly, and carry the corresponding uncertainty.
- The feed occasionally has gaps (dropped connections, empty responses);
  these are logged, not silently interpolated over.
- Coordinates outside Sofia's network bounding box are dropped at
  collection time — a known GTFS-RT coordinate-teleport artifact. The
  bbox is derived from the static feed (see `scripts/derive_bbox.py`) and
  intended to be wide enough not to clip legitimate outlying routes. An
  earlier, hand-picked box was not, and it cost the outlying settlements
  from 2026-08-27 to the afternoon of 2026-08-28; METHODOLOGY.md's Known
  limitations section has the measured extent of that loss.
- The feed reports vehicle speed in km/h in a field GTFS-RT specifies as
  metres per second. The raw archive keeps the field under the name it
  arrived with (`speed_ms`); preprocessed output calls it `feed_speed_kmh`,
  because that is the unit the values are in. See METHODOLOGY.md.
- The "typical weekday" median currently rests on three weekdays, one of
  them a partial day. Read it together with the `n_samples` shipped next to
  every median rather than as a settled figure.

## License

Code: MIT, see [LICENSE](LICENSE). Collected data, once published, will be
released separately under CC BY 4.0.
