"""Tests for verify_remote_checksums: verification, parsing, failures."""

import json
import subprocess
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from verify_remote_checksums import (
    RemoteUnreachable,
    ssh_remote_sha256sums,
    verify_pending,
)

SHA256_HEX_CHARS = 64
DATA_SHA = 'a' * SHA256_HEX_CHARS
POLLS_SHA = 'b' * SHA256_HEX_CHARS
CORRUPT_SHA = 'c' * SHA256_HEX_CHARS
REMOTE_DIR = '/opt/sofia-pt/data/sofia/'
SSH_HOST = 'root@vps'
SSH_KEY = 'ssh_key_file'
FIRST_DAY = '2026-08-27'
DATA_NAME = f'{FIRST_DAY}.jsonl'
POLLS_NAME = f'{FIRST_DAY}.polls.jsonl'
SSH_CONNECT_FAILED = 255
SSH_TIMEOUT_SEC = 30


def write_manifest(data_dir: Path, date_str: str, **overrides) -> Path:
    """Write a closed-day manifest with sensible defaults and overrides."""
    manifest = {
        'date': date_str,
        'data_file': f'{date_str}.jsonl',
        'data_sha256': DATA_SHA,
        'polls_file': f'{date_str}.polls.jsonl',
        'polls_sha256': POLLS_SHA,
        'day_in_progress': False,
    }
    manifest.update(overrides)
    path = data_dir / f'{date_str}.manifest.json'
    path.write_text(json.dumps(manifest), encoding='utf-8')
    return path


def read_manifest(path: Path) -> dict:
    """Load a manifest written by write_manifest or verify_pending."""
    return json.loads(path.read_text(encoding='utf-8'))


class RecordingGetter:
    """Remote-checksum getter that records its calls and replies by rule."""

    def __init__(self, reply: Callable[[list], dict] | None = None) -> None:
        """Remember how to answer; the default answer is an empty map."""
        self.reply = reply or (lambda names: {})
        self.remote_dirs: list = []
        self.calls: list = []

    def __call__(self, remote_dir, filenames):
        """Record the call, then answer like the remote host would."""
        self.remote_dirs.append(remote_dir)
        self.calls.append(list(filenames))
        return self.reply(filenames)


def _unreachable_getter(remote_dir, filenames):
    raise RemoteUnreachable('ssh connection failed')


def _hash_by_name(filenames) -> dict:
    return {
        name: (
            DATA_SHA
            if name.endswith('.jsonl') and 'polls' not in name
            else POLLS_SHA
        )
        for name in filenames
    }


def _completed(*args, returncode, stdout, stderr, **_kwargs):
    return subprocess.CompletedProcess(
        args=args,
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
    )


def _stub_ssh(monkeypatch, *, returncode=0, stdout='', stderr='') -> None:
    monkeypatch.setattr(
        subprocess,
        'run',
        partial(
            _completed,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
        ),
    )


def _raise_timeout(*_args, **_kwargs):
    raise subprocess.TimeoutExpired(cmd='ssh', timeout=SSH_TIMEOUT_SEC)


class CapturingRun:
    """Stand-in for subprocess.run that keeps the remote command string."""

    def __init__(self) -> None:
        """Start with no captured command."""
        self.remote_cmd = ''

    def __call__(self, cmd, **_kwargs):
        """Keep the last argv element and return an empty success."""
        # last argv element is the remote command string
        self.remote_cmd = cmd[-1]
        return subprocess.CompletedProcess(
            args=cmd,
            returncode=0,
            stdout='',
            stderr='',
        )


def _verify(data_dir: Path, getter) -> tuple[list[str], bool]:
    return verify_pending(data_dir, REMOTE_DIR, getter)


def _ssh_sums(names: list) -> dict:
    return ssh_remote_sha256sums(SSH_HOST, SSH_KEY, REMOTE_DIR, names)


# ─── verify_pending: the five required scenarios ───────────────────────────


