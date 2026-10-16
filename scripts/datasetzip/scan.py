"""Static-snapshot discovery and the report of files left out of the zips."""

import re
from collections.abc import Iterator
from pathlib import Path

from datasetzip.files import check_hash, load_manifest

# Same pattern generate_manifest.py/archive_static_feed.py use for static
# snapshot filenames.
STATIC_SNAPSHOT_RE = re.compile(r'^gtfs_(\d{4}-\d{2}-\d{2})(?:T\d{4})?\.zip$')

# A stray file in data_dir whose name doesn't start with a date at all (e.g.
# .DS_Store, a *.log left by hand) gets this reason in the skip report.
NOT_ARCHIVE_FILE = 'not a recognized day/manifest/heartbeat file'

DATE_PREFIX_RE = re.compile(r'^(\d{4}-\d{2}-\d{2})\.')


def _snapshots_in_range(
    static_dir: Path,
    start_date: str,
    end_date: str,
) -> Iterator[tuple[str, Path]]:
    for path in sorted(static_dir.iterdir()):
        match = STATIC_SNAPSHOT_RE.match(path.name) if path.is_file() else None
        if match and start_date <= match.group(1) <= end_date:
            yield match.group(1), path


def find_static_snapshots(
    static_dir: Path,
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Verify every in-range static snapshot against its own manifest.

    Covers every gtfs_<date>.zip in static_dir whose date falls in
    [start_date, end_date], checked against its manifest's zip_sha256.
    Missing static snapshots for a day are normal (a new snapshot is only
    saved when the feed's content actually changes, see
    archive_static_feed.py) and not an error; a missing MANIFEST for a
    snapshot that does exist is.

    Args:
        static_dir: Directory of gtfs_<date>.zip snapshots and manifests.
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD.

    Returns:
        One dict per snapshot: date, zip_path, manifest_path, manifest.

    Raises:
        ValueError: The directory or a manifest is missing, or a hash differs.
    """
    if not static_dir.exists():
        raise ValueError(f'static directory not found: {static_dir}')

    snapshots = []
    for snap_date, zip_path in _snapshots_in_range(
        static_dir,
        start_date,
        end_date,
    ):
        manifest_path = zip_path.with_suffix('.manifest.json')
        manifest = load_manifest(manifest_path)
        check_hash(zip_path, manifest.get('zip_sha256'), manifest_path.name)
        snapshots.append({
            'date': snap_date,
            'zip_path': zip_path,
            'manifest_path': manifest_path,
            'manifest': manifest,
        })
    return snapshots


def _consumed_names(verified_days: list[dict]) -> set[str]:
    consumed = set()
    for day in verified_days:
        consumed.add(day['data_path'].name)
        consumed.add(day['manifest_path'].name)
        if day['polls_path'] is not None:
            consumed.add(day['polls_path'].name)
    return consumed


def _skip_reason(name: str, start_date: str, end_date: str) -> str:
    match = DATE_PREFIX_RE.match(name)
    if match and not start_date <= match.group(1) <= end_date:
        return (
            f'date {match.group(1)} outside requested range '
            f'{start_date}..{end_date}'
        )
    return NOT_ARCHIVE_FILE


def find_skipped_files(
    data_dir: Path,
    verified_days: list[dict],
    start_date: str,
    end_date: str,
) -> list[tuple[str, str]]:
    """List top-level files in data_dir that no zip will contain.

    Files that aren't one of the day/heartbeat/manifest files just packed —
    e.g. a stray 2026-08-27.log or .DS_Store, or a day file whose date falls
    outside the requested range. Subdirectories (static/, processed/, web/)
    are a different kind of content handled elsewhere (static_dir) or not
    part of this dataset record at all, so they're passed over silently
    rather than reported as strays.

    Args:
        data_dir: Directory of the day files.
        verified_days: Output of verify_day() for each packed day.
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD.

    Returns:
        (file name, reason) pairs in name order.
    """
    consumed = _consumed_names(verified_days)
    return [
        (path.name, _skip_reason(path.name, start_date, end_date))
        for path in sorted(data_dir.iterdir())
        if path.is_file() and path.name not in consumed
    ]
