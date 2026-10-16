"""Tests for package_dataset: verification, snapshots, packing, sums."""

import gzip
import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from package_dataset import (
    date_range,
    find_skipped_files,
    find_static_snapshots,
    run,
    sha256_of,
    verify_day,
    write_sha256sums,
)

FIRST_DAY = '2026-08-27'
SECOND_DAY = '2026-08-28'
THIRD_DAY = '2026-08-29'
RANGE_END = '2026-09-01'
SCHEMA_VERSION = 2
OLD_SCHEMA_VERSION = 1
REPEAT_RECORDS = 50
REPEAT_CONTENT = 100
DATA_NAME = f'{FIRST_DAY}.jsonl'
POLLS_NAME = f'{FIRST_DAY}.polls.jsonl'
MANIFEST_NAME = f'{FIRST_DAY}.manifest.json'
STATIC_ZIP_NAME = f'gtfs_{FIRST_DAY}.zip'
DEFAULT_RECORDS = b'{"snapshot_ts": 1000, "vehicle_id": "A1"}\n'
DEFAULT_POLLS = b'{"snapshot_ts": 1000, "fetch_ok": true, "vehicle_count": 1}\n'
RT_EXPECTED_MEMBERS = tuple(
    f'{day}{suffix}'
    for day in (FIRST_DAY, SECOND_DAY)
    for suffix in ('.jsonl', '.polls.jsonl', '.manifest.json')
)
REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256_bytes(payload: bytes) -> str:
    """Return the SHA-256 hex digest of payload."""
    return hashlib.sha256(payload).hexdigest()


def _write_maybe_gzipped(path: Path, payload: bytes, *, gzipped: bool) -> None:
    if gzipped:
        with gzip.open(path, 'wb') as out:
            out.write(payload)
    else:
        path.write_bytes(payload)


def write_day(
    data_dir: Path,
    date_str: str,
    *,
    records: bytes = DEFAULT_RECORDS,
    polls: bytes | None = DEFAULT_POLLS,
    gzip_data: bool = False,
    gzip_polls: bool = False,
    day_in_progress: bool = False,
) -> dict:
    """Write a minimal but real day file trio and return its manifest.

    Writes <date>.jsonl(+.gz)/<date>.polls.jsonl(+.gz)/<date>.manifest.json
    with the manifest's hashes computed the same way generate_manifest.py
    would: over the UNCOMPRESSED bytes regardless of what's on disk. Returns
    the manifest dict actually written, so tests can tweak-and-rewrite it
    for the mismatch cases.
    """
    data_dir.mkdir(parents=True, exist_ok=True)
    data_suffix = '.gz' if gzip_data else ''
    _write_maybe_gzipped(
        data_dir / f'{date_str}.jsonl{data_suffix}',
        records,
        gzipped=gzip_data,
    )

    manifest = {
        'schema_version': SCHEMA_VERSION,
        'date': date_str,
        'data_file': f'{date_str}.jsonl',
        'data_sha256': sha256_bytes(records),
        'data_size_bytes': len(records),
        'compressed': gzip_data,
        'total_vehicle_records': 1,
        'day_in_progress': day_in_progress,
        'coverage_pct': 100.0,
        'gap_count': 0,
        'polls_file': None,
        'polls_sha256': None,
    }

    if polls is not None:
        polls_suffix = '.gz' if gzip_polls else ''
        _write_maybe_gzipped(
            data_dir / f'{date_str}.polls.jsonl{polls_suffix}',
            polls,
            gzipped=gzip_polls,
        )
        manifest['polls_file'] = f'{date_str}.polls.jsonl'
        manifest['polls_sha256'] = sha256_bytes(polls)

    rewrite_manifest(data_dir, date_str, manifest)
    return manifest


def rewrite_manifest(data_dir: Path, date_str: str, manifest: dict) -> None:
    """Write manifest as the day's <date>.manifest.json."""
    manifest_path = data_dir / f'{date_str}.manifest.json'
    manifest_path.write_text(json.dumps(manifest), encoding='utf-8')


