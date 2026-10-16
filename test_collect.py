import datetime as dt
import os
import signal
import time
import types
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from google.transit import gtfs_realtime_pb2

from collect import (
    PollResult,
    dated_output_path,
    fetch_vehicle_positions,
    heartbeat_path_for,
    is_in_network_bbox,
    ping_healthcheck,
    run_collection,
)
from config import load_city

SOFIA_TZ = ZoneInfo('Europe/Sofia')

# The bbox cases below read their bounds from the profile the collector
# actually runs on, so they check the deployed configuration and not a copy
# of it: a bbox edited back to the too-narrow 2026-08-28 values would fail
# the Zhelyava case here rather than silently discard the village again.
SOFIA = load_city('sofia')
BBOX = SOFIA['bbox']

CENTRAL_SOFIA = (42.6977, 23.3219)
ZHELYAVA = (42.745, 23.605)
VARNA = (43.2141, 27.9147)
LAT_BELOW, LAT_ABOVE, LAT_MID = 42.44, 42.91, 42.65
LON_BELOW, LON_ABOVE, LON_MID = 23.02, 23.67, 23.3
OUTSIDE_EDGES = (
    pytest.param(LAT_BELOW, LON_MID, id='below lat_min'),
    pytest.param(LAT_ABOVE, LON_MID, id='above lat_max'),
    pytest.param(LAT_MID, LON_BELOW, id='below lon_min'),
    pytest.param(LAT_MID, LON_ABOVE, id='above lon_max'),
)
INSIDE_LAT, INSIDE_LON = 42.7, 23.32
LAT_TOLERANCE = 1e-4
URL = 'http://example.test'
POLL_INTERVAL_SEC = 30
SIGTERM_DEADLINE_SEC = 5
UNKNOWN_ENUM_VALUE = 99


def _local(stamp: str) -> dt.datetime:
    """Wall-clock time `stamp` in Europe/Sofia."""
    return dt.datetime.fromisoformat(stamp).replace(tzinfo=SOFIA_TZ)


def test_coordinate_inside_bbox():
    """Central Sofia is inside the bbox."""
    assert is_in_network_bbox(*CENTRAL_SOFIA, BBOX)


def test_coordinate_in_peripheral_settlement():
    """Zhelyava (lon 23.605) is inside the corrected bbox.

    A real ЦГМ-served village, it sat outside the old, too-narrow bbox
    (lon_max 23.55) and was silently discarded as if it were a teleportation
    artifact. Must be valid under the corrected bbox.
    """
    assert is_in_network_bbox(*ZHELYAVA, BBOX)


def test_invalid_coordinate_outside_bbox():
    """Varna is a known teleportation case."""
    assert not is_in_network_bbox(*VARNA, BBOX)


@pytest.mark.parametrize(('lat', 'lon'), OUTSIDE_EDGES)
def test_coordinate_just_outside_edge(lat, lon):
    """A point just past any one edge of the bbox is outside it."""
    assert not is_in_network_bbox(lat, lon, BBOX)


def test_dated_output_path_same_day():
    """A time within the day maps to that day's file."""
    out_dir = Path('data/sofia')
    assert (
        dated_output_path(out_dir, SOFIA_TZ, _local('2026-08-27T10:00'))
        == out_dir / '2026-08-27.jsonl'
    )


def test_dated_path_rotates_at_midnight():
    """The file name changes at local midnight."""
    out_dir = Path('data/sofia')
    before_midnight = _local('2026-08-27T23:59')
    after_midnight = _local('2026-08-28T00:01')
    assert (
        dated_output_path(out_dir, SOFIA_TZ, before_midnight)
        == out_dir / '2026-08-27.jsonl'
    )
    assert (
        dated_output_path(out_dir, SOFIA_TZ, after_midnight)
        == out_dir / '2026-08-28.jsonl'
    )


def test_dated_path_uses_local_date():
    """The date is the local one, not the UTC one."""
    # 23:30 UTC on Aug 27 is already Aug 28 in Europe/Sofia
    # (winter offset is two hours, summer offset is three)
    out_dir = Path('data/sofia')
    utc_late = dt.datetime.fromisoformat('2026-08-27T23:30').replace(
        tzinfo=ZoneInfo('UTC'),
    )
    assert (
        dated_output_path(out_dir, SOFIA_TZ, utc_late)
        == out_dir / '2026-08-28.jsonl'
    )


def test_heartbeat_path_sits_next_to_data_file():
    """The heartbeat log shares the data file's directory and stem."""
    data_path = Path('data/sofia/2026-08-27.jsonl')
    assert heartbeat_path_for(data_path) == Path(
        'data/sofia/2026-08-27.polls.jsonl',
    )


