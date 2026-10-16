#!/usr/bin/env python3
r"""Package a closed date range of the archive into Zenodo-ready files.

Package a closed date range of the collected archive into files ready to
upload as a Zenodo dataset record (CLAUDE.md section 3, component F).

Produces, in <out-dir>:
  - <prefix>-rt_<start>_<end>.zip          — one <date>.jsonl,
    <date>.polls.jsonl
    (if that day has one) and <date>.manifest.json per day in the range,
    every day file stored DECOMPRESSED regardless of how it sits on disk
    locally (see deploy/sofia-compress.service — closed days get gzipped on
    the VPS). A reader who extracts the zip must be able to hash the
    extracted <date>.jsonl and get exactly the manifest's data_sha256; a mix
    of flat and .gz members inside the same zip would break that.
  - <prefix>-gtfs-static_<start>_<end>.zip — every gtfs_<YYYY-MM-DD>.zip
    static snapshot (plus its manifest) whose date falls in the range.
    Stored ZIP_STORED, not ZIP_DEFLATED: these are already zip files,
    recompressing compressed bytes wastes CPU for no size gain.
  - SHA256SUMS.txt — sha256sum-format checksums of the two zips above, so
    `sha256sum -c SHA256SUMS.txt` verifies the download unmodified.

<prefix> is the basename of <data-dir> (e.g. data/sofia -> "sofia"), never a
literal city name hardcoded here (CLAUDE.md D2).

Every input is verified against its own manifest BEFORE anything is packed,
and the whole run aborts on the first problem found: a hash mismatch, a
missing manifest, or a missing day file. Recomputed hashes are always over
the UNCOMPRESSED bytes (config.open_maybe_gzip), because a manifest
describes the uncompressed day file even when the local copy is gzipped —
see CLAUDE.md section 8, the single most load-bearing detail in this script.
Absence of a day's <date>.polls.jsonl is only an error when that day's own
manifest says one should exist (polls_file != null) — some early days (e.g.
2026-08-27, collection started before heartbeat logging existed) legitimately
have no heartbeat log at all, and the manifest already records that.

Usage:
    python3 scripts/package_dataset.py data/sofia data/sofia/static \
        2026-08-27 2026-09-01 /tmp/out
"""

import argparse
import sys
from pathlib import Path

# config.py at the repo root, not another scripts/*.py file: this stays a
# standalone tool with only the dependency-free helpers imported, the same
# reasoning archive_static_feed.py gives for not importing generate_manifest.py
# or vice versa. sha256_of() in datasetzip.files is a deliberate small local
# duplicate of generate_manifest.py's function of the same name and purpose,
# not an import of it — same precedent as SNAPSHOT_NAME_RE being copied rather
# than shared between archive_static_feed.py and segment_speeds.py.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: WPS221  fixed bootstrap line, shared by every script
from datasetzip.days import date_range, verify_day
from datasetzip.files import sha256_of
from datasetzip.pack import write_sha256sums
from datasetzip.pipeline import run
from datasetzip.report import print_report
from datasetzip.scan import find_skipped_files, find_static_snapshots

__all__ = (  # noqa: WPS410  re-exports are part of this module's interface
    'date_range',
    'find_skipped_files',
    'find_static_snapshots',
    'main',
    'run',
    'sha256_of',
    'verify_day',
    'write_sha256sums',
)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Verify a closed range of the archive against its manifests, '
            'then package it into two zips plus SHA256SUMS.txt ready for a '
            'Zenodo dataset record'
        ),
    )
    parser.add_argument(
        'data_dir',
        type=Path,
        help=(
            'Directory of <date>.jsonl/.polls.jsonl/.manifest.json day '
            'files, e.g. data/sofia'
        ),
    )
    parser.add_argument(
        'static_dir',
        type=Path,
        help=(
            'Directory of gtfs_<date>.zip static snapshots and their '
            'manifests, e.g. data/sofia/static'
        ),
    )
    parser.add_argument(
        'start_date',
        help='First day to include, inclusive (YYYY-MM-DD)',
    )
    parser.add_argument(
        'end_date',
        help='Last day to include, inclusive (YYYY-MM-DD)',
    )
    parser.add_argument(
        'out_dir',
        type=Path,
        help=(
            'Directory to write the zips and SHA256SUMS.txt into '
            '(created if missing)'
        ),
    )
    return parser


def main() -> None:
    """Parse the command line, package the range and print the report."""
    args = _build_parser().parse_args()

    try:
        packed = run(
            args.data_dir,
            args.static_dir,
            args.start_date,
            args.end_date,
            args.out_dir,
        )
    except ValueError as exc:
        sys.stderr.write(f'Verification failed, nothing packed: {exc}\n')
        sys.exit(1)

    print_report(packed, args.start_date, args.end_date)


if __name__ == '__main__':
    main()
