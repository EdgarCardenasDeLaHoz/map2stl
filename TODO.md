# TODO — strm2stl

> See CLAUDE.md for full architecture. See ui/FUNCTIONALITY_DOC.md for completed feature history.

---

## Open Items

### UX

#### [x] UX10. Onboarding for new users
Workflow stepper bar (`#workflowHint`) shows steps 1–3 with done/active/pending states. Tab badges show ✓ when each step completes. Hidden once all three steps are done. Implemented via `_updateWorkflowStepper()`.

---

### Frontend

#### [x] IMP4. Extract `dem-loader.js` module
`modules/dem-loader.js` now contains all DEM/canvas helpers (~670 lines, removed from app.js): `hslToRgb`, `mapElevationToColor`, `renderSatelliteCanvas`, `updateAxesOverlay`, `drawColorbar`, `drawHistogram`, `applyProjection`, `enableZoomAndPan`, `drawGridlinesOverlay`, `recolorDEM`, `rescaleDEM`, `resetRescale`. `window.renderDEMCanvas` stays in app.js (writes closure `lastDemData`/`addCurvePoint`) but is exposed on `window` for module access. `window.loadDEM` stays in app.js (too many closure dependencies).

#### [x] IMP5. Unify appState — remove dual state from app.js
Added `layerBboxes`, `layerStatus` (shared references — property mutations auto-visible), `originalDemValues`, `curveDataVmin`, `curveDataVmax`, `curvePoints` to `window.appState`. Mirrored all write sites (reassignments) in app.js. Exposed `_setDemEmptyState` and `_updateWorkflowStepper` as `window.appState` callbacks. IMP4 remainder (`renderDEMCanvas`, `recolorDEM`, `rescaleDEM`, `resetRescale`) now extractable; `window.loadDEM` remains in app.js (calls too many closure-only functions).

#### [x] IMP11. Extract remaining JS modules — Phase 1 (quick wins, no closure deps)
- [x] `modules/presets.js` — `applyPreset`, `collectAllSettings`, `saveRegionSettings`, `loadAndApplyRegionSettings`, `initPresetProfiles`, `setupPresetEventListeners`, `updatePresetSelect`, `saveNewPreset`, `deleteSelectedPreset`.
- [x] `modules/curve-editor.js` — all curve functions: `initCurveEditor`, `setupCurveEventListeners`, `setCurvePreset`, `addCurvePoint`, `removeCurvePointNear`, `findCurvePointNear`, `drawCurve`, `applyCurveTodem`, `applyCurveTodemSilent`, `interpolateCurve`, `resetDemToOriginal`, plus undo/redo stack. Uses `window.appState._onDemLoaded` callback for DEM load events.
- [x] `modules/cache.js` — `waterMaskCache` LRU object moved from app.js file-top to module scope; `window.waterMaskCache` exposed.
- [x] Finish `modules/city-overlay.js` — `loadCityRaster`, `_updateCitiesLoadButton`, `_setupCityRasterLayer`, `_clearCityRasterCache` added; wired in city-overlay.js DOMContentLoaded.

#### [x] IMP11B. Extract remaining JS modules — Phase 2 (light refactoring)
- [x] `modules/export-handlers.js` — `generateModelFromTab`, `downloadSTL`, `downloadModel`, `downloadCrossSection`, `_setExportButtonsEnabled`; reads/writes `window.appState.generatedModelData`.
- [x] `modules/model-viewer.js` — `initModelViewer`, `createTerrainMesh`, `previewModelIn3D`, `haversineDiagKm`, `updatePuzzlePreview`, `exportPuzzle3MF`; Three.js state in module scope; `terrainMesh` on `window.appState.terrainMesh`; autoRotate via `window.setViewerAutoRotate`.
- [x] `modules/compare-view.js` — `initCompareMode`, `renderCompareLayer`, `updateCompareCanvases`, `loadCompareRegion`, `applyCompareColormap`, `updateCompareExagLabel`, `updateRegionParamsTable`, `applyRegionParams`; coordinatesData via `window.getCoordinatesData()`.
- [x] `modules/region-ui.js` — `detectContinent`, `groupRegionsByContinent`, `renderCoordinatesList`, `populateRegionsTable`, `loadRegionFromTable`, `viewRegionOnMap`, `setupRegionsTable`, `initRegionNotes`, `showNotesModal`, `hideNotesModal`, `saveRegionNotes`, `initRegionThumbnails`, `saveRegionThumbnail`; coordinatesData via `window.getCoordinatesData()`, sidebarState via `window.getSidebarState()`.

#### [ ] IMP11C. Extract remaining JS modules — Phase 3 (requires appState migration)
- [ ] `modules/water-mask.js` (~455 lines) — `loadWaterMask`, `renderWaterMask`, `renderEsaLandCover`, `renderCombinedView`, `previewWaterSubtract`, `applyWaterSubtract`. Needs `cache.js` (waterMaskCache) and `window.renderDEMCanvas`.
- [ ] `modules/dem-merge.js` (~508 lines) — merge panel; needs `renderDEMCanvas` fully on window and complex internal state sorted.

