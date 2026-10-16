#!/usr/bin/env python3
"""Sofia Public Transport — GTFS-RT data collector.

Source: gtfs.sofiatraffic.bg (Център за градска мобилност)

Usage:
    # Step 1: discover available endpoints
    python collect.py --discover

    # Step 2a: fixed-duration run into a single file
    python collect.py --output data/wednesday.jsonl --hours 24

    # Step 2b: continuous collection with daily rotation (for long-running
    # deployments, e.g. under systemd) — writes <output-dir>/<YYYY-MM-DD>.jsonl,
    # rolling over at local midnight in --timezone. --hours <= 0 means run
    # until stopped (SIGINT/SIGTERM) instead of a fixed duration.
    python collect.py --output-dir data/sofia --hours 0

Each data file <name>.jsonl has a companion <name>.polls.jsonl heartbeat log
with one line per poll attempt — fetch_ok, vehicle_count (post-bbox-filter),
and entities_total / vehicles_with_position / dropped_out_of_bbox so the bbox
filter's own losses are auditable rather than silent. See heartbeat_path_for()
and PollResult. Feed both files into scripts/generate_manifest.py to get a
checksummed, gap-annotated coverage report per day.
"""

from pathlib import Path

from collector.cli import (
    build_parser,
    collection_kwargs,
    prepare_run,
    resolve_city,
)
from collector.discover import discover_endpoint, run_discovery
from collector.feed import (
    PollResult,
    fetch_vehicle_positions,
    is_in_network_bbox,
)
from collector.loop import ping_healthcheck, run
from collector.plan import DEFAULT_HEALTHCHECK_EVERY, Plan
from collector.sink import dated_output_path, heartbeat_path_for

# Re-exported: tests and other tools import these from collect. The noqa is
# for wemake banning __all__ outright.
__all__ = (  # noqa: WPS410
    'PollResult',
    'dated_output_path',
    'discover_endpoint',
    'fetch_vehicle_positions',
    'heartbeat_path_for',
    'is_in_network_bbox',
    'main',
    'ping_healthcheck',
    'run_collection',
)

# Everything this collector needs that differs between cities — feed URLs,
# bounding box, timezone, poll cadence, the User-Agent an operator reads in
# their logs — comes from cities/<slug>.json through config.load_city().
# main() resolves the city from --city or, failing that, from the data
# directory it was given, and passes the values down as arguments: no
# function below decides for itself which city it is working on.


def run_collection(  # noqa: WPS211  # signature is part of the module API
    interval: int,
    hours: float,
    url: str,
    *,
    bbox: dict,
    user_agent: str,
    tz_name: str,
    output_path: Path | None = None,
    output_dir: Path | None = None,
    healthcheck_url: str | None = None,
    healthcheck_every: int = DEFAULT_HEALTHCHECK_EVERY,
) -> None:
    """Poll `url` every `interval` seconds until `hours` elapse.

    Runs forever if `hours` <= 0, writing newline-delimited JSON records.

    `bbox`, `user_agent` and `tz_name` have no defaults on purpose. Each one
    belongs to a particular city, and a default here would let the wrong
    city's bounds silently filter another city's feed — the filter drops a
    position before it reaches disk, so that mistake leaves no trace to find
    later.

    Exactly one of `output_path` (single fixed file) or `output_dir` (daily
    rotation, file named <YYYY-MM-DD>.jsonl in `tz_name`) must be given.

    If `healthcheck_url` is set, it's pinged every `healthcheck_every`
    successful loop iterations (default 20, i.e. ~15 min at the default 45s
    interval) as a dead-man's switch.
    """
    plan = Plan(
        interval=interval,
        hours=hours,
        url=url,
        bbox=bbox,
        user_agent=user_agent,
        tz_name=tz_name,
        output_path=output_path,
        output_dir=output_dir,
        healthcheck_url=healthcheck_url,
        healthcheck_every=healthcheck_every,
    )
    # The fetch function is looked up here, at call time, so a test can
    # replace collect.fetch_vehicle_positions.
    run(plan, fetch_vehicle_positions)


def main() -> None:
    """Parse the command line and run the collector (or --discover)."""
    parser = build_parser()
    args = parser.parse_args()
    city = resolve_city(parser, args)
    if args.discover:
        run_discovery(city)
        return
    prepare_run(parser, args)
    run_collection(**collection_kwargs(args, city))


if __name__ == '__main__':
    main()
