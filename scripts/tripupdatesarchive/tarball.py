"""Hourly tar.xz archives of trip-updates snapshots."""

import contextlib
import hashlib
import os
import tarfile
from pathlib import Path

# xz -6 took a snapshot from ~560 KB to 20,504 B and used 90 MB of RAM on
# the VPS (2026-10-17). Preset 3 is the fallback if memory runs short.
XZ_PRESET = 6


def file_digest(path: Path) -> str:
    """SHA-256 of a file on disk, hex."""
    with path.open('rb') as snapshot:
        return _digest(snapshot)


def member_digests(archive: Path) -> dict[str, str]:
    """SHA-256 of every member by name, reading the whole archive.

    Members are read in order, never sought back to, so an hour costs one
    pass of decompression. A corrupt or truncated archive raises
    tarfile.TarError, lzma.LZMAError or EOFError.
    """
    with tarfile.open(archive, 'r:xz') as tar:
        return {member.name: _digest(tar.extractfile(member)) for member in tar}


def write_archive(part: Path, base: Path | None, snapshots: list[Path]) -> None:
    """Write `part` with the members of `base` and `snapshots`, by name.

    The members of `base` are streamed from it, never held in memory: an
    hour is ~45 MB raw against the packer's MemoryMax=160M. Snapshots keep
    their names and mtimes. `part` is fsynced before this returns.
    """
    with part.open('wb') as raw:
        with (
            tarfile.open(fileobj=raw, mode='w:xz', preset=XZ_PRESET) as out,
            _opened(base) as old,
        ):
            _add_members(
                out,
                old,
                sorted(snapshots, key=lambda path: path.name),
            )
        raw.flush()
        os.fsync(raw.fileno())


def fsync_dir(directory: Path) -> None:
    """Make a rename into `directory` survive a power cut."""
    fd = os.open(directory, os.O_RDONLY)
    os.fsync(fd)
    os.close(fd)


def _opened(archive: Path | None):
    if archive is None:
        return contextlib.nullcontext()
    return tarfile.open(archive, 'r:xz')


def _add_members(out, old, pending: list[Path]) -> None:
    """Merge the old archive's members and the new files, both by name."""
    for member in old or ():
        while pending and pending[0].name < member.name:
            path = pending.pop(0)
            out.add(path, arcname=path.name, recursive=False)
        out.addfile(member, old.extractfile(member))
    for path in pending:
        out.add(path, arcname=path.name, recursive=False)


def _digest(stream) -> str:
    return hashlib.file_digest(stream, 'sha256').hexdigest()
