import datetime as dt
import errno
import functools
import itertools
import json
import lzma
import os
import sys
import tarfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from tripupdatesarchive import pack, snapshot, tarball

NOW = dt.datetime.fromisoformat('2026-10-18T10:07:00+00:00')
ARCHIVE = 'tu_2026-10-18T09Z.tar.xz'
FIRST = '2026-10-18T090015'
FIRST_NAME = f'tu_{FIRST}Z.pb'
STAMPS = ('2026-10-18T095915', '2026-10-18T090000', '2026-10-18T093000')
MTIME = 1791000000
# Random, so that truncating the archive cuts into member data rather than
# into a run of zeros that xz squeezes into a few bytes.
INCOMPRESSIBLE_BYTES = 65536
PENDING_BODY = os.urandom(INCOMPRESSIBLE_BYTES)
DAMAGE_ERRORS = (tarfile.TarError, lzma.LZMAError, EOFError)
# The exit code and incoming/ of a run that packed every closed hour.
ALL_PACKED = (0, [])


@pytest.fixture
def pending(tmp_path: Path) -> Path:
    """An output directory with one snapshot of hour 09 in incoming/."""
    _snap(tmp_path, FIRST, PENDING_BODY)
    return tmp_path


@pytest.fixture
def packed(pending: Path) -> Path:
    """The same directory once hour 09 is archived and its file deleted."""
    pack.pack_closed_hours(pending, NOW)
    return pending


def _run(directory: Path, now: dt.datetime = NOW) -> tuple[int, list[str]]:
    """Pack; return the exit code and what incoming/ still holds."""
    failed = pack.pack_closed_hours(directory, now)
    return failed, _incoming(directory)


def _snap(directory: Path, stamp: str, body: bytes | None = None) -> Path:
    """Put incoming/tu_<stamp>Z.pb in place; its bytes default to the stamp."""
    incoming = directory / 'incoming'
    incoming.mkdir(exist_ok=True)
    path = incoming / f'tu_{stamp}Z.pb'
    path.write_bytes(stamp.encode() if body is None else body)
    return path


def _members(directory: Path) -> dict[str, bytes]:
    with tarfile.open(directory / ARCHIVE, 'r:xz') as tar:
        extract = tar.extractfile
        return {member.name: extract(member).read() for member in tar}


def _names(directory: Path, archive: str = ARCHIVE) -> list[str]:
    with tarfile.open(directory / archive, 'r:xz') as tar:
        return tar.getnames()


def _archives(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob('*.tar.xz'))


def _incoming(directory: Path) -> list[str]:
    return sorted(path.name for path in (directory / 'incoming').glob('*'))


def _runs(directory: Path) -> list[dict]:
    log = directory / 'runs.ndjson'
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text().splitlines()]


