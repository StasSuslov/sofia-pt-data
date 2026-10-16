"""The runs.ndjson log in the alerts directory."""

import datetime as dt
import json
import os
from pathlib import Path

RUN_LOG = 'runs.ndjson'
_TAIL_BYTES = 4096


def _tail_lines(path: Path) -> list[bytes]:
    with path.open('rb') as tail:
        size = tail.seek(0, os.SEEK_END)
        tail.seek(max(0, size - _TAIL_BYTES))
        return tail.read().splitlines()


def last_run(output_dir: Path) -> dict | None:
    """The newest runs.ndjson entry, read off the file's tail."""
    try:
        lines = _tail_lines(output_dir / RUN_LOG)
    except FileNotFoundError:
        return None
    try:
        return json.loads(lines[-1]) if lines else None
    except ValueError:
        return None


def last_entities(output_dir: Path) -> int | None:
    """Alert count of the previous run, None if it left no entry."""
    return (last_run(output_dir) or {}).get('entities')


def log_run(output_dir: Path, now: dt.datetime, **fields) -> None:
    """Append one run entry to runs.ndjson, creating the directory."""
    output_dir.mkdir(parents=True, exist_ok=True)
    entry = {'ts': int(now.timestamp()), **fields}
    with (output_dir / RUN_LOG).open('a', encoding='utf-8') as log:
        log.write(f'{json.dumps(entry)}\n')
