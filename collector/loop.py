"""The collection loop: poll, write, log, sleep, until told to stop."""

import contextlib
import functools
import signal
import threading
import time
from collections.abc import Callable

import requests

from collector.console import clock, out, warn
from collector.feed import PollResult
from collector.plan import Plan, announce, open_sink
from collector.sink import Sink

HEALTHCHECK_TIMEOUT_SEC = 5
SECONDS_PER_HOUR = 3600


def ping_healthcheck(url: str) -> None:
    """Best-effort ping to a dead-man's-switch URL.

    E.g. https://healthchecks.io.
    Proves the poll loop is still iterating — independent of whether any given
    fetch succeeds — so a frozen process or a downed VPS gets noticed even
    when no one is watching the logs. Uses its own short-lived request rather
    than the feed's session, so monitoring and feed cookies never mix.

    Never raises, deliberately catching Exception rather than just
    requests.RequestException: a monitoring hiccup — of any kind — must not
    be able to take down the thing it's monitoring.
    """
    try:
        requests.get(url, timeout=HEALTHCHECK_TIMEOUT_SEC)
    except Exception as error:
        warn(f'Healthcheck ping failed: {error}')


class _StopSignal(threading.Event):
    """Event that doubles as the SIGINT/SIGTERM handler."""

    def __call__(self, signum, frame) -> None:
        self.set()
        out(
            '\n[INFO] Interrupted — '
            'finishing current snapshot and closing file.',
        )


class _Tally:
    """Running counts of the polls so far."""

    def __init__(self) -> None:
        """Start all counts at zero."""
        self.polls = 0
        self.rows = 0
        self.empty = 0
        self.errors = 0

    def log_line(self, poll: PollResult) -> str:
        """Count this poll and return the console line that reports it."""
        head = f'[{clock()}] poll #{self.polls:>4} |'
        dropped = poll.dropped_out_of_bbox
        note = f' | {dropped} dropped (bbox)' if dropped else ''
        if poll.records:
            self.rows += len(poll.records)
            return (
                f'{head} {len(poll.records):>4} vehicles{note} | '
                f'total rows: {self.rows:,}'
            )
        if not poll.fetch_ok:
            self.errors += 1
            return f'{head} fetch error (#{self.errors})'
        self.empty += 1
        return f'{head} empty snapshot (#{self.empty}){note}'


def _poll_until_done(
    plan: Plan,
    stop: _StopSignal,
    sink: Sink,
    poll_once: Callable[[], PollResult],
) -> _Tally:
    deadline = (
        float('inf')
        if plan.hours <= 0
        else time.time() + (plan.hours * SECONDS_PER_HOUR)
    )
    tally = _Tally()
    while time.time() < deadline and not stop.is_set():
        loop_start = time.time()
        sink.rotate_if_needed()
        poll = poll_once()
        tally.polls += 1
        if plan.healthcheck_url and tally.polls % plan.healthcheck_every == 0:
            ping_healthcheck(plan.healthcheck_url)
        sink.write(poll)
        out(tally.log_line(poll))
        # Event.wait, not time.sleep: an interrupted sleep resumes once
        # the handler returns (PEP 475), so a SIGTERM landing in the idle
        # stretch printed "finishing current snapshot" and then held the
        # process for the rest of the interval — 45 s on the deployed
        # cadence, on every systemctl stop or restart.
        remaining = plan.interval - (time.time() - loop_start)
        stop.wait(max(0, remaining))
    return tally


def run(plan: Plan, fetch: Callable[..., PollResult]) -> None:
    """Poll `plan.url` through `fetch` until the plan's hours elapse.

    Runs forever when `plan.hours` <= 0, writing newline-delimited JSON
    records. SIGINT and SIGTERM end the run after the current snapshot.
    """
    # Graceful shutdown on Ctrl+C (SIGINT) or `systemctl stop` (SIGTERM).
    # An Event rather than a flag because the loop idles inside it: see the
    # wait() at the bottom of the loop.
    stop = _StopSignal()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    session = requests.Session()
    session.headers['User-Agent'] = plan.user_agent
    sink = open_sink(plan)
    announce(plan, sink.path)
    poll_once = functools.partial(fetch, plan.url, session, plan.bbox)

    with contextlib.closing(session), contextlib.closing(sink):
        tally = _poll_until_done(plan, stop, sink, poll_once)

    out(
        f'\nDone. Polls: {tally.polls} | Records: {tally.rows:,} | '
        f'Empty snapshots: {tally.empty} | Fetch errors: {tally.errors}',
    )
    out(f'Last output file: {sink.path}')
