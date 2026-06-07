# Known Issues & Status — strm2stl

_Last updated: 2026-05-26_

## Active Bugs

### 1. City raster endpoint 500 — NaN values in JSON response
`/api/terrain/city` (and related city overlay endpoints) returns HTTP 500 when the rasterized city grid contains `NaN` values; Python's `json.dumps` emits bare `NaN` tokens which are not valid JSON, and the browser rejects the response. Fix in progress (parallel task, 2026-05-26).

## Active Technical Debt

### 1. `<script>` vs Module Boundary
HTML inline `onclick=`/`onchange=` attributes have been removed (converted to `addEventListener` in event-listeners.js). One intentional inline `onclick=` remains on the dev-only debug error overlay dismiss button. Converting app.js itself to a full ES module is not planned — keep public functions on `window.*`.

## Resolved Technical Debt

### ~~City raster NaN JSON crash~~ ✅ (2026-05-26)
`/api/cities/raster` crashed with a 500 error when a non-identity projection (e.g. "cosine", "lambert") was applied, because `project_grid` fills out-of-projection areas with `numpy.nan` and `grid.flatten().tolist()` produces Python `float('nan')` which is not valid JSON. Fixed by wrapping the grid with `np.nan_to_num(grid, nan=0.0)` before `.flatten().tolist()` at both the cache-hit and fresh-fetch code paths in `app/server/routers/cities.py`.

### ~~F-SKY11.1 Phase A (pano-level coastline alignment)~~ ✅ (2026-05-17)
Pano heading-offset recovery from stitched 360° pano + water mask vs coastline keypoints. Demo script `scripts/12_pano_coastline_demo.py` demonstrates approach; Cartagena seed_5 recovers 310° vs manual 320°. Phase B (integration into region_pdf.py) pending.

### ~~skyline_cv script sprawl~~ ✅
Scripts `00`–`07` (individual-step runners) removed; single orchestration
entry point is now `scripts/08_region_skyline_pdf.py`. `review.py` removed
(logic merged into `region_pdf.py`). `pipeline.py` and `region_pdf.py` have
expanded module-level docstrings. `STATUS.md` added to capture current
accuracy numbers and known weaknesses. Two-file architecture: `pipeline.py`
(pure math, unit-tested) + `region_pdf.py` (I/O + rendering).

### ~~app.js DOMContentLoaded Closure~~ ✅
`renderDEMCanvas` and `window.loadDEM` were extracted to `dem-main.js`. Closure vars (`lastDemData`, `originalDemValues`) moved to `window.appState`.

### ~~Closure-Only State Vars Not on appState~~ ✅
All ~20 closure-only vars (`boundingBox`, `drawnItems`, `coordinatesData`, `map`, `globeScene`, `sidebarState`, `waterOpacity`, `layerBboxes`, `layerStatus`, etc.) migrated to `window.appState`. Legacy `window.get*/set*` aliases kept for backward compat. Modules can now subscribe to changes via `window.appState.on('key', fn)`.

### ~~Inline onclick/onchange handlers~~ ✅
All non-intentional inline handlers removed. Last remaining: dev-only debug overlay dismiss button (intentional).

## Feature Status

| ID | Feature | Status |
|----|---------|--------|
| P1 | Physical dimensions panel | ✅ Done |
| P2 | Print-bed fit optimizer | ✅ Done |
| P3 | Contour lines in STL | ✅ Done |
| P4 | Base label engraving | ✅ Done |
| P5 | STL mesh repair (trimesh) | ✅ Done |
| P6 | Elevation band export (multi-material STL) | ❌ Denied (see [proposals.md](proposals.md) F-P6) |
| P7 | Cross-section OBJ export | ✅ Done |
| P8 | Flat water surface cap | ✅ Done |
| P9 | Region label editor | ✅ Done |
| P10 | Curve undo/redo | ✅ Done |
| P11 | Region thumbnails | ✅ Done |
| P12 | Map quick-preview tooltips | ✅ Done |

## Open Tasks

No open tasks remain. All items have been completed.

## Library Integration Debt ✅ ALL RESOLVED

