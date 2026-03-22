# TODO — strm2stl

> Read CLAUDE.md for full architecture context before editing.

---

## Open Items

### Bugs (found via Chrome testing, session 13)

#### [x] BUG1. Cache status shows "undefined files" in sidebar
`fetchServerCacheStatus()` (app.js ~510) reads `data.total_files` but the `/api/cache` endpoint returns `total_cached_files`. The display always shows "undefined files (X MB)".
**Fix:** Change `data.total_files` → `data.total_cached_files` in `fetchServerCacheStatus()`.

#### [x] BUG2. Bbox inputs empty when switching to Edit tab via tab button
Selecting a region in the sidebar (Explore tab) then clicking the "Edit" tab leaves `bboxNorth/South/East/West` empty. The bbox inputs are only populated after DEM data finishes loading (via `setBboxInputValues` in the DEM response handler at ~line 4693). The "Open in Edit view" table button works because `goToEdit()` triggers `loadDEM()` which eventually fills the inputs.
**Fix:** In `switchView('dem')`, if `selectedRegion` exists, call `setBboxInputValues(selectedRegion.north, selectedRegion.south, selectedRegion.east, selectedRegion.west)` immediately.

#### [x] BUG3. Extrude tab `.model-layout` doesn't fill container
`.model-layout` has `flex: 0 1 auto` (CSS line ~569) inside its flex parent, so it stays at intrinsic width (~370px) while the container is ~2080px. The 3D viewport gets only ~187px and the model sidebar squeezes to ~133px, making labels wrap and the viewport nearly invisible.
**Fix:** Add `flex: 1` (or `width: 100%`) to `.model-layout` in `app.css`.

#### [x] BUG4. Export buttons enabled before model generation
STL, OBJ, 3MF, City 3MF, Cross-Section, and Puzzle export buttons are all enabled (`disabled: false`, `opacity: 1`, `cursor: pointer`) even when no model has been generated. Clicking them will fail or produce errors.
**Fix:** Disable all export buttons by default. Enable them only after "Generate Model" completes successfully. Add a `modelGenerated` flag or check `terrainMesh !== null`.

---

### UX Improvements (found via Chrome testing, session 13)

#### [x] UX1. No empty state when Edit tab has no data
Switching to the Edit tab with no region selected or no DEM loaded shows blank canvases and empty inputs with no guidance. User has no idea what to do.
**Fix:** Show a centered message like "Select a region from the sidebar or Explore tab to load terrain data" when `lastDemData` is null.

#### [x] UX2. No empty state on Extrude tab
The Extrude tab shows "No model generated" in tiny text at the bottom left, easy to miss. The 3D viewport is a blank dark box.
**Fix:** Show a prominent empty state in the viewport area: "Load terrain in the Edit tab, then click Generate Model" with a button linking back to Edit.

#### [x] UX3. Sidebar takes up too much space on Edit/Extrude tabs
The sidebar is 320px wide (normal mode) or 600px (expanded), taking significant screen real estate from the DEM canvas and model viewport. On the Extrude tab especially, the sidebar is not useful since all controls are in the model sidebar.
**Fix:** Consider auto-collapsing the sidebar when switching to Extrude tab, or making it narrower in Edit/Extrude modes.

#### [x] UX4. Region list has no click affordance
Region items in the sidebar normal view show name + description but have no visual indication they're clickable (no hover highlight, no cursor change beyond default). The "✏️ Edit" badges on the map are the only way to open a region in Edit view from the map, but they're tiny.
**Fix:** Add `cursor: pointer` and a hover highlight to region items. Consider making clicking a region immediately populate bbox and offer "Load DEM" in the Edit tab.

#### [x] UX5. Continent group headers not collapsible in sidebar normal view
The continent headers ("NORTH AMERICA", "SOUTH AMERICA", etc.) appear as static text. With 39 regions, the list is long and not easy to scan.
**Fix:** Make continent headers clickable to collapse/expand their region groups. Add a count badge (e.g., "NORTH AMERICA (19)").

#### [x] UX6. Settings panel sections deeply nested — hard to discover
The Edit tab right panel has collapsible sections (Visualization, Histogram & Curves, Resolution, Water Layer, Land Cover, Presets) but the bottom sections require scrolling. The panel has 1473px of scroll content in 1109px visible.
**Fix:** Start with Resolution/Water/Land Cover sections collapsed by default, or add a quick-jump nav at the top of the settings panel.

#### [x] UX7. DEM subtab buttons show load status
The subtab buttons (Layers, Compare, Cities, Merge) don't indicate whether data is loaded for each layer. User has to click each one to discover its state.
**Fix:** Added colored status dots (gray=empty, orange+pulse=loading, green=loaded, red=error) to the Layers button (3 dots: DEM, water, land cover) and Cities button (1 dot). Dots update via `updateLayerStatusUI()` and city-overlay.js load/error handlers.

