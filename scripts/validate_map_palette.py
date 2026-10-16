#!/usr/bin/env python3
"""Validate the highlight casing colour against the speed ramp.

Is the highlight casing colour distinguishable from every colour SPEED_RAMP
can render, including under simulated colour-vision deficiency?

Both colours are read out of frontend/src/color.ts, so this cannot drift away
from what the map draws. Same reason derive_bbox.py exists: a claim sitting in
a comment should be re-runnable, not taken on trust.

Method (named here because the tool it came from lives in a version-pinned
cache no reader can open): hex -> linear sRGB -> OKLab (Ottosson), CVD
simulation by the Machado, Oliveira & Fernandes (2009) matrices at severity
1.0 in linear sRGB, distance as Euclidean OKLab distance x100 -- not any CIE
Delta E. Floors below are the ones SPEED_RAMP itself was held to.

Usage: python3 scripts/validate_map_palette.py [path/to/color.ts]
Exit status 1 if anything fails its floor.
"""

import sys
from pathlib import Path

from mappalette.distance import contrast
from mappalette.palette import color_ts_from_argv, read_palette
from mappalette.ramp import RAMP_SAMPLES, ramp_samples
from mappalette.ramp import worst_over_ramp as ramp_worst

# Thresholds this project already holds SPEED_RAMP to (validate_palette.py's
# own)
CVD_TARGET = 8.0  # OKLab Delta E x100, protan/deutan, "pass"
CVD_FLOOR = 6.0  # below this, hard FAIL; between here and CVD_TARGET is WARN
NORMAL_FLOOR = 15.0  # OKLab Delta E x100, unsimulated vision, hard gate

PASS, WARN, FAIL = 'PASS', 'WARN', 'FAIL'
LABEL_WIDTH = 28
VISIONS = (
    (None, 'normal vision'),
    ('protan', 'protanopia'),
    ('deutan', 'deuteranopia'),
    ('tritan', 'tritanopia (reported only)'),
)
SURFACES = (
    ('#fcfcfb', 'light reference surface'),
    ('#1a1a19', 'dark reference surface'),
)
DEFAULT_COLOR_TS = (
    Path(__file__).resolve().parent.parent / 'frontend/src/color.ts'
)


def _say(text: str = '') -> None:
    sys.stdout.write(f'{text}\n')


def _cvd_state(distance: float) -> str:
    if distance >= CVD_TARGET:
        return PASS
    return WARN if distance >= CVD_FLOOR else FAIL


def _verdict(kind: str | None, distance: float) -> tuple:
    """Return (note, failed) for the worst distance under vision `kind`."""
    if kind is None:
        state = PASS if distance >= NORMAL_FLOOR else FAIL
        return f'(>= NORMAL_FLOOR {NORMAL_FLOOR}) -> {state}', state == FAIL
    if kind in {'protan', 'deutan'}:
        state = _cvd_state(distance)
        return f'(>= CVD_TARGET {CVD_TARGET}) -> {state}', state == FAIL
    return '', False


def _report_one(casing: str, samples: list, kind: str | None, label: str):
    """Print the line for one vision kind; return True if its gate failed."""
    distance, worst_color = ramp_worst(casing, samples, kind)
    note, failed = _verdict(kind, distance)
    shown = format(distance, '5.1f')
    _say(
        f'{label.ljust(LABEL_WIDTH)} worst ΔE = {shown}  '
        f'(closest ramp point: {worst_color})  {note}',
    )
    return failed


def _report_vision(casing: str, samples: list) -> bool:
    """Print one line per vision kind; return True if any hard gate failed."""
    failures = [
        _report_one(casing, samples, kind, label) for kind, label in VISIONS
    ]
    return any(failures)


def _report_contrast(casing: str) -> None:
    _say()
    for surface, label in SURFACES:
        ratio = format(contrast(casing, surface), '.2f')
        _say(
            f'Contrast vs {label} ({surface}): {ratio}:1  # reported only: '
            'the map sits on OSM tiles, not on a flat surface',
        )


def main() -> int:
    """Validate the casing colour against the ramp; 1 if a floor fails."""
    color_ts = color_ts_from_argv(sys.argv, DEFAULT_COLOR_TS)
    ramp, casing = read_palette(color_ts)
    samples = ramp_samples(ramp)
    _say(f'Casing color: {casing}')
    _say(
        f'Ramp domain sampled at {RAMP_SAMPLES} points '
        "(matches speedToColor's own lerp)",
    )
    _say(
        f'Thresholds: NORMAL_FLOOR={NORMAL_FLOOR}  '
        f'CVD_TARGET={CVD_TARGET}  CVD_FLOOR={CVD_FLOOR}',
    )
    _say()
    failed = _report_vision(casing, samples)
    _report_contrast(casing)
    return 1 if failed else 0


if __name__ == '__main__':
    sys.exit(main())
