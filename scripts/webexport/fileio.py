"""Reading and writing the JSON files of the export."""

import gzip
import json
from pathlib import Path

from webexport.constants import UTF8


def read_json(path: Path) -> object:
    """Parse one JSON file."""
    return json.loads(path.read_text(encoding=UTF8))


def write_compact(path: Path, payload: object) -> None:
    """Write `payload` with no whitespace, the form the browser downloads."""
    path.write_text(
        json.dumps(payload, separators=(',', ':'), ensure_ascii=False),
        encoding=UTF8,
    )


def write_pretty(path: Path, payload: object) -> None:
    """Write `payload` indented, with a trailing newline, for people."""
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    path.write_text(f'{text}\n', encoding=UTF8)


def gzip_size(path: Path) -> int:
    """Return the size of `path` gzipped at level 9 (the budget is measured)."""
    return len(gzip.compress(path.read_bytes(), compresslevel=9))
