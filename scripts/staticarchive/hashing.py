"""Content hashing of a GTFS zip, blind to how it is packaged."""

import functools
import hashlib
import zipfile
from pathlib import Path

_CHUNK_BYTES = 1024 * 1024
_DIGEST_SHOWN = 12


def member_hashes(zip_path: Path) -> list:
    """Hash every real file in the zip.

    Directory entries are skipped: they carry no content of their own, only
    a name and a timestamp, so including them would reintroduce exactly the
    modification-time sensitivity content_hash exists to avoid.

    Args:
        zip_path: The zip to read.

    Returns:
        (member_name, sha256_hex_of_decompressed_bytes) sorted by name.
    """
    with zipfile.ZipFile(zip_path) as archive:
        return sorted(
            (
                entry.filename,
                hashlib.sha256(archive.read(entry.filename)).hexdigest(),
            )
            for entry in archive.infolist()
            if not entry.is_dir()
        )


def digest_of(pairs: list) -> str:
    """Hash (member_name, digest) pairs into one content hash."""
    joined = '\n'.join(f'{name}:{digest}' for name, digest in pairs)
    return hashlib.sha256(joined.encode('utf-8')).hexdigest()


def content_hash(zip_path: Path) -> str:
    """SHA256 over the zip's member names and decompressed content.

    Ignores every other detail of how the zip is packaged (member order,
    per-member modification time, compression level). See the module
    docstring of archive_static_feed for why that distinction is the entire
    point of this function.
    """
    return digest_of(member_hashes(zip_path))


def sha256_of_file(path: Path) -> str:
    """Plain sha256 of the zip's own bytes.

    Streamed rather than read whole so an 18.7 MB file doesn't sit fully in
    memory twice over. Kept separate from content_hash(): this one changes
    on every rebuild even when content_hash() doesn't, which is exactly the
    point, so the manifest records both rather than only the one that would
    make a rebuild look like a real change.
    """
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(functools.partial(source.read, _CHUNK_BYTES), b''):
            digest.update(chunk)
    return digest.hexdigest()


def short_digest(digest: str) -> str:
    """Return the first 12 hex digits of a hash with an ellipsis, for logs."""
    head = digest[:_DIGEST_SHOWN]
    return f'{head}...'
