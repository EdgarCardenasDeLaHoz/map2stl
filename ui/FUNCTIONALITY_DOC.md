# UI Functionality Reference

_Last updated: 2026-03-18_

This document is a high-level map of all implemented features in `strm2stl/ui/templates/index.html`.
Line numbers are approximate — the file grows over time.

---

## Implemented Features

### Navigation & Views
| Feature | JS Function | Notes |
|---------|-------------|-------|
| 3-tab navigation (Explore/Edit/Extrude) | `switchView(view)` | State persists across switches |
| Settings panel collapse/expand | `toggleSettingsPanel()` | Collapses to 0 width; vertical re-open tab stays visible |
| Layer sub-tabs (Layers / Compare) | `switchDemSubtab(subtab)` | Horizontal strip at top of settings panel |

### Explore View
| Feature | JS Function | Notes |
|---------|-------------|-------|
| Leaflet 2D map | `initMap()` | 7 base tile options |
| Terrain hillshade overlay | `toggleTerrainOverlay()` | Optional; opacity slider |
| Three.js globe | `initGlobe()` / `animateGlobe()` | Region markers, orbit controls |
| Sidebar 3-state | `cycleSidebarState()` | Compact → expanded table → hidden |
| Sidebar visibility toggle | `toggleBboxLayerVisibility()` | Shows/hides bbox rectangles + edit markers |
| Expanded sidebar table | `renderSidebarTable()` | N/S/E/W, dim, Edit + Map actions |

### Edit View — Layer Canvas
| Feature | JS Function | Notes |
|---------|-------------|-------|
| DEM rendering | `renderDEMCanvas()` / `recolorDEM()` | Colormaps, elevation curves |
| Water mask / ESA land cover | `renderWaterMask()` / `renderEsaLandCover()` | Combined from `/api/terrain/water-mask` |
| Satellite imagery | `renderSatelliteCanvas()` | ESA WorldCover fallback |
| Stacked layer view | `updateStackedLayers()` | Napari-style compositing |
| Gridline overlay | `drawGridlinesOverlay()` | Projection-aware (Mercator, sinusoidal, etc.) |
| Map projection | `applyProjection()` | None / Cosine / Mercator / Lambert / Sinusoidal |
| Zoom & pan | `enableStackedZoomPan()` | Wheel/pinch zoom, drag pan, double-click reset |
| Hover tooltip | `setupHoverTooltip()` | Elevation + lat/lon under cursor |
| Colorbar | `drawColorbar()` | Inline in bbox row; reflects active colormap |

### Edit View — Bbox Controls
| Feature | JS Function / Element | Notes |
|---------|----------------------|-------|
| N/S/E/W inputs + Reload | `#bboxReloadBtn` | Validates, clamps, reloads all layers |
| Inline mini-map | `toggleBboxMiniMap()` / `initBboxMiniMap()` | Leaflet rect with drag handles |
| Save bbox to backend | `#saveBboxBtn` | PUT `/api/regions/{name}`; updates local cache |
| Elevation range display | `#bboxElevRange` | Shows min/max after DEM loads |

### Settings — Histogram & Curves
| Feature | JS Function | Notes |
|---------|-------------|-------|
| Elevation histogram | `drawHistogram()` | 80 px height, cumulative mode |
| Curve editor | `initCurveEditor()` / `drawCurve()` | Drag control points; presets |
| Curve presets | `setCurvePreset()` | Linear, Peaks, Depths, S-Curve |
| Sea level buffer | `#seaLevelBufferBtn` | Compresses ocean depths, adds shelf |

### Settings — Map Display
| Feature | Element/Function | Notes |
|---------|-----------------|-------|
| Map style | `#mapTileLayer` | 7 providers |
| Terrain overlay | `#showTerrainOverlay` | Hillshade; opacity sub-row shown when enabled |
| Show gridlines | `#showGridlines` | Toggle lat/lon grid on DEM canvas |
| Gridline count | `#gridlineCount` | 3 / 5 / 7 / 10 lines |
| Auto-reload layers | `#autoReloadLayers` | Reloads on bbox/settings change |
| Map projection | `#paramProjection` | Client-side only; re-renders all layers |

### Region Management
| Feature | JS Function | Notes |
|---------|-------------|-------|
| Load / save / delete presets | `initPresetProfiles()` | 5 built-in + custom (localStorage) |
| Favorites | `toggleFavorite()` | Star icon, localStorage |
| Region notes | `showNotesModal()` / `saveRegionNotes()` | Modal, localStorage |
| Compare mode | `initCompareMode()` | Inline side-by-side, independent settings |
| Cache management | `clearClientCache()` / `clearServerCache()` | Client + server-side clear |
| Keyboard shortcuts | `setupKeyboardShortcuts()` | Ctrl+1–4 tabs, Ctrl+S, Ctrl+R, Arrows |

