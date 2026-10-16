"""The daily-profile SVG: one line over shaded peak bands, y axis from zero."""

from pathlib import Path

from dailyprofile import svgkit as kit
from dailyprofile.furniture import (
    bands,
    grid,
    heading,
    hour_labels,
)
from dailyprofile.lines import edge_runs, points
from dailyprofile.profile import core_points

SOLID_STYLE = (('stroke', '#1a4f8a'), ('stroke-width', '2.5'))
THIN_STYLE = (
    ('stroke', '#9aa5b1'),
    ('stroke-width', '1.2'),
    ('stroke-dasharray', '4 3'),
)
CAPTION_FROM_BOTTOM = 20
UNIT_LEFT_GAP = 46
UNIT_ABOVE_TOP = 10


def _xy_text(xy: tuple) -> str:
    return ','.join(map(kit.one_decimal, xy))


def _polyline(run: tuple) -> str:
    solid, edge = run
    coords = ' '.join(_xy_text(xy) for xy in edge)
    style = SOLID_STYLE if solid else THIN_STYLE
    attrs = (('points', coords), (kit.FILL, 'none'), *style)
    return kit.tag('polyline', attrs)


def _polylines(prof: dict) -> list:
    return [_polyline(run) for run in edge_runs(points(prof))]


def _caption(prof: dict) -> str:
    # The caption names the range of the line being drawn, not a remembered
    # one: the first figure's 1.8 km/h stayed in this string while the second
    # figure plotted 2.9, which is the report's own headline contradicted in
    # its own image.
    core = [kmh for _, kmh in core_points(prof)]
    span = kit.one_decimal(max(core) - min(core))
    return kit.label(
        kit.LEFT,
        kit.HEIGHT - CAPTION_FROM_BOTTOM,
        kit.FONT_BODY,
        'The y axis starts at zero on purpose: rescaled to the data, a '
        f'range of {span} km/h would look like a rush hour.',
    )


def svg(prof: dict, period: dict, out: Path) -> None:
    """Draw the profile of `prof` for `period` into the file `out`."""
    parts = [
        *heading(period),
        *bands(),
        *grid(),
        *hour_labels(),
        *_polylines(prof),
        kit.label(
            kit.LEFT - UNIT_LEFT_GAP,
            kit.TOP - UNIT_ABOVE_TOP,
            kit.FONT_BODY,
            'km/h',
        ),
        _caption(prof),
        '</svg>',
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text('\n'.join((*parts, '')), encoding='utf-8')
