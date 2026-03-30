# Function Index — strm2stl Frontend

One-liner index. Search by function name — line numbers are omitted because they go stale.
Modules live in `ui/static/js/modules/` (subdirs). Use grep: `grep -rn "function functionName"`.

## app.js — file-top helpers

| Function | Purpose |
|----------|---------|
| `clearLayerCache()` | Reset lastDemData, waterMask, ESA, layerBboxes, layerStatus |
| `clearLayerDisplays()` | Clear canvas elements + status indicators |
| `getCurrentBboxObject()` | Return `{N,S,E,W}` from boundingBox or form inputs |
| `isLayerCurrent(layer)` | True if layer bbox matches current bbox |
| `showToast(msg, type, ms)` | Toast notification |
| `setLayerStatus(layer, status)` | Update layerStatus + DOM badge |
| `showLoading(el)` / `hideLoading(el)` | Spinner overlay |
| `waterMaskCache` | LRU object `{get,set,has,generateKey,getStats,clear}` |
| `preloadAllRegions()` | Background DEM preload for all regions |

## app.js — Map & Globe (DOMContentLoaded)

| Function | Purpose |
|----------|---------|
| `setTileLayer(key)` | Switch Leaflet tile layer |
| `toggleDemOverlay(show)` | Load + render terrain overlay on map |
| `initMap()` | Init Leaflet map + draw control |
| `initMapGrid()` / `updateMapGrid()` | SVG grid overlay |
| `toggleMapGrid(show)` | Show/hide map grid |
| `initGlobe()` | Three.js globe init |
| `animateGlobe()` | Globe RAF rotation loop |

## app.js — Regions

| Function | Purpose |
|----------|---------|
| `loadCoordinates()` | Fetch /api/coordinates, draw map rectangles |
| `selectCoordinate(index)` | Select region, fly map, update UI |
| `goToEdit(index)` | Switch to Edit tab for region |
| `saveRegionSettings()` | PUT /api/regions/{name}/settings |
| `loadAndApplyRegionSettings(name)` | GET settings + apply to form |
| `renderCoordinatesList()` | Render sidebar list |
| `populateRegionsTable()` | Render sidebar table |

## app.js — Curve Editor

| Function | Purpose |
|----------|---------|
| `initCurveEditor()` | Setup curve canvas + state |
| `setCurvePreset(name)` | Apply named preset shape |
| `addCurvePoint(x, y)` | Add control point |
| `drawCurve()` | Re-render curve canvas |
| `applyCurveTodem()` | Apply curve + re-render DEM |
| `interpolateCurve(x)` | Monotone cubic spline evaluation |
| `resetDemToOriginal()` | Restore originalDemValues |

## app.js — DEM Rendering

| Function | Purpose |
|----------|---------|
| `setupAutoReload()` | Auto-reload timer |
| `loadAllLayers()` | Load DEM + water + land cover |
| `setupOpacityControls()` | Wire #activeLayerOpacity slider |
| `recolorDEM()` | Re-render with current colormap |
| `rescaleDEM(vmin, vmax)` | Rescale display range |
| `renderDEMCanvas(vals,w,h,cmap,vmin,vmax)` | Render elevation LUT → canvas |
| `setupHoverTooltip(canvas)` | Elevation tooltip on mouse-move |

## modules/dem/dem-loader.js

| Function | Purpose |
|----------|---------|
| `hslToRgb(h,s,l)` | HSL → RGB |
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

## modules/dem/dem-main.js

| Function | Purpose |
|----------|---------|
| `loadDEM(highRes?)` | Main DEM loader — fetch, render, update state |
| `loadHighResDEM()` | Fetch higher-res version |
| `setupHoverTooltip(canvas)` | Elevation mouse tooltip |

## modules/layers/water-mask.js

| Function | Purpose |
|----------|---------|
| `loadWaterMask()` | Fetch /api/terrain/water-mask (cached) |
| `renderWaterMask(data)` | Render water mask canvas |
| `renderEsaLandCover(data)` | Render ESA classification canvas |
| `renderCombinedView()` | Composite DEM+water+landcover |
| `previewWaterSubtract()` | Show DEM with water subtracted |
| `applyWaterSubtract()` | Apply subtraction to DEM values |

## modules/layers/city-overlay.js