#### [x] UX8. Keyboard navigation between regions
There's only 1 element with `tabindex` in the entire app. Region list items, export buttons, and tab switching are not keyboard-accessible.
**Fix:** Added `tabindex="0"` + `role="option"` to region items. Arrow Up/Down moves focus between items (crossing continent group boundaries). Enter/Space selects the focused region. `:focus-visible` styled same as hover.

#### [x] UX9. No loading spinner/progress for DEM fetch
There's no global loading spinner. When loading DEM data (which can take several seconds), the only feedback is the "Loading all layers..." text injected into the DEM image area.
**Fix:** Add a proper loading spinner overlay to the DEM canvas area and the Extrude viewport during generation.

#### [ ] UX10. Tab workflow not guided — new users lost
The Explore → Edit → Extrude pipeline is the core workflow, but there's no onboarding or visual flow indicator. A new user doesn't know they need to: (1) select a region, (2) load DEM in Edit, (3) generate model in Extrude.
**Fix:** Add breadcrumb/stepper at the top showing the workflow stages, or show contextual hints on each tab when prerequisite data is missing.

---

### Layout & Visual Polish (found via Chrome testing, session 13)

#### [x] LP1. Extrude model sidebar too narrow for labels
Even after BUG3 is fixed, the model sidebar flex ratio (1:2 with viewport) may still squeeze labels. Many labels ("Vertical Exaggeration:", "Print Dimensions:") are long.
**Fix:** Set a `min-width: 280px` on `.model-sidebar` and test at various viewport sizes.

#### [x] LP2. Map "✏️ Edit" markers overlap and clutter
With 39 regions, the Explore map is cluttered with overlapping "✏️ Edit" markers. At zoom levels showing multiple regions, the markers stack on top of each other.
**Fix:** Added `_updateEditMarkerVisibility()` on `zoomend`/`moveend` — hides markers whose bbox is < 40px diagonal on screen. Each marker stores `_regionBounds` for pixel-diagonal calculation.

#### [x] LP3. Colorbar too small to read
The colorbar in the Edit view is 918x18px — the height (18px) makes the elevation labels very small and hard to read.
**Fix:** Increase colorbar height to 24-30px, or add a larger tooltip on hover showing the exact elevation value.

#### [x] LP4. Curve editor canvas height too short
The curve editor canvas is only 150px tall, making precise control point placement difficult, especially with closely-spaced points.
**Fix:** Increase default height to 200-250px, or make it resizable by the user.

---

### City Overlay Rendering Performance

The city overlay (`modules/city-overlay.js`) runs `_drawCityCanvas` on the main thread every time zoom, pan, data, or projection changes. For a typical city (3 000 buildings, 800 roads, 200 waterways) each full render takes 80–200 ms and creates tens of thousands of short-lived JS objects, blocking the UI thread. The items below are ordered by effort and impact.

#### [ ] PERF1. Shared output object in `geoToPx` — eliminate per-vertex array allocation
**Problem:** `_buildGeoToPx()` returns a closure that does `return [x, y]` for every coordinate. A city with 3 000 buildings × 12 vertices = 36 000 short-lived `[x, y]` arrays allocated per render, immediately discarded, triggering frequent GC pauses.
**Fix:** Replace the return value with a module-level shared mutable object:
```js
const _pt = { x: 0, y: 0 };   // shared across all geoToPx calls in one render
// inside _buildGeoToPx factory return function:
_pt.x = canvasX + xFrac * canvasW;
_pt.y = canvasY + yFrac * canvasH;
return _pt;   // caller reads _pt.x, _pt.y immediately (no store)
```
All call sites in `_drawCityCanvas` must destructure immediately (`const { x: px, y: py } = geoToPx(la, lo)`), never store the returned reference across iterations.
**Impact:** Eliminates ~100% of per-vertex heap allocation. Reduces GC frequency noticeably for dense cities.
**Effort:** ~30 min. Purely mechanical change to `_buildGeoToPx` and all callers in `_drawCityCanvas`.

---

#### [ ] PERF2. Remove `invZ` from the offscreen cache key — stop busting cache on every zoom step
**Problem:** `_makeCacheKey` includes `invZ` (= `1 / stackZoom.scale`). Every scroll-wheel tick changes `stackZoom.scale`, which changes `invZ`, which causes a full cache miss and full redraw on every intermediate zoom step. The CSS transform applied to the overlay canvas (`scale(stackZoom.scale)`) already handles visual zoom correctly between redraws — the cache just needs to survive the animation.
**Fix:**
1. Remove `invZ` from `_makeCacheKey`.
2. In `_doRenderCityOverlay`, always apply the CSS transform after blit (`overlay.style.transform = ...`), whether cache hit or miss.
3. Road line widths currently scale with `invZ` — keep this by computing widths from the *settled* zoom scale stored separately. On zoom settle (when the 300 ms debounce fires a full re-render), the cache misses once and re-draws with correct widths.
**Impact:** Cache hits on every intermediate zoom frame instead of never. Zoom animations go from 80–200 ms per frame to a single `ctx.drawImage` blit (~0.2 ms). Feels instant.
**Effort:** ~45 min. Changes to `_makeCacheKey`, `_doRenderCityOverlay`, and the road-width calculation.

