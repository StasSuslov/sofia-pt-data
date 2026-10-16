"""The manifest stored next to each snapshot, and its report line."""

import datetime as dt
import json
from pathlib import Path

from staticarchive.hashing import short_digest


def build_manifest(
    target_path: Path,
    source_url: str,
    downloaded_at: dt.datetime,
    measured: dict,
    feed_info: dict,
) -> dict:
    """Build the manifest dict stored next to a snapshot.

    Mirrors scripts/generate_manifest.py's naming conventions (a
    <name>_sha256 field, byte size, a plain "file" name) so the two kinds of
    manifest read the same way even though they describe different things.

    Args:
        target_path: The saved snapshot.
        source_url: Where it was downloaded from.
        downloaded_at: Capture time.
        measured: zip_sha256, content_hash, size_bytes, member_count, in
            that order.
        feed_info: The feed_info.txt row, possibly empty.

    Returns:
        The manifest.
    """
    return {
        'file': target_path.name,
        'source_url': source_url,
        'downloaded_at': downloaded_at.astimezone(dt.UTC).isoformat(),
        **measured,
        'feed_version': feed_info.get('feed_version'),
        'feed_start_date': feed_info.get('feed_start_date'),
        'feed_end_date': feed_info.get('feed_end_date'),
        'feed_publisher_name': feed_info.get('feed_publisher_name'),
    }


def write_manifest(target_path: Path, manifest: dict) -> None:
    """Write the manifest next to its snapshot as <name>.manifest.json."""
    manifest_path = target_path.with_suffix('.manifest.json')
    manifest_path.write_text(
        f'{json.dumps(manifest, indent=2, ensure_ascii=False)}\n',
        encoding='utf-8',
    )


def saved_line(target_name: str, reason: str, measured: dict) -> str:
    """Format the stdout line that reports a saved snapshot."""
    content_head = short_digest(measured['content_hash'])
    zip_head = short_digest(measured['zip_sha256'])
    return (
        f'Saved new snapshot {target_name} ({reason}). '
        f'content_hash={content_head} zip_sha256={zip_head}\n'
    )
