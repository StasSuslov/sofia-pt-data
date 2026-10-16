"""The command line of export_web.py."""

import argparse
from pathlib import Path

from config import city_slugs
from webexport.constants import MIN_SAMPLES_DEFAULT


def _add_inputs(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        'static_source',
        type=Path,
        help='GTFS static zip (needs shapes.txt), or a directory of '
        'gtfs_<YYYY-MM-DD>.zip snapshots -- every snapshot found is loaded '
        'and merged (see load_static_sources)',
    )
    parser.add_argument(
        'data_dir',
        type=Path,
        help='Directory with <YYYY-MM-DD>.jsonl/.manifest.json and '
        "segment_speeds.py's processed/ output",
    )


def _add_paths(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--processed-dir',
        type=Path,
        default=None,
        help='Where segment_speeds.py wrote its output '
        '(default: <data_dir>/processed)',
    )
    parser.add_argument(
        '--output-dir',
        type=Path,
        default=None,
        help='Where to write the web export (default: <data_dir>/web)',
    )


def _add_selection(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        '--day',
        type=str,
        default=None,
        help="Export one specific YYYY-MM-DD day's own median instead of "
        'typical_weekday.json (Component D feature 2, the day switcher)',
    )
    parser.add_argument(
        '--min-samples',
        type=int,
        default=MIN_SAMPLES_DEFAULT,
        help='Drop (segment, timeslot) bins with fewer observations than '
        f'this (default: {MIN_SAMPLES_DEFAULT})',
    )
    slugs = ', '.join(city_slugs()) or 'none'
    parser.add_argument(
        '--city',
        type=str,
        default=None,
        help="City profile supplying attribution and the city's own known "
        f'limitations (cities/<slug>.json; have: {slugs}). '
        'Default: read from data_dir, laid out as .../data/<city>/',
    )


def build_parser(description: str) -> argparse.ArgumentParser:
    """Build the parser; `description` is the module docstring."""
    parser = argparse.ArgumentParser(
        description=description,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _add_inputs(parser)
    _add_paths(parser)
    _add_selection(parser)
    return parser