def write_static_snapshot(
    static_dir: Path,
    date_str: str,
    *,
    routes: bytes = b'members,go,here\n',
) -> Path:
    """Write a gtfs_<date>.zip snapshot plus its manifest."""
    static_dir.mkdir(parents=True, exist_ok=True)
    zip_path = static_dir / f'gtfs_{date_str}.zip'
    with zipfile.ZipFile(zip_path, 'w') as archive:
        archive.writestr('routes.txt', routes)
    zip_bytes = zip_path.read_bytes()
    manifest = {
        'file': zip_path.name,
        'zip_sha256': sha256_bytes(zip_bytes),
        'size_bytes': len(zip_bytes),
        'member_count': 1,
    }
    (static_dir / f'gtfs_{date_str}.manifest.json').write_text(
        json.dumps(manifest),
        encoding='utf-8',
    )
    return zip_path


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    """Return the (not yet created) per-city data directory."""
    return tmp_path / 'sofia'


@pytest.fixture
def standard_day(data_dir: Path) -> Path:
    """Return a data directory holding one valid, closed first day."""
    write_day(data_dir, FIRST_DAY)
    return data_dir


def _verify_first_day(day_dir: Path) -> dict:
    return verify_day(day_dir, FIRST_DAY)


def _abort_message(day_dir: Path) -> str:
    try:
        verify_day(day_dir, FIRST_DAY)
    except ValueError as exc:
        return str(exc)
    return pytest.fail('expected ValueError')


def _find_snapshots(static_dir: Path) -> list:
    return find_static_snapshots(static_dir, FIRST_DAY, RANGE_END)


# ─── date_range ──────────────────────────────────────────────────────────────


def test_date_range_inclusive_of_both_ends():
    """Both the first and the last day are part of the range."""
    assert date_range(FIRST_DAY, THIRD_DAY) == [
        FIRST_DAY,
        SECOND_DAY,
        THIRD_DAY,
    ]


def test_date_range_single_day():
    """Equal start and end give a one-day range."""
    assert date_range(FIRST_DAY, FIRST_DAY) == [FIRST_DAY]


def test_date_range_end_before_start_raises():
    """A reversed range is rejected with a "before" message."""
    with pytest.raises(ValueError, match='before'):
        date_range(THIRD_DAY, FIRST_DAY)


# ─── verify_day ──────────────────────────────────────────────────────────────


def test_verify_day_happy_path(standard_day: Path):
    """A consistent day yields the data and polls paths."""
    verified = _verify_first_day(standard_day)

    assert verified['data_path'].name == DATA_NAME
    assert verified['polls_path'].name == POLLS_NAME


def test_verify_day_no_heartbeat_declared_is_ok(data_dir: Path):
    """A manifest with polls_file null needs no polls file on disk."""
    # The real 2026-08-27 case: collection started before heartbeat logging
    # existed, so the manifest itself records polls_file: null. No
    # .polls.jsonl on disk at all must NOT be treated as a missing file.
    write_day(data_dir, FIRST_DAY, polls=None)

    verified = _verify_first_day(data_dir)

    assert verified['polls_path'] is None


def test_verify_day_hash_mismatch_names_the_file(standard_day: Path):
    """A corrupted data file aborts and the message names the file."""
    # Corrupt the data file after its manifest was written.
    (standard_day / DATA_NAME).write_bytes(
        b'{"snapshot_ts": 1000, "vehicle_id": "TAMPERED"}\n',
    )

    message = _abort_message(standard_day)

    assert DATA_NAME in message
    assert 'mismatch' in message


def test_verify_day_polls_hash_mismatch_aborts(standard_day: Path):
    """A corrupted polls file aborts and the message names the file."""
    (standard_day / POLLS_NAME).write_bytes(
        b'{"snapshot_ts": 1000, "fetch_ok": false}\n',
    )

    assert POLLS_NAME in _abort_message(standard_day)


