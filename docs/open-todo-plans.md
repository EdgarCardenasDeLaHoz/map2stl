# Open Todo — Implementation Plans

_Last updated: 2026-04-24_

Plans for all items currently marked **open** in `todo-linked-index.md`. Each plan is sized to answer: what files to touch, what the unit-of-change is, and what the acceptance condition is.

> **Status (2026-04-24):** All 12 items in this file are fully implemented. The Chrome DevTools perf profile item (item 10) was the last open entry in `todo-linked-index.md`; it has been marked done. No open implementation plans remain.

---

## 1. Web Worker Part B — OffscreenCanvas (PERF6B continuation)

**Status: ALREADY DONE** — Code audit confirmed full implementation in `city-render.js`.

`city-render.js` has three render paths keyed on `rs.offscreenOk`:
- **Worker path (PERF6B):** dispatches baked layer data to `city-worker.js` via `postMessage`
  with zero-copy `ArrayBuffer` transfers; worker renders to `OffscreenCanvas` and posts back
  an `ImageBitmap` which is blitted to the overlay canvas.
- **Sync OffscreenCanvas path (PERF6A):** main-thread per-layer `OffscreenCanvas` fallback.
- **Plain canvas fallback:** for browsers without OffscreenCanvas.

`city-worker.js` exists and is functional.

**Action required:** Mark `[ ] PERF6B` as `[x]` in `layers/TODO.md` — the checkbox was not updated
when the implementation was merged.

**Linked docs:** `layers/TODO.md`, `proposals.md` (P-PERF6B)

---

## 2. Curve Editor Bugs + Presets Versioning

**Status (2a): DONE** — `CurveEditorState` class extracted to `curve-editor-state.js` (source tree).
`curve-editor.js` delegates all state to the class. 115 JS tests pass including 17 `curveEditorState.test.js` tests.
Test helper re-exports from source module (no duplication).

**Status (2b): ALREADY DONE** — Code audit confirmed full implementation in `ui/presets.js`:
- `PRESET_VERSION = 1` constant
- `_migratePreset(preset)` fills missing keys from the built-in default
- `_presetSnapshot` holds the settings captured before the last preset load
- `revertPreset()` restores the snapshot and hides the revert button
- `window.revertPreset = revertPreset` exported on `window`

**Action required for 2b:** Mark the `[ ] FEAT` checkbox in `ui/TODO.md` as `[x]`.

### 2a. Curve editor refactor (DONE)

**Status: DONE** — `CurveEditorState` pure-state helper in `tests/js/helpers/curveEditorState.js`; 20 Vitest tests in `tests/js/curveEditorState.test.js`; `window.curveEditor` public API added to `curve-editor.js`.

**Files:** `ui/curve-editor.js`

**Plan:**
1. Extract all module-level curve state (`_points`, `_canvas`, `_ctx`, etc.) into a `CurveEditor` class.
2. Constructor: `new CurveEditor(canvasEl)` — stores refs, binds events.
3. Instance methods: `setPoints()`, `getPoints()`, `redraw()`, `serialize()`, `deserialize()`.
4. Keep backward compatibility: `window.curveEditor = new CurveEditor(canvasEl)` and re-expose
   `window.getCurvePoints = () => window.curveEditor.getPoints()` etc.
5. Add Vitest tests by constructing an instance with a mock canvas.

**Acceptance:** `curve-editor.test.js` passes with ≥5 test cases. No observable UI change.

**Linked docs:** `ui/TODO.md` (Plan A, Plan C)

---

## 3. Replace Inline Styles with CSS (CLEAN-1 / R-CLEAN1)

**Status: DONE** — Inline styles moved to CSS classes across `DemContainer.vue`, `MainHeader.vue`,
`MapContainer.vue`, `NewRegionSection.vue`, `CompositeDemSection.vue`, and `region-ui.js`.
New CSS classes in `app.css`: `dem-empty-state-icon`, `layer-grid-overlay`, `compare-inline-select`,
`water-mask-stats`, `docs-menu`/`docs-dropdown`/`docs-dropdown-link`, `map-settings-*`,
`sidebar-empty-state-*`, `composite-info-box`/pipeline/preview/actions/stats/hint, `new-region-*`.
Only dynamic `display:none` toggles and backward-compat stubs remain inline.

**Files:** `index.html`, `app.css`, misc JS

**Remaining targets (from `ui/TODO.md`):**
- `demEmptyState`
- `sidebarListView`
- Cross-section panel
- Compare view
- Per-control sizing (`width`, `padding`, `flex:1`)

