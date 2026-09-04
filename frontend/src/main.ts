import "leaflet/dist/leaflet.css";
import "./style.css";
import L from "leaflet";
import {
  fetchBundleManifest,
  fetchGeometry,
  fetchIndex,
  fetchPeriodIndex,
  fetchTimeslot,
} from "./data.ts";
import { buildRenderableSegments } from "./segments.ts";
import {
  countEnabledSegments,
  filterByRouteType,
  pickTimeslotIndex,
  routeTypeLabel,
  routeTypesPresent,
  segmentRouteTypes,
  timeslotFile,
} from "./bundle.ts";
import {
  MIN_SAMPLES,
  MIN_SAMPLES_FOR_FULL_OPACITY,
  SPEED_DOMAIN_MAX_KMH,
  samplesToOpacity,
  speedToColor,
} from "./color.ts";
import type {
  BundleManifest,
  ExcludedDay,
  Geometry,
  PeriodInfo,
  Timeslot,
} from "./types.ts";

const REPO_URL = "https://github.com/StasSuslov/sofia-pt-data";
const DATASET_DOI = "10.5281/zenodo.22285128";
const CODE_DOI = "10.5281/zenodo.22256653";

// Dragging the slider fires `input` per pixel; the label follows every one of
// them, the redraw waits for the drag to settle.
//
// ponytail: one L.Polyline per segment, rebuilt per redraw. Measured on the
// current period's 08:00/18:00 slots (~9k segments): ~10-20 ms to build the
// renderable list, ~100 ms for clearLayers(), ~250-360 ms to add the new
// polylines — so ~400-500 ms per redraw, effectively all of it Leaflet's
// per-layer bookkeeping rather than this file's own work. The debounce keeps
// a drag itself smooth (only the label moves) and pays that cost once, when
// the drag stops. If that wait ever becomes the complaint, the upgrade is one
// custom L.Layer drawing every segment into a single canvas pass; nothing
// short of that will move the number, since L.Polyline is the cost.
const SLIDER_DEBOUNCE_MS = 80;

const daySelect = document.getElementById("day-select") as HTMLSelectElement;
const slider = document.getElementById("timeslot-slider") as HTMLInputElement;
const timeLabel = document.getElementById("timeslot-label")!;
const typeFilters = document.getElementById("type-filters") as HTMLFieldSetElement;
const summaryEl = document.getElementById("summary")!;
const legendEl = document.getElementById("legend")!;
const panelBody = document.getElementById("panel-body")!;
const panelToggle = document.getElementById("panel-toggle")!;

const map = L.map("map", { preferCanvas: true });
// One layer for every drawn segment: a redraw clears it instead of stacking
// a second timeslot on top of the first.
const segmentLayer = L.layerGroup().addTo(map);

// Every fetched file is kept: scrubbing back over a visited slot must not
// re-hit the network, and geometry is the big one (327 KB gz per bundle).
const manifestCache = new Map<string, Promise<BundleManifest>>();
const geometryCache = new Map<string, Promise<Geometry>>();
const timeslotCache = new Map<string, Promise<Timeslot>>();

function cached<T>(
  cache: Map<string, Promise<T>>,
  key: string,
  load: () => Promise<T>,
): Promise<T> {
  let pending = cache.get(key);
  if (!pending) {
    // A failed load must not stick: the next change should retry, not replay
    // the rejection forever.
    pending = load().catch((err: unknown) => {
      cache.delete(key);
      throw err;
    });
    cache.set(key, pending);
  }
  return pending;
}

// The bundle whose controls are on screen, the slot domain that goes with it,
// and the slot the user last asked for (a label, not an index — indices mean
// different times in bundles with different slot lists).
let installedBundle = "";
let currentTimeslots: string[] = [];
let selectedTimeslot = "";
const disabledTypes = new Set<number>();
let fittedOnce = false;

// Set once at startup from the typical-weekday index.
let currentPeriod: PeriodInfo | undefined;
let excludedDays: ExcludedDay[] = [];

/**
 * Only the newest refresh may touch the map. Slider and select produce
 * overlapping fetches, and a slow response for a slot the user has already
 * left must not repaint over the one they are looking at.
 */
let refreshSeq = 0;

function geometryBounds(geometry: Geometry): L.LatLngBounds {
  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLon = Infinity;
  let maxLon = -Infinity;
  for (const lat of geometry.lat) {
    if (lat < minLat) minLat = lat;
    if (lat > maxLat) maxLat = lat;
  }
  for (const lon of geometry.lon) {
    if (lon < minLon) minLon = lon;
    if (lon > maxLon) maxLon = lon;
  }
  return L.latLngBounds([minLat, minLon], [maxLat, maxLon]);
}