---

#### [ ] PERF3. Fix OffscreenCanvas write direction — draw to offscreen first, blit to visible
**Problem:** Current flow: draw to visible canvas → `ctx.drawImage(overlay, 0, 0)` into offscreen. This forces the GPU to read back the just-rendered canvas texture in order to copy it, which serialises the pipeline.
**Fix:** Reverse the flow:
1. Always render into `_stackOffscreen` / `_demOffscreen` directly.
2. Blit to the visible overlay with `ctx.drawImage(_stackOffscreen, 0, 0)`.
The offscreen canvas is the master; the visible canvas is always a copy. On cache hit, skip the draw step entirely — only blit.
**Impact:** Removes one full GPU readback per render. Combines naturally with PERF2 (cache hit = one blit = ~0.2 ms).
**Effort:** ~30 min. Refactor `_doRenderCityOverlay` and `_doRenderCityOnDEM`.

---

#### [ ] PERF4. Pre-bake pixel-space `Float32Array` per feature — eliminate all per-frame projection math
**Problem:** Even after PERF1, `geoToPx` is still called for every vertex on every render. For a full redraw the projection math (trig for mercator/sinusoidal, multiply/add for linear) runs 36 000+ times per frame. The geo coordinates never change between renders — only the canvas size or bbox change.
**Fix:** At load time and on canvas-resize/bbox-change, pre-transform every feature's coordinates into pixel space and store them as a flat `Float32Array` on the feature:
```js
// Pre-bake (called once per canvas config, not per frame):
function _prebakeFeatures(features, geoToPx) {
    for (const feat of features) {
        const rings = /* extract coordinate rings from geometry */;
        const counts = new Uint16Array(rings.length);
        let total = 0;
        rings.forEach((r, i) => { counts[i] = r.length; total += r.length; });
        const buf = new Float32Array(total * 2);
        let i = 0;
        for (const ring of rings) {
            for (const [lo, la] of ring) {
                const p = geoToPx(la, lo);
                buf[i++] = p.x; buf[i++] = p.y;
            }
        }
        feat._px = { buf, counts };   // replaces per-frame geoToPx calls
    }
}

// Render hot path (zero projection math, zero allocation):
for (const feat of bucket) {
    const { buf, counts } = feat._px;
    let i = 0;
    for (const count of counts) {
        ctx.moveTo(buf[i], buf[i + 1]); i += 2;
        for (let v = 1; v < count; v++, i += 2) ctx.lineTo(buf[i], buf[i + 1]);
        ctx.closePath();
    }
}
```
Invalidation: whenever canvas size, bbox, or projection changes, re-bake all features (one-time cost, not per frame). Store a `_pxKey` string (`"${W}|${H}|${bboxKey}|${proj}"`) on each feature's `_px` object; check before using cached coords.
**Impact:** 5–15× faster per full render. The render loop becomes pure array iteration — no object property lookup, no trig, no closures.
**Effort:** ~3 hours. Add `_prebakeFeatures`, call from `loadCityData` and on resize events, update `_drawCityCanvas` hot path for buildings/roads/waterways.

---

#### [ ] PERF5. Viewport culling for roads and waterways
**Problem:** Roads and waterways have no sub-pixel or viewport cull. Every road segment (even those completely outside the current pan/zoom viewport) adds `moveTo`/`lineTo` commands to the path, wasting rasteriser time.
**Fix:** At pre-bake time (PERF4) also compute a pixel-space bounding box for each feature (`_pxBbox: {x0, y0, x1, y1}`). In the render loop, skip features where the bbox doesn't intersect the visible draw rect (accounting for the current `stackZoom` offset). For the DEM overlay (invZ = 1, no pan) just check against `[0, 0, W, H]`.
Also add the same y-dimension check to the existing building cull — currently only x-width is checked:
```js
// Current (only checks x-width):
if (Math.abs(x1 - x0) < 0.5) continue;
// Should also check y-height:
if (Math.abs(x1 - x0) < 0.5 && Math.abs(y1 - y0) < 0.5) continue;
```
**Impact:** At high zoom (2–4×) typically 60–80% of features are outside the viewport. Culling them halves or quarters the path command count.
**Effort:** ~2 hours. Depends on PERF4 for the pixel-space bbox. Can be added independently if pre-bake is not done yet.

---