| Function | Purpose |
|----------|---------|
| `loadCityData()` | POST /api/cities, computeTerrainZ, store osmCityData |
| `clearCityOverlay()` | Remove city overlays from canvases |
| `renderCityOverlay()` | Debounced: paint buildings/roads on stacked + DEM canvases |
| `_drawCityCanvas(ctx,...)` | Core draw: buildings alpha-batched (8), sub-pixel skipped |
| `_computeTerrainZ(geojson,dem)` | Sample DEM for feature terrain_z |
| `window.renderCityOnDEM()` | Paint .city-dem-overlay on #demImage |

## modules/layers/stacked-layers.js

| Function | Purpose |
|----------|---------|
| `updateStackedLayers()` | Render active mode buffer → stackViewCanvas |
| `setStackMode(mode)` | Switch active layer mode |
| `applyStackedTransform()` | Apply CSS zoom/pan transform |
| `enableStackedZoomPan()` | Wire wheel/drag on stackViewCanvas |
| `drawLayerGrid()` | Coordinate grid overlay |

## modules/layers/composite-dem.js

| Function | Purpose |
|----------|---------|
| `computeCompositeDem(opts)` | Add water/city/landcover/sat contributions to DEM |
| `applyCompositeToDem()` | Copy composite into lastDemData.values |
| `setupCompositeDemControls()` | Wire all composite sliders + buttons |

## modules/export/model-viewer.js

| Function | Purpose |
|----------|---------|
| `initModelViewer()` | Three.js scene init |
| `createTerrainMesh(vals,w,h,exag)` | PlaneGeometry from DEM values |
| `previewModelIn3D()` | Render current DEM in 3D viewer |
| `haversineDiagKm()` | Bbox diagonal in km |
| `exportPuzzle3MF()` | Puzzle piece 3MF export |

## modules/export/export-handlers.js

| Function | Purpose |
|----------|---------|
| `downloadSTL()` | POST /api/export/stl → blob download |
| `downloadModel(format)` | POST /api/export/{format} → download |
| `downloadCrossSection()` | Cross-section OBJ export |
| `generateModelFromTab()` | Trigger server-side generation |

## modules/regions/regions.js + region-ui.js

| Function | Purpose |
|----------|---------|
| `loadCoordinates()` | Fetch regions, draw map boxes |
| `selectCoordinate(i)` | Select + fly to region |
| `renderCoordinatesList()` | Sidebar list view |
| `populateRegionsTable()` | Sidebar table view |
| `groupRegionsByContinent(regions)` | Group by heuristic continent |
| `initRegionNotes()` | Load notes from localStorage |

## modules/ui/presets.js

| Function | Purpose |
|----------|---------|
| `initPresetProfiles()` | Load presets from localStorage |
| `applyPreset(preset)` | Apply preset to all form controls |
| `collectAllSettings()` | Return full settings object |
| `applyAllSettings(s)` | Apply settings object to form |

## modules/ui/curve-editor.js

| Function | Purpose |
|----------|---------|
| `initCurveEditor()` | Setup canvas + state |
| `applyCurveTodem()` | Apply + re-render |
| `interpolateCurve(x)` | Monotone cubic spline at x∈[0,1] |
| `undoCurve()` / `redoCurve()` | Undo/redo curve edits |

## modules/dem/dem-merge.js

| Function | Purpose |
|----------|---------|
| `setupMergePanel()` | Wire merge panel events |
| `runMerge(apply)` | POST /api/dem/merge, optionally apply |

## modules/ui/view-management.js

| Function | Purpose |
|----------|---------|
| `switchView(view)` | Switch Explore/Edit/Extrude tab |
| `switchDemSubtab(tab)` | Switch DEM sub-tab |
| `cycleSidebarState()` | normal → list → table cycle |

## modules/map/map-globe.js

| Function | Purpose |
|----------|---------|
| `initMap()` | Leaflet map + draw control |
| `initGlobe()` | Three.js globe |
| `setTileLayer(key)` | Switch tile layer |
| `toggleDemOverlay(show)` | Terrain overlay on map |

## modules/map/compare-view.js

| Function | Purpose |
|----------|---------|
| `initCompareMode()` | Side-by-side compare panel |
| `loadCompareRegion(side)` | Load DEM for left/right panel |
