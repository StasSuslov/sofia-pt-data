#!/usr/bin/env python3
"""
Package a closed date range of the collected archive into files ready to
upload as a Zenodo dataset record (CLAUDE.md section 3, component F).

Produces, in <out-dir>:
  - <prefix>-rt_<start>_<end>.zip          — one <date>.jsonl, <date>.polls.jsonl
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
    python3 scripts/package_dataset.py data/sofia data/sofia/static \\
        2026-08-27 2026-09-01 /tmp/out
"""

import argparse
import hashlib
import json
import re
import shutil
import sys
import zipfile
from datetime import date, timedelta
from pathlib import Path

# config.py at the repo root, not another scripts/*.py file: this stays a
# standalone tool with only the dependency-free helpers imported, the same
# reasoning archive_static_feed.py gives for not importing generate_manifest.py
# or vice versa. sha256_of() below is a deliberate small local duplicate of
# generate_manifest.py's function of the same name and purpose, not an import
# of it — same precedent as SNAPSHOT_NAME_RE being copied rather than shared
# between archive_static_feed.py and segment_speeds.py.
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
from config import open_maybe_gzip, resolve_day_file  # noqa: E402

# Kept in step with generate_manifest.py's constant of the same name by hand
# rather than imported, for the reason given above. A bump there makes every
# day here fail loudly until the manifests are regenerated, which is the safe
# direction to fail in.
MANIFEST_SCHEMA_VERSION = 2

# Uploaded alongside the two zips, per CLAUDE.md section 8: a dataset record
# that points at its methodology only by URL loses the link the day the repo
# moves. Copied from the repo root, checksummed with everything else.
DOC_FILES = ("METHODOLOGY.md", "README.md")

# Same pattern generate_manifest.py/archive_static_feed.py use for static
# snapshot filenames.
STATIC_SNAPSHOT_RE = re.compile(r"^gtfs_(\d{4}-\d{2}-\d{2})\.zip$")

# A stray file in data_dir whose name doesn't start with a date at all (e.g.
# .DS_Store, a *.log left by hand) gets this reason in the skip report.
NOT_ARCHIVE_FILE = "not a recognized day/manifest/heartbeat file"


