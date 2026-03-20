# CLAUDE.md — strm2stl Project Reference

> **Purpose**: Permanent reference for AI coding sessions. Read this file first before touching any source file.
> Last updated: 2026-03-19 (session 5 — server.py refactor complete, terrain router, city overlay alignment fix, curve editor re-normalization, compare window redesign)

---

## Table of Contents

1. [Project Overview](#project-overview)
2. [Stack](#stack)
3. [Directory Structure](#directory-structure)
4. [Frontend Architecture](#frontend-architecture)
5. [Key Global State](#key-global-state)
6. [All Major Functions](#all-major-functions)
7. [Backend API Routes](#backend-api-routes)
8. [HTML Structure](#html-structure)
9. [Data Flow](#data-flow)
10. [Known Issues](#known-issues)
11. [Editing Guidelines](#editing-guidelines)
12. [Refactoring Plan](#refactoring-plan)

---

## Project Overview

strm2stl is a web application for selecting geographic regions, downloading Digital Elevation Model (DEM) data, and generating 3D-printable STL files from real terrain.

**Core workflow:**
1. User draws or selects a bounding box on a Leaflet map
2. The server fetches DEM data from OpenTopography (or local SRTM tiles)
3. The DEM is visualised in the browser via Canvas API
4. Optionally: water mask (ESA/GEE), land cover, satellite imagery, city overlays
5. User adjusts parameters (scale, depth, base, colormap, curve editor) and downloads an STL

**Server**: FastAPI on port 9000, started via `python ui/server.py`
**Python venv**: `Code/.venv`

---

## Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3, FastAPI, Uvicorn |
| DEM data | OpenTopography API (SRTMGL1, SRTMGL3, AW3D30, COP30, COP90, SRTM15Plus), local SRTM tiles |
| Land cover | ESA WorldCover (via `geo2stl.sat2stl`) |
| Water mask | Google Earth Engine or ESA band 80 |
| City data | OpenStreetMap / Overpass API |
| 2D map | Leaflet.js + leaflet-draw |
| 3D globe | Three.js |
| 3D model viewer | Three.js (in-page canvas) |
| Rendering | HTML5 Canvas API (client-side) |
| Frontend state | Vanilla JS global variables (no framework) |
| Module system | Mixed — see Frontend Architecture |
| Persistence | `data.db` SQLite (regions + region_settings, WAL mode). Legacy `coordinates.json` and `region_settings.json` backed up as `.json.bak` post-migration. `localStorage` still used for presets and notes. |
| STL generation | `numpy2stl` package (server-side) |

---

## Directory Structure

```
strm2stl/
├── CLAUDE.md                        ← this file
├── TODO.md                          ← Active task list (UI fixes, features, backend refactor, caching, SQLite)
├── TODO_ADVANCED.md                 ← Research notes: Google 3D Tiles / py3dtiles / Blender pipeline
├── coordinates.json.bak             ← Legacy region store (backed up after SQLite migration)
├── region_settings.json.bak         ← Legacy settings store (backed up after SQLite migration)
├── data.db                          ← SQLite database: regions + region_settings (WAL mode)
├── ui/
│   ├── server.py                    ← FastAPI app init, startup lifespan, run_server (~328 lines)
│   ├── schemas.py                   ← All ~30 Pydantic models (BoundingBox, DEMRequest, ExportRequest…)
│   ├── config.py                    ← Constants: OPENTOPO_DATASETS, path consts, TEST_MODE, API keys
│   ├── core/
│   │   ├── dem.py                   ← DEM fetch + processing (fetch_layer_data, apply_layer_processing, blend_layers…)
│   │   ├── export.py                ← STL/OBJ/3MF/crosssection generation logic
│   │   ├── cache.py                 ← Disk cache helpers (.npz arrays, .json.gz OSM, prune_all_caches)
│   │   ├── db.py                    ← SQLite helpers (get_db, init_db, WAL mode)
│   │   ├── osm.py                   ← fetch_osm_data, _fill_building_heights, _get_road_width_m
│   │   └── cities_3d.py             ← generate_city_3mf, 3D building mesh generation
│   ├── routers/
│   │   ├── terrain.py               ← /api/terrain/* + /api/dem/merge + /api/export/preview
│   │   ├── regions.py               ← /api/regions/* (SQLite-first CRUD with JSON fallback)
│   │   ├── export.py                ← /api/export/* (STL/OBJ/3MF/crosssection)
│   │   ├── cities.py                ← /api/cities/* (OSM fetch + 3MF city export)
│   │   ├── cache.py                 ← /api/cache/* (stats, clear, check)
│   │   └── settings.py              ← /api/settings/* (projections, colormaps, datasets)
│   ├── templates/
│   │   └── index.html               ← Single-page app shell
│   └── static/
│       ├── css/
│       │   └── app.css
│       ├── js/
│       │   ├── app.js               ← ~8200-line monolith (the REAL running app)
│       │   ├── api.js               ← ES module (exists but NOT used by app.js)
│       │   ├── state.js             ← ES module (schema doc only — NOT used by app.js)
│       │   ├── main.js              ← ES module (exists but NOT used by app.js)
│       │   ├── components/
│       │   │   └── dem-viewer.js    ← ES module (exists but NOT used by app.js)
│       │   ├── utils/
│       │   │   ├── canvas.js        ← ES module (exists but NOT used by app.js)
│       │   │   └── colors.js        ← ES module (exists but NOT used by app.js)
│       │   └── modules/             ← Plain <script> modules (loaded before app.js)
│       │       ├── city-overlay.js  ← loadCityData, renderCityOverlay, clearCityOverlay, _updateCityLayerCount
│       │       └── stacked-layers.js ← updateStackedLayers, applyStackedTransform, enableStackedZoomPan, drawLayerGrid
│       └── FUNCTIONALITY_DOC.md    ← Completed feature & refactoring history
├── numpy2stl/                       ← STL generation library
│   └── numpy2stl/
│       └── oceans.py                ← make_dem_image()
├── geo2stl/                         ← Geo utilities
│   ├── sat2stl.py                   ← fetch_bbox_image(), GEE integration
│   └── projections.py               ← get_projection_info()
└── notebooks/
    ├── Cities.ipynb                 ← City pipeline (OSM buildings + terrain, Philadelphia/Granada/Cartagena)
    └── Buildings.ipynb              ← Building geometry research
```

All files in the directory tree above now exist and are used. The backend refactor from `location_picker.py` monolith is complete as of session 5.

---

## Frontend Architecture

### The Dual-Architecture Problem (CRITICAL)

**app.js is a 7300-line `<script>` tag — it is NOT an ES module.**

The files `api.js`, `state.js`, `main.js`, `dem-viewer.js`, `canvas.js`, and `colors.js` all exist as proper ES modules with `export` statements, but **none of them are imported anywhere in the running application**. They are effectively dead code relative to the running app.

This means:
- `app.js` contains its own copy of every helper function (renderDEMCanvas, mapElevationToColor, hslToRgb, drawColorbar, drawHistogram, drawGridlinesOverlay, etc.)
- `app.js` manages its own state via loose global variables instead of `state.js`
- `api.js` functions are duplicated inline in `app.js` as direct `fetch()` calls

**Do not attempt to import the ES modules into app.js without a full integration plan.** See Refactoring Plan section.

### How app.js is loaded

In `index.html`, app.js is loaded as a plain script (no `type="module"`):
```html
<script src="/static/js/app.js"></script>
```

All code runs inside a `DOMContentLoaded` event listener, except for a few helpers declared at the very top of the file (before line ~570).

### View System

Three top-level tabs:
- **Explore** (`data-view="map"`) — Leaflet map + globe
- **Edit** (`data-view="dem"`) — DEM canvas, water mask, land cover, stacked layers, compare
- **Extrude** (`data-view="model"`) — 3D model viewer, STL export, puzzle export

Within "Edit", there are DEM sub-tabs (managed by `setupDemSubtabs()` / `switchDemSubtab()`):
- `dem` — main DEM canvas
- `water` — water mask
- `landcover` — ESA land cover
- `combined` — combined view
- `satellite` — satellite imagery

### Sidebar State Machine

`cycleSidebarState()` cycles through: `'normal'` → `'list'` → `'table'` → `'normal'`

---

## Key Global State

All variables are in the `DOMContentLoaded` closure scope (or at file top). There is no central state object.

### Map & Globe

| Variable | Type | Description |
|----------|------|-------------|
| `map` | Leaflet.Map | Main 2D map instance |
| `globeScene` | THREE.Scene | Three.js scene for globe |
| `globeCamera` | THREE.PerspectiveCamera | Globe camera |
| `globeRenderer` | THREE.WebGLRenderer | Globe renderer |
| `globe` | THREE.Mesh | Globe sphere mesh |
| `drawnItems` | L.FeatureGroup | Leaflet layer for drawn rectangles |
| `preloadedLayer` | L.FeatureGroup | Leaflet layer for preloaded region boxes |
| `editMarkersLayer` | L.FeatureGroup | Leaflet layer for edit markers |
| `boundingBox` | L.Rectangle \| null | Currently active bbox rectangle |

### Region Management

| Variable | Type | Description |
|----------|------|-------------|
| `coordinatesData` | Array | Array of region objects `{name, label, north, south, east, west}` |
| `selectedRegion` | Object \| null | Currently selected region object |

### DEM & Layer Data

| Variable | Type | Description |
|----------|------|-------------|
| `lastDemData` | Object \| null | Last DEM API response `{values, width, height, min, max, bbox}` |
| `lastWaterMaskData` | Object \| null | Last water mask response |
| `lastEsaData` | Object \| null | Last ESA land cover response |
| `lastRawDemData` | Object \| null | Last raw (unprocessed) DEM response |
| `originalDemValues` | Float32Array \| null | Original DEM values before curve edits |
| `currentDemBbox` | Object \| null | `{north,south,east,west}` for current DEM |
| `layerBboxes` | Object | `{dem: bbox\|null, water: bbox\|null, landCover: bbox\|null}` |
| `layerStatus` | Object | `{dem: str, water: str, landCover: str}` — 'empty','loading','ready','error' |
| `activeDemSubtab` | String | Current DEM sub-tab name |

### Appearance & Settings

| Variable | Type | Description |
|----------|------|-------------|
| `landCoverConfig` | Object | ESA class → `{color, label, visible}` map |
| `waterOpacity` | Number | 0–1 opacity for water mask overlay (default 0.7) |
| `satOpacity` | Number | 0–1 opacity for satellite overlay (default 0.5) |
| `curvePoints` | Array | `[{x,y}, ...]` control points for curve editor |
| `userPresets` | Object | Named presets loaded from localStorage |
| `lastAppliedPresetName` | String \| null | Name of last applied preset |
| `regionNotes` | Object | `{regionName: noteText}` from localStorage |
| `sidebarState` | String | 'normal' \| 'list' \| 'table' |

### City Overlay

| Variable | Type | Description |
|----------|------|-------------|
| `osmCityData` | Object \| null | Full city response: `{buildings: GeoJSON, roads: GeoJSON, waterways: GeoJSON, ...}`. Features have `height_m` (buildings) and `road_width_m` (roads) properties filled server-side, `terrain_z` filled client-side by `_computeTerrainZ()`, and `_bbox` (geo bounding box) pre-computed for sub-pixel culling. |
| `window.renderCityOnDEM` | Function \| undefined | Set by `city-overlay.js`. Paints buildings+roads+waterways onto the `.city-dem-overlay` canvas inside `#demImage`. Called after DEM reload to keep overlay in sync. |

### Stacked Layers

| Variable | Type | Description |
|----------|------|-------------|
| `stackedLayerData` | Object | `{dem, water, landCover}` each with `{canvas, bbox, label}` |

### Compare Mode

| Variable | Type | Description |
|----------|------|-------------|
| `compareData` | Object | `{left: {region, dem, ...}, right: {region, dem, ...}}` |

### 3D Model Viewer

| Variable | Type | Description |
|----------|------|-------------|
| `terrainMesh` | THREE.Mesh \| null | Current terrain mesh in 3D viewer |
| `viewerAutoRotate` | Boolean | Whether 3D viewer auto-rotates |

### Merge Panel

| Variable | Type | Description |
|----------|------|-------------|
| `_mergeSources` | Array | Available DEM source descriptors |
| `_mergeLayers` | Array | Current merge layer stack objects |

### Cache

| Variable | Type | Description |
|----------|------|-------------|
| `waterMaskCache` | Object | In-memory LRU cache for water mask responses (max 20 entries) |

---

## All Major Functions

### Functions declared BEFORE DOMContentLoaded (file-top helpers, lines 1–570)

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `fetchWithErrorHandling(url, options)` | ~2 | Simple fetch wrapper returning `{data, error}` | `url`, `options` |
| `loadCoordinates()` | ~20 | Stub at top of file (real version at ~1057) | — |
| `clearLayerCache()` | ~84 | Resets `lastDemData`, `lastWaterMaskData`, `lastEsaData`, `lastRawDemData`, `layerBboxes`, `layerStatus` | — |
| `clearLayerDisplays()` | ~104 | Clears canvas elements and status indicators | — |
| `updateLayerStatusIndicators()` | ~127 | Updates DOM badge elements for each layer's status | — |
| `getCurrentBboxObject()` | ~165 | Returns `{north,south,east,west}` from `boundingBox` or form inputs | — |
| `isLayerCurrent(layerName)` | ~191 | Returns true if layer bbox matches current bbox | `layerName` |
| `showToast(message, type, duration)` | ~218 | Shows a toast notification | `message`, `type` ('success'\|'error'\|'info'), `duration` ms |
| `toggleCollapsible(header)` | ~244 | Toggles a collapsible section open/closed | `header` el |
| `setupCoordinateSearch()` | ~269 | Wires search input to filter `coordinatesData` | — |
| `setLayerStatus(layer, status)` | ~285 | Updates `layerStatus[layer]` and calls `updateLayerStatusUI()` | `layer`, `status` |
| `updateLayerStatusUI()` | ~290 | Syncs DOM layer status badges from `layerStatus` | — |
| `showLoading(container)` | ~318 | Shows spinner overlay on container | `container` el |
| `hideLoading(container)` | ~339 | Removes spinner overlay | `container` el |
| `waterMaskCache` | ~354 | LRU object: `{get, set, has, generateKey, getStats, clear}`, max 20 entries | — |
| `updateCacheStatusUI()` | ~418 | Updates memory/server cache count displays | — |
| `fetchServerCacheStatus()` | ~431 | Async: fetches `/api/cache_status`, updates UI | — |
| `preloadAllRegions()` | ~450 | Async: preloads DEM for all regions in background | — |
| `clearClientCache()` | ~530 | Clears `waterMaskCache` and layer data | — |
| `clearServerCache()` | ~538 | Async: POSTs to `/api/clear_cache` | — |
| `setupCacheManagement()` | ~555 | Wires cache management button event listeners | — |

### Inside DOMContentLoaded (lines 571+)

#### Map & Globe

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `TILE_LAYERS` (const) | ~634 | Object mapping tile layer keys to Leaflet TileLayer configs | — |
| `setTileLayer(layerKey)` | ~665 | Switches active Leaflet tile layer | `layerKey` string |
| `toggleDemOverlay(show)` | ~684 | Async: loads terrain DEM for current map view and renders as overlay | `show` bool |
| `toggleTerrainOverlay(show)` | ~798 | Shows/hides the terrain overlay canvas on the map | `show` bool |
| `setTerrainOverlayOpacity(opacity)` | ~804 | Sets CSS opacity of terrain overlay | `opacity` 0–1 |
| `initMap()` | ~826 | Initialises Leaflet map, draw control, event handlers | — |
| `initMapGrid()` | ~892 | Creates SVG grid overlay on map | — |
| `updateMapGrid()` | ~920 | Redraws grid lines when map moves | — |
| `toggleMapGrid(show)` | ~995 | Shows/hides map grid overlay | `show` bool |
| `updateBboxIndicator(color)` | ~998 | Updates bounding box highlight colour in UI | `color` hex |
| `initGlobe()` | ~1007 | Initialises Three.js globe with sphere geometry and texture | — |
| `animateGlobe()` | ~1050 | RAF loop for globe rotation | — |

#### Region Management

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `loadCoordinates()` | ~1057 | Async: fetches `/api/coordinates`, populates `coordinatesData`, draws rectangles on map | — |
| `updateGlobeMarkers()` | ~1144 | Refreshes 3D globe markers for all regions | — |
| `createGlobeMarker(lat, lng)` | ~1171 | Creates a Three.js sprite marker at lat/lng on globe | `lat`, `lng` |
| `selectCoordinate(index)` | ~1187 | Async: selects a region, flies map, updates UI | `index` int |
| `goToEdit(index)` | ~1241 | Switches to Edit tab for region at index | `index` int |
| `detectContinent(lat, lon)` | ~2793 | Heuristic: returns continent name string | `lat`, `lon` |
| `groupRegionsByContinent(regions)` | ~2815 | Groups region array by continent | `regions` array |
| `renderCoordinatesList()` | ~2832 | Renders sidebar list view of regions | — |
| `populateRegionsTable()` | ~2903 | Renders sidebar table view of regions | — |
| `setupRegionsTable()` | ~2950 | Wires region table interactions | — |
| `initRegionNotes()` | ~2981 | Loads `regionNotes` from localStorage | — |
| `showNotesModal(regionName)` | ~3010 | Opens notes modal for a region | `regionName` |
| `hideNotesModal()` | ~3025 | Closes notes modal | — |
| `saveRegionNotes()` | ~3030 | Saves notes to localStorage | — |
| `saveRegionSettings()` | ~2677 | Async: POSTs current settings to `/api/regions/{name}/settings` | — |
| `loadAndApplyRegionSettings(regionName)` | ~2703 | Async: GETs `/api/regions/{name}/settings` and applies | `regionName` |

#### DEM Loading & Rendering

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `window.loadDEM` | ~4193 | Async: main DEM loader — fetches `/api/preview_dem`, renders canvas, updates state | Reads form inputs |
| `loadHighResDEM()` | ~4385 | Async: fetches higher-resolution DEM version | — |
| `drawGridlinesOverlay(containerId)` | ~4396 | Draws lat/lon gridlines on a canvas overlay (local copy) | `containerId` |
| `applyProjection(srcCanvas, bbox)` | ~4596 | Applies selected map projection to source canvas | `srcCanvas`, `bbox` |
| `renderDEMCanvas(values, width, height, colormap, vmin, vmax)` | ~4689 | Renders elevation values to canvas using LUT | params |
| `renderSatelliteCanvas(values, width, height)` | ~4785 | Renders RGB satellite pixel array to canvas | params |
| `mapElevationToColor(t, cmap)` | ~4839 | Maps 0–1 elevation to RGB array (local duplicate) | `t`, `cmap` |
| `updateAxesOverlay(north, south, east, west)` | ~4886 | Draws N/S/E/W labels on axes overlay (local copy) | bbox floats |
| `hslToRgb(h, s, l)` | ~4932 | HSL to RGB conversion (local duplicate) | 0–1 floats |
| `drawColorbar(min, max, colormap)` | ~4956 | Renders colorbar legend to canvas (local copy) | params |
| `drawHistogram(values)` | ~4985 | Renders elevation histogram with cumulative curve (local copy) | `values` array |
| `recolorDEM()` | ~3933 | Re-renders DEM canvas with current colormap/vmin/vmax | — |
| `rescaleDEM(newVmin, newVmax)` | ~3980 | Rescales DEM display range | `newVmin`, `newVmax` |
| `resetRescale()` | ~4020 | Resets display range to data min/max | — |
| `setupHoverTooltip(canvas)` | ~7233 | Attaches mouse-move elevation tooltip to DEM canvas | `canvas` el |

#### Water Mask

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `loadWaterMask()` | ~6521 | Async: fetches `/api/water_mask`, uses `waterMaskCache`, renders | — |
| `renderWaterMask(data)` | ~6661 | Renders water mask data to canvas | `data` object |
| `previewWaterSubtract()` | ~6851 | Async: shows DEM with water subtracted | — |
| `applyWaterSubtract()` | ~6912 | Applies water subtraction to current DEM values | — |
| `setupWaterMaskListeners()` | ~7033 | Wires water mask tab event listeners | — |

#### Satellite / Land Cover

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `loadSatelliteImage()` | ~5274 | Async: fetches satellite/ESA image for current bbox | — |
| `loadSatelliteForTab()` | ~6830 | Async: loads satellite when satellite sub-tab is activated | — |
| `renderEsaLandCover(data)` | ~6701 | Renders ESA land cover classification to canvas | `data` object |
| `renderLandCoverLegend()` | ~6943 | Renders land cover colour legend | — |
| `setupLandCoverEditor()` | ~6990 | Wires land cover class visibility toggles | — |

#### Stacked Layers View

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `setupStackedLayers()` | ~3246 | Initialises stacked layers panel | — |
| `updateStackedLayers()` | ~3275 | Renders all layer canvases in stacked view | — |
| `updateLayerAxisLabels()` | ~3380 | Updates lat/lon axis labels on stacked view | — |
| `niceGeoInterval()` | ~3385 | Returns a "nice" grid interval for degree labels | — |
| `formatCoord(deg)` | ~3394 | Formats a coordinate degree value as string | `deg` float |
| `drawLayerGrid()` | ~3405 | Draws grid overlay on stacked layer canvases | — |
| `enableStackedZoomPan()` | ~3521 | Attaches wheel/drag zoom+pan to stacked view | — |
| `applyStackedTransform()` | ~3666 | Applies CSS transform to stacked layer elements | — |
| `drawGridOverlay()` | ~3680 | Draws coordinate grid on stacked view | — |

#### Compare View

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `initCompareMode()` | ~3054 | Initialises side-by-side compare panel | — |
| `loadCompareRegion(side)` | ~3083 | Async: loads DEM for left or right compare panel | `side` 'left'\|'right' |
| `updateRegionParamsTable(region)` | ~3162 | Populates compare params table for a region | `region` object |
| `applyRegionParams()` | ~3199 | Applies selected region params from compare table | — |

#### Combined View

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `renderCombinedView()` | ~6757 | Async: composites DEM + water + land cover into one canvas | — |

#### Curve Editor

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `initCurveEditor()` | ~1994 | Sets up curve editor canvas and state | — |
| `setupCurveEventListeners()` | ~2024 | Wires curve canvas mouse events | — |
| `setCurvePreset(presetName)` | ~2172 | Sets curve to a named preset shape | `presetName` |
| `addCurvePoint(x, y)` | ~2181 | Adds control point to curve | `x`, `y` (0–1) |
| `removeCurvePointNear(x, y)` | ~2202 | Removes nearest control point | `x`, `y` (0–1) |
| `findCurvePointNear(x, y)` | ~2214 | Returns nearest control point within threshold | `x`, `y` (0–1) |
| `drawCurve()` | ~2221 | Re-renders curve editor canvas | — |
| `applyCurveTodem()` | ~2311 | Applies curve to DEM and re-renders | — |
| `applyCurveTodemSilent()` | ~2366 | Applies curve without triggering UI update | — |
| `interpolateCurve(x)` | ~2400 | Evaluates curve at x using monotone cubic spline | `x` 0–1 |
| `resetDemToOriginal()` | ~2421 | Restores DEM to `originalDemValues` | — |

#### City Overlay (`modules/city-overlay.js`)

All functions are defined in `city-overlay.js` (plain script, loaded before app.js). They read/write `window.appState` for shared state.

| Function | Purpose | Key Params |
|----------|---------|------------|
| `_updateCitiesTabVisibility(region)` | Shows/hides city tab based on region diagonal (≤ 15 km) | `region` object |
| `loadCityData()` | Async: POST `/api/cities`, runs `_computeTerrainZ()` on result, stores in `window.appState.osmCityData`, calls `renderCityOverlay()` | — |
| `_computeTerrainZ(geojson, demData)` | Client-side: samples DEM pixel for each feature centroid; writes `feat.properties.terrain_z` | `geojson`, `demData` |
| `_geomCentroid(geom)` | Returns `[lon, lat]` centroid for any GeoJSON geometry type | `geom` |
| `_computeGeomBbox(geom)` | Returns `{minLon,maxLon,minLat,maxLat}` for a feature geometry; stored as `feat._bbox` at load time | `geom` |
| `_updateCityLayerCount(layer, count)` | Updates layer count badge in UI | `layer` string, `count` int |
| `clearCityOverlay()` | Clears Leaflet markers + removes `.city-dem-overlay` canvas | — |
| `renderCityOverlay()` | Debounced: paints buildings/roads/waterways on stacked-layers canvas; resets CSS transform after render; calls `window.renderCityOnDEM?.()` | — |
| `window.renderCityOnDEM()` | Debounced: paints city features onto `.city-dem-overlay` canvas inside `#demImage` | — |
| `_drawCityCanvas(ctx, geoToPx, invZ, osmCityData, W, tW, bboxLonM)` | Core draw routine (shared between stacked and DEM views). Buildings batched into `ALPHA_BUCKETS=8` groups; sub-pixel buildings (< 1.5 px) skipped. Roads batched by rounded lineWidth. | — |

#### 3D Model Viewer

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `initModelViewer()` | ~5544 | Initialises Three.js scene for 3D terrain preview | — |
| `createTerrainMesh(demValues, width, height, exaggeration)` | ~5651 | Creates Three.js PlaneGeometry mesh from DEM values | params |
| `previewModelIn3D()` | ~5709 | Calls `createTerrainMesh` with current DEM and renders | — |
| `haversineDiagKm()` | ~5801 | Returns diagonal distance in km for current bbox | — |
| `generateModelFromTab()` | ~5346 | Triggers server-side model generation | — |
| `downloadSTL()` | ~5398 | Initiates STL file download via `/api/download_stl` | — |
| `downloadModel(format)` | ~5471 | Downloads model in specified format | `format` 'stl'\|'obj'\|'3mf' |

#### Preset Management

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `initPresetProfiles()` | ~2482 | Loads presets from localStorage, sets up built-in presets | — |
| `setupPresetEventListeners()` | ~2498 | Wires preset UI buttons | — |
| `updatePresetSelect()` | ~2518 | Rebuilds preset dropdown options | — |
| `loadSelectedPreset()` | ~2550 | Reads selected preset and calls `applyPreset()` | — |
| `applyPreset(preset)` | ~2576 | Applies a preset object to all form controls | `preset` object |
| `getCurrentSettings()` | ~2599 | Returns current settings as a partial object | — |
| `collectAllSettings()` | ~2612 | Returns all settings as a full object | — |
| `applyAllSettings(s)` | ~2636 | Applies a full settings object to all form controls | `s` object |

#### Merge Panel

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `_initDemSources()` | ~6069 | Async: fetches available DEM sources from server | — |
| `_mergeSourceOptions()` | ~6124 | Returns HTML string of source `<option>` elements | — |
| `_createMergeLayerObj()` | ~6148 | Creates a new merge layer descriptor object | — |
| `_renderMergeLayerCard(layer, i)` | ~6200 | Returns HTML for one merge layer card | `layer`, `i` index |
| `_renderMergePanel()` | ~6318 | Re-renders entire merge panel HTML | — |
| `_mergeLayerToSpec(layer)` | ~6320 | Converts merge layer object to API spec format | `layer` object |
| `runMerge(apply)` | ~6340 | Async: POSTs to `/api/merge_dem`, optionally applies result | `apply` bool |
| `setupMergePanel()` | ~6413 | Wires merge panel event listeners | — |

#### UI Utilities

| Function | Line | Purpose | Key Params |
|----------|------|---------|------------|
| `setupEventListeners()` | ~1251 | Main event wiring function (very large) | — |
| `setupKeyboardShortcuts()` | ~1910 | Wires keyboard shortcut handlers | — |
| `switchView(view)` | ~4046 | Switches top-level tab view | `view` 'map'\|'dem'\|'model' |
| `setupDemSubtabs()` | ~6432 | Wires DEM sub-tab click handlers | — |
| `switchDemSubtab(subtab)` | ~6462 | Switches active DEM sub-tab | `subtab` string |
| `cycleSidebarState()` | ~5131 | Cycles sidebar through normal/list/table states | — |
| `_setSidebarViews(state)` | ~5147 | Applies sidebar state to DOM | `state` string |
| `renderSidebarTable(filter)` | ~5179 | Renders filterable sidebar table of regions | `filter` string |
| `toggleBboxLayerVisibility()` | ~5236 | Toggles bounding box layer visibility on map | — |
| `toggleStatusPanel()` | ~5251 | Shows/hides status panel | — |
| `setupOpacityControls()` | ~3903 | Wires opacity slider for layer overlays | — |
| `setupAutoReload()` | ~3821 | Sets up auto-reload timer | — |
| `clearAllBoundingBoxes()` | ~3839 | Removes all drawn rectangles from map | — |
| `loadAllLayers()` | ~3867 | Async: loads DEM + water + land cover in sequence | — |
| `loadSelectedRegion()` | ~4113 | Applies selected region bbox to map | — |
| `saveCurrentRegion()` | ~4124 | Saves current bbox as a new region | — |
| `submitBoundingBox()` | ~4183 | Submits current bbox to server (legacy route) | — |
| `setBboxInputValues()` | ~7079 | Fills bbox coordinate input fields | — |
| `initBboxMiniMap()` | ~7120 | Initialises mini map in bbox panel | — |
| `syncBboxMiniMap()` | ~7165 | Syncs mini map with current bbox | — |
| `toggleBboxMiniMap()` | ~7203 | Shows/hides bbox mini map | — |
| `setupGridToggle()` | ~7207 | Wires grid toggle button (local version) | — |
| `populateRegionsPanelTable()` | ~3685 | Renders regions in floating panel | — |
| `closeRegionsPanel()` | ~3750 | Hides regions panel | — |
| `toggleContinentVisibility()` | ~3755 | Collapses/expands a continent group in panel | — |
| `updatePuzzlePreview()` | ~6024 | Updates puzzle piece preview canvas | — |
| `exportPuzzle3MF()` | ~6056 | Exports puzzle as 3MF file | — |

---

## Backend API Routes

Routes are split across `ui/routers/` (terrain, regions, export, cities, cache, settings). `ui/server.py` (~328 lines) handles app init, static mounts, router includes, lifespan, and `run_server` only.

### Region / Coordinate Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve index.html |
| GET | `/api/coordinates` | Legacy: list all saved regions from `coordinates.json` |
| POST | `/api/save_coordinate` | Legacy: save a new region (still used by app.js) |
| GET | `/api/regions` | List all regions (typed `RegionsListResponse`) |
| POST | `/api/regions` | Create a new region (body: `RegionCreate`), 201 response |
| PUT | `/api/regions/{name}` | Update region bbox + metadata |
| DELETE | `/api/regions/{name}` | Delete region; also removes from `region_settings.json` |
| GET | `/api/regions/{name}/settings` | Get per-region saved settings |
| PUT | `/api/regions/{name}/settings` | Save per-region settings |

### DEM / Terrain Routes

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/terrain/dem` | Fetch processed DEM (north/south/east/west/dim + params) |
| GET/POST | `/api/terrain/dem/raw` | Fetch unprocessed DEM array |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover |
| GET/POST | `/api/terrain/satellite` | Fetch satellite imagery |
| GET | `/api/terrain/sources` | List available DEM data sources |
| GET | `/api/terrain/elevation-profile` | Elevation cross-section (returns 501 — not yet implemented) |
| POST | `/api/dem/merge` | Merge multiple DEM layers (body: `MergeRequest`) |

**Note:** All terrain route handlers (`get_terrain_dem`, `get_terrain_water_mask`, `get_terrain_dem_raw`, `get_terrain_satellite`, `merge_dem_layers`, etc.) live in `ui/routers/terrain.py`. They delegate to `core/dem.py` for the actual DEM fetch and processing logic.

### Model Export Routes

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/export/stl` | Generate STL model |
| POST | `/api/export/obj` | Generate OBJ model |
| POST | `/api/export/3mf` | Generate 3MF model |
| POST | `/api/export/preview` | Return DEM values for Three.js preview (no STL) |

**Note:** app.js also calls `/api/generate_{format}` (old pattern) for non-STL formats — these may be dead routes.

### City Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cities/cached` | Check if city data is cached for bbox |
| POST | `/api/cities` | Fetch OSM buildings/roads/waterways from Overpass (rejects bbox > 15 km diagonal); results cached as `.json.gz` |
| POST | `/api/cities/export3mf` | Generate 3MF with terrain + extruded building prisms (Cities 10+12). Body: `CityExportRequest` with DEM values, building GeoJSON, physical dimensions. |

### Cache & Settings Routes

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cache` | Get cache statistics |
| DELETE | `/api/cache` | Clear server-side DEM cache |
| GET | `/api/cache/check` | Check if specific bbox is cached |
| GET | `/api/settings/projections` | List available projections |
| GET | `/api/settings/colormaps` | List available colormaps |
| GET | `/api/settings/datasets` | List available DEM datasets |
| GET | `/api/global_dem_overview` | Cached global DEM PNG overview |

### Pydantic Models (Key)

- `BoundingBox` — `{north, south, east, west}`
- `RegionCreate(BoundingBox)` — `{name, label?, description?, north, south, east, west}`
- `RegionResponse(BoundingBox)` — `{name, label?, description?, north, south, east, west, parameters?}`
- `RegionSettings` — `{name, settings: dict}` — arbitrary JSON settings blob
- `DEMRequest(BoundingBox)` — `{north, south, east, west, dim, ...}`
- `DEMResponse` — `{values, width, height, min, max, bbox, ...}`
- `WaterMaskRequest(BoundingBox)` — `{north, south, east, west, dim, sat_scale}`
- `WaterMaskResponse` — `{water_mask_values, water_mask_dimensions, esa_values, ...}`
- `SatelliteRequest/Response` — satellite imagery request/response
- `ExportRequest(BoundingBox)` — `{north, south, east, west, dim, depth_scale, height, base, ...}`
- `CityRequest(BoundingBox)` — `{north, south, east, west, layers: list[str]}`
- `MergeRequest` — `{north, south, east, west, dim, layers: list[MergeLayerSpec]}`
- `MergeLayerSpec` — `{source, blend_mode, weight, processing: ProcessingSpec}`
- `ProcessingSpec` — `{clip_min, clip_max, smooth_sigma, sharpen, normalize, invert, extract_rivers, river_max_width_px}`
- `CacheStatusResponse` — `{entries, total_size_mb, cache_dirs: list[CacheDirInfo]}`

### DEM Sources (OPENTOPO_DATASETS)

- `SRTMGL1` — SRTM 30m global
- `SRTMGL3` — SRTM 90m global
- `AW3D30` — ALOS 30m
- `COP30` — Copernicus 30m
- `COP90` — Copernicus 90m
- `SRTM15Plus` — SRTM 15-arc-second (ocean bathymetry)
- `local` — Local SRTM tiles via `make_dem_image()`
- `water_esa` — ESA WorldCover water mask band

---

## HTML Structure

Located at `ui/templates/index.html`. Single-page app, all content always in DOM (visibility via CSS/JS).

### Top-level Layout

```
body
├── #toastContainer                    — floating toast notifications
├── #regionNotesModal                  — modal dialog for region notes
└── .page-wrapper
    ├── .sidebar                       — left sidebar
    │   ├── #sidebarListView           — list view of regions
    │   │   ├── #coordSearch           — search input
    │   │   └── #coordinatesList       — <ul> of region cards
    │   └── #sidebarTableView          — table view of regions
    │       └── #sidebarRegionsTable   — <table>
    └── .main-content
        ├── .main-header
        │   └── .tabs                  — Explore / Edit / Extrude tabs
        └── .content-area
            ├── #mapContainer          — Leaflet map container
            │   ├── #map               — actual Leaflet map
            │   ├── .map-floating-controls — floating action buttons
            │   ├── #mapSettingsPanel  — map settings popup
            │   ├── #floatingDrawBtn   — "+ New Region" button
            │   └── #regionsPanel      — hideable regions side panel
            ├── #globeContainer        — Three.js globe (hidden by default)
            │   └── #globe
            └── [DEM/Model tab content — various panels]
```

### Key Element IDs

**Map Controls**
- `#floatingTerrainToggle`, `#floatingGridToggle`, `#floatingGlobeToggle`, `#floatingRegionsToggle`, `#floatingMapSettingsBtn`
- `#mapTileLayerExplore` — tile layer select
- `#showTerrainOverlayExplore`, `#terrainOverlayOpacityExplore`
- `#showGridlinesExplore`
- `#floatingDrawBtn` — draw new region

**Region Panel**
- `#regionsPanel`, `#regionsPanelSearch`, `#regionsPanelList`, `#regionsPanelNewBtn`, `#closeRegionsPanel`

**Cache Management**
- `#memoryCacheCount`, `#serverCacheCount`, `#cacheHitRate`, `#preloadedCount`
- `#preloadRegionsBtn`, `#clearClientCacheBtn`, `#clearServerCacheBtn`, `#genGlobalDemBtn`

**DEM Sub-tabs** (Edit view)
- Buttons with `data-subtab="dem|water|landcover|combined|satellite"`

**City Overlay**
- `#citiesTab`, `#loadCitiesBtn`, `#clearCitiesBtn`
- Layer count badges per city type

**3D Viewer**
- `#modelViewer` — Three.js canvas container
- `#previewModelBtn`, `#autoRotateBtn`

---

## Data Flow

### DEM Load Flow

```
User clicks "Load DEM"
  → window.loadDEM()
    → fetch /api/preview_dem (north/south/east/west/dim/...)
    → response: {values: float[], width, height, min, max, bbox, ...}
    → store in lastDemData
    → store originalDemValues (copy)
    → renderDEMCanvas(values, width, height, colormap, vmin, vmax)
    → drawColorbar(min, max, colormap)
    → drawHistogram(values)
    → updateAxesOverlay(north, south, east, west)
    → setLayerStatus('dem', 'ready')
    → (optional) applyCurveTodemSilent() if curve is active
```

### Water Mask Flow

```
User activates water mask sub-tab
  → loadWaterMask()
    → waterMaskCache.has(key) → return cached / fetch /api/water_mask
    → response: {mask: int[], width, height, bbox}
    → waterMaskCache.set(key, data)
    → renderWaterMask(data)
    → setLayerStatus('water', 'ready')
```

### Region Save Flow

```
User draws bbox on map
  → Leaflet draw:created event
  → prompt for region name
  → POST /api/save_coordinate {name, label, north, south, east, west}
  → loadCoordinates() (refresh)
  → renderCoordinatesList()
```

### STL/OBJ/3MF Export Flow

```
User clicks "Download STL"
  → downloadSTL() (~line 6058)
    → POST /api/export/stl {dem_values, height, width, depth_scale, base, ...}
    → response: blob → browser download

User clicks "Download OBJ" or "Download 3MF"
  → downloadModel(format) (~line 6133)
    → POST /api/export/{format} {dem_values, height, width, depth_scale, base, ...}
    → response: blob → browser download
```

### City Overlay Flow

```
User clicks "Load Cities"
  → _updateCitiesTabVisibility(region)  (checks diagonal ≤ 10 km)
  → loadCityData()
    → POST /api/cities {north, south, east, west, layers: [...], simplify_tolerance, min_area}
    → response: {buildings: GeoJSON, roads: GeoJSON, waterways: GeoJSON, ...}
    →   buildings have height_m filled (OSM tag / levels×4 / 10m fallback)
    →   roads have road_width_m filled per highway type
    → _computeTerrainZ(data.buildings, demData)
    →   also pre-computes feat._bbox for each feature (sub-pixel culling at render time)
    → _computeTerrainZ(data.roads, demData)
    → store in window.appState.osmCityData
    → renderCityOverlay()   [debounced via RAF]
      → _drawCityCanvas: buildings batched by alpha bucket (8 groups), sub-pixel skipped
      → overlay.style.transform = '' (resets any CSS zoom applied during scroll)
      → calls window.renderCityOnDEM?.()  — also paints on DEM canvas overlay

User zooms/pans stacked layers view
  → enableStackedZoomPan wheel handler → stackZoom.scale changes
  → applyStackedTransform() called
  →   CSS transform applied to all layer canvases AND .osm-overlay (smooth visual)
  →   if scale change > 15%: immediate renderCityOverlay() for LOD road widths
  →   else: debounced renderCityOverlay() after 300 ms (avoids per-tick re-render)

User clicks "🏙️ 3MF + Buildings" button (Extrude tab, City Buildings row)
  → POST /api/cities/export3mf {bbox, dem_values, dem_width, dem_height, buildings GeoJSON, ...}
  → server: _terrain_mesh() + _build_building_meshes() + write3MF()
  → response: 3MF bytes → browser download as "{name}_city.3mf"
```

---

## Known Issues

### 1. Dead Module Files
`api.js`, `state.js`, `main.js`, `dem-viewer.js`, `canvas.js`, `colors.js` are never imported by the running application. They represent a parallel (incomplete) modularisation attempt. Any changes to these files have NO effect on the running app.

### 2. Duplicate Functions
The following functions exist in both `app.js` AND in module files, with the `app.js` versions being the ones actually used:
- `renderDEMCanvas` (also in `canvas.js`)
- `mapElevationToColor` (also in `colors.js`)
- `hslToRgb` (also in `colors.js`)
- `drawColorbar` (also in `canvas.js`)
- `drawHistogram` (also in `canvas.js`)
- `drawGridlinesOverlay` (also in `dem-viewer.js`)
- `enableZoomAndPan` (also in `canvas.js`)
- `loadCoordinates` (also in `api.js` and `main.js`)
- `recolorDEM` (also in `dem-viewer.js`)

### 3. Loose Global State
app.js uses ~30 loose global variables instead of a centralised state object. `state.js` exists but is unused. This makes reasoning about state changes difficult.

### 4. state.js / window.appState
`state.js` is an ES module (unused by app.js) that now documents the full state schema including `currentDemBbox`, `layerBboxes`, `layerStatus`, `activeDemSubtab`, `osmCityData`, and all `cache.*` fields. The live cross-module state is `window.appState` (set up in app.js); the closure-local variables (`lastDemData`, `currentDemBbox`, `layerBboxes`, `layerStatus`) still exist in app.js alongside their `window.appState` mirrors.

### 5. Two `loadCoordinates()` Functions in app.js
There is a stub `loadCoordinates()` at the very top of app.js (~line 20) and the real async implementation at ~line 1057 inside the `DOMContentLoaded` closure. The outer stub is a leftover.

### 6. `<script>` vs Module Boundary
Because app.js is loaded as `<script>` (not `type="module"`), it cannot use `import`/`export`. Converting it to a module requires restructuring the entire file and changing the HTML script tag.

### 7. ~~Broken OBJ/3MF Export Route~~ — FIXED
`downloadModel(format)` now calls `/api/export/${format}` correctly. All three export formats (STL, OBJ, 3MF) use the `/api/export/{format}` endpoints and work.

### 8. ~~STL Export Data Flow Mismatch~~ — RESOLVED
STL export correctly POSTs to `/api/export/stl` with DEM values in the request body. The Data Flow section below has been updated to reflect this accurately.

---

## Editing Guidelines

1. **Read before editing.** Always read the relevant section of app.js before making changes. Line numbers in this document are approximate — search for the function name.

2. **Do not break the `DOMContentLoaded` closure.** All functions from line ~571 onward must remain inside this closure or explicitly be moved to file-top scope.

3. **Do not convert app.js to a module** without a plan. Adding `type="module"` to the script tag will break all global function references in HTML `onclick` handlers.

4. **HTML onclick handlers.** Many elements in `index.html` use `onclick="functionName()"` directly. These functions must remain in global scope or `window.functionName` scope. Check before making any function private.

5. **window.loadDEM is intentionally on window.** It is called from HTML and possibly from console. Do not rename or make it a closure-only function without updating all call sites.

6. **The waterMaskCache is not a class.** It is a plain object with methods. It lives at file-top scope so it persists across DOMContentLoaded.

7. **Do not edit module files** (api.js, state.js, etc.) expecting the changes to affect the running app. Those files are unused.

8. **Test with the actual server.** Run `python ui/server.py` from the `ui/` directory with the venv activated (`Code/.venv`). The server must be running on port 9000.

9. **When adding new API calls**, add the route to the appropriate `ui/routers/` file (or create a new one) and add a corresponding call in app.js. Business logic goes in `ui/core/`. Do not add to api.js (unused) unless you are also integrating the module system.

10. **Colormap names** used in the app: `terrain`, `viridis`, `jet`, `rainbow`, `hot`, `gray`. These must match the `COLORMAPS` object in the local `mapElevationToColor()` implementation inside app.js.

---

## Refactoring Plan

### Short-term (current tasks)

**Task 2** — Add section headers and JSDoc to app.js
- Add `// ============================================================` section dividers
- Add `/** ... */` JSDoc to every function without one
- Sections: Global State, Initialization, Map/Globe, Region Management, DEM Loading & Rendering, Water Mask, Satellite/Land Cover, Stacked Layers View, Compare View, Combined View, Curve Editor, City Overlay, Merge Panel, 3D Model Viewer, Preset Management, UI Utilities, Event Listeners
- No logic changes

**Task 3 — DONE** — `ui/static/js/modules/city-overlay.js`
- Contains: `loadCityData()`, `clearCityOverlay()`, `renderCityOverlay()`, `_updateCityLayerCount()`
- Loaded as plain `<script>` before app.js; functions on `window`
- Shared state via `window.appState` (selectedRegion, currentDemBbox, osmCityData, showToast, haversineDiagKm)

**Task 4 — DONE** — `ui/static/js/modules/stacked-layers.js`
- Contains: `updateStackedLayers()`, `applyStackedTransform()`, `enableStackedZoomPan()`, `drawLayerGrid()`, `updateLayerAxisLabels()`, `drawGridOverlay()`
- Internal state: `stackZoom`, `stackZoomInitialized` (module-scoped)
- Shared state via `window.appState` (currentDemBbox, selectedRegion, lastDemData [new])

**Task 5 — DONE** — Clean up state.js
- Added missing properties: `currentDemBbox`, `layerBboxes`, `layerStatus`, `activeDemSubtab`, `osmCityData`, and all `cache.*` fields
- Added JSDoc to all functions; updated `clearCache()` to also reset layer tracking state
- Removed dead `let osmCityData = null` from app.js (orphaned after city-overlay.js extraction)
- `state.js` remains an ES module (unused by app.js) but now accurately documents the full state schema

### Medium-term (future)

- **Integrate api.js**: Replace direct `fetch()` calls in app.js with calls to `api.js` functions. Requires either making app.js a module or exposing api.js functions on `window`.
- **Integrate state.js**: Replace loose globals with `getState()`/`updateState()` calls.
- **Integrate canvas.js / colors.js**: Remove duplicated rendering functions from app.js.
- **Integrate dem-viewer.js**: Consolidate DEM rendering logic.

### Long-term (ideal target — JS)

- Split app.js into proper ES modules under `ui/static/js/modules/`
- Load via `<script type="module" src="/static/js/main.js">`
- `main.js` imports and wires all modules
- `window.x` bridge for any remaining HTML onclick handlers
- Full state management via `state.js`
- Unit tests for pure functions (projection, colormap, curve interpolation)

---

### Backend Architecture (complete as of session 5)

The backend split from `location_picker.py` monolith is done. Current layout:

```
ui/
├── server.py    (~328 lines) ← app init, lifespan, router includes, run_server
├── schemas.py   (~319 lines) ← all ~30 Pydantic models
├── config.py    (~97 lines)  ← OPENTOPO_DATASETS, path constants, TEST_MODE, API keys
├── core/
│   ├── dem.py               ← fetch_layer_data, apply_layer_processing, blend_layers, fetch_*_dem
│   ├── export.py            ← generate_stl, generate_obj, generate_3mf, generate_crosssection
│   ├── cache.py             ← write_array_cache (.npz), write_osm_cache (.json.gz), prune_all_caches
│   ├── db.py                ← get_db, init_db, WAL mode (SQLite)
│   ├── osm.py               ← fetch_osm_data, _fill_building_heights, _get_road_width_m
│   └── cities_3d.py         ← generate_city_3mf, 3D building mesh + terrain mesh
└── routers/
    ├── terrain.py           ← /api/terrain/* + /api/dem/merge + /api/export/preview
    ├── regions.py           ← /api/regions/* (SQLite-first CRUD with JSON fallback)
    ├── export.py            ← /api/export/* (STL/OBJ/3MF/crosssection)
    ├── cities.py            ← /api/cities/* (OSM fetch + 3MF city export)
    ├── cache.py             ← /api/cache/* (stats, clear, check)
    └── settings.py          ← /api/settings/* (projections, colormaps, datasets)
```

**Cache**: DEM arrays stored as `.npz` (float32, ~350 KB/region). OSM data as `.json.gz`. Cache key: `MD5(namespace + ":" + bbox_4dp + ":" + sorted_params)`. Pruned at startup via `prune_all_caches()`.

**SQLite**: `data.db` has `regions` + `region_settings` tables. Legacy `coordinates.json` / `region_settings.json` backed up as `.bak` after migration. `scripts/migrate_json_to_sqlite.py` was the one-time migration script.

---

### Feature Status (P1–P9)

| ID | Feature | Status |
|----|---------|--------|
| P1 | Physical dimensions panel in Extrude tab | ✅ Done |
| P2 | Print-bed fit optimizer (auto-scale to fit Bambu 256, Prusa 250, etc.) | Pending |
| P3 | Contour lines baked into STL surface | ✅ Done |
| P4 | Base label engraving (region name embossed on base) | ✅ Done |
| P5 | STL mesh repair (trimesh watertight check + auto-fix) | ✅ Done |
| P6 | Elevation band export — multi-material STL | Pending |
| P7 | Terrain cross-section export (OBJ slice at user-defined lat or lon) | ✅ Done |
| P8 | Flat water surface cap (solid STL floor at sea level over ocean areas) | ✅ Done |
| P9 | Region label editor in UI (rename, re-label, reorder groups) | ✅ Done |

### UI Fixes (A–C)

| ID | Fix | Status |
|----|-----|--------|
| A | Edit button → sidebar goes to 'normal' (not expanded) mode | ✅ Done (session 5) |
| B | Settings panel inner components reflow continuously on resize | ✅ Done (session 5) |
| C | City overlay LOD: road line widths scale with `stackZoom.scale` | ✅ Done |
