"""Scheduled capture of the GTFS-RT service alerts feed.

Saves the response under data/<city>/alerts/ only when the alerts themselves
changed.

What "changed" means here: ЦГМ gives every entity a fresh random UUID on
every response (17 of 17 changed between two requests 20 s apart on
2026-10-10), and header.timestamp moves with each request too, so a hash
over the response bytes would change on every run and save a 128 KB
duplicate every five minutes. alerts_digest() hashes each entity's `alert`
message on its own, without entity.id, sorts those per-alert digests and
hashes them joined by newlines, the same shape as _digest_of() in
archive_static_feed.py. A reordered response is the same content; a changed
text, active period, cause, effect or informed entity is not.

What gets refused (exit 1, nothing saved):
- a failed request, or a body that doesn't parse;
- a body with no FeedMessage header. An empty body parses without error as
  a FeedMessage with zero entities and no header, and the vehicle-positions
  feed has answered 200 with zero entities 7 times in 82,503 polls. Saving
  that would put "every alert ended, and came back five minutes later" into
  the archive, indistinguishable from the real thing.

A response that parses with a header but carries no alerts is saved only
when the run before it saw no alerts either: one empty answer is a hiccup
until a second one confirms it.

Every run, saved or not, failed or not, appends a line to runs.ndjson in the
same directory. Without it a stretch with no new file reads the same as a
stretch with no runs at all, and "alert X ended between t1 and t2" needs the
first. The name stays clear of sofia-compress.service's *.jsonl mask, which
would otherwise gzip the log out from under the next append.

Saved files are named alerts_<YYYY-MM-DD>T<HHMMSS>Z.pb.gz in UTC, not the
city's local time: the local clock repeats an hour when DST ends (00:30Z and
01:30Z on 2026-10-25 are both 03:30 in Sofia), and the newest file is picked
by name. Inside is the response exactly as served, gzipped. Nothing prunes
or compresses these: one change costs about 19 KB.

Runs from the collector's venv, unlike archive_static_feed.py on system
python3, because parsing needs protobuf (gtfs-realtime-bindings).

Usage:
    python scripts/archive_alerts.py data/sofia/alerts
"""

import datetime as dt
import gzip
import sys
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent))
from alertsarchive.cli import build_parser
from alertsarchive.feed import (
    FeedRejectedError,
    alerts_digest,
    message,
    parse_feed,
)
from alertsarchive.fetch import fetch
from alertsarchive.files import (
    latest_saved,
    save_response,
)
from alertsarchive.runlog import last_entities, log_run

from config import CityProfileError, city_slugs, load_city_for_path


def _previous_digest(previous: Path | None) -> str | None:
    if not previous:
        return None
    feed = parse_feed(gzip.decompress(previous.read_bytes()))
    return alerts_digest(feed)[0]


def _skip_message(output_dir: Path, digest: str, count: int) -> str | None:
    """Return why a parsed response is not saved, or None if it is new."""
    previous = latest_saved(output_dir)
    if digest == _previous_digest(previous):
        return message('No change', count, previous.name, digest)
    if count == 0 and last_entities(output_dir) != 0:
        return 'No alerts in the response; saving only if the next run agrees.'
    return None


def _save_if_new(output_dir: Path, body: bytes, now, feed):
    """Print the verdict on a parsed response and save it if it is new."""
    digest, count = alerts_digest(feed)
    skipped = _skip_message(output_dir, digest, count)
    if skipped:
        sys.stdout.write(f'{skipped}\n')
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    saved = save_response(output_dir, body, now)
    line = message('Saved', count, saved.name, digest)
    sys.stdout.write(f'{line}\n')
    return saved


def archive_alerts(
    output_dir: Path,
    body: bytes,
    now: dt.datetime | None = None,
) -> int:
    """Decide whether one fetched response is new content and log the run.

    Saves the response if it is new, and logs the run either way. Split out
    from main() so tests drive it with bytes they built, no network involved.

    Args:
        output_dir: Directory for saved responses and runs.ndjson.
        body: The response bytes as served.
        now: Capture time, default the current time.

    Returns:
        The exit code.
    """
    now = now or dt.datetime.now(dt.UTC)
    try:
        feed = parse_feed(body)
    except FeedRejectedError as rejected:
        log_run(output_dir, now, ok=False, error=str(rejected))
        sys.stderr.write(f'Rejected response: {rejected}\n')
        return 1

    saved = _save_if_new(output_dir, body, now, feed)
    digest, count = alerts_digest(feed)
    # After the file, as collect.py writes data before its heartbeat line: the
    # log never names a file that didn't land.
    log_run(
        output_dir,
        now,
        ok=True,
        entities=count,
        hash=digest,
        saved=saved.name if saved else None,
    )
    return 0


def _alerts_url(parser, args) -> tuple[str, str]:
    try:
        city = load_city_for_path(args.output_dir, slug=args.city)
    except CityProfileError as profile_error:
        parser.error(str(profile_error))

    # alerts is optional in a profile (config.REQUIRED_FEEDS), so a city
    # without one is a configuration to name, not a traceback every 5 minutes.
    url = args.url or city['feeds'].get('alerts')
    if not url:
        parser.error(
            f'cities/{city["slug"]}.json has no feeds.alerts; '
            'add it or pass --url',
        )
    return url, city['user_agent']


def _fetch_or_exit(args, target: tuple[str, str], now: dt.datetime) -> bytes:
    url, user_agent = target
    try:
        return fetch(url, user_agent, args.timeout)
    except requests.RequestException as fetch_error:
        message = f'fetch failed: {fetch_error}'
        log_run(args.output_dir, now, ok=False, error=message)
        sys.stderr.write(f'Fetch failed: {fetch_error}\n')
        sys.exit(1)


def main() -> None:
    """Fetch the alerts feed once and archive it."""
    parser = build_parser(__doc__, city_slugs())
    args = parser.parse_args()
    target = _alerts_url(parser, args)

    now = dt.datetime.now(dt.UTC)
    body = _fetch_or_exit(args, target, now)
    sys.exit(archive_alerts(args.output_dir, body, now))


if __name__ == '__main__':
    main()
