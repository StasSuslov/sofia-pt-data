import { describe, expect, it } from "vitest";
import {
  UNKNOWN_ROUTE_TYPE,
  countEnabledSegments,
  filterByRouteType,
  incompleteNotice,
  pickTimeslotIndex,
  routeTypeLabel,
  routeTypesPresent,
  segmentRouteTypes,
  timeslotFile,
} from "./bundle.ts";
import type { BundleManifest, Geometry, RenderSegment } from "./types.ts";

// Four segments over three shapes: a bus shape (segments 0 and 1), a tram
// shape (segment 2) and one whose routes disagreed on route_type, which the
// exporter writes as null (segment 3).
const geometry: Geometry = {
  segment_length_m: 200,
  shape_keys: ["bus", "tram", "mixed"],
  shape_ids: [["B1"], ["T1"], ["M1"]],
  shape_route_ids: [["R3"], ["R0"], ["R3", "R0"]],
  shape_route_names: [["84"], ["10"], ["84", "10"]],
  shape_route_type: [3, 0, null],
  shape_idx: [0, 0, 1, 2],
  segment_index: [0, 1, 0, 0],
  point_offset: [0, 2, 4, 6, 8],
  lat: [42.1, 42.11, 42.12, 42.13, 42.14, 42.15, 42.16, 42.17],
  lon: [23.1, 23.11, 23.12, 23.13, 23.14, 23.15, 23.16, 23.17],
};

const segment = (segmentIdx: number): RenderSegment => ({
  segmentIdx,
  points: [[42, 23]],
  speedKmh: 20,
  nSamples: 5,
});

describe("timeslotFile", () => {
  it("drops the colon a timeslot label carries and a filename cannot", () => {
    expect(timeslotFile("08:00")).toBe("0800");
    expect(timeslotFile("00:15")).toBe("0015");
    expect(timeslotFile("23:45")).toBe("2345");
  });
});

describe("pickTimeslotIndex", () => {
  const timeslots = ["00:00", "00:15", "08:00", "23:45"];

  it("starts at 08:00 when the bundle has it", () => {
    expect(pickTimeslotIndex(timeslots)).toBe(2);
  });

  it("keeps the slot carried over from the bundle just left", () => {
    expect(pickTimeslotIndex(timeslots, "23:45")).toBe(3);
  });

  it("falls back to 08:00 when the carried-over slot is missing here", () => {
    expect(pickTimeslotIndex(timeslots, "12:30")).toBe(2);
  });

  it("falls back to the first slot when the bundle has no 08:00", () => {
    expect(pickTimeslotIndex(["05:00", "05:15"], "12:30")).toBe(0);
  });
});

describe("segmentRouteTypes", () => {
  it("spreads each shape's route_type over that shape's segments", () => {
    expect(segmentRouteTypes(geometry)).toEqual([3, 3, 0, UNKNOWN_ROUTE_TYPE]);
  });

  it("lists only the types present, ascending, with null bucketed", () => {
    expect(routeTypesPresent(segmentRouteTypes(geometry))).toEqual([
      UNKNOWN_ROUTE_TYPE,
      0,
      3,
    ]);
  });
});

describe("routeTypeLabel", () => {
  it("names the GTFS codes this feed uses", () => {
    expect(routeTypeLabel(0)).toBe("Tram");
    expect(routeTypeLabel(3)).toBe("Bus");
    expect(routeTypeLabel(11)).toBe("Trolleybus");
  });

  it("names the null bucket instead of hiding it", () => {
    expect(routeTypeLabel(UNKNOWN_ROUTE_TYPE)).toBe("Unknown type");
  });

  it("falls back to the bare code for a type it does not know", () => {
    expect(routeTypeLabel(99)).toBe("Type 99");
  });
});

