#!/usr/bin/env python3
"""
Publish the web export as a static site (CLAUDE.md D11): build the frontend,
assemble a self-contained tree of built assets plus the data the site is
allowed to fetch, and force-push it as a single commit to a separate
repository served by GitHub Pages.

Why a separate repository with one commit. Git keeps history forever, and
the export is ~2 MB of regenerated JSON per collected day. Committing it
into the data repository would turn a clone into an archive download within
a year, and deleting the old files later would not give the weight back —
the objects stay reachable from history. So the staging tree is built fresh
in a temp directory every run and force-pushed over whatever was there: the
published repository holds exactly one commit describing exactly one state,
and its size tracks the size of the current export rather than the sum of
every export ever made.

Why a rolling window rather than everything. The typical-weekday medians are
the published result and ship whole, every schedule period, because dropping
a period would silently change what a reader can compare. The per-day
bundles are raw supporting material (D5) and only the most recent --days of
them are published; everything older is addressed by DOI in the Zenodo
dataset record, which is the citable copy anyway. That keeps the site in the
60-80 MB range indefinitely instead of growing without bound.

Why the index is regenerated rather than copied. web/index.json in the
source export lists every day bundle on disk, and the day switcher
(Component D feature 2) enumerates days from it — a static site cannot list
a directory. Copy that index alongside a windowed subset of the days and
every day that fell outside the window becomes a 404 the user can click.
Rebuilding it by scanning the staging tree makes the published index
describe exactly what was published, by construction rather than by care.
The rebuild reuses export_web.write_root_index() rather than reimplementing
the scan, so the index format has one definition.

Why the built dist/ is copied without its data/ directory. frontend/public/
holds a `data` symlink to the export for the dev server, and Vite
dereferences it at build time — so frontend/dist/data is a full, already
stale copy of whatever the export looked like when the build ran, including
days outside the window. Copied as-is it would defeat the window and put
those extra days back in front of write_root_index(). The staging copy skips
it, and the data the site actually serves is assembled here from <web-dir>.

Nothing in this script writes to <web-dir> or anywhere else under data/.

Usage:
    python3 scripts/publish_web.py data/sofia/web frontend --dry-run
    python3 scripts/publish_web.py data/sofia/web frontend
"""

import argparse
import os
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

# export_web.py lives beside this file; the index format is defined there and
# is imported rather than restated, for the reason in the docstring above.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_web import write_root_index  # noqa: E402

DEFAULT_DAYS = 30
DEFAULT_REPO = "git@github.com:StasSuslov/sofia-pt-web.git"

# A GitHub Pages *project* site serves from /<repo>/, not from /. Both the
# built asset URLs (Vite's `base`) and the data fetches
# (VITE_DATA_BASE_URL, see frontend/src/data.ts) have to agree with that or
# every request resolves one level too high.
DEFAULT_BASE_PATH = "/sofia-pt-web/"

# GitHub Pages runs Jekyll over the published tree unless this file exists,
# and Jekyll drops paths beginning with an underscore. Nothing here starts
# with one today, but a future Vite asset name could, and the failure mode is
# a silent 404 rather than a build error.
NOJEKYLL = ".nojekyll"

def readme_text(attribution: dict) -> str:
    """The published repository's README, named after the city whose data it
    actually holds and crediting that city's feed under that feed's licence.

    Written from the export's own manifests rather than from a constant here:
    a constant would keep saying Sofia, and CC BY 4.0, over whatever tree was
    handed to it."""
    return f"""\
# {attribution.get('city', 'Public')} public transport — map

Generated output. This site is built and force-pushed by
`scripts/publish_web.py` in the project repository; it holds a single commit
and is rewritten on every publish, so nothing here is edited by hand.

It publishes a rolling window of the most recent observed days plus the
typical-weekday medians for every schedule period in the archive. The
complete archive is published as a dataset record with a DOI.

- Code and methodology: https://github.com/StasSuslov/sofia-pt-data
- Code DOI (concept): 10.5281/zenodo.22256653
- Dataset DOI (concept): 10.5281/zenodo.22285128

Transit data: {attribution['source_name']} ({attribution.get('feed_description', 'GTFS/GTFS-RT')}),
operated by {attribution.get('operator', 'the local operator')}, {attribution['licence']}.
{attribution.get('source_url', '')}

Licences: code MIT, data {attribution['licence']}.
"""