---

## Dead Code Removed (Historical)

| Function/Element | Reason |
|-----------------|--------|
| `renderLanduseCanvas()` | Never called; replaced by `renderEsaLandCover()` |
| `toggleCoordsTable()` / `populateCoordsTable()` | Button `#toggleCoords` never existed |
| `drawZoomedAndPannedCanvas()` | Empty function; CSS transforms used instead |
| `#coordsTable` CSS | Orphaned styles |
| `.dem-subtab` CSS + JS | Replaced by `.layer-tab` |

---

## Architecture Notes

- **Split-file SPA:** HTML/CSS/JS are separate files (`templates/index.html` ~1 151 lines, `static/css/app.css` ~2 879 lines, `static/js/app.js` ~8 183 lines). Refactored from original 9 700-line single `index.html` in 2026-03-17 session.
- **Three bbox state variables:** `boundingBox` (Leaflet draw bounds), `selectedRegion` (saved region object), `currentDemBbox` (plain object after DEM loads). All three are updated together in `_onBboxMiniMouseUp()` and the Reload handler.
- **Layer cache:** `lastDemData`, `lastWaterMaskData`, `lastRawDemData` are nulled by `clearLayerCache()` on any bbox change.
- **Projection:** `applyProjection(srcCanvas, bbox)` is called after every render. Adding a new projection = one `if` branch here + one `<option>` in `#paramProjection`.
- **API routes:** Backend uses both legacy routes (`/api/preview_dem`, `/api/water_mask`) and new typed routes (`/api/terrain/dem`, `/api/terrain/water-mask`). app.js primarily calls the new routes.
- **Extracted modules:** `city-overlay.js` and `stacked-layers.js` live in `static/js/modules/`, loaded as plain `<script>` tags before `app.js`. Functions exposed on `window`; shared state via `window.appState`.
- **CSS variables:** All colours and the panel width are defined as `--bg-dark`, `--bg-mid`, `--bg-light`, `--border`, `--border-light`, `--text-dim`, `--text-muted`, `--panel-width` in the `:root` block in `app.css`.

---

## Completed Refactoring (2026-03-18)

### Settings Panel
| Fix | Detail |
|-----|--------|
| "Map Display" section removed | Map tile/terrain overlay controls hidden; DEM-relevant controls (gridlines, projection, auto-reload) kept inline |
| "Parameter Presets" renamed + collapsed | Was "Preset Profiles"; now collapsed by default with tooltip |
| Tooltips on all controls | Added `title="..."` to all Extrude tab inputs/buttons, Puzzle controls, City Load/Clear |
| "Visualization & Display" reordered | Moved above "Histogram & Curves" section |
| Auto-rescale on by default | `#autoRescale` checkbox has `checked` attribute |
| Settings panel resize fixed | Resize `mouseup` now calls `updateStackedLayers()` so canvas reflows |

### Bug Fixes
| Bug | Fix |
|-----|-----|
| OBJ/3MF export 404 | `downloadModel()` now calls `/api/export/${format}` (was `/api/generate_${format}`) |
| Compare view broken | `loadCompareRegion()` now renders `dem_values` client-side (was looking for `dem_image` which API no longer returns) |
| Merge panel broken | `runMerge()` now renders merged DEM via `renderDEMCanvas()` → `applyProjection()` (was calling undefined `renderDEM()`) |

### Elevation Curve Editor
| Fix | Detail |
|-----|--------|
| Min/max rescale shifts control points | Added `curveDataVmin/Vmax` — stable reference captured at load time |
| Accidental point creation | Hit radius increased; click-to-add no longer fires after a drag |
| Delete interaction | Right-click on point deletes it; points drawn larger (8px) with × hint |
| Sea level point auto-inserted | Inserted when DEM loads with sub-zero elevations |
| Canvas tooltip | Explains left-click=add, drag=move, right-click=delete |

### JS Module Extractions
| Module | Contents |
|--------|---------|
| `modules/city-overlay.js` | `loadCityData`, `renderCityOverlay`, `clearCityOverlay`, `_updateCityLayerCount` |
| `modules/stacked-layers.js` | `updateStackedLayers`, `applyStackedTransform`, `enableStackedZoomPan`, `drawLayerGrid`, `updateLayerAxisLabels`, `drawGridOverlay` |

### CSS / State
| Change | Detail |
|--------|--------|
| CSS variables | 8 custom properties in `:root`; all hardcoded colour hex values replaced |
| `state.js` updated | Added `currentDemBbox`, `layerBboxes`, `layerStatus`, `activeDemSubtab`, `osmCityData`, all `cache.*` fields |
| Dead `osmCityData` local removed | `let osmCityData = null` in app.js was orphaned after module extraction |

---

## Completed Features & Fixes (Sessions 13–15, 2026-03)

