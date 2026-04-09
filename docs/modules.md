# JS Module Map — strm2stl

All modules in `app/client/static/js/modules/`, imported by `main.js` in dependency order.
Modules expose functions via `window.*` — they do **not** import each other.

## Subdirectory Groups

### `core/` — Foundation & utilities
| File | Key exports | Purpose |
|------|-------------|---------|
| `state.js` | `window.appState` | Proxy-based reactive state with `.on()/.set()/.emit()` |
| `events.js` | `window.events`, `window.EV` | Event bus + EV constants |
| `api.js` | `window.api.*` | All fetch helpers (regions, dem, export, cities, cache, settings) |
| `ui-helpers.js` | `showToast`, `showLoading`, `setLayerStatus` | Toast, spinners, layer status UI |
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
| `stacked-layers.js` | `updateStackedLayers`, `setStackMode`, `applyStackedTransform` | Single-canvas stacked view, zoom/pan |
| `composite-dem.js` | `computeCompositeDem`, `setupCompositeDemControls` | Additive height contributions + ML feature arrays |
| `water-mask.js` | `loadWaterMask`, `renderWaterMask`, `renderEsaLandCover` | Water mask + ESA land cover |
| `city-overlay.js` | `loadCityData`, `renderCityOverlay`, `window.renderCityOnDEM` | OSM building/road/waterway overlay |
| `city-render.js` | `loadCityRaster`, `_clearCityRasterCache` | City rasterization via /api/composite/city-raster |

### `map/` — Map, globe, bbox
| File | Key exports | Purpose |
|------|-------------|---------|
| `map-globe.js` | `initMap`, `initGlobe`, `setTileLayer`, `toggleDemOverlay` | Leaflet 2D map + Three.js globe |
| `bbox-panel.js` | `setBboxInputValues`, `initBboxMiniMap`, `syncBboxMiniMap` | Bbox input panel + mini-map |
| `compare-view.js` | `initCompareMode`, `loadCompareRegion` | Side-by-side region comparison |

### `regions/` — Region management
| File | Key exports | Purpose |
|------|-------------|---------|
| `regions.js` | `loadCoordinates`, `selectCoordinate`, `goToEdit` | Region CRUD, sidebar list, selection |
| `region-ui.js` | `renderCoordinatesList`, `populateRegionsTable`, `groupRegionsByContinent` | Sidebar views, notes, groups |

### `export/` — 3D export
| File | Key exports | Purpose |
|------|-------------|---------|
| `model-viewer.js` | `initModelViewer`, `previewModelIn3D`, `haversineDiagKm`, `exportPuzzle3MF` | Three.js terrain preview + puzzle export |
| `export-handlers.js` | `downloadSTL`, `downloadModel`, `downloadCrossSection` | STL/OBJ/3MF/cross-section downloads |

### `ui/` — UI management
| File | Key exports | Purpose |
|------|-------------|---------|
| `view-management.js` | `switchView`, `switchDemSubtab`, `cycleSidebarState` | Tab switching + sidebar state machine |
| `app-setup.js` | `setupOpacityControls`, `loadAllLayers`, `saveCurrentRegion` | App init wiring helpers |
| `presets.js` | `initPresetProfiles`, `applyPreset`, `collectAllSettings` | Preset save/load/apply |
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
dem/dem-loader → dem/dem-gridlines → ui/presets → ui/curve-editor
layers/city-overlay → layers/city-render → layers/stacked-layers → layers/composite-dem
export/export-handlers → export/model-viewer → map/compare-view
regions/region-ui → dem/dem-merge → layers/water-mask
map/map-globe → regions/regions → map/bbox-panel
ui/app-setup → ui/keyboard-shortcuts
events/event-listeners-map → events/event-listeners-export → events/event-listeners-ui → events/event-listeners
ui/view-management → dem/dem-main → app.js
```

## Notes
- `app.js` is loaded as plain `<script>`, **after** all modules. It is the only non-module file.
- CDN globals (`window.L`, `window.THREE`, `window.Plotly`) are loaded as `<script>` tags before `main.js`.
- Colormaps: `terrain`, `viridis`, `jet`, `rainbow`, `hot`, `gray` — must match `COLORMAPS` in `mapElevationToColor()`.