describe("filterByRouteType", () => {
  const types = segmentRouteTypes(geometry);
  // Segment 1 has no median in this slot: the sparse timeslot decides what is
  // drawable, the filter only narrows it.
  const drawable = [segment(0), segment(2), segment(3)];

  it("keeps only the enabled types", () => {
    const kept = filterByRouteType(drawable, types, new Set([3]));
    expect(kept.map((s) => s.segmentIdx)).toEqual([0]);
  });

  it("keeps everything when every present type is enabled", () => {
    const kept = filterByRouteType(
      drawable,
      types,
      new Set([UNKNOWN_ROUTE_TYPE, 0, 3]),
    );
    expect(kept.map((s) => s.segmentIdx)).toEqual([0, 2, 3]);
  });

  it("drops everything when no type is enabled", () => {
    expect(filterByRouteType(drawable, types, new Set())).toEqual([]);
  });
});

describe("countEnabledSegments", () => {
  const types = segmentRouteTypes(geometry);

  it("counts the population the drawn segments were filtered from", () => {
    // Bus only: 2 of the geometry's 4 segments, so a coverage line reading
    // "1 of 2" compares like with like — not 1 of all 4.
    expect(countEnabledSegments(types, new Set([3]))).toBe(2);
  });

  it("counts every segment when every present type is enabled", () => {
    expect(
      countEnabledSegments(types, new Set([UNKNOWN_ROUTE_TYPE, 0, 3])),
    ).toBe(4);
  });
});

// Bundle manifest as export_web.py writes one, cut to the fields the panel
// reads. This is 2026-08-27: the day the collector started at 11:07, so its
// own manifest carries a coverage note and its slot list starts at 11:00.
const slotsFrom11 = Array.from({ length: 52 }, (_, i) => {
  const minute = 11 * 60 + i * 15;
  return `${String(Math.floor(minute / 60)).padStart(2, "0")}:${String(minute % 60).padStart(2, "0")}`;
});

const dayManifest: BundleManifest = {
  mode: "2026-08-27",
  segment_count: 9155,
  timeslot_count: 52,
  timeslots: slotsFrom11,
  days_in_median: ["2026-08-27"],
  incomplete_days: { "2026-08-27": "only 52.45% coverage of the calendar day" },
  preprocessing_thresholds: { timeslot_minutes: 15 },
  known_limitations: [],
};

describe("incompleteNotice", () => {
  it("quotes the exporter's reason and counts the slots off the manifest", () => {
    // timeslot_count is the exporter's own count of the same list; the notice
    // must agree with the list that drives the slider, not with a constant.
    const notice = incompleteNotice(dayManifest)!;

    expect(notice).toContain("2026-08-27: only 52.45% coverage of the calendar day");
    expect(notice).toContain("52 of 96 15-minute slots, 11:00 through 23:45");
  });

  it("counts a full day at the manifest's own bin width, not at 15 minutes", () => {
    expect(
      incompleteNotice({
        ...dayManifest,
        timeslots: ["06:00", "06:30"],
        preprocessing_thresholds: { timeslot_minutes: 30 },
      }),
    ).toContain("2 of 48 30-minute slots, 06:00 through 06:30");
  });

  it("says nothing when every day behind the bundle is whole", () => {
    expect(
      incompleteNotice({ ...dayManifest, incomplete_days: {} }),
    ).toBeUndefined();
  });

  it("ignores a reason keyed to a day this bundle does not draw", () => {
    // The median splits by schedule period: a note about a day in the other
    // period is about the other bundle. Reporting it here would put a number
    // from one object next to a map built from another.
    expect(
      incompleteNotice({
        ...dayManifest,
        mode: "typical_weekday",
        days_in_median: ["2026-08-31", "2026-09-01"],
      }),
    ).toBeUndefined();
  });

  it("names every incomplete day the median actually used", () => {
    const notice = incompleteNotice({
      ...dayManifest,
      mode: "typical_weekday",
      days_in_median: ["2026-08-27", "2026-08-28"],
      incomplete_days: {
        "2026-08-27": "reason A",
        "2026-08-28": "reason B",
        "2026-09-07": "reason C",
      },
    })!;

    expect(notice).toContain("Incomplete days");
    expect(notice).toContain("2026-08-27: reason A");
    expect(notice).toContain("2026-08-28: reason B");
    expect(notice).not.toContain("2026-09-07");
  });
});
