# core/ — Open Tasks & Improvement Plans

## Open TODOs

### [~] ARCH4 — Add Vite bundler (config done, npm install pending)
**Files:** `strm2stl/package.json`, `strm2stl/vite.config.js` (already created)

Config files exist. Remaining steps:
1. `cd strm2stl && npm install` — installs Vite + dependencies
2. `npm run dev` — HMR dev server on port 5173 (API proxied to 9000)
3. Update `<script type="module" src="main.js">` in `index.html` to point at Vite's entry
4. Verify CDN globals (`window.L`, `window.THREE`, `window.Plotly`) still resolve correctly

**Risk:** Low. CDN globals are already referenced via `window.*`, not imports.

---

### [ ] ARCH5 — Vitest unit tests for pure functions (requires ARCH4)
**Files:** `modules/__tests__/*.test.js` (new), `package.json`

Priority test targets:
- `interpolateCurve(x)` — curve-editor.js
- `mapElevationToColor(t, cmap)` — dem-loader.js
- `detectContinent(lat, lon)` — api.js or config
- `haversineDiagKm()` — regions.js
- `niceGeoInterval()`, `nicePixelInterval()` — stacked-layers.js
- `isLayerCurrent()` — cache.js

**Setup:**
```js
// package.json
"scripts": { "test:unit": "vitest" }
```
Create `modules/__tests__/` directory, import functions by path.

---

### [x] CORE-1 — Typed event constants
**File:** `core/events.js`

`window.EV` already has event name constants, but they're plain strings. If a new module misspells a constant, events silently don't fire. A `Proxy`-based trap that `console.warn`s on access to unknown keys would catch typos at runtime.

**Fix:**
```js
window.EV = new Proxy(_evConstants, {
    get(target, key) {
        if (!(key in target)) console.warn(`[events] Unknown event: ${key}`);
        return target[key];
    }
});
```

---

### [x] CORE-2 — API error normalisation + raw fetch migration
**Files:** `core/api.js`, `map/map-globe.js`, `dem/dem-main.js`, `layers/city-overlay.js`, `export/model-viewer.js`

- All callers now destructure `{ data, error }` and check the error field.
- Added `api.export.preview`, `api.cities.cached`, signal support to `api.dem.satellite`.
- Migrated 3 remaining raw `fetch('/api/...')` calls to use `window.api.*` wrappers.
- Only non-API fetch remaining: `/static/global_dem_meta.json` in `map-globe.js` (static file, not an API route — intentional).

---

## Improvement Plans

### Plan A — Bundle splitting (requires ARCH4)
Once Vite is set up, split the bundle into:
- `vendor` chunk: Leaflet, THREE, Plotly (CDN today — could move to npm)
- `core` chunk: state, events, api, cache
- `layers` chunk: dem, water, city, composite
- `ui` chunk: curve editor, presets, view management

Load chunks lazily per tab (`Explore` tab doesn't need export chunk; `Layers` tab doesn't need map chunk).

### Plan B — Service worker for API caching
Cache `/api/terrain/dem` and `/api/terrain/satellite` responses in a service worker with a stale-while-revalidate strategy. DEM re-renders for the same bbox would be instant on reload.

**Files:** New `sw.js` at project root; register in `app-setup.js`.

### Plan C — OpenAPI schema validation
`schemas.py` has Pydantic models for all endpoints. Auto-generate a JSON Schema from FastAPI's `/openapi.json` and validate API responses in `core/api.js` in dev mode. Catches backend schema drift early.
