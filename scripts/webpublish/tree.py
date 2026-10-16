"""Assembly of the publishable staging tree."""

import shutil
from pathlib import Path

from export_web import write_root_index

from webpublish.attribution import collect_attribution
from webpublish.readme import readme_text
from webpublish.staging import copy_dist, require_empty

NOJEKYLL = '.nojekyll'
TYPICAL = 'typical_weekday'


def tree_size_bytes(root: Path) -> int:
    """Total size of the regular files under `root`."""
    return sum(
        path.stat().st_size for path in root.rglob('*') if path.is_file()
    )


def select_day_bundles(
    web_dir: Path,
    days: int,
) -> tuple[list[Path], list[Path]]:
    """(published, skipped) day-bundle directories, newest `days` published.

    Same admission rule as write_root_index(): a `????-??-??` directory
    counts as a bundle only once it holds a manifest.json. A directory
    half-written by an interrupted export has no manifest yet, and shipping
    it would put a day in the switcher whose timeslots 404.

    Sorted by name, which for ISO dates is chronological — no date parsing
    needed, and a directory that merely looks like a date sorts harmlessly.

    Args:
        web_dir: The web export.
        days: How many of the newest bundles to publish.

    Returns:
        The published and the skipped bundle directories.

    Raises:
        ValueError: `days` is not positive.
    """
    bundles = [
        bundle
        for bundle in sorted(web_dir.glob('????-??-??'))
        if bundle.is_dir() and (bundle / 'manifest.json').exists()
    ]
    if days <= 0:
        raise ValueError(f'--days must be positive, got {days}')
    return bundles[-days:], bundles[:-days]


def _require_typical(web_dir: Path) -> None:
    if not (web_dir / TYPICAL).is_dir():
        raise ValueError(
            f'no {TYPICAL}/ in {web_dir} — run export_web.py first',
        )


def _copy_typical(web_dir: Path, staging_data: Path) -> list[str]:
    shutil.copytree(web_dir / TYPICAL, staging_data / TYPICAL)
    return sorted(
        period.name
        for period in (staging_data / TYPICAL).iterdir()
        if period.is_dir()
    )


def _copy_days(
    web_dir: Path,
    staging_data: Path,
    days: int,
) -> tuple[list[str], list[str]]:
    published, skipped = select_day_bundles(web_dir, days)
    for day_dir in published:
        shutil.copytree(day_dir, staging_data / day_dir.name)
    return (
        [bundle.name for bundle in published],
        [bundle.name for bundle in skipped],
    )


def _write_readme(staging_dir: Path, attribution: dict) -> None:
    (staging_dir / NOJEKYLL).write_text('', encoding='utf-8')
    (staging_dir / 'README.md').write_text(
        readme_text(attribution),
        encoding='utf-8',
    )


def build_staging_tree(
    web_dir: Path,
    dist_dir: Path,
    staging_dir: Path,
    days: int,
) -> dict:
    """Assemble the complete publishable tree and report what went into it.

    Deliberately does no npm and no git: everything that decides *what gets
    published* lives here, so it can be run and asserted against without a
    toolchain or a network. The caller does the building and the pushing.

    `staging_dir` must be empty or absent, and that is enforced rather than
    assumed. The tree is built from scratch every run (see the module
    docstring) rather than updated in place, so a period or a day that
    disappeared upstream also disappears here instead of lingering.

    Args:
        web_dir: The web export.
        dist_dir: The built frontend.
        staging_dir: Where to assemble the tree.
        days: How many of the newest day bundles to publish.

    Returns:
        The report that print_summary renders.

    Raises:
        ValueError: The export has no typical_weekday/ or the staging
            directory is not empty.
    """
    _require_typical(web_dir)
    require_empty(staging_dir)
    copy_dist(dist_dir, staging_dir)

    staging_data = staging_dir / 'data'
    period_dirs = _copy_typical(web_dir, staging_data)
    days = _copy_days(web_dir, staging_data, days)

    # After the copying, never before: the index has to describe the tree
    # that exists, not the one that was intended.
    index = write_root_index(staging_data)

    attribution = collect_attribution(staging_data)
    _write_readme(staging_dir, attribution)

    return {
        'index': index,
        'attribution': attribution,
        'days_published': days[0],
        'days_skipped': days[1],
        'period_dirs': period_dirs,
        'current_period': (index.get(TYPICAL) or {}).get('current_period'),
        'size_bytes': tree_size_bytes(staging_dir),
    }
