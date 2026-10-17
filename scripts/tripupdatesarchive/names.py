"""Names of trip-updates snapshots and hourly archives, all in UTC.

UTC, not Sofia's clock: the local clock repeats an hour when DST ends, and
the packer groups snapshots by the hour in their names.
"""

import datetime as dt
import re

INCOMING = 'incoming'
_SNAPSHOT_RE = re.compile(r'^tu_(\d{4}-\d\d-\d\dT\d\d)\d{4}Z\.pb(?:\.tmp)?$')


def snapshot_name(now: dt.datetime) -> str:
    """Name of the snapshot of a request started at `now`."""
    return now.astimezone(dt.UTC).strftime('tu_%Y-%m-%dT%H%M%SZ.pb')


def hour_key(moment: dt.datetime) -> str:
    """The UTC hour of `moment` as names spell it, e.g. 2026-10-18T09."""
    return moment.astimezone(dt.UTC).strftime('%Y-%m-%dT%H')


def hour_of(name: str) -> str | None:
    """The UTC hour of a snapshot or .tmp name, None for any other name."""
    match = _SNAPSHOT_RE.match(name)
    return match.group(1) if match else None


def archive_name(hour: str) -> str:
    """Name of the archive that holds one UTC hour of snapshots."""
    return f'tu_{hour}Z.tar.xz'