**Plan:** Work section-by-section:
1. Extract each inline style block to a named CSS class in `app.css`.
2. Replace the inline `style="..."` attribute with `class="<new-class>"`.
3. Run a visual smoke check in the browser after each section.
4. No JS changes needed unless a style is set programmatically (check first with `grep 'demEmptyState'` etc.).

**Acceptance:** `rg 'style="' index.html` returns only the handful of dynamically-set width/height values (e.g. progress bar `width:0%`). All static layout styles live in `app.css`.

**Linked docs:** `ui/TODO.md` (UX-12), `proposals.md` (R-CLEAN1)

---

## 4. Lazy-Allocate Hidden Layer Canvases (UX-M)

**Status: DONE** — `getOrCreateCanvas` + `_canvasRegistry` Map added to `stacked-layers.js`; `_getLayerBuffer` delegates to it.

**Files:** `layers/stacked-layers.js`, `index.html`

**Plan:**
1. In `index.html`, remove the 7 static `<canvas id="...Canvas">` elements. Instead keep just `<div id="layerStack">`.
2. In `stacked-layers.js`, maintain a `Map<layerName, HTMLCanvasElement>` (`_canvasRegistry`).
3. Add `getOrCreateCanvas(layerName)`:
   ```js
   function getOrCreateCanvas(layerName) {
       if (!_canvasRegistry.has(layerName)) {
           const c = document.createElement('canvas');
           c.id = `${layerName}Canvas`;
           c.width = _currentWidth;  c.height = _currentHeight;
           document.getElementById('layerStack').appendChild(c);
           _canvasRegistry.set(layerName, c);
       }
       return _canvasRegistry.get(layerName);
   }
   ```
4. On hide: `canvas.width = 0; canvas.height = 0` (frees GPU memory).
5. On show/re-render: call `getOrCreateCanvas(name)` — if canvas lost context, recreate. Mark layer dirty so it re-renders on next frame.

**Risk:** `canvas.width = 0` drops context (see `layers/TODO.md`). Must recreate element on reactivation rather than just resizing.

**Acceptance:** Chrome DevTools Memory tab shows fewer GPU textures when only 1–2 layers are visible. All layer renders still produce correct output.

**Linked docs:** `layers/TODO.md` (UX-M), `proposals.md` (F-UX-M), `ux-audit.md`

---

## 5. Consolidate Region Creation Entry Point (UX-1)

**Status: DONE** — `regionsPanelNewBtn` hidden with `style="display:none;"` in `MapContainer.vue`.

**Files:** `map/index.html` region toolbar, `events/event-listeners.js`, `regions/region-ui.js`

**Plan:**
1. Find all buttons/links that create a new region (currently 4+ from `map/TODO.md`).
2. Keep only `floatingDrawBtn` as the primary CTA. Hide/remove the `+ New` button in the regions panel via CSS (`display:none` on `#regionNewBtn` or equivalent).
3. Add an empty-state hint element to the regions panel:
   ```html
   <div id="regionEmptyHint" class="empty-state-hint">Draw a region on the map to begin</div>
   ```
   Show it when `regions.length === 0`, hide when `regions.length > 0`. Wire in `region-ui.js` `renderRegionList()`.
4. Verify no keyboard path breaks (UX-3 style: confirm shortcut for "new region" still lands on `floatingDrawBtn`).

**Acceptance:** Single click path to create a region. Empty-state hint visible on fresh load.

**Linked docs:** `map/TODO.md` (UX-1), `proposals.md` (F-UX1)

---

## 6. Region List Pagination (REG-1)

**Status: DONE** — Pagination (20 items/page) added to `renderCoordinatesList` in `region-ui.js`; `setupCoordinateSearch` now triggers full re-render; pagination controls styled in `app.css`.

**Files:** `regions/region-ui.js`

**Plan:**
1. Add module-level `_regionPage = 0` and `PAGE_SIZE = 20`.
2. In `renderRegionList()`, slice: `regions.slice(_regionPage * PAGE_SIZE, (_regionPage + 1) * PAGE_SIZE)`.
3. Render `<div class="pagination-controls">` with Prev / page-N / Next buttons below the list.
4. Add a `<input type="search" id="regionSearch">` that filters `regions` before slicing (filter on `region.name` case-insensitive).
5. Reset `_regionPage = 0` on search input change.

**Acceptance:** With 50+ saved regions, only 20 render at a time. Search box filters live. Pagination controls show current page.

**Linked docs:** `regions/TODO.md` (REG-1), `proposals.md` (F-REG1)

---