def sha256_of(path: Path) -> tuple[str, int]:
    """
    (hexdigest, byte_count) of `path`'s UNCOMPRESSED content, streamed in
    1 MiB chunks so a 150 MB day file is never held whole in memory. Works
    unchanged for a plain file (static zips, already-flat day files) since
    open_maybe_gzip() only decompresses when the suffix is literally ".gz".
    """
    h = hashlib.sha256()
    size = 0
    with open_maybe_gzip(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
            size += len(chunk)
    return h.hexdigest(), size


def load_manifest(path: Path) -> dict:
    """The manifest at `path`, or a ValueError naming it: a missing or
    unparseable manifest is grounds to abort the whole run, never to guess
    or skip — "absence of proof is not proof"."""
    if not path.exists():
        raise ValueError(f"missing manifest: {path}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise ValueError(f"manifest is not valid JSON: {path} ({e})") from e


def date_range(start_date: str, end_date: str) -> list[str]:
    try:
        start = date.fromisoformat(start_date)
        end = date.fromisoformat(end_date)
    except ValueError as e:
        raise ValueError(f"invalid date: {e}") from e
    if end < start:
        raise ValueError(f"end date {end_date} is before start date {start_date}")
    days = []
    d = start
    while d <= end:
        days.append(d.isoformat())
        d += timedelta(days=1)
    return days


def verify_day(data_dir: Path, date_str: str) -> dict:
    """
    Verify one day's data file (and heartbeat file, if its manifest says
    there is one) hashes to exactly what <date>.manifest.json recorded.
    Raises ValueError naming the offending file on any mismatch or absence.
    Returns the resolved paths and parsed manifest for the packing step, so
    packing never has to re-derive anything verification already settled.
    """
    manifest_path = data_dir / f"{date_str}.manifest.json"
    manifest = load_manifest(manifest_path)

    # Before trusting any field below, including day_in_progress: an older
    # manifest may simply not have the field, and an absent day_in_progress
    # reads as "closed, go ahead". generate_manifest.py treats a version
    # mismatch as grounds for regeneration; here it is grounds for refusing.
    if manifest.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        raise ValueError(
            f"{manifest_path.name} has schema_version "
            f"{manifest.get('schema_version')!r}, expected {MANIFEST_SCHEMA_VERSION} "
            f"— regenerate it with scripts/generate_manifest.py before packaging"
        )

    # A day still being written is a moving target: hashing it now proves
    # nothing about what a Zenodo download will contain later. A "closed
    # date range" excludes it by definition.
    if manifest.get("day_in_progress"):
        raise ValueError(
            f"{date_str} is still in progress (day_in_progress=true in "
            f"{manifest_path.name}) — refusing to package an open day"
        )

    data_path = resolve_day_file(data_dir, date_str, ".jsonl")
    if not data_path.exists():
        raise ValueError(f"missing day file for {date_str}: {data_path} (or {data_path.name}.gz)")

    data_sha256, _ = sha256_of(data_path)
    if data_sha256 != manifest.get("data_sha256"):
        raise ValueError(
            f"hash mismatch for {data_path.name}: computed {data_sha256}, "
            f"{manifest_path.name} says {manifest.get('data_sha256')}"
        )

    polls_path = None
    if manifest.get("polls_file"):
        polls_path = resolve_day_file(data_dir, date_str, ".polls.jsonl")
        if not polls_path.exists():
            raise ValueError(
                f"missing heartbeat file for {date_str}: {polls_path} (or "
                f"{polls_path.name}.gz), but {manifest_path.name} declares one"
            )
        polls_sha256, _ = sha256_of(polls_path)
        if polls_sha256 != manifest.get("polls_sha256"):
            raise ValueError(
                f"hash mismatch for {polls_path.name}: computed {polls_sha256}, "
                f"{manifest_path.name} says {manifest.get('polls_sha256')}"
            )

    return {
        "date": date_str,
        "manifest_path": manifest_path,
        "manifest": manifest,
        "data_path": data_path,
        "polls_path": polls_path,
    }


def find_static_snapshots(static_dir: Path, start_date: str, end_date: str) -> list[dict]:
    """
    Verify every gtfs_<date>.zip in static_dir whose date falls in
    [start_date, end_date] against its own manifest's zip_sha256. Missing
    static snapshots for a day are normal (a new snapshot is only saved when
    the feed's content actually changes, see archive_static_feed.py) and not
    an error; a missing MANIFEST for a snapshot that does exist is.
    """
    if not static_dir.exists():
        raise ValueError(f"static directory not found: {static_dir}")

    snapshots = []
    for p in sorted(static_dir.iterdir()):
        if not p.is_file():
            continue
        m = STATIC_SNAPSHOT_RE.match(p.name)
        if not m:
            continue
        snap_date = m.group(1)
        if not (start_date <= snap_date <= end_date):
            continue

        manifest_path = static_dir / f"gtfs_{snap_date}.manifest.json"
        manifest = load_manifest(manifest_path)

        zip_sha256, _ = sha256_of(p)
        if zip_sha256 != manifest.get("zip_sha256"):
            raise ValueError(
                f"hash mismatch for {p.name}: computed {zip_sha256}, "
                f"{manifest_path.name} says {manifest.get('zip_sha256')}"
            )

        snapshots.append({
            "date": snap_date,
            "zip_path": p,
            "manifest_path": manifest_path,
            "manifest": manifest,
        })
    return snapshots


def find_skipped_files(data_dir: Path, verified_days: list[dict], start_date: str, end_date: str) -> list[tuple[str, str]]:
    """
    Top-level files in data_dir that aren't one of the day/heartbeat/manifest
    files just packed — e.g. a stray 2026-08-27.log or .DS_Store, or a day
    file whose date falls outside the requested range. Subdirectories
    (static/, processed/, web/) are a different kind of content handled
    elsewhere (static_dir) or not part of this dataset record at all, so
    they're passed over silently rather than reported as strays.
    """
    consumed = set()
    for day in verified_days:
        consumed.add(day["data_path"].name)
        consumed.add(day["manifest_path"].name)
        if day["polls_path"] is not None:
            consumed.add(day["polls_path"].name)

    date_prefix_re = re.compile(r"^(\d{4}-\d{2}-\d{2})\.")
    skipped = []
    for p in sorted(data_dir.iterdir()):
        if not p.is_file() or p.name in consumed:
            continue
        m = date_prefix_re.match(p.name)
        if m and not (start_date <= m.group(1) <= end_date):
            skipped.append((p.name, f"date {m.group(1)} outside requested range {start_date}..{end_date}"))
        else:
            skipped.append((p.name, NOT_ARCHIVE_FILE))
    return skipped


def stream_into_zip(zf: zipfile.ZipFile, src_path: Path, arcname: str) -> None:
    """
    Write src_path into zf under arcname, decompressing transparently if
    it's stored gzipped on disk. Streamed in 1 MiB chunks on both sides —
    some day files are 145 MB and the whole range is roughly 500 MB
    uncompressed, too much to hold whole in memory even once.
    """
    with open_maybe_gzip(src_path, "rb") as src, zf.open(arcname, "w") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)


def pack_rt_zip(verified_days: list[dict], out_path: Path) -> None:
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for day in verified_days:
            date_str = day["date"]
            stream_into_zip(zf, day["data_path"], f"{date_str}.jsonl")
            if day["polls_path"] is not None:
                stream_into_zip(zf, day["polls_path"], f"{date_str}.polls.jsonl")
            # A real file already on disk, unlike the day files above:
            # ZipFile.write() streams it internally, no separate handling needed.
            zf.write(day["manifest_path"], arcname=f"{date_str}.manifest.json")


def pack_static_zip(snapshots: list[dict], out_path: Path) -> None:
    with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_STORED) as zf:
        for snap in snapshots:
            zf.write(snap["zip_path"], arcname=snap["zip_path"].name)
            zf.write(snap["manifest_path"], arcname=snap["manifest_path"].name)


