# TODO — strm2stl

> Started 2026-03-18. All prior UI/frontend items are complete — see FUNCTIONALITY_DOC.md.
> Read CLAUDE.md for full architecture context before editing.

---

## What this app is (my understanding)

`strm2stl` is a **local terrain-to-3D-print pipeline**. The workflow:

1. Pick a geographic region (country, mountain range, city block, ocean basin) from a Leaflet map
2. Pull real elevation data (OpenTopography SRTM/COP30, local tiles, ESA, Earth Engine)
3. Visualise and tune it in the browser — colormap, curve editor, water mask, projection, DEM merging
4. Generate a watertight STL/OBJ/3MF and send it to a slicer / printer

The regions in `coordinates.json` suggest two distinct use modes:

**Macro terrain** (Appalachia, Colorado, Chile, Norway, Caribbean) — large geographic prints, emphasis on topography, bathymetry, water bodies. High base height, low depth scale. Often no water subtraction.

**Micro/city** (Wissahickon, Philadelphia, Granada, Cartagena) — small-area prints at high resolution with building geometry. The `Cities.ipynb` pipeline — OSM footprints + height fill + road polygons + terrain Z — feeds this use case.

The **puzzle feature** exists to split large models across print beds. The **cities feature** is the emerging second workflow on top of the terrain pipeline.

---

## UI — Fixes

### [x] A. Edit button → switch region panel to compact form
- Pressing Edit (in region list / map popup) should collapse the full sidebar and show only the compact bbox form (N/S/E/W inputs + Reload) with the region name above.

### [x] B. Settings panel resize → inner components reflow continuously
- Canvas, histogram, and curve editor should resize during drag, not only on `mouseup`.

### [x] C. City overlay LOD on zoom
- Road line widths scale with `stackZoom.scale` via per-feature `road_width_m` → `metrePerPx` conversion in `city-overlay.js`.

---

## New Features — Implemented

### [x] P1. Physical dimensions panel
Show the user what their print will physically be **before** they hit Generate.
Pure JS, no backend call. Displays print footprint in mm, model height, base thickness, bed fit check.

### [x] P3. Contour lines on model surface
Raised or engraved topo contour lines baked into the STL output.
- Contour interval configurable (100m, 250m, 500m, 1000m)
- Raised (bump) or engraved (groove) style toggle
- Index contours (every 5th line thicker)

### [x] P4. Base label engraving
Engrave region name, scale bar, and north arrow onto the bottom face of the base.
Uses `numpy2stl` text/polygon tools. Region name pulled from `coordinates.json`.

### [x] P5. STL mesh repair before download
Runs `trimesh` watertight repair before serving the STL file.
`trimesh.repair.fill_holes()`, `trimesh.repair.fix_normals()`.
Adds `is_watertight: bool` and `face_count: int` to the export response.

### [x] P7. Terrain cross-section export
Cut the terrain along a chosen latitude or longitude line.
Output: a flat rectangular STL showing the elevation profile.
UI: cut axis select + coordinate input + "Mid" button + slab thickness.

### [x] P8. Flat water surface cap
For ocean/bathymetry regions: automatically add a flat cap at sea level (0m).
Toggle in Extrude tab: "Add sea level cap".

### [x] P9. Region label editor in UI
Text input for `label` pre-filled with current value. Datalist dropdown showing existing labels.
`PUT /api/regions/{name}` already accepts `label` — no backend change needed.

---

## Cities Feature — Notebook → App

> From `notebooks/Cities.ipynb`. Decide which activities to bring in:

| # | Activity | Status | Priority |
|---|----------|--------|----------|
| 1 | DEM + ESA satellite fetch | ✅ in app | — |
| 2 | OSM building footprints (`building: True`) | ✅ done | High |
| 3 | OSM road network | ✅ partial | — |
| 4 | Fill missing building heights | ✅ done | High |
| 5 | Simplify building polygons | ✅ done | High |
| 6 | Roads → width-aware polygons | ✅ done | Medium |
| 7 | Map terrain Z onto buildings/roads | ✅ done | Medium |
| 8 | 2D overlay (buildings + roads on DEM) | ✅ done | Medium |
| 9 | 3D landscape mesh | 🔲 not in app | Low |
| 10 | 3D building mesh generation | ✅ done | High |
| 11 | Napari 3D preview | ❌ skip | Desktop-only |
| 12 | 3MF export terrain + buildings | ✅ done | High |
| 13 | Mesh validation (watertight check) | → see P5 above | High |
| 14 | Mesh simplification | ✅ done | Medium |
| 15 | Google 3D Tiles via Blender/bpy | → see TODO_ADVANCED.md | Research phase |

