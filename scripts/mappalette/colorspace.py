"""hex -> linear sRGB -> OKLab (Ottosson) conversion."""

from functools import reduce
from operator import add, mul

HEX_OFFSETS = (0, 2, 4)  # start of the red, green and blue hex pairs
HEX_BASE = 16
BYTE_MAX = 255
SRGB_LINEAR_CUTOFF = 0.04045
SRGB_LINEAR_SLOPE = 12.92
SRGB_OFFSET = 0.055
SRGB_SCALE = 1.055
SRGB_GAMMA = 2.4
CUBE_ROOT = 1 / 3

# Bjorn Ottosson's transform: linear sRGB -> LMS -> OKLab.
LINEAR_TO_LMS = (
    (0.4122214708, 0.5363325363, 0.0514459929),
    (0.2119034982, 0.6806995451, 0.1073969566),
    (0.0883024619, 0.2817188376, 0.6299787005),
)
LMS_TO_OKLAB = (
    (0.2104542553, 0.793617785, -0.0040720468),
    (1.9779984951, -2.428592205, 0.4505937099),
    (0.0259040371, 0.7827717662, -0.808675766),
)


def dot(row: tuple, vector: tuple) -> float:
    """Left-to-right dot product, kept naive so the floats stay bit-stable."""
    return reduce(add, map(mul, row, vector))


def hex_to_srgb(hex_color: str) -> tuple:
    """Parse '#rrggbb' into three channels in [0, 1]."""
    digits = hex_color.strip().lstrip('#')
    return tuple(
        int(digits[start : start + 2], HEX_BASE) / BYTE_MAX
        for start in HEX_OFFSETS
    )


def srgb_to_linear(channel: float) -> float:
    """Undo the sRGB transfer curve for one channel."""
    if channel <= SRGB_LINEAR_CUTOFF:
        return channel / SRGB_LINEAR_SLOPE
    return ((channel + SRGB_OFFSET) / SRGB_SCALE) ** SRGB_GAMMA


def hex_to_linear(hex_color: str) -> tuple:
    """Parse '#rrggbb' into linear sRGB."""
    return tuple(srgb_to_linear(chan) for chan in hex_to_srgb(hex_color))


def linear_to_oklab(red: float, green: float, blue: float) -> tuple:
    """Convert linear sRGB to OKLab (L, a, b)."""
    cone = (red, green, blue)
    lms = tuple(dot(row, cone) ** CUBE_ROOT for row in LINEAR_TO_LMS)
    return tuple(dot(row, lms) for row in LMS_TO_OKLAB)
