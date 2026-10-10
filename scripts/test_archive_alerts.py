import gzip
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from google.transit import gtfs_realtime_pb2

sys.path.insert(0, str(Path(__file__).parent))

from archive_alerts import archive_alerts

T0 = datetime(2026, 10, 10, 13, 0, tzinfo=timezone.utc)


def _alerts_feed(alerts: list[str], *, stamp: int = 1) -> bytes:
    """One alert per string, its text and route both taken from the string.
    Entity ids and header.timestamp follow `stamp`, the way ЦГМ hands out a
    fresh UUID and timestamp on every response."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    feed.header.timestamp = stamp
    for i, text in enumerate(alerts):
        entity = feed.entity.add()
        entity.id = f"{stamp}-{i}"
        entity.alert.description_text.translation.add(text=text, language="bg")
        entity.alert.informed_entity.add(route_id=text)
    return feed.SerializeToString()


def _saved(d: Path) -> list[str]:
    return sorted(p.name for p in d.glob("alerts_*.pb.gz"))


def _runs(d: Path) -> list[dict]:
    return [json.loads(line) for line in (d / "runs.ndjson").read_text().splitlines()]


def test_first_response_is_saved_gzipped_under_its_utc_time(tmp_path):
    content = _alerts_feed(["A", "B"])
    assert archive_alerts(tmp_path, content, T0) == 0

    assert _saved(tmp_path) == ["alerts_2026-10-10T130000Z.pb.gz"]
    assert gzip.decompress((tmp_path / "alerts_2026-10-10T130000Z.pb.gz").read_bytes()) == content
    assert _runs(tmp_path)[-1]["saved"] == "alerts_2026-10-10T130000Z.pb.gz"
    assert _runs(tmp_path)[-1]["entities"] == 2


def test_same_alerts_under_new_ids_timestamp_and_order_are_not_saved(tmp_path):
    archive_alerts(tmp_path, _alerts_feed(["A", "B"], stamp=1), T0)
    archive_alerts(tmp_path, _alerts_feed(["B", "A"], stamp=2), T0 + timedelta(minutes=5))

    first, second = _runs(tmp_path)
    assert len(_saved(tmp_path)) == 1
    assert second["hash"] == first["hash"]
    assert second["saved"] is None


def test_changed_alert_is_saved(tmp_path):
    archive_alerts(tmp_path, _alerts_feed(["A", "B"]), T0)
    archive_alerts(tmp_path, _alerts_feed(["A", "C"]), T0 + timedelta(minutes=5))

    assert len(_saved(tmp_path)) == 2


def test_names_keep_capture_order_across_the_autumn_dst_change(tmp_path):
    # 00:30Z and 01:30Z on 2026-10-25 are both 03:30 local in Sofia. With a
    # local name the second change would sort before the first, and every
    # run after it would compare against the wrong file and save again.
    dst_end = datetime(2026, 10, 25, 0, 30, tzinfo=timezone.utc)
    archive_alerts(tmp_path, _alerts_feed(["A"]), dst_end)
    archive_alerts(tmp_path, _alerts_feed(["B"]), dst_end + timedelta(hours=1))
    archive_alerts(tmp_path, _alerts_feed(["B"]), dst_end + timedelta(hours=1, minutes=5))

    assert _saved(tmp_path) == ["alerts_2026-10-25T003000Z.pb.gz", "alerts_2026-10-25T013000Z.pb.gz"]


def test_no_alerts_is_saved_only_when_the_next_run_agrees(tmp_path):
    archive_alerts(tmp_path, _alerts_feed(["A"]), T0)
    archive_alerts(tmp_path, _alerts_feed([]), T0 + timedelta(minutes=5))
    assert len(_saved(tmp_path)) == 1

    archive_alerts(tmp_path, _alerts_feed([]), T0 + timedelta(minutes=10))
    assert _saved(tmp_path)[-1] == "alerts_2026-10-10T131000Z.pb.gz"


def test_one_empty_response_between_identical_ones_leaves_no_file(tmp_path):
    archive_alerts(tmp_path, _alerts_feed(["A"]), T0)
    archive_alerts(tmp_path, _alerts_feed([]), T0 + timedelta(minutes=5))
    archive_alerts(tmp_path, _alerts_feed(["A"]), T0 + timedelta(minutes=10))

    assert len(_saved(tmp_path)) == 1
    assert [run["entities"] for run in _runs(tmp_path)] == [1, 0, 1]


def test_body_without_a_header_or_not_protobuf_is_refused(tmp_path):
    # b"" parses cleanly as a FeedMessage with zero entities; only the
    # missing header tells it from a feed that really has no alerts.
    archive_alerts(tmp_path, _alerts_feed(["A"]), T0)
    for i, body in enumerate((b"", b"\xff\xff\xff\xff")):
        assert archive_alerts(tmp_path, body, T0 + timedelta(minutes=5 * (i + 1))) == 1

    assert len(_saved(tmp_path)) == 1
    assert [run["ok"] for run in _runs(tmp_path)] == [True, False, False]
