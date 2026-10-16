"""Command line of scripts/archive_alerts.py."""

import argparse
from pathlib import Path

DEFAULT_TIMEOUT_SEC = 30


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
        help='Directory for alerts_<UTC>.pb.gz files and runs.ndjson '
        '(e.g. data/sofia/alerts)',
    )
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help='City profile supplying the feed URL '
        f'(cities/<slug>.json; have: {have}). '
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
        default=DEFAULT_TIMEOUT_SEC,
        help='Request timeout in seconds (default: 30)',
    )
    return parser
