// Shapes of data/sofia/web/**. See CLAUDE.md section on the data contract —
// this file must not add fields the pipeline doesn't emit.

/** web/index.json — the root listing every bundle the export wrote. */
export interface RootIndex {
  format_version: number;
  generated_at: string;
  days: { date: string; path: string }[];
  typical_weekday: { path: string; current_period: string };
}

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

/** web/typical_weekday/manifest.json — the index over schedule periods. */
export interface Manifest {
  current_period: string;
  period_count: number;
  periods: PeriodInfo[];
  days_excluded_from_median: ExcludedDay[];
}

/**
 * A bundle's own manifest: web/<date>/manifest.json or
 * web/typical_weekday/<period_key>/manifest.json. `mode` is "typical_weekday"
 * or the date, and `timeslots` is the authoritative slot domain for the
 * slider — the count is not a constant of the format.
 */
export interface BundleManifest {
  mode: string;
  segment_count: number;
  timeslot_count: number;
  timeslots: string[];
  days_in_median: string[];
  // date -> the exporter's own reason, for days that were not a complete
  // closed calendar day. Empty object when every day is whole.
  incomplete_days: Record<string, string>;
  // Only the field the panel needs: the bin width the slot labels were cut
  // on, so "N of a full day's slots" is computed, not assumed.
  preprocessing_thresholds: { timeslot_minutes: number };
  known_limitations: string[];
}

export interface Geometry {
  segment_length_m: number;
  shape_keys: string[];
  shape_ids: string[][];
  shape_route_ids: string[][];
  shape_route_names: string[][];
  // null where a shape's routes disagree on GTFS route_type — the exporter
  // writes null rather than picking one (see export_web.py write_manifest).
  shape_route_type: (number | null)[];
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
