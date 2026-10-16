"""The settings of one collection run, and the setup derived from them."""

import datetime as dt
import functools
from pathlib import Path
from typing import NamedTuple
from zoneinfo import ZoneInfo

from collector.console import out
from collector.sink import Sink, dated_output_path

DEFAULT_HEALTHCHECK_EVERY = 20


class Plan(NamedTuple):
    """Everything one collection run needs, resolved by the caller."""

    interval: int
    hours: float
    url: str
    bbox: dict
    user_agent: str
    tz_name: str
    output_path: Path | None
    output_dir: Path | None
    healthcheck_url: str | None
    healthcheck_every: int


def open_sink(plan: Plan) -> Sink:
    """Create the output directory and open the run's files."""
    if plan.output_dir is None:
        plan.output_path.parent.mkdir(parents=True, exist_ok=True)
        return Sink(lambda: plan.output_path)
    tz = ZoneInfo(plan.tz_name)
    plan.output_dir.mkdir(parents=True, exist_ok=True)
    return Sink(functools.partial(dated_output_path, plan.output_dir, tz))


def announce(plan: Plan, path: Path) -> None:
    """Print the run's banner to stdout."""
    mode_desc = (
        'single file'
        if plan.output_dir is None
        else f'daily rotation in {plan.output_dir} ({plan.tz_name})'
    )
    duration = 'forever' if plan.hours <= 0 else f'{plan.hours}h'
    # Naive local time, as the log has always shown it.
    started = dt.datetime.now(dt.UTC).astimezone().replace(tzinfo=None)
    out(f'Starting collection → {path}')
    out(f'Endpoint : {plan.url}')
    out(
        f'Interval : {plan.interval}s  |  Duration: {duration}  |  '
        f'Mode: {mode_desc}  |  Started: {started.isoformat()}',
    )
    out('Press Ctrl+C to stop early.\n')
