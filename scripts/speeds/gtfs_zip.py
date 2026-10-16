"""Reading the CSV members of a static GTFS zip."""

import csv
import io
import zipfile
from collections import defaultdict
from operator import itemgetter
from pathlib import Path


def iter_csv_rows(feed: zipfile.ZipFile, name: str, encoding: str = 'utf-8'):
    """Yield the rows of one CSV member of an open GTFS zip as dicts."""
    with feed.open(name) as member:
        yield from csv.DictReader(io.TextIOWrapper(member, encoding=encoding))


def _shape_points(feed: zipfile.ZipFile) -> dict:
    """Map shape_id to its (sequence, lat, lon) rows, in file order."""
    shape_points = defaultdict(list)
    for row in iter_csv_rows(feed, 'shapes.txt'):
        shape_points[row['shape_id']].append((
            int(row['shape_pt_sequence']),
            float(row['shape_pt_lat']),
            float(row['shape_pt_lon']),
        ))
    return shape_points


def _ordered_coords(shape_points: dict) -> dict:
    """Map shape_id to its (lat, lon) points ordered by shape_pt_sequence."""
    return {
        shape_id: [
            (lat, lon) for _, lat, lon in sorted(rows, key=itemgetter(0))
        ]
        for shape_id, rows in shape_points.items()
    }


def read_static_tables(gtfs_zip_path: Path) -> tuple:
    """Return (trips.txt rows, shape_id -> ordered (lat, lon)) of one feed."""
    with zipfile.ZipFile(gtfs_zip_path) as feed:
        trip_rows = list(iter_csv_rows(feed, 'trips.txt'))
        return trip_rows, _ordered_coords(_shape_points(feed))
