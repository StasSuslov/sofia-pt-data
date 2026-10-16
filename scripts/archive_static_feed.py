r"""Scheduled capture of the GTFS Static feed.

Saves a new snapshot under data/sofia/static/ only when the feed's actual
contents changed.

Why this exists: scripts/segment_speeds.py and scripts/export_web.py both
match a processed day to the latest gtfs_<YYYY-MM-DD>.zip snapshot dated on
or before it (see find_static_snapshots()/pick_snapshot_for_day() in
segment_speeds.py). That matching is only as good as the set of snapshots
on disk, and until now every one of them was downloaded by hand. The agency
republished the feed on 2026-08-31 with no announcement; processing that
day against the previous snapshot rejected 7.013% of records as unmatched
to any known trip, against 0.017% for the correct one, and 32 shape_ids
kept their identifier while changing geometry underneath it. A republish
nobody notices silently degrades every day processed after it.

What "changed" means here: a zip stores a modification time per member, so
an agency that rebuilds its feed export nightly from unchanged source data
still produces a different file on disk every night, even though nothing a
rider or this pipeline cares about is different. Hashing the zip's own
bytes would treat that as a change and archive an 18.7 MB duplicate every
day forever. Instead this hashes the archive's *content*: every member
name paired with the SHA256 of that member's decompressed bytes, sorted by
name, then hashed together (see content_hash() below). This is the same
move commit a748492 made to the segment aggregation key (build_shape_key()
in scripts/segment_speeds.py): identify data by what it says, not by an
incidental encoding detail of how it happens to be packaged.

A second change inside one local day lands as gtfs_<YYYY-MM-DD>T<HHMM>.zip
next to that day's plain snapshot. Until 2026-09-03 it was detected and
thrown away, because the naming scheme held one snapshot per calendar day
and the archive ran once daily. The agency then republished at around 11:00
local on 2026-09-02, renumbering the trips of four routes; the day's own
snapshot had been taken three hours earlier, and 10,166 RT records (1.4% of
the day) matched no trip in it. Keeping only the morning capture makes the
afternoon of such a day unprocessable against anything but a guess.

Usage:
    python scripts/archive_static_feed.py data/sofia/static
    python scripts/archive_static_feed.py data/sofia/static \
        --url https://gtfs.sofiatraffic.bg/api/v1/static
"""

import datetime as dt
import sys
from pathlib import Path
from typing import NamedTuple
from urllib.error import URLError
from zoneinfo import ZoneInfo

# config.py at the repo root, not scripts/segment_speeds.py: this script has
# to run unattended on the collector host's bare system Python, with no
# venv (see deploy/sofia-static-archive.service), so it only imports from
# config.py, which is deliberately kept dependency-free for exactly this
# kind of reuse, and never from another scripts/*.py file that could grow a
# dependency later without anyone thinking of this script.
sys.path.insert(0, str(Path(__file__).parent.parent))
from staticarchive.cli import build_parser
from staticarchive.download import download_to_temp, validate_feed_zip
from staticarchive.hashing import (
    content_hash,
    digest_of,
    member_hashes,
    short_digest,
)
from staticarchive.snapshots import Candidate, latest_snapshot, save_snapshot

from config import CityProfileError, city_slugs, load_city_for_path


def archive_feed(
    output_dir: Path,
    tmp_path: Path,
    source_url: str,
    tz: ZoneInfo,
    now: dt.datetime | None = None,
) -> int:
    """Decide whether the downloaded candidate is new content and save it.

    The candidate at tmp_path is already downloaded and already validated.
    If it is new it is saved as today's snapshot (local date in `tz`,
    matching the timezone the rest of the pipeline rotates on) plus its
    manifest.

    Every branch consumes tmp_path: it's either renamed into place or
    deleted, never left behind. Split out from main() so tests can drive
    this decision logic against a zip they built directly, with no network
    involved (see scripts/test_archive_static_feed.py).

    Args:
        output_dir: The static directory.
        tmp_path: The candidate zip.
        source_url: Where the candidate came from.
        tz: Timezone for the snapshot's calendar date.
        now: Capture time, default the current time.

    Returns:
        The process exit code the caller should use.
    """
    now = now or dt.datetime.now(dt.UTC)
    pairs = member_hashes(tmp_path)
    new_hash = digest_of(pairs)

    existing = latest_snapshot(output_dir)
    if existing is not None and content_hash(existing[1]) == new_hash:
        tmp_path.unlink()
        sys.stdout.write(
            f'No change: content hash matches {existing[1].name} '
            f'({short_digest(new_hash)}). Discarding this download.\n',
        )
        return 0

    candidate = Candidate(tmp_path, source_url, new_hash, len(pairs))
    save_snapshot(
        output_dir,
        candidate,
        now.astimezone(tz),
        None if existing is None else existing[1].name,
    )
    return 0


class _Target(NamedTuple):
    url: str
    tz: ZoneInfo
    user_agent: str


def _resolve_target(parser, args) -> _Target:
    try:
        city = load_city_for_path(args.output_dir, slug=args.city)
    except CityProfileError as profile_error:
        parser.error(str(profile_error))

    return _Target(
        args.url or city['feeds']['static'],
        ZoneInfo(args.timezone or city['timezone']),
        city['user_agent'],
    )


def _download_valid(args, target: _Target) -> Path:
    sys.stdout.write(f'Downloading {target.url} ...\n')
    try:
        tmp_path = download_to_temp(
            target.url,
            args.output_dir,
            target.user_agent,
            timeout=args.timeout,
        )
    except (URLError, OSError) as download_error:
        sys.stderr.write(f'Download failed: {download_error}\n')
        sys.exit(1)

    try:
        validate_feed_zip(tmp_path)
    except ValueError as invalid:
        tmp_path.unlink(missing_ok=True)
        sys.stderr.write(f'Rejected download: {invalid}\n')
        sys.exit(1)
    return tmp_path


def main() -> None:
    """Download the static feed once and archive it if its content changed."""
    parser = build_parser(__doc__, city_slugs())
    args = parser.parse_args()
    target = _resolve_target(parser, args)
    tmp_path = _download_valid(args, target)
    sys.exit(archive_feed(args.output_dir, tmp_path, target.url, target.tz))


if __name__ == '__main__':
    main()
