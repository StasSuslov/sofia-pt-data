"""One poll of the GTFS-RT VehiclePositions feed, filtered to the network."""

import datetime as dt
from typing import NamedTuple

import requests
from google.transit import gtfs_realtime_pb2

from collector.console import warn
from collector.records import vehicle_record

FETCH_TIMEOUT_SEC = 15


def is_in_network_bbox(lat: float, lon: float, bbox: dict) -> bool:
    """Is this position inside the city's network, or is it teleportation?"""
    return (
        bbox['lat_min'] <= lat <= bbox['lat_max']
        and bbox['lon_min'] <= lon <= bbox['lon_max']
    )


class PollResult(NamedTuple):
    """Outcome of one poll: its records plus the counts that audit the bbox."""

    records: list[dict]
    fetch_ok: bool
    poll_ts: int
    entities_total: int  # feed entities that are vehicles at all
    vehicles_with_position: int  # ...and report a position
    dropped_out_of_bbox: int  # ...but fell outside NETWORK_BBOX


def _get_ok(url: str, session: requests.Session) -> requests.Response:
    response = session.get(url, timeout=FETCH_TIMEOUT_SEC)
    response.raise_for_status()
    return response


def parse_feed(raw: bytes) -> gtfs_realtime_pb2.FeedMessage:
    """Decode a GTFS-RT protobuf payload."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(raw)
    return feed


def _load_feed(url: str, session: requests.Session):
    """Return the decoded feed, or None after warning that it failed."""
    try:
        response = _get_ok(url, session)
    except requests.RequestException as error:
        warn(f'Fetch failed: {error}')
        return None
    try:
        return parse_feed(response.content)
    except Exception as error:
        warn(f'Protobuf parse failed: {error}')
        return None


def fetch_vehicle_positions(
    url: str,
    session: requests.Session,
    bbox: dict,
) -> PollResult:
    """Fetch and parse one GTFS-RT VehiclePositions snapshot.

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
    poll_ts = int(dt.datetime.now(dt.UTC).timestamp())
    feed = _load_feed(url, session)
    if feed is None:
        return PollResult(
            records=[],
            fetch_ok=False,
            poll_ts=poll_ts,
            entities_total=0,
            vehicles_with_position=0,
            dropped_out_of_bbox=0,
        )

    vehicles = [
        entity.vehicle for entity in feed.entity if entity.HasField('vehicle')
    ]
    positioned = [veh for veh in vehicles if veh.HasField('position')]
    inside = [
        veh
        for veh in positioned
        if is_in_network_bbox(
            veh.position.latitude,
            veh.position.longitude,
            bbox,
        )
    ]
    return PollResult(
        records=[vehicle_record(veh, poll_ts) for veh in inside],
        fetch_ok=True,
        poll_ts=poll_ts,
        entities_total=len(vehicles),
        vehicles_with_position=len(positioned),
        dropped_out_of_bbox=len(positioned) - len(inside),
    )