**Suggested order:** 2+4+5 (buildings in 2D overlay) → 6+7 (road geometry) → 10+12 (3D buildings + 3MF)

---

## Caching — Server-side disk cache overhaul

### [x] D. Create `core/cache.py` — shared helpers
### [x] E. Cache processed DEM response in `preview_dem()`
### [x] F. Cache water mask in `get_water_mask()`
### [x] G. Compress OSM cache → `.json.gz`
### [x] H. Wire `prune_cache()` into server startup

---

## Backend Refactor — Split location_picker.py

```
ui/
├── server.py          ← app init, startup, run_server (was location_picker.py)
├── schemas.py         ← all ~30 Pydantic models
├── config.py          ← OPENTOPO_DATASETS, path constants, TEST_MODE, API keys
├── core/
│   ├── dem.py         ← DEM fetch + processing functions
│   ├── export.py      ← STL/OBJ/3MF generation logic
│   ├── cache.py       ← cache helpers
│   └── osm.py         ← fetch_osm_data
└── routers/
    ├── terrain.py     ← /api/terrain/* + /api/dem/merge
    ├── regions.py     ← /api/regions/*
    ├── export.py      ← /api/export/*
    ├── cities.py      ← /api/cities/*
    ├── cache.py       ← /api/cache/*
    └── settings.py    ← /api/settings/*
```

### [x] 1. `config.py` — extract all constants and path declarations
### [x] 2. `schemas.py` — extract all ~30 Pydantic models
### [x] 3. `core/dem.py` — extract DEM fetch + processing functions
### [x] 4. `core/osm.py` — extract `fetch_osm_data`
### [x] 5. `core/export.py` — extract STL/OBJ/3MF business logic
### [x] 6. `routers/` — one file per route group (terrain router added session 5)
### [x] 7. Rename `location_picker.py` → `server.py` (do last)
### [x] 7b. Clean up server.py — remove 2100+ lines of dead code (duplicate helpers, inline routes, generate_* functions); server.py now 328 lines
### [ ] 8. Remove legacy routes (`/api/save_coordinate`, `/api/coordinates`) after migrating app.js
### [x] 9. Fix Pydantic V2 warnings: `@validator` → `@field_validator`, `on_event` → lifespan

---

## Data Storage — Migrate regions from JSON to SQLite

### [x] 10. Create `core/db.py` — database initialisation and connection helper
### [x] 11. Write migration script `scripts/migrate_json_to_sqlite.py`
### [x] 12. Update region routes to read/write SQLite

---

---

## Audit Findings (2026-03-18) — Fixed / Pending

### [x] BUG: `_ear_clip` index reversal (core/cities_3d.py)
When a building polygon was CW (area < 0), the pts array was reversed for the algorithm
but the returned indices still referenced the reversed array. Fixed by remapping indices
back to original with winding flip: `(n-1-a, n-1-c, n-1-b)`.

### [x] BUG: `_terrain_mesh` wrong face normals (core/cities_3d.py)
- Top surface: winding `[tl, tr_, bl]` gave normal pointing DOWN. Fixed to `[tl, bl, tr_]`.
- Bottom plate: winding `[bl_c, br_c, tr_c]` gave normal pointing UP. Fixed to `[bl_c, tr_c, br_c]`.
Left/right/front/back walls were already correct.

### [x] Cleanup: unused imports in core/cities_3d.py
Removed `zipfile`, `xml.etree.ElementTree`, `BytesIO`, `Dict`, `Optional`.
Moved `import tempfile, os` to module top-level.

### [x] Fix: `asyncio.get_event_loop()` → `asyncio.get_running_loop()` (routers/cities.py)
Both OSM fetch and 3MF export routes now use the non-deprecated form.

### [ ] Question: `building_z_scale` hardcoded at 0.5 mm/m — should it be a UI control?
Currently a fixed default in `CityExportRequest`. Real building heights vary enormously
(residential 3–15 m, skyscrapers 100–500 m). Consider exposing in the Cities export panel.

### [x] Question: "Export 3MF" button placement
Moved to Extrude tab under a "City Buildings" row alongside STL/OBJ/3MF buttons.
Button label changed to "🏙️ 3MF + Buildings" to distinguish from plain terrain 3MF.

