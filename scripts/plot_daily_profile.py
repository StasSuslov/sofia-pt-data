#!/usr/bin/env python3
"""Daily speed profile of Sofia's surface network, plus the paired
same-segment comparisons the report quotes.

Every number in reports/2026-09-09-peak-vs-permanent.en.md comes out of this
script. Run it and you get the figure and the numbers together:

    python3 scripts/plot_daily_profile.py \
        data/sofia/processed/typical_weekday.json \
        reports/figures/2026-09-09-daily-profile.svg

Why this reads the unrounded aggregate and not the published web export: the
export rounds speeds to whole km/h, and the finding here is that the profile
varies by less than 2 km/h across the day. Rounded data cannot tell a flat
line from a line flattened by rounding. The aggregate keeps median_speed_ms
as it was computed, so the flatness is checkable rather than assumed.

Two things this script refuses to do, both from the same rule: a number must
be measured on the object it describes.

Paired comparisons only. Comparing the median over everything seen at 08:00
against the median over everything seen at 03:00 compares two populations,
not two times of day. Night belongs to night routes: of the 20,111 segments
observed in the day window, 1,230 also carry a night median. A per-segment
difference asks the question the report asks.

No zoomed y axis. The axis starts at zero. Rescaled to the data, the same
line becomes a mountain range, and the reader would take a range of 1.8 km/h
for a rush hour.
"""

import argparse
import json
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from export_web import current_period_key  # same rule the map opens with

MIN_SAMPLES = 2  # matches export_web.MIN_SAMPLES_DEFAULT
MS_TO_KMH = 3.6

# Windows are named where the report names them. The peak windows are the ones
# a Sofia timetable and a Sofia commuter would both call peak; the point of the
# figure is what the data does inside them, so they are marked, not derived.
MORNING_PEAK = "08:00"
MIDDAY = "12:00"
EVENING_PEAK = "17:15"     # the slowest slot of the whole day, see profile
NIGHT_WINDOW = ("00:00", "03:45")
DAY_WINDOW = ("07:00", "18:45")
CORE_WINDOW = ("07:00", "19:00")
PEAK_BANDS = [("07:30", "09:00"), ("16:30", "18:30")]

# A slot observed on this many segments or more gets a solid line. Below it the
# line goes thin and grey. Between 03:00 and 08:00 the population of the
# network changes by a factor of fifty, and a line of constant weight would
# offer the night as if it were the same measurement as the morning. The cut is
# a drawing choice, not a finding; the counts themselves print to stdout.
SOLID_MIN_SEGMENTS = 1000


def slots() -> list:
    return [f"{h:02d}:{m:02d}" for h in range(24) for m in (0, 15, 30, 45)]


def load_period(path: Path, period_key: str | None) -> tuple:
    doc = json.loads(path.read_text(encoding="utf-8"))
    key = period_key or current_period_key(doc["schedule_periods"])
    if key is None:
        raise SystemExit("no schedule period holds a weekday median")
    period = next(p for p in doc["schedule_periods"] if p["period_key"] == key)
    # {(shape_key, segment_index): {slot: kmh}} over bins that clear the sample
    # threshold, so the figure rests on the same bins the map publishes.
    by_segment = defaultdict(dict)
    for bin_key, value in doc["segments"][key].items():
        if value["n_samples"] < MIN_SAMPLES:
            continue
        shape_key, seg_idx, slot = bin_key.rsplit("|", 2)
        by_segment[(shape_key, seg_idx)][slot] = value["median_speed_ms"] * MS_TO_KMH
    return period, dict(by_segment)


def profile(by_segment: dict) -> dict:
    """slot -> (median km/h over segments observed in that slot, segment count).

    The count moves with the slot by a factor of sixteen between 03:00 and
    08:00, which is why the line alone is not the whole story and the report
    prints the counts next to it.
    """
    per_slot = defaultdict(list)
    for speeds in by_segment.values():
        for slot, kmh in speeds.items():
            per_slot[slot].append(kmh)
    return {s: (statistics.median(v), len(v)) for s, v in per_slot.items()}


def window_median(speeds: dict, window: tuple) -> float | None:
    """A segment's median over the slots it actually has inside the window."""
    lo, hi = window
    inside = [kmh for slot, kmh in speeds.items() if lo <= slot <= hi]
    return statistics.median(inside) if inside else None