def collect_attribution(staging_data: Path) -> dict:
    """The one attribution every bundle in the tree agrees on.

    Refuses a tree whose bundles disagree, and a bundle that names no source
    at all. Both refusals guard the same mistake: a site that credits one
    city's operator, or publishes one city's licence, over another city's
    data. export_web.py writes attribution: null when it could not resolve a
    city, so that export reaches exactly this check instead of the web."""
    found = {}
    bundles = sorted(staging_data.glob("*/manifest.json")) + \
        sorted(staging_data.glob("typical_weekday/*/manifest.json"))
    for path in bundles:
        if path.parent.name == "typical_weekday":
            continue  # the period index, not a bundle
        manifest = json.loads(path.read_text(encoding="utf-8"))
        attribution = manifest.get("attribution")
        if not attribution:
            raise ValueError(
                f"{path.relative_to(staging_data)} carries no attribution — refusing to "
                f"publish data whose source and licence it cannot state. Re-run "
                f"export_web.py with --city, or with a data directory named after the city.")
        found[json.dumps(attribution, sort_keys=True)] = attribution
    if not found:
        raise ValueError(f"no bundle manifests found under {staging_data}")
    if len(found) > 1:
        raise ValueError(
            f"the bundles in this export carry {len(found)} different attributions — one "
            f"site publishes one city's data; export each city to its own tree")
    return next(iter(found.values()))


def tree_size_bytes(root: Path) -> int:
    return sum(p.stat().st_size for p in root.rglob("*") if p.is_file())


def select_day_bundles(web_dir: Path, days: int) -> tuple[list[Path], list[Path]]:
    """(published, skipped) day-bundle directories, newest `days` published.

    Same admission rule as write_root_index(): a `????-??-??` directory
    counts as a bundle only once it holds a manifest.json. A directory
    half-written by an interrupted export has no manifest yet, and shipping
    it would put a day in the switcher whose timeslots 404.

    Sorted by name, which for ISO dates is chronological — no date parsing
    needed, and a directory that merely looks like a date sorts harmlessly.
    """
    bundles = [d for d in sorted(web_dir.glob("????-??-??"))
               if d.is_dir() and (d / "manifest.json").exists()]
    if days <= 0:
        raise ValueError(f"--days must be positive, got {days}")
    return bundles[-days:], bundles[:-days]


def build_staging_tree(web_dir: Path, dist_dir: Path, staging_dir: Path, days: int) -> dict:
    """Assemble the complete publishable tree in `staging_dir`, and report
    what went into it.

    Deliberately does no npm and no git: everything that decides *what gets
    published* lives here, so it can be run and asserted against without a
    toolchain or a network. The caller does the building and the pushing.

    `staging_dir` must be empty or absent, and that is enforced rather than
    assumed. The tree is built from scratch every run (see the module
    docstring) rather than updated in place, so a period or a day that
    disappeared upstream also disappears here instead of lingering.
    """
    typical_src = web_dir / "typical_weekday"
    if not typical_src.is_dir():
        raise ValueError(f"no typical_weekday/ in {web_dir} — run export_web.py first")

    # Refusing a non-empty directory here is what keeps three separate
    # failures out of the published site, all of them ending in a force-push:
    #
    #   - a directory that already holds a repository. `git init` over an
    #     existing .git is a silent reinit, so push() would commit whatever
    #     lives there on top of that history and force-push it to --repo.
    #   - a directory holding an earlier run's tree. Nothing below deletes,
    #     only copies, and write_root_index() scans the result — so a day
    #     that has since rolled out of the --days window would reappear in
    #     the very index that exists to describe only what was copied.
    #   - the second run into the same --staging, which otherwise dies on
    #     an uncaught FileExistsError deep in copytree.
    #
    # The caller's own temp directory is fresh, so this never fires by
    # accident; it fires when a human points --staging somewhere real.
    if staging_dir.exists() and any(staging_dir.iterdir()):
        raise ValueError(
            f"{staging_dir} is not empty — refusing to build a publishable tree in it. "
            f"Publishing force-pushes this directory as a fresh repository; point --staging "
            f"at a new or empty path.")
    staging_dir.mkdir(parents=True, exist_ok=True)

    # dist/data is Vite's dereferenced copy of the public/data dev symlink:
    # stale, unwindowed, and about to be replaced by the real thing below.
    # Matched against dist_dir itself so a `data` directory nested somewhere
    # inside the built assets would still be copied.
    shutil.copytree(
        dist_dir, staging_dir, dirs_exist_ok=True,
        ignore=lambda src, names: {"data"} if Path(src) == dist_dir else set(),
    )

    staging_data = staging_dir / "data"
    shutil.copytree(typical_src, staging_data / "typical_weekday")
    period_dirs = sorted(p.name for p in (staging_data / "typical_weekday").iterdir() if p.is_dir())

    published, skipped = select_day_bundles(web_dir, days)
    for d in published:
        shutil.copytree(d, staging_data / d.name)

    # After the copying, never before: the index has to describe the tree
    # that exists, not the one that was intended.
    index = write_root_index(staging_data)

    attribution = collect_attribution(staging_data)

    (staging_dir / NOJEKYLL).write_text("", encoding="utf-8")
    (staging_dir / "README.md").write_text(readme_text(attribution), encoding="utf-8")

    return {
        "index": index,
        "attribution": attribution,
        "days_published": [d.name for d in published],
        "days_skipped": [d.name for d in skipped],
        "period_dirs": period_dirs,
        "current_period": (index.get("typical_weekday") or {}).get("current_period"),
        "size_bytes": tree_size_bytes(staging_dir),
    }


