"""Fixture tests for the paired comparisons behind the published reports.

The paired comparisons in reports/2026-09-09-peak-vs-permanent.en.md come
out of these three functions, so they get a fixture with a known answer.
"""

from types import MappingProxyType

from plot_daily_profile import paired, persistence, profile, window_median

STOPPED = float(0)
HALF = 0.5
WHOLE = 1.0
MINUS_ONE = -1.0
CUT_SLOW = 15.0
CUT_STRICT = 18.0
MIDPOINT = 15.0
IQR_EDGE = 4.0

# Three segments. A is slower at peak, B is faster, C never appears at 12:00
# and must drop out of any comparison against midday. D reads exactly 0 at
# midday and must drop out too, without taking the rest of the row with it.
BY_SEGMENT = MappingProxyType({
    ('a', '1'): {'08:00': 10.0, '12:00': 14.0, '00:00': 30.0},
    ('b', '1'): {'08:00': 20.0, '12:00': 16.0},
    ('c', '1'): {'08:00': 12.0},
    ('d', '1'): {'08:00': 8.0, '12:00': STOPPED},
})


def test_paired_needs_both_sides():
    """Only segments present on both sides enter the comparison."""
    stats = paired(BY_SEGMENT, '08:00', '12:00')
    assert stats['n'] == 2  # c has no midday value, d reads zero
    assert stats['iqr'] == (-IQR_EDGE, IQR_EDGE)
    assert stats['zeroes'] == 1
    assert stats['median_diff'] == STOPPED  # median of -4.0 and +4.0
    assert stats['share_a_slower'] == HALF


def test_paired_nothing_without_both():
    """No segment carrying both slots yields an empty comparison."""
    assert paired(BY_SEGMENT, '08:00', '23:45')['n'] == 0


def test_window_median_uses_present_slots():
    """The window median takes only the slots a segment has."""
    speeds = {'07:00': 10.0, '12:00': 20.0, '23:00': 99.0}
    assert window_median(speeds, ('07:00', '18:45')) == MIDPOINT
    assert window_median(speeds, ('01:00', '03:00')) is None


def test_persistence_counts_first_slow():
    """Persistence counts only segments slow at the first time."""
    stats = persistence(BY_SEGMENT, CUT_SLOW, '08:00', '12:00')
    assert stats['population'] == 2  # a and b; c has no midday, d reads zero
    assert stats['slow_at_a'] == 1  # a at 10
    assert stats['still_slow'] == 1  # a is at 14 four hours later
    assert stats['share'] == WHOLE
    # b runs at 20 at 08:00 and 16 at midday. It is under an 18 km/h cut at
    # midday and never enters the count, because the question runs one
    # direction only: slow at peak first, still slow later second.
    strict = persistence(BY_SEGMENT, CUT_STRICT, '08:00', '12:00')
    assert strict['slow_at_a'] == 1


def test_profile_reports_segment_counts():
    """Each profile slot carries the segment count behind its median."""
    prof = profile(BY_SEGMENT)
    assert prof['08:00'] == (11.0, 4)  # median of 8, 10, 12, 20
    assert prof['00:00'] == (30.0, 1)


# The same three segments a schedule period later. A slowed by 2 km/h, B is
# unchanged, C is new to this period and E has gone: neither has a counterpart
# to be compared against.
LATER = MappingProxyType({
    ('a', '1'): {'08:00': 8.0, '12:00': 14.0},
    ('b', '1'): {'08:00': 20.0, '12:00': 16.0},
    ('e', '1'): {'08:00': 5.0, '12:00': 5.0},
})


def test_paired_across_periods_own_sides():
    """Across periods, each side comes from its own period."""
    stats = paired(LATER, '08:00', '08:00', other=BY_SEGMENT)
    assert stats['n'] == 2  # c and d are in one period only, e in neither
    assert stats['median_diff'] == MINUS_ONE  # median of -2.0 and 0.0
    assert stats['share_a_slower'] == HALF