def _truncated(archive: Path) -> None:
    packed_bytes = archive.read_bytes()
    archive.write_bytes(packed_bytes[: len(packed_bytes) // 2])


def _garbled(archive: Path) -> None:
    archive.write_bytes(b'not an xz stream')


def _unlink_crashing_on_second(path, calls, real_unlink, *, missing_ok=False):
    if next(calls) == 2:
        raise OSError('power cut')
    real_unlink(path, missing_ok=missing_ok)


def _write_empty(part: Path, base, snapshots) -> None:
    tarball.write_archive(part, None, [])


def _disk_full(part: Path, base, snapshots) -> None:
    part.write_bytes(b'half an archive')
    raise OSError(errno.ENOSPC, 'No space left on device')


def test_only_closed_hours_are_packed(tmp_path):
    """Hours before the current UTC hour are archived, their files go."""
    first = _snap(tmp_path, FIRST)
    second = _snap(tmp_path, '2026-10-18T095915')
    _snap(tmp_path, '2026-10-18T100015')
    assert _run(tmp_path) == (0, ['tu_2026-10-18T100015Z.pb'])
    assert _archives(tmp_path) == [ARCHIVE]
    assert _members(tmp_path) == {
        first.name: FIRST.encode(),
        second.name: b'2026-10-18T095915',
    }


def test_members_are_sorted_with_mtimes_kept(tmp_path):
    """Members go in by name whatever order the files came in."""
    for stamp in STAMPS:
        os.utime(_snap(tmp_path, stamp), (MTIME, MTIME))
    _run(tmp_path)
    with tarfile.open(tmp_path / ARCHIVE, 'r:xz') as tar:
        members = tar.getmembers()
    assert [member.name for member in members] == sorted(
        f'tu_{stamp}Z.pb' for stamp in STAMPS
    )
    assert {member.mtime for member in members} == {MTIME}


def test_utc_midnight_splits_the_archives(tmp_path):
    """23Z and 00Z land in archives of their own; a .tmp is never a member."""
    _snap(tmp_path, '2026-10-18T235945')
    _snap(tmp_path, '2026-10-19T000015')
    (tmp_path / 'incoming' / 'tu_2026-10-18T235950Z.pb.tmp').write_bytes(b'cut')
    after_midnight = dt.datetime.fromisoformat('2026-10-19T01:07:00+00:00')
    assert _run(tmp_path, after_midnight) == ALL_PACKED
    assert _archives(tmp_path) == [
        'tu_2026-10-18T23Z.tar.xz',
        'tu_2026-10-19T00Z.tar.xz',
    ]
    assert _names(tmp_path, 'tu_2026-10-18T23Z.tar.xz') == [
        'tu_2026-10-18T235945Z.pb',
    ]


@pytest.mark.parametrize('subdir', ['', 'incoming'])
def test_missing_or_empty_incoming_packs_nothing(tmp_path, subdir):
    """Before the first poll there is no incoming/; neither case fails."""
    (tmp_path / subdir).mkdir(exist_ok=True)
    assert _run(tmp_path) == ALL_PACKED
    assert _archives(tmp_path) == []


def test_leftovers_the_archive_holds_are_deleted(packed):
    """Files left by a stop between rename and deletion just go."""
    _snap(packed, FIRST, PENDING_BODY)
    assert _run(packed) == ALL_PACKED
    assert _names(packed) == [FIRST_NAME]
    assert _runs(packed) == []


def test_crash_mid_deletion_is_finished_by_rerun(tmp_path, monkeypatch):
    """A run killed between two deletions leaves files the rerun drops."""
    for stamp in STAMPS:
        _snap(tmp_path, stamp)
    crashing = functools.partialmethod(
        _unlink_crashing_on_second,
        itertools.count(1),
        Path.unlink,
    )
    with monkeypatch.context() as patched:
        patched.setattr(Path, 'unlink', crashing)
        with pytest.raises(OSError, match='power cut'):
            _run(tmp_path)
    assert len(_incoming(tmp_path)) == 2
    assert _run(tmp_path) == ALL_PACKED
    assert len(_names(tmp_path)) == len(STAMPS)


def test_late_snapshot_joins_archive_in_order(packed):
    """A snapshot that turns up after its hour was packed joins the archive."""
    _snap(packed, '2026-10-18T090145')
    _run(packed)
    late = _snap(packed, '2026-10-18T090100')
    assert _run(packed) == ALL_PACKED
    assert _names(packed) == [
        FIRST_NAME,
        'tu_2026-10-18T090100Z.pb',
        'tu_2026-10-18T090145Z.pb',
    ]
    assert _members(packed)[late.name] == b'2026-10-18T090100'


def test_namesake_with_other_bytes_is_kept(packed):
    """Same name, other bytes: nothing is overwritten or deleted, exit 1."""
    namesake = _snap(packed, FIRST, b'second')
    assert _run(packed) == (1, [FIRST_NAME])
    assert namesake.read_bytes() == b'second'
    assert _members(packed) == {FIRST_NAME: PENDING_BODY}
    logged = [(run['stage'], run['hour']) for run in _runs(packed)]
    assert logged == [('pack', '2026-10-18T09')]


@pytest.mark.parametrize('damage', [_truncated, _garbled])
def test_damaged_archive_deletes_nothing(packed, damage):
    """A truncated or garbled archive stops the run before any deletion."""
    damage(packed / ARCHIVE)
    _snap(packed, FIRST)
    _snap(packed, '2026-10-18T090100')
    with pytest.raises(DAMAGE_ERRORS):
        _run(packed)
    assert _incoming(packed) == [FIRST_NAME, 'tu_2026-10-18T090100Z.pb']


def test_debris_of_killed_runs_is_cleared(pending):
    """Debris of killed runs: a .part is rewritten, a closed-hour .tmp goes."""
    incoming = pending / 'incoming'
    (incoming / f'{ARCHIVE}.part').write_bytes(b'junk')
    (incoming / 'tu_2026-10-18T090100Z.pb.tmp').write_bytes(b'cut')
    (incoming / 'tu_2026-10-18T100100Z.pb.tmp').write_bytes(b'in flight')
    assert _run(pending) == (0, ['tu_2026-10-18T100100Z.pb.tmp'])
    assert _names(pending) == [FIRST_NAME]


def test_bad_read_back_keeps_the_hour(pending, monkeypatch):
    """A .part that does not match its files is dropped; the files stay."""
    monkeypatch.setattr(pack, 'write_archive', _write_empty)
    assert _run(pending) == (1, [FIRST_NAME])
    assert _archives(pending) == []
    assert [run['stage'] for run in _runs(pending)] == ['pack']


def test_autumn_dst_hours_stay_apart(tmp_path):
    """Review Focus 3: 03:30 comes twice in Sofia on 25.10, UTC names don't."""
    summer = dt.datetime.fromisoformat('2026-10-25T03:30:00+03:00')
    winter = dt.datetime.fromisoformat('2026-10-25T03:30:00+02:00')
    snapshot.save_snapshot(tmp_path, b'summer', summer)
    snapshot.save_snapshot(tmp_path, b'winter', winter)
    after = dt.datetime.fromisoformat('2026-10-25T02:07:00+00:00')
    assert _run(tmp_path, after) == ALL_PACKED
    assert _archives(tmp_path) == [
        'tu_2026-10-25T00Z.tar.xz',
        'tu_2026-10-25T01Z.tar.xz',
    ]


def test_disk_full_mid_write_keeps_the_hour(pending, monkeypatch):
    """Review Focus 4: a write that runs out of disk leaves the snapshots."""
    with monkeypatch.context() as patched:
        patched.setattr(pack, 'write_archive', _disk_full)
        with pytest.raises(OSError, match='No space'):
            _run(pending)
    assert _incoming(pending) == [FIRST_NAME, f'{ARCHIVE}.part']
    assert _run(pending) == ALL_PACKED


def test_rerun_leaves_the_archive_alone(packed):
    """Review Focus 5: a catch-up run after boot rewrites nothing."""
    archive = packed / ARCHIVE
    packed_at = archive.stat().st_mtime_ns
    assert _run(packed) == ALL_PACKED
    assert archive.stat().st_mtime_ns == packed_at
    assert _runs(packed) == []
