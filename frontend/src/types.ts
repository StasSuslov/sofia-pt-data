// Shapes of data/sofia/web/**. See CLAUDE.md section on the data contract —
// this file must not add fields the pipeline doesn't emit.

export interface PeriodInfo {
  period_key: string;
  path: string;
  first_date: string;
  last_date: string;
  days_in_median: string[];
  route_count: number;
  trip_count: number;
  segments_retained: number;
  timeslot_count: number;
}

export interface ExcludedDay {
  date: string;
  reason: string;
}

export interface Manifest {
  current_period: string;
  period_count: number;
  periods: PeriodInfo[];
  days_excluded_from_median: ExcludedDay[];
}

export interface Geometry {
  segment_length_m: number;
  shape_keys: string[];
  shape_ids: string[][];
  shape_route_ids: string[][];
  shape_route_names: string[][];
  shape_route_type: number[];
  shape_idx: number[];
  segment_index: number[];
  // CSR layout (format 2): segment i owns points [point_offset[i],
  // point_offset[i + 1]) of the flat lat/lon arrays. Two points is a straight
  // bin; more means the shape turns inside those 200 m.
  point_offset: number[];
  lat: number[];
  lon: number[];
}

export interface Timeslot {
  timeslot: string;
  segment_idx: number[];
  speed_kmh: number[];
  n_samples: number[];
}

/** One segment ready to draw: its polyline plus its median for the slot. */
export interface RenderSegment {
  segmentIdx: number;
  points: [number, number][];
  speedKmh: number;
  nSamples: number;
}
