"""Command-line driver: one manifest per day file in a data directory."""

import argparse
import json
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

from config import (
    CityProfileError,
    city_slugs,
    date_from_path,
    find_day_files,
    load_city_for_path,
    resolve_day_file,
)
from manifests.build import build_manifest
from manifests.report import report_line
from manifests.staleness import (
    carry_remote_verification,
    read_manifest,
    should_skip,
)

DEFAULT_GAP_THRESHOLD = 3.0


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser of scripts/generate_manifest.py.

    Returns:
        The configured parser.
    """
    parser = argparse.ArgumentParser(
        description=(
            'Generate checksummed, gap-annotated coverage manifests for '
            'collected GTFS-RT day files'
        ),
    )
    parser.add_argument(
        'data_dir',
        type=Path,
        help='Directory containing <YYYY-MM-DD>.jsonl files',
    )
    parser.add_argument(
        '--gap-threshold',
        type=float,
        default=DEFAULT_GAP_THRESHOLD,
        help='Flag a gap when it exceeds this multiple of the *configured* '
        'poll interval, not the observed one (default: 3x)',
    )
    have = ', '.join(city_slugs()) or 'none'
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help='City profile supplying the poll cadence and timezone '
        f'(cities/<slug>.json; have: {have}). Default: read from data_dir, '
        'laid out as .../data/<city>/',
    )
    parser.add_argument(
        '--interval',
        type=int,
        default=None,
        help='Nominal collector poll interval in seconds, must match the '
        "cadence of the run being audited (default: the city profile's, "
        "which is also collect.py's)",
    )
    parser.add_argument(
        '--timezone',
        type=str,
        default=None,
        help="Timezone for calendar-day boundaries, must match collect.py's "
        "(default: the city profile's)",
    )
    parser.add_argument(
        '--force',
        action='store_true',
        help="Recompute every manifest even if it's already newer than its "
        'data and heartbeat files (default: skip days whose manifest is '
        'already current)',
    )
    return parser


def _process_day(
    path: Path,
    args: argparse.Namespace,
    interval: int,
    tz: ZoneInfo,
) -> None:
    date_str = date_from_path(path)
    polls_path = resolve_day_file(args.data_dir, date_str, '.polls.jsonl')
    manifest_path = args.data_dir / f'{date_str}.manifest.json'

    if should_skip(manifest_path, path, polls_path, force=args.force):
        sys.stdout.write(
            f'{path.name}: skipped (manifest already up to date)\n',
        )
        return

    manifest = carry_remote_verification(
        build_manifest(path, args.gap_threshold, interval, tz),
        read_manifest(manifest_path),
    )
    manifest_path.write_text(
        f'{json.dumps(manifest, indent=2, ensure_ascii=False)}\n',
        encoding='utf-8',
    )
    sys.stdout.write(report_line(path, manifest))


def _poll_settings(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> tuple[int, str]:
    try:
        city = load_city_for_path(args.data_dir, slug=args.city)
    except CityProfileError as err:
        parser.error(str(err))
    interval = (
        city['poll_interval_sec'] if args.interval is None else args.interval
    )
    return interval, args.timezone or city['timezone']


def _day_files_or_exit(data_dir: Path) -> list[Path]:
    # <date>.jsonl or <date>.jsonl.gz, deduplicated by date
    day_files = find_day_files(data_dir)
    if not day_files:
        sys.stderr.write(f'No day files found in {data_dir}\n')
        sys.exit(1)
    return day_files


def run() -> None:
    """Generate or refresh the manifest of every day file in data_dir."""
    parser = build_parser()
    args = parser.parse_args()
    interval, tz_name = _poll_settings(parser, args)

    for path in _day_files_or_exit(args.data_dir):
        _process_day(path, args, interval, ZoneInfo(tz_name))