### [ ] write3MF prints to stdout on every export
`numpy2stl/save.py` line ~113 has `print(f"✅ Successfully saved...")`.
This pollutes server logs. Patch or monkey-patch to use `logger.info()` instead.

### [x] Feature: h5_local DEM source wired (2026-03-19)
`strm_data.h5` at `C:/Users/eac84/Desktop/Desktop/FILES/` contains SRTM3 tiles at
6000×6000 px per 5° tile (~90m resolution). Exposed as `dem_source=h5_local`.
- `config.py`: `H5_SRTM_ROOT`, `H5_SRTM_FILE`, `H5_SRTM_AVAILABLE` (override via `STRM_H5_ROOT` env var)
- `core/dem.py`: `fetch_h5_dem(N,S,E,W)` — standalone h5 reader, no city2stl deps
- `server.py` `preview_dem()`: routes `dem_source==h5_local` before OpenTopography block
- `/api/terrain/sources`: exposes `h5_local` with `available` flag
- For Cartagena (0.045° bbox): returns native 54×55 px (vs 11×11 from stitch_tiles)
- Upsampling to `dim` still applied after (bilinear cv2.resize)

### [ ] Future: SRTM h5 web fallback (high priority)
When `strm_data.h5` is absent or for regions not covered, fall back to:
1. **OpenTopography SRTMGL3** — identical 90m SRTM3 data via REST API. Same resolution
   as h5, global coverage, no local file needed. Requires `OPENTOPO_API_KEY`.
   Implementation: `dem_source=h5_local` auto-falls-back to `SRTMGL3` when h5 unavailable.
2. **Google Earth Engine SRTM/NASADEM** — 30m resolution (3× better). `geo2stl/sat2stl.py`
   already has EE integration. Add `fetch_ee_dem(N,S,E,W)` alongside `fetch_h5_dem`.
   For city-scale bboxes EE quota is trivial (< 1km² tiles).
3. **Auto-selection rule**: for bboxes with diagonal < 30km, prefer h5_local → SRTMGL3 →
   EE in that order. For larger regions, keep using `local` (stitch_tiles) or OpenTopo.

### [x] BUG: DEM upsampling for small bboxes (server.py — 2026-03-19)
`make_dem_image` only downsamples; for small city-scale bboxes (Cartagena, Granada,
Wiss) the SRTM stitcher returns a tiny native grid (e.g. 11×11 for ~5km bbox).
Fixed in `preview_dem()`: after calling `make_dem_image`, upsample with `cv2.resize`
(bilinear) to `dim` when native data is smaller. Satellite is now resized to
`max(dem_dim, requested_dim)` rather than being forced to the (tiny) DEM native shape.

### [x] Feature: auto-load city data on Cities tab switch (app.js — 2026-03-19)
`switchDemSubtab('cities')` now calls `loadCityData()` automatically when
`window.appState.osmCityData` is null. User no longer needs to click "Load City Data"
manually after selecting a cities region.

### [x] BUG: city overlay misalignment in DEM single view (session 5)
The `.city-dem-overlay` was positioned with `inset:0` filling the whole `#demImage` container,
but the DEM canvas is vertically centered inside it via flexbox padding. Fixed by using
`getBoundingClientRect()` to position the overlay to match the DEM canvas's actual CSS rect.

### [ ] BUG: city overlay layer misalignment in stacked view
Building outlines extend beyond DEM letterbox bounds. Root cause: `.osm-overlay` canvas
may not be using the same `tX/tY/tW/tH` letterbox rectangle as the DEM layer canvas when
CSS zoom transform is active. Needs investigation and fix.

### [x] BUG: Settings panel components not resizing with window (session 5)
`.dem-controls-inner` had `width: 640px` fixed width. Changed to `width: 100%`.
`.dem-right-panel` changed `flex-shrink: 0` → `flex-shrink: 1`, added `min-width: 220px`.

