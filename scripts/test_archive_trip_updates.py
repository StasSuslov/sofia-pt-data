import datetime as dt
import functools
import json
import sys
from pathlib import Path

import pytest
import requests
from google.transit import gtfs_realtime_pb2

sys.path.insert(0, str(Path(__file__).parent))

from archive_trip_updates import archive_trip_updates

T0 = dt.datetime.fromisoformat('2026-10-18T12:00:15+03:00')
SAVED = 'tu_2026-10-18T090015Z.pb'


def _feed() -> bytes:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = '2.0'
    feed.header.timestamp = int(T0.timestamp())
    feed.entity.add(id='1').trip_update.trip.trip_id = 'A1'
    return feed.SerializeToString()


def _raise(error: Exception) -> bytes:
    raise error


def _incoming(directory: Path) -> list[str]:
    return sorted(path.name for path in (directory / 'incoming').glob('*'))


def _runs(directory: Path) -> list[dict]:
    log = directory / 'runs.ndjson'
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def test_feed_is_saved_as_served_under_utc_name(tmp_path):
    """A feed is saved byte for byte, named by its UTC fetch time."""
    body = _feed()
    assert archive_trip_updates(tmp_path, lambda: body, T0) == 0
    assert _incoming(tmp_path) == [SAVED]
    assert (tmp_path / 'incoming' / SAVED).read_bytes() == body
    assert _runs(tmp_path) == []


@pytest.mark.parametrize(
    'body',
    [b'', b'<html><body>502 Bad Gateway</body></html>'],
)
def test_body_that_is_not_a_feed_is_refused(tmp_path, body):
    """An empty body or an HTML error page saves nothing, logs the poll."""
    assert archive_trip_updates(tmp_path, lambda: body, T0) == 1
    assert _incoming(tmp_path) == []
    polls = [(run['ts'], run['stage']) for run in _runs(tmp_path)]
    assert polls == [(int(T0.timestamp()), 'poll')]


@pytest.mark.parametrize(
    'refusal',
    [
        requests.ConnectionError('portal down'),
        requests.Timeout('read timed out'),
        requests.HTTPError('429 Client Error: Too Many Requests'),
    ],
)
def test_failed_request_saves_nothing(tmp_path, refusal):
    """No connection, a timeout or an error status: a poll line, exit 1."""
    fetch_body = functools.partial(_raise, refusal)
    assert archive_trip_updates(tmp_path, fetch_body, T0) == 1
    assert _incoming(tmp_path) == []
    assert [run['error'] for run in _runs(tmp_path)] == [
        f'fetch failed: {refusal}',
    ]
