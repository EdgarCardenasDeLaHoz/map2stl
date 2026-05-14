# AI-Proposed Features & Tasks — strm2stl

_Last updated: 2026-04-19_

> Completed and denied items are archived at the bottom of this file.

> **How to use:**
> - Set `Status` to `approved` to queue an item for implementation.
> - Set `Status` to `denied` to permanently drop it (AI will not re-propose).
> - Set `Status` to `deferred` to skip for now without closing the idea.
> - Leave `pending` for items not yet reviewed.
>
> When you approve an item, Claude will implement it in the next session and mark it `done` here.
> Claude will **not** implement any item whose status is not `approved`.

---

## New Features

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| F-EXP1 | Export progress indicator — spinner/bar during STL generation (poll `/api/export/status` or streaming) | `export/export-handlers.js` | Medium | done |
| F-REG1 | Region list pagination — virtual scroll or 20-per-page for 50+ regions | `regions/region-ui.js` | Medium | done |
| F-REG2 | Region import/export — download all as `regions.json`; import via file picker | `regions/regions.js`, `regions/region-ui.js` | Small | done |
| F-REG3 | Region settings inheritance — "use global defaults" override per region | `regions/regions.js`, `app/server/routers/regions.py` | Medium | pending |
| F-UX1 | Consolidate region creation — keep only `floatingDrawBtn`; add empty-state hint to panel | `map/`, `index.html`, `events/event-listeners.js` | Small | done |
| F-UX-M | Lazy-allocate hidden layer canvases — create/destroy canvas elements on show/hide | `layers/stacked-layers.js`, `index.html` | Medium | done |
| F-FEAT | Preset undo — snapshot slider values before loading a preset; expose `window.revertPreset()` | `ui/presets.js` | Small | done |

---

## Performance

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| P-PERF6B | Web Worker for city polygon rendering (Part A — Float32Array buffers; Part B — OffscreenCanvas) | `layers/city-render.js`, `workers/city-worker.js` | Large | done |
| P-PLANB-DEM | Off-thread DEM pixel loop — post `{values, lut}` to Worker, receive `ImageBitmap` | `dem/dem-main.js`, new `workers/dem-render-worker.js` | Medium | pending |
| P-PROJ-CACHE | Plate Carrée cache refactor — store raw (unprojected) raster for all layers (DEM, water, ESA, satellite, height); apply `project_grid` at response time rather than at write time. Removes `proj`/`cn` from all cache keys so one fetch serves every projection. See `docs/projections.md` for trade-off analysis. Target: 600×600 rasters. | `routers/terrain.py`, `routers/height.py`, `routers/composite.py`, `core/cache.py` | Large | pending |

---

## Refactoring / Code Cleanup

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| R-CLEAN1 | Replace remaining inline styles with CSS utility classes (index.html, misc JS) | `index.html`, `app.css`, various | Medium | pending |
| R-LAYER-LOAD | Shared `loadLayer(name, fetchFn, options)` wrapper in `ui-helpers.js` — consolidates `setLayerStatus` calls, error-toast handling, `isLayerCurrent()` guards, and stale canvas ref clearing. **Regression plan required before any code movement**: add explicit Vitest tests covering each layer's loading-state transitions (idle → loading → loaded / error) for `dem-main.js`, `water-mask.js`, `city-overlay.js`, `hydrology-overlay.js` before refactoring. | `app/client/static/js/modules/ui/ui-helpers.js`, `dem/dem-main.js`, `layers/water-mask.js`, `layers/city-overlay.js`, `layers/hydrology-overlay.js` | Large | pending |
| R-LAYERS | LayerBuffer class — unified canvas allocate/resize/dirty-track across all layer canvases | `layers/stacked-layers.js` | Large | pending |
| R-EVENTS-A | Event bus consolidation — add `EV.DEM_LOADED`, `EV.REGION_SELECTED`; replace direct `window.fn()` calls | `events/`, all modules | Large | pending |
| R-EVENTS-B | Keyboard shortcut registry — replace `keydown` switch with `window.registerShortcut(key, label, fn)` | `events/keyboard-shortcuts.js` | Small | pending |
| R-EVENTS-C | Debounce audit — gate `input` handlers where target takes >5ms | all modules | Small | pending |