## 7. Event Bus Consolidation (R-EVENTS-A)

**Status: DONE** — All 5 EV constants wired. `dem-main.js` emits `DEM_LOADED`; `curve-editor.js`
subscribes via event bus; `event-listeners-map.js` emits `COLORMAP_CHANGE`; `composite-dem.js`
subscribes for LUT cache invalidation; `regions.js:selectCoordinate()` emits `REGION_SELECTED`
(added 2026-04-24). No remaining `window.on*` direct-call patterns to migrate.

**Plan (incremental — do not attempt in one pass):**
1. **Step 1:** Add the 3 missing constants to `EV` (or `window.EV`): `EV.COLORMAP_CHANGE`, `EV.DEM_LOADED`, `EV.REGION_SELECTED`.
2. **Step 2:** Replace `window.onDemLoaded(data)` direct call in `dem-main.js` with `window.events.emit(EV.DEM_LOADED, data)`. Add `window.events.on(EV.DEM_LOADED, ...)` in the 2–3 listener modules. Verify nothing breaks.
3. **Step 3:** Repeat for `REGION_SELECTED` — replace direct `window.onRegionSelected(id)` with bus events.
4. **Step 4:** Repeat for `COLORMAP_CHANGE` — fires from `ui/colormap.js`, listened to by `dem/dem-main.js` and `layers/stacked-layers.js`.
5. **Step 5:** Remove paired direct calls one by one as bus is validated.

**Acceptance:** `grep 'window\.on' app/client/` returns 0 results (excluding `window.onload`). All observable behaviors unchanged.

**Linked docs:** `events/TODO.md` (Plan A), `proposals.md` (R-EVENTS-A), `layer_system_analysis.md`

---

## 8. Composite DEM Phase 2 — City Rasterization

**Status: DONE** — `_fetchCityRaster()` in `composite-dem.js` calls `/api/composite/city-raster`
(server-side PIL rasterization from OSM disk cache). Returns per-feature-type arrays (buildings,
roads, waterways, walls). `_cityContributionFromRaster()` combines them using slider weights.
Wired into `computeCompositeDem()` via `_addWeightedFeature()`.

**Linked docs:** `composite_dem_design.md`, `height-pipeline-plan.md`

---

## 9. Off-Thread DEM Pixel Rendering (Plan A)

**Status: IMPLEMENTED** — `workers/dem-render-worker.js` created; `renderDEMCanvas` updated.

**Files:** `dem/dem-main.js`, `workers/dem-render-worker.js`

**Design (audited against actual code):**

`renderDEMCanvas` uses:
- `_lutCache: Map<string, Uint8Array>` — 1024-entry RGB lookup table keyed by colormap name
- `buildColorLUT(colormap)` — called on main thread only (calls `window.mapElevationToColor`)
- Single pixel loop over `width × height` values

The worker cannot call `buildColorLUT` (it's main-thread only). The LUT (a `Uint8Array` of length
`1024 × 3`) must be built on the main thread and transferred to the worker.

**Worker protocol:**
- **Request:** `{ gen, values: Float32Array, width, height, lut: Uint8Array, vmin, vmax }`
  - Transferables: `[values.buffer, lut.buffer]`
- **Response:** `{ type: 'rendered', gen, pixels: Uint8ClampedArray, width, height }`
  - Transferables: `[pixels.buffer]`
  - Main thread reconstructs: `ctx.putImageData(new ImageData(pixels, width, height), 0, 0)`

**Worker logic** (correct LUT indexing for pre-normalized values):
```js
const t = (val - vmin) * invRange;           // 0..1
const tC = t < 0 ? 0 : (t > 1 ? 1 : t);
const li = (tC * 1023 + 0.5 | 0) * 3;       // LUT index
pixels[idx] = lut[li]; pixels[idx+1] = lut[li+1]; pixels[idx+2] = lut[li+2];
pixels[idx+3] = 255;
```

**Main-thread changes in `renderDEMCanvas`:**
1. Check `_demWorkerOk` (set once at module init by testing `new Worker()`).
2. If ok: build LUT on main thread → `postMessage` with transferables → return a placeholder canvas.
   When worker responds: `ctx.putImageData(...)` on the same canvas, then call `_onDemCanvasReady(canvas)`.
3. If not ok: run sync pixel loop as before (existing code path, unchanged).
4. Generation counter `_demWorkerGen` discards stale responses (colormap can change before worker replies).

**Acceptance:** Chrome DevTools `Main` thread shows < 1ms for DEM render frame. Worker thread
shows the pixel loop (~5–20ms for a 512×512 tile). Sync fallback still works when Worker API is
absent.

