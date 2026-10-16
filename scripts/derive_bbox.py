#!/usr/bin/env python3
r"""
Derive config.py's NETWORK_BBOX from a GTFS Static feed.

The derivation keeps the bounds auditable and reproducible rather than an
assertion in a comment.

The bbox was originally hand-picked and turned out to be
narrower than the real network on all four sides, silently discarding ~11%
of routes as if they were the GTFS-RT teleportation bug the filter exists to
catch (see METHODOLOGY.md). The corrected bbox was derived from this
exact computation; this script exists so that derivation can be re-run and
checked rather than taken on trust, and re-applied if the network extent
changes (a new route added far from the current edges, a feed update, a
different city's feed for the multi-city pipeline).

Usage:
    python scripts/derive_bbox.py data/sofia/static/gtfs_2026-08-27.zip
    python scripts/derive_bbox.py data/sofia/static/gtfs_2026-08-27.zip \
        --margin 0.04

Note: the values actually committed in config.py's NETWORK_BBOX were rounded
by hand from an earlier run of this same computation for readability — don't
expect this script's suggested values to match it to the decimal place, only
to be in the same ballpark and to cover the same measured extent.
"""

import argparse
import csv
import io
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path

# (filename, lat column, lon column) — both files carry real service extent;
# stops.txt alone can miss shape geometry that runs past the last stop.
GTFS_FILES = (
    ('stops.txt', 'stop_lat', 'stop_lon'),
    ('shapes.txt', 'shape_pt_lat', 'shape_pt_lon'),
)

DEFAULT_MARGIN_DEG = 0.04

# Printed key order, and the direction the margin pushes each edge.
_BBOX_KEYS = ('lat_min', 'lat_max', 'lon_min', 'lon_max')
_MARGIN_SIGNS = (-1, 1, -1, 1)
_GTFS_FILENAMES = tuple(gtfs_file[0] for gtfs_file in GTFS_FILES)


def _points(
    archive: zipfile.ZipFile,
    zip_name: str,
    filename: str,
    lat_field: str,
    lon_field: str,
) -> Iterator[tuple[float, float]]:
    """Yield (lat, lon) per row of one GTFS file, warning if it is absent."""
    if filename not in archive.namelist():
        sys.stderr.write(
            f'[WARN] {filename} not found in {zip_name}, skipping\n',
        )
        return
    with archive.open(filename) as member:
        for row in csv.DictReader(io.TextIOWrapper(member, encoding='utf-8')):
            yield float(row[lat_field]), float(row[lon_field])


def extent_from_gtfs_zip(zip_path: Path) -> tuple[float, float, float, float]:
    """Measure the network extent across stops.txt + shapes.txt.

    Args:
        zip_path: The GTFS Static zip to read.

    Returns:
        (lat_min, lat_max, lon_min, lon_max).

    Raises:
        ValueError: Neither file yielded a coordinate.
    """
    points = _read_points(zip_path)
    if not points:
        raise ValueError(
            f'No coordinates found in {zip_path} — checked '
            f'{list(_GTFS_FILENAMES)}',
        )

    lats = [point[0] for point in points]
    lons = [point[1] for point in points]
    return min(lats), max(lats), min(lons), max(lons)


def _read_points(zip_path: Path) -> list[tuple[float, float]]:
    with zipfile.ZipFile(zip_path) as archive:
        return [
            point
            for gtfs_file in GTFS_FILES
            for point in _points(archive, zip_path.name, *gtfs_file)
        ]


def _print_extent(extent: tuple[float, float, float, float]) -> None:
    lat_min, lat_max, lon_min, lon_max = extent
    sys.stdout.write(
        'Measured network extent (no margin): '
        f'lat {lat_min:.4f}-{lat_max:.4f}, lon {lon_min:.4f}-{lon_max:.4f}\n',
    )


def _print_suggestion(
    extent: tuple[float, float, float, float],
    margin: float,
) -> None:
    sys.stdout.write(f'\nWith {margin}° margin, suggested NETWORK_BBOX:\n')
    for key, edge, sign in zip(_BBOX_KEYS, extent, _MARGIN_SIGNS, strict=True):
        padded = edge + sign * margin
        sys.stdout.write(f'    "{key}": {padded:.2f},\n')


def main() -> None:
    """Print the measured extent and the bbox suggested for the profile."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        'gtfs_zip',
        type=Path,
        help='Path to a GTFS Static zip (needs stops.txt and/or shapes.txt)',
    )
    parser.add_argument(
        '--margin',
        type=float,
        default=DEFAULT_MARGIN_DEG,
        help=(
            'Degrees of margin added on each side for GPS drift near the '
            'edges (default: 0.04, ~4km)'
        ),
    )
    args = parser.parse_args()
    extent = extent_from_gtfs_zip(args.gtfs_zip)
    _print_extent(extent)
    _print_suggestion(extent, args.margin)


if __name__ == '__main__':
    main()
