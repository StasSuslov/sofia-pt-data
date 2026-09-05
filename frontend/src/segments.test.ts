import { describe, expect, it } from "vitest";
import { buildRenderableSegments } from "./segments.ts";
import type { Geometry, Timeslot } from "./types.ts";

// Minimal geometry.json-shaped fixture in the CSR layout: three segments,
// only two of which (0 and 2) have a median in the timeslot below — segment 1
// is the sparse gap this contract relies on. Segment 2 carries three points,
// the case a chord-shaped reader would silently truncate.
const geometry: Geometry = {
  segment_length_m: 200,
  shape_keys: ["shapeA"],
  shape_ids: [["A1"]],
  shape_route_ids: [["R1"]],
  shape_route_names: [["1"]],
  shape_route_type: [3],
  shape_idx: [0, 0, 0],
  segment_index: [0, 1, 2],
  point_offset: [0, 2, 4, 7],
  lat: [42.1, 42.15, 42.2, 42.25, 42.3, 42.32, 42.35],
  lon: [23.1, 23.15, 23.2, 23.25, 23.3, 23.32, 23.35],
};

const timeslot: Timeslot = {
  timeslot: "08:00",
  segment_idx: [2, 0],
  speed_kmh: [30, 12],
  n_samples: [7, 3],
};

describe("buildRenderableSegments", () => {
  it("resolves segment_idx to its full polyline and median", () => {
    const result = buildRenderableSegments(geometry, timeslot);

    expect(result).toEqual([
      {
        segmentIdx: 2,
        points: [
          [42.3, 23.3],
          [42.32, 23.32],
          [42.35, 23.35],
        ],
        speedKmh: 30,
        nSamples: 7,
      },
      {
        segmentIdx: 0,
        points: [
          [42.1, 23.1],
          [42.15, 23.15],
        ],
        speedKmh: 12,
        nSamples: 3,
      },
    ]);
  });

  it("omits a segment absent from the sparse timeslot array", () => {
    const result = buildRenderableSegments(geometry, timeslot);

    expect(result.some((s) => s.segmentIdx === 1)).toBe(false);
    expect(result).toHaveLength(2);
  });
});

describe("a timeslot that disagrees with the geometry", () => {
  it("throws on a segment_idx the geometry has no polyline for", () => {
    // Reading past point_offset yields an empty polyline instead: nothing on
    // the map, still counted as a drawn segment in the panel.
    expect(() =>
      buildRenderableSegments(geometry, {
        ...timeslot,
        segment_idx: [0, 3],
        speed_kmh: [12, 30],
        n_samples: [3, 7],
      }),
    ).toThrow(/segment_idx 3/);
  });

  it("throws on a segment whose offsets hold fewer than two points", () => {
    expect(() =>
      buildRenderableSegments(
        { ...geometry, point_offset: [0, 1, 4, 7] },
        { ...timeslot, segment_idx: [0], speed_kmh: [12], n_samples: [3] },
      ),
    ).toThrow(/1 points/);
  });
});