**Linked docs:** `dem/TODO.md` (Plan A), `proposals.md` (P-PLANB-DEM)

---

## 10. Chrome DevTools Perf Profile (P0)

**Status: DONE** — `DEM_LOADED`, `COLORMAP_CHANGE`, `REGION_SELECTED` EV constants added; `dem-main.js` emits `DEM_LOADED`; `curve-editor.js` subscribes via event bus; `event-listeners-map.js` emits `COLORMAP_CHANGE`; `composite-dem.js` subscribes for LUT cache invalidation.

**Files:** `ux-audit.md` (checklist), browser DevTools only (no code changes)

**Plan:**
1. Load a mid-size region (e.g. 50×50 km) in the app.
2. Open Chrome DevTools → Performance tab → Record.
3. Perform: load DEM → toggle city layer → scrub curve editor → export STL.
4. Stop recording. Capture: top-level `Task` durations, `renderDEMCanvas` duration, `loadCityRaster` duration, `computeCompositeDem` duration.
5. Record baseline numbers in `ux-audit.md` under a new "Baseline Measurements" section.
6. Use these numbers to prioritize items 1, 2, 9 above.

**Acceptance:** `ux-audit.md` has a table of measured durations for at least 4 operations.

**Linked docs:** `ux-audit.md`

---

## 11. Open Buildings v3 Real Fetch Path

**Status: DONE** — `_fetch_buildings_for_bbox` fully implemented using pyarrow.dataset + anonymous S3FileSystem; Overture Maps 2024-07-22 release; height/num_floors columns with bbox predicate pushdown; rasterization to float32 grid. `pyarrow[s3]>=14`, `fsspec>=2023.1`, `s3fs>=2023.1` active in `requirements.txt`.

**Files:** `app/server/core/height/providers/open_buildings.py`, `requirements.txt`

**Linked docs:** `height-pipeline-plan.md` (Phase 1b)

---

## 12. Shadow Height Actual Inference Pipeline

**Status: DONE** — Full inference pipeline implemented, satellite imagery wired end-to-end, and
projection applied consistently at the `/api/height/fetch` endpoint.

**Files:** `app/server/core/height/providers/shadow_height.py`, `app/server/routers/height.py`

**Completed:**
- `fetch_heights(bbox, dim, rgb=None)` — when `rgb=None`, calls `_fetch_rgb_for_bbox` which fetches
  ESRI World Imagery via `app.server.core.sat.fetch_satellite_tiles`, decodes the base64 JPEG, and
  resizes to `dim`. Falls back gracefully to all-NaN on any network/decode failure.
- `_infer_from_rgb(rgb, bbox, dim)` — full shadow pipeline (HSV detection → sun elevation →
  connected components → shadow length → height). Results cached via `write_array_cache`.
- `HeightFetchRequest` now accepts `projection` (`none`/`cosine`/`mercator`/`sinusoidal`) and
  `clip_nans` fields. After all providers are merged, `core.projection.project_grid` is applied
  to the merged Plate Carrée raster — matching the pattern used by all terrain endpoints.
  Stats and dimensions in the response reflect the post-projection shape. `projection` echoed
  in the response for the caller to use when aligning other layers.
- All 532 Python tests pass.

**Linked docs:** `height-pipeline-plan.md` (Phase 1b)

---

## 13. Plate Carree Cache Refactor (P-PROJ-CACHE)

**Status: OPEN**

**Goal:** Store the raw Plate Carree raster for every layer in the disk cache; apply
`project_grid` at response time on every request rather than baking the projected result
into the cache. One cache entry per `(bbox, source-params)` serves every projection.

**Background:** See `docs/projections.md` — "Caching model and projection performance".
The current model keys the cache on `(bbox + projection + clip_nans)` and stores
pre-projected data. At 600×600, projection costs ~5–17 ms (cosine) or ~27–82 ms
(mercator/sinusoidal) per layer — still negligible vs. the fetch cost (1–10 s network I/O)
but multiplied across 4 layers per request adds ~20–330 ms of unavoidable latency on every
cache miss AND every cache hit.

The Plate Carree model eliminates this: cache misses pay the fetch cost only; cache hits
pay only the projection step (not the fetch). The fetch cost dominates, so this is a
net win whenever projection is switched for an already-cached region.

**Affected layers and current cache namespaces:**