def _add_vehicle(
    feed: gtfs_realtime_pb2.FeedMessage,
    index: int,
    spec: tuple[bool, float, float],
) -> None:
    has_position, lat, lon = spec
    entity = feed.entity.add()
    entity.id = f'e{index}'
    entity.vehicle.vehicle.id = f'v{index}'
    entity.vehicle.trip.route_id = 'R1'
    if has_position:
        entity.vehicle.position.latitude = lat
        entity.vehicle.position.longitude = lon


def _build_feed(vehicles: list[tuple[bool, float, float]]) -> bytes:
    """Serialise vehicles, a list of (has_position, lat, lon)."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    for index, spec in enumerate(vehicles):
        _add_vehicle(feed, index, spec)
    return feed.SerializeToString()


def _fake_response(payload: bytes) -> types.SimpleNamespace:
    return types.SimpleNamespace(content=payload, raise_for_status=lambda: None)


class _FakeSession:
    def __init__(
        self,
        payload: bytes | None = None,
        error: Exception | None = None,
    ):
        self._payload = payload
        self._error = error

    def get(self, url, timeout):
        if self._error:
            raise self._error
        return _fake_response(self._payload)


def _fetch(payload: bytes) -> PollResult:
    return fetch_vehicle_positions(URL, _FakeSession(payload=payload), BBOX)


def test_fetch_partitions_by_bbox_stage():
    """The three counts split the feed at each filtering stage."""
    payload = _build_feed([
        (True, INSIDE_LAT, INSIDE_LON),  # in bbox
        (True, *VARNA),  # has position, but out of bbox (Varna)
        (False, 0, 0),  # vehicle entity with no position at all
    ])
    poll = _fetch(payload)

    assert poll.fetch_ok is True
    assert (
        poll.entities_total,
        poll.vehicles_with_position,
        poll.dropped_out_of_bbox,
    ) == (3, 2, 1)
    assert len(poll.records) == 1
    assert poll.records[0]['lat'] == pytest.approx(
        INSIDE_LAT,
        abs=LAT_TOLERANCE,
    )


def test_fetch_records_share_one_poll_timestamp():
    """Every record carries the poll's own timestamp."""
    poll = _fetch(
        _build_feed([
            (True, INSIDE_LAT, INSIDE_LON),
            (True, 42.71, 23.33),
        ]),
    )
    assert {rec['snapshot_ts'] for rec in poll.records} == {poll.poll_ts}


def test_fetch_reports_fetch_failure():
    """A failed request gives an empty, not-ok poll."""
    poll = fetch_vehicle_positions(
        URL,
        _FakeSession(error=requests.RequestException('boom')),
        BBOX,
    )
    assert poll.fetch_ok is False
    assert poll.records == []
    assert (
        poll.entities_total,
        poll.vehicles_with_position,
        poll.dropped_out_of_bbox,
    ) == (0, 0, 0)


def test_fetch_reports_parse_failure():
    """An unparseable body gives an empty, not-ok poll."""
    poll = _fetch(b'\xff\xff\xff\xff')
    assert poll.fetch_ok is False
    assert poll.records == []


_NEW_FIELDS = (
    'stop_id',
    'current_status',
    'occupancy_status',
    'congestion_level',
    'schedule_relationship',
)


def _feed_of(vehicle: gtfs_realtime_pb2.VehiclePosition) -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    entity = feed.entity.add()
    entity.id = 'e0'
    entity.vehicle.CopyFrom(vehicle)
    return feed.SerializeToString()


def _positioned_vehicle() -> gtfs_realtime_pb2.VehiclePosition:
    vehicle = gtfs_realtime_pb2.VehiclePosition()
    vehicle.position.latitude = INSIDE_LAT
    vehicle.position.longitude = INSIDE_LON
    return vehicle


def _only_record(payload: bytes) -> dict:
    poll = _fetch(payload)
    assert len(poll.records) == 1
    return poll.records[0]


def test_fetch_writes_feed_enums_by_name():
    """Enum fields reach the record by name."""
    vehicle = _positioned_vehicle()
    vehicle.trip.trip_id = 'T1'
    vehicle.trip.schedule_relationship = gtfs_realtime_pb2.TripDescriptor.ADDED
    vehicle.stop_id = 'A0123'
    vehicle.current_status = gtfs_realtime_pb2.VehiclePosition.STOPPED_AT
    vehicle.occupancy_status = gtfs_realtime_pb2.VehiclePosition.FULL
    vehicle.congestion_level = gtfs_realtime_pb2.VehiclePosition.STOP_AND_GO

    record = _only_record(_feed_of(vehicle))
    assert {key: record[key] for key in _NEW_FIELDS} == {
        'stop_id': 'A0123',
        'current_status': 'STOPPED_AT',
        'occupancy_status': 'FULL',
        'congestion_level': 'STOP_AND_GO',
        'schedule_relationship': 'ADDED',
    }


