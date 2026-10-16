#!/usr/bin/env python3
"""Daily speed profile of Sofia's surface network and its paired comparisons.

Daily speed profile of Sofia's surface network, plus the paired
same-segment comparisons the report quotes.

Every number in reports/2026-09-09-peak-vs-permanent.en.md and in
reports/2026-09-21-school-week-repeat.en.md comes out of this script. Run it
and you get the figure and the numbers together:

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
not two times of day. Night belongs to night routes: only a fraction of the
segments observed in the day window carry a night median at all, and both
counts print with the profile. A per-segment difference asks the question
the report asks.

No zoomed y axis. The axis starts at zero. Rescaled to the data, the same
line becomes a mountain range, and the reader would take a range of two or
three km/h for a rush hour. The figure's caption names the range of the
period it draws, measured off that line, never a number carried over from
the last report.
"""

import argparse
import sys
from pathlib import Path

from dailyprofile.compare import (
    report_against,
    report_paired,
    report_persistence,
)
from dailyprofile.figure import svg
from dailyprofile.loader import load_period
from dailyprofile.pairing import paired, persistence
from dailyprofile.profile import profile, window_median
from dailyprofile.report import (
    report_core,
    report_header,
    report_populations,
    report_slots,
)

# Names the tests (and anyone importing this script) reach for.
__all__ = ('paired', 'persistence', 'profile', 'window_median')  # noqa: WPS410


def _parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('aggregate', type=Path)
    ap.add_argument('out_svg', type=Path)
    ap.add_argument('--period', default=None)
    ap.add_argument(
        '--against',
        default=None,
        metavar='PERIOD_KEY',
        help='an earlier schedule period to difference against, '
        'segment by segment, at the same time of day',
    )
    return ap.parse_args()


def _report_against(args: argparse.Namespace, by_segment: dict) -> None:
    older, old_by_segment = load_period(args.aggregate, args.against)
    report_against(by_segment, older, old_by_segment)


def main() -> int:
    """Draw the figure and print the numbers; returns the exit code."""
    args = _parse_args()

    period, by_segment = load_period(args.aggregate, args.period)
    prof = profile(by_segment)
    svg(prof, period, args.out_svg)

    report_header(period, by_segment, args.out_svg)
    report_slots(prof)
    report_core(prof)
    report_paired(by_segment)
    report_persistence(by_segment)
    if args.against:
        _report_against(args, by_segment)
    report_populations(by_segment)
    return 0


if __name__ == '__main__':
    sys.exit(main())
