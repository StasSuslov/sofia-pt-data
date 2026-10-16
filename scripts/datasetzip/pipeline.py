"""Verify everything first, then write the zips, docs and checksums."""

import shutil
from pathlib import Path

from datasetzip.days import date_range, verify_day
from datasetzip.pack import pack_rt_zip, pack_static_zip, write_sha256sums
from datasetzip.scan import find_skipped_files, find_static_snapshots

REPO_ROOT = Path(__file__).resolve().parents[2]

# Uploaded alongside the two zips, per CLAUDE.md section 8: a dataset record
# that points at its methodology only by URL loses the link the day the repo
# moves. Copied from the repo root, checksummed with everything else.
DOC_FILES = ('METHODOLOGY.md', 'README.md')


def _existing_docs() -> list[Path]:
    docs = [REPO_ROOT / name for name in DOC_FILES]
    missing_docs = [doc.name for doc in docs if not doc.exists()]
    if missing_docs:
        names = ', '.join(missing_docs)
        raise ValueError(
            f'missing file(s) the record must ship with: {names}',
        )
    return docs


def _zip_names(data_dir: Path, start_date: str, end_date: str) -> list[str]:
    # e.g. data/sofia -> "sofia" — CLAUDE.md D2, no city literal here
    prefix = Path(data_dir).name
    return [
        f'{prefix}-rt_{start_date}_{end_date}.zip',
        f'{prefix}-gtfs-static_{start_date}_{end_date}.zip',
    ]


def _copy_doc(doc: Path, out_dir: Path) -> Path:
    return Path(shutil.copy2(doc, out_dir / doc.name))


def _write_artifacts(
    out_dir: Path,
    zip_names: list[str],
    verified_days: list[dict],
    snapshots: list[dict],
    docs: list[Path],
) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    rt_zip_path, static_zip_path = (out_dir / name for name in zip_names)

    pack_rt_zip(verified_days, rt_zip_path)
    pack_static_zip(snapshots, static_zip_path)
    doc_paths = [_copy_doc(doc, out_dir) for doc in docs]
    return {
        'rt_zip_path': rt_zip_path,
        'static_zip_path': static_zip_path,
        'doc_paths': doc_paths,
        'sums_path': write_sha256sums(
            out_dir,
            [rt_zip_path, static_zip_path, *doc_paths],
        ),
    }


def run(
    data_dir: Path,
    static_dir: Path,
    start_date: str,
    end_date: str,
    out_dir: Path,
) -> dict:
    """Verify every input for [start_date, end_date], then pack it.

    Verification is complete (every day, every in-range static snapshot)
    before a single byte is written to either zip — see the abort rules in
    package_dataset.py's docstring. Raises ValueError on any verification
    failure; nothing is packed in that case.

    Args:
        data_dir: Directory of the day files, e.g. data/sofia.
        static_dir: Directory of static snapshots, e.g. data/sofia/static.
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD.
        out_dir: Directory to write the artifacts into (created if missing).

    Returns:
        The report data main() prints.

    Raises:
        ValueError: Any verification failure.
    """
    verified_days = [
        verify_day(data_dir, day) for day in date_range(start_date, end_date)
    ]
    snapshots = find_static_snapshots(static_dir, start_date, end_date)
    skipped = find_skipped_files(data_dir, verified_days, start_date, end_date)
    docs = _existing_docs()

    return {
        'verified_days': verified_days,
        'snapshots': snapshots,
        'skipped': skipped,
        **_write_artifacts(
            out_dir,
            _zip_names(data_dir, start_date, end_date),
            verified_days,
            snapshots,
            docs,
        ),
    }
