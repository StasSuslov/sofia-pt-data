#!/usr/bin/env python3
"""
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
import math
import pathlib
import re
import sys

# -- OKLab conversion (Bjorn Ottosson's transform: linear sRGB -> LMS -> OKLab) --
def hex2srgb(h: str):
    h = h.strip().lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255 for i in (0, 2, 4))


def s2lin(c: float) -> float:
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def lin(h: str):
    return tuple(s2lin(c) for c in hex2srgb(h))


def lin2oklab(r: float, g: float, b: float):
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l, m, s = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    L = 0.2104542553 * l + 0.7936177850 * m - 0.0040720468 * s
    a = 1.9779984951 * l - 2.4285922050 * m + 0.4505937099 * s
    bb = 0.0259040371 * l + 0.7827717662 * m - 0.8086757660 * s
    return L, a, bb


# -- Machado, Oliveira & Fernandes (2009) CVD simulation, severity 1.0, linear RGB --
MACHADO = {
    "protan": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deutan": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
    "tritan": [
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ],
}


def simulate(h: str, kind: str):
    r, g, b = lin(h)
    M = MACHADO[kind]
    sr = M[0][0] * r + M[0][1] * g + M[0][2] * b
    sg = M[1][0] * r + M[1][1] * g + M[1][2] * b
    sb = M[2][0] * r + M[2][1] * g + M[2][2] * b
    return (max(0.0, min(1.0, sr)), max(0.0, min(1.0, sg)), max(0.0, min(1.0, sb)))


def deltaE(h1: str, h2: str, kind: str | None = None) -> float:
    """Euclidean distance in OKLab, x100. kind=None -> unsimulated (normal) vision."""
    a = lin2oklab(*(simulate(h1, kind) if kind else lin(h1)))
    b = lin2oklab(*(simulate(h2, kind) if kind else lin(h2)))
    return 100 * math.dist(a, b)


# -- WCAG contrast (for the surface-contrast lines, reported alongside) --
def relative_luminance(h: str) -> float:
    r, g, b = lin(h)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast(h1: str, h2: str) -> float:
    a, b = sorted((relative_luminance(h1), relative_luminance(h2)), reverse=True)
    return (a + 0.05) / (b + 0.05)


# -- Thresholds this project already holds SPEED_RAMP to (validate_palette.py's own) --
CVD_TARGET = 8.0     # OKLab Delta E x100, protan/deutan, "pass"
CVD_FLOOR = 6.0       # below this, hard FAIL; between here and CVD_TARGET is WARN
NORMAL_FLOOR = 15.0   # OKLab Delta E x100, unsimulated vision, hard gate

# -- SPEED_RAMP and the casing colour, read from the frontend so they can't drift --
COLOR_TS = pathlib.Path(sys.argv[1] if len(sys.argv) > 1
                        else pathlib.Path(__file__).resolve().parent.parent / "frontend/src/color.ts")
_ts = COLOR_TS.read_text(encoding="utf-8")
SPEED_RAMP = re.findall(r"#[0-9a-fA-F]{6}",
                        re.search(r"SPEED_RAMP[^=]*=\s*\[(.*?)\]", _ts, re.S).group(1))
CASING = re.search(r'HIGHLIGHT_CASING_COLOR\s*=\s*"(#[0-9a-fA-F]{6})"', _ts).group(1)
assert len(SPEED_RAMP) >= 2 and CASING, f"could not read the palette out of {COLOR_TS}"


def hex_to_rgb255(h: str):
    n = int(h.lstrip("#"), 16)
    return ((n >> 16) & 255, (n >> 8) & 255, n & 255)


def lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def ramp_color_at(t: float) -> str:
    """t in [0,1] over the ramp domain -> hex, mirrors color.ts's speedToColor."""
    scaled = t * (len(SPEED_RAMP) - 1)
    lo = int(scaled)
    hi = min(len(SPEED_RAMP) - 1, lo + 1)
    frac = scaled - lo
    r1, g1, b1 = hex_to_rgb255(SPEED_RAMP[lo])
    r2, g2, b2 = hex_to_rgb255(SPEED_RAMP[hi])
    r = round(lerp(r1, r2, frac))
    g = round(lerp(g1, g2, frac))
    b = round(lerp(b1, b2, frac))
    return f"#{r:02x}{g:02x}{b:02x}"


N_SAMPLES = 1001  # dense sweep of the ramp's own continuous interpolation, not just the 13 stops
ramp_samples = [ramp_color_at(i / (N_SAMPLES - 1)) for i in range(N_SAMPLES)]


def worst_over_ramp(kind: str | None):
    """Minimum deltaE (least distinguishable pair) between CASING and any
    point sampled across the ramp's full domain, under vision `kind`."""
    best = None
    for c in ramp_samples:
        d = deltaE(CASING, c, kind)
        if best is None or d < best[0]:
            best = (d, c)
    return best


def main() -> int:
    failed = False
    print(f"Casing color: {CASING}")
    print(f"Ramp domain sampled at {N_SAMPLES} points (matches speedToColor's own lerp)")
    print(f"Thresholds: NORMAL_FLOOR={NORMAL_FLOOR}  CVD_TARGET={CVD_TARGET}  CVD_FLOOR={CVD_FLOOR}")
    print()

    for kind, label in [
        (None, "normal vision"),
        ("protan", "protanopia"),
        ("deutan", "deuteranopia"),
        ("tritan", "tritanopia (reported only)"),
    ]:
        d, worst_c = worst_over_ramp(kind)
        if kind is None:
            state = "PASS" if d >= NORMAL_FLOOR else "FAIL"
            failed |= state == "FAIL"
            note = f"(>= NORMAL_FLOOR {NORMAL_FLOOR}) -> {state}"
        elif kind in ("protan", "deutan"):
            state = "PASS" if d >= CVD_TARGET else ("WARN" if d >= CVD_FLOOR else "FAIL")
            failed |= state == "FAIL"
            note = f"(>= CVD_TARGET {CVD_TARGET}) -> {state}"
        else:
            note = ""
        print(f"{label:28} worst ΔE = {d:5.1f}  (closest ramp point: {worst_c})  {note}")

    print()
    for surface, label in [("#fcfcfb", "light reference surface"), ("#1a1a19", "dark reference surface")]:
        cr = contrast(CASING, surface)
        print(f"Contrast vs {label} ({surface}): {cr:.2f}:1  # reported only: the map sits on OSM tiles, not on a flat surface")


    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
