"""Preparing the staging directory: emptiness check and the dist copy."""

import shutil
from functools import partial
from pathlib import Path


def require_empty(staging_dir: Path) -> None:
    """Create `staging_dir` if absent and refuse a non-empty one.

    Args:
        staging_dir: The directory the tree is built in.

    Raises:
        ValueError: The directory exists and holds something.
    """
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
            f'{staging_dir} is not empty — refusing to build a publishable '
            'tree in it. '
            'Publishing force-pushes this directory as a fresh repository; '
            'point --staging '
            'at a new or empty path.',
        )
    staging_dir.mkdir(parents=True, exist_ok=True)


def _skip_data(dist_dir: Path, src: str, names: list[str]) -> set[str]:
    return {'data'} if Path(src) == dist_dir else set()


def copy_dist(dist_dir: Path, staging_dir: Path) -> None:
    """Copy the built frontend without its stale data/ directory.

    Args:
        dist_dir: The built frontend.
        staging_dir: The directory the tree is built in.
    """
    # dist/data is Vite's dereferenced copy of the public/data dev symlink:
    # stale, unwindowed, and about to be replaced by the real thing below.
    # Matched against dist_dir itself so a `data` directory nested somewhere
    # inside the built assets would still be copied.
    shutil.copytree(
        dist_dir,
        staging_dir,
        dirs_exist_ok=True,
        ignore=partial(_skip_data, dist_dir),
    )
