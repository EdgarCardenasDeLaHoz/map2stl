# JS Module Map — strm2stl

_Last updated: 2026-05-14_

All modules in `app/client/static/js/modules/`, imported by `main.js` in dependency order.
Modules expose functions via `window.*` — they do **not** import each other.
See [arch.md § Module Boundary](arch.md#module-boundary) for detailed coordination rules and entry-point architecture.

### Module group overview

```mermaid
flowchart LR
    CORE["core/<br/>state, events, api"] --> DEM["dem/<br/>loader, merge"]
    CORE --> LAYERS["layers/<br/>water, city, composite"]
    CORE --> MAP["map/<br/>globe, bbox"]
    DEM --> EXPORT["export/<br/>STL, 3MF, viewer"]
    LAYERS --> UI["ui/<br/>views, presets, curves"]
    MAP --> REGIONS["regions/<br/>CRUD, sidebar"]
    UI --> EVENTS["events/<br/>wiring"]
    EVENTS --> APP["app.js"]
```

## Subdirectory Groups

### `core/` — Foundation & utilities
| File | Key exports | Purpose |
|------|-------------|---------|
| `state.js` | `window.appState` | Proxy-based reactive state with `.on()/.set()/.emit()` |
| `events.js` | `window.events`, `window.EV` | Event bus + EV constants |
| `api.js` | `window.api.*` | All fetch helpers (regions, dem, export, cities, cache, settings) |
| `ui-helpers.js` | `showToast`, `showLoading`, `setLayerStatus`, `getProjectionParams` | Toast, spinners, layer status UI; `getProjectionParams()` reads `#paramProjection`/`#paramMaintainDimensions`/`#paramClipNans` — the single source every layer fetch uses so they all request matching projection settings (F-PROJ-DIMS) |
| `cache.js` | `waterMaskCache`, `setupCacheManagement` | In-memory water mask LRU + cache UI |

### `dem/` — DEM rendering & processing
| File | Key exports | Purpose |
|------|-------------|---------|
| `dem-loader.js` | `mapElevationToColor`, `recolorDEM`, `applyProjection`, `drawHistogram` | Canvas rendering, colormaps, projection, zoom |
| `dem-main.js` | `loadDEM`, `window.renderDEMCanvas` | Main DEM loader + orchestration |
| `dem-gridlines.js` | `drawGridlinesOverlay`, `toggleGridOverlay` | Lat/lon gridline overlay |
| `dem-merge.js` | `setupMergePanel`, `runMerge` | Multi-source DEM blending UI |

### `layers/` — Layer composition & city overlays
| File | Key exports | Purpose |
|------|-------------|---------|
| `stacked-layers.js` | `updateStackedLayers`, `setStackMode`, `applyStackedTransform`, `moveLayer`, `setLayerOpacity`, `getLayerOrder`, `getActiveLayers`, `setSplitViewEnabled`, `isSplitViewEnabled` | Single-canvas stacked view, zoom/pan; uses `LAYER_CANVAS_IDS` registry + `_getLayerBuffer`/`_freeLayerBuffer` for GPU memory management. `setSplitViewEnabled` draws CompositeDem/SatImg side-by-side in `stackViewCanvas` instead of alpha-blending, sharing the same `stackZoom` transform so pan/zoom stays synced. |
| `composite-dem.js` | `computeCompositeDem`, `setupCompositeDemControls` | Additive height contributions (DEM/water/buildings/roads/waterways/walls/landcover/sat/trails, each independently toggleable) + per-layer histograms + ML feature arrays |
| `mesh-layer.js` | `uploadMeshLayer`, `selectLibraryMeshFile`, `computeMeshHeightmap`, `autoRegisterMesh`, `suggestedMeshResolutionM`, `applyMeshRegistration`, `applyMeshToDem`, `clearMeshLayer` | STL/OBJ import (F-MESHIMPORT): upload/library source → heightmap → registered `MeshImport` stacked layer → optional DEM merge. `autoRegisterMesh` geocodes the filename + runs automatic OSM registration, always handing off to the manual picker |
| `mesh-registration.js` | `openMeshRegistrationModal`, `computeMeshRegistration`, `undoLastMeshPointPair`, `clearMeshPointPairs` | Side-by-side pan/zoom point-pair picker (DEM vs. mesh heightmap) feeding the `/register` affine fit |
| `water-mask.js` | `loadWaterMask`, `renderWaterMask`, `renderEsaLandCover` | Water mask + ESA land cover |
| `city-overlay.js` | `loadCityData`, `renderCityOverlay`, `window.renderCityOnDEM` | OSM building/road/waterway overlay |
| `city-render.js` | `loadCityRaster`, `_clearCityRasterCache` | City rasterization via `/api/cities/raster` |
| `hydrology-overlay.js` | `window.loadHydrology`, `window.clearHydrology`, `window.cancelHydroLoad` | HydroRIVERS depression grid fetch + canvas render |
| `water-hydrology-combined.js` | `loadWaterHydrology`, `clearWaterHydrology` | Unified water + hydrology combined layer; sets `appState.waterHydrologyCanvas` |
| `trails-overlay.js` | `loadTrails`, `renderTrails`, `refreshTrailsCategories`, `clearTrails`, `cancelTrailsLoad` | Ski + hiking trail grids from `/api/terrain/trails`; sets `appState.trailsSourceCanvas` and retains `appState.lastTrailsData` so every display control (category, area fill, colours, difficulty) repaints without refetching. Exposes the difficulty palette as `window.TRAILS_DIFFICULTY_RGB` and fires a `trails-rendered` window event after each repaint |

### `map/` — Map, globe, bbox
| File | Key exports | Purpose |
|------|-------------|---------|
| `map-globe.js` | `initMap`, `initGlobe`, `setTileLayer`, `toggleDemOverlay` | Leaflet 2D map + Three.js globe |
| `bbox-panel.js` | `setBboxInputValues`, `setBboxRectangle`, `initBboxMiniMap`, `syncBboxMiniMap` | Bbox input panel + mini-map. `setBboxInputValues` writes the four coordinate fields, which are display only; `setBboxRectangle` moves `appState.boundingBox`, which is the value every layer fetch actually reads. Anything that changes which area the app is looking at has to call the second one, and the pair should normally be called together |
| `compare-view.js` | `initCompareMode`, `loadCompareRegion` | Side-by-side region comparison |

### `regions/` — Region management
| File | Key exports | Purpose |
|------|-------------|---------|
| `regions.js` | `loadCoordinates`, `selectCoordinate`, `goToEdit` | Region CRUD, sidebar list, selection |
| `region-ui.js` | `renderCoordinatesList`, `populateRegionsTable`, `groupRegionsByContinent`, `setupRegionsTable` | Sidebar views, notes, groups; paginated table (20/page, search filter via `_tablePage`/`_tableSearch`) |
| `regions-import-export.js` | `exportRegionsJson`, `importRegionsJsonFile` | Bulk JSON export/import of saved regions + settings |

### `export/` — 3D export
| File | Key exports | Purpose |
|------|-------------|---------|
| `model-viewer.js` | `initModelViewer`, `previewModelIn3D`, `haversineDiagKm`, `exportPuzzle3MF`, `resetViewerCamera`, `rebuildViewerColors`, `setViewerNormals`, `setViewerAutoRotate` | Three.js terrain preview; orbit/pan/zoom + pinch-zoom; puzzle cut preview; async puzzle export with progress polling |
| `export-handlers.js` | `downloadSTL`, `downloadModel`, `downloadCrossSection` | STL/OBJ/3MF/cross-section downloads |
| `print-scale.js` | `modelScale`, `bboxDiagonalKm`, `formatGroundLength`, `parseBedSize`, `defaultPieceMm`, `piecesNeeded` | Pure ES exports (no window): model scale and vertical exaggeration (port of `city_model.choose_scale`), bed size, puzzle piece grid (port of `puzzle.plan_grid`) |
| `building-heights.js` | `summarizeBuildingHeights`, `heightSourceGroup`, `buildingsWithOverrides`, `hasOverrides` | Pure ES exports: height-source summary, histogram, tallest list; City Model `layer_data` payload with height overrides |

### `ui/` — UI management
| File | Key exports | Purpose |
|------|-------------|---------|
| `view-management.js` | `switchView`, `switchDemSubtab`, `_setSidebarViews` | Tab switching; shows the sidebar's list/table view for a mode. The mode itself belongs to `SidebarPanel.vue`. |
| `app-setup.js` | `setupOpacityControls`, `loadAllLayers`, `saveCurrentRegion` | App init wiring helpers |
| `cache-inventory.js` | `loadCacheInventory` | Cache stats browser (Plotly chart + region table) |
| `presets.js` | `initPresetProfiles`, `applyPreset`, `collectAllSettings`, `applyAllSettings`, `saveNewPreset`, `revertPreset`, `loadSelectedPreset` | Preset save/load/apply; `PRESET_VERSION` migration; `_presetSnapshot` revert; `_migratePreset()` fills missing keys from built-in defaults |
| `workflow-presets.js` | `WORKFLOW_PRESETS`, `applyFields`, `applyWorkflowPreset` | Pure ES exports: City / Mountain / Coast presets applied by setting inputs and firing input+change; returns an undo list (presets.js `window.applyWorkflowPreset` wraps it) |
| `curve-editor-state.js` | `CurveEditorState`, `CURVE_PRESETS` | Curve editor state class + named preset definitions (shared by curve-editor.js and tests) |
| `curve-editor.js` | `initCurveEditor`, `applyCurveTodem`, `interpolateCurve`, `undoCurve` | Elevation curve editor (spline + undo/redo) |
| `keyboard-shortcuts.js` | (no named exports) | Keyboard shortcut event listeners |

### `events/` — Event wiring
| File | Purpose |
|------|---------|
| `event-listeners.js` | Core app event setup |
| `event-listeners-ui.js` | UI button/slider handlers |
| `event-listeners-map.js` | Leaflet map + draw events |
| `event-listeners-export.js` | Export tab button handlers |

## main.js Import Order

The current order in `main.js` (must be preserved — foundation before dependents):
```
core/events → core/api → core/cache → core/ui-helpers → core/state
dem/dem-loader → dem/dem-gridlines → ui/presets → ui/curve-editor-state → ui/curve-editor
layers/city-overlay → layers/city-render → layers/stacked-layers → layers/composite-dem
export/export-handlers → export/model-viewer → map/compare-view
regions/region-ui → regions/regions-import-export → dem/dem-merge → layers/water-mask
layers/hydrology-overlay → layers/water-hydrology-combined
map/map-globe → regions/regions → map/bbox-panel
ui/cache-inventory → ui/app-setup → ui/keyboard-shortcuts
events/event-listeners-map → events/event-listeners-export → events/event-listeners-ui → events/event-listeners
ui/view-management → dem/dem-main → app.js
```

## Standalone page scripts

Not part of `main.js`. Each is loaded by its own template and owns its whole page.

| Script | Page | Purpose |
|--------|------|---------|
| `static/js/reports.js` | `templates/reports.html` (`GET /reports`) | Pipeline results browser: reads `/api/reports/index` and renders the skyline artifacts |

## Notes
- `app.js` is loaded as plain `<script>`, **after** all modules. It is the only non-module file.
- CDN globals (`window.L`, `window.THREE`, `window.Plotly`) are loaded as `<script>` tags before `main.js`.
- Colormaps: `terrain`, `viridis`, `jet`, `rainbow`, `hot`, `gray` — must match `COLORMAPS` in `mapElevationToColor()`.

## Layer Canvas Lifecycle

```mermaid
flowchart TD
    INIT["Page Load"] --> REG["LAYER_CANVAS_IDS registered<br/>(stacked-layers.js init)"]
    REG --> IDLE["All layers inactive"]
    IDLE --> ACT["setStackMode('Dem')"]
    ACT --> GET["_getLayerBuffer('Dem')<br/>→ getElementById('layerDemCanvas')"]
    GET --> RENDER["updateStackedLayers()<br/>→ drawImage to stackViewCanvas"]
    RENDER --> SWITCH{"Mode switch?"}
    SWITCH -->|Yes| FREE["_freeLayerBuffer(old)<br/>canvas.width = canvas.height = 0<br/>(releases GPU backing store)"]
    FREE --> ACT
    SWITCH -->|No| RENDER
```

## Preset Lifecycle

```mermaid
flowchart LR
    INIT2["initPresetProfiles()"] --> LOAD["Load from localStorage"]
    LOAD --> MIG["_migratePreset(preset)<br/>merge with builtInPresets.default<br/>add _version: PRESET_VERSION"]
    MIG --> READY["Presets ready"]
    READY --> SELECT["loadSelectedPreset()"]
    SELECT --> SNAP["_presetSnapshot = collectAllSettings()"]
    SNAP --> APPLY["applyAllSettings(preset)"]
    APPLY --> SHOW["Show #revertPresetBtn"]
    SHOW --> REVERT{"User reverts?"}
    REVERT -->|Yes| RESTORE["applyAllSettings(_presetSnapshot)"]
    RESTORE --> HIDE["Hide button, clear snapshot"]
    REVERT -->|No| SAVE["saveNewPreset()"]
    SAVE --> LS["localStorage ← {settings, _version}"]
```

---

## Function Index

One-liner index. Search by function name — line numbers are omitted because they go stale.
Use grep: `grep -rn "function functionName" app/client/static/js/`.

### app.js — file-top helpers

| Function | Purpose |
|----------|---------|
| `clearLayerCache()` | Reset lastDemData, waterMask, layerBboxes, layerStatus, composite canvases |
| `clearLayerDisplays()` | Clear canvas elements + status indicators |
| `getCurrentBboxObject()` | Return `{N,S,E,W}` from boundingBox or form inputs |
| `isLayerCurrent(layer)` | True if layer bbox matches current bbox |

### dem/dem-loader.js

| Function | Purpose |
|----------|---------|
| `mapElevationToColor(t, cmap)` | 0–1 → RGB array (12 colormaps) |
| `renderSatelliteCanvas(vals,w,h)` | RGB sat pixels → canvas |
| `updateAxesOverlay(N,S,E,W)` | Draw N/S/E/W axis labels |
| `drawColorbar(min,max,cmap)` | Render colorbar legend |
| `drawHistogram(values)` | Elevation histogram + cumulative |
| `applyProjection(srcCanvas, bbox)` | Apply map projection to canvas |
| `enableZoomAndPan(canvas)` | Mouse wheel/drag zoom on DEM canvas |
| `recolorDEM()` | Re-render DEM with current settings |
| `rescaleDEM(vmin, vmax)` | Rescale display |
| `resetRescale()` | Reset to data min/max |

### dem/dem-main.js

| Function | Purpose |
|----------|---------|
| `loadDEM(highRes?)` | Main DEM loader — fetch, render, update state (pass `true` for high-res) |
| `renderDEMCanvas(vals,w,h,cmap,vmin,vmax)` | Render elevation LUT → canvas |
| `loadSatelliteImage()` | Load ESA land cover (classification raster) |
| `loadSatelliteRGBImage()` | Load ESRI satellite imagery tiles |

### dem/dem-merge.js

| Function | Purpose |
|----------|---------|
| `setupMergePanel()` | Wire merge panel events |
| `runMerge(apply)` | POST /api/composite/dem-merge, optionally apply |

### layers/water-mask.js

| Function | Purpose |
|----------|---------|
| `loadWaterMask()` | Fetch /api/terrain/water-mask (cached) |
| `renderWaterMask(data)` | Render water mask canvas |
| `renderEsaLandCover(data)` | Render ESA classification canvas |
| `renderCombinedView()` | Composite DEM+water+landcover |

### layers/city-overlay.js

| Function | Purpose |
|----------|---------|
| `loadCityData()` | POST /api/cities, computeTerrainZ, store osmCityData |
| `clearCityOverlay()` | Remove city overlays from canvases |
| `renderCityOverlay()` | Debounced: paint buildings/roads on stacked + DEM canvases |
| `_drawCityCanvas(ctx,...)` | Core draw: buildings alpha-batched (8), sub-pixel skipped |
| `renderCityOnDEM()` | Paint .city-dem-overlay on #demImage |

### layers/hydrology-overlay.js

| Function | Purpose |
|----------|---------|
| `loadHydrology()` | Fetch /api/terrain/hydrology, render depression grid |
| `clearHydrology()` | Clear canvas + state + emit update |
| `cancelHydroLoad()` | Abort any in-flight hydrology request |

### layers/trails-overlay.js

| Function | Purpose |
|----------|---------|
| `loadTrails({activate})` | Fetch /api/terrain/trails, retain payload, render both categories. `activate` defaults to true and switches the Trails layer on; callers that did not ask to see trails (`loadAllLayers`, `LAYER_AUTOLOAD`) pass false |
| `renderTrails(data?)` | Paint ski and hiking linework to the offscreen canvas; ski wins on overlap. Colours default to cyan/orange but follow the `trailsSkiColor` / `trailsHikingColor` pickers. Interiors of areal features are tinted in the same colour at alpha 46 from the response's area masks, so a ski-area polygon reads as an extent rather than a solid blob. With `trailsColorByDifficulty` on, each ski pixel takes its colour from the response's `ski_difficulty_grid`; an ungraded piste keeps the plain ski colour rather than vanishing. Fires a `trails-rendered` window event when done |
| `_viewSettings()` (private) | Read every Trails Display control out of the DOM in one place, with defaults for the case where the view section has not mounted yet |
| `refreshTrailsCategories()` | Repaint from the retained payload when any Trails Display control changes. Never refetches - one response already carries both categories, both area masks, and the grades |
| `clearTrails()` | Clear canvas + retained payload + emit update |
| `cancelTrailsLoad()` | Abort any in-flight trails request |

### layers/water-hydrology-combined.js

| Function | Purpose |
|----------|---------|
| `loadWaterHydrology()` | Fetch water mask + hydrology in parallel, composite + render combined canvas |
| `clearWaterHydrology()` | Clear combined canvas + `appState.waterHydrologyCanvas` |

### layers/stacked-layers.js

| Function | Purpose |
|----------|---------|
| `updateStackedLayers()` | Render active mode buffer → stackViewCanvas |
| `setStackMode(mode)` | Toggle a layer on or off. Switching one on fetches its data when it has none yet, via the `LAYER_AUTOLOAD` registry (Dem, WaterHydrology, Sat, SatImg, CityRaster, CityOverlay, Trails) |
| `getActiveLayers()` | Copy of the set of layers currently switched on. Read by the layer rack in `LayerViewSection.vue`, which rebuilds on the `layer-stack-changed` window event that `setStackMode` and `moveLayer` dispatch |
| `applyStackedTransform()` | Apply CSS zoom/pan transform |
| `enableStackedZoomPan()` | Wire wheel/drag on stackViewCanvas |
| `drawLayerGrid()` | Coordinate grid overlay. Needs only `currentDemBbox` and the stack rect — it does not depend on the Dem layer being active |

### layers/composite-dem.js

| Function | Purpose |
|----------|---------|
| `computeCompositeDem(opts)` | Add DEM/water/city(buildings+roads+waterways+walls)/landcover/sat/trails contributions — DEM and each city sub-layer independently toggleable |
| `_trailsContribution(demW, demH)` (private) | Nearest-neighbour resample of the retained trails relief onto the DEM grid. Only the linework contributes; the area masks are display-only. Where a piste and a path cross, the deeper cut wins rather than the two summing. Weight defaults to 0, so loading the Trails layer to look at it never silently changes an export |
| `applyCompositeToDem()` | Copy composite into lastDemData.values, and publish the server layer spec on `appState.compositeLayerSpec` |
| `buildCompositeLayerSpec()` | Translate the panel's flat parameters into the server's ordered `MergeLayerSpec` list; returns `{layers, unsupported}` — `unsupported` names channels with no server source yet (land cover, vegetation, trails), for which export falls back to inline values |
| `setupCompositeDemControls()` | Wire all composite sliders + toggles + buttons + split-view button |
| `_drawHistogram(canvas, values)` / `_renderAllHistograms(channels)` | Canvas-drawn per-layer + combined contribution histograms (no chart lib) |

### layers/mesh-layer.js

| Function | Purpose |
|----------|---------|
| `uploadMeshLayer(file)` | POST an STL/OBJ, store `upload_id` on `appState.meshImport` |
| `selectLibraryMeshFile(relPath, filename)` | Use a mesh-library file instead of an upload |
| `computeMeshHeightmap(opts)` | Fetch heightmap for the current DEM bbox, render preview canvas. Defaults `resolutionM` via `suggestedMeshResolutionM` when not given |
| `autoRegisterMesh(opts)` | Geocode the mesh's filename/foldername, run automatic OSM registration + region match/create (`/auto-register`), then `loadDEM` + `computeMeshHeightmap` + always open the manual picker — never auto-accepts the result |
| `suggestedMeshResolutionM(bbox)` | Suggest a heightmap resolution (m/px) targeting ~300px on a bbox's longer side; shared by the UI slider default and `autoRegisterMesh` |
| `applyMeshRegistration(result)` | Called by mesh-registration.js with the `/register` response; renders the `MeshImport` layer canvas, fires `mesh-import-registered` window event |
| `applyMeshToDem(blendWeight)` | Patch `lastDemData.values` with the registered mesh in its footprint (mirrors `applyCompositeToDem`) |
| `clearMeshLayer()` | Reset `appState.meshImport`/`meshSourceCanvas` |

### layers/mesh-registration.js

| Function | Purpose |
|----------|---------|
| `openMeshRegistrationModal()` | Show the picker, render both DEM and mesh heightmap canvases |
| `computeMeshRegistration()` | POST point pairs to `/register`, hand the warped result to `applyMeshRegistration` |
| `undoLastMeshPointPair()` / `clearMeshPointPairs()` | Edit the pending point-pair list |

### layers/stacked-layers.js

| Function | Purpose |
|----------|---------|
| `initModelViewer()` | Three.js scene init |
| `previewModelIn3D()` | Render current DEM in 3D viewer |
| `haversineDiagKm()` | Bbox diagonal in km |
| `exportPuzzle3MF()` | Puzzle piece 3MF export |

### export/export-handlers.js

| Function | Purpose |
|----------|---------|
| `downloadSTL()` | POST /api/export/stl → blob download |
| `downloadModel(format)` | POST /api/export/{format} → download |
| `downloadCrossSection()` | Cross-section OBJ export |
| `generateModelFromTab()` | Trigger server-side generation |

### regions/regions.js + region-ui.js

| Function | Purpose |
|----------|---------|
| `loadCoordinates()` | Fetch regions, draw map boxes |
| `selectCoordinate(i)` | Select + fly to region |
| `goToEdit(i)` | Switch to Edit tab for region |
| `renderCoordinatesList()` | Sidebar list view |
| `groupRegionsByContinent(regions)` | Group by heuristic continent |
| `initRegionNotes()` | Load notes from localStorage |

### regions/regions-import-export.js

| Function | Purpose |
|----------|---------|
| `exportRegionsJson()` | Download all saved regions + settings as JSON file |
| `importRegionsJsonFile(file)` | Import regions from a JSON file blob, creating missing regions via POST |

### ui/presets.js

| Function | Purpose |
|----------|---------|
| `initPresetProfiles()` | Load presets from localStorage |
| `applyPreset(preset)` | Apply preset to all form controls |
| `collectAllSettings()` | Return full settings object |
| `applyAllSettings(s)` | Apply settings object to form |

### ui/curve-editor.js

| Function | Purpose |
|----------|---------|
| `initCurveEditor()` | Setup canvas + state |
| `applyCurveTodem()` | Apply + re-render |
| `interpolateCurve(x)` | Monotone cubic spline at x∈[0,1] |
| `undoCurve()` / `redoCurve()` | Undo/redo curve edits |

### ui/view-management.js

| Function | Purpose |
|----------|---------|
| `switchView(view)` | Switch Explore/Edit/Extrude tab |
| `switchDemSubtab(tab)` | Switch DEM sub-tab |
| `_setSidebarViews(state)` | Show the list or table view for a sidebar mode |

### map/map-globe.js

| Function | Purpose |
|----------|---------|
| `initMap()` | Leaflet map + draw control |
| `initGlobe()` | Three.js globe |
| `setTileLayer(key)` | Switch tile layer |
| `toggleDemOverlay(show)` | Terrain overlay on map |

### map/compare-view.js

| Function | Purpose |
|----------|---------|
| `initCompareMode()` | Side-by-side compare panel |
| `loadCompareRegion(side)` | Load DEM for left/right panel |

### reports.js — pipeline results browser (standalone)

One IIFE on `window.reportsPage`; state is `{data, selection, tab, quality, search, heights}`.

| Function | Purpose |
|----------|---------|
| `load()` | Fetch `/api/reports/index` and rerender everything |
| `currentRegions()` / `currentRows()` | Apply the sidebar search and quality filters |
| `renderTotals()` / `renderSidebar()` | Header chips; region, height-report and trace lists |
| `qbar(qual)` / `detClass(n)` | Quality bar markup; the warn/caution class for a detection count |
| `seedTable(rows, withRegion)` | Per-seed metrics table, one row per seed |
| `renderOverview()` / `renderRegionOverview(pane)` | All-regions cards vs. one region's screening map, web sources and seeds |
| `loadHeights(dir)` | Fetch `/api/reports/heights/{dir}` for the height summary line |
| `seedHead(row)` / `figure(row, label, url)` | Seed block header; one captioned, lightbox-able image |
| `renderPanoramas()` / `renderViews()` | The panorama lanes and the street-view grid |
| `renderActive()` / `setTab(tab)` | Draw the visible tab only |
| `selectRegion(dir)` / `selectAll()` | Sidebar selection |
| `openFile(url, label)` | Point the Rendered report iframe at an artifact |
| `openLightbox(src, cap)` | Full-size image overlay |
| `init()` | Wire the tabs, filters, search and lightbox, then `load()` |
