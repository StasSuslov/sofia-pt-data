import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from verify_remote_checksums import RemoteUnreachable, ssh_remote_sha256sums, verify_pending

DATA_SHA = "a" * 64
POLLS_SHA = "b" * 64
REMOTE_DIR = "/opt/sofia-pt/data/sofia/"


def write_manifest(data_dir: Path, date_str: str, **overrides) -> Path:
    manifest = {
        "date": date_str,
        "data_file": f"{date_str}.jsonl",
        "data_sha256": DATA_SHA,
        "polls_file": f"{date_str}.polls.jsonl",
        "polls_sha256": POLLS_SHA,
        "day_in_progress": False,
    }
    manifest.update(overrides)
    path = data_dir / f"{date_str}.manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def read_manifest(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ─── verify_pending: the five required scenarios ───────────────────────────

def test_matching_checksums_are_recorded_as_verified(tmp_path: Path):
    manifest_path = write_manifest(tmp_path, "2026-08-27")

    def getter(remote_dir, filenames):
        assert remote_dir == REMOTE_DIR
        return {"2026-08-27.jsonl": DATA_SHA, "2026-08-27.polls.jsonl": POLLS_SHA}

    log_lines, mismatch_found = verify_pending(tmp_path, REMOTE_DIR, getter)

    assert mismatch_found is False
    manifest = read_manifest(manifest_path)
    assert manifest["remote_verified"] is True
    assert "remote_verified_at" in manifest
    assert "remote_verify_note" not in manifest
    assert any("verified" in line for line in log_lines)


def test_mismatched_checksum_is_flagged_and_reported(tmp_path: Path):
    manifest_path = write_manifest(tmp_path, "2026-08-27")

    def getter(remote_dir, filenames):
        # data corrupted somewhere between the VPS and the local copy
        return {"2026-08-27.jsonl": "c" * 64, "2026-08-27.polls.jsonl": POLLS_SHA}

    log_lines, mismatch_found = verify_pending(tmp_path, REMOTE_DIR, getter)

    assert mismatch_found is True
    manifest = read_manifest(manifest_path)
    assert manifest["remote_verified"] is False
    assert "data_sha256" in manifest["remote_verify_note"]
    assert any("MISMATCH" in line for line in log_lines)


def test_unreachable_host_leaves_manifest_untouched(tmp_path: Path):
    manifest_path = write_manifest(tmp_path, "2026-08-27")

    def getter(remote_dir, filenames):
        raise RemoteUnreachable("ssh connection failed")

    log_lines, mismatch_found = verify_pending(tmp_path, REMOTE_DIR, getter)

    # unreachable is routine network noise, never a mismatch
    assert mismatch_found is False
    manifest = read_manifest(manifest_path)
    assert "remote_verified" not in manifest
    assert any("unreachable" in line.lower() for line in log_lines)


def test_day_still_in_progress_is_never_checked(tmp_path: Path):
    manifest_path = write_manifest(tmp_path, "2026-08-31", day_in_progress=True)
    calls = []

    def getter(remote_dir, filenames):
        calls.append(filenames)
        return {}

    verify_pending(tmp_path, REMOTE_DIR, getter)

    assert calls == []  # getter never invoked — nothing to write home about
    assert "remote_verified" not in read_manifest(manifest_path)


def test_already_verified_day_is_not_rechecked(tmp_path: Path):
    write_manifest(tmp_path, "2026-08-26", remote_verified=True, remote_verified_at="2026-08-27T00:00:00+00:00")
    calls = []

    def getter(remote_dir, filenames):
        calls.append(filenames)
        return {}

    verify_pending(tmp_path, REMOTE_DIR, getter)

    assert calls == []  # one remote read per file, for the life of the file


# ─── supporting behavior ────────────────────────────────────────────────────

