import datetime as dt
import json
import sys
import zipfile
from pathlib import Path
from types import MappingProxyType
from urllib.error import URLError
from zoneinfo import ZoneInfo

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from archive_static_feed import archive_feed
from staticarchive.download import download_to_temp, validate_feed_zip
from staticarchive.hashing import content_hash, sha256_of_file

SOFIA = ZoneInfo('Europe/Sofia')
SOURCE_URL = 'https://example.test/static'
CANDIDATE_BYTES = b'<html>502 Bad Gateway</html>'
OLD_MTIME = (2026, 1, 1, 0, 0, 0)
NEW_MTIME = (2026, 6, 15, 12, 30, 0)
REBUILT_MTIME = (2026, 8, 31, 3, 0, 0)
AUG_END_NOON = dt.datetime.fromisoformat('2026-08-31T12:00:00+00:00')
AUG_END_EVENING = dt.datetime.fromisoformat('2026-08-31T20:00:00+00:00')
AUG_END_LATE = dt.datetime.fromisoformat('2026-08-31T22:00:00+00:00')
SEP_START_NOON = dt.datetime.fromisoformat('2026-09-01T12:00:00+00:00')
GROUP_OTHER_MODE_MASK = 0o777
SNAPSHOT_MODE = 0o644
REQUIRED_PLUS_FEED_INFO = 5

# Minimal but real GTFS: enough for validate_feed_zip() to accept it and for
# archive_feed()'s manifest fields to have something to report.
GTFS_MEMBERS = MappingProxyType(
    {
        'trips.txt': 'trip_id,route_id,service_id,shape_id\nT1,R1,SVC,S1\n',
        'shapes.txt': (
            'shape_id,shape_pt_lat,shape_pt_lon,shape_pt_sequence\n'
            'S1,42.69,23.32,0\nS1,42.70,23.33,1\n'
        ),
        'stops.txt': (
            'stop_id,stop_name,stop_lat,stop_lon\nST1,Sample stop,42.69,23.32\n'
        ),
        'routes.txt': 'route_id,route_short_name,route_type\nR1,9,3\n',
    },
)


def _write_zip(
    path: Path,
    members: dict,
    *,
    mtime: tuple = OLD_MTIME,
) -> Path:
    """Build a zip with the given {name: text} members.

    Every member is stamped with the same mtime unless overridden. Stands in
    for what download_to_temp() would have produced -- tests must not hit
    the real network, see archive_static_feed.py's module docstring.
    """
    with zipfile.ZipFile(path, 'w') as archive:
        for name, text in members.items():
            archive.writestr(zipfile.ZipInfo(name, date_time=mtime), text)
    return path


def _gtfs_zip(
    path: Path,
    *,
    shapes_extra_point: bool = False,
    feed_info: str | None = None,
    mtime: tuple = OLD_MTIME,
) -> Path:
    members = dict(GTFS_MEMBERS)
    if shapes_extra_point:
        members['shapes.txt'] = f'{members["shapes.txt"]}S1,42.71,23.34,2\n'
    if feed_info is not None:
        members['feed_info.txt'] = feed_info
    return _write_zip(path, members, mtime=mtime)


def _read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding='utf-8'))


def _archive(output_dir: Path, candidate: Path, now: dt.datetime) -> int:
    return archive_feed(output_dir, candidate, SOURCE_URL, SOFIA, now=now)


def _names(directory: Path) -> list[str]:
    return sorted(entry.name for entry in directory.iterdir())


@pytest.fixture
def output_dir(tmp_path) -> Path:
    """An empty static directory."""
    static_dir = tmp_path / 'static'
    static_dir.mkdir()
    return static_dir


# ─── content_hash ─────────────────────────────────────────────────────────


def test_content_hash_ignores_member_mtime(tmp_path):
    """Equal content under different mtimes: content_hash same, raw differs."""
    zip_a = _write_zip(
        tmp_path / 'a.zip',
        {'routes.txt': 'route_id\nR1\n'},
        mtime=OLD_MTIME,
    )
    zip_b = _write_zip(
        tmp_path / 'b.zip',
        {'routes.txt': 'route_id\nR1\n'},
        mtime=NEW_MTIME,
    )

    assert content_hash(zip_a) == content_hash(zip_b)
    # The whole point: a raw-bytes hash WOULD differ here, because the zip
    # format stores a per-member modification time in both the local file
    # header and the central directory, and that's exactly the noise
    # content_hash() exists to see through.
    assert sha256_of_file(zip_a) != sha256_of_file(zip_b)


def test_content_hash_sees_member_change(tmp_path):
    """A changed member changes the content hash."""
    zip_a = _write_zip(tmp_path / 'a.zip', {'routes.txt': 'route_id\nR1\n'})
    zip_b = _write_zip(tmp_path / 'b.zip', {'routes.txt': 'route_id\nR1\nR2\n'})

    assert content_hash(zip_a) != content_hash(zip_b)