def test_verify_day_missing_manifest_aborts(data_dir: Path):
    """A day with no manifest at all aborts."""
    data_dir.mkdir()
    (data_dir / DATA_NAME).write_bytes(b'{"snapshot_ts": 1000}\n')
    # no .manifest.json written at all

    assert 'manifest' in _abort_message(data_dir)


def test_verify_day_missing_data_file_aborts(standard_day: Path):
    """A manifest without its data file aborts and names the day."""
    (standard_day / DATA_NAME).unlink()

    assert FIRST_DAY in _abort_message(standard_day)


def test_verify_day_missing_polls_file_aborts(standard_day: Path):
    """A declared but missing polls file aborts with "heartbeat"."""
    (standard_day / POLLS_NAME).unlink()

    assert 'heartbeat' in _abort_message(standard_day)


def test_verify_day_stale_schema_version_aborts(data_dir: Path):
    """A schema-1 manifest aborts instead of reading as a closed day."""
    # A schema-1 manifest predates day_in_progress: absent, it would read as
    # "closed" and a still-open day would sail into a permanent record.
    manifest = write_day(data_dir, FIRST_DAY)
    manifest['schema_version'] = OLD_SCHEMA_VERSION
    manifest.pop('day_in_progress')
    rewrite_manifest(data_dir, FIRST_DAY, manifest)

    assert 'schema_version' in _abort_message(data_dir)


def test_verify_day_no_schema_version_aborts(data_dir: Path):
    """A manifest with no schema_version aborts."""
    manifest = write_day(data_dir, FIRST_DAY)
    manifest.pop('schema_version')
    rewrite_manifest(data_dir, FIRST_DAY, manifest)

    assert 'schema_version' in _abort_message(data_dir)


def test_verify_day_in_progress_day_aborts(data_dir: Path):
    """A day still in progress is never packed."""
    write_day(data_dir, '2026-09-02', day_in_progress=True)

    with pytest.raises(ValueError, match='in progress'):
        verify_day(data_dir, '2026-09-02')


def test_verify_day_gzipped_matches_plain_hash(data_dir: Path):
    """A gzipped day verifies against the uncompressed-bytes hash."""
    # The manifest always records the hash of the UNCOMPRESSED bytes (see
    # generate_manifest.py's sha256_of docstring) even when the day is
    # gzipped on disk — verify_day must recompute the same way.
    write_day(data_dir, FIRST_DAY, gzip_data=True, gzip_polls=True)

    verified = _verify_first_day(data_dir)

    assert verified['data_path'].name == f'{DATA_NAME}.gz'
    assert verified['polls_path'].name == f'{POLLS_NAME}.gz'


# ─── find_static_snapshots ──────────────────────────────────────────────────


def test_static_snapshots_filter_by_range(tmp_path: Path):
    """Only snapshots inside the range are returned, hash-verified."""
    static_dir = tmp_path / 'static'
    write_static_snapshot(static_dir, FIRST_DAY)
    write_static_snapshot(static_dir, '2026-08-31')
    write_static_snapshot(static_dir, '2026-09-05')  # outside range

    snapshots = _find_snapshots(static_dir)

    assert [snap['date'] for snap in snapshots] == [FIRST_DAY, '2026-08-31']


def test_static_snapshots_hash_mismatch_aborts(tmp_path: Path):
    """A snapshot changed after its manifest was written aborts."""
    static_dir = tmp_path / 'static'
    zip_path = write_static_snapshot(static_dir, FIRST_DAY)
    with zipfile.ZipFile(zip_path, 'a') as archive:
        archive.writestr('extra.txt', 'tampered after manifest was written')

    with pytest.raises(ValueError, match=STATIC_ZIP_NAME.replace('.', r'\.')):
        _find_snapshots(static_dir)


def test_static_snapshots_missing_manifest(tmp_path: Path):
    """A snapshot without a manifest aborts."""
    static_dir = tmp_path / 'static'
    write_static_snapshot(static_dir, FIRST_DAY)
    (static_dir / f'gtfs_{FIRST_DAY}.manifest.json').unlink()

    with pytest.raises(ValueError, match='manifest'):
        _find_snapshots(static_dir)