def test_remote_file_missing_is_treated_like_unreachable_not_a_mismatch(tmp_path: Path):
    manifest_path = write_manifest(tmp_path, "2026-08-27")

    def getter(remote_dir, filenames):
        return {}  # host answered, but these files aren't there (cleaned up manually?)

    log_lines, mismatch_found = verify_pending(tmp_path, REMOTE_DIR, getter)

    assert mismatch_found is False
    assert "remote_verified" not in read_manifest(manifest_path)
    assert any("not found" in line for line in log_lines)


def test_multiple_pending_days_batch_into_a_single_getter_call(tmp_path: Path):
    write_manifest(tmp_path, "2026-08-25")
    write_manifest(tmp_path, "2026-08-26")
    calls = []

    def getter(remote_dir, filenames):
        calls.append(sorted(filenames))
        return {name: (DATA_SHA if name.endswith(".jsonl") and "polls" not in name else POLLS_SHA) for name in filenames}

    verify_pending(tmp_path, REMOTE_DIR, getter)

    assert len(calls) == 1  # one ssh round trip, not one per pending day
    assert calls[0] == [
        "2026-08-25.jsonl", "2026-08-25.polls.jsonl",
        "2026-08-26.jsonl", "2026-08-26.polls.jsonl",
    ]


def test_no_pending_manifests_is_a_no_op(tmp_path: Path):
    calls = []

    def getter(remote_dir, filenames):
        calls.append(filenames)
        return {}

    log_lines, mismatch_found = verify_pending(tmp_path, REMOTE_DIR, getter)

    assert calls == []
    assert mismatch_found is False


def test_heartbeat_unavailable_day_only_needs_the_data_file_verified(tmp_path: Path):
    # matches build_manifest()'s own polls_file=None when heartbeat_available is False
    manifest_path = write_manifest(tmp_path, "2026-08-20", polls_file=None, polls_sha256=None)

    def getter(remote_dir, filenames):
        assert filenames == ["2026-08-20.jsonl"]  # never asks for a polls file that doesn't exist
        return {"2026-08-20.jsonl": DATA_SHA}

    log_lines, mismatch_found = verify_pending(tmp_path, REMOTE_DIR, getter)

    assert mismatch_found is False
    assert read_manifest(manifest_path)["remote_verified"] is True


# ─── ssh_remote_sha256sums: parsing and failure classification ─────────────

def test_ssh_remote_sha256sums_parses_sha256sum_output(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(
            args=args, returncode=0,
            stdout=f"{DATA_SHA}  /opt/sofia-pt/data/sofia/2026-08-27.jsonl\n{POLLS_SHA}  /opt/sofia-pt/data/sofia/2026-08-27.polls.jsonl\n",
            stderr="",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = ssh_remote_sha256sums("root@vps", "/tmp/key", REMOTE_DIR, ["2026-08-27.jsonl", "2026-08-27.polls.jsonl"])

    assert result == {"2026-08-27.jsonl": DATA_SHA, "2026-08-27.polls.jsonl": POLLS_SHA}


def test_ssh_remote_sha256sums_raises_unreachable_on_ssh_connection_failure(monkeypatch):
    def fake_run(*args, **kwargs):
        return subprocess.CompletedProcess(args=args, returncode=255, stdout="", stderr="ssh: connect to host 203.0.113.5 port 22: Operation timed out\n")

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        ssh_remote_sha256sums("root@vps", "/tmp/key", REMOTE_DIR, ["2026-08-27.jsonl"])
        assert False, "expected RemoteUnreachable"
    except RemoteUnreachable as exc:
        # the real ssh stderr (which can contain the VPS address) must never
        # end up inside the exception message that gets logged
        assert "203.0.113.5" not in str(exc)


def test_ssh_remote_sha256sums_raises_unreachable_on_timeout(monkeypatch):
    def fake_run(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="ssh", timeout=30)

    monkeypatch.setattr(subprocess, "run", fake_run)
    try:
        ssh_remote_sha256sums("root@vps", "/tmp/key", REMOTE_DIR, ["2026-08-27.jsonl"])
        assert False, "expected RemoteUnreachable"
    except RemoteUnreachable:
        pass
