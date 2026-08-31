#!/usr/bin/env python3
"""
Close the provenance gap generate_manifest.py leaves open: its data_sha256 /
polls_sha256 are computed from the LOCAL copy after rsync already landed it,
so they prove the local file is self-consistent, not that it's byte-identical
to what collect.py actually wrote on the VPS. This script reads the real
checksum off the VPS, once, for each closed day, and records the comparison
in that day's manifest.

Deliberately NOT `rsync --checksum`: that re-hashes every file on both ends
on every run, which on a multi-GB archive pulled every couple of hours is
real bandwidth and CPU on a $4/mo droplet that's also busy collecting. A file
that's already closed for the day never changes again, so checking it once
and remembering the answer is enough — see manifest_is_current() in
generate_manifest.py for the same one-shot-then-remember idea applied to the
local hash.

Rules encoded here (see CLAUDE.md section 9 / the task this was written for):
  - A day still being written (day_in_progress: true) is never checked — it's
    expected to differ from whatever's on the VPS mid-write, and that's not
    a finding.
  - A day already carrying a remote_verified value (true OR false) is never
    re-checked. One remote read per file, for the life of the file.
  - A checksum MISMATCH means possible corruption and must be loud: recorded
    in the manifest, logged, and surfaced via a distinct process exit code.
  - The VPS being unreachable is routine network noise, not evidence of
    corruption: it's logged and the day is left unverified for the next run
    to retry, and it must never be reported through the same exit code as a
    real mismatch.

Usage:
    python scripts/verify_remote_checksums.py data/sofia \
        --vps-host root@203.0.113.5 --vps-key ~/.ssh/sofia_pt_do \
        --remote-dir /opt/sofia-pt/data/sofia/
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

# Keep in sync with CHECKSUM_MISMATCH_EXIT_CODE in scripts/fetch_data.sh and
# scripts/scheduled_fetch.sh — same convention as MANIFEST_FAILED_EXIT_CODE
# already uses to let a shell caller tell exit codes apart without parsing
# stdout.
MISMATCH_EXIT_CODE = 43

RemoteChecksumGetter = Callable[[str, list[str]], dict[str, str]]


class RemoteUnreachable(Exception):
    """
    The VPS couldn't be reached (or ssh itself failed) — as opposed to the
    VPS answering with a checksum that doesn't match. Never carries the raw
    ssh host or stderr text: those can contain the VPS address, and this
    exception's message is the kind of thing that ends up in logs/fetch.log.
    """


def ssh_remote_sha256sums(vps_host: str, vps_key: str, remote_dir: str, filenames: list[str]) -> dict[str, str]:
    """
    One ssh call, one remote `for` loop over every requested filename —
    verifying N pending days costs one round trip, not N. Returns
    {filename: hexdigest} for whichever of the requested files still exist
    on the remote side (plain or gzipped); a name simply absent from the
    result means that file wasn't found there in either form (manual
    cleanup, or a transfer that hasn't happened yet), which the caller
    treats as "can't verify yet", not as a mismatch.

    `filenames` are always the UNCOMPRESSED names — manifest["data_file"] /
    manifest["polls_file"] never carry a .gz suffix, per CLAUDE.md's
    provenance invariant. deploy/sofia-compress.service may have gzipped
    that day on the VPS after the manifest was written and before this runs,
    so each name is checked plain first, falling back to decompress-then-
    hash of "<name>.gz" — either way the digest is over the same
    uncompressed bytes generate_manifest.py hashed locally, and the emitted
    line always names the uncompressed path so parsing below can't tell
    which branch ran.
    """
    remote_dir = remote_dir.rstrip("/")
    names_quoted = " ".join(f'"{name}"' for name in filenames)
    # stderr redirected on the remote side: nothing here should print to it
    # given the -f guards, but a permission error or a corrupt .gz must not
    # abort the batch or leak a remote path into our own stderr.
    remote_cmd = (
        f"for f in {names_quoted}; do "
        f'p="{remote_dir}/$f"; '
        f'if [ -f "$p" ]; then sha256sum "$p"; '
        f'elif [ -f "$p.gz" ]; then h=$(gzip -dc "$p.gz" | sha256sum | cut -d\' \' -f1); echo "$h  $p"; '
        f"fi; done 2>/dev/null"
    )
    try:
        result = subprocess.run(
            ["ssh", "-i", vps_key, "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", vps_host, remote_cmd],
            capture_output=True, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RemoteUnreachable(type(exc).__name__) from exc

    # ssh reserves exit code 255 for its OWN connection-level failures (auth,
    # timeout, refused, DNS) rather than the remote command's exit status —
    # that's the one code that means "never actually ran". A non-zero exit
    # otherwise just means sha256sum itself was unhappy (e.g. a missing
    # file), and stdout still holds valid lines for every file that WAS
    # found, so that case falls through to the normal parsing below.
    if result.returncode == 255:
        raise RemoteUnreachable("ssh connection failed")

    checksums = {}
    for line in result.stdout.splitlines():
        digest, _, path = line.partition("  ")
        if digest and path:
            checksums[Path(path.strip()).name] = digest
    return checksums


def find_pending_manifests(data_dir: Path) -> list[Path]:
    """Closed-day manifests with no remote verification recorded yet."""
    pending = []
    for manifest_path in sorted(data_dir.glob("????-??-??.manifest.json")):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("day_in_progress", True):
            continue
        if manifest.get("remote_verified") is not None:
            continue
        pending.append(manifest_path)
    return pending


def verify_pending(data_dir: Path, remote_dir: str, get_remote_checksums: RemoteChecksumGetter) -> tuple[list[str], bool]:
    """
    Verify every pending manifest under data_dir in a single batched call to
    get_remote_checksums(remote_dir, filenames) -> {filename: hexdigest}.

    Returns (human-readable log lines, whether any real mismatch was found).
    A day is only ever written back to disk once a definitive answer (match
    or mismatch) is reached; an unreachable host or a missing remote file
    leaves the manifest exactly as it was, for the next run to retry.
    """
    pending = find_pending_manifests(data_dir)
    if not pending:
        return ["nothing pending remote verification"], False

    manifests = {p: json.loads(p.read_text(encoding="utf-8")) for p in pending}

    filenames = set()
    for manifest in manifests.values():
        filenames.add(manifest["data_file"])
        if manifest.get("polls_file"):
            filenames.add(manifest["polls_file"])

    try:
        remote_sums = get_remote_checksums(remote_dir, sorted(filenames))
    except RemoteUnreachable as exc:
        return [f"remote verification skipped for {len(pending)} day(s) — VPS unreachable ({exc}); will retry next run"], False

    log_lines = []
    mismatch_found = False
    for manifest_path, manifest in manifests.items():
        data_remote = remote_sums.get(manifest["data_file"])
        polls_needed = manifest.get("polls_file")
        polls_remote = remote_sums.get(polls_needed) if polls_needed else None

        if data_remote is None or (polls_needed and polls_remote is None):
            log_lines.append(f"{manifest['date']}: could not verify — file not found on VPS, will retry next run")
            continue

        mismatches = []
        if data_remote != manifest["data_sha256"]:
            mismatches.append(f"data_sha256 local={manifest['data_sha256']} remote={data_remote}")
        if polls_needed and polls_remote != manifest["polls_sha256"]:
            mismatches.append(f"polls_sha256 local={manifest['polls_sha256']} remote={polls_remote}")

        manifest["remote_verified"] = not mismatches
        manifest["remote_verified_at"] = datetime.now(timezone.utc).isoformat()
        if mismatches:
            manifest["remote_verify_note"] = "; ".join(mismatches)
            mismatch_found = True
            log_lines.append(f"{manifest['date']}: CHECKSUM MISMATCH — {manifest['remote_verify_note']}")
        else:
            manifest.pop("remote_verify_note", None)
            log_lines.append(f"{manifest['date']}: remote checksum verified, matches VPS")

        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    return log_lines, mismatch_found


def main():
    parser = argparse.ArgumentParser(
        description="Verify closed-day manifest checksums against the VPS that actually wrote the data, once per file"
    )
    parser.add_argument("data_dir", type=Path, help="Directory containing <YYYY-MM-DD>.manifest.json files")
    parser.add_argument("--vps-host", required=True, help="ssh destination, e.g. root@203.0.113.5")
    parser.add_argument("--vps-key", required=True, help="Path to the ssh private key")
    parser.add_argument("--remote-dir", required=True, help="Remote directory holding this data_dir's day files")
    args = parser.parse_args()

    def getter(remote_dir: str, filenames: list[str]) -> dict[str, str]:
        return ssh_remote_sha256sums(args.vps_host, args.vps_key, remote_dir, filenames)

    log_lines, mismatch_found = verify_pending(args.data_dir, args.remote_dir, getter)
    for line in log_lines:
        print(f"{args.data_dir}: {line}")

    if mismatch_found:
        sys.exit(MISMATCH_EXIT_CODE)


if __name__ == "__main__":
    main()
