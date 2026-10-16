"""Per-run cache of loaded static feeds and schedule calendars."""

import sys
import time
from collections import defaultdict
from pathlib import Path

from speeds.feed import load_static
from speeds.feedmeta import load_feed_info
from speeds.timetable import load_schedule_calendar


class FeedCache:
    """Static feeds and calendars keyed by path, loaded once per run.

    Cache keyed by path rather than reloaded per day. shapes_by_key is
    content-addressed (build_shape_key), so merging several feeds' shapes
    into one dict is safe and self-deduplicating -- two feeds sharing 98.3%
    of their geometry (the observed 2026-08-27/2026-08-31 case) cost barely
    more to hold in memory than one.
    """

    def __init__(self) -> None:
        """Start with nothing loaded."""
        # path -> (trip_map, shapes_by_key, feed_info)
        self.feeds = {}
        # path -> (services_by_date, trip_services)
        self.calendars = {}
        self.shape_ids_by_key = defaultdict(set)

    def load_feed(self, path: Path) -> tuple:
        """Return (trip_map, shapes_by_key, feed_info), loading once."""
        if path not in self.feeds:
            self._fetch(path)
        return self.feeds[path]

    def load_calendar(self, path: Path) -> tuple:
        """Return the feed's calendar tables, loading once."""
        # Cached per feed, not per day: calendar_dates.txt is half a million
        # rows and several days usually share one snapshot.
        if path not in self.calendars:
            self.calendars[path] = load_schedule_calendar(path)
        return self.calendars[path]

    def _fetch(self, path: Path) -> None:
        sys.stdout.write(f'Loading static feed {path} ...\n')
        started = time.time()
        trip_map, shapes_by_key, shape_ids_by_key = load_static(path)
        self.feeds[path] = trip_map, shapes_by_key, load_feed_info(path)
        elapsed = time.time() - started
        sys.stdout.write(
            f'  {len(trip_map):,} trips, {len(shapes_by_key):,} shapes '
            f'({elapsed:.1f}s)\n',
        )
        self._merge_ids(shape_ids_by_key)

    def _merge_ids(self, shape_ids_by_key: dict) -> None:
        for key, shape_ids in shape_ids_by_key.items():
            self.shape_ids_by_key[key].update(shape_ids)