#### [ ] PERF6. Per-layer OffscreenCanvas + Web Worker — zero main-thread blocking
**Problem:** All three city layers (buildings, roads, waterways) share one draw call on the main thread. Toggling roads re-renders buildings too. For dense cities (5 000+ buildings) the draw blocks the UI for 150–300 ms regardless of the other optimisations above.
**Fix — Part A (per-layer canvases, no Worker):**
Split `_drawCityCanvas` into three separate `OffscreenCanvas` instances — one per layer. Cache each independently. Toggling a layer only re-renders that layer's offscreen; the composite step is three `drawImage` blits.
```
_buildingsOffscreen  ←→  invalidated when building data or canvas changes
_roadsOffscreen      ←→  invalidated when road data, widths, or canvas changes
_waterwaysOffscreen  ←→  invalidated when waterway data or canvas changes
compositeCanvas      ←  ctx.drawImage(_buildingsOffscreen, 0, 0) ×3
```
**Fix — Part B (Web Worker, depends on Part A):**
Transfer each layer's OffscreenCanvas to a Worker via `transferControlToOffscreen()`. The Worker holds the pre-baked `Float32Array` buffers (structured-cloned once at load) and renders asynchronously. The main thread posts a `{type:'render', layerId, cacheKey}` message and receives an `ImageBitmap` back via `postMessage` with transfer. Main thread does `ctx.drawImage(bitmap, 0, 0)` — total main-thread cost per frame: < 1 ms.
```
loadCityData()
  → worker.postMessage({ type: 'init', buildings, roads, waterways })   (structured clone, once)
  → worker pre-bakes Float32Array buffers

on zoom settle / data change:
  → worker.postMessage({ type: 'render', layer: 'buildings', W, H, bbox, proj })
  ← worker: self.postMessage({ bitmap }, [bitmap])   (zero-copy transfer)
  → ctx.drawImage(bitmap, 0, 0)
```
**Impact:** Main thread is completely unblocked during city render. Part A alone removes the layer-coupling problem. Part B removes all blocking for even the densest cities.
**Effort:** Part A ~4 hours. Part B ~1 day (requires Worker setup, structured clone, ImageBitmap transfer). Do Part A first.

---

### City Features

#### [x] CITY1. OSM → City Heights raster layer

**Overview:**
Convert the vector OSM feature collections (buildings with `height_m`, roads, waterways) into a 2D height-map raster on the server, then expose it as an optional layer in the Edit tab stacked layers view alongside DEM, Water, and Satellite. The raster shows urban structure in the same colour-mapped style as elevation — tall buildings appear bright, roads are flat, waterways are slightly depressed.

**Backend — `POST /api/cities/raster` in `ui/routers/cities.py`:**

New Pydantic request model in `schemas.py`:
```python
class CityRasterRequest(BaseModel):
    north: float; south: float; east: float; west: float
    dim: int = 200
    buildings: dict          # GeoJSON FeatureCollection (already fetched)
    roads: dict
    waterways: dict
    building_scale: float = 1.0       # multiplier on height_m
    road_depression_m: float = 0.0    # road surface relative to ground
    water_depression_m: float = -2.0  # waterway surface relative to ground
```

New function `rasterize_city_data(...)` in `ui/core/osm.py`:
```python
import numpy as np
from rasterio.transform import from_bounds
from rasterio.features import rasterize as rio_rasterize
from shapely.geometry import shape

def rasterize_city_data(north, south, east, west, dim,
                        buildings_geojson, roads_geojson, waterways_geojson,
                        building_scale=1.0, road_depression_m=0.0,
                        water_depression_m=-2.0):
    transform = from_bounds(west, south, east, north, dim, dim)
    grid = np.zeros((dim, dim), dtype=np.float32)

    # Layer 1 — waterways (lowest, painted first)
    shapes = [(shape(f['geometry']), water_depression_m)
              for f in (waterways_geojson.get('features') or []) if f.get('geometry')]
    if shapes:
        rio_rasterize(shapes, out=grid, transform=transform, merge_alg='replace')

    # Layer 2 — roads (buffered to road_width_m/2 before burning)
    road_shapes = []
    for f in (roads_geojson.get('features') or []):
        if not f.get('geometry'): continue
        w_deg = f['properties'].get('road_width_m', 4) / 2 / 111_000
        road_shapes.append((shape(f['geometry']).buffer(w_deg), road_depression_m))
    if road_shapes:
        rio_rasterize(road_shapes, out=grid, transform=transform, merge_alg='replace')

    # Layer 3 — buildings (tall wins via np.maximum)
    for feat in (buildings_geojson.get('features') or []):
        if not feat.get('geometry'): continue
        h = (feat.get('properties') or {}).get('height_m', 10) * building_scale
        tmp = rio_rasterize([(shape(feat['geometry']), h)], out_shape=(dim, dim),
                            transform=transform, fill=0, dtype='float32')
        np.maximum(grid, tmp, out=grid)

    return {
        'values': grid.flatten().tolist(),
        'width': dim, 'height': dim,
        'vmin': float(grid.min()), 'vmax': float(grid.max()),
        'bbox': {'north': north, 'south': south, 'east': east, 'west': west},
    }
```

Route handler calls `rasterize_city_data` via `run_in_executor` (CPU-bound). Result is cached as `.npz` using the DEM cache scheme. Cache key includes `north,south,east,west,dim,building_scale,water_depression_m`.

Confirm `rasterio` is in `requirements.txt` — it is likely already present as a transitive dependency of `geo2stl`, but add explicitly if not.

**Frontend — new "City Heights" layer in stacked layers view:**