def write_sha256sums(out_dir: Path, artifact_paths: list[Path]) -> Path:
    """sha256sum(1) text-mode format ("<hex>  <name>\\n"), filenames bare
    (no directory component) so `sha256sum -c SHA256SUMS.txt` works
    unmodified from inside out_dir, matching the task's own acceptance check."""
    lines = []
    for p in artifact_paths:
        digest, _ = sha256_of(p)
        lines.append(f"{digest}  {p.name}\n")
    sums_path = out_dir / "SHA256SUMS.txt"
    sums_path.write_text("".join(lines), encoding="utf-8")
    return sums_path


def run(data_dir: Path, static_dir: Path, start_date: str, end_date: str, out_dir: Path) -> dict:
    """
    Verify every input for [start_date, end_date], then pack it. Verification
    is complete (every day, every in-range static snapshot) before a single
    byte is written to either zip — see the module docstring's abort rules.
    Raises ValueError on any verification failure; nothing is packed in that
    case. Returns the report data main() prints.
    """
    days = date_range(start_date, end_date)
    verified_days = [verify_day(data_dir, d) for d in days]
    snapshots = find_static_snapshots(static_dir, start_date, end_date)
    skipped = find_skipped_files(data_dir, verified_days, start_date, end_date)

    docs = [REPO_ROOT / name for name in DOC_FILES]
    missing_docs = [d.name for d in docs if not d.exists()]
    if missing_docs:
        raise ValueError(f"missing file(s) the record must ship with: {', '.join(missing_docs)}")

    prefix = Path(data_dir).name  # e.g. data/sofia -> "sofia" — CLAUDE.md D2, no city literal here
    out_dir.mkdir(parents=True, exist_ok=True)
    rt_zip_path = out_dir / f"{prefix}-rt_{start_date}_{end_date}.zip"
    static_zip_path = out_dir / f"{prefix}-gtfs-static_{start_date}_{end_date}.zip"

    pack_rt_zip(verified_days, rt_zip_path)
    pack_static_zip(snapshots, static_zip_path)
    doc_paths = [Path(shutil.copy2(d, out_dir / d.name)) for d in docs]
    sums_path = write_sha256sums(out_dir, [rt_zip_path, static_zip_path, *doc_paths])

    return {
        "verified_days": verified_days,
        "snapshots": snapshots,
        "skipped": skipped,
        "rt_zip_path": rt_zip_path,
        "static_zip_path": static_zip_path,
        "doc_paths": doc_paths,
        "sums_path": sums_path,
    }


def print_report(result: dict, start_date: str, end_date: str) -> None:
    print(f"RT day files packed ({start_date}..{end_date}):")
    for day in result["verified_days"]:
        m = day["manifest"]
        print(
            f"  {day['date']}: {m.get('total_vehicle_records', 0):,} records | "
            f"coverage {m.get('coverage_pct')}% | {m.get('gap_count')} gap(s)"
        )

    print("\nStatic feed snapshots packed:")
    if result["snapshots"]:
        for snap in result["snapshots"]:
            print(f"  {snap['zip_path'].name} ({snap['manifest'].get('size_bytes', 0):,} bytes)")
    else:
        print("  none found in range")

    if result["skipped"]:
        print("\nSkipped (not part of the archive):")
        for name, reason in result["skipped"]:
            print(f"  {name}: {reason}")

    print("\nArtifacts:")
    for p in (result["rt_zip_path"], result["static_zip_path"], *result["doc_paths"], result["sums_path"]):
        print(f"  {p.name}: {p.stat().st_size:,} bytes")


def main():
    parser = argparse.ArgumentParser(
        description="Verify a closed range of the archive against its manifests, then package it "
                     "into two zips plus SHA256SUMS.txt ready for a Zenodo dataset record"
    )
    parser.add_argument("data_dir", type=Path, help="Directory of <date>.jsonl/.polls.jsonl/.manifest.json day files, e.g. data/sofia")
    parser.add_argument("static_dir", type=Path, help="Directory of gtfs_<date>.zip static snapshots and their manifests, e.g. data/sofia/static")
    parser.add_argument("start_date", help="First day to include, inclusive (YYYY-MM-DD)")
    parser.add_argument("end_date", help="Last day to include, inclusive (YYYY-MM-DD)")
    parser.add_argument("out_dir", type=Path, help="Directory to write the zips and SHA256SUMS.txt into (created if missing)")
    args = parser.parse_args()

    try:
        result = run(args.data_dir, args.static_dir, args.start_date, args.end_date, args.out_dir)
    except ValueError as e:
        print(f"Verification failed, nothing packed: {e}", file=sys.stderr)
        sys.exit(1)

    print_report(result, args.start_date, args.end_date)


if __name__ == "__main__":
    main()
