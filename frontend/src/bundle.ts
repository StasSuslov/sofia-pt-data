import type { Geometry, RenderSegment } from "./types.ts";

/** "08:00" -> "0800": timeslot files drop the colon. */
export function timeslotFile(timeslot: string): string {
  return timeslot.replace(":", "");
}

/** Where the slider lands with nothing else to go on: the morning peak. */
export const PREFERRED_TIMESLOT = "08:00";

/**
 * Index of the slot to show in a bundle. The bundle's own `timeslots` list is
 * the domain — a bundle is not required to carry all 96 — so a slot carried
 * over from another bundle is honoured only if this one has it, then the
 * preferred slot, then the first one that exists.
 */
export function pickTimeslotIndex(
  timeslots: string[],
  carriedOver?: string,
): number {
  for (const want of [carriedOver, PREFERRED_TIMESLOT]) {
    if (want === undefined) continue;
    const i = timeslots.indexOf(want);
    if (i >= 0) return i;
  }
  return 0;
}

// GTFS route_type. Sofia's feed uses 0/3/11 today; the rest are named so a
// value the exporter passes through arrives as a word rather than a number.
const ROUTE_TYPE_LABELS: Record<number, string> = {
  0: "Tram",
  1: "Metro",
  2: "Rail",
  3: "Bus",
  4: "Ferry",
  5: "Cable tram",
  6: "Aerial lift",
  7: "Funicular",
  11: "Trolleybus",
  12: "Monorail",
};

/**
 * Stands in for a null shape_route_type so those segments get their own
 * checkbox instead of disappearing from every filter — a segment that leaves
 * the map without a control saying so is the failure mode to avoid here.
 */
export const UNKNOWN_ROUTE_TYPE = -1;

export function routeTypeLabel(code: number): string {
  if (code === UNKNOWN_ROUTE_TYPE) return "Unknown type";
  return ROUTE_TYPE_LABELS[code] ?? `Type ${code}`;
}

/** Per-segment GTFS route_type, null folded to UNKNOWN_ROUTE_TYPE. */
export function segmentRouteTypes(geometry: Geometry): number[] {
  return geometry.shape_idx.map(
    (shapeIdx) => geometry.shape_route_type[shapeIdx] ?? UNKNOWN_ROUTE_TYPE,
  );
}

/** The route types this geometry actually draws, ascending. */
export function routeTypesPresent(types: number[]): number[] {
  return [...new Set(types)].sort((a, b) => a - b);
}

/** The segments the type filter admits — what actually gets drawn. */
export function filterByRouteType(
  segments: RenderSegment[],
  types: number[],
  enabled: ReadonlySet<number>,
): RenderSegment[] {
  return segments.filter((s) => enabled.has(types[s.segmentIdx]));
}

/**
 * How many of the bundle's segments the filter admits. This is the
 * denominator of the coverage line: with a filter on, "N of M" has to count
 * the same population N was drawn from, or it compares two different objects.
 */
export function countEnabledSegments(
  types: number[],
  enabled: ReadonlySet<number>,
): number {
  let n = 0;
  for (const t of types) if (enabled.has(t)) n++;
  return n;
}