`app/server/core/` is now a thin wrapper over `geo2stl` and `numpy2stl` as intended.
All five B-LIB items from [proposals.md](proposals.md#library-integration-debt) are complete.
See [arch.md](arch.md) for the current import map.

| ID | Summary | Severity | Status |
|----|---------|----------|--------|
| B-LIB1 | `cities_3d._terrain_mesh` → delegates to `numpy2stl.array_to_mesh` | High | ✅ Done |
| B-LIB2 | `cities_3d._extrude_ring`/`_ear_clip` → delegates to `numpy2stl.polygon_to_prism` | High | ✅ Done |
| B-LIB3 | `dem.py` legacy `proj_map_geo_to_2D` removed | Medium | ✅ Done |
| B-LIB4 | `sat.py` uses `geo2stl.sat2stl.calculate_scale_for_dimensions` | Medium | ✅ Done |
| B-LIB5 | `terrain.py` no longer imports `make_dem_image` directly | Small | ✅ Done |

## Completed Refactoring Milestones

- EXP-1 ✅ — Export progress indicator (spinner + `/api/export/status` polling)
- CLEAN-1 ✅ — Inline styles replaced with CSS classes across Vue components and JS modules
- UX-M ✅ — Lazy canvas allocation via `getOrCreateCanvas` + `_canvasRegistry` in `stacked-layers.js`
- UX-1 ✅ — Region creation consolidated to single `floatingDrawBtn` entry point
- REG-1 ✅ — Region list pagination (20 items/page + live search)
- REG-2 ✅ — Region import/export as JSON
- R-EVENTS-A ✅ — Event bus consolidation (`DEM_LOADED`, `COLORMAP_CHANGE`, `REGION_SELECTED`)
- P-PLANB-DEM ✅ — Off-thread DEM pixel rendering via `dem-render-worker.js`
- dim refactor ✅ — All terrain/water/ESA endpoints take `dim` (px); server computes `sat_scale` from bbox; `resolution_m` returned in responses
- IMP4 ✅ — dem-loader.js owns all DEM canvas helpers
- IMP5 ✅ — window.appState unified across modules
- ARCH1 ✅ — state.js Proxy appState + events.js event bus
- ARCH3 ✅ — api.js centralizes all fetch calls
- ARCH4 ✅ — Vite bundler installed (npm install + build verified, package.json + vite.config.js)
- FA2 ✅ — No duplicate functions between app.js and modules
- PERF6B ✅ — city-worker.js Web Worker for city overlay rendering; generation counter for stale-reply discard; sync fallback preserved
- Reactive bbox ✅ — N/S/E/W inputs update Leaflet rectangle live on every keystroke (no reload until Enter/button)
- Backend split ✅ — server.py + schemas.py + config.py + core/ + routers/
- SQLite migration ✅ — data.db with WAL mode
- Backend DEAD-1 ✅ — removed JSON fallback (~150 lines) from regions.py
- Backend REFACTOR-1–5 ✅ — split fetch_osm_data, merge _fill_heights, split _rasterize_city, extract H5 tile helpers, satellite tile math
- Backend EXTRACT-1 ✅ — fetch_water_mask extracted from terrain router to core/dem.py
- Backend DEAD-2/4 ✅ — removed unused dim param and local import math from terrain.py
- Frontend CLEAN-1–5 ✅ — regions.js: inline onclick, haversineDiagKm bug, AUTO_SCALE constants, globe marker colors, selectCoordinate JSDoc
- Frontend DEM-CLEAN-1–3 ✅ — dem-main.js: extracted _applyDemResult, moved progress bar/cancel/sat-unavailable inline styles to CSS
- CLOSURE-MIGRATE ✅ — All ~20 closure-only vars migrated to window.appState; legacy get*/set* aliases kept; no closure vars remain in app.js
- ARCH5 ✅ — Vitest 4.x installed; 58 JS unit tests across 5 test files (interpolateCurve, mapElevationToColor, detectContinent, haversineDiagKm, nicePixelInterval, niceGeoInterval); helpers in tests/js/helpers/; config in vite.config.js test block

Full history: `docs/archive/functionality_doc.md`

## Completed Python / Session Milestones

- Session PEP8 ✅ — 51 PEP 8 violations fixed in terrain_session.py
- Session REFACTOR ✅ — 7 helper methods + 5 settings properties; ~150 lines removed (~8.3%); matplotlib consolidation; fetch method consolidation
- HYDRO-OPT ✅ — HydroRIVERS geometry simplification pipeline (collinear point reduction + shapely simplify + simplified cache)
- HYDRO-REGION ✅ — Region bounding box coverage fix (SA north to +15°N, NA south to -10°S); eliminated Central America gap
- HYDRO-CACHE ✅ — Simplified shapefile validation probe before trusting cached files
- HYDRO-RASTER-CACHE ✅ — Server-side disk cache of rasterized `river_grid` keyed on (bbox, dim, source, depression_m, scale_m, min_order, order_exponent, projection, clip_nans); 30-day TTL. Cuts repeat hydrology fetches from ~200 s to <50 ms.
- HYDRO-DEFAULT ✅ — SDK default source flipped from `natural_earth` to `hydrorivers` to match UI.
- HYDRO-VECTORIZE ✅ — Killed the `combined.to_json()` round-trip and replaced the per-feature simplify+buffer Python loop with vectorized GeoPandas/shapely-2.0 ops. **Why (do not re-litigate):** the JSON serialization existed only as an artifact of an old API boundary; the rasterizer immediately rebuilt shapely shapes from coordinate dicts. Vectorized GeoSeries.simplify/buffer is C-level and ~75× faster than the Python loop. **How to apply:** `fetch_hydrorivers` now returns a `GeoDataFrame` directly (not GeoJSON dict); `rasterize_hydrorivers` consumes the GDF. Also added `width_factor` (multiplier on per-line buffer) and `all_touched=True` in the rasterize call so thin rivers stay visible. Amazon cold dropped from ~270 s → ~17 s; Vermont from ~5 s → ~0.3 s.
- HYDRO-DEDUPE ✅ — Server-side in-flight dedupe via a `dict[cache_key, asyncio.Future]` in `terrain.py` so concurrent identical requests share one pipeline; client-side dedupe in `hydrology-overlay.js` returns the in-flight promise on duplicate calls and disables the Load button while running. **Why:** the disk cache only helps requests arriving *after* the first one completes; without dedupe, N parallel identical requests = N full pipelines (we observed 5× contention on Amazon-bbox loads). **How to apply:** clients re-firing `loadHydrology` with the same params now no-op until the first finishes; concurrent server requests likewise. **Rationale (do not re-litigate):** HydroRIVERS is the better default at *every* zoom we care about, not just city zoom. Two reasons baked into [`geo2stl/hydrology.py`](../geo2stl/hydrology.py): (1) the HydroRIVERS rasterizer applies `min_buf_deg = pixel_deg * 0.6` so thin features stay ≥1 px wide and survive downsampling; the Natural Earth path has no such buffering, so tributaries alias away at continent zoom. (2) HydroRIVERS carries Strahler order 1–9 and scales depression depth as `base * (order/9)**exponent`, so major rivers carve deep while small tributaries still register. Natural Earth has neither. Natural Earth remains valid only as a fallback when (a) the HydroRIVERS regional shapefile isn't downloaded yet, or (b) the bbox spans regions HydroRIVERS doesn't cover (e.g. Antarctica).
- SRV-LIFECYCLE ✅ — start() reuses healthy server; _ensure_bbox() validates 4 keys; server wait timeout 60 attempts
- Bbox validation ✅ — Frontend rejects satellite/water requests for areas > 20°×20°
- PERF-RAF ✅ — RAF-gated curve drag in curve-editor.js
- MAP-2 ✅ — Keyboard accessibility for bbox drag handles
- UX-2 ✅ — Text labels on floating map buttons
- UX-3 ✅ — Clarified sidebar 3-state toggle
- ARCH4 ✅ — Vite bundler installed
- ARCH5 ✅ — Vitest 4.x installed; 58 JS unit tests across 5 test files
