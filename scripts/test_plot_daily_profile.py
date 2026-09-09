"""The paired comparisons in reports/2026-09-09-peak-vs-permanent.en.md come
out of these three functions, so they get a fixture with a known answer."""

from plot_daily_profile import paired, persistence, profile, window_median

# Three segments. A is slower at peak, B is faster, C never appears at 12:00
# and must drop out of any comparison against midday. D reads exactly 0 at
# midday and must drop out too, without taking the rest of the row with it.
BY_SEGMENT = {
    ("a", "1"): {"08:00": 10.0, "12:00": 14.0, "00:00": 30.0},
    ("b", "1"): {"08:00": 20.0, "12:00": 16.0},
    ("c", "1"): {"08:00": 12.0},
    ("d", "1"): {"08:00": 8.0, "12:00": 0.0},
}


def test_paired_uses_only_segments_present_on_both_sides():
    r = paired(BY_SEGMENT, "08:00", "12:00")
    assert r["n"] == 2            # c has no midday value, d reads zero there
    assert r["iqr"] == (-4.0, 4.0)
    assert r["zeroes"] == 1
    assert r["median_diff"] == 0.0            # median of -4.0 and +4.0
    assert r["share_a_slower"] == 0.5


def test_paired_reports_nothing_when_no_segment_carries_both():
    assert paired(BY_SEGMENT, "08:00", "23:45")["n"] == 0


def test_window_median_takes_the_slots_a_segment_actually_has():
    speeds = {"07:00": 10.0, "12:00": 20.0, "23:00": 99.0}
    assert window_median(speeds, ("07:00", "18:45")) == 15.0
    assert window_median(speeds, ("01:00", "03:00")) is None


def test_persistence_counts_only_segments_slow_at_the_first_time():
    r = persistence(BY_SEGMENT, 15.0, "08:00", "12:00")
    assert r["population"] == 2   # a and b; c has no midday value, d reads zero
    assert r["slow_at_a"] == 1    # a at 10
    assert r["still_slow"] == 1   # a is at 14 four hours later
    assert r["share"] == 1.0
    # b runs at 20 at 08:00 and 16 at midday. It is under an 18 km/h cut at
    # midday and never enters the count, because the question runs one
    # direction only: slow at peak first, still slow later second.
    assert persistence(BY_SEGMENT, 18.0, "08:00", "12:00")["slow_at_a"] == 1


def test_profile_reports_the_segment_count_behind_each_slot():
    prof = profile(BY_SEGMENT)
    assert prof["08:00"] == (11.0, 4)   # median of 8, 10, 12, 20
    assert prof["00:00"] == (30.0, 1)
