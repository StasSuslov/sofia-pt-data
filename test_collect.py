from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import requests
from google.transit import gtfs_realtime_pb2

from collect import (
    dated_output_path,
    fetch_vehicle_positions,
    heartbeat_path_for,
    is_valid_sofia_coordinate,
)

SOFIA_TZ = ZoneInfo("Europe/Sofia")


def test_valid_sofia_coordinate_inside_bbox():
    assert is_valid_sofia_coordinate(42.6977, 23.3219)  # central Sofia


def test_valid_sofia_coordinate_peripheral_settlement():
    # Zhelyava (lon 23.605) — real ЦГМ-served village, sat outside the old,
    # too-narrow bbox (lon_max 23.55) and was silently discarded as if it
    # were a teleportation artifact. Must be valid under the corrected bbox.
    assert is_valid_sofia_coordinate(42.745, 23.605)


def test_invalid_coordinate_outside_bbox():
    assert not is_valid_sofia_coordinate(43.2141, 27.9147)  # Varna — known teleportation case


def test_invalid_coordinate_just_outside_each_edge():
    assert not is_valid_sofia_coordinate(42.44, 23.30)   # below lat_min
    assert not is_valid_sofia_coordinate(42.91, 23.30)   # above lat_max
    assert not is_valid_sofia_coordinate(42.65, 23.02)   # below lon_min
    assert not is_valid_sofia_coordinate(42.65, 23.67)   # above lon_max


def test_dated_output_path_same_day():
    out_dir = Path("data/sofia")
    when = datetime(2026, 8, 27, 10, 0, tzinfo=SOFIA_TZ)
    assert dated_output_path(out_dir, SOFIA_TZ, when) == out_dir / "2026-08-27.jsonl"


def test_dated_output_path_rotates_at_local_midnight():
    out_dir = Path("data/sofia")
    before_midnight = datetime(2026, 8, 27, 23, 59, tzinfo=SOFIA_TZ)
    after_midnight = datetime(2026, 8, 28, 0, 1, tzinfo=SOFIA_TZ)
    assert dated_output_path(out_dir, SOFIA_TZ, before_midnight) == out_dir / "2026-08-27.jsonl"
    assert dated_output_path(out_dir, SOFIA_TZ, after_midnight) == out_dir / "2026-08-28.jsonl"


def test_dated_output_path_uses_local_not_utc_date():
    # 23:30 UTC on Aug 27 is already Aug 28 in Europe/Sofia (UTC+2 in winter, +3 in summer)
    out_dir = Path("data/sofia")
    utc_late = datetime(2026, 8, 27, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert dated_output_path(out_dir, SOFIA_TZ, utc_late) == out_dir / "2026-08-28.jsonl"


def test_heartbeat_path_sits_next_to_data_file():
    data_path = Path("data/sofia/2026-08-27.jsonl")
    assert heartbeat_path_for(data_path) == Path("data/sofia/2026-08-27.polls.jsonl")


def _build_feed(vehicles: list[tuple[bool, float, float]]) -> bytes:
    """vehicles: list of (has_position, lat, lon)."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for i, (has_position, lat, lon) in enumerate(vehicles):
        entity = feed.entity.add()
        entity.id = f"e{i}"
        entity.vehicle.vehicle.id = f"v{i}"
        entity.vehicle.trip.route_id = "R1"
        if has_position:
            entity.vehicle.position.latitude = lat
            entity.vehicle.position.longitude = lon
    return feed.SerializeToString()


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        pass


class _FakeSession:
    def __init__(self, content: bytes | None = None, exc: Exception | None = None):
        self._content = content
        self._exc = exc

    def get(self, url, timeout):
        if self._exc:
            raise self._exc
        return _FakeResponse(self._content)


def test_fetch_vehicle_positions_partitions_by_bbox_stage():
    content = _build_feed([
        (True, 42.70, 23.32),    # in bbox
        (True, 43.2141, 27.9147),  # has position, but out of bbox (Varna)
        (False, 0, 0),           # vehicle entity with no position at all
    ])
    poll = fetch_vehicle_positions("http://example.test", _FakeSession(content=content))

    assert poll.fetch_ok is True
    assert poll.entities_total == 3
    assert poll.vehicles_with_position == 2
    assert poll.dropped_out_of_bbox == 1
    assert len(poll.records) == 1
    assert poll.records[0]["lat"] == pytest.approx(42.7, abs=1e-4)


def test_fetch_vehicle_positions_records_share_one_poll_timestamp():
    content = _build_feed([(True, 42.70, 23.32), (True, 42.71, 23.33)])
    poll = fetch_vehicle_positions("http://example.test", _FakeSession(content=content))
    assert {r["snapshot_ts"] for r in poll.records} == {poll.poll_ts}


def test_fetch_vehicle_positions_reports_fetch_failure():
    poll = fetch_vehicle_positions(
        "http://example.test", _FakeSession(exc=requests.RequestException("boom"))
    )
    assert poll.fetch_ok is False
    assert poll.records == []
    assert (poll.entities_total, poll.vehicles_with_position, poll.dropped_out_of_bbox) == (0, 0, 0)


def test_fetch_vehicle_positions_reports_parse_failure():
    poll = fetch_vehicle_positions("http://example.test", _FakeSession(content=b"\xff\xff\xff\xff"))
    assert poll.fetch_ok is False
    assert poll.records == []
