import type { Geometry, Manifest, Timeslot } from "./types.ts";

const DATA_BASE_URL: string = import.meta.env.VITE_DATA_BASE_URL ?? "/data";

async function fetchJson<T>(path: string): Promise<T> {
  const res = await fetch(`${DATA_BASE_URL}${path}`);
  if (!res.ok) {
    throw new Error(`fetch ${path} failed: ${res.status} ${res.statusText}`);
  }
  return res.json() as Promise<T>;
}

export function fetchManifest(): Promise<Manifest> {
  return fetchJson<Manifest>("/typical_weekday/manifest.json");
}

export function fetchGeometry(periodPath: string): Promise<Geometry> {
  return fetchJson<Geometry>(`/typical_weekday/${periodPath}/geometry.json`);
}

export function fetchTimeslot(
  periodPath: string,
  timeslot: string,
): Promise<Timeslot> {
  return fetchJson<Timeslot>(
    `/typical_weekday/${periodPath}/timeslots/${timeslot}.json`,
  );
}
