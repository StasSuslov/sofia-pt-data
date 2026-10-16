"""Tests for scripts/drop_superseded_plain.sh, the fetch pipeline's deleter.

It is the only step of the fetch pipeline that deletes a local day file. Each
case is a plain/.gz pair the script meets in practice; the assertion is which
of the two survives.
"""

import gzip
import subprocess
from pathlib import Path

LINES_PER_DAY = 500
CUT_AT = 1234
RESUME_AT = 1235
LONG_PREFIX = 2000
SHORT_PREFIX = 100
SCRIPT = Path(__file__).parent / 'drop_superseded_plain.sh'
DAY = b'{"snapshot_ts": 1}\n' * LINES_PER_DAY


def run_on(tmp_path: Path, plain: bytes, gz: bytes) -> tuple[bool, bool]:
    """Run the script on one plain/.gz pair; return which files survive."""
    city = tmp_path / 'sofia'
    city.mkdir()
    plain_file = city / '2026-09-15.jsonl'
    gz_file = city / '2026-09-15.jsonl.gz'
    plain_file.write_bytes(plain)
    gz_file.write_bytes(gz)
    subprocess.run(['bash', str(SCRIPT), str(tmp_path)], check=True)
    return plain_file.exists(), gz_file.exists()


def test_identical_plain_dropped(tmp_path: Path):
    """An identical plain copy is dropped."""
    survivors = run_on(tmp_path, DAY, gzip.compress(DAY))
    assert survivors == (False, True)


def test_truncated_pull_dropped(tmp_path: Path):
    """A truncated pull is dropped once the gz holds the whole day."""
    survivors = run_on(tmp_path, DAY[:CUT_AT], gzip.compress(DAY))
    assert survivors == (False, True)


def test_diverging_plain_kept(tmp_path: Path):
    """A plain copy that diverges from the gz is kept."""
    head, tail = DAY[:CUT_AT], DAY[RESUME_AT:]
    other = b'X'.join((head, tail))
    plain = other[:LONG_PREFIX]
    survivors = run_on(tmp_path, plain, gzip.compress(DAY))
    assert survivors == (True, True)


def test_plain_kept_when_gz_cut_short(tmp_path: Path):
    """A plain copy is kept when the gz is cut short."""
    gz = gzip.compress(DAY)
    # A gz cut mid-stream still decompresses its head, so without gzip -t the
    # prefix check alone would trade a partial plain file for a partial gz.
    plain = DAY[:SHORT_PREFIX]
    cut_gz = gz[: len(gz) // 2]
    survivors = run_on(tmp_path, plain, cut_gz)
    assert survivors == (True, True)
