# core/ — Open Tasks & Improvement Plans

## Requires External Setup

### [~] ARCH4 — Add Vite bundler (config done, npm install pending)
**Files:** `strm2stl/package.json`, `strm2stl/vite.config.js` (already created)

Remaining steps:
1. `cd strm2stl && npm install`
2. `npm run dev` — HMR dev server on port 5173 (API proxied to 9000)
3. Update `<script type="module" src="main.js">` in `index.html` to point at Vite's entry
4. Verify CDN globals (`window.L`, `window.THREE`, `window.Plotly`) still resolve

---

### [ ] ARCH5 — Vitest unit tests for pure functions (requires ARCH4)

Priority test targets:
- `interpolateCurve(x)` — curve-editor.js
- `mapElevationToColor(t, cmap)` — dem-loader.js
- `detectContinent(lat, lon)` — region-ui.js
- `haversineDiagKm()` — model-viewer.js
- `niceGeoInterval()`, `nicePixelInterval()` — stacked-layers.js

---

## Improvement Plans

### Plan A — Bundle splitting (requires ARCH4)
Split into `vendor`, `core`, `layers`, `ui` chunks loaded lazily per tab.

### Plan B — Service worker for API caching
Cache `/api/terrain/dem` and `/api/terrain/satellite` with stale-while-revalidate strategy.

### Plan C — OpenAPI schema validation
Auto-generate JSON Schema from FastAPI's `/openapi.json`; validate API responses in `core/api.js` in dev mode.