function speedLegendHtml(): string {
  const stops = [0, 10, 20, 30, 40, 50];
  const gradient = stops
    .map((s) => `${speedToColor(s)} ${(s / SPEED_DOMAIN_MAX_KMH) * 100}%`)
    .join(", ");
  const ticks = stops
    .map((s) => `<span>${s === SPEED_DOMAIN_MAX_KMH ? `${s}+` : s}</span>`)
    .join("");
  return `
    <div class="legend-block">
      <h3>Speed (km/h)</h3>
      <div class="ramp" style="background: linear-gradient(to right, ${gradient})"></div>
      <div class="ticks">${ticks}</div>
    </div>`;
}

function samplesLegendHtml(): string {
  const counts = [MIN_SAMPLES, 5, 10, MIN_SAMPLES_FOR_FULL_OPACITY];
  const swatches = counts
    .map(
      (n) =>
        `<span class="sample-swatch" style="opacity:${samplesToOpacity(n)}"></span><label>${n}${n === MIN_SAMPLES_FOR_FULL_OPACITY ? "+" : ""}</label>`,
    )
    .join("");
  return `
    <div class="legend-block">
      <h3>Samples per bin (n_samples)</h3>
      <p class="hint">A thin median (few samples) fades but stays visible — it is never hidden by a cutoff.</p>
      <div class="samples-scale">${swatches}</div>
    </div>`;
}

function sourcesHtml(): string {
  return `
    <div class="legend-block">
      <h3>Sources</h3>
      <p>Map tiles &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors.</p>
      <p>Transit data: <a href="https://urbandata.sofia.bg" target="_blank" rel="noopener">urbandata.sofia.bg</a> (CGM Sofia GTFS/GTFS-RT), CC BY 4.0.</p>
      <p>Dataset DOI: <a href="https://doi.org/${DATASET_DOI}" target="_blank" rel="noopener">${DATASET_DOI}</a></p>
      <p>Code DOI: <a href="https://doi.org/${CODE_DOI}" target="_blank" rel="noopener">${CODE_DOI}</a></p>
      <p><a href="${REPO_URL}/blob/main/METHODOLOGY.md" target="_blank" rel="noopener">Methodology</a></p>
    </div>`;
}

function count(n: number): string {
  return n.toLocaleString("en");
}

/**
 * The panel's numbers describe what is on the map, not what is in the file:
 * with a type filter on, both sides of "N of M" count only the enabled types.
 */
function summaryHtml(
  manifest: BundleManifest,
  timeslot: string,
  drawn: number,
  drawable: number,
  enabledTypes: number[],
  presentTypes: number[],
): string {
  let what: string;
  if (manifest.mode === "typical_weekday" && currentPeriod) {
    const p = currentPeriod;
    what = `Typical weekday median, ${p.first_date} – ${p.last_date} (${p.days_in_median.length} weekdays, ${p.route_count} routes, ${count(p.trip_count)} trips), timeslot ${timeslot}.`;
  } else {
    what = `${manifest.mode}, a single day — its own median per segment and 15-minute bin, not the typical-weekday median across days. Timeslot ${timeslot}.`;
  }

  const filtered = enabledTypes.length < presentTypes.length;
  const scope = filtered
    ? ` (${enabledTypes.map(routeTypeLabel).join(", ")} only)`
    : "";
  const coverage =
    enabledTypes.length === 0
      ? "No transport type is selected, so nothing is drawn."
      : `${count(drawn)} of ${count(drawable)} segments${scope} have a median at ${timeslot}; the rest were not observed often enough at this time of day to aggregate.`;

  const excluded =
    manifest.mode === "typical_weekday"
      ? excludedDays.length > 0
        ? `<p class="hint">${excludedDays.length} day(s) excluded from the median: ${excludedDays.map((d) => `${d.date} (${d.reason})`).join(", ")}.</p>`
        : `<p class="hint">No days excluded from the median.</p>`
      : "";

  return `
    <div class="legend-block">
      <h3>What this shows</h3>
      <p>${what}</p>
      <p class="hint">${coverage}</p>
      ${excluded}
    </div>`;
}

function showError(err: unknown): void {
  console.error(err);
  panelBody.hidden = false;
  panelToggle.setAttribute("aria-expanded", "true");
  summaryEl.innerHTML = `<div class="legend-block"><h3>Failed to load</h3><p class="error"></p></div>`;
  summaryEl.querySelector("p")!.textContent = String(err);
}

/** Checkboxes for the types this geometry actually has, not a fixed list. */
function installTypeFilters(presentTypes: number[]): void {
  typeFilters.innerHTML =
    `<legend>Transport type</legend>` +
    presentTypes
      .map(
        (t) =>
          `<label><input type="checkbox" value="${t}"${disabledTypes.has(t) ? "" : " checked"} /> ${routeTypeLabel(t)}</label>`,
      )
      .join("");
}

