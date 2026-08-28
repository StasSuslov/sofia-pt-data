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
- `scripts/derive_bbox.py` — recomputes the network bounding box from a
  GTFS Static feed's `stops.txt`/`shapes.txt`, so the bbox used for
  filtering is reproducible from data rather than hand-picked.
- `scripts/fetch_data.sh` / `scripts/scheduled_fetch.sh` — pull the archive
  from a remote collector host via `rsync`.
- `deploy/` — a `systemd` unit and a `launchd` template for running the
  collector continuously and syncing its output on a schedule.

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

## Tests

```bash
python3 -m pytest test_collect.py scripts/test_generate_manifest.py
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
  intended to be wide enough not to clip legitimate outlying routes.

## License

Code: MIT, see [LICENSE](LICENSE). Collected data, once published, will be
released separately under CC BY 4.0.
