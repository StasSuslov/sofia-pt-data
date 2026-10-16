"""Geometry and element helpers for the daily-profile SVG."""

WIDTH = 820
HEIGHT = 420
LEFT = 60
RIGHT = 24
TOP = 56
BOTTOM = 84
PLOT_W = WIDTH - LEFT - RIGHT
PLOT_H = HEIGHT - TOP - BOTTOM
Y_MAX = 30.0
LAST_SLOT = 95  # 96 quarter-hour slots, indexed 0 to 95

FONT_TITLE = 15
FONT_SUBTITLE = 12
FONT_BODY = 11
GREY = '#555'
FILL = 'fill'


def one_decimal(number: float) -> str:
    """Format as '{n:.1f}'."""
    return format(number, '.1f')


def tag(name: str, attrs: tuple, text: str | None = None) -> str:
    """One SVG element: self-closing without text, wrapped around it with."""
    inner = ' '.join(f'{key}="{attr}"' for key, attr in attrs)
    if text is None:
        return f'<{name} {inner}/>'
    return f'<{name} {inner}>{text}</{name}>'


def label(x_pos, y_pos, size, text: str, extra: tuple = ()) -> str:
    """A grey <text> element; `extra` attributes go before the fill."""
    attrs = (('x', x_pos), ('y', y_pos), ('font-size', size), *extra)
    return tag('text', (*attrs, (FILL, GREY)), text)


def x_of(index: int) -> float:
    """X pixel of the slot with this index."""
    return LEFT + PLOT_W * index / LAST_SLOT


def y_of(kmh: float) -> float:
    """Y pixel of a speed; the axis starts at zero."""
    return TOP + PLOT_H * (1 - kmh / Y_MAX)
