#!/usr/bin/env python3
"""Close the provenance gap generate_manifest.py leaves open.

Its data_sha256 /
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
    re-checked here. One remote read per file, for the life of the file. A
    false is cleared upstream: generate_manifest.py regenerates such a
    manifest and does not carry the false over, so the day is pending again
    (finding 19).
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
import sys
from pathlib import Path

from remotecheck.ssh import RemoteUnreachable, ssh_remote_sha256sums
from remotecheck.verify import (
    RemoteChecksumGetter,
    find_pending_manifests,
    verify_pending,
)

# Keep in sync with CHECKSUM_MISMATCH_EXIT_CODE in scripts/fetch_data.sh and
# scripts/scheduled_fetch.sh — same convention as POST_PULL_FAILED_EXIT_CODE
# already uses to let a shell caller tell exit codes apart without parsing
# stdout.
MISMATCH_EXIT_CODE = 43

# Names tests and the fetch pipeline import from here; the implementation
# lives in scripts/remotecheck/.
__all__ = (  # noqa: WPS410  re-exports are part of this module's interface
    'MISMATCH_EXIT_CODE',
    'RemoteChecksumGetter',
    'RemoteUnreachable',
    'find_pending_manifests',
    'main',
    'ssh_remote_sha256sums',
    'verify_pending',
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Verify closed-day manifest checksums against the VPS that '
            'actually wrote the data, once per file'
        ),
    )
    parser.add_argument(
        'data_dir',
        type=Path,
        help='Directory containing <YYYY-MM-DD>.manifest.json files',
    )
    parser.add_argument(
        '--vps-host',
        required=True,
        help='ssh destination, e.g. root@203.0.113.5',
    )
    parser.add_argument(
        '--vps-key',
        required=True,
        help='Path to the ssh private key',
    )
    parser.add_argument(
        '--remote-dir',
        required=True,
        help="Remote directory holding this data_dir's day files",
    )
    return parser


def main() -> None:
    """Verify pending manifests against the VPS and set the exit code."""
    args = _build_parser().parse_args()

    log_lines, mismatch_found = verify_pending(
        args.data_dir,
        args.remote_dir,
        lambda remote_dir, filenames: ssh_remote_sha256sums(
            args.vps_host,
            args.vps_key,
            remote_dir,
            filenames,
        ),
    )
    for line in log_lines:
        sys.stdout.write(f'{args.data_dir}: {line}\n')

    if mismatch_found:
        sys.exit(MISMATCH_EXIT_CODE)


if __name__ == '__main__':
    main()
