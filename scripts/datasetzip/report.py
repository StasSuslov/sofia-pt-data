"""The stdout report printed after a successful packaging run."""

import sys

from datasetzip.artifacts import artifact_lines


def _day_line(day: dict) -> str:
    manifest = day['manifest']
    records = manifest.get('total_vehicle_records', 0)
    return (
        f'  {day["date"]}: {records:,} records | '
        f'coverage {manifest.get("coverage_pct")}% | '
        f'{manifest.get("gap_count")} gap(s)'
    )


def _day_lines(packed: dict, start_date: str, end_date: str) -> list[str]:
    return [
        f'RT day files packed ({start_date}..{end_date}):',
        *map(_day_line, packed['verified_days']),
    ]


def _snapshot_line(snap: dict) -> str:
    size = snap['manifest'].get('size_bytes', 0)
    return f'  {snap["zip_path"].name} ({size:,} bytes)'


def _static_lines(packed: dict) -> list[str]:
    header = '\nStatic feed snapshots packed:'
    if not packed['snapshots']:
        return [header, '  none found in range']
    return [header, *map(_snapshot_line, packed['snapshots'])]


def _skipped_lines(packed: dict) -> list[str]:
    if not packed['skipped']:
        return []
    return [
        '\nSkipped (not part of the archive):',
        *[f'  {name}: {reason}' for name, reason in packed['skipped']],
    ]


def print_report(packed: dict, start_date: str, end_date: str) -> None:
    """Print what was packed, what was skipped and the artifact sizes.

    Args:
        packed: The dict returned by pipeline.run().
        start_date: First day, YYYY-MM-DD.
        end_date: Last day, YYYY-MM-DD.
    """
    lines = [
        *_day_lines(packed, start_date, end_date),
        *_static_lines(packed),
        *_skipped_lines(packed),
        *artifact_lines(packed),
    ]
    sys.stdout.write(''.join(f'{line}\n' for line in lines))
