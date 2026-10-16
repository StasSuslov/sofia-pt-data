"""Day-range arithmetic and per-day verification against manifests."""

import datetime as dt
from pathlib import Path

from config import resolve_day_file
from datasetzip.files import check_hash, load_manifest

# Kept in step with generate_manifest.py's constant of the same name by hand
# rather than imported, for the reason given in package_dataset.py. A bump
# there makes every day here fail loudly until the manifests are regenerated,
# which is the safe direction to fail in.
MANIFEST_SCHEMA_VERSION = 2


def _parse_iso(text: str) -> dt.date:
    try:
        return dt.date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f'invalid date: {exc}') from exc


def date_range(start_date: str, end_date: str) -> list[str]:
    """List the ISO dates from `start_date` to `end_date`, inclusive.

    Args:
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD.

    Returns:
        The ISO dates in order.

    Raises:
        ValueError: A date is invalid or the range is reversed.
    """
    start = _parse_iso(start_date)
    end = _parse_iso(end_date)
    if end < start:
        raise ValueError(
            f'end date {end_date} is before start date {start_date}',
        )
    span = (end - start).days
    return [
        (start + dt.timedelta(days=offset)).isoformat()
        for offset in range(span + 1)
    ]


def _require_current_and_closed(
    manifest: dict,
    manifest_path: Path,
    date_str: str,
) -> None:
    # Before trusting any field below, including day_in_progress: an older
    # manifest may simply not have the field, and an absent day_in_progress
    # reads as "closed, go ahead". generate_manifest.py treats a version
    # mismatch as grounds for regeneration; here it is grounds for refusing.
    if manifest.get('schema_version') != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f'{manifest_path.name} has schema_version '
            f'{manifest.get("schema_version")!r}, '
            f'expected {MANIFEST_SCHEMA_VERSION} '
            '— regenerate it with scripts/generate_manifest.py '
            'before packaging',
        )

    # A day still being written is a moving target: hashing it now proves
    # nothing about what a Zenodo download will contain later. A "closed
    # date range" excludes it by definition.
    if manifest.get('day_in_progress'):
        raise ValueError(
            f'{date_str} is still in progress (day_in_progress=true in '
            f'{manifest_path.name}) — refusing to package an open day',
        )


def _verify_polls(
    data_dir: Path,
    date_str: str,
    manifest: dict,
    manifest_path: Path,
) -> Path | None:
    if not manifest.get('polls_file'):
        return None
    polls_path = resolve_day_file(data_dir, date_str, '.polls.jsonl')
    if not polls_path.exists():
        raise ValueError(
            f'missing heartbeat file for {date_str}: {polls_path} (or '
            f'{polls_path.name}.gz), but {manifest_path.name} declares one',
        )
    check_hash(polls_path, manifest.get('polls_sha256'), manifest_path.name)
    return polls_path


def verify_day(data_dir: Path, date_str: str) -> dict:
    """Verify one day's files hash to what <date>.manifest.json recorded.

    Checks the data file (and heartbeat file, if its manifest says there is
    one). Raises ValueError naming the offending file on any mismatch or
    absence. Returns the resolved paths and parsed manifest for the packing
    step, so packing never has to re-derive anything verification already
    settled.

    Args:
        data_dir: Directory of the day files.
        date_str: Day to verify, YYYY-MM-DD.

    Returns:
        The date, manifest and the resolved data and heartbeat paths.

    Raises:
        ValueError: The manifest is stale, the day is open, a file is missing
            or a hash does not match.
    """
    manifest_path = data_dir / f'{date_str}.manifest.json'
    manifest = load_manifest(manifest_path)
    _require_current_and_closed(manifest, manifest_path, date_str)

    data_path = resolve_day_file(data_dir, date_str, '.jsonl')
    if not data_path.exists():
        raise ValueError(
            f'missing day file for {date_str}: {data_path} '
            f'(or {data_path.name}.gz)',
        )
    check_hash(data_path, manifest.get('data_sha256'), manifest_path.name)

    return {
        'date': date_str,
        'manifest_path': manifest_path,
        'manifest': manifest,
        'data_path': data_path,
        'polls_path': _verify_polls(
            data_dir,
            date_str,
            manifest,
            manifest_path,
        ),
    }