1. Add `<canvas class="layer-canvas" id="layerCityRasterCanvas"></canvas>` to `#layersStack` in `index.html`, after `layerSatCanvas`, hidden by default.
2. Add `lastCityRasterData = null` to app.js global state.
3. Add `layerCityRasterVisible` checkbox + opacity slider to the Visualization section's Layers grid (row: `🏙️ City Heights`), hidden unless `osmCityData` is set.
4. After `loadCityData()` completes (via `appState.on('osmCityData', ...)` in app.js), if the "City Heights" toggle is checked, call `loadCityRaster()`.
5. `loadCityRaster()` in app.js:
   - POSTs `{ ...currentDemBbox, dim, buildings, roads, waterways, building_scale, water_depression_m }` to `/api/cities/raster`
   - Renders result with `renderDEMCanvas(data.values, data.width, data.height, colormap, data.vmin, data.vmax)` into `#layerCityRasterCanvas`
   - Stores result in `lastCityRasterData`; calls `updateStackedLayers()` to composite
   - Sets `layerStatus['cityRaster']` to 'ready'
6. `updateStackedLayers()` and `updateLayerStatusIndicators()` already use `layerStatus` generically — add `'cityRaster'` as a new key alongside `'dem'`, `'water'`, `'landCover'`.
7. City raster opacity slider wires into the same `layerOpacity` pattern as DEM/Water/Sat.

**Cache invalidation:** `lastCityRasterData = null` when `osmCityData` changes or building_scale/water_depression_m inputs change. Re-fetch on next toggle-on.

**Visual result:** The stacked layers view shows a fourth canvas where building footprints appear as coloured raised patches (height = their real height in metres), roads are flat ground level, and waterways are a uniform slight depression. The layer is fully optional and independent of the vector city overlay.

**Effort:** Backend ~3 hours. Frontend ~2 hours.

---

#### [x] CITY2. Merge Cities controls into the settings panel — remove dedicated subtab

**Overview:**
The "Cities" DEM subtab sits alongside Layers, Compare, and Merge. It requires a tab-switch to discover, and its controls (layer toggles, load button, simplification params, status text) are duplicated away from the other layer controls. Moving them into a collapsible "Cities" section in the main settings panel makes the workflow linear: scroll down through DEM → Water → Land Cover → Cities without switching tabs.

**What moves, and where:**

| Element | From | To |
|---------|------|-----|
| Buildings / Roads / Waterways / POIs toggles + color pickers | Cities subtab | Settings → `▶ 🏙️ Cities` collapsible |
| Load Cities / Clear buttons | Cities subtab | Cities section header row (right-aligned) |
| `#cityDataStatus` status text | Cities subtab | Cities section, one line under Load button |
| Simplification tolerance + min area inputs | Cities subtab | Cities section |
| Building scale + water offset inputs (new — CITY1) | — | Cities section |
| City 3MF export button | Cities subtab | Extrude tab city export row (already present) |
| `_updateCitiesTabVisibility()` size guard | `switchDemSubtab('cities')` call | `selectCoordinate()` — disable Load button when diag > 10 km, show tooltip |

**Settings panel layout after change:**
```
▼ 🎨 Visualization & Display
    DEM    [opacity slider]
    Water  [opacity slider]
    Sat    [opacity slider]
    City Heights  [opacity slider]    ← new from CITY1

▼ 📊 Histogram & Curves
▶ 📐 Resolution          (collapsed)
▶ 💧 Water Layer         (collapsed)
▶ 🗺️ Land Cover          (collapsed)
▶ 🏙️ Cities              (collapsed)   ← NEW
    [Load Cities ▶]  [Clear ✕]  •  Loaded: 312 buildings, 87 roads
    [x] Buildings [#c8b89a]   [x] Roads [#cc8844]
    [x] Waterways [#4488cc]   [ ] POIs  [#ff6644]
    Tolerance (m) [0.5]   Min area (m²) [5]
    Building scale [1.0]  Water offset (m) [-2.0]  ← from CITY1
▶ 🎚️ Presets             (collapsed)
```

**Code changes:**

*`index.html`*:
- Remove `<button data-subtab="cities">Cities</button>` from the DEM subtab row.
- Remove the entire `#citiesTabContent` div (or its outer wrapper). Copy its inner HTML into a new `<div class="collapsible-section collapsed">` with header `🏙️ Cities` inside `.dem-controls-inner`, after the Land Cover section.
- Add the two new City Heights inputs (building_scale, water_depression_m) to the new section for CITY1 integration.

*`app.js`*:
- Remove `case 'cities':` block from `switchDemSubtab()`.
- Remove the call to `_updateCitiesTabVisibility()` from `selectCoordinate()` (replaced by the simpler Load-button disable logic inside the section).
- Add a guard in `selectCoordinate()`: after region is set, check diagonal; if > 10 km, set `document.getElementById('loadCityDataBtn').disabled = true` and update its title attribute. If ≤ 10 km, re-enable.
- The Cities collapsible auto-expands (remove `collapsed` class) when `osmCityData` is first set (via `appState.on('osmCityData', ...)`) so the user sees the loaded counts without having to open it.

