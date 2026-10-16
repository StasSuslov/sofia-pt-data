"""Static-feed metadata: feed_info row, file hash, shape-id statistics."""

import hashlib
import zipfile
from collections import defaultdict
from pathlib import Path

from speeds.gtfs_zip import iter_csv_rows


def load_feed_info(gtfs_zip_path: Path) -> dict:
    """Best-effort feed_info.txt row, for stamping run metadata.

    Best-effort feed_info.txt row, for stamping run metadata — absent in
    some GTFS feeds, so this must not be load-bearing for anything else.
    """
    try:
        with zipfile.ZipFile(gtfs_zip_path) as feed:
            rows = iter_csv_rows(feed, 'feed_info.txt')
            first_row = next(rows, {})
            rows.close()
            return first_row
    except (KeyError, zipfile.BadZipFile, StopIteration):
        return {}


def sha256_of_file(path: Path) -> str:
    """Plain sha256 of a static feed zip.

    Plain sha256 of a static feed zip. Not generate_manifest.py's
    sha256_of(): that one exists to hash a day file transparently through
    optional gzip compression (deploy/sofia-compress.service); a static zip
    is never stored gzipped, so that indirection has nothing to do here.
    """
    with path.open('rb') as feed_file:
        return hashlib.file_digest(feed_file, 'sha256').hexdigest()


def count_multi_geometry_shape_ids(shape_ids_by_key: dict) -> int:
    """Count shape_ids published under more than one geometry.

    How many distinct bare shape_ids appear under more than one distinct
    geometry key among the feeds loaded in one run. This is the number that
    produced the 2026-08-31 finding — 32 shape_ids reused across a
    republished feed with different geometry underneath them — and it
    belongs in typical_weekday.json permanently, not only in a one-off
    investigation.
    """
    keys_by_shape_id = defaultdict(set)
    for key, shape_ids in shape_ids_by_key.items():
        for shape_id in shape_ids:
            keys_by_shape_id[shape_id].add(key)
    return sum(1 for keys in keys_by_shape_id.values() if len(keys) > 1)


def count_multi_id_geometries(shape_ids_by_key: dict) -> int:
    """Count geometries published under more than one shape_id.

    The mirror of count_multi_geometry_shape_ids(): how many distinct
    geometries are published under more than one shape_id. This is the
    2026-09-02 finding — A4500-A4503 shipped as byte-identical copies of
    A1192/A3949/A1221/A2710 with the originals left in the feed — and it is
    exactly what the shape_id in the old aggregation key turned into a
    permanent split. Zero here means the publisher's ids and its geometries
    agree; a rising number means it is churning identifiers again.
    """
    return sum(
        1 for shape_ids in shape_ids_by_key.values() if len(shape_ids) > 1
    )
