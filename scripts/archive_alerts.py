#!/usr/bin/env python3
"""
Scheduled capture of the GTFS-RT service alerts feed, saving the response
under data/<city>/alerts/ only when the alerts themselves changed.

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

import argparse
import gzip
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import CityProfileError, city_slugs, load_city_for_path

SAVED_NAME_RE = re.compile(r'^alerts_\d{4}-\d{2}-\d{2}T\d{6}Z\.pb\.gz$')
RUN_LOG = 'runs.ndjson'


class FeedRejected(ValueError):
    pass


def parse_feed(content: bytes):
    feed = gtfs_realtime_pb2.FeedMessage()
    try:
        feed.ParseFromString(content)
    except Exception as e:
        raise FeedRejected(f'not a GTFS-RT FeedMessage ({e})') from e
    if not feed.HasField('header'):
        raise FeedRejected('no FeedMessage header (empty or truncated body)')
    return feed


def alerts_digest(feed) -> tuple[str, int]:
    """(content hash, number of alerts). See the module docstring."""
    digests = sorted(
        hashlib.sha256(e.alert.SerializeToString()).hexdigest()
        for e in feed.entity
        if e.HasField('alert')
    )
    return hashlib.sha256('\n'.join(digests).encode('utf-8')).hexdigest(), len(
        digests,
    )


def latest_saved(output_dir: Path) -> Path | None:
    if not output_dir.exists():
        return None
    saved = [
        p
        for p in output_dir.iterdir()
        if p.is_file() and SAVED_NAME_RE.match(p.name)
    ]
    return max(saved, key=lambda p: p.name, default=None)


def last_run(output_dir: Path) -> dict | None:
    """The newest runs.ndjson entry, read off the file's tail."""
    try:
        with (output_dir / RUN_LOG).open('rb') as f:
            size = f.seek(0, os.SEEK_END)
            f.seek(max(0, size - 4096))
            lines = f.read().splitlines()
        return json.loads(lines[-1]) if lines else None
    except (FileNotFoundError, ValueError):
        return None


def log_run(output_dir: Path, now: datetime, **fields) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with (output_dir / RUN_LOG).open('a', encoding='utf-8') as f:
        f.write(json.dumps({'ts': int(now.timestamp()), **fields}) + '\n')


def save_response(output_dir: Path, content: bytes, now: datetime) -> Path:
    """
    Through a temp file and os.replace(): a truncated .pb.gz under a real
    name would become the newest file, fail to decompress on every later
    run, and stop the archive until someone deleted it by hand.
    """
    target = output_dir / f'alerts_{now.astimezone(UTC):%Y-%m-%dT%H%M%SZ}.pb.gz'
    fd, tmp_name = tempfile.mkstemp(
        prefix='.alerts_',
        suffix='.part',
        dir=output_dir,
    )
    try:
        with os.fdopen(fd, 'wb') as out:
            out.write(gzip.compress(content))
        Path(tmp_name).replace(target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise
    Path(target).chmod(0o644)  # mkstemp's 0600 survives os.replace
    return target


def archive_alerts(
    output_dir: Path,
    content: bytes,
    now: datetime | None = None,
) -> int:
    """
    Decide whether one fetched response is new content, save it if so, and
    log the run either way. Split out from main() so tests drive it with
    bytes they built, no network involved. Returns the exit code.
    """
    now = now or datetime.now(UTC)
    try:
        feed = parse_feed(content)
    except FeedRejected as e:
        log_run(output_dir, now, ok=False, error=str(e))
        print(f'Rejected response: {e}', file=sys.stderr)
        return 1

    digest, count = alerts_digest(feed)
    previous = latest_saved(output_dir)
    previous_digest = (
        alerts_digest(parse_feed(gzip.decompress(previous.read_bytes())))[0]
        if previous
        else None
    )
    saved = None
    if digest == previous_digest:
        print(
            f'No change: {count} alerts match {previous.name} ({digest[:12]}...).',
        )
    elif count == 0 and (last_run(output_dir) or {}).get('entities') != 0:
        print('No alerts in the response; saving only if the next run agrees.')
    else:
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = save_response(output_dir, content, now)
        print(f'Saved {saved.name}: {count} alerts ({digest[:12]}...).')

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


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'output_dir',
        type=Path,
        help='Directory for alerts_<UTC>.pb.gz files and runs.ndjson '
        '(e.g. data/sofia/alerts)',
    )
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help='City profile supplying the feed URL '
        f'(cities/<slug>.json; have: {", ".join(city_slugs()) or "none"}). '
        'Default: read from output_dir, laid out as .../data/<city>/alerts',
    )
    parser.add_argument(
        '--url',
        type=str,
        default=None,
        help="GTFS-RT alerts feed URL (default: the city profile's)",
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=30,
        help='Request timeout in seconds (default: 30)',
    )
    args = parser.parse_args()

    try:
        city = load_city_for_path(args.output_dir, slug=args.city)
    except CityProfileError as e:
        parser.error(str(e))

    # alerts is optional in a profile (config.REQUIRED_FEEDS), so a city
    # without one is a configuration to name, not a traceback every 5 minutes.
    url = args.url or city['feeds'].get('alerts')
    if not url:
        parser.error(
            f'cities/{city["slug"]}.json has no feeds.alerts; add it or pass --url',
        )

    now = datetime.now(UTC)
    try:
        response = requests.get(
            url,
            timeout=args.timeout,
            headers={'User-Agent': city['user_agent']},
        )
        response.raise_for_status()
    except requests.RequestException as e:
        log_run(args.output_dir, now, ok=False, error=f'fetch failed: {e}')
        print(f'Fetch failed: {e}', file=sys.stderr)
        sys.exit(1)

    sys.exit(archive_alerts(args.output_dir, response.content, now))


if __name__ == '__main__':
    main()
