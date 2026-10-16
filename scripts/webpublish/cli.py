"""Command line of publish_web.py: parser and the closing summary."""

import argparse
import sys
from pathlib import Path

DEFAULT_DAYS = 30
DEFAULT_REPO = 'git@github.com:StasSuslov/sofia-pt-web.git'

# A GitHub Pages *project* site serves from /<repo>/, not from /. Both the
# built asset URLs (Vite's `base`) and the data fetches
# (VITE_DATA_BASE_URL, see frontend/src/data.ts) have to agree with that or
# every request resolves one level too high.
DEFAULT_BASE_PATH = '/sofia-pt-web/'

MEGABYTE = 1e6


def build_parser(description: str) -> argparse.ArgumentParser:
    """The publish_web.py argument parser.

    Args:
        description: The text shown by --help.

    Returns:
        The parser.
    """
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'web_dir',
        type=Path,
        help='Web export to publish from, e.g. data/sofia/web',
    )
    parser.add_argument(
        'frontend_dir',
        type=Path,
        help='Vite project to build, e.g. frontend',
    )
    parser.add_argument(
        '--days',
        type=int,
        default=DEFAULT_DAYS,
        help=(
            'How many of the newest day bundles to publish '
            f'(default: {DEFAULT_DAYS}); '
            'older days stay addressable through the Zenodo dataset record'
        ),
    )
    parser.add_argument(
        '--staging',
        type=Path,
        default=None,
        help=(
            'Directory to assemble the tree in '
            '(default: a fresh temp directory). '
            'Give one to inspect what would be published.'
        ),
    )
    parser.add_argument(
        '--repo',
        default=DEFAULT_REPO,
        help=f'Push target (default: {DEFAULT_REPO})',
    )
    parser.add_argument(
        '--base-path',
        default=DEFAULT_BASE_PATH,
        help=(
            'URL prefix the site is served from, with a trailing slash '
            f'(default: {DEFAULT_BASE_PATH})'
        ),
    )
    parser.add_argument(
        '--dry-run',
        action='store_true',
        help='Build the tree and report it, then stop without pushing',
    )
    return parser


def _days_line(days: list[str]) -> str:
    if not days:
        return '  days published: 0'
    count = len(days)
    first, last = days[0], days[-1]
    span = f'{first} .. {last}'
    return f'  days published: {count} ({span})'


def _skipped_line(skipped: list[str]) -> str:
    oldest = f' (oldest {skipped[0]})' if skipped else ''
    return f'  days skipped:   {len(skipped)}{oldest}'


def _periods_line(report: dict) -> str:
    count = len(report['period_dirs'])
    current = report['current_period']
    return f'  periods:        {count}, current {current}'


def print_summary(
    report: dict,
    staging_dir: Path,
    base_path: str,
    *,
    dry_run: bool,
    staging_kept: bool,
) -> None:
    """Write the closing report of a run to stdout.

    Args:
        report: What build_staging_tree returned.
        staging_dir: The staging directory.
        base_path: The URL prefix of the site.
        dry_run: Whether nothing was pushed.
        staging_kept: Whether the staging directory outlives the run.
    """
    verdict = 'DRY RUN — nothing pushed' if dry_run else 'Published'
    lines = [f'\n{verdict}']
    if staging_kept:
        lines.append(f'  staging:        {staging_dir}')
    size_mb = report['size_bytes'] / MEGABYTE
    lines += [
        f'  base path:      {base_path} (data at {base_path}data)',
        _days_line(report['days_published']),
        _skipped_line(report['days_skipped']),
        _periods_line(report),
        f'  total size:     {size_mb:.1f} MB',
        '',
    ]
    sys.stdout.write('\n'.join(lines))
