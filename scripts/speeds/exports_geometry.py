"""Geometry and projection: names that segment_speeds re-exports."""

from speeds.geometry import (
    Shape,
    build_shape,
    haversine_m,
)
from speeds.projection import (
    project_onto_segment,
    project_point,
    window_bounds,
)

# Re-exports: listing them keeps the imports above from reading as unused.
__all__ = (  # noqa: WPS410  # re-exports for segment_speeds
    'Shape',
    'build_shape',
    'haversine_m',
    'project_onto_segment',
    'project_point',
    'window_bounds',
)
