"""The size and count report an export prints (measure the budget)."""

import sys
from pathlib import Path

from webexport.constants import MANIFEST_NAME
from webexport.fileio import gzip_size

GEOMETRY_NAME = 'geometry.json'
SLOTS_DIR_NAME = 'timeslots'


def _file_row(label: str, path: Path, tail: str = '') -> int:
    """Print one file's raw and gzipped size and return the gzipped one."""
    raw_bytes = path.stat().st_size
    gz_bytes = gzip_size(path)
    sys.stdout.write(
        f'  {label}  {raw_bytes:,}B raw / {gz_bytes:,}B gz{tail}\n',
    )
    return gz_bytes


def _geometry_tail(geometry: dict) -> str:
    segments = len(geometry['shape_idx'])
    shapes = len(geometry['shape_ids'])
    return f'  ({segments:,} segments, {shapes:,} shapes)'


def _slots_row(slots_dir: Path) -> int:
    """Print the timeslot summary and return the heaviest slot's gz size."""
    slot_gz = [gzip_size(path) for path in sorted(slots_dir.glob('*.json'))]
    avg_gz = sum(slot_gz) / len(slot_gz) if slot_gz else 0
    avg_text = format(avg_gz, ',.0f')
    max_gz = max(slot_gz, default=0)
    sys.stdout.write(f'  {len(slot_gz)} timeslot files, ')
    sys.stdout.write(f'avg {avg_text}B gz, ')
    sys.stdout.write(f'largest {max_gz:,}B gz\n')
    return max_gz


def report_sizes(out_dir: Path, geometry: dict) -> None:
    """Print the byte counts of a bundle against the first-load budget."""
    # --- size report: measure the budget, don't assume it holds ---
    sys.stdout.write(f'\n{out_dir}\n')
    manifest_gz = _file_row('manifest.json', out_dir / MANIFEST_NAME)
    geometry_gz = _file_row(
        'geometry.json',
        out_dir / GEOMETRY_NAME,
        _geometry_tail(geometry),
    )
    max_gz = _slots_row(out_dir / SLOTS_DIR_NAME)
    # The heaviest slot, not slot_paths[0]. Alphabetically first is 0000.json,
    # the emptiest file of the day (5 KB gz here against 40 KB at the morning
    # peak), and the frontend opens on 08:00 anyway. A budget check that picks
    # the cheapest slot reports a load nobody performs.
    first_load_gz = manifest_gz + geometry_gz + max_gz
    sys.stdout.write(
        '  first load (manifest + geometry + heaviest slot): '
        f'{first_load_gz:,}B gz  (budget: ~1,000,000B)\n',
    )


def report_counts(
    bins: tuple,
    pairs: tuple,
    min_samples: int,
    missing_shapes: int,
) -> None:
    """Print the threshold's effect, and warn about shapes the feed lacks.

    Args:
        bins: Bin counts (before, after) the threshold.
        pairs: Segment counts (before, after) the threshold.
        min_samples: The threshold itself.
        missing_shapes: Pairs whose shape_key the static feed lacks.
    """
    sys.stdout.write(
        f'  bins: {bins[0]:,} -> {bins[1]:,} retained '
        f'(min_samples={min_samples}), '
        f'segments: {pairs[0]:,} -> {pairs[1]:,}\n',
    )
    if missing_shapes:
        sys.stderr.write(
            f'  WARNING: {missing_shapes} (shape,segment) pair(s) '
            'referenced a shape_key absent from the static feed(s) loaded '
            '-- dropped from geometry and every timeslot file\n',
        )
