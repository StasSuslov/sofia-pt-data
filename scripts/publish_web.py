"""Publish the web export as a static site (CLAUDE.md D11).

Build the frontend,
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
import shutil
import subprocess  # noqa: S404  # CalledProcessError is caught here
import sys
import tempfile
from pathlib import Path

# export_web.py lives beside this file; the index format is defined there and
# is imported rather than restated, for the reason in the docstring above.
# webpublish/ sits beside it too, hence the same path entry.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from webpublish.build import build_frontend, push
from webpublish.cli import build_parser, print_summary
from webpublish.tree import build_staging_tree, select_day_bundles

__all__ = ('build_staging_tree', 'main', 'select_day_bundles')  # noqa: WPS410


def _trailing_slash(base_path: str) -> str:
    return base_path if base_path.endswith('/') else f'{base_path}/'


def _assemble(
    args: argparse.Namespace,
    base_path: str,
    staging_dir: Path,
) -> dict:
    dist_dir = build_frontend(args.frontend_dir, base_path)
    return build_staging_tree(
        args.web_dir,
        dist_dir,
        staging_dir,
        args.days,
    )


def _publish(args: argparse.Namespace, staging_dir: Path) -> None:
    try:
        push(staging_dir, args.repo)
    except subprocess.CalledProcessError as error:
        sys.stderr.write(
            f'Tree built in {staging_dir} but the push failed: {error}\n',
        )
        sys.exit(1)


def main() -> None:
    """Build the site, assemble the tree and push it unless --dry-run."""
    args = build_parser(__doc__).parse_args()

    base_path = _trailing_slash(args.base_path)
    staging_dir = args.staging or Path(tempfile.mkdtemp(prefix='publish-web-'))
    ephemeral = args.staging is None

    try:
        report = _assemble(args, base_path, staging_dir)
    except (ValueError, subprocess.CalledProcessError) as error:
        sys.stderr.write(f'Nothing published: {error}\n')
        sys.exit(1)

    if not args.dry_run:
        _publish(args, staging_dir)

    print_summary(
        report,
        staging_dir,
        base_path,
        dry_run=args.dry_run,
        staging_kept=not ephemeral,
    )

    # A temp directory this script made has done its job once the tree is
    # pushed or reported; left alone it puts another ~30 MB in $TMPDIR on
    # every run, unseen. A directory the human named with --staging is
    # theirs to keep, and a tree left behind by a failure stays put too —
    # the error message above names it so the work can be retried by hand.
    if ephemeral:
        shutil.rmtree(staging_dir, ignore_errors=True)


if __name__ == '__main__':
    main()
