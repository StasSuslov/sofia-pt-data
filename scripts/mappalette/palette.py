"""Read SPEED_RAMP and the casing colour out of frontend/src/color.ts."""

import re
from pathlib import Path

HEX_COLOR = r'#[0-9a-fA-F]{6}'
MIN_RAMP_STOPS = 2


def color_ts_from_argv(argv: list, default: Path) -> Path:
    """The optional first CLI argument as the color.ts path, else `default`."""
    return Path(argv[1]) if len(argv) > 1 else default


def read_palette(color_ts: Path) -> tuple:
    """Return (SPEED_RAMP stops, casing colour) parsed out of color.ts."""
    source = color_ts.read_text(encoding='utf-8')
    ramp = re.findall(
        HEX_COLOR,
        re.search(r'SPEED_RAMP[^=]*=\s*\[(.*?)\]', source, re.DOTALL).group(1),
    )
    casing = re.search(
        rf'HIGHLIGHT_CASING_COLOR\s*=\s*"({HEX_COLOR})"',
        source,
    ).group(1)
    if not (len(ramp) >= MIN_RAMP_STOPS and casing):
        message = f'could not read the palette out of {color_ts}'
        raise AssertionError(message)
    return tuple(ramp), casing
