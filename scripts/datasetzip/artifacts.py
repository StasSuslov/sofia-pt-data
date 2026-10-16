"""Report lines for the files written to the output directory."""

from pathlib import Path


def _artifact_line(path: Path) -> str:
    size = path.stat().st_size
    return f'  {path.name}: {size:,} bytes'


def artifact_lines(packed: dict) -> list[str]:
    """Return the "Artifacts:" section of the report.

    Args:
        packed: The dict returned by pipeline.run().

    Returns:
        A header line followed by one line per written file.
    """
    artifacts = (
        packed['rt_zip_path'],
        packed['static_zip_path'],
        *packed['doc_paths'],
        packed['sums_path'],
    )
    return ['\nArtifacts:', *map(_artifact_line, artifacts)]
