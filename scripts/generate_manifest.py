#!/usr/bin/env python3
"""Generate a per-day integrity/coverage manifest for collected GTFS-RT data.

For each <YYYY-MM-DD>.jsonl (or, once deploy/sofia-compress.service has run on
the VPS, <YYYY-MM-DD>.jsonl.gz — both are read transparently, see
config.open_maybe_gzip) in a data directory, writes a <YYYY-MM-DD>.manifest.json
with:
  - SHA256 checksums of the data file and its heartbeat log (tamper-evidence —
    anyone re-checksumming a published file can confirm it matches).
  - Poll-level completeness derived from the <date>.polls.jsonl heartbeat log
    written by collect.py: successful / empty / fetch-error polls, and any
    gaps in the heartbeat itself (a real gap means the collector was down —
    an empty poll or a transient fetch error still leaves a heartbeat entry).
  - For days collected before the heartbeat log existed, falls back to
    inferring coverage from the data file's own snapshot_ts values (can only
    see polls that found >=1 vehicle — flagged via "heartbeat_available").

Usage:
    python scripts/generate_manifest.py data/sofia
    python scripts/generate_manifest.py data/sofia --gap-threshold 3
"""

import sys
from pathlib import Path

# The city profile is the single source of truth for the cadence collect.py
# actually runs at and the timezone it rotates days on; coverage computed
# against any other number measures the wrong thing. Importing collect.py
# itself would pull in requests and the protobuf bindings, which this file
# never needs.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: WPS221  fixed bootstrap line, shared by every script
from manifests.build import build_manifest
from manifests.cli import run
from manifests.coverage import DayWindow, analyze_gaps, day_bounds
from manifests.hashing import load_jsonl, sha256_of
from manifests.staleness import (
    MANIFEST_SCHEMA_VERSION,
    REMOTE_VERIFICATION_FIELDS,
    carry_remote_verification,
    manifest_is_current,
    read_manifest,
    should_skip,
)

from config import find_day_files

# Names tests and other scripts import from here; the implementation lives
# in scripts/manifests/.
__all__ = (  # noqa: WPS410  re-exports are part of this module's interface
    'MANIFEST_SCHEMA_VERSION',
    'REMOTE_VERIFICATION_FIELDS',
    'DayWindow',
    'analyze_gaps',
    'build_manifest',
    'carry_remote_verification',
    'day_bounds',
    'find_day_files',
    'load_jsonl',
    'main',
    'manifest_is_current',
    'read_manifest',
    'sha256_of',
    'should_skip',
)


def main() -> None:
    """Run the manifest generator (see the module docstring for usage)."""
    run()


if __name__ == '__main__':
    main()
