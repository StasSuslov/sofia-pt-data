import type { Geometry, RenderSegment, Timeslot } from "./types.ts";

/**
 * Resolves a timeslot's sparse (segment_idx, speed_kmh, n_samples) triples
 * against geometry.json's CSR point arrays.
 *
 * The timeslot array is sparse by contract: a segment with no median in this
 * slot is simply absent from segment_idx, and this function must not invent
 * one for it — that's the behavior the test locks down.
 *
 * The reverse — a segment_idx the geometry has no polyline for — is a broken
 * bundle, not sparseness, and throwing is the point: the two files disagree,
 * so every count drawn from them describes something other than the map.
 * Reading past the end of point_offset instead yields an empty polyline,
 * which draws as nothing and reports as a segment. The caller turns this into
 * the panel's error block, so the page survives and the mismatch is stated.
 */
export function buildRenderableSegments(
  geometry: Geometry,
  timeslot: Timeslot,
): RenderSegment[] {
  const segmentCount = geometry.point_offset.length - 1;
  return timeslot.segment_idx.map((segmentIdx, i) => {
    const from = geometry.point_offset[segmentIdx];
    const to = geometry.point_offset[segmentIdx + 1];
    if (from === undefined || to === undefined || to - from < 2) {
      throw new Error(
        `timeslot ${timeslot.timeslot}: segment_idx ${segmentIdx} has no ` +
          `drawable polyline in geometry.json (${segmentCount} segments, ` +
          `point_offset gives ${to === undefined || from === undefined ? "no" : to - from} points)`,
      );
    }
    const points: [number, number][] = [];
    for (let p = from; p < to; p++) {
      points.push([geometry.lat[p], geometry.lon[p]]);
    }
    return {
      segmentIdx,
      points,
      speedKmh: timeslot.speed_kmh[i],
      nSamples: timeslot.n_samples[i],
    };
  });
}