*`city-overlay.js`*: No changes needed — all element IDs stay the same.

**Notes:**
- Keep the subtab row if Merge, Compare, or other subtabs still use it. If Cities is the only removal and the remaining tabs (Layers/Compare/Merge) still make sense as subtabs, just remove the Cities button — the row stays. If after this change the subtab row only has Layers (the default view), Compare, and Merge, consider whether Compare and Merge should also move to settings (separate future items).
- Cities section starts collapsed. When `osmCityData` loads successfully, the section header badge shows "Loaded • N buildings" in green and the section auto-expands once (not on subsequent re-renders).

**Effort:** ~3 hours (HTML restructuring + subtab cleanup + Load-button guard).

---

### Backend








---

### Frontend


#### [ ] FA2. Duplicate functions between app.js and active modules (long-term)
`renderDEMCanvas`, `mapElevationToColor`, `hslToRgb`, `drawColorbar`, `drawHistogram`, `drawGridlinesOverlay`, `enableZoomAndPan`, `loadCoordinates`, `recolorDEM` are duplicated inside app.js and the `modules/` plain-script files. Remove when extracting those functions into modules.

#### [ ] FA3. Loose global state (long-term)
~50 closure variables in app.js alongside `window.appState` mirrors. Long-term: single source of truth via a central `appState` emitter (see ARCH1).



---

### Frontend Architecture Refactor

#### [x] ARCH1. State event emitter — single source of truth (do first)
Replace the ~50 closure variables + `window.appState` dual-state pattern with a single event-emitter state object loaded as the first `<script>`:
```js
// modules/state.js
const appState = (() => {
  const _state = { lastDemData: null, selectedRegion: null, ... };
  const _listeners = {};
  return {
    get: (key) => _state[key],
    set: (key, val) => { _state[key] = val; (_listeners[key] || []).forEach(fn => fn(val)); },
    on:  (key, fn) => { (_listeners[key] ??= []).push(fn); }
  };
})();
```
Modules subscribe to keys they care about instead of polling globals. Wire into `city-overlay.js` and `stacked-layers.js` first as proof of concept.

#### [ ] ARCH2. Extract cohesive sections from app.js into plain `<script>` modules
Continue the pattern established by `city-overlay.js` and `stacked-layers.js`. Priority order:
1. `modules/dem-loader.js` — `loadDEM`, `recolorDEM`, `renderDEMCanvas`, `drawColorbar`, `drawHistogram`
2. `modules/water-mask.js` — `loadWaterMask`, `renderWaterMask`, `waterMaskCache`
3. `modules/regions.js` — `loadCoordinates`, `renderCoordinatesList`, `saveCurrentRegion`, `detectContinent`
4. `modules/export.js` — `downloadSTL`, `downloadModel`, `generateCrossSection`
5. `modules/curve-editor.js` — `initCurveEditor`, `applyCurveTodem`, `interpolateCurve`

Each module subscribes to `appState` rather than being called directly. No bundler required for this phase.

#### [ ] ARCH3. Centralize all API calls into `api.js`
`api.js` already has the right structure but is dead. Convert it from an ES module to a plain `<script>` (remove `export` statements), load it before app.js, and replace raw `fetch()` calls in app.js with the centralized functions. Ensures all routes are in one place with consistent error handling.

#### [ ] ARCH4. Add Vite as bundler (enables proper ESM)
One-day setup: `npm create vite@latest -- --template vanilla`. Converts the module system from plain `<script>` tags to real `import`/`export`. Migration path:
1. Keep FastAPI serving the Jinja template; Vite proxies in dev, builds `/dist` for production
2. Convert each plain-script module to a proper ES module one at a time
3. Replace HTML `onclick="fn()"` refs with `addEventListener` bindings in `main.js`

Payoff: tree-shaking, source maps, hot reload in dev.

#### [ ] ARCH5. Unit tests for pure functions (requires ARCH4)
Once on Vite, add Vitest (zero-config). Test pure functions: `interpolateCurve`, `mapElevationToColor`, `detectContinent`, `haversineDiagKm`, bbox math, cache key generation. The centralized `api.js` layer (ARCH3) makes fetch calls trivially mockable.

---

### Features

#### [ ] P10. Undo/redo for curve editor and DEM modifications
#### [ ] P11. Region thumbnail previews in sidebar
Generate small PNG thumbnails of each region's DEM and show them in the sidebar list. Makes it much easier to identify regions at a glance vs. reading bbox coordinates.
#### [ ] P12. Quick-preview in Explore tab
Show a small DEM preview thumbnail when hovering over a region bbox on the map, without needing to switch to Edit tab.

## Completed

### UI Fixes
- [x] A. Edit button → sidebar compact mode
- [x] B. Settings panel resize reflow
- [x] C. City overlay LOD on zoom (road widths scale with `stackZoom.scale`)

