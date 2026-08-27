#!/usr/bin/env python3
"""
Sofia Public Transport — GTFS-RT data collector.
Source: gtfs.sofiatraffic.bg (Център за градска мобилност)

Usage:
    # Step 1: discover available endpoints
    python collect.py --discover

    # Step 2: run 24-hour collection
    python collect.py --output data/wednesday.jsonl

    # Optional: custom interval and duration
    python collect.py --output data/saturday.jsonl --interval 45 --hours 24
"""

import argparse
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

# ─── Configuration ────────────────────────────────────────────────────────────

BASE_URL = "https://gtfs.sofiatraffic.bg"

# Candidate paths to try during discovery.
# Confirmed via urbandata.sofia.bg (Ниво 1 open data, CC BY 4.0) 2026-08-20:
#   Static:         /api/v1/static
#   Vehicle pos.:   /api/v1/vehicle-positions  ← confirmed live, returns protobuf
#   Trip updates:   /api/v1/trip-updates
#   Alerts:         /api/v1/alerts
CANDIDATE_PATHS = [
    "/api/v1/vehicle-positions",
    "/api/v1/trip-updates",
    "/api/v1/alerts",
]

# Sofia bounding box — coordinates outside this range are discarded
# (known GTFS-RT teleportation bug where vehicles appear in the Black Sea etc.)
SOFIA_BBOX = {
    "lat_min": 42.57,
    "lat_max": 42.80,
    "lon_min": 23.15,
    "lon_max": 23.55,
}

DEFAULT_INTERVAL_SEC = 45   # poll every 45 seconds
DEFAULT_HOURS = 24

# ─── Helpers ──────────────────────────────────────────────────────────────────

def is_valid_sofia_coordinate(lat: float, lon: float) -> bool:
    return (
        SOFIA_BBOX["lat_min"] <= lat <= SOFIA_BBOX["lat_max"]
        and SOFIA_BBOX["lon_min"] <= lon <= SOFIA_BBOX["lon_max"]
    )


