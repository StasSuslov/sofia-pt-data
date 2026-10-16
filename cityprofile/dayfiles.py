"""Day-file helpers shared by generate_manifest.py and segment_speeds.py.

Both need to find a <date>.jsonl day file and its <date>.polls.jsonl
heartbeat companion whether or not deploy/sofia-compress.service has gzipped
it by the time scripts/fetch_data.sh pulls it down from the VPS. gzip and
pathlib are stdlib, so this stays as dependency-free as config.py.
"""

import gzip
from pathlib import Path


def date_from_path(path: Path) -> str:
    """Return the YYYY-MM-DD a day file belongs to, gzipped or not.

    Works for the .polls.jsonl companion too. Path.stem only strips one
    suffix, so "2026-08-29.jsonl.gz".stem is "2026-08-29.jsonl", not the date
    this is actually needed for — strip the optional .gz first, then take
    everything before the first remaining dot.
    """
    name = path.name
    name = name.removesuffix('.gz')
    return name.split('.', 1)[0]


def resolve_day_file(data_dir: Path, date_str: str, suffix: str) -> Path:
    """Return the path to a per-day file that may be stored gzipped on disk.

    See deploy/sofia-compress.service. The uncompressed copy is preferred
    when both exist. `suffix` is e.g. ".jsonl" for the data file itself or
    ".polls.jsonl" for its heartbeat companion. What this resolves to is a
    pure storage detail — manifests always describe the uncompressed bytes
    regardless (see CLAUDE.md's provenance invariant for gzipped archives).

    Returns the uncompressed path even when neither exists, so callers can
    still call .exists() on the result and get a sane "not found".
    """
    plain = data_dir / f'{date_str}{suffix}'
    if plain.exists():
        return plain
    gz_path = data_dir / f'{date_str}{suffix}.gz'
    return gz_path if gz_path.exists() else plain


def find_day_files(data_dir: Path) -> list[Path]:
    """Return one Path per calendar day found under data_dir.

    A day counts whether stored as <date>.jsonl or <date>.jsonl.gz. When both
    exist for the same date, resolve_day_file()'s uncompressed-first
    preference wins, so callers never have to think about compression twice.
    """
    plain = data_dir.glob('????-??-??.jsonl')
    dates = {date_from_path(day_file) for day_file in plain}
    zipped = data_dir.glob('????-??-??.jsonl.gz')
    dates |= {date_from_path(day_file) for day_file in zipped}
    return [resolve_day_file(data_dir, day, '.jsonl') for day in sorted(dates)]


def open_maybe_gzip(path: Path, mode: str = 'rb', encoding: str | None = None):
    """Open a day file (or heartbeat log) whether it's gzipped on disk or not.

    Text-mode callers pass mode="rt", encoding="utf-8"; binary callers
    (sha256 hashing) use the defaults.
    """
    opener = gzip.open if path.suffix == '.gz' else open
    return opener(path, mode, encoding=encoding)
