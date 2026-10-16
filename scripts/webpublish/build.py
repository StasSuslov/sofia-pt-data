"""The two external steps: the frontend build and the git push."""

import datetime as dt
import os
import subprocess  # noqa: S404  # npm and git are the whole point
from pathlib import Path


def build_frontend(frontend_dir: Path, base_path: str) -> Path:
    """`npm run build` with the published URL prefix in the environment.

    The two variables are one fact spelled twice: Vite rewrites asset URLs
    with VITE_BASE_PATH (see frontend/vite.config.ts) and the fetch layer
    prefixes data URLs with VITE_DATA_BASE_URL (frontend/src/data.ts). They
    are derived from the same argument here so they cannot drift apart.

    Args:
        frontend_dir: The Vite project.
        base_path: URL prefix with a trailing slash.

    Returns:
        The dist directory the build produced.

    Raises:
        ValueError: The build reported success but left no dist directory.
    """
    env = {
        **os.environ,
        'VITE_BASE_PATH': base_path,
        'VITE_DATA_BASE_URL': f'{base_path}data',
    }
    subprocess.run(
        ['npm', 'run', 'build'],  # noqa: S607  # npm from PATH by design
        cwd=frontend_dir,
        env=env,
        check=True,
    )
    dist_dir = frontend_dir / 'dist'
    if not dist_dir.is_dir():
        raise ValueError(
            f'npm run build reported success but produced no {dist_dir}',
        )
    return dist_dir


def push(staging_dir: Path, repo: str) -> None:
    """Force-push the staging tree as the single commit on `main`.

    --force, and a repository initialised from scratch each time, is the
    whole retention story: the remote ends up with one commit whose tree is
    this staging tree, and the previous publish's blobs stop being
    reachable. Any manual commit made in the published repository is
    discarded by the next publish — it is generated output, and the README
    written into it says so.

    Args:
        staging_dir: The tree to publish.
        repo: The push target.
    """
    stamp = dt.datetime.now(dt.UTC).isoformat(timespec='seconds')
    message = f'Publish web export {stamp}'
    for cmd in (
        ['init', '-q'],
        ['add', '-A'],
        ['commit', '-q', '-m', message],
        ['branch', '-M', 'main'],
        ['remote', 'add', 'origin', repo],
        ['push', '--force', 'origin', 'main'],
    ):
        subprocess.run(  # noqa: S603  # fixed argv, no shell
            ['git', '-C', str(staging_dir), *cmd],  # noqa: S607  # git on PATH
            check=True,
        )
