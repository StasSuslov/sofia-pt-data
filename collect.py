#!/usr/bin/env python3
"""
Sofia Public Transport — GTFS-RT data collector.
Source: gtfs.sofiatraffic.bg (Център за градска мобилност)

Usage:
    # Step 1: discover available endpoints
    python collect.py --discover

    # Step 2a: fixed-duration run into a single file
    python collect.py --output data/wednesday.jsonl --hours 24

    # Step 2b: continuous collection with daily rotation (for long-running
    # deployments, e.g. under systemd) — writes <output-dir>/<YYYY-MM-DD>.jsonl,
    # rolling over at local midnight in --timezone. --hours <= 0 means run
    # until stopped (SIGINT/SIGTERM) instead of a fixed duration.
    python collect.py --output-dir data/sofia --hours 0

Each data file <name>.jsonl has a companion <name>.polls.jsonl heartbeat log
with one line per poll attempt — fetch_ok, vehicle_count (post-bbox-filter),
and entities_total / vehicles_with_position / dropped_out_of_bbox so the bbox
filter's own losses are auditable rather than silent. See heartbeat_path_for()
and PollResult. Feed both files into scripts/generate_manifest.py to get a
checksummed, gap-annotated coverage report per day.
"""

import argparse
import json
import os
import signal
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

import requests
from google.transit import gtfs_realtime_pb2

from config import (  # noqa: E402
    DEFAULT_HOURS,
    CityProfileError,
    city_slugs,
    load_city,
    load_city_for_path,
)

# ─── Configuration ────────────────────────────────────────────────────────────

# Everything this collector needs that differs between cities — feed URLs,
# bounding box, timezone, poll cadence, the User-Agent an operator reads in
# their logs — comes from cities/<slug>.json through config.load_city().
# main() resolves the city from --city or, failing that, from the data
# directory it was given, and passes the values down as arguments: no
# function below decides for itself which city it is working on.

# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_in_network_bbox(lat: float, lon: float, bbox: dict) -> bool:
    """Is this position inside the city's network, or is it teleportation?"""
    return (
        bbox["lat_min"] <= lat <= bbox["lat_max"]
        and bbox["lon_min"] <= lon <= bbox["lon_max"]
    )


def dated_output_path(output_dir: Path, tz: ZoneInfo, when: datetime | None = None) -> Path:
    """Path for the daily-rotated file covering `when`'s local calendar date in `tz`."""
    local_date = (when or datetime.now(tz)).astimezone(tz).date()
    return output_dir / f"{local_date.isoformat()}.jsonl"


def heartbeat_path_for(data_path: Path) -> Path:
    """
    Path for the companion per-poll heartbeat log next to a data file.

    A poll that fetches successfully but finds zero vehicles in the bbox, or
    one whose HTTP/protobuf fetch fails outright, writes no rows to the data
    file — from the vehicle data alone those two cases and genuine collector
    downtime are indistinguishable. The heartbeat log records every poll
    attempt (outcome and vehicle count) so completeness can be verified
    independently of the collector's own run-summary counters.
    """
    return data_path.with_name(f"{data_path.stem}.polls.jsonl")


class PollResult(NamedTuple):
    records: list[dict]
    fetch_ok: bool
    poll_ts: int
    entities_total: int            # feed entities that are vehicles at all
    vehicles_with_position: int    # ...and report a position
    dropped_out_of_bbox: int       # ...but fell outside NETWORK_BBOX


