import json
import sys
import urllib.error
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).parent))

from archive_static_feed import (
    SAME_DATE_COLLISION_EXIT_CODE,
    archive_feed,
    content_hash,
    download_to_temp,
    sha256_of_file,
    validate_feed_zip,
)

SOFIA = ZoneInfo("Europe/Sofia")

# Minimal but real GTFS: enough for validate_feed_zip() to accept it and for
# archive_feed()'s manifest fields to have something to report.
GTFS_MEMBERS = {
    "trips.txt": "trip_id,route_id,service_id,shape_id\nT1,R1,SVC,S1\n",
    "shapes.txt": "shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\nS1,42.69,23.32,0\nS1,42.70,23.33,1\n",
    "stops.txt": "stop_id,stop_name,stop_lat,stop_lon\nST1,Sample stop,42.69,23.32\n",
    "routes.txt": "route_id,route_short_name,route_type\nR1,9,3\n",
}


def _write_zip(path: Path, members: dict, *, mtime: tuple = (2026, 1, 1, 0, 0, 0)) -> Path:
    """Build a zip with the given {name: text} members, every member
    stamped with the same mtime unless overridden. Stands in for what
    download_to_temp() would have produced -- tests must not hit the real
    network, see archive_static_feed.py's module docstring."""
    with zipfile.ZipFile(path, "w") as z:
        for name, text in members.items():
            info = zipfile.ZipInfo(name, date_time=mtime)
            z.writestr(info, text)
    return path


def _gtfs_zip(path: Path, *, shapes_extra_point: bool = False, feed_info: str | None = None,
              mtime: tuple = (2026, 1, 1, 0, 0, 0)) -> Path:
    members = dict(GTFS_MEMBERS)
    if shapes_extra_point:
        members["shapes.txt"] += "S1,42.71,23.34,2\n"
    if feed_info is not None:
        members["feed_info.txt"] = feed_info
    return _write_zip(path, members, mtime=mtime)


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ─── content_hash ─────────────────────────────────────────────────────────

def test_content_hash_ignores_member_mtime_but_raw_bytes_differ(tmp_path):
    zip_a = _write_zip(tmp_path / "a.zip", {"routes.txt": "route_id\nR1\n"}, mtime=(2026, 1, 1, 0, 0, 0))
    zip_b = _write_zip(tmp_path / "b.zip", {"routes.txt": "route_id\nR1\n"}, mtime=(2026, 6, 15, 12, 30, 0))

    assert content_hash(zip_a) == content_hash(zip_b)
    # The whole point: a raw-bytes hash WOULD differ here, because the zip
    # format stores a per-member modification time in both the local file
    # header and the central directory, and that's exactly the noise
    # content_hash() exists to see through.
    assert sha256_of_file(zip_a) != sha256_of_file(zip_b)


def test_content_hash_changes_when_a_member_actually_changes(tmp_path):
    zip_a = _write_zip(tmp_path / "a.zip", {"routes.txt": "route_id\nR1\n"})
    zip_b = _write_zip(tmp_path / "b.zip", {"routes.txt": "route_id\nR1\nR2\n"})

    assert content_hash(zip_a) != content_hash(zip_b)


# ─── validate_feed_zip ──────────────────────────────────────────────────────

def test_missing_required_member_is_rejected_and_nothing_written(tmp_path):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    members = dict(GTFS_MEMBERS)
    del members["stops.txt"]
    candidate = _write_zip(output_dir / ".gtfs_static_download_test.zip.part", members)

    try:
        validate_feed_zip(candidate)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "stops.txt" in str(e)

    candidate.unlink()  # what main() does on rejection
    assert list(output_dir.iterdir()) == []


def test_corrupt_non_zip_download_is_rejected_and_nothing_written(tmp_path):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    candidate = output_dir / ".gtfs_static_download_test.zip.part"
    candidate.write_bytes(b"<html>502 Bad Gateway</html>")

    try:
        validate_feed_zip(candidate)
        assert False, "expected ValueError"
    except ValueError:
        pass

    candidate.unlink()
    assert list(output_dir.iterdir()) == []


# ─── download_to_temp ────────────────────────────────────────────────────────

def test_download_to_temp_leaves_no_file_behind_on_failure(tmp_path):
    dest_dir = tmp_path / "static"
    # file:// URL to a path that doesn't exist -- exercises the real
    # download/cleanup code path without touching the network.
    bad_url = (tmp_path / "does-not-exist.zip").as_uri()

    try:
        download_to_temp(bad_url, dest_dir, timeout=5)
        assert False, "expected a download error"
    except urllib.error.URLError:
        pass

    assert list(dest_dir.glob(".gtfs_static_download_*")) == []


# ─── archive_feed ────────────────────────────────────────────────────────────

