"""Schedule periods and per-day processing, re-exported."""

from speeds.dayfile import process_day
from speeds.models import timeslot_for
from speeds.period_report import build_schedule_periods
from speeds.periods import assign_schedule_periods
from speeds.timetable import (
    load_schedule_calendar,
    schedule_churn,
    schedule_signature,
)
from speeds.typical import build_typical_weekday

# Re-exports: listing them keeps the imports above from reading as unused.
__all__ = (  # noqa: WPS410  # re-exports for segment_speeds
    'assign_schedule_periods',
    'build_schedule_periods',
    'build_typical_weekday',
    'load_schedule_calendar',
    'process_day',
    'schedule_churn',
    'schedule_signature',
    'timeslot_for',
)
