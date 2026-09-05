# Sofia PT frontend

Draws the typical-weekday median speed on a Leaflet map, with a timeslot
slider, a day switcher, and a transport-type filter. No congestion layer,
stop info, coverage heatmap, or duplicate-route detection yet — see
`CLAUDE.md` section 3 for the feature roadmap.

Vite + TypeScript (vanilla, no framework) + Leaflet, per the project's stack
conventions (`CLAUDE.md` section 5).

## Run it

```
npm install
npm run dev
```

Data comes from `data/sofia/web/` at the repo root, via a symlink at
`frontend/public/data` (created once, `ln -s ../../data/sofia/web
frontend/public/data`, not tracked in git). If it's missing, recreate it or
run the processing pipeline (`scripts/segment_speeds.py` /
`scripts/export_web.py`) first.

In production the app reads from `import.meta.env.VITE_DATA_BASE_URL`
(default `/data`) instead of the symlink — where that URL points in
production is not decided by this scaffold.

## Checks

```
npx tsc --noEmit
npm test
npm run build
```
