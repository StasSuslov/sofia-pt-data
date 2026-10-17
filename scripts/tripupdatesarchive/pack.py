"""Pack the closed UTC hours of incoming/ into hourly tar.xz archives."""

import datetime as dt
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

from alertsarchive.runlog import log_run

from tripupdatesarchive.lock import exclusive
from tripupdatesarchive.names import INCOMING, archive_name, hour_key, hour_of
from tripupdatesarchive.tarball import (
    file_digest,
    fsync_dir,
    member_digests,
    write_archive,
)


def pack_closed_hours(output_dir: Path, now: dt.datetime) -> int:
    """Archive every hour before the current UTC hour, then clear its files.

    Args:
        output_dir: The trip updates directory, incoming/ inside it.
        now: The run's time; its UTC hour and later ones stay untouched.

    Returns:
        How many hours failed; each has a line in runs.ndjson.

    Raises:
        PackerBusyError: another run holds output_dir; nothing was touched.
    """
    incoming = output_dir / INCOMING
    current = hour_key(now)
    with exclusive(output_dir):
        _drop_stale_tmp(incoming, current)
        hours = _closed(incoming.glob('tu_*.pb'), current)
        return sum(
            _pack_and_log(output_dir, now, hour, snapshots)
            for hour, snapshots in hours.items()
        )


def _pack_and_log(output_dir, now, hour: str, snapshots: list[Path]) -> int:
    error = _pack_hour(output_dir, hour, snapshots)
    if error is None:
        return 0
    log_run(output_dir, now, stage='pack', hour=hour, error=error)
    return 1


def _drop_stale_tmp(incoming: Path, current: str) -> None:
    """Delete the .tmp files a poller killed by its timeout left behind."""
    for stale in _closed(incoming.glob('tu_*.pb.tmp'), current).values():
        for partial in stale:
            partial.unlink(missing_ok=True)


def _closed(paths: Iterable[Path], current: str) -> dict[str, list[Path]]:
    """Group files by the hour in their names, hours before `current` only."""
    by_hour = defaultdict(list)
    for path in sorted(paths):
        hour = hour_of(path.name)
        if hour is not None and hour < current:
            by_hour[hour].append(path)
    return by_hour


def _pack_hour(
    output_dir: Path,
    hour: str,
    snapshots: list[Path],
) -> str | None:
    """Archive one closed hour, delete what the archive holds, report the rest.

    Returns:
        None, or why the hour is not done.
    """
    archive = output_dir / archive_name(hour)
    matched, orphans, conflicts = _compare(archive, snapshots)
    if orphans and not _commit(archive, orphans):
        return f'{archive.name} did not read back as its snapshots'
    for path in (*matched, *orphans):
        path.unlink()
    if not conflicts:
        return None
    return (
        f'{len(conflicts)} snapshots differ from their archived namesakes, '
        f'first {conflicts[0].name}'
    )


def _compare(archive: Path, snapshots: list[Path]):
    """Sort an hour's files: archived alike, not archived, archived unlike."""
    archived = member_digests(archive) if archive.exists() else {}
    held = [path for path in snapshots if path.name in archived]
    matched = [
        path for path in held if file_digest(path) == archived[path.name]
    ]
    return (
        matched,
        [path for path in snapshots if path.name not in archived],
        [path for path in held if path not in matched],
    )


def _commit(archive: Path, snapshots: list[Path]) -> bool:
    """Write the archive plus `snapshots`, check it, put it in place.

    The .part goes in incoming/, which the laptop's pull skips. It is
    checked against the old members and the new files, then renamed over
    the archive and the directory fsynced: without both fsyncs a power cut
    could leave an empty archive with its snapshots already deleted.

    Returns:
        False when the .part did not read back as expected; it is deleted
        and the archive is untouched.
    """
    base = archive if archive.exists() else None
    expected = {} if base is None else member_digests(base)
    expected.update({path.name: file_digest(path) for path in snapshots})
    part = snapshots[0].with_name(f'{archive.name}.part')
    write_archive(part, base, snapshots)
    if member_digests(part) != expected:
        part.unlink()
        return False
    part.replace(archive)
    fsync_dir(archive.parent)
    return True
