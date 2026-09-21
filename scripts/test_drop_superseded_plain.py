"""
Tests for scripts/drop_superseded_plain.sh, the only step of the fetch
pipeline that deletes a local day file. Each case is a plain/.gz pair the
script meets in practice; the assertion is which of the two survives.
"""

import gzip
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).parent / "drop_superseded_plain.sh"
DAY = b'{"snapshot_ts": 1}\n' * 500


def run_on(tmp_path: Path, plain: bytes, gz: bytes) -> tuple[bool, bool]:
    city = tmp_path / "sofia"
    city.mkdir()
    (city / "2026-09-15.jsonl").write_bytes(plain)
    (city / "2026-09-15.jsonl.gz").write_bytes(gz)
    subprocess.run(["bash", str(SCRIPT), str(tmp_path)], check=True)
    return (city / "2026-09-15.jsonl").exists(), (city / "2026-09-15.jsonl.gz").exists()


def test_identical_plain_copy_is_dropped(tmp_path: Path):
    assert run_on(tmp_path, DAY, gzip.compress(DAY)) == (False, True)


def test_truncated_pull_is_dropped_once_the_gz_holds_the_whole_day(tmp_path: Path):
    assert run_on(tmp_path, DAY[:1234], gzip.compress(DAY)) == (False, True)


def test_diverging_plain_copy_is_kept(tmp_path: Path):
    other = DAY[:1234] + b"X" + DAY[1235:]
    assert run_on(tmp_path, other[:2000], gzip.compress(DAY)) == (True, True)


def test_plain_copy_is_kept_when_the_gz_is_cut_short(tmp_path: Path):
    gz = gzip.compress(DAY)
    # A gz cut mid-stream still decompresses its head, so without gzip -t the
    # prefix check alone would trade a partial plain file for a partial gz.
    assert run_on(tmp_path, DAY[:100], gz[: len(gz) // 2]) == (True, True)
