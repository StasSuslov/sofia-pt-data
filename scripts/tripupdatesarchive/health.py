"""What the packer reports to its healthchecks.io check."""

import datetime as dt
from pathlib import Path

from tripupdatesarchive.names import archive_name, hour_key
from tripupdatesarchive.tarball import member_digests

_SECONDS_PER_HOUR = 3600
_HOUR = dt.timedelta(hours=1)


def ping_target(
    url: str | None,
    output_dir: Path,
    now: dt.datetime,
    poll_interval_sec: int,
    *,
    failed: bool,
) -> str | None:
    """The URL to ping after a run, None when no check is configured.

    Success needs a run without failures and an archive of the last closed
    hour with at least half the snapshots the cadence allows, 40 of 80 at
    45 s. Anything else pings <url>/fail, so a dead poller shows at the next
    :07 and does not wait for the check's grace period.
    """
    if not url:
        return None
    if failed or not _hour_half_full(output_dir, now, poll_interval_sec):
        return f'{url}/fail'
    return url


def _hour_half_full(output_dir: Path, now: dt.datetime, interval: int) -> bool:
    archive = output_dir / archive_name(hour_key(now - _HOUR))
    if not archive.exists():
        return False
    return len(member_digests(archive)) * 2 >= _SECONDS_PER_HOUR // interval