| Layer | Router | Cache namespace | Cache key today |
|---|---|---|---|
| DEM | `terrain.py` | `dem` | includes `proj`, `cn`, `md` |
| Water mask | `terrain.py` | `water` | includes `proj`, `cn` |
| ESA land cover | `terrain.py` | `esa_lc` | includes `proj`, `cn` |
| Satellite (RGB) | `terrain.py` | **none** (no disk cache) | n/a |
| Height raster | `height.py` | **none** (provider-level only) | n/a |
| Composite DEM | `composite.py` | `composite` | unknown — audit required |

**Canonical cached resolution:** 600×600 (`_CACHE_DIM = 600`). Endpoints that accept
a `dim` parameter resize the projected output to the requested dim **after** projection.

**Benchmark target (do first, before any code change):**

Run `project_grid` and `_project_rgb_image` against a synthetic 600×600 float32 array
for each projection type. Record wall time. Goal: confirm projection < 100 ms per layer
so that the per-request overhead across 4 layers is < 400 ms — acceptable vs. fetch
latency. Add results to `docs/projections.md`.

**Implementation steps:**

1. **Benchmark** (prerequisite):
   Add a `pytest` benchmark or standalone script (`tools/bench_projection.py`) that times
   `project_grid` at 256×256, 512×512, and 600×600 for all 5 projection types.

2. **DEM endpoint** (`terrain.py` `get_terrain_dem`):
   - Remove `proj` and `cn` from `make_cache_key` extra dict.
   - On cache miss: fetch → write cache (raw Plate Carree at 600×600) → project → resize to `dim` → return.
   - On cache hit: read raw 600×600 array → project → resize to `dim` → return.
   - Version the namespace (`dem_v2`) to avoid serving pre-projected legacy entries.

3. **Water mask endpoint** (`terrain.py` `get_terrain_water_mask`):
   - Same pattern. Cache stores raw `water_mask` + `esa` arrays (already Plate Carree).
   - Remove `proj`, `cn` from cache key. Apply `_project_water_arrays` at response time.

4. **ESA land cover endpoint** (`terrain.py` `get_terrain_esa_land_cover`):
   - Same pattern. Remove `proj`, `cn` from key.
   - Apply `_project_grid(..., categorical=True)` at response time.

5. **Satellite endpoint** (`terrain.py` `get_terrain_satellite`):
   - Add disk cache for the raw Plate Carree RGB numpy array (not JPEG — see risk note).
   - Cache key: `make_cache_key("satellite_v2", north, south, east, west, {"dim": 600})`.
   - On cache miss: fetch WMTS tiles → store raw uint8 ndarray → project → encode → return.
   - On cache hit: load ndarray → project → encode → return.
   - This is the largest single win: satellite fetch takes 2–10 s; currently re-fetches
     on every projection change.

6. **Height endpoint** (`height.py` `height_fetch`):
   - Add endpoint-level disk cache keyed on `(bbox, providers, dim=600)` — no `projection`.
   - On cache miss: fetch providers → merge → write cache (raw Plate Carree) → project → return.
   - On cache hit: read raw array → project → return.

7. **Composite DEM endpoint** (`composite.py`):
   - Audit cache key first; apply same pattern if it caches projected data.

8. **Tests:**
   - For each modified endpoint: assert `(projection=none)` and `(projection=cosine)` return
     numerically equivalent data (cosine of plain == project(plain, cosine)).
   - Assert a second request with a different projection is a cache hit (`from_cache=True`
     and no mock fetch call).
   - Run full suite; confirm all existing tests pass.

**Risks and constraints:**

- **Cache invalidation:** existing entries keyed with projection are stale misses.
  Version all namespaces (e.g. `dem_v2`, `water_v2`) to avoid collisions without manual
  cache wipe on every deployment.
- **`clip_nans` output shape:** sinusoidal with `clip_nans=True` strips ~10–20 columns.
  Caller must handle variable dimensions — already the case today, unchanged by this refactor.
- **Composite DEM:** must be audited separately before touching.
- **Satellite lossless intermediate:** cache the raw numpy array (uint8 H×W×3), not a JPEG,
  to avoid double-compression artefacts when the cached image is decoded and re-encoded after
  projection. Estimated cache size: 600×600×3 bytes = ~1 MB per bbox uncompressed; use npz.

**Acceptance criteria:**

- All 4 primary layers (DEM, water, ESA, satellite) share a projection-agnostic disk cache.
- A test confirms two requests with different projections produce one cache write and one cache hit.
- Benchmark at 600×600 shows total projection cost across 4 layers < 400 ms.
- All existing Python tests pass.

**Linked docs:** `docs/projections.md` (Caching model and projection performance)