# ─── validate_feed_zip ──────────────────────────────────────────────────────


def test_missing_member_is_rejected(output_dir):
    """A zip without stops.txt is rejected and leaves nothing behind."""
    members = dict(GTFS_MEMBERS)
    members.pop('stops.txt')
    candidate = _write_zip(
        output_dir / '.gtfs_static_download_test.zip.part',
        members,
    )

    with pytest.raises(ValueError, match=r'stops\.txt'):
        validate_feed_zip(candidate)

    candidate.unlink()  # what main() does on rejection
    assert list(output_dir.iterdir()) == []


def test_corrupt_download_is_rejected(output_dir):
    """A non-zip body is rejected and leaves nothing behind."""
    candidate = output_dir / '.gtfs_static_download_test.zip.part'
    candidate.write_bytes(CANDIDATE_BYTES)

    with pytest.raises(ValueError, match='not a valid zip file'):
        validate_feed_zip(candidate)

    candidate.unlink()
    assert list(output_dir.iterdir()) == []


# ─── download_to_temp ────────────────────────────────────────────────────────


def test_failed_download_leaves_no_file(tmp_path):
    """A failed download removes its temp file before raising."""
    dest_dir = tmp_path / 'static'
    # file:// URL to a path that doesn't exist -- exercises the real
    # download/cleanup code path without touching the network.
    bad_url = (tmp_path / 'does-not-exist.zip').as_uri()

    with pytest.raises(URLError):
        download_to_temp(
            bad_url,
            dest_dir,
            'sofia-transport-research/1.0',
            timeout=5,
        )

    assert list(dest_dir.glob('.gtfs_static_download_*')) == []


# ─── archive_feed ────────────────────────────────────────────────────────────


def test_unchanged_feed_saves_nothing(tmp_path, output_dir):
    """A rebuilt-but-identical feed is discarded without a new snapshot."""
    _gtfs_zip(output_dir / 'gtfs_2026-08-27.zip', mtime=OLD_MTIME)
    before = _names(output_dir)

    # Same content as the existing snapshot, rebuilt with a different
    # member mtime -- the "agency rebuilds its export nightly from
    # unchanged data" case content_hash() exists to see through.
    candidate = _gtfs_zip(tmp_path / 'download.zip', mtime=REBUILT_MTIME)

    assert _archive(output_dir, candidate, AUG_END_NOON) == 0
    assert not candidate.exists()
    assert _names(output_dir) == before


FEED_INFO = (
    'feed_publisher_name,feed_publisher_url,feed_lang,default_lang,'
    'feed_start_date,feed_end_date,feed_version,feed_contact_email,'
    'feed_contact_url\n'
    'Theoremus,https://theoremus.com/,bg,en,20260831,20270831,1.0,a@b.com,'
    'https://theoremus.com/\n'
)


@pytest.fixture
def saved_changed_feed(tmp_path, output_dir) -> Path:
    """Archive a changed feed carrying feed_info.txt; return the directory."""
    _gtfs_zip(output_dir / 'gtfs_2026-08-27.zip')
    candidate = _gtfs_zip(
        tmp_path / 'download.zip',
        shapes_extra_point=True,
        feed_info=FEED_INFO,
    )
    assert _archive(output_dir, candidate, AUG_END_NOON) == 0
    assert not candidate.exists()
    return output_dir


def test_changed_feed_saves_zip_and_manifest(saved_changed_feed):
    """The new zip and its manifest land; the earlier snapshot is untouched."""
    assert (saved_changed_feed / 'gtfs_2026-08-31.zip').exists()
    assert (saved_changed_feed / 'gtfs_2026-08-31.manifest.json').exists()
    # The earlier snapshot is untouched, not overwritten or merged into.
    assert (saved_changed_feed / 'gtfs_2026-08-27.zip').exists()


def test_manifest_hashes_match_the_saved_zip(saved_changed_feed):
    """The manifest's hashes and counts describe the saved zip."""
    target = saved_changed_feed / 'gtfs_2026-08-31.zip'
    manifest = _read_manifest(
        saved_changed_feed / 'gtfs_2026-08-31.manifest.json',
    )

    assert manifest['file'] == 'gtfs_2026-08-31.zip'
    assert manifest['source_url'] == SOURCE_URL
    assert manifest['zip_sha256'] == sha256_of_file(target)
    assert manifest['content_hash'] == content_hash(target)
    # the four required files plus feed_info.txt
    assert manifest['member_count'] == REQUIRED_PLUS_FEED_INFO