def test_static_snapshots_missing_dir_aborts(tmp_path: Path):
    """A missing static directory aborts."""
    with pytest.raises(ValueError, match='static directory'):
        _find_snapshots(tmp_path / 'does-not-exist')


# ─── find_skipped_files ──────────────────────────────────────────────────────


def test_skipped_files_stray_and_out_of_range(data_dir: Path):
    """Stray and out-of-range files are listed with a reason."""
    write_day(data_dir, FIRST_DAY)
    write_day(data_dir, '2026-09-02')  # outside the range below
    (data_dir / f'{FIRST_DAY}.log').write_text('stray log', encoding='utf-8')
    (data_dir / '.DS_Store').write_bytes(b'\x00')

    verified = [_verify_first_day(data_dir)]
    skipped = find_skipped_files(data_dir, verified, FIRST_DAY, FIRST_DAY)

    names = dict(skipped)
    assert 'not a recognized' in names[f'{FIRST_DAY}.log']
    assert '.DS_Store' in names
    assert 'outside requested range' in names['2026-09-02.jsonl']
    assert '2026-09-02.manifest.json' in names
    # consumed files for the day actually packed must not show up as skipped
    assert {DATA_NAME, MANIFEST_NAME, POLLS_NAME}.isdisjoint(names)


# ─── run() end-to-end ────────────────────────────────────────────────────────


def _assert_rt_zip_round_trips(rt_zip_path: Path) -> None:
    # Round-trip: every member inside the RT zip is stored DECOMPRESSED and
    # hashes to exactly what its manifest (also inside the zip) declares,
    # regardless of whether the source was flat or gzipped on disk.
    with zipfile.ZipFile(rt_zip_path) as archive:
        members = set(archive.namelist())
        manifest_first = json.loads(archive.read(MANIFEST_NAME))
        extracted_hash = sha256_bytes(archive.read(DATA_NAME))

        assert members.issuperset(RT_EXPECTED_MEMBERS)
        assert extracted_hash == manifest_first['data_sha256']
        compress_type = archive.getinfo(DATA_NAME).compress_type
        assert compress_type == zipfile.ZIP_DEFLATED


def _assert_static_zip_stored(static_zip_path: Path) -> None:
    with zipfile.ZipFile(static_zip_path) as archive:
        members = archive.namelist()
        assert STATIC_ZIP_NAME in members
        assert f'gtfs_{FIRST_DAY}.manifest.json' in members
        compress_type = archive.getinfo(STATIC_ZIP_NAME).compress_type
        assert compress_type == zipfile.ZIP_STORED


def _assert_docs_and_sums(packed: dict, out_dir: Path) -> None:
    # METHODOLOGY.md and README.md ship with the record (CLAUDE.md section 8)
    # and are covered by the same SHA256SUMS.txt as the zips, so a reader
    # verifies the whole download with one `sha256sum -c`.
    sums = packed['sums_path'].read_text()
    for name in ('METHODOLOGY.md', 'README.md'):
        copied = out_dir / name
        assert copied.exists()
        assert copied.read_bytes() == (REPO_ROOT / name).read_bytes()
        assert f'  {name}\n' in sums
    assert f'  {packed["rt_zip_path"].name}\n' in sums


def test_run_packs_flat_and_gzipped_round_trip(tmp_path: Path, data_dir: Path):
    """Flat and gzipped days pack into zips that round-trip."""
    static_dir = data_dir / 'static'
    write_day(
        data_dir,
        FIRST_DAY,
        records=b'{"snapshot_ts": 1000, "vehicle_id": "A1"}\n' * REPEAT_RECORDS,
        gzip_data=True,
        gzip_polls=True,
    )
    write_day(
        data_dir,
        SECOND_DAY,
        records=b'{"snapshot_ts": 2000, "vehicle_id": "B1"}\n' * REPEAT_RECORDS,
    )
    write_static_snapshot(static_dir, FIRST_DAY)
    out_dir = tmp_path / 'out'

    packed = run(data_dir, static_dir, FIRST_DAY, SECOND_DAY, out_dir)

    assert packed['rt_zip_path'].exists()
    assert packed['static_zip_path'].exists()
    assert packed['sums_path'].exists()
    assert (
        packed['rt_zip_path'].name == f'sofia-rt_{FIRST_DAY}_{SECOND_DAY}.zip'
    )
    assert (
        packed['static_zip_path'].name
        == f'sofia-gtfs-static_{FIRST_DAY}_{SECOND_DAY}.zip'
    )
    _assert_rt_zip_round_trips(packed['rt_zip_path'])
    _assert_static_zip_stored(packed['static_zip_path'])
    _assert_docs_and_sums(packed, out_dir)


