"""Argument parsing and option resolution for collect.py."""

import argparse
import os
from pathlib import Path

from collector.plan import DEFAULT_HEALTHCHECK_EVERY
from config import (
    DEFAULT_HOURS,
    CityProfileError,
    city_slugs,
    load_city_for_path,
)


def _opt(*flags: str, **kwargs) -> tuple[tuple[str, ...], dict]:
    return flags, kwargs


def _options() -> list:
    slugs = ', '.join(city_slugs()) or 'none'
    return [
        _opt(
            '--city',
            type=str,
            default=None,
            help='City profile to collect (cities/<slug>.json; have: '
            f'{slugs}). Default: read from '
            '--output-dir, which is laid out as .../data/<city>/',
        ),
        _opt(
            '--discover',
            action='store_true',
            help="Probe the city's feed URLs and exit",
        ),
        _opt(
            '--url',
            type=str,
            default=None,
            help="GTFS-RT vehicle positions URL (default: the city profile's)",
        ),
        _opt(
            '--output',
            type=Path,
            default=None,
            help='Single output file, no rotation (default: '
            'data/snapshot.jsonl if --output-dir is not given)',
        ),
        _opt(
            '--output-dir',
            type=Path,
            default=None,
            help='Directory for daily-rotated output files named '
            '<YYYY-MM-DD>.jsonl (mutually exclusive with --output)',
        ),
        _opt(
            '--timezone',
            type=str,
            default=None,
            help='Timezone for day-boundary rotation (default: the city '
            "profile's)",
        ),
        _opt(
            '--interval',
            type=int,
            default=None,
            help="Poll interval in seconds (default: the city profile's)",
        ),
        _opt(
            '--hours',
            type=float,
            default=DEFAULT_HOURS,
            help=f'Collection duration in hours (default: {DEFAULT_HOURS}). '
            '0 or negative means run indefinitely until stopped.',
        ),
        _opt(
            '--healthcheck-url',
            type=str,
            default=os.environ.get('HEALTHCHECK_URL'),
            help="Dead-man's-switch URL (e.g. a healthchecks.io check), "
            'pinged periodically to prove the loop is still running. '
            'Defaults to the HEALTHCHECK_URL env var; omit both to disable.',
        ),
        _opt(
            '--healthcheck-every',
            type=int,
            default=DEFAULT_HEALTHCHECK_EVERY,
            help='Ping the healthcheck URL every N polls '
            f'(default: {DEFAULT_HEALTHCHECK_EVERY})',
        ),
    ]


def build_parser() -> argparse.ArgumentParser:
    """Return the collector's argument parser."""
    parser = argparse.ArgumentParser(description='GTFS-RT collector')
    for flags, kwargs in _options():
        parser.add_argument(*flags, **kwargs)
    return parser


def resolve_city(parser: argparse.ArgumentParser, args) -> dict:
    """Return the city profile the options select, or exit with an error.

    Two passes over the city-dependent options, because they are their own
    chicken and egg: the defaults live in the profile, and which profile to
    read is itself one of the arguments. So they parse as None, the city is
    resolved, and only then does the profile fill the gaps — leaving an
    explicit flag to win over the profile, as a flag should.
    """
    if args.output and args.output_dir:
        parser.error('--output and --output-dir are mutually exclusive')
    if not (args.city or args.output_dir):
        parser.error(
            'pass --city, or --output-dir laid out as .../data/<city>/',
        )
    try:
        city = load_city_for_path(args.output_dir or Path(), slug=args.city)
    except CityProfileError as error:
        parser.error(str(error))
    return city


def prepare_run(parser: argparse.ArgumentParser, args) -> None:
    """Reject bad run options and default the output file."""
    if args.healthcheck_every < 1:
        parser.error('--healthcheck-every must be at least 1')

    if not args.output and not args.output_dir:
        args.output = Path('data/snapshot.jsonl')


def collection_kwargs(args, city: dict) -> dict:
    """Return run_collection()'s arguments: flags win over the profile."""
    interval = args.interval
    return {
        'interval': (
            city['poll_interval_sec'] if interval is None else interval
        ),
        'hours': args.hours,
        'url': args.url or city['feeds']['vehicle_positions'],
        'bbox': city['bbox'],
        'user_agent': city['user_agent'],
        'tz_name': args.timezone or city['timezone'],
        'output_path': args.output,
        'output_dir': args.output_dir,
        'healthcheck_url': args.healthcheck_url,
        'healthcheck_every': args.healthcheck_every,
    }