### Features
- [x] P1. Physical dimensions panel
- [x] P3. Contour lines on model surface
- [x] P4. Base label engraving
- [x] P5. STL mesh repair (trimesh watertight)
- [x] P7. Terrain cross-section export
- [x] P8. Flat water surface cap
- [x] P9. Region label editor in UI

### Cities Feature (Notebook → App)
| # | Activity | Status |
|---|----------|--------|
| 1 | DEM + ESA satellite fetch | ✅ |
| 2 | OSM building footprints | ✅ |
| 3 | OSM road network | ✅ partial |
| 4 | Fill missing building heights | ✅ |
| 5 | Simplify building polygons | ✅ |
| 6 | Roads → width-aware polygons | ✅ |
| 7 | Map terrain Z onto buildings/roads | ✅ |
| 8 | 2D overlay (buildings + roads on DEM) | ✅ |
| 9 | 3D landscape mesh | 🔲 low priority |
| 10 | 3D building mesh generation | ✅ |
| 11 | Napari 3D preview | ❌ skip (desktop-only) |
| 12 | 3MF export terrain + buildings | ✅ |
| 13 | Mesh validation (watertight) | ✅ (P5) |
| 14 | Mesh simplification | ✅ |
| 15 | Google 3D Tiles via Blender/bpy | → TODO_ADVANCED.md |

### Caching
- [x] D–H. `core/cache.py`, DEM/water mask caching, `.json.gz` OSM cache, startup prune

### Backend Refactor
- [x] 1–7b. Full split from `location_picker.py` monolith → `server.py` (328 lines) + `schemas.py` + `config.py` + `core/` + `routers/`
- [x] 9. Pydantic V2 warnings fixed

### Data Storage
- [x] 10–12. SQLite migration (`data.db` — regions + region_settings, WAL mode)

### Bug Fixes
- [x] `_ear_clip` index reversal (cities_3d.py)
- [x] `_terrain_mesh` wrong face normals (cities_3d.py)
- [x] `asyncio.get_event_loop()` → `get_running_loop()` (routers/cities.py)
- [x] DEM upsampling for small bboxes (cv2.resize when native < dim)
- [x] Auto-load city data on Cities tab switch
- [x] City overlay misalignment in DEM single view (getBoundingClientRect positioning)
- [x] Settings panel fixed width → 100%
- [x] h5_local DEM source wired

### Frontend Audit (session 6)
- [x] FA1. Dead modules marked `/* DEAD */`; endpoints updated to `/api/terrain/*`
- [x] FA4. Dead stub `loadCoordinates()` removed
- [x] FB1–FB5. AbortController, null guards, double-attach guard, Leaflet clearLayers, resp.ok checks
- [x] FP1–FP5. Grid CSS-only pan, curve LUT, color LUT, DocumentFragment, Three.js disposal
- [x] FU1–FU4. Cancel button, skipped-feature toast, Escape key modal, WebGL error handling
- [x] FQ1–FQ2. write3MF uses logger; index-modular.html removed

### Frontend Audit (session 7)
- [x] FB6. All `alert()` calls → `showToast()`
- [x] FB8/FB9. `resp.ok` guard in `fetchServerCacheStatus()`; `.catch()` chain verified on export fetches
- [x] FB11. Error message `innerHTML` → `textContent` / `replaceChildren()` (XSS fix)
- [x] FB12. All `localStorage` keys prefixed with `strm2stl_`
- [x] FB13. Curve editor `ResizeObserver` fires once on init; RAF debounce added to both observers
- [x] FP6. `ResizeObserver` in `initCurveEditor()` debounced via `requestAnimationFrame`
- [x] FP7. Stacked-layers axis labels batched via `DocumentFragment`

### Backend Audit (session 7)
- [x] BA1. `asyncio.get_event_loop()` → `get_running_loop()` (server.py, terrain.py)
- [x] BS1. Traceback removed from error responses in terrain.py; `exc_info=True` on logger
- [x] BS2/BS3. `_validate_bbox()` + `_validate_dim()` helpers added; called at DEM, raw DEM, water-mask route entry
- [x] BC1. `show_sat` / `show_landuse` added to DEM cache key
- [x] BC3. `blend_layers()` raises `ValueError` on unknown mode; `blend_mode` is `Literal[...]` in schema
- [x] BC4. 501 stub endpoints (`/api/terrain/satellite`, `/api/terrain/elevation-profile`) removed

### Session 13 Fixes (current)
- [x] BUG1–4: Cache status label, bbox inputs on tab switch, model-layout fill, export buttons disabled until generation
- [x] UX1: DEM empty state (`#demEmptyState` + `_setDemEmptyState()`, shown when `lastDemData` is null)
- [x] UX2: Extrude empty state (`#modelEmptyState` centered overlay in model viewport)
- [x] UX3: Sidebar auto-collapses when switching to Extrude tab, restored on other tabs
- [x] UX5: Continent headers in sidebar already collapsible with count badge (verified)
- [x] UX6: Resolution/Water/Land Cover sections already collapsed by default (verified)
- [x] LP1/LP3: Model sidebar min-width 280px, colorbar height 26px
- [x] ARCH1: `modules/state.js` Proxy-based reactive appState loaded before other modules; subscribed in city-overlay.js
- [x] OSM edge polygon fix: removed coordinate clamping in geoToPx — canvas clip() handles bounds
- [x] Projection-aware city overlay: `_buildGeoToPx()` reads `paramProjection`, applies mercator/cosine/lambert/sinusoidal
- [x] OSM min_area in EPSG:3857: reproject before area filter; simplify_tolerance now applied to buildings too
- [x] Merge panel pre-populated from current layers (`_syncMergeFromCurrentLayers`, Pipeline Settings grid)

