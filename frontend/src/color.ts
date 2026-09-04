// Sequential, colorblind-safe blue ramp — validated palette, see the
// dataviz skill's references/palette.md (sequential hue, light -> dark).
// Darkest step = near zero, lightest = the top of the domain: congestion is
// the subject, so the slow end carries the visual weight.
const SPEED_RAMP: readonly string[] = [
  "#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf", "#2a78d6",
  "#3987e5", "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#b7d3f6", "#cde2fb",
];

// ponytail: fixed domain, not stretched to each timeslot's actual min/max —
// keeps colors comparable across timeslots without recomputing a scale per
// fetch. p95 of the current period's 08:00 slot is 40 km/h; raise this if a
// later period regularly clips at the top.
export const SPEED_DOMAIN_MAX_KMH = 50;

function hexToRgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function lerp(a: number, b: number, t: number): number {
  return a + (b - a) * t;
}

/** Maps a speed in km/h to a hex color on the sequential ramp. */
export function speedToColor(speedKmh: number): string {
  const t = Math.max(0, Math.min(1, speedKmh / SPEED_DOMAIN_MAX_KMH));
  const scaled = t * (SPEED_RAMP.length - 1);
  const lo = Math.floor(scaled);
  const hi = Math.min(SPEED_RAMP.length - 1, lo + 1);
  const frac = scaled - lo;
  const [r1, g1, b1] = hexToRgb(SPEED_RAMP[lo]);
  const [r2, g2, b2] = hexToRgb(SPEED_RAMP[hi]);
  const r = Math.round(lerp(r1, r2, frac));
  const g = Math.round(lerp(g1, g2, frac));
  const b = Math.round(lerp(b1, b2, frac));
  return `rgb(${r}, ${g}, ${b})`;
}

// n_samples confidence encoding: a bin needs >= 2 samples to survive export
// (see min_samples_rationale in the manifest), so 2 is the true floor, not 0.
// Below MIN_SAMPLES_FOR_FULL_OPACITY the line fades but never disappears —
// a thin median must stay visible, not get filtered out client-side.
export const MIN_SAMPLES = 2;
export const MIN_SAMPLES_FOR_FULL_OPACITY = 15;
const MIN_OPACITY = 0.35;

/** Maps a sample count to line opacity: thin medians fade, never vanish. */
export function samplesToOpacity(nSamples: number): number {
  const t = Math.max(
    0,
    Math.min(
      1,
      (nSamples - MIN_SAMPLES) / (MIN_SAMPLES_FOR_FULL_OPACITY - MIN_SAMPLES),
    ),
  );
  return MIN_OPACITY + (1 - MIN_OPACITY) * t;
}