def fetch_vehicle_positions(url: str, session: requests.Session, bbox: dict) -> PollResult:
    """
    Fetch and parse one GTFS-RT VehiclePositions snapshot.

    `poll_ts` is stamped once, before the request, and used both for any
    records produced and for the caller's heartbeat entry — a single clock
    for the whole poll, rather than one timestamp for the attempt and a
    second, slightly later one only on success.

    `fetch_ok` is False when the HTTP request or protobuf parse failed; True
    when the feed was decoded regardless of whether any vehicles fell inside
    the bounding box. The three counts let a caller audit exactly how much
    the bbox filter is discarding, poll by poll, instead of that being
    invisible.
    """
    poll_ts = int(datetime.now(timezone.utc).timestamp())

    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] Fetch failed: {e}", file=sys.stderr)
        return PollResult([], False, poll_ts, 0, 0, 0)

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(response.content)
    except Exception as e:
        print(f"[WARN] Protobuf parse failed: {e}", file=sys.stderr)
        return PollResult([], False, poll_ts, 0, 0, 0)

    records = []
    entities_total = 0
    vehicles_with_position = 0
    dropped_out_of_bbox = 0

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        entities_total += 1

        v = entity.vehicle
        if not v.HasField("position"):
            continue
        vehicles_with_position += 1

        lat = v.position.latitude
        lon = v.position.longitude

        if not is_in_network_bbox(lat, lon, bbox):
            dropped_out_of_bbox += 1
            continue

        record = {
            "snapshot_ts": poll_ts,
            "vehicle_id":  v.vehicle.id if v.HasField("vehicle") else None,
            "route_id":    v.trip.route_id if v.HasField("trip") else None,
            "trip_id":     v.trip.trip_id if v.HasField("trip") else None,
            "lat":         round(lat, 6),
            "lon":         round(lon, 6),
            "bearing":     v.position.bearing if v.position.HasField("bearing") else None,  # type: ignore[attr-defined]
            "speed_ms":    round(v.position.speed, 2) if v.position.HasField("speed") else None,  # type: ignore[attr-defined]
            "vehicle_ts":  v.timestamp if v.HasField("timestamp") else None,
        }
        records.append(record)

    return PollResult(records, True, poll_ts, entities_total, vehicles_with_position, dropped_out_of_bbox)


# ─── Discovery ────────────────────────────────────────────────────────────────

def discover_endpoint(session: requests.Session, city: dict) -> None:
    """
    Probe the city profile's own feed URLs and report which ones respond with
    parseable GTFS-RT data. Run this when adding a city, or to re-verify a
    feed that was working when the profile was written.

    The URLs come from the profile whole, never assembled from a base plus a
    path: a domain that resolves says nothing about a path that exists, and
    section 7 of CLAUDE.md forbids guessing the second from the first. A feed
    that a probe cannot find belongs in the profile only after someone finds
    the real URL and writes it down.
    """
    print(f"Probing {city['name']}'s GTFS-RT feeds ({city['slug']})...\n")

    found = False
    for name, url in city["feeds"].items():
        if name == "static":
            continue  # a zip, not protobuf: parsing it here would only ever fail
        r = None
        try:
            r = session.get(url, timeout=10)
            status = r.status_code

            # Try to parse as protobuf
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(r.content)

            vehicle_count = sum(1 for e in feed.entity if e.HasField("vehicle"))
            print(f"  ✅  {name}: {url}")
            print(f"      HTTP {status} | {len(feed.entity)} entities | {vehicle_count} vehicles")
            print(f"      → collect with: --url {url}")
            found = True

        except requests.RequestException as e:
            print(f"  ❌  {name}: {url}  →  {e}")
        except Exception:
            status = r.status_code if r is not None else "???"
            print(f"  ⚠️   {name}: {url}  →  HTTP {status}, not valid GTFS-RT protobuf")

    if found:
        print(f"\nThese are the URLs already in cities/{city['slug']}.json; collection uses "
              "them by default, and --url overrides one for a single run.")
    else:
        print(f"\nNo feed in cities/{city['slug']}.json answered with GTFS-RT. Check the "
              "agency's open-data portal for the current URLs, or watch what the operator's "
              "own site requests, and update the profile with what you find.")


# ─── Collection loop ──────────────────────────────────────────────────────────


def ping_healthcheck(url: str) -> None:
    """
    Best-effort ping to a dead-man's-switch URL (e.g. https://healthchecks.io).
    Proves the poll loop is still iterating — independent of whether any given
    fetch succeeds — so a frozen process or a downed VPS gets noticed even
    when no one is watching the logs. Uses its own short-lived request rather
    than the feed's session, so monitoring and feed cookies never mix.

    Never raises, deliberately catching Exception rather than just
    requests.RequestException: a monitoring hiccup — of any kind — must not
    be able to take down the thing it's monitoring.
    """
    try:
        requests.get(url, timeout=5)
    except Exception as e:  # noqa: BLE001 - intentional, see docstring
        print(f"[WARN] Healthcheck ping failed: {e}", file=sys.stderr)


