"""Windows, thresholds and unit factors shared across the daily profile."""

MIN_SAMPLES = 2  # matches export_web.MIN_SAMPLES_DEFAULT
MS_TO_KMH = 3.6

# Windows are named where the report names them. The peak windows are the ones
# a Sofia timetable and a Sofia commuter would both call peak; the point of the
# figure is what the data does inside them, so they are marked, not derived.
MORNING_PEAK = '08:00'
MIDDAY = '12:00'
EVENING_PEAK = '17:15'  # the slowest slot of the whole day, see profile
NIGHT_WINDOW = ('00:00', '03:45')
DAY_WINDOW = ('07:00', '18:45')
CORE_WINDOW = ('07:00', '19:00')
PEAK_BANDS = (('07:30', '09:00'), ('16:30', '18:30'))

# A slot observed on this many segments or more gets a solid line. Below it the
# line goes thin and grey. Between 03:00 and 08:00 the population of the
# network changes by a factor of fifty, and a line of constant weight would
# offer the night as if it were the same measurement as the morning. The cut is
# a drawing choice, not a finding; the counts themselves print to stdout.
SOLID_MIN_SEGMENTS = 1000
