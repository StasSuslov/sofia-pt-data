import gzip
import hashlib
import json
import sys
import zipfile
from pathlib import Path

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


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_day(data_dir: Path, date_str: str, *, records: bytes = b'{"snapshot_ts": 1000, "vehicle_id": "A1"}\n',
              polls: bytes | None = b'{"snapshot_ts": 1000, "fetch_ok": true, "vehicle_count": 1}\n',
              gzip_data: bool = False, gzip_polls: bool = False, day_in_progress: bool = False) -> dict:
    """
    Write a minimal but real <date>.jsonl(+.gz)/<date>.polls.jsonl(+.gz)/
    <date>.manifest.json trio, with the manifest's hashes computed the same
    way generate_manifest.py would: over the UNCOMPRESSED bytes regardless
    of what's on disk. Returns the manifest dict actually written, so tests
    can tweak-and-rewrite it for the mismatch cases.
    """
    data_dir.mkdir(parents=True, exist_ok=True)

    if gzip_data:
        data_path = data_dir / f"{date_str}.jsonl.gz"
        with gzip.open(data_path, "wb") as f:
            f.write(records)
    else:
        data_path = data_dir / f"{date_str}.jsonl"
        data_path.write_bytes(records)

    manifest = {
        "schema_version": 2,
        "date": date_str,
        "data_file": f"{date_str}.jsonl",
        "data_sha256": sha256_bytes(records),
        "data_size_bytes": len(records),
        "compressed": gzip_data,
        "total_vehicle_records": 1,
        "day_in_progress": day_in_progress,
        "coverage_pct": 100.0,
        "gap_count": 0,
        "polls_file": None,
        "polls_sha256": None,
    }

    if polls is not None:
        if gzip_polls:
            polls_path = data_dir / f"{date_str}.polls.jsonl.gz"
            with gzip.open(polls_path, "wb") as f:
                f.write(polls)
        else:
            polls_path = data_dir / f"{date_str}.polls.jsonl"
            polls_path.write_bytes(polls)
        manifest["polls_file"] = f"{date_str}.polls.jsonl"
        manifest["polls_sha256"] = sha256_bytes(polls)

    manifest_path = data_dir / f"{date_str}.manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    return manifest


