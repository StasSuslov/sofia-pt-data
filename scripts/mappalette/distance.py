"""CVD simulation, OKLab distance and WCAG contrast.

Method (named here because the tool it came from lives in a version-pinned
cache no reader can open): CVD simulation by the Machado, Oliveira &
Fernandes (2009) matrices at severity 1.0 in linear sRGB, distance as
Euclidean OKLab distance x100 -- not any CIE Delta E.
"""

import math
from types import MappingProxyType

from mappalette.colorspace import dot, hex_to_linear, linear_to_oklab

OKLAB_DISTANCE_SCALE = 100
CONTRAST_FLARE = 0.05
LUMINANCE_WEIGHTS = (0.2126, 0.7152, 0.0722)

# Machado, Oliveira & Fernandes (2009) CVD simulation, severity 1.0, linear RGB.
PROTAN = (
    (0.152286, 1.052583, -0.204868),
    (0.114503, 0.786281, 0.099216),
    (-0.003882, -0.048116, 1.051998),
)
DEUTAN = (
    (0.367322, 0.860646, -0.227968),
    (0.280085, 0.672501, 0.047413),
    (-0.01182, 0.04294, 0.968881),
)
TRITAN = (
    (1.255528, -0.076749, -0.178779),
    (-0.078411, 0.930809, 0.147602),
    (0.004733, 0.691367, 0.3039),
)
MACHADO = MappingProxyType(
    {'protan': PROTAN, 'deutan': DEUTAN, 'tritan': TRITAN},
)


def _clamp_unit(channel: float) -> float:
    return max(0, min(1, channel))


def simulate(hex_color: str, kind: str) -> tuple:
    """Linear sRGB of the colour as seen under CVD `kind`, clamped to [0, 1]."""
    channels = hex_to_linear(hex_color)
    return tuple(_clamp_unit(dot(row, channels)) for row in MACHADO[kind])


def _oklab_of(hex_color: str, kind: str | None) -> tuple:
    channels = simulate(hex_color, kind) if kind else hex_to_linear(hex_color)
    return linear_to_oklab(*channels)


def delta_e(first: str, second: str, kind: str | None = None) -> float:
    """Euclidean distance in OKLab, x100. kind=None: unsimulated vision."""
    return OKLAB_DISTANCE_SCALE * math.dist(
        _oklab_of(first, kind),
        _oklab_of(second, kind),
    )


def relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance (for the surface-contrast lines)."""
    return dot(LUMINANCE_WEIGHTS, hex_to_linear(hex_color))


def contrast(first: str, second: str) -> float:
    """WCAG contrast ratio between two colours."""
    lighter, darker = sorted(
        (relative_luminance(first), relative_luminance(second)),
        reverse=True,
    )
    return (lighter + CONTRAST_FLARE) / (darker + CONTRAST_FLARE)