def build_frontend(frontend_dir: Path, base_path: str) -> Path:
    """`npm run build` with the published URL prefix in the environment,
    returning the dist directory it produced.

    The two variables are one fact spelled twice: Vite rewrites asset URLs
    with VITE_BASE_PATH (see frontend/vite.config.ts) and the fetch layer
    prefixes data URLs with VITE_DATA_BASE_URL (frontend/src/data.ts). They
    are derived from the same argument here so they cannot drift apart.
    """
    env = {**os.environ,
           "VITE_BASE_PATH": base_path,
           "VITE_DATA_BASE_URL": f"{base_path}data"}
    subprocess.run(["npm", "run", "build"], cwd=frontend_dir, env=env, check=True)
    dist_dir = frontend_dir / "dist"
    if not dist_dir.is_dir():
        raise ValueError(f"npm run build reported success but produced no {dist_dir}")
    return dist_dir


def push(staging_dir: Path, repo: str) -> None:
    """Force-push the staging tree as the single commit on `main`.

    --force, and a repository initialised from scratch each time, is the
    whole retention story: the remote ends up with one commit whose tree is
    this staging tree, and the previous publish's blobs stop being
    reachable. Any manual commit made in the published repository is
    discarded by the next publish — it is generated output, and the README
    written into it says so.
    """
    message = f"Publish web export {datetime.now(timezone.utc).isoformat(timespec='seconds')}"
    for cmd in (["init", "-q"],
                ["add", "-A"],
                ["commit", "-q", "-m", message],
                ["branch", "-M", "main"],
                ["remote", "add", "origin", repo],
                ["push", "--force", "origin", "main"]):
        subprocess.run(["git", "-C", str(staging_dir), *cmd], check=True)


def print_summary(report: dict, staging_dir: Path, base_path: str, dry_run: bool,
                  staging_kept: bool) -> None:
    days = report["days_published"]
    skipped = report["days_skipped"]
    print(f"\n{'DRY RUN — nothing pushed' if dry_run else 'Published'}")
    if staging_kept:
        print(f"  staging:        {staging_dir}")
    print(f"  base path:      {base_path} (data at {base_path}data)")
    if days:
        print(f"  days published: {len(days)} ({days[0]} .. {days[-1]})")
    else:
        print("  days published: 0")
    print(f"  days skipped:   {len(skipped)}" + (f" (oldest {skipped[0]})" if skipped else ""))
    print(f"  periods:        {len(report['period_dirs'])}, current {report['current_period']}")
    print(f"  total size:     {report['size_bytes'] / 1e6:.1f} MB")


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("web_dir", type=Path, help="Web export to publish from, e.g. data/sofia/web")
    parser.add_argument("frontend_dir", type=Path, help="Vite project to build, e.g. frontend")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS,
                        help=f"How many of the newest day bundles to publish (default: {DEFAULT_DAYS}); "
                             f"older days stay addressable through the Zenodo dataset record")
    parser.add_argument("--staging", type=Path, default=None,
                        help="Directory to assemble the tree in (default: a fresh temp directory). "
                             "Give one to inspect what would be published.")
    parser.add_argument("--repo", default=DEFAULT_REPO, help=f"Push target (default: {DEFAULT_REPO})")
    parser.add_argument("--base-path", default=DEFAULT_BASE_PATH,
                        help=f"URL prefix the site is served from, with a trailing slash "
                             f"(default: {DEFAULT_BASE_PATH})")
    parser.add_argument("--dry-run", action="store_true",
                        help="Build the tree and report it, then stop without pushing")
    args = parser.parse_args()

    base_path = args.base_path if args.base_path.endswith("/") else args.base_path + "/"
    staging_dir = args.staging or Path(tempfile.mkdtemp(prefix="publish-web-"))
    ephemeral = args.staging is None

    try:
        dist_dir = build_frontend(args.frontend_dir, base_path)
        report = build_staging_tree(args.web_dir, dist_dir, staging_dir, args.days)
    except (ValueError, subprocess.CalledProcessError) as e:
        print(f"Nothing published: {e}", file=sys.stderr)
        sys.exit(1)

    if not args.dry_run:
        try:
            push(staging_dir, args.repo)
        except subprocess.CalledProcessError as e:
            print(f"Tree built in {staging_dir} but the push failed: {e}", file=sys.stderr)
            sys.exit(1)

    print_summary(report, staging_dir, base_path, args.dry_run, staging_kept=not ephemeral)

    # A temp directory this script made has done its job once the tree is
    # pushed or reported; left alone it puts another ~30 MB in $TMPDIR on
    # every run, unseen. A directory the human named with --staging is
    # theirs to keep, and a tree left behind by a failure stays put too —
    # the error message above names it so the work can be retried by hand.
    if ephemeral:
        shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
