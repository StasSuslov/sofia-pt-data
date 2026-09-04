import "leaflet/dist/leaflet.css";
import "./style.css";
import L from "leaflet";
import { fetchGeometry, fetchManifest, fetchTimeslot } from "./data.ts";
import { buildRenderableSegments } from "./segments.ts";
import {
  MIN_SAMPLES,
  MIN_SAMPLES_FOR_FULL_OPACITY,
  SPEED_DOMAIN_MAX_KMH,
  samplesToOpacity,
  speedToColor,
} from "./color.ts";
import type { Geometry } from "./types.ts";

// TODO: this is the timeline-slider's future single stop. Wire a slider
// bound to the period's `timeslot_count` (96 x 15-minute slots) instead of
// this constant — frontend feature priority 1 (CLAUDE.md section 3).
const TIMESLOT_FILE = "0800";
const TIMESLOT_LABEL = "08:00";

const REPO_URL = "https://github.com/StasSuslov/sofia-pt-data";
const DATASET_DOI = "10.5281/zenodo.22285128";
const CODE_DOI = "10.5281/zenodo.22256653";

function geometryBounds(geometry: Geometry): L.LatLngBounds {
  let minLat = Infinity;
  let maxLat = -Infinity;
  let minLon = Infinity;
  let maxLon = -Infinity;
  for (let i = 0; i < geometry.start_lat.length; i++) {
    const lats = [geometry.start_lat[i], geometry.end_lat[i]];
    const lons = [geometry.start_lon[i], geometry.end_lon[i]];
    for (const lat of lats) {
      if (lat < minLat) minLat = lat;
      if (lat > maxLat) maxLat = lat;
    }
    for (const lon of lons) {
      if (lon < minLon) minLon = lon;
      if (lon > maxLon) maxLon = lon;
    }
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

function attributionHtml(
  periodLabel: string,
  coverageSummary: string,
  excludedSummary: string,
): string {
  return `
    <div class="legend-block">
      <h3>What this shows</h3>
      <p>${periodLabel}</p>
      <p class="hint">${coverageSummary}</p>
      <p class="hint">${excludedSummary}</p>
    </div>
    <div class="legend-block">
      <h3>Sources</h3>
      <p>Map tiles &copy; <a href="https://www.openstreetmap.org/copyright" target="_blank" rel="noopener">OpenStreetMap</a> contributors.</p>
      <p>Transit data: <a href="https://urbandata.sofia.bg" target="_blank" rel="noopener">urbandata.sofia.bg</a> (CGM Sofia GTFS/GTFS-RT), CC BY 4.0.</p>
      <p>Dataset DOI: <a href="https://doi.org/${DATASET_DOI}" target="_blank" rel="noopener">${DATASET_DOI}</a></p>
      <p>Code DOI: <a href="https://doi.org/${CODE_DOI}" target="_blank" rel="noopener">${CODE_DOI}</a></p>
      <p><a href="${REPO_URL}/blob/main/METHODOLOGY.md" target="_blank" rel="noopener">Methodology</a></p>
    </div>`;
}

async function main(): Promise<void> {
  const t0 = performance.now();

  const map = L.map("map", { preferCanvas: true });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors",
  }).addTo(map);

  const manifest = await fetchManifest();
  const period = manifest.periods.find(
    (p) => p.period_key === manifest.current_period,
  );
  if (!period) {
    throw new Error(
      `manifest.current_period ${manifest.current_period} not found in periods[]`,
    );
  }

  const [geometry, timeslot] = await Promise.all([
    fetchGeometry(period.path),
    fetchTimeslot(period.path, TIMESLOT_FILE),
  ]);

  map.fitBounds(geometryBounds(geometry));

  const renderSegments = buildRenderableSegments(geometry, timeslot);
  for (const seg of renderSegments) {
    L.polyline(
      [
        [seg.startLat, seg.startLon],
        [seg.endLat, seg.endLon],
      ],
      {
        color: speedToColor(seg.speedKmh),
        opacity: samplesToOpacity(seg.nSamples),
        weight: 3,
      },
    ).addTo(map);
  }

  const elapsedMs = Math.round(performance.now() - t0);
  console.log(
    `[sofia-pt] ${renderSegments.length} segments for ${TIMESLOT_LABEL} drawn in ${elapsedMs} ms (fetch + render)`,
  );

  const periodLabel = `Typical weekday median, ${period.first_date} – ${period.last_date} (${period.days_in_median.length} weekdays, ${period.route_count} routes, ${period.trip_count} trips), timeslot ${TIMESLOT_LABEL}.`;
  const coverageSummary = `${renderSegments.length.toLocaleString("en")} of ${geometry.start_lat.length.toLocaleString("en")} segments have a median at ${TIMESLOT_LABEL}; the rest were not observed often enough at this time of day to aggregate.`;
  const excludedSummary =
    manifest.days_excluded_from_median.length > 0
      ? `${manifest.days_excluded_from_median.length} day(s) excluded from the median: ${manifest.days_excluded_from_median
          .map((d) => `${d.date} (${d.reason})`)
          .join(", ")}.`
      : "No days excluded from the median.";

  document.getElementById("panel-body")!.innerHTML =
    attributionHtml(periodLabel, coverageSummary, excludedSummary) +
    speedLegendHtml() +
    samplesLegendHtml();
}

const toggle = document.getElementById("panel-toggle")!;
const body = document.getElementById("panel-body")!;
body.hidden = window.innerWidth < 600;
toggle.setAttribute("aria-expanded", String(!body.hidden));
toggle.addEventListener("click", () => {
  body.hidden = !body.hidden;
  toggle.setAttribute("aria-expanded", String(!body.hidden));
});

main().catch((err: unknown) => {
  console.error(err);
  body.hidden = false;
  body.innerHTML = `<div class="legend-block"><h3>Failed to load</h3><p>${String(err)}</p></div>`;
});