def test_run_verifies_before_packing_anything(tmp_path: Path, data_dir: Path):
    """A mismatch on the second day leaves no zip behind."""
    # A mismatch on the SECOND day must mean NO zip is written at all — not a
    # half-packed archive containing only the first, valid day.
    static_dir = data_dir / 'static'
    write_day(data_dir, FIRST_DAY)
    write_day(data_dir, SECOND_DAY)
    (data_dir / f'{SECOND_DAY}.jsonl').write_bytes(
        b'{"snapshot_ts": 1, "vehicle_id": "TAMPERED"}\n',
    )
    out_dir = tmp_path / 'out'

    with pytest.raises(ValueError, match='mismatch'):
        run(data_dir, static_dir, FIRST_DAY, SECOND_DAY, out_dir)

    assert list(out_dir.glob('*.zip')) == []


def test_run_missing_day_in_the_middle_aborts(tmp_path: Path, data_dir: Path):
    """A gap inside the range aborts and names the missing day."""
    static_dir = data_dir / 'static'
    write_day(data_dir, FIRST_DAY)
    # 2026-08-28 deliberately not written at all
    write_day(data_dir, THIRD_DAY)
    out_dir = tmp_path / 'out'

    with pytest.raises(ValueError, match=SECOND_DAY):
        run(data_dir, static_dir, FIRST_DAY, THIRD_DAY, out_dir)


def test_run_prefix_comes_from_dir_name(tmp_path: Path):
    """Artifacts take their prefix from the data directory name."""
    # CLAUDE.md D2: no city literal in code. A data_dir named after some
    # other city must produce artifacts named after THAT city, not "sofia".
    data_dir = tmp_path / 'plovdiv'
    static_dir = data_dir / 'static'
    write_day(data_dir, FIRST_DAY, polls=None)
    static_dir.mkdir(parents=True)
    out_dir = tmp_path / 'out'

    packed = run(data_dir, static_dir, FIRST_DAY, FIRST_DAY, out_dir)

    assert packed['rt_zip_path'].name.startswith('plovdiv-rt_')
    assert packed['static_zip_path'].name.startswith('plovdiv-gtfs-static_')


# ─── write_sha256sums ────────────────────────────────────────────────────────


def test_sha256sums_format_is_compatible(tmp_path: Path):
    """Each line is "<digest>  <name>" like sha256sum prints."""
    first = tmp_path / 'a.zip'
    first.write_bytes(b'content a')
    second = tmp_path / 'b.zip'
    second.write_bytes(b'content b')

    sums_path = write_sha256sums(tmp_path, [first, second])
    lines = sums_path.read_text(encoding='utf-8').splitlines()

    assert lines[0] == f'{sha256_bytes(b"content a")}  a.zip'
    assert lines[1] == f'{sha256_bytes(b"content b")}  b.zip'


def test_sha256_of_gzip_matches_plain(tmp_path: Path):
    """The digest and size are over the uncompressed bytes."""
    payload = b'same bytes either way\n' * REPEAT_CONTENT
    plain = tmp_path / 'plain.jsonl'
    plain.write_bytes(payload)
    gzipped = tmp_path / 'gz.jsonl.gz'
    _write_maybe_gzipped(gzipped, payload, gzipped=True)

    expected = (sha256_bytes(payload), len(payload))

    assert sha256_of(plain) == expected
    assert sha256_of(gzipped) == expected
