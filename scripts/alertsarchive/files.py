"""Saved alert responses in the alerts directory."""

import datetime as dt
import gzip
import os
import re
import tempfile
from pathlib import Path

SAVED_NAME_RE = re.compile(r'^alerts_\d{4}-\d{2}-\d{2}T\d{6}Z\.pb\.gz$')
_SAVED_MODE = 0o644


def latest_saved(output_dir: Path) -> Path | None:
    """Return the newest saved response by name, or None if there is none."""
    if not output_dir.exists():
        return None
    saved = [
        candidate
        for candidate in output_dir.iterdir()
        if candidate.is_file() and SAVED_NAME_RE.match(candidate.name)
    ]
    return max(saved, key=lambda candidate: candidate.name, default=None)


def _write_gzip(fd: int, body: bytes, tmp_name: str, target: Path) -> None:
    with os.fdopen(fd, 'wb') as out:
        out.write(gzip.compress(body))
    Path(tmp_name).replace(target)


def _write_through_temp(output_dir: Path, body: bytes, target: Path) -> None:
    fd, tmp_name = tempfile.mkstemp(
        prefix='.alerts_',
        suffix='.part',
        dir=output_dir,
    )
    try:
        _write_gzip(fd, body, tmp_name, target)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def save_response(output_dir: Path, body: bytes, now: dt.datetime) -> Path:
    """Save a response gzipped under its UTC capture time.

    Through a temp file and os.replace(): a truncated .pb.gz under a real
    name would become the newest file, fail to decompress on every later
    run, and stop the archive until someone deleted it by hand.
    """
    stamp = now.astimezone(dt.UTC).strftime('%Y-%m-%dT%H%M%SZ')
    target = output_dir / f'alerts_{stamp}.pb.gz'
    _write_through_temp(output_dir, body, target)
    target.chmod(_SAVED_MODE)  # mkstemp's 0600 survives os.replace
    return target
