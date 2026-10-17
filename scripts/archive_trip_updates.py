"""Scheduled capture of the GTFS-RT trip updates feed.

Saves every response as served under data/<city>/tripupdates/incoming/,
one file a run, named tu_<YYYY-MM-DD>T<HHMMSS>Z.pb by the UTC time the
request started. scripts/pack_trip_updates.py folds each closed UTC hour
into one tar.xz.

No content check, unlike archive_alerts.py: ЦГМ builds the feed on every
request (header.timestamp is the second of the request), so no two
responses match and there is no duplicate to skip. What gets refused (exit
1, nothing saved, one line in runs.ndjson with stage "poll"):
- a failed request: no connection, a timeout, an HTTP error status;
- a body that is not a FeedMessage with a header. An empty body parses as
  a FeedMessage with no header, see archive_alerts.py.

A good run writes no log line: the snapshot is the record of it. The file
goes through <name>.tmp and fsync before it takes its name, so a power cut
cannot leave an empty tu_*.pb behind.

Runs from the collector's venv every 45 s, see
deploy/sofia-tripupdates-archive.timer.

Usage:
    python scripts/archive_trip_updates.py data/sofia/tripupdates
"""

import datetime as dt
import functools
import sys
from collections.abc import Callable
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from alertsarchive.cli import build_parser
from alertsarchive.feed import FeedRejectedError, parse_feed
from alertsarchive.fetch import fetch
from alertsarchive.runlog import log_run
from tripupdatesarchive.snapshot import save_snapshot

from config import CityProfileError, city_slugs, load_city_for_path


def _failed(output_dir: Path, now: dt.datetime, error: str) -> int:
    log_run(output_dir, now, stage='poll', error=error)
    sys.stderr.write(f'Trip updates not saved: {error}\n')
    return 1


def archive_trip_updates(
    output_dir: Path,
    fetch_body: Callable[[], bytes],
    now: dt.datetime,
) -> int:
    """Fetch one response, check that it is a feed, save it as served.

    Split out from main() so tests hand in a fetch of their own, no network
    involved.

    Args:
        output_dir: The trip updates directory, incoming/ inside it.
        fetch_body: Performs the request and returns the response body.
        now: When the request started.

    Returns:
        The exit code.
    """
    try:
        body = fetch_body()
    except requests.RequestException as fetch_error:
        return _failed(output_dir, now, f'fetch failed: {fetch_error}')
    try:
        parse_feed(body)
    except FeedRejectedError as rejected:
        return _failed(output_dir, now, str(rejected))
    save_snapshot(output_dir, body, now)
    return 0


def _feed_target(parser, args) -> tuple[str, str]:
    try:
        city = load_city_for_path(args.output_dir, slug=args.city)
    except CityProfileError as profile_error:
        parser.error(str(profile_error))

    # trip_updates is optional in a profile (config.REQUIRED_FEEDS), so a
    # city without one is a configuration to name, not a traceback every 45 s.
    url = args.url or city['feeds'].get('trip_updates')
    if not url:
        parser.error(
            f'cities/{city["slug"]}.json has no feeds.trip_updates; '
            'add it or pass --url',
        )
    return url, city['user_agent']


def main() -> None:
    """Fetch the trip updates feed once and save the response."""
    parser = build_parser(__doc__, city_slugs())
    args = parser.parse_args()
    url, user_agent = _feed_target(parser, args)
    sys.exit(
        archive_trip_updates(
            args.output_dir,
            functools.partial(fetch, url, user_agent, args.timeout),
            dt.datetime.now(dt.UTC),
        ),
    )


if __name__ == '__main__':
    main()
