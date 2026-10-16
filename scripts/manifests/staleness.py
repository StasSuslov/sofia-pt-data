"""Manifest schema version, reading and carry-over of earlier manifests."""

import json
from pathlib import Path

# Bump whenever the set of fields build_manifest() writes changes, so old
# manifests are rewritten once instead of keeping a format nothing produces
# any more. Without this the only staleness signal is mtime, and mtime does
# not move when a format changes: it does not even move when the day file
# itself is gzipped on the VPS, because gzip preserves the original's
# timestamp. That is how "compressed" reached the 2026-08-31 manifest and
# never reached 2026-08-27's or 2026-08-28's, which had already been
# compressed and so looked untouched forever. A one-off --force clears that
# symptom for one field; a version the writer and the reader both know
# clears it for every future field.
#
# 1: first explicitly versioned format. A manifest with no schema_version at
#    all predates this and is regenerated on sight.
MANIFEST_SCHEMA_VERSION = 2

# Written by scripts/verify_remote_checksums.py after the fact, not by
# build_manifest(), so a regeneration would otherwise silently drop a
# recorded VPS comparison and force it to be redone. Carried over only when
# the checksums it vouched for are unchanged (see carry_remote_verification()).
REMOTE_VERIFICATION_FIELDS = (
    'remote_verified',
    'remote_verified_at',
    'remote_verify_note',
)

_VOUCHED_HASHES = ('data_sha256', 'polls_sha256')


def read_manifest(manifest_path: Path) -> dict | None:
    """Return the manifest already on disk, or None if missing or unreadable.

    A manifest that will not parse is not a manifest to build on: both callers
    treat None as "regenerate".

    Args:
        manifest_path: Path of the <date>.manifest.json file.

    Returns:
        The parsed manifest, or None.
    """
    try:
        return json.loads(manifest_path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None


def _hashes_moved(manifest: dict, previous: dict) -> bool:
    return any(
        previous.get(key) != manifest.get(key) for key in _VOUCHED_HASHES
    )


def carry_remote_verification(manifest: dict, previous: dict | None) -> dict:
    """Move remote-check fields from the replaced manifest onto the new one.

    These are scripts/verify_remote_checksums.py's fields, carried over only
    while the checksums they vouched for still hold. "This day matched the
    VPS" is a claim about a specific pair of SHA256s; carrying it across a
    manifest whose hashes moved would restate it about bytes it never saw.
    When the hashes differ, the fields are left off and the next verify run
    records the answer again.

    A recorded failure (remote_verified false) is never carried either, even
    over identical hashes. Finding 19: a pull copied 2026-09-23.jsonl at 56%
    of the day, the manifest was built over that copy and the check wrote
    false; the next pull brought the complete file, but regeneration over the
    same bytes would have copied the false forward and verify, which skips any
    manifest holding a value, would never have looked again. Dropping it makes
    the regenerated manifest pending, so every run re-reads the VPS: a repaired
    file turns true, a persisting mismatch raises its exit code again.

    Args:
        manifest: The freshly built manifest.
        previous: The manifest being replaced, if any.

    Returns:
        The new manifest, with the remote-check fields added when they hold.
    """
    if not previous or previous.get('remote_verified') is not True:
        return manifest
    if _hashes_moved(manifest, previous):
        return manifest
    manifest.update({
        field: previous[field]
        for field in REMOTE_VERIFICATION_FIELDS
        if field in previous
    })
    return manifest


def _schema_and_remote_are_current(existing: dict | None) -> bool:
    if existing is None:
        return False
    if existing.get('schema_version') != MANIFEST_SCHEMA_VERSION:
        return False
    # A failed remote check says the local bytes are wrong, and rsync -a
    # carries the server's mtime, so a repaired file can look older than the
    # manifest that condemned it (finding 19). mtime cannot vouch for it.
    return existing.get('remote_verified') is not False


def manifest_is_current(
    manifest_path: Path,
    data_path: Path,
    polls_path: Path,
) -> bool:
    """Say whether an existing manifest can be kept as it is.

    True when an existing manifest is already newer than every input that
    could change it AND was written by the current schema version. A closed
    past day's data and heartbeat files never change again, so re-hashing
    them (SHA256 over a file already 100+ MB, on a cadence that runs every
    couple of hours) is pure waste once this holds (see CLAUDE.md section
    on why generate_manifest.py needs wiring into the fetch pipeline without
    regenerating the whole archive each time).

    The version check is what makes that skip safe. mtime alone answers "did
    the inputs change", never "did the output format change", and the two
    are not the same question. See MANIFEST_SCHEMA_VERSION for the field
    that reached one day's manifest and no other because of it.

    data_path/polls_path must already be resolved to whichever copy (plain
    or .gz) actually exists on disk — see config.resolve_day_file() — so a
    day whose only change was being gzipped in place still compares against
    a real mtime instead of a nonexistent uncompressed path.

    Args:
        manifest_path: Path of the <date>.manifest.json file.
        data_path: The resolved day file.
        polls_path: The resolved heartbeat file (may not exist).

    Returns:
        True when the manifest is up to date.
    """
    if not _schema_and_remote_are_current(read_manifest(manifest_path)):
        return False
    manifest_mtime = manifest_path.stat().st_mtime
    if data_path.stat().st_mtime > manifest_mtime:
        return False
    return not (
        polls_path.exists() and polls_path.stat().st_mtime > manifest_mtime
    )


def should_skip(
    manifest_path: Path,
    data_path: Path,
    polls_path: Path,
    *,
    force: bool,
) -> bool:
    """Say whether this day's manifest regeneration can be skipped.

    Args:
        manifest_path: Path of the <date>.manifest.json file.
        data_path: The resolved day file.
        polls_path: The resolved heartbeat file (may not exist).
        force: Regenerate even when the manifest is current.

    Returns:
        True when the manifest is current and force is off.
    """
    return not force and manifest_is_current(
        manifest_path,
        data_path,
        polls_path,
    )
