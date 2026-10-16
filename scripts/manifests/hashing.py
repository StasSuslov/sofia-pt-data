"""Streaming SHA256 and tolerant JSONL reading for day files."""

import hashlib
import json
from collections.abc import Iterable, Iterator
from functools import partial
from pathlib import Path

from config import open_maybe_gzip

READ_CHUNK_BYTES = 1024 * 1024

_MALFORMED = object()


def sha256_of(path: Path) -> tuple[str, int]:
    """Return (hexdigest, byte_count) of the file's UNCOMPRESSED content.

    A manifest describes the uncompressed day file even when the local copy is
    stored gzipped (deploy/sofia-compress.service may have run on the VPS
    before scripts/fetch_data.sh pulled it). The byte count is taken from the
    same streaming read used to hash, rather than a second full read (or a
    bare .stat().st_size, which for a .gz file would report the *compressed*
    size — exactly the number this must not report).

    Args:
        path: Plain or gzipped day file.

    Returns:
        The hex digest and the uncompressed size in bytes.
    """
    digest = hashlib.sha256()
    size = 0
    with open_maybe_gzip(path, 'rb') as day_file:
        for chunk in iter(partial(day_file.read, READ_CHUNK_BYTES), b''):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def _nonblank(lines: Iterable[str]) -> Iterator[str]:
    for raw in lines:
        stripped = raw.strip()
        if stripped:
            yield stripped


def _decode(line: str) -> object:
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return _MALFORMED


def load_jsonl(path: Path) -> tuple[list[dict], int]:
    """Parse a newline-delimited JSON file, tolerating a malformed line.

    Returns (records, malformed_line_count).

    A torn trailing line is routine here, not exotic — scripts/fetch_data.sh
    can run via scheduled_fetch.sh every couple of hours while collect.py is
    still actively appending to the file being pulled, so an rsync can land
    mid-write. A manifest generator whose whole purpose is detecting
    corruption should report corruption, not die on it or silently drop it
    without a trace — the malformed count is surfaced in the manifest by
    build_manifest() below.

    Args:
        path: Plain or gzipped JSONL file.

    Returns:
        The parsed records and the number of lines that failed to parse.
    """
    with open_maybe_gzip(path, 'rt', encoding='utf-8') as day_file:
        decoded = [_decode(line) for line in _nonblank(day_file)]
    records = [record for record in decoded if record is not _MALFORMED]
    return records, len(decoded) - len(records)