#### [x] FA2. Remove duplicate functions between app.js and modules
All listed functions are now in exactly one location. `mapElevationToColor`, `hslToRgb`, `drawColorbar`, `drawHistogram`, `renderSatelliteCanvas`, `updateAxesOverlay`, `applyProjection`, `enableZoomAndPan`, `drawGridlinesOverlay`, `recolorDEM`, `rescaleDEM`, `resetRescale` are exclusively in `dem-loader.js`. `renderDEMCanvas` is exclusively in `app.js` (exposed as `window.renderDEMCanvas`). No duplicates remain.

#### [~] FA3. Centralize global state (long-term)
Key DEM/curve state vars now mirrored to `window.appState` (IMP5). Remaining ~20 closure vars (`boundingBox`, `coordinatesData`, `drawnItems`, etc.) still closure-only — add when extracting their respective modules (IMP11).

#### [x] Dead code removed (session 17)
Deleted 4 unreferenced functions: `editRegionCell` (inline table editing, no caller), `loadHighResDEM` (wrapper around `loadDEM(true)`, no caller), `addGridLines` + `toggleGridLines` (old pixel-grid API, superseded by `drawGridlinesOverlay`). Also removed stale `components/dem-viewer.js` comment (file never existed).

---

### Frontend Architecture

#### [ ] ARCH2. Extract cohesive sections from app.js
Continue the established pattern. Priority: `dem-loader.js`, `water-mask.js`, `regions.js`, `export.js`, `curve-editor.js`. Each module subscribes to `appState` rather than being called directly.

#### [x] ARCH3. Centralize API calls into `api.js`
`modules/api.js` exposes `window.api` with `api.regions.*`, `api.dem.*`, `api.export.*`, `api.cities.*`, `api.cache.*`, `api.settings.*`, `api.misc.*`. All raw `fetch()` calls in app.js migrated to `window.api`. `fetchWithErrorHandling` removed (was dead code). Only two native `fetch()` remain: internal `api._fetch` wrapper and static file load in `toggleDemOverlay`.

#### [ ] ARCH4. Add Vite as bundler
`npm create vite@latest -- --template vanilla`. Converts module system to real ESM. Keep FastAPI serving; Vite proxies in dev, builds `/dist` for production.

#### [ ] ARCH5. Unit tests for pure functions (requires ARCH4)
Add Vitest once Vite is set up. Test: `interpolateCurve`, `mapElevationToColor`, `detectContinent`, `haversineDiagKm`, bbox math, cache key generation.

---

### Features

#### [x] P10. Undo/redo for curve editor
`_curveHistory` stack (max 30). `_pushCurveHistory()` called before add/remove/drag/preset/sea-level mutations. `undoCurve()` / `redoCurve()` wired to Undo/Redo buttons and Ctrl+Z / Ctrl+Y keyboard shortcuts.

#### [x] P12. Quick-preview in Explore tab
Leaflet tooltip on `mouseover` of each region bbox rectangle. Tooltip shows the 48×30 thumbnail from `regionThumbnails` (captured by P11) plus the region label. Styled with `.leaflet-tooltip.region-thumb-tooltip` dark theme.

---

### Performance

#### [~] PERF6 Part B. Web Worker for city rendering (depends on Part A — done)
Transfer each layer's OffscreenCanvas to a Worker via `transferControlToOffscreen()`. Worker holds pre-baked `Float32Array` buffers (structured-cloned once at load) and renders asynchronously. Main thread posts `{type:'render'}`, receives `ImageBitmap` back. Total main-thread cost per frame: < 1 ms.

---

## Completed

See [ui/FUNCTIONALITY_DOC.md](ui/FUNCTIONALITY_DOC.md) for full feature history.

### Recent (Sessions 13–15)
- All BUG1–4, UX1–9, LP1–4, PERF1–6A, ARCH1 fixed/implemented
- CITY1 (city heights raster layer), CITY2 (cities controls merged into settings)
- IMP1–IMP10: API key security, test rewrites, viewport culling, .gitmodules cleanup, dead code deletion, requirements.txt fix, deprecation shims
- P11: Region thumbnail previews in sidebar (capture DEM canvas → localStorage → `<img>` in sidebar)

### Features (P1–P9)
P1 physical dimensions, P2 bed optimizer, P3 contour lines, P4 base label engraving, P5 mesh repair, P7 cross-section export, P8 flat water cap, P9 region label editor

### Backend (Sessions 1–12)
Full monolith split: `server.py` + `schemas.py` + `config.py` + `core/` (dem, export, cache, db, osm, cities_3d) + `routers/` (terrain, regions, export, cities, cache, settings). SQLite migration. Pydantic V2. Blocking ops in `run_in_executor`.

### Frontend Audits (Sessions 6–8)
Dead modules removed, AbortController, XSS fixes, localStorage key prefixing, curve editor ResizeObserver debounce, DocumentFragment batching.
