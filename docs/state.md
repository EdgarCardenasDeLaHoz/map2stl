# Global State Reference — strm2stl

_Last updated: 2026-05-14_

All variables live on the `window.appState` reactive Proxy (from `modules/core/state.js`). Modules can subscribe via `window.appState.on('key', fn)`. Legacy `window.get*/set*` aliases are kept for backward compatibility.

### State ownership overview

```mermaid
flowchart TD
    APP["app.js init"] --> MAP["Map & Globe"]
    APP --> REG["Region Management"]
    APP --> DEM["DEM & Layer Data"]
    APP --> STYLE["Appearance & Settings"]
    APP --> CITY["City Overlay"]
    WAS["window.appState"] -.->|owns all| MAP
    WAS -.->|owns all| DEM
    WAS -.->|owns all| CITY
    LS["localStorage"] -.->|persists| STYLE
```

## Map & Globe

| Key | Type | Description |
|-----|------|-------------|
| `map` | Leaflet.Map | Main 2D map instance |
| `globeScene` | THREE.Scene | Three.js scene for globe |
| `globeCamera` | THREE.PerspectiveCamera | Globe camera |
| `globeRenderer` | THREE.WebGLRenderer | Globe renderer |
| `globe` | THREE.Mesh | Globe sphere mesh |
| `drawnItems` | L.FeatureGroup | Drawn bbox rectangles |
| `preloadedLayer` | L.FeatureGroup | Preloaded region boxes |
| `editMarkersLayer` | L.FeatureGroup | Edit buttons inside each bbox |
| `boundingBox` | L.Rectangle\|null | Currently active bbox |

## Region Management

| Key | Type | Description |
|-----|------|-------------|
| `coordinatesData` | Array | `{name, label, north, south, east, west}[]` |
| `selectedRegion` | Object\|null | Currently selected region |

## DEM & Layer Data

| Key | Type | Description |
|-----|------|-------------|
| `lastDemData` | Object\|null | `{values, width, height, min, max, bbox}` |
| `lastWaterMaskData` | Object\|null | Water mask + ESA response |
| `currentDemBbox` | Object\|null | `{north,south,east,west}` for current DEM |
| `layerBboxes` | Object | `{dem, water, landCover}` each bbox or null |
| `layerStatus` | Object | `{dem, water, landCover}` — 'empty'\|'loading'\|'ready'\|'error' |
| `activeDemSubtab` | String | Current DEM sub-tab name |
| `demLayout` | Object | `{x, y, w, h}` pixel layout of the active DEM canvas within the viewport |
| `satImgSourceCanvas` | HTMLCanvasElement\|null | Offscreen canvas holding satellite imagery (set by `city-render.js` / `stacked-layers.js`) |
| `cityRasterSourceCanvas` | HTMLCanvasElement\|null | Offscreen canvas holding city raster (set by `city-render.js`) |
| `compositeDemSourceCanvas` | HTMLCanvasElement\|null | Offscreen canvas holding composite DEM output (set by `composite-dem.js`) |

## Appearance & Settings

| Key | Type | Description |
|-----|------|-------------|
| `landCoverConfig` | Object | ESA class → `{color, label, visible}` |
| `waterOpacity` | Number | 0–1, default 0.7 |
| `curvePoints` | Array | `[{x,y}]` curve editor control points |
| `userPresets` | Object | Named presets from localStorage |
| `regionNotes` | Object | `{regionName: text}` from localStorage |
| `sidebarState` | String | 'normal'\|'expanded'\|'hidden' |

**Preset module-level state** (in `ui/presets.js`, not on `window.appState`):

| Variable | Type | Description |
|----------|------|-------------|
| `PRESET_VERSION` | Number (const) | Currently `1`; guards against stale preset shapes in localStorage |
| `_presetSnapshot` | Object\|null | Settings snapshot taken before loading a preset; cleared on revert or new preset load |

**Region table module-level state** (in `regions/region-ui.js`, not on `window.appState`):

| Variable | Type | Description |
|----------|------|-------------|
| `TABLE_PAGE_SIZE` | Number (const) | `20` — rows per page |
| `_tablePage` | Number | Current page index (0-based) |
| `_tableSearch` | String | Current search filter text |

## City Overlay

| Key | Type | Description |
|-----|------|-------------|
| `osmCityData` | Object\|null | `{buildings, roads, waterways, walls}` GeoJSON. Features have `height_m`, `road_width_m` (server), `terrain_z` (client), `_bbox` (pre-computed). |
| `window.renderCityOnDEM` | Function | Set by city-overlay.js; paints `.city-dem-overlay` on DEM canvas |
| `hydrologySourceCanvas` | HTMLCanvasElement\|null | Offscreen canvas with rendered river depression grid (set by hydrology-overlay.js) |
| `waterHydrologyCanvas` | HTMLCanvasElement\|null | Offscreen canvas with the combined water + hydrology layer (set by water-hydrology-combined.js); read by stacked-layers.js |
| `lastLandCoverData` | Object\|null | ESA WorldCover classification response |

## 3D Viewer

| Key | Type | Description |
|-----|------|-------------|
| `terrainMesh` | THREE.Mesh\|null | Current 3D terrain mesh in the Extrude viewer |
| `viewerScene` | THREE.Scene\|null | The Three.js scene for the model viewer |
| `generatedModelData` | Object\|null | `{values, width, height, resolution, exaggeration, baseHeight, vmin, vmax}` — last preview parameters, used by download buttons |

## Other

| Key | Type | Description |
|-----|------|-------------|
| `stackedLayerData` | Object | `{dem, water, landCover}` each `{canvas, bbox, label}` |
| `compareData` | Object | `{left: {region, dem, ...}, right: {...}}` |
| `_mergeSources` | Array | Available DEM source descriptors |
| `_mergeLayers` | Array | Current merge layer stack |
| `waterMaskCache` | Object | File-top LRU, max 20 entries. Methods: `get/set/has/generateKey/getStats/clear` |

## window.appState Keys (modules read these)

Mirrored from closure. Set via `appState.set(key, val)` or direct assignment:

| Key | Source | Used by |
|-----|--------|---------|
| `selectedRegion` | closure | city-overlay, regions, stacked-layers |
| `currentDemBbox` | closure | dem-loader, city-overlay, stacked-layers |
| `lastDemData` | closure | dem-loader, composite-dem, model-viewer |
| `osmCityData` | closure | city-overlay, composite-dem |
| `lastWaterMaskData` | closure | water-mask, composite-dem |
| `originalDemValues` | appState-only | curve-editor |
| `curveDataVmin` | appState-only | curve-editor, dem-main |
| `curveDataVmax` | appState-only | curve-editor, dem-main |
| `curvePoints` | closure | curve-editor |
| `layerBboxes` | closure (shared ref) | stacked-layers |
| `layerStatus` | closure (shared ref) | ui-helpers |
| `compositeSourceCanvas` | — | stacked-layers, composite-dem |
| `compositeFeatures` | — | composite-dem |
| `satImgSourceCanvas` | — | composite-dem, stacked-layers |
| `cityRasterSourceCanvas` | — | city-render, stacked-layers |
| `compositeDemSourceCanvas` | — | composite-dem, stacked-layers |
| `demLayout` | — | stacked-layers, dem-loader |
| `terrainMesh` | model-viewer | export-handlers |
| `viewerScene` | model-viewer | (read-only) |
| `generatedModelData` | model-viewer | export-handlers, puzzle export |
| `_setDemEmptyState` | callback | dem-main |
| `_updateWorkflowStepper` | callback | dem-main, model-viewer |
