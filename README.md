# Sofia PT Data

[![Code DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22256653.svg)](https://doi.org/10.5281/zenodo.22256653)
[![Data DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.22285128.svg)](https://doi.org/10.5281/zenodo.22285128)

I collect Sofia's public transport feeds and keep every day of them, so that
a claim about how this network runs can be checked against something. The
repository holds the collector and the pipeline that turns its output into
speeds a map can draw. The collected data ships as a separate archive under
CC BY 4.0.

[METHODOLOGY.md](METHODOLOGY.md) has the data model, the thresholds and the
limitations I know about.

## What's here

- `collect.py` polls the GTFS-RT vehicle-positions feed at a fixed interval
  and appends each validated snapshot to a JSONL archive, dropping whatever
  falls outside Sofia's network bounding box.
- `scripts/generate_manifest.py` writes a SHA256 and coverage manifest for a
  day's archive file, so you can check a published day yourself instead of
  trusting my word for it.
- `scripts/verify_remote_checksums.py` reads each closed day's checksum off
  the collector host over `ssh` and records the comparison in that day's
  manifest. That way the local copy is checked against what the collector
  wrote, not only against itself after `rsync`. Each file is checked once. A
  mismatch exits with its own code; an unreachable host does not.
- `scripts/derive_bbox.py` recomputes the network bounding box from a GTFS
  Static feed's `stops.txt` and `shapes.txt`, so I can show where the box
  came from instead of asserting it.
- `scripts/archive_static_feed.py` downloads the GTFS Static feed on a
  schedule and saves a dated snapshot when the contents changed. It compares
  the members inside the zip, so a rebuild of identical data does not look
  like a republish.
- `scripts/segment_speeds.py` projects raw GTFS-RT positions onto GTFS
  Static shapes and derives one speed sample per 200 m segment. Monday to
  Friday samples become a "typical weekday" median per segment and
  15-minute time slot.
- `scripts/export_web.py` turns that aggregation into the small,
  timeslot-sliced JSON files a browser fetches on its own, with no backend.
- `scripts/fetch_data.sh` and `scripts/scheduled_fetch.sh` pull the archive
  off the collector host with `rsync`.
- `deploy/` holds the `systemd` units and a `launchd` template: continuous
  collection, a GTFS Static snapshot when the feed changes, gzip for closed
  days so the collector's disk survives, and a sync on a schedule. The
  readers handle a day file in either form, and a manifest always describes
  the uncompressed bytes, so compressing a day never changes its recorded
  checksum.

## Data sources

- **GTFS-RT and GTFS Static**, published by Sofia's public transport
  operator (ЦГМ) through the [urbandata.sofia.bg](https://urbandata.sofia.bg)
  open data portal. Level 1, CC BY 4.0, no registration.
- **OpenStreetMap** through the Overpass API for district boundaries and the
  road network. I need those for the accessibility analysis, which is not
  written yet.

I enter nothing by hand, and I assume nothing about the network that a feed
has not confirmed.

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

Every poll goes into a companion `<date>.polls.jsonl` heartbeat file with
its outcome, whether or not it produced vehicle records. Without that file,
a quiet feed and a dead collector leave the same trace in the archive.

## Verifying an archive file

```bash
python3 scripts/generate_manifest.py data/sofia/2026-08-27.jsonl
```

You get a manifest with the file checksums and a gap analysis built from the
heartbeat log. Days collected before heartbeat logging existed get their gap
analysis from the data file alone, and the manifest marks them as
lower-confidence.

## Preprocessing

Two scripts stand between a raw day and anything a map can render.

`scripts/segment_speeds.py` projects each vehicle position onto its trip's
GTFS Static shape, turns consecutive same-trip positions into a speed sample
for a 200 m segment of that shape, and aggregates Monday to Friday samples
into a per-segment, per-15-minute median (the "typical weekday", see
METHODOLOGY.md's Aggregation section). Every discarded record moves one of
six named counters: `trip_not_in_static`, `shape_not_found`,
`non_positive_time_delta`, `gap_too_large`, `moved_backward`,
`speed_too_high`. The counters are reported per day and in total, next to
the thresholds that produced them. METHODOLOGY.md's Preprocessing section
explains the projection and what each counter means.

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

It writes `segment_speeds_<date>.jsonl`, one row per accepted sample, and
`typical_weekday.json` with the median aggregation, a per-day reject
breakdown and which static snapshot matched which day. Both land under
`--output-dir` (default `<data_dir>/processed`).

`scripts/export_web.py` reads that output and writes what a browser fetches
directly: one small JSON per 15-minute time slot, pointing into a shared
`geometry.json` by index so coordinates are not repeated in every bin.

```bash
python3 scripts/export_web.py data/sofia/static/gtfs_2026-08-27.zip data/sofia

# one specific day's own median instead of the Monday-Friday typical weekday
python3 scripts/export_web.py data/sofia/static/gtfs_2026-08-27.zip data/sofia \
    --day 2026-08-28

# a directory of snapshots: every one found is loaded and merged
python3 scripts/export_web.py data/sofia/static data/sofia
```

It writes `geometry.json`, one `timeslots/<HHMM>.json` per surviving time
slot and a `manifest.json` carrying the thresholds applied, the bins dropped
and the export's own known limitations. Output goes under `--output-dir`
(default `<data_dir>/web/<mode>`, where `<mode>` is `typical_weekday` or the
`--day` value).

`--min-samples N` (default 2) drops bins built from fewer than N
observations before anything is written. The count survives the threshold:
`n_samples` ships with every bin that stays, so a thin median stays visible
to the client. `--processed-dir` points the export at `segment_speeds.py`'s
output when it went somewhere other than `<data_dir>/processed`.
`segment_speeds.py` takes `--timezone` (default `Europe/Sofia`), which fixes
both the time-slot bins and which days count as Monday to Friday.

Both scripts accept either a single GTFS Static zip or a directory of
`gtfs_<YYYY-MM-DD>.zip` snapshots. The directory form exists because the
agency rebuilds the static feed every night, and a stale snapshot degrades
preprocessing without announcing it. `scripts/archive_static_feed.py` keeps
that directory populated. METHODOLOGY.md's Aggregation and Known limitations
sections have what a stale snapshot did to one 2026-08-31 run, and how the
fix works.

## Tests

```bash
python3 -m pytest
```

## Known limitations

- The GTFS-RT feed carries no metro, so everything here describes surface
  transport.
- Speed between two consecutive snapshots is interpolated from position and
  elapsed time, with the uncertainty that implies.
- The feed drops connections and returns empty responses from time to time.
  I log those gaps and leave them as gaps.
- Coordinates outside Sofia's network bounding box are dropped at collection
  time, because the feed emits occasional coordinate teleports. The box
  comes from the static feed (see `scripts/derive_bbox.py`) and is meant to
  be wide enough to keep the outlying routes. My first box was hand-picked
  and too narrow, and it cost me the outlying settlements from 2026-08-27 to
  the afternoon of 2026-08-28. METHODOLOGY.md's Known limitations section
  has the measured size of that loss.
- The feed reports vehicle speed in km/h in a field GTFS-RT specifies as
  metres per second. The raw archive keeps the field under the name it
  arrived with, `speed_ms`. Preprocessed output calls it `feed_speed_kmh`,
  which is the unit the values are in. METHODOLOGY.md has the numbers behind
  that reading.
- The "typical weekday" median rests on five weekdays so far, one of them a
  partial day, and its base is uneven across the map. Segments outside that
  old, narrower box drew on fewer days and carry fewer samples per bin.
  METHODOLOGY.md's Known limitations section has the measured extent. Read
  it together with the `n_samples` that ships next to every median.

## Citing

The code and the methodology are archived on Zenodo. The concept DOI below
always resolves to the latest version; cite a specific version from the
record's own page when you need the exact code behind a result.

> Suslov, Stanislav (2026). *Sofia PT Data: an open archive and analysis
> pipeline for Sofia's public transport feeds*. Zenodo.
> https://doi.org/10.5281/zenodo.22256653

The collected data has its own Zenodo record under CC BY 4.0, versioned
apart from the code. v1.0.0 covers 27 August to 2 September 2026.

> Suslov, Stanislav (2026). *Sofia public transport: raw GTFS-Realtime
> vehicle positions and static feed snapshots, 2026-08-27 to 2026-09-02*.
> Zenodo. https://doi.org/10.5281/zenodo.22285128

Cite both when you cite a result: the data it rests on, and the code that
produced it.

## License

Code: MIT, see [LICENSE](LICENSE). Collected data is released separately
under CC BY 4.0: https://doi.org/10.5281/zenodo.22285128