def fetch_vehicle_positions(url: str, session: requests.Session) -> tuple[list[dict], bool]:
    """
    Fetch and parse one GTFS-RT VehiclePositions snapshot.
    Returns (records, fetch_ok). fetch_ok is False when the HTTP request or
    protobuf parse failed; True when the feed was decoded regardless of whether
    any vehicles fell inside the bounding box.
    """
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
    except requests.RequestException as e:
        print(f"[WARN] Fetch failed: {e}", file=sys.stderr)
        return [], False

    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(response.content)
    except Exception as e:
        print(f"[WARN] Protobuf parse failed: {e}", file=sys.stderr)
        return [], False

    snapshot_ts = int(datetime.now(timezone.utc).timestamp())
    records = []

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue

        v = entity.vehicle
        if not v.HasField("position"):
            continue

        lat = v.position.latitude
        lon = v.position.longitude

        if not is_valid_sofia_coordinate(lat, lon):
            continue

        record = {
            "snapshot_ts": snapshot_ts,
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

    return records, True


# ─── Discovery ────────────────────────────────────────────────────────────────

def discover_endpoint(session: requests.Session) -> None:
    """
    Try candidate paths and report which ones respond with parseable GTFS-RT data.
    Run this once to find the correct endpoint before starting collection.
    """
    print(f"Probing {BASE_URL} for GTFS-RT vehicle position endpoints...\n")

    found = False
    for path in CANDIDATE_PATHS:
        url = BASE_URL + path
        r = None
        try:
            r = session.get(url, timeout=10)
            status = r.status_code

            # Try to parse as protobuf
            feed = gtfs_realtime_pb2.FeedMessage()
            feed.ParseFromString(r.content)

            vehicle_count = sum(1 for e in feed.entity if e.HasField("vehicle"))
            print(f"  ✅  {url}")
            print(f"      HTTP {status} | {len(feed.entity)} entities | {vehicle_count} vehicles")
            print(f"      → collect with: --url {url}")
            found = True

        except requests.RequestException as e:
            print(f"  ❌  {url}  →  {e}")
        except Exception:
            status = r.status_code if r is not None else "???"
            print(f"  ⚠️   {url}  →  HTTP {status}, not valid GTFS-RT protobuf")

    if found:
        print("\nPass --url <URL> to the collection command. No source edits needed.")
    else:
        print("\nNo working endpoint found. Check BASE_URL or inspect browser network traffic")
        print("on https://sofiatraffic.bg to see what URL the site itself calls.")


# ─── Collection loop ──────────────────────────────────────────────────────────

VEHICLE_POSITIONS_URL = BASE_URL + "/api/v1/vehicle-positions"


def run_collection(output_path: Path, interval: int, hours: float, url: str) -> None:
    deadline = time.time() + hours * 3600
    total_records = 0
    poll_count = 0
    empty_snapshots = 0
    fetch_errors = 0

    # Graceful shutdown on Ctrl+C
    interrupted = False
    def _handler(sig, frame):
        nonlocal interrupted
        interrupted = True
        print("\n[INFO] Interrupted — finishing current snapshot and closing file.")
    signal.signal(signal.SIGINT, _handler)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    session.headers["User-Agent"] = "sofia-transport-research/1.0"

    print(f"Starting collection → {output_path}")
    print(f"Endpoint : {url}")
    print(f"Interval : {interval}s  |  Duration: {hours}h  |  Started: {datetime.now().isoformat()}")
    print("Press Ctrl+C to stop early.\n")

    with output_path.open("a", encoding="utf-8") as f:
        while time.time() < deadline and not interrupted:
            loop_start = time.time()

            records, fetch_ok = fetch_vehicle_positions(url, session)
            poll_count += 1

            if records:
                for r in records:
                    f.write(json.dumps(r, ensure_ascii=False) + "\n")
                f.flush()
                total_records += len(records)
                print(
                    f"[{datetime.now().strftime('%H:%M:%S')}] "
                    f"poll #{poll_count:>4} | {len(records):>4} vehicles | "
                    f"total rows: {total_records:,}"
                )
            elif not fetch_ok:
                fetch_errors += 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] poll #{poll_count:>4} | fetch error (#{fetch_errors})")
            else:
                empty_snapshots += 1
                print(f"[{datetime.now().strftime('%H:%M:%S')}] poll #{poll_count:>4} | empty snapshot (#{empty_snapshots})")

            elapsed = time.time() - loop_start
            sleep_for = max(0, interval - elapsed)
            time.sleep(sleep_for)

    session.close()
    print(f"\nDone. Polls: {poll_count} | Records: {total_records:,} | "
          f"Empty snapshots: {empty_snapshots} | Fetch errors: {fetch_errors}")
    print(f"Output: {output_path} ({output_path.stat().st_size / 1024:.1f} KB)")


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Sofia GTFS-RT collector")
    parser.add_argument("--discover", action="store_true",
                        help="Probe candidate endpoints and exit")
    parser.add_argument("--url", type=str, default=VEHICLE_POSITIONS_URL,
                        help=f"GTFS-RT vehicle positions URL (default: {VEHICLE_POSITIONS_URL})")
    parser.add_argument("--output", type=Path, default=Path("data/snapshot.jsonl"),
                        help="Output file (newline-delimited JSON)")
    parser.add_argument("--interval", type=int, default=DEFAULT_INTERVAL_SEC,
                        help=f"Poll interval in seconds (default: {DEFAULT_INTERVAL_SEC})")
    parser.add_argument("--hours", type=float, default=DEFAULT_HOURS,
                        help=f"Collection duration in hours (default: {DEFAULT_HOURS})")
    args = parser.parse_args()

    session = requests.Session()
    session.headers["User-Agent"] = "sofia-transport-research/1.0"

    if args.discover:
        discover_endpoint(session)
        session.close()
        return

    run_collection(args.output, args.interval, args.hours, args.url)


if __name__ == "__main__":
    main()