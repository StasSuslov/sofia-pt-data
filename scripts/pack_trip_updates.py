"""Hourly packing of trip-updates snapshots into tar.xz archives.

Every UTC hour before the current one that still has snapshots in
incoming/ becomes tu_<YYYY-MM-DD>T<HH>Z.tar.xz beside incoming/: members
sorted and named as the snapshot files, bytes and mtimes kept. Snapshots
of one hour repeat each other, so xz -6 packed an hour at ~20 KB a snapshot
against ~560 KB raw on the VPS (2026-10-17); one snapshot alone packs to
~64 KB.

A snapshot is deleted only once the archive that holds it is on disk for
good. The archive is written as incoming/<archive>.part, read back against
the files, fsynced, renamed into place, and its directory fsynced. A .part
that does not read back is deleted and the hour waits for the next run.

An hour whose archive already exists is checked against it. That happens
after a run stopped between the rename and the deletions, or when a
snapshot arrived after its hour was packed because the clock stepped back.
Files the archive holds are deleted, files it lacks are added by rebuilding
it, and a file whose namesake in the archive has other bytes is left alone.

A failed hour gets a line in runs.ndjson with stage "pack" and makes the
exit code 1. An unexpected exception (disk full, a corrupt archive) ends
the run with a traceback and no ping.

With HEALTHCHECK_URL set, the run ends with one ping: success when nothing
failed and the last closed hour's archive holds at least half the
snapshots poll_interval_sec allows, <URL>/fail otherwise. A failed ping is
a warning and leaves the exit code alone.

Usage:
    python scripts/pack_trip_updates.py data/sofia/tripupdates
"""

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from tripupdatesarchive.health import ping_target
from tripupdatesarchive.pack import pack_closed_hours

from collector.loop import ping_healthcheck
from config import CityProfileError, city_slugs, load_city_for_path


def _parser() -> argparse.ArgumentParser:
    have = ', '.join(city_slugs()) or 'none'
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'output_dir',
        type=Path,
        help='The trip updates directory, incoming/ inside it '
        '(e.g. data/sofia/tripupdates)',
    )
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help='City profile supplying poll_interval_sec '
        f'(cities/<slug>.json; have: {have}). '
        'Default: read from output_dir',
    )
    return parser


def _poll_interval(parser, args) -> int:
    try:
        city = load_city_for_path(args.output_dir, slug=args.city)
    except CityProfileError as profile_error:
        parser.error(str(profile_error))
    return city['poll_interval_sec']


def _report(output_dir: Path, now, interval: int, *, failed: bool) -> None:
    target = ping_target(
        os.environ.get('HEALTHCHECK_URL'),
        output_dir,
        now,
        interval,
        failed=failed,
    )
    if target:
        ping_healthcheck(target)


def main() -> None:
    """Pack the closed hours, then report to healthchecks.io if configured."""
    parser = _parser()
    args = parser.parse_args()
    interval = _poll_interval(parser, args)
    now = dt.datetime.now(dt.UTC)
    failed = pack_closed_hours(args.output_dir, now)
    _report(args.output_dir, now, interval, failed=failed > 0)
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
