"""Writing the two dataset zips and their SHA256SUMS.txt."""

import zipfile
from functools import partial
from pathlib import Path

from config import open_maybe_gzip
from datasetzip.files import READ_CHUNK_BYTES, sha256_of


def stream_into_zip(
    archive: zipfile.ZipFile,
    src_path: Path,
    arcname: str,
) -> None:
    """Write src_path into the archive under arcname, gunzipping if needed.

    Decompresses transparently if it's stored gzipped on disk. Streamed in
    1 MiB chunks on both sides — some day files are 145 MB and the whole
    range is roughly 500 MB uncompressed, too much to hold whole in memory
    even once.

    Args:
        archive: Open zip being written.
        src_path: Plain or gzipped source file.
        arcname: Member name inside the zip.
    """
    with (
        open_maybe_gzip(src_path, 'rb') as src,
        archive.open(arcname, 'w') as dst,
    ):
        for chunk in iter(partial(src.read, READ_CHUNK_BYTES), b''):
            dst.write(chunk)


def _pack_day(archive: zipfile.ZipFile, day: dict) -> None:
    date_str = day['date']
    stream_into_zip(archive, day['data_path'], f'{date_str}.jsonl')
    if day['polls_path'] is not None:
        stream_into_zip(
            archive,
            day['polls_path'],
            f'{date_str}.polls.jsonl',
        )
    # A real file already on disk, unlike the day files above:
    # ZipFile.write() streams it internally, no separate handling needed.
    archive.write(day['manifest_path'], arcname=f'{date_str}.manifest.json')


def pack_rt_zip(verified_days: list[dict], out_path: Path) -> None:
    """Write the RT zip: every day's decompressed files, in range order.

    Args:
        verified_days: Output of verify_day() for each packed day.
        out_path: Zip file to create.
    """
    with zipfile.ZipFile(
        out_path,
        'w',
        compression=zipfile.ZIP_DEFLATED,
    ) as archive:
        for day in verified_days:
            _pack_day(archive, day)


def pack_static_zip(snapshots: list[dict], out_path: Path) -> None:
    """Write the static zip: each snapshot beside its manifest, stored.

    Args:
        snapshots: Output of find_static_snapshots().
        out_path: Zip file to create.
    """
    with zipfile.ZipFile(
        out_path,
        'w',
        compression=zipfile.ZIP_STORED,
    ) as archive:
        for snap in snapshots:
            archive.write(snap['zip_path'], arcname=snap['zip_path'].name)
            archive.write(
                snap['manifest_path'],
                arcname=snap['manifest_path'].name,
            )


def write_sha256sums(out_dir: Path, artifact_paths: list[Path]) -> Path:
    r"""Write SHA256SUMS.txt in sha256sum(1) text-mode format.

    Lines are "<hex>  <name>\n" with filenames bare (no directory component)
    so `sha256sum -c SHA256SUMS.txt` works unmodified from inside out_dir,
    matching the task's own acceptance check.

    Args:
        out_dir: Directory to write SHA256SUMS.txt into.
        artifact_paths: Files to checksum, in output order.

    Returns:
        The path of SHA256SUMS.txt.
    """
    lines = []
    for path in artifact_paths:
        digest, _size = sha256_of(path)
        lines.append(f'{digest}  {path.name}\n')
    sums_path = out_dir / 'SHA256SUMS.txt'
    sums_path.write_text(''.join(lines), encoding='utf-8')
    return sums_path