### Bug Fixes
| ID | Fix |
|----|-----|
| BUG1 | Cache status label: `data.total_files` → `data.total_cached_files` in `fetchServerCacheStatus()` |
| BUG2 | Bbox inputs empty on Edit tab switch: `setBboxInputValues()` called immediately in `switchView('dem')` when `selectedRegion` exists |
| BUG3 | `.model-layout` wasn't filling container: added `flex: 1` to `.model-layout` |
| BUG4 | Export buttons enabled before generation: all export buttons disabled by default; enabled only after `generateModel()` succeeds |

### UX Improvements
| ID | Feature |
|----|---------|
| UX1 | DEM empty state: `#demEmptyState` + `_setDemEmptyState()` shown when `lastDemData` is null |
| UX2 | Extrude empty state: `#modelEmptyState` centered overlay in 3D viewport |
| UX3 | Sidebar auto-collapses when switching to Extrude tab; restored on other tabs |
| UX4 | Region hover affordance: left border accent + color transition on `.coordinate-item` |
| UX5 | Continent headers collapsible in sidebar with count badge (already existed, verified) |
| UX6 | Resolution/Water/Land Cover settings sections collapsed by default (already existed, verified) |
| UX7 | DEM subtab status dots: colored indicators (gray/orange-pulse/green/red) on layer buttons |
| UX8 | Keyboard navigation: `tabindex="0"` + `role="option"` on region items; Arrow Up/Down focus |
| UX9 | Loading spinners on DEM load and model generation via `showLoading`/`hideLoading` |

### Layout & Visual Polish
| ID | Fix |
|----|-----|
| LP1 | Model sidebar `min-width: 280px` |
| LP2 | Edit markers hide when bbox < 40px diagonal on screen (`_updateEditMarkerVisibility` on `zoomend`/`moveend`) |
| LP3 | Colorbar height increased to 26px |
| LP4 | Curve editor canvas height increased from 150px → 220px |

### City Overlay Performance (PERF1–6A)
| ID | Optimization |
|----|-------------|
| PERF1 | Shared `_pt` object in `_buildGeoToPx` — eliminates per-vertex `[x,y]` array allocation |
| PERF2 | `invZ` removed from cache key — cache survives zoom animation; only misses on zoom settle |
| PERF3 | Draw to offscreen first, blit to visible — removes GPU readback |
| PERF4 | Pre-baked `Float32Array` per feature (`feat._px`) — zero projection math per frame |
| PERF5 | Viewport culling for roads/waterways using `feat._bbox`; x+y sub-pixel check for buildings |
| PERF6A | Per-layer OffscreenCanvas (`_buildingsOffscreen`, `_roadsOffscreen`, `_waterwaysOffscreen`) — toggle one layer re-renders only that layer |

### City Features
| ID | Feature |
|----|---------|
| CITY1 | City heights raster layer: `POST /api/cities/raster` burns building heights + road/waterway depressions onto a DEM-sized grid using rasterio; exposed as "City Heights" layer in stacked view |
| CITY2 | Cities controls merged into settings panel collapsible section — removed dedicated subtab |

### Architecture
| ID | Change |
|----|--------|
| ARCH1 | `modules/state.js` Proxy-based reactive `appState` — loaded before other modules; subscribed in city-overlay.js and stacked-layers.js |
| IMP1 | API key moved from `config.json` to `OPENTOPO_API_KEY` env var; `config.json` added to `.gitignore` |
| IMP2 | Test suite rewritten: `httpx.AsyncClient` + `ASGITransport(app=app)` pattern for all API tests |
| IMP3 | PERF5 viewport culling (see above) |
| IMP6 | Stale `.gitmodules` in strm2stl repo removed |
| IMP7 | `Code/cache/` stray duplicate directory deleted |
| IMP8 | Duplicate `get_open_edges_old` functions in `numpy2stl/solid.py` deleted |
| IMP9 | `location_picker.py` now emits `DeprecationWarning` at import time |
| IMP10 | `Code/requirements.txt` deleted (duplicate); `strm2stl/requirements.txt` rewritten as clean UTF-8 |

### New Features
| ID | Feature |
|----|---------|
| P11 | Region thumbnail previews: DEM canvas captured as 48×30 JPEG after load, stored in `regionThumbnails` (localStorage key `strm2stl_thumbs`), shown as `<img class="coordinate-item-thumb">` in sidebar list |
| OSM edge polygon fix | Coordinate clamping removed from `geoToPx` — canvas `clip()` handles bounds |
| Projection-aware city overlay | `_buildGeoToPx()` reads `paramProjection`, applies mercator/cosine/lambert/sinusoidal |
| Merge panel auto-populate | `_syncMergeFromCurrentLayers()` pre-populates merge panel from current DEM/water/sat layers |