---

## Architecture

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| A-SW | Service worker for API response caching — stale-while-revalidate for `/api/terrain/dem` and `/api/terrain/satellite` | new `sw.js` | Medium | pending |
| A-OBJ-TEX | OBJ cross-section export with UV map + PNG texture from current colormap | `app/server/core/export.py`, `export/export-handlers.js` | Large | denied |

---

## Backend

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| B-STREAM | Streaming STL generation — Python generators + `StreamingResponse` to reduce peak RAM | `app/server/core/export.py`, `app/server/routers/export.py` | Medium | pending |
| B-MULTI | Print-bed multi-piece export — auto-tile large DEMs into N×M pieces with alignment tabs | `app/server/core/export.py`, `export/export-handlers.js` | Large | done |
| B-OPENAPI | OpenAPI schema validation in dev — auto-generate JSON Schema from `/openapi.json`; validate in `api.js` | `core/api.js` | Small | done |

---

## Library Integration Debt

See [arch.md](arch.md) for the full import map and thin-wrapper assessment.

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| B-LIB1 | Refactor `cities_3d._terrain_mesh` → `numpy2stl.array_to_mesh(solid=True)` — ~90 lines of duplicate mesh generation | `app/server/core/cities_3d.py`, `numpy2stl/generate.py` | High | done |
| B-LIB2 | Refactor `cities_3d._extrude_ring` / `_ear_clip` → `numpy2stl.polygon_to_prism` + `numpy2stl.polygon.triangulate_polygon` | `app/server/core/cities_3d.py`, `numpy2stl/polygon.py`, `numpy2stl/generate.py` | High | done |
| B-LIB3 | Fix legacy projection in `dem.py:compute_raw_dem` → use `core/projection.project_grid` instead of raw `geo2stl.projections.proj_map_geo_to_2D` | `app/server/core/dem.py`, `app/server/core/projection.py` | Medium | done |
| B-LIB4 | Use `geo2stl.sat2stl.calculate_scale_for_dimensions` in `core/sat.py` — currently computes tile scale manually | `app/server/core/sat.py`, `geo2stl/sat2stl.py` | Medium | done |
| B-LIB5 | Route `terrain.py` `make_dem_image` import through `core/dem` — router bypasses core layer | `app/server/routers/terrain.py`, `app/server/core/dem.py` | Small | done |

---

## Completed

| ID | Description | Status |
|----|-------------|--------|
| F-UX2 | Text labels on floating map buttons | done |
| F-UX3 | Clarify sidebar 3-state toggle | done |
| F-REG1 | Region list pagination (20-per-page, search filter) | done |
| F-REG2 | Region import/export as JSON | done |
| F-UX1 | Consolidate region creation UI, draw-first empty state | done |
| F-UX-M | Lazy canvas allocation via LAYER_CANVAS_IDS registry | done |
| F-FEAT | Preset undo / revert snapshot | done |
| P-RAF | RAF-gate `applyCurveTodemSilent` | done |
| P-PERF6B | Web Worker for city polygon rendering | done |
| R-MAP2 | Bbox drag handle keyboard accessibility | done |
| B-LIB1 | Refactor cities_3d._terrain_mesh | done |
| B-LIB2 | Refactor _extrude_ring/_ear_clip | done |
| B-LIB3 | Fix legacy projection in dem.py | done |
| B-LIB4 | Use geo2stl scale calculation | done |
| B-LIB5 | Route terrain.py through core/dem | done |
| A-ARCH4 | Vite bundler setup | done |
| A-ARCH5 | Vitest unit tests for pure functions | done |
| B-OPENAPI | OpenAPI schema validation in dev | done |
| B-MULTI | Print-bed multi-piece export | done |
| F-EXP1 | Export progress indicator | done |

---

## Denied / Deferred

| ID | Reason |
|----|--------|
| F-P6 | Denied — multi-material band export adds complexity with limited demand. Standard STL/3MF export covers the use case. |
| A-OBJ-TEX | Denied — OBJ cross-section export with UV map + PNG texture. |
