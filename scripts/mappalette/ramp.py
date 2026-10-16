"""SPEED_RAMP and the casing colour, read from the frontend (no drift)."""

from operator import itemgetter

from mappalette.colorspace import HEX_BASE
from mappalette.distance import delta_e

# Dense sweep of the ramp's own continuous interpolation, not just the 13 stops.
RAMP_SAMPLES = 1001
BYTE_MASK = 255
GREEN_SHIFT = 8
RED_SHIFT = 16


def _hex_to_rgb255(hex_color: str) -> tuple:
    packed = int(hex_color.lstrip('#'), HEX_BASE)
    return (
        (packed >> RED_SHIFT) & BYTE_MASK,
        (packed >> GREEN_SHIFT) & BYTE_MASK,
        packed & BYTE_MASK,
    )


def _lerp(start: float, end: float, fraction: float) -> float:
    return start + (end - start) * fraction


def _blend(start_hex: str, end_hex: str, fraction: float) -> str:
    channels = (
        round(_lerp(start, end, fraction))
        for start, end in zip(
            _hex_to_rgb255(start_hex),
            _hex_to_rgb255(end_hex),
            strict=True,
        )
    )
    digits = ''.join(format(channel, '02x') for channel in channels)
    return f'#{digits}'


def ramp_color_at(ramp: tuple, position: float) -> str:
    """Position in [0,1] over the ramp domain -> hex, mirrors speedToColor."""
    scaled = position * (len(ramp) - 1)
    low = int(scaled)
    high = min(len(ramp) - 1, low + 1)
    return _blend(ramp[low], ramp[high], scaled - low)


def ramp_samples(ramp: tuple) -> list:
    """The ramp colour at RAMP_SAMPLES evenly spaced points."""
    return [
        ramp_color_at(ramp, index / (RAMP_SAMPLES - 1))
        for index in range(RAMP_SAMPLES)
    ]


def worst_over_ramp(casing: str, samples: list, kind: str | None) -> tuple:
    """Minimum delta_e (least distinguishable pair) and its ramp colour.

    Compares `casing` with every sampled point across the ramp's full domain,
    under vision `kind`.
    """
    return min(
        ((delta_e(casing, color, kind), color) for color in samples),
        key=itemgetter(0),
    )
