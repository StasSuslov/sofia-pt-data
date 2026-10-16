"""Per-date timetable signatures read out of one static snapshot."""

import hashlib
import zipfile
from collections import Counter, defaultdict
from pathlib import Path

from speeds.feed import KEY_HEX_CHARS
from speeds.gtfs_zip import iter_csv_rows

CSV_ENCODING = 'utf-8-sig'  # these two members may carry a BOM


def load_schedule_calendar(gtfs_zip_path: Path) -> tuple:
    """Return (services_by_date, trip_services) of one static snapshot.

    (services_by_date, trip_services) for one static snapshot:
    "YYYYMMDD" -> {service_id, ...} from calendar_dates.txt, and every
    trips.txt row as a (service_id, route_id) pair.

    This feed publishes no calendar.txt — checked against every snapshot in
    the archive — so calendar_dates.txt is not a list of exceptions to a
    weekly pattern, it is the entire calendar, one row per service per date
    (499,841 rows over 1,119 dates in the 2026-09-04 snapshot). Only
    exception_type "1" (service added) is read; "2" removes a service from a
    base calendar that does not exist here, and no snapshot has ever carried
    one.
    """
    services_by_date = defaultdict(set)
    with zipfile.ZipFile(gtfs_zip_path) as feed:
        for row in iter_csv_rows(feed, 'calendar_dates.txt', CSV_ENCODING):
            if row['exception_type'] == '1':
                services_by_date[row['date']].add(row['service_id'])
        trip_services = [
            (trip['service_id'], trip['route_id'])
            for trip in iter_csv_rows(feed, 'trips.txt', CSV_ENCODING)
        ]
    return dict(services_by_date), trip_services


def _digest(pairs: list) -> str:
    text = ';'.join(f'{route_id},{trips}' for route_id, trips in pairs)
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:KEY_HEX_CHARS]


def schedule_signature(
    services_by_date: dict,
    trip_services: list,
    date_str: str,
) -> tuple:
    """Return (signature_key, counts) for one calendar date.

    (signature_key, counts) for one calendar date read out of one static
    snapshot — the day's own snapshot (pick_snapshot_for_day), never the
    next_snapshot_after() fallback: the question here is which timetable the
    day ran under, and that is the feed it started under.

    counts is route_id -> trips scheduled that date. signature_key is the
    first 16 hex chars of a sha256 over the sorted (route_id, trip_count)
    pairs, the same content-addressing convention as build_shape_key(). It
    names exactly what the agency published that day, with no tolerance:
    which of these signatures are near enough to share a median is
    assign_schedule_periods()'s decision, and the raw key stays in the
    output per day so a reader sees the observation the grouping was made
    from, not only its result.

    Content-addressed for the same reason shape identity is: the agency
    renumbers trip_ids and service_ids between nightly rebuilds (2026-09-02,
    157 trips of four routes at 11:00 local) and a key built from those
    identifiers would declare a new timetable every time it did. Route ids
    and per-route trip counts sit still through a renumbering.

    ponytail: the ceiling is a pure retiming — the agency moves departure times
    but keeps every route's trip count identical — which hashes to the same key,
    and those two timetables would merge into one median. Detecting it needs
    stop_times.txt, 45.8 MB unpacked against trips.txt's 3.2 MB, re-read for
    every archived day on every scheduled fetch. Not worth paying until a
    retiming-only change is actually observed; it is named as a limitation in
    METHODOLOGY.md instead.
    """
    active = services_by_date.get(date_str.replace('-', ''), frozenset())
    counts = Counter(
        route_id
        for service_id, route_id in trip_services
        if service_id in active
    )
    pairs = sorted(counts.items())
    return _digest(pairs), dict(pairs)


def schedule_churn(counts: dict, ref_counts: dict) -> int:
    """Trips separating two days' timetables.

    Trips separating two days' timetables: per route, the absolute
    difference in scheduled trips, summed over every route either day runs.
    A route that appears or vanishes contributes its whole trip count, so
    dropping a route is never free.
    """
    return sum(
        abs(counts.get(route, 0) - ref_counts.get(route, 0))
        for route in set(counts) | set(ref_counts)
    )
