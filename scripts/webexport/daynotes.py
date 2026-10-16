"""Which exported days were not complete calendar days."""

from pathlib import Path

from webexport.constants import INCOMPLETE_COVERAGE_PCT
from webexport.fileio import read_json


def _day_note(manifest: dict) -> str | None:
    """Why this day's manifest says it is not a complete closed day, if so."""
    if manifest.get('day_in_progress'):
        return (
            'day was still in progress when its own manifest was generated, '
            'not a closed calendar day'
        )
    coverage = manifest.get('coverage_pct')
    if coverage is not None and coverage < INCOMPLETE_COVERAGE_PCT:
        return f'only {coverage}% coverage of the calendar day'
    return None


def incomplete_day_notes(data_dir: Path, days: list) -> dict:
    """Map a day to a human-readable reason it was not a complete day.

    Covers every day in `days` whose own
    <date>.manifest.json (generate_manifest.py's output) shows it wasn't a
    complete, closed calendar day. Says nothing about a day whose manifest
    is missing, rather than guessing.
    """
    notes = {}
    for day in days:
        manifest_path = data_dir / f'{day}.manifest.json'
        note = (
            _day_note(read_json(manifest_path))
            if manifest_path.exists()
            else None
        )
        if note:
            notes[day] = note
    return notes
