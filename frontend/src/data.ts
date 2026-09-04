import type {
  BundleManifest,
  Geometry,
  Manifest,
  RootIndex,
  Timeslot,
} from "./types.ts";

const DATA_BASE_URL: string = import.meta.env.VITE_DATA_BASE_URL ?? "/data";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${DATA_BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`fetch ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

/** The root index: which days exist and where the typical weekday lives. */
export function fetchIndex(): Promise<RootIndex> {
  return fetchJson<RootIndex>("/index.json");
}

/** The typical-weekday index over schedule periods (not a bundle manifest). */
export function fetchPeriodIndex(path: string): Promise<Manifest> {
  return fetchJson<Manifest>(`/${path}/manifest.json`);
}

// bundlePath is "<date>" or "typical_weekday/<period_key>" — one shape of
// bundle on disk, so one set of fetchers for both.

export function fetchBundleManifest(
  bundlePath: string,
): Promise<BundleManifest> {
  return fetchJson<BundleManifest>(`/${bundlePath}/manifest.json`);
}

export function fetchGeometry(bundlePath: string): Promise<Geometry> {
  return fetchJson<Geometry>(`/${bundlePath}/geometry.json`);
}

export function fetchTimeslot(
  bundlePath: string,
  timeslotFile: string,
): Promise<Timeslot> {
  return fetchJson<Timeslot>(
    `/${bundlePath}/timeslots/${timeslotFile}.json`,
  );
}
