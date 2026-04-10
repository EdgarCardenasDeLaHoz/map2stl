# core/ — Open Tasks & Improvement Plans

## Requires External Setup

### [x] ARCH4 — Vite bundler (done)
`npm install` complete. `npm run build` produces `dist/js/main.js` (221 kB → 63 kB gzip).
`npm run dev` starts HMR dev server on port 5173 with `/api` proxied to FastAPI on port 9000.
Note: the Jinja template is served by FastAPI, not Vite — use http://localhost:9000 for full
integration testing; use http://localhost:5173 for JS HMR during frontend-only work.

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
