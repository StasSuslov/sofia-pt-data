#!/usr/bin/env python3
"""
Scheduled capture of the GTFS Static feed, saving a new snapshot under
data/sofia/static/ only when the feed's actual contents changed.

Why this exists: scripts/segment_speeds.py and scripts/export_web.py both
match a processed day to the latest gtfs_<YYYY-MM-DD>.zip snapshot dated on
or before it (see find_static_snapshots()/pick_snapshot_for_day() in
segment_speeds.py). That matching is only as good as the set of snapshots
on disk, and until now every one of them was downloaded by hand. The agency
republished the feed on 2026-08-31 with no announcement; processing that
day against the previous snapshot rejected 7.013% of records as unmatched
to any known trip, against 0.017% for the correct one, and 32 shape_ids
kept their identifier while changing geometry underneath it. A republish
nobody notices silently degrades every day processed after it.

What "changed" means here: a zip stores a modification time per member, so
an agency that rebuilds its feed export nightly from unchanged source data
still produces a different file on disk every night, even though nothing a
rider or this pipeline cares about is different. Hashing the zip's own
bytes would treat that as a change and archive an 18.7 MB duplicate every
day forever. Instead this hashes the archive's *content*: every member
name paired with the SHA256 of that member's decompressed bytes, sorted by
name, then hashed together (see content_hash() below). This is the same
move commit a748492 made to the segment aggregation key (build_shape_key()
in scripts/segment_speeds.py): identify data by what it says, not by an
incidental encoding detail of how it happens to be packaged.

A second change inside one local day lands as gtfs_<YYYY-MM-DD>T<HHMM>.zip
next to that day's plain snapshot. Until 2026-09-03 it was detected and
thrown away, because the naming scheme held one snapshot per calendar day
and the archive ran once daily. The agency then republished at around 11:00
local on 2026-09-02, renumbering the trips of four routes; the day's own
snapshot had been taken three hours earlier, and 10,166 RT records (1.4% of
the day) matched no trip in it. Keeping only the morning capture makes the
afternoon of such a day unprocessable against anything but a guess.

Usage:
    python scripts/archive_static_feed.py data/sofia/static
    python scripts/archive_static_feed.py data/sofia/static \\
        --url https://gtfs.sofiatraffic.bg/api/v1/static
"""

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

# config.py at the repo root, not scripts/segment_speeds.py: this script has
# to run unattended on the collector host's bare system Python, with no
# venv (see deploy/sofia-static-archive.service), so it only imports from
# config.py, which is deliberately kept dependency-free for exactly this
# kind of reuse, and never from another scripts/*.py file that could grow a
# dependency later without anyone thinking of this script.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from config import BASE_URL, DEFAULT_TIMEZONE  # noqa: E402

DEFAULT_URL = BASE_URL + "/api/v1/static"

# The files segment_speeds.py's load_static()/load_feed_info() and
# export_web.py's load_route_info() actually read. Absence of any one of
# these means a truncated download or an HTML error page served with a 200
# status, not a usable snapshot.
REQUIRED_MEMBERS = ("trips.txt", "shapes.txt", "stops.txt", "routes.txt")

# Same full-match pattern as find_static_snapshots() in segment_speeds.py,
# kept as a short local copy rather than an import for the reason given
# above. A prefix or glob match would also catch the unpacked
# gtfs_2026-08-27/ sibling directory or a stray .DS_Store that already sit
# next to the zips in data/sofia/static/ today.
SNAPSHOT_NAME_RE = re.compile(r"^gtfs_(\d{4}-\d{2}-\d{2})(?:T\d{4})?\.zip$")


# ─── Content hashing ────────────────────────────────────────────────────────

def _member_hashes(zip_path: Path) -> list:
    """
    (member_name, sha256_hex_of_decompressed_bytes) for every real file in
    the zip, sorted by name. Directory entries are skipped: they carry no
    content of their own, only a name and a timestamp, so including them
    would reintroduce exactly the modification-time sensitivity content_hash
    exists to avoid.
    """
    with zipfile.ZipFile(zip_path) as z:
        return sorted(
            (info.filename, hashlib.sha256(z.read(info.filename)).hexdigest())
            for info in z.infolist()
            if not info.is_dir()
        )


def _digest_of(member_hashes: list) -> str:
    joined = "\n".join(f"{name}:{digest}" for name, digest in member_hashes)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


def content_hash(zip_path: Path) -> str:
    """
    SHA256 over the zip's member names and decompressed content, ignoring
    every other detail of how the zip is packaged (member order, per-member
    modification time, compression level). See the module docstring for why
    that distinction is the entire point of this function.
    """
    return _digest_of(_member_hashes(zip_path))