def test_unchanged_feed_saves_nothing(tmp_path):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    _gtfs_zip(output_dir / "gtfs_2026-08-27.zip", mtime=(2026, 1, 1, 0, 0, 0))
    before = sorted(p.name for p in output_dir.iterdir())

    # Same content as the existing snapshot, rebuilt with a different
    # member mtime -- the "agency rebuilds its export nightly from
    # unchanged data" case content_hash() exists to see through.
    candidate = _gtfs_zip(tmp_path / "download.zip", mtime=(2026, 8, 31, 3, 0, 0))

    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    exit_code = archive_feed(output_dir, candidate, "https://example.test/static", SOFIA, now=now)

    assert exit_code == 0
    assert not candidate.exists()
    assert sorted(p.name for p in output_dir.iterdir()) == before


def test_changed_feed_saves_zip_and_manifest_with_matching_hashes(tmp_path):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    _gtfs_zip(output_dir / "gtfs_2026-08-27.zip")

    feed_info = (
        "feed_publisher_name,feed_publisher_url,feed_lang,default_lang,"
        "feed_start_date,feed_end_date,feed_version,feed_contact_email,feed_contact_url\n"
        "Theoremus,https://theoremus.com/,bg,en,20260831,20270831,1.0,a@b.com,https://theoremus.com/\n"
    )
    candidate = _gtfs_zip(tmp_path / "download.zip", shapes_extra_point=True, feed_info=feed_info)

    now = datetime(2026, 8, 31, 12, 0, tzinfo=timezone.utc)
    exit_code = archive_feed(output_dir, candidate, "https://example.test/static", SOFIA, now=now)

    assert exit_code == 0
    assert not candidate.exists()

    target = output_dir / "gtfs_2026-08-31.zip"
    manifest_path = output_dir / "gtfs_2026-08-31.manifest.json"
    assert target.exists()
    assert manifest_path.exists()

    manifest = _read_manifest(manifest_path)
    assert manifest["file"] == "gtfs_2026-08-31.zip"
    assert manifest["source_url"] == "https://example.test/static"
    assert manifest["zip_sha256"] == sha256_of_file(target)
    assert manifest["content_hash"] == content_hash(target)
    assert manifest["member_count"] == 5  # the four required files plus feed_info.txt
    assert manifest["feed_version"] == "1.0"
    assert manifest["feed_start_date"] == "20260831"
    assert manifest["feed_end_date"] == "20270831"
    assert manifest["feed_publisher_name"] == "Theoremus"

    # The earlier snapshot is untouched, not overwritten or merged into.
    assert (output_dir / "gtfs_2026-08-27.zip").exists()


def test_same_date_collision_keeps_existing_file_and_exits_nonzero(tmp_path):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    today_snapshot = _gtfs_zip(output_dir / "gtfs_2026-08-31.zip")
    original_bytes = today_snapshot.read_bytes()

    candidate = _gtfs_zip(tmp_path / "download.zip", shapes_extra_point=True)  # genuinely different content

    now = datetime(2026, 8, 31, 20, 0, tzinfo=timezone.utc)
    exit_code = archive_feed(output_dir, candidate, "https://example.test/static", SOFIA, now=now)

    assert exit_code == SAME_DATE_COLLISION_EXIT_CODE
    assert not candidate.exists()
    assert today_snapshot.read_bytes() == original_bytes
    assert sorted(p.name for p in output_dir.iterdir()) == ["gtfs_2026-08-31.zip"]


def test_snapshot_date_comes_from_sofia_local_time_not_utc(tmp_path):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    _gtfs_zip(output_dir / "gtfs_2026-08-27.zip")
    candidate = _gtfs_zip(tmp_path / "download.zip", shapes_extra_point=True)

    # 22:00 UTC on the 31st is already 01:00 on the 1st in Sofia. Filing this
    # under the UTC date would hand pick_snapshot_for_day() a snapshot dated
    # a day before the local day it actually describes, and the day file
    # collect.py wrote at that same moment carries the Sofia date.
    now = datetime(2026, 8, 31, 22, 0, tzinfo=timezone.utc)
    assert archive_feed(output_dir, candidate, "https://example.test/static", SOFIA, now=now) == 0

    assert (output_dir / "gtfs_2026-09-01.zip").exists()
    assert (output_dir / "gtfs_2026-09-01.manifest.json").exists()
    assert not (output_dir / "gtfs_2026-08-31.zip").exists()


def test_warns_when_captured_feed_predates_the_snapshot_date(tmp_path, capsys):
    output_dir = tmp_path / "static"
    output_dir.mkdir()
    _gtfs_zip(output_dir / "gtfs_2026-08-27.zip")
    # A capture that ran before the agency's daily build gets yesterday's feed
    # filed under today's date, and pick_snapshot_for_day() would then hand a
    # day the feed that preceded it.
    stale = ("feed_publisher_name,feed_start_date,feed_end_date,feed_version\n"
             "Theoremus,20260831,20270831,1.0\n")
    candidate = _gtfs_zip(tmp_path / "download.zip", shapes_extra_point=True, feed_info=stale)

    now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    assert archive_feed(output_dir, candidate, "https://example.test/static", SOFIA, now=now) == 0

    saved = output_dir / "gtfs_2026-09-01.zip"
    assert saved.exists()  # kept, not refused: a scheduling fault is not a bad file
    assert "20260831" in capsys.readouterr().err
    assert saved.stat().st_mode & 0o777 == 0o644
