"""The profile half of the stdout report: header, slot table, core range."""

from operator import itemgetter
from pathlib import Path

from dailyprofile.constants import (
    CORE_WINDOW,
    DAY_WINDOW,
    MIN_SAMPLES,
    NIGHT_WINDOW,
)
from dailyprofile.profile import core_points, observed_in_window, slots
from dailyprofile.textout import comma, period_label, say, window_label


def report_header(period: dict, by_segment: dict, out_svg: Path) -> None:
    """Period line and the figure path."""
    label = period_label(period)
    segments = comma(len(by_segment))
    say(
        f'period {label}, {segments} segments '
        f'with at least one bin at n>={MIN_SAMPLES}',
    )
    say(f'\nfigure: {out_svg}')


def report_slots(prof: dict) -> None:
    """Median km/h and segment count of every observed slot."""
    say('\nslot   km/h  segments')
    observed = [slot for slot in slots() if slot in prof]
    for slot in observed:
        kmh, count = prof[slot]
        cells = (slot, format(kmh, '5.2f'), format(count, '8,'))
        say('  '.join(cells))


def _kmh_at(point: tuple) -> str:
    slot, kmh = point
    return f'{kmh:.2f} km/h at {slot}'


def report_core(prof: dict) -> None:
    """Slowest and fastest slot inside the core window."""
    core = core_points(prof)
    lowest = min(core, key=itemgetter(1))
    highest = max(core, key=itemgetter(1))
    spread = format(highest[1] - lowest[1], '.2f')
    say(
        f'\ncore {window_label(CORE_WINDOW)}: '
        f'{_kmh_at(lowest)} to {_kmh_at(highest)}, range {spread}',
    )


def report_populations(by_segment: dict) -> None:
    """How many segments carry a day-window and a night-window median."""
    day_pop = comma(observed_in_window(by_segment, DAY_WINDOW))
    night_pop = comma(observed_in_window(by_segment, NIGHT_WINDOW))
    say(
        f'\npopulations: {day_pop} segments observed in the day window, '
        f'{night_pop} at night',
    )