def run_collection(
    interval: int,
    hours: float,
    url: str,
    *,
    bbox: dict,
    user_agent: str,
    tz_name: str,
    output_path: Path | None = None,
    output_dir: Path | None = None,
    healthcheck_url: str | None = None,
    healthcheck_every: int = 20,
) -> None:
    """
    Poll `url` every `interval` seconds until `hours` elapse (or forever if
    `hours` <= 0), writing newline-delimited JSON records.

    `bbox`, `user_agent` and `tz_name` have no defaults on purpose. Each one
    belongs to a particular city, and a default here would let the wrong
    city's bounds silently filter another city's feed — the filter drops a
    position before it reaches disk, so that mistake leaves no trace to find
    later.

    Exactly one of `output_path` (single fixed file) or `output_dir` (daily
    rotation, file named <YYYY-MM-DD>.jsonl in `tz_name`) must be given.

    If `healthcheck_url` is set, it's pinged every `healthcheck_every`
    successful loop iterations (default 20, i.e. ~15 min at the default 45s
    interval) as a dead-man's switch.
    """
    rotating = output_dir is not None
    forever = hours <= 0
    deadline = float("inf") if forever else time.time() + hours * 3600
    total_records = 0
    poll_count = 0
    empty_snapshots = 0
    fetch_errors = 0

    # Graceful shutdown on Ctrl+C (SIGINT) or `systemctl stop` (SIGTERM).
    # An Event rather than a flag because the loop idles inside it: see the
    # wait() at the bottom of the loop.
    stop = threading.Event()
    def _handler(sig, frame):
        stop.set()
        print("\n[INFO] Interrupted — finishing current snapshot and closing file.")
    signal.signal(signal.SIGINT, _handler)
    signal.signal(signal.SIGTERM, _handler)

    session = requests.Session()
    session.headers["User-Agent"] = user_agent

    if rotating:
        tz = ZoneInfo(tz_name)
        output_dir.mkdir(parents=True, exist_ok=True)
        current_path = dated_output_path(output_dir, tz)
    else:
        tz = None
        output_path.parent.mkdir(parents=True, exist_ok=True)
        current_path = output_path

    f = current_path.open("a", encoding="utf-8")
    hb_f = heartbeat_path_for(current_path).open("a", encoding="utf-8")

    mode_desc = f"daily rotation in {output_dir} ({tz_name})" if rotating else "single file"
    print(f"Starting collection → {current_path}")
    print(f"Endpoint : {url}")
    print(
        f"Interval : {interval}s  |  Duration: {'forever' if forever else f'{hours}h'}  |  "
        f"Mode: {mode_desc}  |  Started: {datetime.now().isoformat()}"
    )
    print("Press Ctrl+C to stop early.\n")

    try:
        while time.time() < deadline and not stop.is_set():
            loop_start = time.time()

            if rotating:
                fresh_path = dated_output_path(output_dir, tz)
                if fresh_path != current_path:
                    f.close()
                    hb_f.close()
                    current_path = fresh_path
                    f = current_path.open("a", encoding="utf-8")
                    hb_f = heartbeat_path_for(current_path).open("a", encoding="utf-8")
                    print(f"[{datetime.now().strftime('%H:%M:%S')}] rotated → {current_path}")

            poll = fetch_vehicle_positions(url, session, bbox)
            poll_count += 1

            if healthcheck_url and poll_count % healthcheck_every == 0:
                ping_healthcheck(healthcheck_url)

            # Data written (and durably flushed) before the heartbeat line
            # that reports it, so the heartbeat can never claim more rows
            # exist than actually landed on disk.
            if poll.records:
                for r in poll.records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                total_records += len(poll.records)

            hb_f.write(json.dumps({
                "snapshot_ts": poll.poll_ts,
                "fetch_ok": poll.fetch_ok,
                "vehicle_count": len(poll.records),
                "entities_total": poll.entities_total,
                "vehicles_with_position": poll.vehicles_with_position,
                "dropped_out_of_bbox": poll.dropped_out_of_bbox,
            }, ensure_ascii=False) + "\n")
            hb_f.flush()

            if poll.records:
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"poll #{poll_count:>4} | {len(poll.records):>4} vehicles"
                    f"{f' | {poll.dropped_out_of_bbox} dropped (bbox)' if poll.dropped_out_of_bbox else ''} | "
                    f"total rows: {total_records:,}"
                )
            elif not poll.fetch_ok:
                fetch_errors += 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] poll #{poll_count:>4} | fetch error (#{fetch_errors})")
            else:
                empty_snapshots += 1
                note = f" | {poll.dropped_out_of_bbox} dropped (bbox)" if poll.dropped_out_of_bbox else ""
                print(f"[{datetime.now().strftime('%H:%M:%S')}] poll #{poll_count:>4} | empty snapshot (#{empty_snapshots}){note}")

            # Event.wait, not time.sleep: an interrupted sleep resumes once
            # the handler returns (PEP 475), so a SIGTERM landing in the idle
            # stretch printed "finishing current snapshot" and then held the
            # process for the rest of the interval — 45 s on the deployed
            # cadence, on every systemctl stop or restart.
            elapsed = time.time() - loop_start
            stop.wait(max(0, interval - elapsed))
    finally:
        f.close()
        hb_f.close()
        session.close()

    print(f"\nDone. Polls: {poll_count} | Records: {total_records:,} | "
          f"Empty snapshots: {empty_snapshots} | Fetch errors: {fetch_errors}")
    print(f"Last output file: {current_path}")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GTFS-RT collector")
    parser.add_argument("--city", type=str, default=None,
                        help="City profile to collect (cities/<slug>.json; have: "
                             f"{', '.join(city_slugs()) or 'none'}). Default: read from "
                             "--output-dir, which is laid out as .../data/<city>/")
    parser.add_argument("--discover", action="store_true",
                        help="Probe the city's feed URLs and exit")
    parser.add_argument("--url", type=str, default=None,
                        help="GTFS-RT vehicle positions URL (default: the city profile's)")
    parser.add_argument("--output", type=Path, default=None,
                        help="Single output file, no rotation (default: data/snapshot.jsonl "
                             "if --output-dir is not given)")
    parser.add_argument("--output-dir", type=Path, default=None,
                        help="Directory for daily-rotated output files named <YYYY-MM-DD>.jsonl "
                             "(mutually exclusive with --output)")
    parser.add_argument("--timezone", type=str, default=None,
                        help="Timezone for day-boundary rotation (default: the city profile's)")
    parser.add_argument("--interval", type=int, default=None,
                        help="Poll interval in seconds (default: the city profile's)")
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS,
                        help=f"Collection duration in hours (default: {DEFAULT_HOURS}). "
                             "0 or negative means run indefinitely until stopped.")
    parser.add_argument("--healthcheck-url", type=str, default=os.environ.get("HEALTHCHECK_URL"),
                        help="Dead-man's-switch URL (e.g. a healthchecks.io check), pinged "
                             "periodically to prove the loop is still running. Defaults to the "
                             "HEALTHCHECK_URL env var; omit both to disable.")
    parser.add_argument("--healthcheck-every", type=int, default=20,
                        help="Ping the healthcheck URL every N polls (default: 20)")
    args = parser.parse_args()

    if args.output and args.output_dir:
        parser.error("--output and --output-dir are mutually exclusive")

    # Two passes over the city-dependent options, because they are their own
    # chicken and egg: the defaults live in the profile, and which profile to
    # read is itself one of the arguments. So they parse as None, the city is
    # resolved, and only then does the profile fill the gaps — leaving an
    # explicit flag to win over the profile, as a flag should.
    try:
        if args.city or args.output_dir:
            city = load_city_for_path(args.output_dir or Path("."), slug=args.city)
        else:
            parser.error("pass --city, or --output-dir laid out as .../data/<city>/")
    except CityProfileError as e:
        parser.error(str(e))

    url = args.url or city["feeds"]["vehicle_positions"]
    tz_name = args.timezone or city["timezone"]
    interval = args.interval if args.interval is not None else city["poll_interval_sec"]

    if args.discover:
        session = requests.Session()
        session.headers["User-Agent"] = city["user_agent"]
        discover_endpoint(session, city)
        session.close()
        return

    if args.healthcheck_every < 1:
        parser.error("--healthcheck-every must be at least 1")

    if not args.output and not args.output_dir:
        args.output = Path("data/snapshot.jsonl")

    run_collection(
        interval,
        args.hours,
        url,
        bbox=city["bbox"],
        user_agent=city["user_agent"],
        tz_name=tz_name,
        output_path=args.output,
        output_dir=args.output_dir,
        healthcheck_url=args.healthcheck_url,
        healthcheck_every=args.healthcheck_every,
    )


if __name__ == "__main__":
    main()
