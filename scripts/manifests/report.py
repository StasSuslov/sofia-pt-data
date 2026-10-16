"""Per-day status line printed after a manifest is written."""

from pathlib import Path

SHA_PREVIEW_CHARS = 12


def _coverage_note(manifest: dict) -> str:
    if manifest['heartbeat_available']:
        if manifest['heartbeat_partial']:
            return (
                '  [heartbeat started mid-day — coverage before that point '
                'is inferred, less reliable]'
            )
        return ''
    return '  [no heartbeat log — coverage inferred, less reliable]'


def _heartbeat_note(manifest: dict) -> str:
    malformed = sum((
        manifest['data_malformed_lines'],
        manifest['polls_malformed_lines'] or 0,
    ))
    if not malformed:
        return _coverage_note(manifest)
    return (
        f'{_coverage_note(manifest)}'
        f'  [WARNING: {malformed} malformed line(s) skipped — likely a '
        'mid-transfer pull; checksum reflects exactly what was read, '
        'not necessarily a complete file]'
    )


def report_line(path: Path, manifest: dict) -> str:
    """Return the stdout line summarising one freshly written manifest.

    Args:
        path: The day file the manifest describes.
        manifest: The manifest just written.

    Returns:
        One newline-terminated status line.
    """
    preview = manifest['data_sha256'][:SHA_PREVIEW_CHARS]
    return (
        f'{path.name}: {manifest["total_vehicle_records"]:,} records | '
        f'coverage {manifest["coverage_pct"]}% | '
        f'{manifest["gap_count"]} gap(s) | '
        f'sha256={preview}…{_heartbeat_note(manifest)}\n'
    )