def pairs(by_segment: dict, a, b) -> tuple:
    """[(value at a, value at b)] over segments carrying both, plus the count
    dropped for reading exactly 0.

    a and b are either a slot ("08:00") or a window ("07:00", "18:45").

    Both tables in the report draw their population from here, so they cover
    the same segments and a reader comparing them compares like with like.

    A median of exactly 0 km/h over five weekdays says the segment produced no
    movement at all in that slot, which describes a vehicle sitting at a
    terminus or a position that never updated, not the speed of a street. They
    are dropped and counted, never dropped in silence.
    """
    def value(speeds, spec):
        return speeds.get(spec) if isinstance(spec, str) else window_median(speeds, spec)

    kept, zeroes = [], 0
    for speeds in by_segment.values():
        va, vb = value(speeds, a), value(speeds, b)
        if va is None or vb is None:
            continue
        if va == 0 or vb == 0:
            zeroes += 1
            continue
        kept.append((va, vb))
    return kept, zeroes


def paired(by_segment: dict, a, b) -> dict:
    """Per-segment difference a minus b, over segments carrying both."""
    kept, zeroes = pairs(by_segment, a, b)
    if not kept:
        return {"n": 0, "zeroes": zeroes}
    diffs = sorted(va - vb for va, vb in kept)
    return {
        "n": len(diffs),
        "zeroes": zeroes,
        "median_diff": statistics.median(diffs),
        "share_a_slower": sum(1 for d in diffs if d < 0) / len(diffs),
        "iqr": (diffs[len(diffs) // 4], diffs[3 * len(diffs) // 4]),
    }


def persistence(by_segment: dict, cut_kmh: float, a: str, b: str) -> dict:
    """Of the segments under cut_kmh at a, how many are still under it at b.

    This is the report's second claim without the ratio threshold the first
    draft leaned on. A ratio of peak to midday needs a cutoff nothing in the
    data picks: the distribution of that ratio runs smooth from p10 0.73 to
    p90 1.17, so any line through it is mine, not the city's. A plain speed
    cut is a stated choice a reader can move, and the answer barely moves
    with it.
    """
    kept, zeroes = pairs(by_segment, a, b)
    slow_at_a = [(va, vb) for va, vb in kept if va < cut_kmh]
    still = sum(1 for _, vb in slow_at_a if vb < cut_kmh)
    return {"population": len(kept), "zeroes": zeroes, "slow_at_a": len(slow_at_a),
            "still_slow": still,
            "share": still / len(slow_at_a) if slow_at_a else 0.0}


def svg(prof: dict, period: dict, out: Path) -> None:
    all_slots = slots()
    w, h = 820, 420
    left, right, top, bottom = 60, 24, 56, 84
    pw, ph = w - left - right, h - top - bottom
    y_max = 30.0

    def x(i):  return left + pw * i / (len(all_slots) - 1)
    def y(kmh): return top + ph * (1 - kmh / y_max)

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" '
        f'width="{w}" height="{h}" font-family="Helvetica,Arial,sans-serif">',
        f'<rect width="{w}" height="{h}" fill="#ffffff"/>',
        f'<text x="{left}" y="24" font-size="15" font-weight="bold" fill="#111">'
        'Median speed by time of day, Sofia surface network</text>',
        f'<text x="{left}" y="42" font-size="12" fill="#555">'
        f'Typical weekday, {period["first_date"]} to {period["last_date"]} '
        f'({len(period["days_in_median_mon_fri"])} weekdays). Shaded: the hours '
        'a timetable calls peak.</text>',
        f'<text x="{left}" y="{h - 38}" font-size="11" fill="#555">'
        f'Thin grey: fewer than {SOLID_MIN_SEGMENTS:,} segments carry a median in '
        'that slot. Night runs on night routes, so it is a different network, '
        'not a faster one.</text>',
    ]
    for lo, hi in PEAK_BANDS:
        x0, x1 = x(all_slots.index(lo)), x(all_slots.index(hi))
        parts.append(f'<rect x="{x0:.1f}" y="{top}" width="{x1 - x0:.1f}" '
                     f'height="{ph}" fill="#ffd9c7"/>')
    for kmh in range(0, int(y_max) + 1, 5):
        yy = y(kmh)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{left + pw}" y2="{yy:.1f}" '
                     f'stroke="#dcdcdc" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{yy + 4:.1f}" font-size="11" '
                     f'text-anchor="end" fill="#555">{kmh}</text>')
    for i, slot in enumerate(all_slots):
        if slot.endswith(":00") and int(slot[:2]) % 3 == 0:
            parts.append(f'<text x="{x(i):.1f}" y="{top + ph + 18:.1f}" font-size="11" '
                         f'text-anchor="middle" fill="#555">{slot}</text>')
    # Weight belongs to the edge, not to the point. A piece of line joining a
    # well-observed slot to a thin one rests on the thin one, so it is drawn
    # thin. Weighting by the point drew the crossing edge solid going into the
    # night and dashed coming back out, which is one edge weighted two ways.
    pts = [(x(i), y(prof[s][0]), prof[s][1] >= SOLID_MIN_SEGMENTS)
           for i, s in enumerate(all_slots) if s in prof]
    runs = []
    for (x0, y0, s0), (x1, y1, s1) in zip(pts, pts[1:]):
        solid = s0 and s1
        if runs and runs[-1][0] == solid:
            runs[-1][1].append((x1, y1))
        else:
            runs.append((solid, [(x0, y0), (x1, y1)]))
    for solid, points in runs:
        style = ('stroke="#1a4f8a" stroke-width="2.5"' if solid
                 else 'stroke="#9aa5b1" stroke-width="1.2" stroke-dasharray="4 3"')
        coords = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        parts.append(f'<polyline points="{coords}" fill="none" {style}/>')
    parts.append(f'<text x="{left - 46}" y="{top - 10}" font-size="11" fill="#555">km/h</text>')
    parts.append(f'<text x="{left}" y="{h - 20}" font-size="11" fill="#555">'
                 'The y axis starts at zero on purpose: rescaled to the data, a '
                 'range of 1.8 km/h would look like a rush hour.</text>')
    parts.append("</svg>")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(parts) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("aggregate", type=Path)
    ap.add_argument("out_svg", type=Path)
    ap.add_argument("--period", default=None)
    args = ap.parse_args()

    period, by_segment = load_period(args.aggregate, args.period)
    prof = profile(by_segment)
    svg(prof, period, args.out_svg)

    print(f"period {period['period_key']} "
          f"{period['first_date']}..{period['last_date']} "
          f"({len(period['days_in_median_mon_fri'])} weekdays), "
          f"{len(by_segment):,} segments with at least one bin at n>={MIN_SAMPLES}")
    print(f"\nfigure: {args.out_svg}")

    print("\nslot   km/h  segments")
    for s in slots():
        if s in prof:
            print(f"{s}  {prof[s][0]:5.2f}  {prof[s][1]:8,}")

    core = [(s, prof[s][0]) for s in slots()
            if s in prof and CORE_WINDOW[0] <= s <= CORE_WINDOW[1]]
    lo = min(core, key=lambda kv: kv[1])
    hi = max(core, key=lambda kv: kv[1])
    print(f"\ncore {CORE_WINDOW[0]}-{CORE_WINDOW[1]}: "
          f"{lo[1]:.2f} km/h at {lo[0]} to {hi[1]:.2f} km/h at {hi[0]}, "
          f"range {hi[1] - lo[1]:.2f}")

    comparisons = [
        (f"morning peak {MORNING_PEAK} vs midday {MIDDAY}", MORNING_PEAK, MIDDAY),
        (f"evening peak {EVENING_PEAK} vs midday {MIDDAY}", EVENING_PEAK, MIDDAY),
        (f"evening peak {EVENING_PEAK} vs morning peak {MORNING_PEAK}", EVENING_PEAK, MORNING_PEAK),
        (f"night {NIGHT_WINDOW[0]}-{NIGHT_WINDOW[1]} vs day "
         f"{DAY_WINDOW[0]}-{DAY_WINDOW[1]}", NIGHT_WINDOW, DAY_WINDOW),
    ]
    print("\npaired same-segment comparisons")
    for label, a, b in comparisons:
        r = paired(by_segment, a, b)
        print(f"  {label}")
        print(f"    {r['n']:,} segments carry both "
              f"({r['zeroes']:,} excluded for a median of exactly 0)")
        print(f"    median difference {r['median_diff']:+.2f} km/h, "
              f"IQR {r['iqr'][0]:+.2f} to {r['iqr'][1]:+.2f}")
        print(f"    first side slower in {r['share_a_slower']:.1%} of them")

    print("\nis slow at peak the same street as slow at midday?")
    for cut in (10.0, 15.0, 20.0):
        r = persistence(by_segment, cut, MORNING_PEAK, MIDDAY)
        print(f"  under {cut:g} km/h at {MORNING_PEAK}: {r['slow_at_a']:,} of "
              f"{r['population']:,} segments, and {r['still_slow']:,} of them "
              f"({r['share']:.1%}) are still under {cut:g} km/h at {MIDDAY}")

    day_pop = sum(1 for sp in by_segment.values()
                  if window_median(sp, DAY_WINDOW) is not None)
    night_pop = sum(1 for sp in by_segment.values()
                    if window_median(sp, NIGHT_WINDOW) is not None)
    print(f"\npopulations: {day_pop:,} segments observed in the day window, "
          f"{night_pop:,} at night")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
