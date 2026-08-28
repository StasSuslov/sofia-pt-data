from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from collect import dated_output_path, heartbeat_path_for, is_valid_sofia_coordinate

SOFIA_TZ = ZoneInfo("Europe/Sofia")


def test_valid_sofia_coordinate_inside_bbox():
    assert is_valid_sofia_coordinate(42.6977, 23.3219)  # central Sofia


def test_invalid_coordinate_outside_bbox():
    assert not is_valid_sofia_coordinate(43.2141, 27.9147)  # Varna — known teleportation case


def test_invalid_coordinate_just_outside_each_edge():
    assert not is_valid_sofia_coordinate(42.56, 23.30)   # below lat_min
    assert not is_valid_sofia_coordinate(42.81, 23.30)   # above lat_max
    assert not is_valid_sofia_coordinate(42.65, 23.14)   # below lon_min
    assert not is_valid_sofia_coordinate(42.65, 23.56)   # above lon_max


def test_dated_output_path_same_day():
    out_dir = Path("data/sofia")
    when = datetime(2026, 8, 27, 10, 0, tzinfo=SOFIA_TZ)
    assert dated_output_path(out_dir, SOFIA_TZ, when) == out_dir / "2026-08-27.jsonl"


def test_dated_output_path_rotates_at_local_midnight():
    out_dir = Path("data/sofia")
    before_midnight = datetime(2026, 8, 27, 23, 59, tzinfo=SOFIA_TZ)
    after_midnight = datetime(2026, 8, 28, 0, 1, tzinfo=SOFIA_TZ)
    assert dated_output_path(out_dir, SOFIA_TZ, before_midnight) == out_dir / "2026-08-27.jsonl"
    assert dated_output_path(out_dir, SOFIA_TZ, after_midnight) == out_dir / "2026-08-28.jsonl"


def test_dated_output_path_uses_local_not_utc_date():
    # 23:30 UTC on Aug 27 is already Aug 28 in Europe/Sofia (UTC+2 in winter, +3 in summer)
    out_dir = Path("data/sofia")
    utc_late = datetime(2026, 8, 27, 23, 30, tzinfo=ZoneInfo("UTC"))
    assert dated_output_path(out_dir, SOFIA_TZ, utc_late) == out_dir / "2026-08-28.jsonl"


def test_heartbeat_path_sits_next_to_data_file():
    data_path = Path("data/sofia/2026-08-27.jsonl")
    assert heartbeat_path_for(data_path) == Path("data/sofia/2026-08-27.polls.jsonl")