def test_matching_checksums_marked_verified(tmp_path: Path):
    """Equal digests mark the manifest verified and log it."""
    manifest_path = write_manifest(tmp_path, FIRST_DAY)
    getter = RecordingGetter(
        lambda names: {DATA_NAME: DATA_SHA, POLLS_NAME: POLLS_SHA},
    )

    log_lines, mismatch_found = _verify(tmp_path, getter)

    assert getter.remote_dirs == [REMOTE_DIR]
    assert mismatch_found is False
    manifest = read_manifest(manifest_path)
    assert manifest['remote_verified'] is True
    assert (
        'remote_verified_at' in manifest,
        'remote_verify_note' in manifest,
    ) == (
        True,
        False,
    )
    assert any('verified' in line for line in log_lines)


def test_mismatched_checksum_is_flagged(tmp_path: Path):
    """A differing digest marks the manifest unverified and logs MISMATCH."""
    manifest_path = write_manifest(tmp_path, FIRST_DAY)
    # data corrupted somewhere between the VPS and the local copy
    getter = RecordingGetter(
        lambda names: {DATA_NAME: CORRUPT_SHA, POLLS_NAME: POLLS_SHA},
    )

    log_lines, mismatch_found = _verify(tmp_path, getter)

    assert mismatch_found is True
    manifest = read_manifest(manifest_path)
    assert manifest['remote_verified'] is False
    assert 'data_sha256' in manifest['remote_verify_note']
    assert any('MISMATCH' in line for line in log_lines)


def test_unreachable_host_leaves_manifest(tmp_path: Path):
    """An unreachable host is network noise, never a mismatch."""
    manifest_path = write_manifest(tmp_path, FIRST_DAY)

    log_lines, mismatch_found = verify_pending(
        tmp_path,
        REMOTE_DIR,
        _unreachable_getter,
    )

    assert mismatch_found is False
    assert 'remote_verified' not in read_manifest(manifest_path)
    assert any('unreachable' in line.lower() for line in log_lines)


def test_day_still_in_progress_is_never_checked(tmp_path: Path):
    """A day still being written never reaches the getter."""
    manifest_path = write_manifest(
        tmp_path,
        '2026-08-31',
        day_in_progress=True,
    )
    getter = RecordingGetter()

    verify_pending(tmp_path, REMOTE_DIR, getter)

    assert getter.calls == []  # getter never invoked
    assert 'remote_verified' not in read_manifest(manifest_path)


def test_already_verified_day_is_not_rechecked(tmp_path: Path):
    """A verified day is never read from the remote a second time."""
    write_manifest(
        tmp_path,
        '2026-08-26',
        remote_verified=True,
        remote_verified_at='2026-08-27T00:00:00+00:00',
    )
    getter = RecordingGetter()

    verify_pending(tmp_path, REMOTE_DIR, getter)

    assert getter.calls == []  # one remote read per file, for its lifetime


# ─── supporting behavior ────────────────────────────────────────────────────


def test_remote_file_missing_is_not_a_mismatch(tmp_path: Path):
    """A host that answers without the files is treated like unreachable."""
    manifest_path = write_manifest(tmp_path, FIRST_DAY)
    # host answered, but these files aren't there (cleaned up manually?)
    getter = RecordingGetter()

    log_lines, mismatch_found = _verify(tmp_path, getter)

    assert mismatch_found is False
    assert 'remote_verified' not in read_manifest(manifest_path)
    assert any('not found' in line for line in log_lines)


def test_pending_days_batch_into_one_call(tmp_path: Path):
    """All pending files go to the remote in a single round trip."""
    write_manifest(tmp_path, '2026-08-25')
    write_manifest(tmp_path, '2026-08-26')
    getter = RecordingGetter(_hash_by_name)

    verify_pending(tmp_path, REMOTE_DIR, getter)

    assert len(getter.calls) == 1  # one ssh round trip, not one per day
    assert getter.calls[0] == [
        '2026-08-25.jsonl',
        '2026-08-25.polls.jsonl',
        '2026-08-26.jsonl',
        '2026-08-26.polls.jsonl',
    ]