function draw(
  manifest: BundleManifest,
  geometry: Geometry,
  slot: Timeslot,
  timeslot: string,
): void {
  const t0 = performance.now();
  const types = segmentRouteTypes(geometry);
  const present = routeTypesPresent(types);
  const enabled = new Set(present.filter((t) => !disabledTypes.has(t)));

  const drawn = filterByRouteType(
    buildRenderableSegments(geometry, slot),
    types,
    enabled,
  );

  segmentLayer.clearLayers();
  for (const seg of drawn) {
    segmentLayer.addLayer(
      L.polyline(seg.points, {
        color: speedToColor(seg.speedKmh),
        opacity: samplesToOpacity(seg.nSamples),
        weight: 3,
      }),
    );
  }

  // Only the first draw moves the map: someone zoomed into a junction keeps
  // that view across a day or slot change.
  if (!fittedOnce && geometry.lat.length > 0) {
    map.fitBounds(geometryBounds(geometry));
    fittedOnce = true;
  }

  summaryEl.innerHTML = summaryHtml(
    manifest,
    timeslot,
    drawn.length,
    countEnabledSegments(types, enabled),
    [...enabled],
    present,
  );

  console.log(
    `[sofia-pt] ${manifest.mode} ${timeslot}: ${drawn.length} segments drawn in ${Math.round(performance.now() - t0)} ms`,
  );
}

async function refresh(): Promise<void> {
  const mySeq = ++refreshSeq;
  const bundlePath = daySelect.value;
  try {
    const [manifest, geometry] = await Promise.all([
      cached(manifestCache, bundlePath, () => fetchBundleManifest(bundlePath)),
      cached(geometryCache, bundlePath, () => fetchGeometry(bundlePath)),
    ]);
    if (manifest.timeslots.length === 0) {
      throw new Error(`${bundlePath}/manifest.json lists no timeslots`);
    }
    const index = pickTimeslotIndex(manifest.timeslots, selectedTimeslot);
    const timeslot = manifest.timeslots[index];
    const slot = await cached(
      timeslotCache,
      `${bundlePath}/${timeslot}`,
      () => fetchTimeslot(bundlePath, timeslotFile(timeslot)),
    );

    // Single checkpoint: past here nothing awaits, so what lands on the map
    // and in the panel is one consistent bundle/slot/filter triple.
    if (mySeq !== refreshSeq) return;

    selectedTimeslot = timeslot;
    currentTimeslots = manifest.timeslots;
    // The slider belongs to the hand on it. Writing it on every refresh yanks
    // the knob back whenever a request that left before the drag lands during
    // it; only a bundle change, which can move the slot under the knob, may
    // move the knob.
    if (installedBundle !== bundlePath) {
      slider.max = String(manifest.timeslots.length - 1);
      slider.value = String(index);
      timeLabel.textContent = timeslot;
      installTypeFilters(routeTypesPresent(segmentRouteTypes(geometry)));
      installedBundle = bundlePath;
    }
    draw(manifest, geometry, slot, timeslot);
  } catch (err: unknown) {
    if (mySeq === refreshSeq) showError(err);
  }
}

let debounceTimer: ReturnType<typeof setTimeout> | undefined;
function scheduleRefresh(delayMs: number): void {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => void refresh(), delayMs);
}

slider.addEventListener("input", () => {
  const timeslot = currentTimeslots[Number(slider.value)];
  if (timeslot === undefined) return;
  selectedTimeslot = timeslot;
  timeLabel.textContent = timeslot;
  scheduleRefresh(SLIDER_DEBOUNCE_MS);
});

daySelect.addEventListener("change", () => scheduleRefresh(0));

typeFilters.addEventListener("change", (event) => {
  const box = event.target as HTMLInputElement;
  const type = Number(box.value);
  if (box.checked) disabledTypes.delete(type);
  else disabledTypes.add(type);
  scheduleRefresh(0);
});

async function main(): Promise<void> {
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  legendEl.innerHTML = sourcesHtml() + speedLegendHtml() + samplesLegendHtml();

  const index = await fetchIndex();
  const periodIndex = await fetchPeriodIndex(index.typical_weekday.path);
  currentPeriod = periodIndex.periods.find(
    (p) => p.period_key === periodIndex.current_period,
  );
  if (!currentPeriod) {
    throw new Error(
      `current_period ${periodIndex.current_period} not found in periods[]`,
    );
  }
  excludedDays = periodIndex.days_excluded_from_median;

  const excludedReason = new Map(
    excludedDays.map((d) => [d.date, d.reason] as const),
  );
  const typicalPath = `${index.typical_weekday.path}/${currentPeriod.path}`;
  daySelect.innerHTML =
    `<option value="${typicalPath}">Typical weekday (${currentPeriod.first_date} – ${currentPeriod.last_date})</option>` +
    index.days
      .map((d) => {
        const reason = excludedReason.get(d.date);
        return `<option value="${d.path}">${d.date}${reason ? ` (${reason})` : ""}</option>`;
      })
      .join("");
  daySelect.value = typicalPath;

  await refresh();
}

panelBody.hidden = window.innerWidth < 600;
panelToggle.setAttribute("aria-expanded", String(!panelBody.hidden));
panelToggle.addEventListener("click", () => {
  panelBody.hidden = !panelBody.hidden;
  panelToggle.setAttribute("aria-expanded", String(!panelBody.hidden));
});

main().catch((err: unknown) => {
  showError(err);
});
