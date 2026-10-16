"""Static feed loading and snapshot selection, re-exported."""

from speeds.feed import build_shape_key, load_static
from speeds.feedmeta import (
    count_multi_geometry_shape_ids,
    count_multi_id_geometries,
)
from speeds.snapshots import (
    find_static_snapshots,
    next_snapshot_after,
    pick_snapshot_for_day,
)

# Re-exports: listing them keeps the imports above from reading as unused.
__all__ = (  # noqa: WPS410  # re-exports for segment_speeds
    'build_shape_key',
    'count_multi_geometry_shape_ids',
    'count_multi_id_geometries',
    'find_static_snapshots',
    'load_static',
    'next_snapshot_after',
    'pick_snapshot_for_day',
)