### Session 14 Fixes
- [x] UX4: Region hover affordance (left border accent + color transition)
- [x] UX9: Loading spinners on DEM load and model generation (`showLoading`/`hideLoading`)
- [x] LP2: Edit marker zoom-based visibility (`_updateEditMarkerVisibility` hides markers when bbox < 40px diagonal)
- [x] LP4: Curve editor canvas height increased (150→220px)

### Session 12 Cleanup
- [x] Deleted 14 dead JS files: `api.js`, `state.js`, `main.js`, `ui_init.js`, `dem_renderer.js`, `components/dem-viewer.js`, `components/map.js`, `components/contentScript.bundle.js` (Chrome extension artifact), `utils/canvas.js`, `utils/colors.js`, `core/LayerManager.js`, `core/LayerCompositor.js`, `cache/LayerCache.js`, `api/LayerApi.js` — and their now-empty directories
- [x] Removed dead `<script type="module">` tags from `index.html` (dem-viewer.js, canvas.js, ui_init.js module block)
- [x] Deleted `coordinates.json.bak` (SQLite migration backup)
- [x] Updated CLAUDE.md and TODO.md to reflect the cleaned-up state

### Session 11 Fixes
- [x] `setupEventListeners` refactor — extracted 7 helper function declarations: `_setupBboxListeners`, `_setupModelExportListeners`, `_setupMapAndDemListeners`, `_setupResizablePanel`, `_setupSettingsJsonToggle`, `_setupCityAndExportListeners`, `_setupSidebarEditView`; body reduced from ~884 lines to ~77 lines of calls
- [x] `activateDrawTool` restored as a hoisted function declaration inside `setupEventListeners` scope (accessible from `_setupMapAndDemListeners`)

### Session 10 Fixes
- [x] FQ3. `|| 0` → `?? 0` for nullable defaults where 0 is a valid value (`min_elevation`, bbox coordinates, water mask values, files_deleted count)
- [x] SRTM h5 web fallback — `fetch_h5_dem` raises `FileNotFoundError` when file absent or region uncovered (no tiles found); `fetch_layer_data` catches it and falls back to `fetch_opentopo_dem(..., demtype="SRTMGL3")`

### Session 9 Fixes
- [x] BUG: City overlay misalignment — added `ctx.save()/clip(tX,tY,tW,tH)/restore()` in `renderCityOverlay` to constrain feature drawing to the letterbox rect
- [x] BP1. Heavy blocking ops (make_dem_image, fetch_sat_overlay, stitch/proj raw DEM, fetch_water_mask_images) extracted into sync helpers and called via `run_in_executor` in `routers/terrain.py`
- [x] BP2. Blocking JSON fallback I/O in `routers/regions.py` async handlers wrapped in `run_in_executor(None, partial(...))`; `functools.partial` + `asyncio` added as imports
- [x] 8. Legacy routes `/api/save_coordinate` and `/api/coordinates` confirmed already removed from backend (only reference is dead `api.js`)
- [x] P2. Print-bed fit optimizer confirmed already implemented (`_updateBedOptimizer` + HTML `bedSizeSelect`/`bedOptimizerResult`)
- [x] building_z_scale. Building height scale confirmed already a UI slider (`buildingZScale` input, wired in app.js line ~2327)

### Session 8 Fixes
- [x] FB7. All `localStorage` accesses wrapped in try/catch with toast fallback; keys prefixed `strm2stl_`
- [x] FB10. AbortController added to `loadWaterMask()` and `loadSatelliteImage()`; stale responses ignored
- [x] FQ4. Numeric form inputs (`modelResolution`, `modelExaggeration`, `modelBaseHeight`) range-validated before export
- [x] FU6. Cities tab button grayed-out (`disabled`, opacity 0.4, `not-allowed` cursor, tooltip) when region > 10 km; click guard added in `setupDemSubtabs`
- [x] BC2. Lambda captures in `run_in_executor` replaced with `functools.partial`
- [x] BE2. Temp file cleanup via `BackgroundTask(os.unlink, ...)` added to all 4 export responses in `core/export.py`
- [x] BS4. All `os.chdir()` calls removed from `server.py`, `terrain.py`, `core/dem.py`, `core/export.py`; module-level `sys.path.insert(0, _STRM2STL_DIR)` added to each
- [x] BE1. OSM layer catches add `exc_info=True`; cities.py silent `except: pass` → debug log; 3MF error generic message
