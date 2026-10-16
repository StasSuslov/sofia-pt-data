"""Heartbeat (polls.jsonl) fields of a day's manifest."""

from pathlib import Path

from config import date_from_path, resolve_day_file
from manifests.hashing import load_jsonl, sha256_of

# Heartbeat starting this many seconds after the data file's first record
# counts as deployed mid-day.
HEARTBEAT_PARTIAL_SEC = 120

POLL_FIELDS = (
    'polls_file',
    'polls_sha256',
    'polls_malformed_lines',
    'polls_logged',
    'successful_polls',
    'empty_polls',
    'fetch_error_polls',
    'entities_total',
    'vehicles_with_position',
    'dropped_out_of_bbox',
    'dropped_out_of_bbox_pct',
)


def polls_path_of(data_path: Path) -> Path:
    """Return the heartbeat file next to a day file (it may not exist).

    Args:
        data_path: The day file, plain or gzipped.

    Returns:
        The plain or gzipped heartbeat path, whichever exists.
    """
    return resolve_day_file(
        data_path.parent,
        date_from_path(data_path),
        '.polls.jsonl',
    )


def _sum_field(polls: list[dict], name: str) -> int:
    # .get() default: polls logged before these fields existed don't have them.
    return sum(poll.get(name, 0) for poll in polls)


def _bbox_fields(polls: list[dict]) -> dict:
    vehicles_with_position = _sum_field(polls, 'vehicles_with_position')
    dropped_out_of_bbox = _sum_field(polls, 'dropped_out_of_bbox')
    return {
        'entities_total': _sum_field(polls, 'entities_total'),
        'vehicles_with_position': vehicles_with_position,
        'dropped_out_of_bbox': dropped_out_of_bbox,
        # rate, not just a bare count, so a reader doesn't have to divide
        # two numbers from different rows to tell if this is significant
        'dropped_out_of_bbox_pct': (
            round(100 * dropped_out_of_bbox / vehicles_with_position, 3)
            if vehicles_with_position
            else None
        ),
    }


def _poll_fields(polls_path: Path, date_str: str) -> tuple[dict, list[int]]:
    polls, polls_malformed_lines = load_jsonl(polls_path)
    ok_polls = [poll for poll in polls if poll['fetch_ok']]
    polls_sha256, _ = sha256_of(
        polls_path,
    )  # size not tracked for the heartbeat file
    fields = {
        # uncompressed name, same reasoning as data_file
        'polls_file': f'{date_str}.polls.jsonl',
        'polls_sha256': polls_sha256,
        'polls_malformed_lines': polls_malformed_lines,
        'polls_logged': len(polls),
        'successful_polls': len(ok_polls),
        'empty_polls': len([
            poll for poll in ok_polls if poll['vehicle_count'] == 0
        ]),
        'fetch_error_polls': len(polls) - len(ok_polls),
        **_bbox_fields(polls),
    }
    return fields, sorted(poll['snapshot_ts'] for poll in polls)


def _pre_heartbeat_note(heartbeat_start: int, date_str: str) -> str:
    return (
        f'Heartbeat logging only starts at snapshot_ts {heartbeat_start} '
        # The uncompressed name, like polls_file above: whether the VPS
        # has gzipped this day yet is a storage detail, and letting it
        # into the note made the same day's manifest read differently
        # before and after compression for no change in the data.
        f'({date_str}.polls.jsonl was deployed partway through the day). '
        'Coverage before that point is inferred from the data file alone and '
        'cannot distinguish a genuinely empty poll, a fetch error, or real '
        'downtime.'
    )


def _reconcile_start(
    heartbeat_timestamps: list[int],
    data_timestamps: list[int],
    date_str: str,
) -> tuple[dict, list[int]]:
    # collect.py may be deployed/restarted mid-day, so the heartbeat log
    # can start well after the data file's first record. Treating the
    # heartbeat's own (short) span as "the day" would silently claim
    # 100% coverage while ignoring the un-instrumented hours before it
    # existed — exactly the false confidence this manifest exists to
    # avoid. Detect that gap and fall back to data-derived timestamps
    # (with an explicit caveat) for the period heartbeat didn't cover.
    heartbeat_start = heartbeat_timestamps[0]
    data_start = data_timestamps[0] if data_timestamps else heartbeat_start
    if (heartbeat_start - data_start) <= HEARTBEAT_PARTIAL_SEC:
        return {'heartbeat_partial': False}, heartbeat_timestamps
    return {
        'heartbeat_partial': True,
        'pre_heartbeat_note': _pre_heartbeat_note(heartbeat_start, date_str),
    }, sorted(
        {stamp for stamp in data_timestamps if stamp < heartbeat_start}
        | set(heartbeat_timestamps),
    )


def poll_section(
    data_path: Path,
    data_timestamps: list[int],
) -> tuple[dict, list[int]]:
    """Return the polls_* manifest fields and the timestamps to measure.

    Args:
        data_path: The day file, plain or gzipped.
        data_timestamps: Sorted snapshot_ts values found in the day file.

    Returns:
        The heartbeat fields (None-filled without a heartbeat log) and the
        poll timestamps coverage is computed from.
    """
    polls_path = polls_path_of(data_path)
    if not polls_path.exists():
        # No ground truth for attempted-but-empty/failed polls — the only
        # signal left is which timestamps made it into the data file at all.
        return dict.fromkeys(POLL_FIELDS), data_timestamps
    fields, heartbeat_timestamps = _poll_fields(
        polls_path,
        date_from_path(data_path),
    )
    extra, timestamps = _reconcile_start(
        heartbeat_timestamps,
        data_timestamps,
        date_from_path(data_path),
    )
    return {**fields, **extra}, timestamps