def sha256_of_file(path: Path) -> str:
    """
    Plain sha256 of the zip's own bytes, streamed rather than read whole so
    an 18.7 MB file doesn't sit fully in memory twice over. Kept separate
    from content_hash(): this one changes on every rebuild even when
    content_hash() doesn't, which is exactly the point, so the manifest
    records both rather than only the one that would make a rebuild look
    like a real change.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


# ─── Download and validation ────────────────────────────────────────────────

def download_to_temp(url: str, dest_dir: Path, timeout: int = 60) -> Path:
    """
    Download `url` into a hidden temp file inside dest_dir and return its
    path. The temp name starts with "." and doesn't match SNAPSHOT_NAME_RE,
    so it can never be mistaken for a real snapshot by this script or by
    segment_speeds.py's find_static_snapshots() if a run is interrupted
    before cleanup. Written inside dest_dir, not the system temp directory,
    so the final save is a same-filesystem os.replace() rather than a
    cross-device copy.

    Any failure, network or otherwise, removes the temp file before the
    exception propagates. Nothing calling this function needs to remember
    to clean up after it.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".gtfs_static_download_", suffix=".zip.part", dir=dest_dir)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out:
            req = urllib.request.Request(url, headers={"User-Agent": "sofia-transport-research/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                shutil.copyfileobj(resp, out)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    return tmp_path


def validate_feed_zip(path: Path) -> None:
    """
    Raise ValueError with a human-readable reason when `path` isn't a
    usable GTFS Static snapshot: not a valid zip at all, or missing one of
    REQUIRED_MEMBERS. Returns normally (no return value) when the zip is
    fine. Callers must fail loudly on the exception rather than archive the
    file anyway; see the module docstring.
    """
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile as e:
        raise ValueError(f"not a valid zip file ({e})") from e

    missing = [m for m in REQUIRED_MEMBERS if m not in names]
    if missing:
        raise ValueError(f"missing required file(s): {', '.join(missing)}")


def load_feed_info(zip_path: Path) -> dict:
    """
    Best-effort feed_info.txt row for stamping the manifest. Absent in some
    GTFS feeds and not required by validate_feed_zip(), so a missing file
    or an empty table returns {} rather than raising.
    """
    try:
        with zipfile.ZipFile(zip_path) as z:
            with z.open("feed_info.txt") as f:
                return next(csv.DictReader(io.TextIOWrapper(f, encoding="utf-8")), {})
    except (KeyError, zipfile.BadZipFile, StopIteration):
        return {}


# ─── Snapshot comparison and save ───────────────────────────────────────────

def latest_snapshot(output_dir: Path):
    """
    (date_str, path) for the newest snapshot already in output_dir, or None
    if there isn't one yet (a fresh archive, or a directory that doesn't
    exist).

    Newest is decided on the filename, not on the parsed date, because two
    snapshots can share a date: gtfs_2026-09-02.zip and its intra-day
    sibling gtfs_2026-09-02T1100.zip. "." sorts before "T", so the plain
    name compares as the earlier of the two, which is what it is.
    """
    if not output_dir.exists():
        return None
    snapshots = []
    for p in output_dir.iterdir():
        if not p.is_file():
            continue
        m = SNAPSHOT_NAME_RE.match(p.name)
        if m:
            snapshots.append((m.group(1), p))
    if not snapshots:
        return None
    return max(snapshots, key=lambda pair: pair[1].name)


def build_manifest(target_path: Path, source_url: str, downloaded_at: datetime,
                    zip_sha256: str, content_hash_value: str, size_bytes: int,
                    member_count: int, feed_info: dict) -> dict:
    """
    Mirrors scripts/generate_manifest.py's naming conventions (a
    <name>_sha256 field, byte size, a plain "file" name) so the two kinds of
    manifest read the same way even though they describe different things.
    """
    return {
        "file": target_path.name,
        "source_url": source_url,
        "downloaded_at": downloaded_at.astimezone(timezone.utc).isoformat(),
        "zip_sha256": zip_sha256,
        "content_hash": content_hash_value,
        "size_bytes": size_bytes,
        "member_count": member_count,
        "feed_version": feed_info.get("feed_version"),
        "feed_start_date": feed_info.get("feed_start_date"),
        "feed_end_date": feed_info.get("feed_end_date"),
        "feed_publisher_name": feed_info.get("feed_publisher_name"),
    }


def archive_feed(output_dir: Path, tmp_path: Path, source_url: str, tz: ZoneInfo,
                  now: datetime | None = None) -> int:
    """
    Decide whether the already-downloaded, already-validated candidate at
    tmp_path is new content and, if so, save it as today's snapshot (local
    date in `tz`, matching the timezone the rest of the pipeline rotates on)
    plus its manifest.

    Every branch consumes tmp_path: it's either renamed into place or
    deleted, never left behind. Split out from main() so tests can drive
    this decision logic against a zip they built directly, with no network
    involved (see scripts/test_archive_static_feed.py).

    Returns the process exit code the caller should use.
    """
    now = now or datetime.now(timezone.utc)
    today_str = now.astimezone(tz).date().isoformat()

    member_pairs = _member_hashes(tmp_path)
    new_hash = _digest_of(member_pairs)

    existing = latest_snapshot(output_dir)
    if existing is not None:
        existing_date, existing_path = existing
        if content_hash(existing_path) == new_hash:
            tmp_path.unlink()
            print(f"No change: content hash matches {existing_path.name} "
                  f"({new_hash[:12]}...). Discarding this download.")
            return 0

    target_path = output_dir / f"gtfs_{today_str}.zip"
    if target_path.exists():
        # A second change inside one local day. Overwriting the morning
        # capture would destroy the only evidence of what the agency served
        # earlier today in exchange for evidence of what it serves now, and
        # discarding the new one (what this did until 2026-09-03) loses the
        # afternoon instead. Both go on disk; the intra-day name carries the
        # local capture time, so it sorts after the plain name for the same
        # date and pick_snapshot_for_day() in segment_speeds.py picks it up
        # as the newer snapshot for that day.
        target_path = output_dir / f"gtfs_{today_str}T{now.astimezone(tz):%H%M}.zip"

    zip_sha256 = sha256_of_file(tmp_path)
    size_bytes = tmp_path.stat().st_size
    feed_info = load_feed_info(tmp_path)

    os.replace(tmp_path, target_path)
    # mkstemp() creates at 0600 and os.replace() carries that mode over, which
    # would leave the snapshot the one file under data/ that only its writer
    # can read. Day files land at the umask default.
    os.chmod(target_path, 0o644)

    declared = (feed_info.get("feed_start_date") or "").strip()
    if declared and declared != today_str.replace("-", ""):
        # The name records when this archive observed the feed, deliberately,
        # rather than trusting the publisher's own date (see METHODOLOGY.md).
        # The two agree only while the capture runs after the agency's daily
        # build. When they stop agreeing the build schedule moved, and every
        # snapshot from here on holds the previous day's feed under today's
        # name. Not fatal: the file is still a real snapshot worth keeping,
        # and refusing it would lose data over a scheduling problem.
        print(f"Warning: {target_path.name} declares feed_start_date={declared}. "
              "This capture ran before the agency's daily build; check the "
              "schedule in deploy/sofia-static-archive.timer against the member "
              "timestamps inside the zip.", file=sys.stderr)

    manifest = build_manifest(target_path, source_url, now, zip_sha256, new_hash,
                               size_bytes, len(member_pairs), feed_info)
    manifest_path = target_path.with_suffix(".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    reason = "no prior snapshot on disk" if existing is None else f"changed from {existing_path.name}"
    print(f"Saved new snapshot {target_path.name} ({reason}). "
          f"content_hash={new_hash[:12]}... zip_sha256={zip_sha256[:12]}...")
    return 0


# ─── Entry point ─────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("output_dir", type=Path,
                         help="Directory to store gtfs_<YYYY-MM-DD>.zip snapshots and their "
                              "manifests (e.g. data/sofia/static)")
    parser.add_argument("--url", type=str, default=DEFAULT_URL,
                         help=f"GTFS Static feed URL (default: {DEFAULT_URL})")
    parser.add_argument("--timezone", type=str, default=DEFAULT_TIMEZONE,
                         help=f"Timezone for the snapshot's calendar date (default: {DEFAULT_TIMEZONE}), "
                              "must match collect.py's --timezone so a snapshot files under the same "
                              "local day the RT collector rotates on")
    parser.add_argument("--timeout", type=int, default=60, help="Download timeout in seconds (default: 60)")
    args = parser.parse_args()

    tz = ZoneInfo(args.timezone)

    print(f"Downloading {args.url} ...")
    try:
        tmp_path = download_to_temp(args.url, args.output_dir, timeout=args.timeout)
    except (urllib.error.URLError, OSError) as e:
        print(f"Download failed: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        validate_feed_zip(tmp_path)
    except ValueError as e:
        tmp_path.unlink(missing_ok=True)
        print(f"Rejected download: {e}", file=sys.stderr)
        sys.exit(1)

    sys.exit(archive_feed(args.output_dir, tmp_path, args.url, tz))


if __name__ == "__main__":
    main()
