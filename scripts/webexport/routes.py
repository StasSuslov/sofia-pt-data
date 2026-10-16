"""Route metadata per shape, from a GTFS zip."""

import csv
import io
import zipfile
from collections import defaultdict
from pathlib import Path

_ROUTE_ID = 'route_id'
_ROUTE_TYPE = 'route_type'


def _csv_rows(archive: zipfile.ZipFile, name: str):
    """Yield the rows of one CSV member of a GTFS zip."""
    with archive.open(name) as member:
        yield from csv.DictReader(
            io.TextIOWrapper(member, encoding='utf-8-sig'),
        )


def _routes_table(archive: zipfile.ZipFile) -> dict:
    """Return route_id -> (display name, route_type) from routes.txt."""
    return {
        row[_ROUTE_ID]: (
            row.get('route_short_name') or row[_ROUTE_ID],
            row[_ROUTE_TYPE],
        )
        for row in _csv_rows(archive, 'routes.txt')
    }


def _shape_routes(archive: zipfile.ZipFile, routes: dict) -> dict:
    """Return shape_id -> {route_id} from trips.txt."""
    shape_routes = defaultdict(set)
    for row in _csv_rows(archive, 'trips.txt'):
        if row['shape_id'] and row[_ROUTE_ID] in routes:
            shape_routes[row['shape_id']].add(row[_ROUTE_ID])
    return shape_routes


def _route_entry(route_ids: set, routes: dict) -> dict:
    """Build the route metadata of one shape."""
    ids = sorted(route_ids)
    types = {routes[route_id][1] for route_id in ids}
    return {
        'route_ids': ids,
        'route_names': sorted({routes[route_id][0] for route_id in ids}),
        # A mixed-type shape would make a type filter lie, so it is left
        # unset rather than resolved by picking a winner.
        _ROUTE_TYPE: int(types.pop()) if len(types) == 1 else None,
    }


def load_route_info(gtfs_zip_path: Path) -> dict:
    """Map shape_id to its routes.

    The value is {"route_ids": [...], "route_names": [...], "route_type": int}.

    Feature 3 in CLAUDE.md's frontend priorities is a transport-type filter,
    and shape_id alone cannot answer "is this a tram or a bus". Twelve shapes
    in this feed are served by more than one route, so route_ids is a list
    rather than a single value; no shape mixes route types (checked against
    the feed), so route_type stays a single int and a filter can key on it
    directly. GTFS route_type: 0 tram, 1 metro, 3 bus, 11 trolleybus.
    """
    with zipfile.ZipFile(gtfs_zip_path) as archive:
        routes = _routes_table(archive)
        shape_routes = _shape_routes(archive, routes)
    return {
        shape_id: _route_entry(route_ids, routes)
        for shape_id, route_ids in shape_routes.items()
    }