### [x] Performance: city overlay slow on large city areas (session 4 — 2026-03-19)
**Root cause**: `applyStackedTransform()` called `renderCityOverlay()` on every wheel
scroll event, triggering a full canvas re-render of thousands of buildings per tick.
**Fix (stacked-layers.js)**: Also apply CSS transform to `.osm-overlay` canvas for smooth
visual during zoom. Only re-render when scale change > 15% (LOD update) OR after 300 ms
debounce when zoom settles. Variables `_cityOverlayLastScale` + `_cityOverlayDebounceTimer`.
**Fix (city-overlay.js)**: After actual re-render, reset `overlay.style.transform = ''`
so the canvas doesn't appear double-scaled.
**Fix (city-overlay.js)**: Pre-compute `feat._bbox = {minLon,maxLon,minLat,maxLat}` for
all features at load time (in `_computeTerrainZ`). During render, skip buildings whose
geo bbox spans < 1.5 px (invisible at current zoom — common for small sheds at low zoom).

---

## Frontend Audit — 2026-03-19

> Full audit of `static/js/` (19 files, ~11k LOC) and `templates/index.html`. Items marked `[ ]` are actionable; severity in parens.

### Architecture

#### [ ] FA1. Dead module files — delete or integrate (High)
`api.js`, `state.js`, `main.js`, `components/dem-viewer.js`, `utils/canvas.js`, `utils/colors.js` — ~2500 lines of ES module code, **none imported anywhere**. Developers editing these files see no effect in the running app.
- Short-term fix: delete or clearly mark `/* DEAD — not loaded */` at top of each file.
- Long-term: integrate into app.js or complete the module migration (see CLAUDE.md Refactoring Plan).

#### [ ] FA2. Duplicate function implementations (High)
The following functions exist in **both** `app.js` and the dead module files. The app.js copies are the live ones:
`renderDEMCanvas`, `mapElevationToColor`, `hslToRgb`, `drawColorbar`, `drawHistogram`, `drawGridlinesOverlay`, `enableZoomAndPan`, `loadCoordinates`, `recolorDEM`.
- Short-term fix: add a comment at each duplicate site in app.js pointing to the module file.
- Long-term: remove duplicates when integrating modules.

#### [ ] FA3. Loose global state / dual state (Medium)
~50 closure variables in app.js live alongside `window.appState`. Both `lastDemData` and `window.appState.lastDemData` exist but are not always kept in sync. Same for `currentDemBbox`, `layerBboxes`, `layerStatus`.
- Fix: establish a single source of truth per variable. Remove the closure copy or the `window.appState` copy, not both.

#### [ ] FA4. Dead stub `loadCoordinates()` at file top (Low)
Lines ~34–39 contain a stub `async function loadCoordinates()` that is overridden by the real implementation at ~1057. The stub is never called.
- Fix: delete the stub.

---

### Correctness Bugs

#### [ ] FB1. Race condition — DEM fetch has no AbortController (Medium)
`window.loadDEM` fires a new `fetch(/api/terrain/dem)` on every call with no cancellation of in-flight requests. Rapid region changes can return results out of order, leaving the wrong DEM rendered.
- Fix: store an `AbortController` on `window.loadDEM._controller`; abort it at the start of each call.

#### [ ] FB2. Missing null checks on canvas queries (Medium)
Several places do `const demCanvas = document.querySelector(...); const W = demCanvas.width;` without checking for null. If the query returns null (e.g. before the DEM tab is shown), this crashes silently.
- Affected: app.js ~4700 (DEM render), ~3200 (stacked layers), ~5500 (water mask).
- Fix: add `if (!demCanvas) return;` guards.

#### [ ] FB3. Event listener double-attachment (Medium)
`setupCoordinateSearch()` (app.js ~327) adds an `input` listener to the search box with no guard against being called twice. Multiple calls stack listeners, causing search events to fire multiple times.
Same issue in curve editor (`setupCurveEventListeners()` ~2024) — no cleanup function, so toggling the curve editor attaches stacking listeners.
- Fix: use `{ once: false }` + removeEventListener, or a `_initialized` guard flag.

#### [ ] FB4. Leaflet layers accumulate on repeated `loadCoordinates()` (Low)
`preloadedLayer` and `editMarkersLayer` FeatureGroups are added to the map on each call to `loadCoordinates()` without removing old ones first.
- Fix: call `.clearLayers()` before re-populating.

#### [ ] FB5. `fetch` response not validated as JSON (Medium)
API calls in app.js do `const data = await resp.json()` without first checking `resp.ok` or `Content-Type`. If the server returns an HTML error page, `.json()` throws an unhandled rejection.
- Fix: wrap in `if (!resp.ok) throw new Error(...)` before `.json()`.

---

### Performance