def test_no_pending_manifests_is_a_no_op(tmp_path: Path):
    """An empty directory never calls the getter."""
    getter = RecordingGetter()

    _log_lines, mismatch_found = _verify(tmp_path, getter)

    assert getter.calls == []
    assert mismatch_found is False


def test_no_heartbeat_day_needs_data_file_only(tmp_path: Path):
    """A day without a polls file asks only for its data file."""
    # matches build_manifest()'s own polls_file=None when heartbeat is off
    manifest_path = write_manifest(
        tmp_path,
        '2026-08-20',
        polls_file=None,
        polls_sha256=None,
    )
    getter = RecordingGetter(lambda names: {'2026-08-20.jsonl': DATA_SHA})

    _log_lines, mismatch_found = _verify(tmp_path, getter)

    # never asks for a polls file that doesn't exist
    assert getter.calls == [['2026-08-20.jsonl']]
    assert mismatch_found is False
    assert read_manifest(manifest_path)['remote_verified'] is True


# ─── ssh_remote_sha256sums: parsing and failure classification ─────────────


def test_ssh_sums_parse_sha256sum_output(monkeypatch):
    """Each "<digest>  <path>" line maps its basename to the digest."""
    remote_prefix = '/opt/sofia-pt/data/sofia'
    _stub_ssh(
        monkeypatch,
        stdout=(
            f'{DATA_SHA}  {remote_prefix}/{DATA_NAME}\n'
            f'{POLLS_SHA}  {remote_prefix}/{POLLS_NAME}\n'
        ),
    )

    parsed = _ssh_sums([DATA_NAME, POLLS_NAME])

    assert parsed == {DATA_NAME: DATA_SHA, POLLS_NAME: POLLS_SHA}


def test_ssh_sums_unreachable_on_connect_fail(monkeypatch):
    """A failed ssh connection raises without leaking the host address."""
    _stub_ssh(
        monkeypatch,
        returncode=SSH_CONNECT_FAILED,
        stderr='ssh: connect to host 203.0.113.5 port 22: timed out\n',
    )

    with pytest.raises(RemoteUnreachable) as excinfo:
        _ssh_sums([DATA_NAME])

    # the real ssh stderr (which can contain the VPS address) must never
    # end up inside the exception message that gets logged
    assert '203.0.113.5' not in str(excinfo.value)


def test_ssh_sums_command_falls_back_to_gzip(monkeypatch):
    """A day gzipped on the VPS is decompressed before it is hashed.

    A day sofia-compress.service has already gzipped on the VPS only has
    "<name>.gz" there, not "<name>" — the remote command must decompress
    before hashing, or the digest would be over gzip bytes and never match
    manifest["data_sha256"] (always computed over uncompressed content).
    """
    capture = CapturingRun()
    monkeypatch.setattr(subprocess, 'run', capture)

    _ssh_sums([DATA_NAME])

    # the requested (uncompressed) name feeds the loop
    assert f'"{DATA_NAME}"' in capture.remote_cmd
    # decompress-then-hash fallback, not a bare sha256sum of the .gz
    assert 'gzip -dc' in capture.remote_cmd
    # checked against the SAME base path as the plain-file branch
    assert '"$p.gz"' in capture.remote_cmd


def test_ssh_sums_parse_the_gzip_fallback_output(monkeypatch):
    """Parsing does not care which remote branch printed the line.

    Whatever the remote for-loop's gzip-fallback branch prints for an
    already-compressed day, it's the same "<digest>  <path>" shape as a
    plain sha256sum line and names the uncompressed path.
    """
    _stub_ssh(
        monkeypatch,
        stdout=f'{DATA_SHA}  {REMOTE_DIR.rstrip("/")}/{DATA_NAME}\n',
    )

    parsed = _ssh_sums([DATA_NAME])

    assert parsed == {DATA_NAME: DATA_SHA}


def test_ssh_sums_raise_unreachable_on_timeout(monkeypatch):
    """A timeout from subprocess.run becomes RemoteUnreachable."""
    monkeypatch.setattr(subprocess, 'run', _raise_timeout)

    with pytest.raises(RemoteUnreachable):
        _ssh_sums([DATA_NAME])