def test_manifest_carries_the_feed_info_fields(saved_changed_feed):
    """feed_info.txt fields are copied into the manifest."""
    manifest = _read_manifest(
        saved_changed_feed / 'gtfs_2026-08-31.manifest.json',
    )

    assert manifest['feed_version'] == '1.0'
    assert manifest['feed_start_date'] == '20260831'
    assert manifest['feed_end_date'] == '20270831'
    assert manifest['feed_publisher_name'] == 'Theoremus'


def test_second_change_saved_beside_first(tmp_path, output_dir):
    """An intra-day change gets a T<HHMM> name next to the plain one."""
    today_snapshot = _gtfs_zip(output_dir / 'gtfs_2026-08-31.zip')
    original_bytes = today_snapshot.read_bytes()

    candidate = _gtfs_zip(
        tmp_path / 'download.zip',
        shapes_extra_point=True,
    )  # genuinely different content

    # 20:00 UTC is 23:00 in Sofia, and the intra-day name carries the local
    # capture time so it sorts after the plain name for the same date.
    assert _archive(output_dir, candidate, AUG_END_EVENING) == 0
    assert not candidate.exists()
    # The morning capture is kept as it was: the point is both, not the newer.
    assert today_snapshot.read_bytes() == original_bytes
    assert _names(output_dir) == [
        'gtfs_2026-08-31.zip',
        'gtfs_2026-08-31T2300.manifest.json',
        'gtfs_2026-08-31T2300.zip',
    ]


def test_intra_day_manifest_names_its_zip(tmp_path, output_dir):
    """The intra-day manifest points at the intra-day zip."""
    _gtfs_zip(output_dir / 'gtfs_2026-08-31.zip')
    candidate = _gtfs_zip(tmp_path / 'download.zip', shapes_extra_point=True)
    _archive(output_dir, candidate, AUG_END_EVENING)

    manifest = json.loads(
        (output_dir / 'gtfs_2026-08-31T2300.manifest.json').read_text(),
    )
    assert manifest['file'] == 'gtfs_2026-08-31T2300.zip'
    assert manifest['content_hash'] == content_hash(
        output_dir / 'gtfs_2026-08-31T2300.zip',
    )


def test_third_change_compares_to_intra_day(
    tmp_path,
    output_dir,
):
    """The intra-day file is the newest; equal content is dropped."""
    # The intra-day file has to be the one latest_snapshot() returns, or the
    # next run would compare against the morning capture and re-save content
    # it already holds.
    _gtfs_zip(output_dir / 'gtfs_2026-08-31.zip')
    _gtfs_zip(output_dir / 'gtfs_2026-08-31T1100.zip', shapes_extra_point=True)

    candidate = _gtfs_zip(tmp_path / 'download.zip', shapes_extra_point=True)

    assert _archive(output_dir, candidate, AUG_END_EVENING) == 0
    assert not candidate.exists()
    assert not (output_dir / 'gtfs_2026-08-31T2300.zip').exists()


def test_date_comes_from_sofia_local_time(tmp_path, output_dir):
    """22:00 UTC on the 31st is filed under the Sofia date, the 1st."""
    _gtfs_zip(output_dir / 'gtfs_2026-08-27.zip')
    candidate = _gtfs_zip(tmp_path / 'download.zip', shapes_extra_point=True)

    # 22:00 UTC on the 31st is already 01:00 on the 1st in Sofia. Filing this
    # under the UTC date would hand pick_snapshot_for_day() a snapshot dated
    # a day before the local day it actually describes, and the day file
    # collect.py wrote at that same moment carries the Sofia date.
    assert _archive(output_dir, candidate, AUG_END_LATE) == 0

    assert (output_dir / 'gtfs_2026-09-01.zip').exists()
    assert (output_dir / 'gtfs_2026-09-01.manifest.json').exists()
    assert not (output_dir / 'gtfs_2026-08-31.zip').exists()


STALE_FEED_INFO = (
    'feed_publisher_name,feed_start_date,feed_end_date,feed_version\n'
    'Theoremus,20260831,20270831,1.0\n'
)


def test_warns_when_feed_predates_snapshot(
    tmp_path,
    output_dir,
    capsys,
):
    """A feed_start_date behind the capture date warns but still saves."""
    _gtfs_zip(output_dir / 'gtfs_2026-08-27.zip')
    # A capture that ran before the agency's daily build gets yesterday's feed
    # filed under today's date, and pick_snapshot_for_day() would then hand a
    # day the feed that preceded it.
    candidate = _gtfs_zip(
        tmp_path / 'download.zip',
        shapes_extra_point=True,
        feed_info=STALE_FEED_INFO,
    )

    assert _archive(output_dir, candidate, SEP_START_NOON) == 0

    saved = output_dir / 'gtfs_2026-09-01.zip'
    # kept, not refused: a scheduling fault is not a bad file
    assert saved.exists()
    assert '20260831' in capsys.readouterr().err
    assert saved.stat().st_mode & GROUP_OTHER_MODE_MASK == SNAPSHOT_MODE
