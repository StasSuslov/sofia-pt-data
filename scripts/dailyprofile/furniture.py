"""Heading, peak bands, grid and hour labels of the daily-profile SVG."""

from dailyprofile import svgkit as kit
from dailyprofile.constants import PEAK_BANDS, SOLID_MIN_SEGMENTS
from dailyprofile.profile import slot_label, slots

ALL_SLOTS = tuple(slots())
BACKGROUND = (
    ('width', kit.WIDTH),
    ('height', kit.HEIGHT),
    (kit.FILL, '#ffffff'),
)
GRID_STEP = 5
GRID_KMH = range(0, int(kit.Y_MAX) + 1, GRID_STEP)
LABEL_EVERY_HOURS = 3
HOURS_PER_DAY = 24
HOUR_LABEL_SLOTS = frozenset(
    slot_label(hour, 0) for hour in range(0, HOURS_PER_DAY, LABEL_EVERY_HOURS)
)
TITLE_Y = 24
SUBTITLE_Y = 42
NOTE_FROM_BOTTOM = 38
TICK_GAP = 8
TICK_DROP = 4
HOUR_LABEL_DROP = 18


def heading(period: dict) -> list:
    """Opening <svg>, background, title, subtitle and the thin-grey note."""
    first, last = period['first_date'], period['last_date']
    weekdays = len(period['days_in_median_mon_fri'])
    return [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {kit.WIDTH} {kit.HEIGHT}" '
            f'width="{kit.WIDTH}" height="{kit.HEIGHT}" '
            'font-family="Helvetica,Arial,sans-serif">'
        ),
        kit.tag(
            'rect',
            BACKGROUND,
        ),
        kit.tag(
            'text',
            (
                ('x', kit.LEFT),
                ('y', TITLE_Y),
                ('font-size', kit.FONT_TITLE),
                ('font-weight', 'bold'),
                (kit.FILL, '#111'),
            ),
            'Median speed by time of day, Sofia surface network',
        ),
        kit.label(
            kit.LEFT,
            SUBTITLE_Y,
            kit.FONT_SUBTITLE,
            f'Typical weekday, {first} to {last} ({weekdays} weekdays). '
            'Shaded: the hours a timetable calls peak.',
        ),
        kit.label(
            kit.LEFT,
            kit.HEIGHT - NOTE_FROM_BOTTOM,
            kit.FONT_BODY,
            f'Thin grey: fewer than {SOLID_MIN_SEGMENTS:,} segments carry a '
            'median in that slot. Night runs on night routes, so it is a '
            'different network, not a faster one.',
        ),
    ]


def bands() -> list:
    """Shaded rectangles behind the peak windows."""
    found = []
    for lo, hi in PEAK_BANDS:
        x_lo = kit.x_of(ALL_SLOTS.index(lo))
        x_hi = kit.x_of(ALL_SLOTS.index(hi))
        found.append(
            kit.tag(
                'rect',
                (
                    ('x', kit.one_decimal(x_lo)),
                    ('y', kit.TOP),
                    ('width', kit.one_decimal(x_hi - x_lo)),
                    ('height', kit.PLOT_H),
                    (kit.FILL, '#ffd9c7'),
                ),
            ),
        )
    return found


def _grid_row(kmh: int) -> list:
    y_pos = kit.y_of(kmh)
    line = kit.tag(
        'line',
        (
            ('x1', kit.LEFT),
            ('y1', kit.one_decimal(y_pos)),
            ('x2', kit.LEFT + kit.PLOT_W),
            ('y2', kit.one_decimal(y_pos)),
            ('stroke', '#dcdcdc'),
            ('stroke-width', 1),
        ),
    )
    tick = kit.label(
        kit.LEFT - TICK_GAP,
        kit.one_decimal(y_pos + TICK_DROP),
        kit.FONT_BODY,
        str(kmh),
        (('text-anchor', 'end'),),
    )
    return [line, tick]


def grid() -> list:
    """A grid line and a km/h tick label every GRID_STEP km/h."""
    rows = [_grid_row(kmh) for kmh in GRID_KMH]
    return [part for row in rows for part in row]


def hour_labels() -> list:
    """Time-of-day labels under the axis, every third hour."""
    return [
        kit.label(
            kit.one_decimal(kit.x_of(index)),
            kit.one_decimal(kit.TOP + kit.PLOT_H + HOUR_LABEL_DROP),
            kit.FONT_BODY,
            slot,
            (('text-anchor', 'middle'),),
        )
        for index, slot in enumerate(ALL_SLOTS)
        if slot in HOUR_LABEL_SLOTS
    ]
