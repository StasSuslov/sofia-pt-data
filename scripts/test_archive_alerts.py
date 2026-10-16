import datetime as dt
import gzip
import json
import sys
from pathlib import Path

from google.transit import gtfs_realtime_pb2

sys.path.insert(0, str(Path(__file__).parent))

from archive_alerts import archive_alerts

T0 = dt.datetime.fromisoformat('2026-10-10T13:00:00+00:00')
FIVE_MIN = dt.timedelta(minutes=5)
TEN_MIN = dt.timedelta(minutes=10)
HOUR = dt.timedelta(hours=1)
HOUR_FIVE = dt.timedelta(hours=1, minutes=5)


def _alerts_feed(alerts: list[str], *, stamp: int = 1) -> bytes:
    """One alert per string, its text and route both taken from the string.

    Entity ids and header.timestamp follow `stamp`, the way ЦГМ hands out a
    fresh UUID and timestamp on every response.
    """
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    feed.header.timestamp = stamp
    for index, text in enumerate(alerts):
        entity = feed.entity.add()
        entity.id = f'{stamp}-{index}'
        entity.alert.description_text.translation.add(text=text, language='bg')
        entity.alert.informed_entity.add(route_id=text)
    return feed.SerializeToString()


def _saved(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob('alerts_*.pb.gz'))


def _saved_count(directory: Path) -> int:
    return len(_saved(directory))


def _runs(directory: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (directory / 'runs.ndjson').read_text().splitlines()
    ]


def test_first_response_is_saved_gzipped_by_utc(tmp_path):
    """The first response is saved gzipped under its UTC capture time."""
    body = _alerts_feed(['A', 'B'])
    assert archive_alerts(tmp_path, body, T0) == 0

    assert _saved(tmp_path) == ['alerts_2026-10-10T130000Z.pb.gz']
    assert (
        gzip.decompress(
            (tmp_path / 'alerts_2026-10-10T130000Z.pb.gz').read_bytes(),
        )
        == body
    )
    last_run = _runs(tmp_path)[-1]
    assert last_run['saved'] == 'alerts_2026-10-10T130000Z.pb.gz'
    assert last_run['entities'] == 2


def test_same_alerts_under_new_ids_are_not_saved(tmp_path):
    """New ids, timestamp and order alone do not make a new save."""
    archive_alerts(tmp_path, _alerts_feed(['A', 'B'], stamp=1), T0)
    archive_alerts(
        tmp_path,
        _alerts_feed(['B', 'A'], stamp=2),
        T0 + FIVE_MIN,
    )

    first, second = _runs(tmp_path)
    assert _saved_count(tmp_path) == 1
    assert second['hash'] == first['hash']
    assert second['saved'] is None


def test_changed_alert_is_saved(tmp_path):
    """A changed alert is saved as a new file."""
    archive_alerts(tmp_path, _alerts_feed(['A', 'B']), T0)
    archive_alerts(
        tmp_path,
        _alerts_feed(['A', 'C']),
        T0 + FIVE_MIN,
    )

    assert _saved_count(tmp_path) == 2


def test_names_keep_order_across_autumn_dst(tmp_path):
    """File names sort in capture order when local time repeats an hour."""
    # 00:30Z and 01:30Z on 2026-10-25 are both 03:30 local in Sofia. With a
    # local name the second change would sort before the first, and every
    # run after it would compare against the wrong file and save again.
    dst_end = dt.datetime.fromisoformat('2026-10-25T00:30:00+00:00')
    archive_alerts(tmp_path, _alerts_feed(['A']), dst_end)
    archive_alerts(tmp_path, _alerts_feed(['B']), dst_end + HOUR)
    archive_alerts(
        tmp_path,
        _alerts_feed(['B']),
        dst_end + HOUR_FIVE,
    )

    assert _saved(tmp_path) == [
        'alerts_2026-10-25T003000Z.pb.gz',
        'alerts_2026-10-25T013000Z.pb.gz',
    ]


def test_no_alerts_saved_only_when_next_agrees(tmp_path):
    """An empty response is saved only once a second one confirms it."""
    archive_alerts(tmp_path, _alerts_feed(['A']), T0)
    archive_alerts(tmp_path, _alerts_feed([]), T0 + FIVE_MIN)
    assert _saved_count(tmp_path) == 1

    archive_alerts(tmp_path, _alerts_feed([]), T0 + TEN_MIN)
    assert _saved(tmp_path)[-1] == 'alerts_2026-10-10T131000Z.pb.gz'


def test_one_empty_response_leaves_no_file(tmp_path):
    """A single empty response between identical ones saves nothing."""
    archive_alerts(tmp_path, _alerts_feed(['A']), T0)
    archive_alerts(tmp_path, _alerts_feed([]), T0 + FIVE_MIN)
    archive_alerts(tmp_path, _alerts_feed(['A']), T0 + TEN_MIN)

    assert _saved_count(tmp_path) == 1
    assert [run['entities'] for run in _runs(tmp_path)] == [1, 0, 1]


def test_headerless_or_non_protobuf_is_refused(tmp_path):
    """Bodies without a header, or not protobuf, are refused and logged."""
    # b"" parses cleanly as a FeedMessage with zero entities; only the
    # missing header tells it from a feed that really has no alerts.
    archive_alerts(tmp_path, _alerts_feed(['A']), T0)
    for index, body in enumerate((b'', b'\xff\xff\xff\xff')):
        assert archive_alerts(tmp_path, body, T0 + FIVE_MIN * (index + 1)) == 1

    assert _saved_count(tmp_path) == 1
    assert [run['ok'] for run in _runs(tmp_path)] == [True, False, False]
