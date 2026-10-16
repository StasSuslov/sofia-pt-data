"""Hashing and manifest loading shared by the packaging checks."""

import hashlib
import json
from functools import partial
from pathlib import Path

from config import open_maybe_gzip

READ_CHUNK_BYTES = 1024 * 1024


def sha256_of(path: Path) -> tuple[str, int]:
    """Return (hexdigest, byte_count) of `path`'s UNCOMPRESSED content.

    Streamed in 1 MiB chunks so a 150 MB day file is never held whole in
    memory. Works unchanged for a plain file (static zips, already-flat day
    files) since open_maybe_gzip() only decompresses when the suffix is
    literally ".gz".

    Args:
        path: Plain or gzipped file.

    Returns:
        The hex digest and the uncompressed size in bytes.
    """
    digest = hashlib.sha256()
    size = 0
    with open_maybe_gzip(path, 'rb') as src:
        for chunk in iter(partial(src.read, READ_CHUNK_BYTES), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def load_manifest(path: Path) -> dict:
    """Return the manifest at `path`, or raise a ValueError naming it.

    A missing or unparseable manifest is grounds to abort the whole run,
    never to guess or skip — "absence of proof is not proof".

    Args:
        path: Manifest file.

    Returns:
        The parsed manifest.

    Raises:
        ValueError: The manifest is missing or not valid JSON.
    """
    if not path.exists():
        raise ValueError(f'missing manifest: {path}')
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except json.JSONDecodeError as exc:
        raise ValueError(
            f'manifest is not valid JSON: {path} ({exc})',
        ) from exc


def check_hash(path: Path, expected: str | None, manifest_name: str) -> None:
    """Raise a ValueError unless `path` hashes to the manifest's value.

    Args:
        path: File whose uncompressed content is hashed.
        expected: The digest the manifest recorded.
        manifest_name: Manifest file name, quoted in the error.

    Raises:
        ValueError: The computed digest differs from `expected`.
    """
    computed, _ = sha256_of(path)
    if computed != expected:
        raise ValueError(
            f'hash mismatch for {path.name}: computed {computed}, '
            f'{manifest_name} says {expected}',
        )
