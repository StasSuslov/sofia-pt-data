"""One trip-updates snapshot written into incoming/."""

import datetime as dt
import os
from pathlib import Path

from tripupdatesarchive.names import INCOMING, snapshot_name


def save_snapshot(output_dir: Path, body: bytes, now: dt.datetime) -> Path:
    """Write a response as served into incoming/, on disk before it is named.

    The bytes go to <name>.tmp and are fsynced before the file takes its
    name: a power cut must not leave an empty tu_*.pb that the packer would
    archive as a snapshot. A .tmp left by a killed run is the packer's to
    delete once its hour is closed.

    Returns:
        The saved snapshot.
    """
    incoming = output_dir / INCOMING
    incoming.mkdir(parents=True, exist_ok=True)
    target = incoming / snapshot_name(now)
    partial = target.with_name(f'{target.name}.tmp')
    with partial.open('wb') as snapshot:
        snapshot.write(body)
        snapshot.flush()
        os.fsync(snapshot.fileno())
    return partial.replace(target)
