"""Command line of segment_speeds.py: arguments, city, timezone, snapshots."""

import argparse
import sys
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from config import CityProfileError, city_slugs, load_city_for_path
from speeds.snapshots import find_static_snapshots


class Setup(NamedTuple):
    """Everything a run needs that the command line decides."""

    args: argparse.Namespace
    city: dict
    tz: ZoneInfo
    output_dir: Path
    snapshots: list | None


def _add_positionals(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        'static_source',
        type=Path,
        help='GTFS static zip (one feed for every day processed), or a '
        'directory of gtfs_<YYYY-MM-DD>.zip snapshots to pick from per day '
        '(see pick_snapshot_for_day)',
    )
    parser.add_argument(
        'data_dir',
        type=Path,
        help='Directory containing <YYYY-MM-DD>.jsonl RT snapshot files',
    )
    parser.add_argument(
        'dates',
        nargs='*',
        help='Specific YYYY-MM-DD days to process (default: every '
        '<YYYY-MM-DD>.jsonl file found in data_dir)',
    )


def _city_help() -> str:
    have = ', '.join(city_slugs()) or 'none'
    return (
        "City profile supplying the timezone and the feed's speed unit "
        f'(cities/<slug>.json; have: {have}). '
        'Default: read from data_dir, laid out as .../data/<city>/'
    )


def _add_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Where to write outputs (default: <data_dir>/processed)',
    )
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help=_city_help(),
    )
    parser.add_argument(
        '--timezone',
        type=str,
        default=None,
        help='Timezone for timeslot bins and weekday classification '
        "(default: the city profile's)",
    )


def _snapshots(static_source: Path) -> list | None:
    """None for a single zip, else the date-sorted snapshots to pick from."""
    # None (single zip, same feed for every day -- unchanged prior behaviour)
    # or a date-sorted [(snapshot_date, path), ...] to pick from per day.
    if not static_source.is_dir():
        return None
    snapshots = find_static_snapshots(static_source)
    if not snapshots:
        sys.stderr.write(
            f'No gtfs_<YYYY-MM-DD>.zip snapshots found in {static_source}\n',
        )
        sys.exit(1)
    return snapshots


def _parse(doc: str) -> tuple:
    parser = argparse.ArgumentParser(
        description=doc,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_positionals(parser)
    _add_options(parser)
    args = parser.parse_args()
    try:
        city = load_city_for_path(args.data_dir, slug=args.city)
    except CityProfileError as profile_error:
        parser.error(str(profile_error))
    return args, city


def parse_setup(doc: str) -> Setup:
    """Parse the command line and resolve city, timezone and snapshots."""
    args, city = _parse(doc)
    output_dir = args.output_dir or (args.data_dir / 'processed')
    output_dir.mkdir(parents=True, exist_ok=True)
    tz = ZoneInfo(args.timezone or city['timezone'])
    return Setup(args, city, tz, output_dir, _snapshots(args.static_source))
