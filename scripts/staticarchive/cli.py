"""Command line of scripts/archive_static_feed.py."""

import argparse
from pathlib import Path

from staticarchive.download import DEFAULT_TIMEOUT_SEC


def build_parser(
    description: str | None,
    slugs: list[str],
) -> argparse.ArgumentParser:
    """Build the argument parser.

    Args:
        description: The module docstring, printed by --help.
        slugs: City profile slugs available in cities/.

    Returns:
        The configured parser.
    """
    have = ', '.join(slugs) or 'none'
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'output_dir',
        type=Path,
        help='Directory to store gtfs_<YYYY-MM-DD>.zip snapshots and their '
        'manifests (e.g. data/sofia/static)',
    )
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help='City profile supplying the feed URL and timezone '
        f'(cities/<slug>.json; have: {have}). '
        'Default: read from output_dir, laid out as .../data/<city>/static',
    )
    parser.add_argument(
        '--url',
        type=str,
        default=None,
        help="GTFS Static feed URL (default: the city profile's)",
    )
    parser.add_argument(
        '--timezone',
        type=str,
        default=None,
        help="Timezone for the snapshot's calendar date (default: the city "
        "profile's, the same value collect.py rotates the RT archive on, so "
        'a snapshot files under the same local day)',
    )
    parser.add_argument(
        '--timeout',
        type=int,
        default=DEFAULT_TIMEOUT_SEC,
        help='Download timeout in seconds (default: 60)',
    )
    return parser
