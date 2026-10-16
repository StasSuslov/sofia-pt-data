"""The comparison half of the stdout report: paired, persistence, against."""

from dailyprofile.constants import (
    DAY_WINDOW,
    EVENING_PEAK,
    MIDDAY,
    MORNING_PEAK,
    NIGHT_WINDOW,
)
from dailyprofile.pairing import paired, persistence
from dailyprofile.profile import slot_label
from dailyprofile.textout import (
    comma,
    iqr_text,
    percent,
    period_label,
    say,
    signed,
    window_label,
)

FIRST_HOUR = 7
END_HOUR = 20
HOURS = range(FIRST_HOUR, END_HOUR)
COMPARISONS = (
    (
        f'morning peak {MORNING_PEAK} vs midday {MIDDAY}',
        MORNING_PEAK,
        MIDDAY,
    ),
    (
        f'evening peak {EVENING_PEAK} vs midday {MIDDAY}',
        EVENING_PEAK,
        MIDDAY,
    ),
    (
        f'evening peak {EVENING_PEAK} vs morning peak {MORNING_PEAK}',
        EVENING_PEAK,
        MORNING_PEAK,
    ),
    (
        f'night {window_label(NIGHT_WINDOW)} vs day {window_label(DAY_WINDOW)}',
        NIGHT_WINDOW,
        DAY_WINDOW,
    ),
)
AGAINST_SPECS = (
    *(slot_label(hour, 0) for hour in HOURS),
    EVENING_PEAK,
    DAY_WINDOW,
    NIGHT_WINDOW,
)
FAR_CUT = 20.0
PERSISTENCE_CUTS = (10.0, 15.0, FAR_CUT)
LABEL_WIDTH = 12


def _paired_block(label: str, outcome: dict) -> str:
    segments = comma(outcome['n'])
    excluded = comma(outcome['zeroes'])
    median = signed(outcome['median_diff'])
    slower = percent(outcome['share_a_slower'])
    iqr = iqr_text(outcome)
    return '\n'.join(
        (
            f'  {label}',
            (
                f'    {segments} segments carry both '
                f'({excluded} excluded for a median of exactly 0)'
            ),
            f'    median difference {median} km/h, {iqr}',
            f'    first side slower in {slower} of them',
        ),
    )


def report_paired(by_segment: dict) -> None:
    """The four same-segment comparisons the report quotes."""
    say('\npaired same-segment comparisons')
    for label, first, second in COMPARISONS:
        say(_paired_block(label, paired(by_segment, first, second)))


def _persistence_line(cut: float, outcome: dict) -> str:
    cut_text = format(cut, 'g')
    slow = comma(outcome['slow_at_a'])
    population = comma(outcome['population'])
    still = comma(outcome['still_slow'])
    share = percent(outcome['share'])
    return (
        f'  under {cut_text} km/h at {MORNING_PEAK}: {slow} of '
        f'{population} segments, and {still} of them '
        f'({share}) are still under {cut_text} km/h at {MIDDAY}'
    )


def report_persistence(by_segment: dict) -> None:
    """Of the segments slow at the morning peak, how many stay slow."""
    say('\nis slow at peak the same street as slow at midday?')
    for cut in PERSISTENCE_CUTS:
        outcome = persistence(by_segment, cut, MORNING_PEAK, MIDDAY)
        say(_persistence_line(cut, outcome))


def _against_line(label: str, outcome: dict) -> str:
    cells = [label.rjust(LABEL_WIDTH)]
    if outcome['n']:
        cells.extend(
            (
                f'n={format(outcome["n"], "6,")}',
                f'zero={format(outcome["zeroes"], "4,")}',
                f'median {signed(outcome["median_diff"])} km/h',
                iqr_text(outcome),
                f'slower now in {percent(outcome["share_a_slower"])}',
            ),
        )
    else:
        cells.append('no segment carries both')
    return '  '.join(('', *cells))


def _against_header(by_segment: dict, older: dict, old_by_segment: dict):
    common = by_segment.keys() & old_by_segment.keys()
    older_label = period_label(older)
    say(
        f'\nagainst period {older_label}, '
        f'{comma(len(old_by_segment))} segments, {comma(len(common))} in both',
    )
    say('same segment, same time of day, this period minus that one')


def report_against(by_segment: dict, older: dict, old_by_segment: dict) -> None:
    """Difference this period against an earlier one, per time of day."""
    _against_header(by_segment, older, old_by_segment)
    for spec in AGAINST_SPECS:
        outcome = paired(by_segment, spec, spec, other=old_by_segment)
        label = spec if isinstance(spec, str) else window_label(spec)
        say(_against_line(label, outcome))
