"""Value objects that carry one export run through the package."""

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True, kw_only=True)
class Shared:
    """What every bundle of one run has in common."""

    min_samples: int
    shapes_by_key: dict
    route_info: dict
    shape_ids_by_key: dict
    attribution: dict | None = None
    city_limitations: tuple = ()


@dataclass(frozen=True, kw_only=True)
class Bundle:
    """What distinguishes one bundle: a schedule period, or a single day."""

    mode: str
    source: dict
    days_processed: list
    days_in_median: list
    incomplete_days: dict
    schedule_period: dict | None = None


@dataclass(frozen=True, kw_only=True)
class ManifestInputs:
    """Every number and label build_manifest() needs."""

    mode: str
    min_samples: int
    bins_before: int
    bins_after: int
    pairs_before: int
    pairs_after: int
    missing_shapes: int
    timeslot_labels: list
    source: dict
    days_processed: list
    days_in_median: list
    incomplete_days: dict
    shapes_observed: int
    shapes_written: int
    total_static_shapes: int
    multi_route_shapes: int
    attribution: dict | None = None
    city_limitations: tuple = ()
    schedule_period: dict | None = None


@dataclass(frozen=True, kw_only=True)
class Run:
    """One invocation: where it reads and writes, and what it shares."""

    shared: Shared
    data_dir: Path
    processed_dir: Path
    output_root: Path
    static_feed_names: list


@dataclass
class SegmentColumns:
    """The parallel columns of geometry.json, filled one segment at a time."""

    shape_idx: list = field(default_factory=list)
    segment_index: list = field(default_factory=list)
    lat: list = field(default_factory=list)
    lon: list = field(default_factory=list)
    point_offset: list = field(default_factory=lambda: [0])
    index_of: dict = field(default_factory=dict)

    def add(self, key: tuple, shape_pos: int, polyline: list) -> None:
        """Append the segment `key` = (shape_key, segment_index)."""
        self.index_of[key] = len(self.shape_idx)
        self.lat.extend(point[0] for point in polyline)
        self.lon.extend(point[1] for point in polyline)
        self.point_offset.append(len(self.lat))
        self.shape_idx.append(shape_pos)
        self.segment_index.append(key[1])

    def as_geometry(self) -> dict:
        """The columns in the order geometry.json lists them."""
        return {
            'shape_idx': self.shape_idx,
            'segment_index': self.segment_index,
            'point_offset': self.point_offset,
            'lat': self.lat,
            'lon': self.lon,
        }
