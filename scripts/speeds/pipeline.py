"""One segment_speeds run, from parsed arguments to typical_weekday.json."""

from speeds.cli import Setup
from speeds.dayplan import build_day_plan
from speeds.feedcache import FeedCache
from speeds.report import finish
from speeds.rundays import run_days
from speeds.runstate import RunState
from speeds.selection import plan_files, requested_dates, select_day_files


def run(setup: Setup) -> None:
    """Process the requested days and publish their typical weekday."""
    cache = FeedCache()
    state = RunState()
    day_files = select_day_files(
        setup.args.data_dir,
        setup.args.dates,
        setup.tz,
    )
    day_plan = build_day_plan(
        setup,
        cache,
        plan_files(day_files, setup.args.data_dir, setup.tz),
        requested_dates(day_files),
    )
    run_days(setup, cache, day_plan, state)
    finish(setup, cache, day_plan, state)