def write_static_snapshot(static_dir: Path, date_str: str, *, content: bytes = b"members,go,here\n") -> Path:
    static_dir.mkdir(parents=True, exist_ok=True)
    zip_path = static_dir / f"gtfs_{date_str}.zip"
    with zipfile.ZipFile(zip_path, "w") as z:
        z.writestr("routes.txt", content)
    zip_bytes = zip_path.read_bytes()
    manifest = {
        "file": zip_path.name,
        "zip_sha256": sha256_bytes(zip_bytes),
        "size_bytes": len(zip_bytes),
        "member_count": 1,
    }
    (static_dir / f"gtfs_{date_str}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return zip_path


# ─── date_range ──────────────────────────────────────────────────────────────

def test_date_range_inclusive_of_both_ends():
    assert date_range("2026-08-27", "2026-08-29") == ["2026-08-27", "2026-08-28", "2026-08-29"]


def test_date_range_single_day():
    assert date_range("2026-08-27", "2026-08-27") == ["2026-08-27"]


def test_date_range_end_before_start_raises():
    try:
        date_range("2026-08-29", "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "before" in str(e)


# ─── verify_day ──────────────────────────────────────────────────────────────

def test_verify_day_happy_path(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27")

    result = verify_day(data_dir, "2026-08-27")

    assert result["data_path"].name == "2026-08-27.jsonl"
    assert result["polls_path"].name == "2026-08-27.polls.jsonl"


def test_verify_day_no_heartbeat_declared_is_not_an_error(tmp_path: Path):
    # The real 2026-08-27 case: collection started before heartbeat logging
    # existed, so the manifest itself records polls_file: null. No
    # .polls.jsonl on disk at all must NOT be treated as a missing file.
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27", polls=None)

    result = verify_day(data_dir, "2026-08-27")

    assert result["polls_path"] is None


def test_verify_day_hash_mismatch_aborts_and_names_the_file(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27")
    # Corrupt the data file after its manifest was written.
    (data_dir / "2026-08-27.jsonl").write_bytes(b'{"snapshot_ts": 1000, "vehicle_id": "TAMPERED"}\n')

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "2026-08-27.jsonl" in str(e)
        assert "mismatch" in str(e)


def test_verify_day_polls_hash_mismatch_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27")
    (data_dir / "2026-08-27.polls.jsonl").write_bytes(b'{"snapshot_ts": 1000, "fetch_ok": false}\n')

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "2026-08-27.polls.jsonl" in str(e)


def test_verify_day_missing_manifest_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    data_dir.mkdir()
    (data_dir / "2026-08-27.jsonl").write_bytes(b'{"snapshot_ts": 1000}\n')
    # no .manifest.json written at all

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "manifest" in str(e)


def test_verify_day_missing_data_file_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27")
    (data_dir / "2026-08-27.jsonl").unlink()

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "2026-08-27" in str(e)


def test_verify_day_missing_declared_polls_file_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27")
    (data_dir / "2026-08-27.polls.jsonl").unlink()

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "heartbeat" in str(e)


def test_verify_day_stale_schema_version_aborts(tmp_path: Path):
    # A schema-1 manifest predates day_in_progress: absent, it would read as
    # "closed" and a still-open day would sail into a permanent record.
    data_dir = tmp_path / "sofia"
    manifest = write_day(data_dir, "2026-08-27")
    manifest["schema_version"] = 1
    del manifest["day_in_progress"]
    (data_dir / "2026-08-27.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "schema_version" in str(e)


def test_verify_day_manifest_without_schema_version_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    manifest = write_day(data_dir, "2026-08-27")
    del manifest["schema_version"]
    (data_dir / "2026-08-27.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")

    try:
        verify_day(data_dir, "2026-08-27")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "schema_version" in str(e)


def test_verify_day_in_progress_day_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-09-02", day_in_progress=True)

    try:
        verify_day(data_dir, "2026-09-02")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "in progress" in str(e)


def test_verify_day_gzipped_day_matches_uncompressed_hash(tmp_path: Path):
    # The manifest always records the hash of the UNCOMPRESSED bytes (see
    # generate_manifest.py's sha256_of docstring) even when the day is
    # gzipped on disk — verify_day must recompute the same way.
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27", gzip_data=True, gzip_polls=True)

    result = verify_day(data_dir, "2026-08-27")

    assert result["data_path"].name == "2026-08-27.jsonl.gz"
    assert result["polls_path"].name == "2026-08-27.polls.jsonl.gz"


# ─── find_static_snapshots ──────────────────────────────────────────────────

def test_find_static_snapshots_filters_by_range_and_verifies_hash(tmp_path: Path):
    static_dir = tmp_path / "static"
    write_static_snapshot(static_dir, "2026-08-27")
    write_static_snapshot(static_dir, "2026-08-31")
    write_static_snapshot(static_dir, "2026-09-05")  # outside range

    snapshots = find_static_snapshots(static_dir, "2026-08-27", "2026-09-01")

    assert [s["date"] for s in snapshots] == ["2026-08-27", "2026-08-31"]


def test_find_static_snapshots_hash_mismatch_aborts(tmp_path: Path):
    static_dir = tmp_path / "static"
    zip_path = write_static_snapshot(static_dir, "2026-08-27")
    with zipfile.ZipFile(zip_path, "a") as z:
        z.writestr("extra.txt", "tampered after manifest was written")

    try:
        find_static_snapshots(static_dir, "2026-08-27", "2026-09-01")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "gtfs_2026-08-27.zip" in str(e)


def test_find_static_snapshots_missing_manifest_aborts(tmp_path: Path):
    static_dir = tmp_path / "static"
    write_static_snapshot(static_dir, "2026-08-27")
    (static_dir / "gtfs_2026-08-27.manifest.json").unlink()

    try:
        find_static_snapshots(static_dir, "2026-08-27", "2026-09-01")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "manifest" in str(e)


def test_find_static_snapshots_missing_static_dir_aborts(tmp_path: Path):
    try:
        find_static_snapshots(tmp_path / "does-not-exist", "2026-08-27", "2026-09-01")
        assert False, "expected ValueError"
    except ValueError as e:
        assert "static directory" in str(e)


# ─── find_skipped_files ──────────────────────────────────────────────────────

def test_find_skipped_files_reports_stray_and_out_of_range(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    write_day(data_dir, "2026-08-27")
    write_day(data_dir, "2026-09-02")  # outside the range below
    (data_dir / "2026-08-27.log").write_text("stray log", encoding="utf-8")
    (data_dir / ".DS_Store").write_bytes(b"\x00")

    verified = [verify_day(data_dir, "2026-08-27")]
    skipped = find_skipped_files(data_dir, verified, "2026-08-27", "2026-08-27")

    names = dict(skipped)
    assert "2026-08-27.log" in names and "not a recognized" in names["2026-08-27.log"]
    assert ".DS_Store" in names
    assert "2026-09-02.jsonl" in names and "outside requested range" in names["2026-09-02.jsonl"]
    assert "2026-09-02.manifest.json" in names
    # consumed files for the day actually packed must not show up as skipped
    assert "2026-08-27.jsonl" not in names
    assert "2026-08-27.manifest.json" not in names
    assert "2026-08-27.polls.jsonl" not in names


# ─── run() end-to-end ────────────────────────────────────────────────────────

def test_run_packs_flat_and_gzipped_days_and_round_trips(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    static_dir = data_dir / "static"
    write_day(data_dir, "2026-08-27", records=b'{"snapshot_ts": 1000, "vehicle_id": "A1"}\n' * 50, gzip_data=True, gzip_polls=True)
    write_day(data_dir, "2026-08-28", records=b'{"snapshot_ts": 2000, "vehicle_id": "B1"}\n' * 50)
    write_static_snapshot(static_dir, "2026-08-27")
    out_dir = tmp_path / "out"

    result = run(data_dir, static_dir, "2026-08-27", "2026-08-28", out_dir)

    assert result["rt_zip_path"].exists()
    assert result["static_zip_path"].exists()
    assert result["sums_path"].exists()
    assert result["rt_zip_path"].name == "sofia-rt_2026-08-27_2026-08-28.zip"
    assert result["static_zip_path"].name == "sofia-gtfs-static_2026-08-27_2026-08-28.zip"

    # Round-trip: every member inside the RT zip is stored DECOMPRESSED and
    # hashes to exactly what its manifest (also inside the zip) declares,
    # regardless of whether the source was flat or gzipped on disk.
    with zipfile.ZipFile(result["rt_zip_path"]) as zf:
        names = set(zf.namelist())
        assert {"2026-08-27.jsonl", "2026-08-27.polls.jsonl", "2026-08-27.manifest.json",
                "2026-08-28.jsonl", "2026-08-28.polls.jsonl", "2026-08-28.manifest.json"} <= names

        manifest_27 = json.loads(zf.read("2026-08-27.manifest.json"))
        extracted_hash = sha256_bytes(zf.read("2026-08-27.jsonl"))
        assert extracted_hash == manifest_27["data_sha256"]

        info = zf.getinfo("2026-08-27.jsonl")
        assert info.compress_type == zipfile.ZIP_DEFLATED

    with zipfile.ZipFile(result["static_zip_path"]) as zf:
        assert "gtfs_2026-08-27.zip" in zf.namelist()
        assert "gtfs_2026-08-27.manifest.json" in zf.namelist()
        assert zf.getinfo("gtfs_2026-08-27.zip").compress_type == zipfile.ZIP_STORED

    # METHODOLOGY.md and README.md ship with the record (CLAUDE.md section 8)
    # and are covered by the same SHA256SUMS.txt as the zips, so a reader
    # verifies the whole download with one `sha256sum -c`.
    sums = result["sums_path"].read_text()
    for name in ("METHODOLOGY.md", "README.md"):
        copied = out_dir / name
        assert copied.exists()
        assert copied.read_bytes() == (Path(__file__).resolve().parent.parent / name).read_bytes()
        assert f"  {name}\n" in sums
    assert f"  {result['rt_zip_path'].name}\n" in sums


def test_run_verifies_before_packing_anything(tmp_path: Path):
    # A mismatch on the SECOND day must mean NO zip is written at all — not a
    # half-packed archive containing only the first, valid day.
    data_dir = tmp_path / "sofia"
    static_dir = data_dir / "static"
    write_day(data_dir, "2026-08-27")
    write_day(data_dir, "2026-08-28")
    (data_dir / "2026-08-28.jsonl").write_bytes(b'{"snapshot_ts": 1, "vehicle_id": "TAMPERED"}\n')
    out_dir = tmp_path / "out"

    try:
        run(data_dir, static_dir, "2026-08-27", "2026-08-28", out_dir)
        assert False, "expected ValueError"
    except ValueError:
        pass

    assert list(out_dir.glob("*.zip")) == []


def test_run_missing_day_in_the_middle_of_the_range_aborts(tmp_path: Path):
    data_dir = tmp_path / "sofia"
    static_dir = data_dir / "static"
    write_day(data_dir, "2026-08-27")
    # 2026-08-28 deliberately not written at all
    write_day(data_dir, "2026-08-29")
    out_dir = tmp_path / "out"

    try:
        run(data_dir, static_dir, "2026-08-27", "2026-08-29", out_dir)
        assert False, "expected ValueError"
    except ValueError as e:
        assert "2026-08-28" in str(e)


def test_run_prefix_derived_from_data_dir_basename_not_hardcoded(tmp_path: Path):
    # CLAUDE.md D2: no city literal in code. A data_dir named after some
    # other city must produce artifacts named after THAT city, not "sofia".
    data_dir = tmp_path / "plovdiv"
    static_dir = data_dir / "static"
    write_day(data_dir, "2026-08-27", polls=None)
    static_dir.mkdir(parents=True)
    out_dir = tmp_path / "out"

    result = run(data_dir, static_dir, "2026-08-27", "2026-08-27", out_dir)

    assert result["rt_zip_path"].name.startswith("plovdiv-rt_")
    assert result["static_zip_path"].name.startswith("plovdiv-gtfs-static_")


# ─── write_sha256sums ────────────────────────────────────────────────────────

def test_write_sha256sums_format_is_sha256sum_compatible(tmp_path: Path):
    a = tmp_path / "a.zip"
    a.write_bytes(b"content a")
    b = tmp_path / "b.zip"
    b.write_bytes(b"content b")

    sums_path = write_sha256sums(tmp_path, [a, b])
    text = sums_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    assert lines[0] == f"{sha256_bytes(b'content a')}  a.zip"
    assert lines[1] == f"{sha256_bytes(b'content b')}  b.zip"


def test_sha256_of_gzipped_file_matches_uncompressed_content(tmp_path: Path):
    content = b"same bytes either way\n" * 100
    plain = tmp_path / "plain.jsonl"
    plain.write_bytes(content)
    gz = tmp_path / "gz.jsonl.gz"
    with gzip.open(gz, "wb") as f:
        f.write(content)

    plain_digest, plain_size = sha256_of(plain)
    gz_digest, gz_size = sha256_of(gz)

    assert plain_digest == gz_digest == sha256_bytes(content)
    assert plain_size == gz_size == len(content)
