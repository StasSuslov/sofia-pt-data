"""Output files of a collection run: a data file and its heartbeat log."""

import datetime as dt
import json
from collections.abc import Callable
from pathlib import Path
from zoneinfo import ZoneInfo

from collector.console import clock, out
from collector.feed import PollResult


def dated_output_path(
    output_dir: Path,
    tz: ZoneInfo,
    when: dt.datetime | None = None,
) -> Path:
    """Path for the daily-rotated file covering `when`'s local date in `tz`."""
    local_date = (when or dt.datetime.now(tz)).astimezone(tz).date()
    return output_dir / f'{local_date.isoformat()}.jsonl'


def heartbeat_path_for(data_path: Path) -> Path:
    """Path for the companion per-poll heartbeat log next to a data file.

    A poll that fetches successfully but finds zero vehicles in the bbox, or
    one whose HTTP/protobuf fetch fails outright, writes no rows to the data
    file — from the vehicle data alone those two cases and genuine collector
    downtime are indistinguishable. The heartbeat log records every poll
    attempt (outcome and vehicle count) so completeness can be verified
    independently of the collector's own run-summary counters.
    """
    return data_path.with_name(f'{data_path.stem}.polls.jsonl')


def _append(path: Path):
    return path.open('a', encoding='utf-8')


def _json_line(payload: dict) -> str:
    return f'{json.dumps(payload, ensure_ascii=False)}\n'


class Sink:
    """Data and heartbeat files for the path `path_for` currently names.

    `path_for` is asked again on every rotation check: for a daily-rotated
    run it returns the file for today's local date, for a single-file run it
    always returns the same path.
    """

    def __init__(self, path_for: Callable[[], Path]) -> None:
        """Open the files for whatever `path_for` names right now."""
        self._path_for = path_for
        self.path = path_for()
        self._rows = _append(self.path)
        self._heartbeat = _append(heartbeat_path_for(self.path))

    def close(self) -> None:
        """Close both files."""
        self._rows.close()
        self._heartbeat.close()

    def rotate_if_needed(self) -> None:
        """Switch to the new day's files when the date has rolled over."""
        fresh_path = self._path_for()
        if fresh_path == self.path:
            return
        self.close()
        self.path = fresh_path
        self._rows = _append(self.path)
        self._heartbeat = _append(heartbeat_path_for(self.path))
        out(f'[{clock()}] rotated → {self.path}')

    def write(self, poll: PollResult) -> None:
        """Append the poll's rows, then the heartbeat line that reports them.

        Data written (and durably flushed) before the heartbeat line
        that reports it, so the heartbeat can never claim more rows
        exist than actually landed on disk.
        """
        for record in poll.records:
            self._rows.write(_json_line(record))
        self._rows.flush()
        self._heartbeat.write(
            _json_line(
                {
                    'snapshot_ts': poll.poll_ts,
                    'fetch_ok': poll.fetch_ok,
                    'vehicle_count': len(poll.records),
                    'entities_total': poll.entities_total,
                    'vehicles_with_position': poll.vehicles_with_position,
                    'dropped_out_of_bbox': poll.dropped_out_of_bbox,
                },
            ),
        )
        self._heartbeat.flush()
