#!/usr/bin/env python3
"""
Derive config.py's NETWORK_BBOX from a GTFS Static feed, so the bounds are
auditable and reproducible rather than an assertion in a comment.

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
    python scripts/derive_bbox.py data/sofia/static/gtfs_2026-08-27.zip --margin 0.04

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
from pathlib import Path

# (filename, lat column, lon column) — both files carry real service extent;
# stops.txt alone can miss shape geometry that runs past the last stop.
GTFS_FILES = [
    ("stops.txt", "stop_lat", "stop_lon"),
    ("shapes.txt", "shape_pt_lat", "shape_pt_lon"),
]


def extent_from_gtfs_zip(zip_path: Path) -> tuple[float, float, float, float]:
    """Returns (lat_min, lat_max, lon_min, lon_max) across stops.txt + shapes.txt."""
    lats: list[float] = []
    lons: list[float] = []
    with zipfile.ZipFile(zip_path) as z:
        names = set(z.namelist())
        for filename, lat_field, lon_field in GTFS_FILES:
            if filename not in names:
                print(f"[WARN] {filename} not found in {zip_path.name}, skipping", file=sys.stderr)
                continue
            with z.open(filename) as f:
                reader = csv.DictReader(io.TextIOWrapper(f, encoding="utf-8"))
                for row in reader:
                    lats.append(float(row[lat_field]))
                    lons.append(float(row[lon_field]))

    if not lats:
        raise ValueError(f"No coordinates found in {zip_path} — checked {[n for n, _, _ in GTFS_FILES]}")

    return min(lats), max(lats), min(lons), max(lons)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("gtfs_zip", type=Path, help="Path to a GTFS Static zip (needs stops.txt and/or shapes.txt)")
    parser.add_argument(
        "--margin", type=float, default=0.04,
        help="Degrees of margin added on each side for GPS drift near the edges (default: 0.04, ~4km)",
    )
    args = parser.parse_args()

    lat_min, lat_max, lon_min, lon_max = extent_from_gtfs_zip(args.gtfs_zip)
    print(f"Measured network extent (no margin): lat {lat_min:.4f}-{lat_max:.4f}, lon {lon_min:.4f}-{lon_max:.4f}")

    m = args.margin
    print(f"\nWith {m}° margin, suggested NETWORK_BBOX:")
    print(f'    "lat_min": {lat_min - m:.2f},')
    print(f'    "lat_max": {lat_max + m:.2f},')
    print(f'    "lon_min": {lon_min - m:.2f},')
    print(f'    "lon_max": {lon_max + m:.2f},')


if __name__ == "__main__":
    main()
