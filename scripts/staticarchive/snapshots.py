"""Finding, naming and saving snapshots in the static directory."""

import datetime as dt
import re
import sys
from pathlib import Path
from typing import NamedTuple

from staticarchive.download import load_feed_info
from staticarchive.hashing import sha256_of_file
from staticarchive.manifest import build_manifest, saved_line, write_manifest

# Same full-match pattern as find_static_snapshots() in segment_speeds.py,
# kept as a short local copy rather than an import for the reason given
# in archive_static_feed.py. A prefix or glob match would also catch the
# unpacked gtfs_2026-08-27/ sibling directory or a stray .DS_Store that
# already sit next to the zips in data/sofia/static/ today.
SNAPSHOT_NAME_RE = re.compile(r'^gtfs_(\d{4}-\d{2}-\d{2})(?:T\d{4})?\.zip$')

_SNAPSHOT_MODE = 0o644


class Candidate(NamedTuple):
    """A downloaded, validated zip that may become a snapshot."""

    tmp_path: Path
    source_url: str
    content_hash: str
    member_count: int


def latest_snapshot(output_dir: Path):
    """Find the newest snapshot already in output_dir.

    Newest is decided on the filename, not on the parsed date, because two
    snapshots can share a date: gtfs_2026-09-02.zip and its intra-day
    sibling gtfs_2026-09-02T1100.zip. "." sorts before "T", so the plain
    name compares as the earlier of the two, which is what it is.

    Returns:
        (date_str, path), or None if there isn't one yet (a fresh archive,
        or a directory that doesn't exist).
    """
    if not output_dir.exists():
        return None
    snapshots = []
    for candidate in output_dir.iterdir():
        if not candidate.is_file():
            continue
        matched = SNAPSHOT_NAME_RE.match(candidate.name)
        if matched:
            snapshots.append((matched.group(1), candidate))
    if not snapshots:
        return None
    return max(snapshots, key=lambda pair: pair[1].name)


def _target_path(output_dir: Path, local: dt.datetime) -> Path:
    today = local.date().isoformat()
    target_path = output_dir / f'gtfs_{today}.zip'
    if target_path.exists():
        # A second change inside one local day. Overwriting the morning
        # capture would destroy the only evidence of what the agency served
        # earlier today in exchange for evidence of what it serves now, and
        # discarding the new one (what this did until 2026-09-03) loses the
        # afternoon instead. Both go on disk; the intra-day name carries the
        # local capture time, so it sorts after the plain name for the same
        # date and pick_snapshot_for_day() in segment_speeds.py picks it up
        # as the newer snapshot for that day.
        clock = local.strftime('%H%M')
        return output_dir / f'gtfs_{today}T{clock}.zip'
    return target_path


def _warn_if_stale(target_name: str, feed_info: dict, local: dt.datetime):
    declared = (feed_info.get('feed_start_date') or '').strip()
    if declared and declared != local.date().strftime('%Y%m%d'):
        # The name records when this archive observed the feed, deliberately,
        # rather than trusting the publisher's own date (see METHODOLOGY.md).
        # The two agree only while the capture runs after the agency's daily
        # build. When they stop agreeing the build schedule moved, and every
        # snapshot from here on holds the previous day's feed under today's
        # name. Not fatal: the file is still a real snapshot worth keeping,
        # and refusing it would lose data over a scheduling problem.
        sys.stderr.write(
            f'Warning: {target_name} declares feed_start_date={declared}. '
            "This capture ran before the agency's daily build; check the "
            'schedule in deploy/sofia-static-archive.timer against the member '
            'timestamps inside the zip.\n',
        )


def _measure(candidate: Candidate) -> dict:
    return {
        'zip_sha256': sha256_of_file(candidate.tmp_path),
        'content_hash': candidate.content_hash,
        'size_bytes': candidate.tmp_path.stat().st_size,
        'member_count': candidate.member_count,
    }


def save_snapshot(
    output_dir: Path,
    candidate: Candidate,
    local: dt.datetime,
    previous_name: str | None,
) -> None:
    """Move the candidate into place, write its manifest and report.

    Args:
        output_dir: The static directory.
        candidate: The new content; its tmp_path is consumed.
        local: Capture time in the city's timezone.
        previous_name: The snapshot this one replaces, None for the first.
    """
    measured = _measure(candidate)
    feed_info = load_feed_info(candidate.tmp_path)
    target_path = _target_path(output_dir, local)

    candidate.tmp_path.replace(target_path)
    # mkstemp() creates at 0600 and os.replace() carries that mode over, which
    # would leave the snapshot the one file under data/ that only its writer
    # can read. Day files land at the umask default.
    target_path.chmod(_SNAPSHOT_MODE)

    _warn_if_stale(target_path.name, feed_info, local)
    manifest = build_manifest(
        target_path,
        candidate.source_url,
        local,
        measured,
        feed_info,
    )
    write_manifest(target_path, manifest)
    reason = (
        'no prior snapshot on disk'
        if previous_name is None
        else f'changed from {previous_name}'
    )
    sys.stdout.write(saved_line(target_path.name, reason, measured))