#### [ ] FP1. Grid redrawn on every mousemove (High)
`stacked-layers.js` ~334: the `mousemove` pan handler calls `applyStackedTransform()` which calls `drawLayerGrid()` — a full DOM-writing grid redraw — on every mouse event (~60/sec while panning).
- Fix: separate the CSS transform update from the grid redraw. Apply the CSS transform in the `mousemove` handler; only redraw the grid on `mouseup`.

#### [ ] FP2. Curve interpolation called per pixel — missing LUT (Low)
`applyCurveTodemSilent()` calls `interpolateCurve(t)` for every DEM value (up to 40,000 calls per render). `interpolateCurve` runs a monotone cubic spline evaluation each time.
- Fix: pre-compute a 1024-point lookup table when `curvePoints` changes; replace per-pixel calls with `lut[Math.round(t * 1023)]`.

#### [ ] FP3. Color mapping per pixel — missing LUT (Low)
`mapElevationToColor(t, cmap)` is called for each pixel in `renderDEMCanvas`. It recalculates the colormap branch and RGBA math per pixel.
- Fix: build a `Uint8ClampedArray[1024 * 4]` LUT per colormap; use it in the pixel loop.

#### [ ] FP4. Region list uses O(n) individual `appendChild` calls (Low)
`renderCoordinatesList()` appends one DOM node per region on every filter/refresh. With 500+ regions this causes layout thrash.
- Fix: use `DocumentFragment` to batch all nodes before a single `appendChild`.

#### [ ] FP5. Three.js geometry never disposed on scene reset (Low)
When a new terrain mesh is created in `createTerrainMesh()`, the old `terrainMesh` geometry and material are not disposed before the new mesh replaces it.
- Fix: call `terrainMesh.geometry.dispose(); terrainMesh.material.dispose();` before reassigning.

---

### UX / Error Handling

#### [ ] FU1. No cancel / abort button for long DEM loads (Medium)
If a DEM fetch hangs (slow OpenTopography API, large bbox), the user sees a spinner but has no way to cancel. The app is stuck until the request times out.
- Fix: add an "× Cancel" button that calls `controller.abort()` on the current DEM fetch.

#### [ ] FU2. Failed city overlay features silently dropped (Low)
In `city-overlay.js`, malformed GeoJSON features are silently skipped. Users see "2500 buildings loaded" but may have lost some features with no indication.
- Fix: count skipped features and show a warning toast if `skipped > 0`.

#### [ ] FU3. Modal has no Escape key handler (Low)
`#regionNotesModal` has no `keydown` listener for `Escape` to close it. Focus is also not trapped inside the modal while open.
- Fix: add `document.addEventListener('keydown', e => { if (e.key === 'Escape') hideNotesModal(); })` when modal opens.

#### [ ] FU4. WebGL initialisation failure is silent (Low)
`new THREE.WebGLRenderer()` in `initGlobe()` is not wrapped in try/catch. On browsers/hardware where WebGL is unavailable, the globe silently fails to initialise.
- Fix: wrap in try/catch and show an informational message (or simply hide the globe toggle button).

---

### Code Quality

#### [ ] FQ1. `write3MF` prints to stdout on every export (Low)
`numpy2stl/save.py` line ~113 has `print(f"✅ Successfully saved...")`. This pollutes server logs.
- Fix: monkey-patch or patch the library to use `logger.info()` instead.

#### [ ] FQ2. index-modular.html — purpose unclear (Low)
`templates/index-modular.html` exists but is never referenced in server routes. Unknown if it is an in-progress alternative or dead code.
- Fix: either wire it to a route or delete it.

#### [ ] FQ3. `??` vs `||` for nullable defaults (Low)
Scattered through app.js: `const res = bbox.resolution || 0` — coerces empty string to 0. Should use `?? 0` (nullish coalescing) where zero is a valid value.

---

### Pending (from prior sessions, now in audit context)

#### [ ] BUG: city overlay layer misalignment in stacked view (Medium)
Building outlines extend beyond DEM letterbox bounds. Root cause: `.osm-overlay` canvas may not use the same `tX/tY/tW/tH` letterbox rectangle as the DEM layer canvas when CSS zoom transform is active.

---

## Notes

- **Backend refactor is complete** — `server.py` is now 328 lines (was 2588). All routes are in routers/. All business logic is in core/. `location_picker.py` renamed to `server.py`.
- **Order:** SQLite migration (10–12) can be done independently of the code split (1–9).
- Cache refactor (D–H) is complete and has immediate UX benefit.
