import type { Geometry, RenderSegment, Timeslot } from "./types.ts";

/**
 * Resolves a timeslot's sparse (segment_idx, speed_kmh, n_samples) triples
 * against geometry.json's CSR point arrays.
 *
 * The timeslot array is sparse by contract: a segment with no median in this
 * slot is simply absent from segment_idx, and this function must not invent
 * one for it — that's the behavior the test locks down.
 */
export function buildRenderableSegments(
  geometry: Geometry,
  timeslot: Timeslot,
): RenderSegment[] {
  return timeslot.segment_idx.map((segmentIdx, i) => {
    const points: [number, number][] = [];
    for (
      let p = geometry.point_offset[segmentIdx];
      p < geometry.point_offset[segmentIdx + 1];
      p++
    ) {
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
