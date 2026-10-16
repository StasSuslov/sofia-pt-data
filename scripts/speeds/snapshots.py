"""Static snapshot selection: which archived feed answers for which day."""

import re
from pathlib import Path

SNAPSHOT_NAME_RE = re.compile(r'^gtfs_(\d{4}-\d{2}-\d{2})(?:T\d{4})?\.zip$')


def find_static_snapshots(static_dir: Path) -> list:
    """List the dated static snapshots under a directory, oldest first.

    [(snapshot_date, path), ...] in chronological order, for every file
    directly under static_dir matching gtfs_<YYYY-MM-DD>.zip or
    gtfs_<YYYY-MM-DD>T<HHMM>.zip exactly. A full-match regex (not a glob or
    a prefix check) is what keeps this from picking up the unpacked
    gtfs_2026-08-27/ sibling directory or .DS_Store that live alongside the
    zips on disk.

    The optional T<HHMM> is the second and later capture of one local day
    (archive_static_feed.py), added after the agency republished mid-day on
    2026-09-02 and the daily-only archive missed it for 21 hours. Sorting by
    filename rather than by the parsed date is what orders those correctly:
    "." (0x2E) sorts before "T" (0x54), so the plain morning snapshot always
    precedes that day's intra-day ones.
    """
    matches = (
        (SNAPSHOT_NAME_RE.match(path.name), path)
        for path in static_dir.iterdir()
        if path.is_file()
    )
    snapshots = [(match.group(1), path) for match, path in matches if match]
    snapshots.sort(key=lambda pair: pair[1].name)
    return snapshots


def pick_snapshot_for_day(snapshots: list, date_str: str) -> tuple:
    """Pick the snapshot that stands in for `date_str`.

    Returns (snapshot_date, path, is_fallback) for the snapshot that
    should stand in for `date_str`: the latest one dated on or before it —
    the filename date is when this archive actually observed the agency
    serving that feed, which feed_start_date only asserts. `snapshots` must
    already be sorted by date (find_static_snapshots() guarantees this).

    is_fallback is True only when date_str precedes every snapshot — there
    is no feed on record from before the archive started, so the earliest
    available one is used, but the caller must record that as a fallback
    rather than let it look like a real date match.

    One date can hold more than one snapshot since archive_static_feed.py
    started capturing hourly (gtfs_<date>.zip plus gtfs_<date>T<HHMM>.zip),
    and which of them is "the day's own feed" depends on whose date it is.
    For the day itself it is the first capture of that morning: that is the
    feed the day started under, and a republish later the same day belongs
    in the intra-day sibling, which next_snapshot_after() then hands to
    process_day() as the fallback for exactly the trips the morning feed
    cannot resolve. Taking the intra-day capture as the primary instead
    would score the whole morning against a feed that did not exist yet and
    leave the morning's own snapshot behind the fallback pointer, where
    nothing would ever read it. For an earlier date it is the last capture
    of that date, because by the processed day every republish of it had
    already happened.
    """
    candidates = [snap for snap in snapshots if snap[0] <= date_str]
    if not candidates:
        snap_date, path = snapshots[0]
        return snap_date, path, True
    latest_date = candidates[-1][0]
    same_date = [snap for snap in candidates if snap[0] == latest_date]
    snap_date, path = same_date[0 if latest_date == date_str else -1]
    return snap_date, path, False


def next_snapshot_after(snapshots: list, path: Path):
    """Return the snapshot captured right after `path`, or None.

    The snapshot immediately after `path` in `snapshots` (which
    find_static_snapshots() returns in chronological order), or None if
    `path` is the newest one on record.

    A day is matched to the newest feed captured on or before it, so a feed
    the agency republishes *during* that day is not in the day's own
    snapshot — it is in the next one. On 2026-09-02 that cost 10,166 records
    (1.4% of the day), rejected as trip_not_in_static: the agency renumbered
    157 trips of four routes around 11:00 local, the RT stream started
    emitting the new trip_ids immediately, and every one of them turned up
    in the next morning's capture. process_day() resolves against this
    second feed only for trip_ids the day's own feed does not know, so the
    day is still scored against the feed it was actually collected under
    wherever that feed has an answer.
    """
    paths = [snap_path for _, snap_path in snapshots]
    if path not in paths:
        return None
    following = paths.index(path) + 1
    return paths[following] if following < len(paths) else None