def test_fetch_writes_null_for_absent_fields():
    """Fields the feed left out are null, not the proto2 default.

    The trap: proto2 answers an unset enum with its default, and the
    defaults IN_TRANSIT_TO and SCHEDULED are exactly what ЦГМ sends live.
    Code that skips HasField would pass a test built from live-like
    values and still invent those two for every vehicle that never had them.
    """
    vehicle = _positioned_vehicle()
    vehicle.trip.trip_id = 'T1'  # trip present, schedule_relationship unset
    record = _only_record(_feed_of(vehicle))
    assert {key: record[key] for key in _NEW_FIELDS} == dict.fromkeys(
        _NEW_FIELDS,
    )

    no_trip = _only_record(_feed_of(_positioned_vehicle()))
    assert no_trip['schedule_relationship'] is None


def test_fetch_writes_null_for_unknown_enum_value():
    """An enum value the bindings do not know is written as null.

    A value newer than gtfs-realtime-bindings goes to unknown fields on a
    closed proto2 enum, so the record can't tell it from an absent field.
    METHODOLOGY.md says null means either; this pins that it stays null
    rather than raising out of Enum.Name() and losing the whole poll.
    """
    position_cls = gtfs_realtime_pb2.VehiclePosition
    field = position_cls.DESCRIPTOR.fields_by_name['occupancy_status']
    wire_tag = field.number << 3
    raw = _positioned_vehicle().SerializeToString() + bytes([
        wire_tag,
        UNKNOWN_ENUM_VALUE,
    ])
    vehicle = position_cls()
    vehicle.ParseFromString(raw)

    assert _only_record(_feed_of(vehicle))['occupancy_status'] is None


def test_ping_healthcheck_calls_get(monkeypatch):
    """The ping is one GET with a 5 second timeout."""
    calls = []
    monkeypatch.setattr(
        requests,
        'get',
        lambda url, timeout: calls.append((url, timeout)),
    )

    ping_healthcheck('https://hc-ping.com/abc')
    assert calls == [('https://hc-ping.com/abc', 5)]


def _raise_request_error(url, timeout):
    raise requests.RequestException('network unreachable')


def _raise_value_error(url, timeout):
    raise ValueError('something unrelated to networking went wrong')


def test_ping_healthcheck_swallows_request_errors(monkeypatch):
    """A failed ping never raises."""
    monkeypatch.setattr(requests, 'get', _raise_request_error)
    # must not raise: a monitoring hiccup can't be allowed to kill the
    # collector
    ping_healthcheck('https://hc-ping.com/abc')


def test_ping_swallows_non_request_errors_too(monkeypatch):
    """Not even a non-network error escapes the ping.

    The docstring's guarantee is "never raises", not "never raises for
    RequestException" — a bug in the ping path itself must not escape either.
    """
    monkeypatch.setattr(requests, 'get', _raise_value_error)
    ping_healthcheck('https://hc-ping.com/abc')


def _fetch_then_sigterm(url, session, bbox):
    os.kill(os.getpid(), signal.SIGTERM)
    return PollResult(
        [],
        fetch_ok=True,
        poll_ts=0,
        entities_total=0,
        vehicles_with_position=0,
        dropped_out_of_bbox=0,
    )


@pytest.fixture
def restored_signals():
    """Put the SIGINT and SIGTERM handlers back after the test."""
    previous = signal.getsignal(signal.SIGINT), signal.getsignal(signal.SIGTERM)
    yield
    signal.signal(signal.SIGINT, previous[0])
    signal.signal(signal.SIGTERM, previous[1])


@pytest.mark.usefixtures('restored_signals')
def test_run_collection_returns_on_sigterm(tmp_path, monkeypatch):
    """run_collection() returns at once on SIGTERM.

    The handler announces "finishing current snapshot and closing file", so
    the process must actually be finishing. A plain time.sleep() resumes
    after the handler returns (PEP 475), which would hold the collector for
    the rest of the interval — 45 s on the deployed cadence, on every
    systemctl stop. Measured by the clock, because the flag was set the whole
    time this was broken.
    """
    monkeypatch.setattr(
        'collect.fetch_vehicle_positions',
        _fetch_then_sigterm,
    )
    started = time.monotonic()
    run_collection(
        POLL_INTERVAL_SEC,
        0,
        URL,
        bbox=BBOX,
        user_agent=SOFIA['user_agent'],
        tz_name=SOFIA['timezone'],
        output_dir=tmp_path,
    )

    assert time.monotonic() - started < SIGTERM_DEADLINE_SEC
