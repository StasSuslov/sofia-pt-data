"""The one attribution every bundle in a publishable tree agrees on."""

import json
from pathlib import Path


def _bundle_manifests(staging_data: Path) -> list[Path]:
    bundles = sorted(staging_data.glob('*/manifest.json')) + sorted(
        staging_data.glob('typical_weekday/*/manifest.json'),
    )
    # the period index, not a bundle
    return [path for path in bundles if path.parent.name != 'typical_weekday']


def _manifest_attribution(path: Path, staging_data: Path) -> dict:
    manifest = json.loads(path.read_text(encoding='utf-8'))
    attribution = manifest.get('attribution')
    if not attribution:
        where = path.relative_to(staging_data)
        raise ValueError(
            f'{where} carries no attribution — refusing to '
            'publish data whose source and licence it cannot state. Re-run '
            'export_web.py with --city, or with a data directory named '
            'after the city.',
        )
    return attribution


def collect_attribution(staging_data: Path) -> dict:
    """Return the one attribution every bundle in the tree agrees on.

    Refuses a tree whose bundles disagree, and a bundle that names no source
    at all. Both refusals guard the same mistake: a site that credits one
    city's operator, or publishes one city's licence, over another city's
    data. export_web.py writes attribution: null when it could not resolve a
    city, so that export reaches exactly this check instead of the web.

    Args:
        staging_data: The data/ directory of the staging tree.

    Returns:
        The shared attribution.

    Raises:
        ValueError: A bundle has no attribution, none was found, or the
            bundles carry different ones.
    """
    found = {}
    for path in _bundle_manifests(staging_data):
        attribution = _manifest_attribution(path, staging_data)
        found[json.dumps(attribution, sort_keys=True)] = attribution
    if not found:
        raise ValueError(f'no bundle manifests found under {staging_data}')
    if len(found) > 1:
        raise ValueError(
            f'the bundles in this export carry {len(found)} different '
            'attributions — one '
            "site publishes one city's data; export each city to its own tree",
        )
    return next(iter(found.values()))
