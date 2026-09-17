# Layer System — strm2stl

_Last updated: 2026-05-28_

Complete reference for the stacked-layer rendering pipeline, canvas lifecycle, GPU memory management, and the 7-layer compositing system.

For the short version, see [../arch.md](../arch.md).

---

## Overview

The Edit tab displays one of 7 possible data layers at a time on a single visible canvas (`#stackViewCanvas`). All other layer canvases live offscreen. When the user switches layers, the active layer is composited to `stackViewCanvas` and the previous layer's GPU backing store is freed.

```mermaid
flowchart LR
    subgraph Offscreen ["Offscreen layer canvases (hidden)"]
        DEM["#layerDemCanvas"]
        WATER["#layerWaterCanvas"]
        SAT["#layerSatCanvas"]
        SATIMG["#layerSatImgCanvas"]
        CITY["#layerCityRasterCanvas"]
        COMP["#layerCompositeDemCanvas"]
        HYDRO["#layerHydroCanvas"]
        TRAILS["#layerTrailsCanvas"]
    end
    subgraph Visible ["Visible output"]
        STACK["#stackViewCanvas"]
    end
    DEM & WATER & SAT & SATIMG & CITY & COMP & HYDRO & TRAILS --> STACK
```

---

## Layer Registry

`LAYER_CANVAS_IDS` in `stacked-layers.js` maps each layer mode key to its DOM canvas element ID:

| Mode key | Canvas ID | Data source |
|----------|-----------|-------------|
| `Dem` | `layerDemCanvas` | DEM height array → rendered by `dem-loader.js` |
| `Water` | `layerWaterCanvas` | JRC/ESA water mask → rendered by `water-mask.js` |
| `Sat` | `layerSatCanvas` | ESRI satellite tile → rendered by `dem-loader.js` |
| `SatImg` | `layerSatImgCanvas` | High-res satellite imagery → `appState.satImgSourceCanvas` |
| `CityRaster` | `layerCityRasterCanvas` | City height raster → `appState.cityRasterSourceCanvas` |
| `CompositeDem` | `layerCompositeDemCanvas` | Composite DEM output → `appState.compositeDemSourceCanvas` |
| `WaterHydrology` | `layerWaterHydrologyCanvas` | Water mask and HydroRIVERS depressions composited together → `appState.waterHydrologyCanvas`. The separate `Water` and `Hydrology` modes were merged into this one; `#layerHydroCanvas` still exists in the DOM as the hydrology renderer's own target. |
| `MeshImport` | `layerMeshImportCanvas` | Imported STL/OBJ heightmap → `appState.meshSourceCanvas` |
| `Trails` | `layerTrailsCanvas` | Ski and hiking trail relief grids → `appState.trailsSourceCanvas` |

---

## Canvas Lifecycle

```mermaid
stateDiagram-v2
    [*] --> Inactive: Page load (canvas exists, zero size)
    Inactive --> Active: setStackMode(mode)
    Active --> Rendered: updateStackedLayers()
    Rendered --> Active: Next animation frame
    Active --> Freed: setStackMode(other)
    Freed --> Inactive: _freeLayerBuffer()
    note right of Freed : canvas.width = canvas.height = 0\nReleases GPU backing store
```

### Key Functions

| Function | Purpose |
|----------|---------|
| `_getLayerBuffer(mode)` | Look up canvas by `LAYER_CANVAS_IDS[mode]`, return element |
| `_freeLayerBuffer(mode)` | Set `canvas.width = canvas.height = 0` to release GPU memory |
| `setStackMode(mode)` | Deactivate old layer (`_freeLayerBuffer`), activate new layer |
| `updateStackedLayers()` | Composite active layer(s) to `stackViewCanvas` via `drawImage` |
| `moveLayer(mode, direction)` | Reorder layers in the stack |
| `setLayerOpacity(mode, opacity)` | Set alpha for a layer in compositing |
| `getLayerOrder()` | Return current ordered list of active modes |
| `getActiveLayers()` | Return a copy of the set of layers switched on |

---

## Data Pipeline Per Layer

### DEM Layer

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Browser
    participant BE as FastAPI
    participant OT as OpenTopography

    U->>FE: Click "Load DEM"
    FE->>BE: POST /api/terrain/dem
    BE->>OT: Fetch GeoTIFF tiles
    OT-->>BE: Raw elevation data
    BE-->>FE: {values, width, height, vmin, vmax, bbox}
    FE->>FE: renderDEMCanvas() → #layerDemCanvas
    FE->>FE: drawColorbar() + drawHistogram()
    FE->>FE: setLayerStatus('dem', 'ready')
    FE->>FE: updateStackedLayers() → #stackViewCanvas
```

### Water Mask Layer

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant EE as Earth Engine

    FE->>BE: GET /api/terrain/water-mask
    BE->>EE: Query JRC Yearly Water History
    EE-->>BE: Float32 mask array
    BE-->>FE: {values, width, height}
    FE->>FE: renderWaterMask() → #layerWaterCanvas
    FE->>FE: updateStackedLayers()
```

Cached in `waterMaskCache` (LRU, max 20 entries) keyed by `bbox + resolution`.

### Satellite Layer

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant ESRI as ESRI Tile Server

    FE->>BE: GET /api/terrain/satellite
    BE->>BE: calculate_scale_for_dimensions(N,S,E,W, target_dim)
    BE->>ESRI: fetch_bbox_image(bbox, scale)
    ESRI-->>BE: Tiled imagery
    BE->>BE: stitch_tiles_no_rasterio()
    BE-->>FE: PNG image bytes
    FE->>FE: Draw to #layerSatCanvas
    FE->>FE: updateStackedLayers()
```

### Hydrology Layer

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant HDB as HydroRIVERS shapefile

    FE->>BE: GET /api/terrain/hydrology
    BE->>HDB: Load + clip shapefile to bbox
    BE-->>FE: River depression grid {values, width, height}
    FE->>FE: Render to appState.hydrologySourceCanvas
    FE->>FE: Copy to #layerHydroCanvas
    FE->>FE: updateStackedLayers()
```

**Source choice — `hydrorivers` is the default; do not re-litigate.**
The endpoint accepts `source=natural_earth | hydrorivers` but HydroRIVERS wins at every zoom level for two structural reasons:

1. **Thin-feature survival** — the HydroRIVERS rasterizer in [`geo2stl/hydrology.py`](../../geo2stl/hydrology.py) applies `min_buf_deg = pixel_deg * 0.6` so river lines are buffered to at least one pixel wide before rasterization. Without this, tributaries alias away during downsampling. The Natural Earth path has no such buffering, which is why tributaries vanish at continent zoom on NE.
2. **Strahler-weighted depth** — HydroRIVERS carries Strahler order 1–9. Depression depth is computed as `base * (order/9)**exponent`, so major rivers carve deep while small tributaries still register at any printed scale. Natural Earth has no order field, so every river gets the same depth (or none).

Use Natural Earth only as a fallback when (a) the HydroRIVERS regional shapefile hasn't been downloaded yet, or (b) the bbox covers a region HydroRIVERS does not include (e.g. Antarctica).

**Server cache.** Rasterized `river_grid` is cached under `cache/hydrology/{key}.npz` keyed on `(bbox, dim, source, depression_m, scale_m, min_order, order_exponent, projection, clip_nans)` with a 30-day TTL. First Amazon-bbox call ≈200 s; repeats <50 ms. See [`app/server/routers/terrain.py`](../../app/server/routers/terrain.py) `get_terrain_hydrology` and [`app/server/core/cache.py`](../../app/server/core/cache.py).

### Trails Layer

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant OSM as Overpass
    participant USFS as USFS EDW

    FE->>BE: GET /api/terrain/trails
    BE->>OSM: piste/route + path/footway tags in bbox
    BE->>USFS: TrailNFSPublish query in bbox (US only)
    BE-->>FE: {ski_grid, hiking_grid, counts, sources}
    FE->>FE: Render both to appState.trailsSourceCanvas
    FE->>FE: updateStackedLayers()
```

**Both categories always come back together.** The endpoint rasterizes ski and
hiking into two separate grids and returns both, even when the UI is showing
only one. The category checkboxes are therefore a client-side repaint
(`refreshTrailsCategories()` re-renders from `appState.lastTrailsData`) rather
than a second Overpass round trip, which would cost tens of seconds.

**Source choice.** OSM is authoritative for ski: OpenSkiMap and OpenSnowMap are
rendered *from* OSM and publish tiles or planet dumps, not bbox vector queries,
so querying them would return the same geometry by a slower route. The US Forest
Service EDW service is the one genuinely independent source, and it carries
hiking trails only, inside the United States. `source=all` unions what is
available for the bbox and reports which providers actually contributed in the
response's `sources` field.

**Rendering.** Ski is cyan and hiking is orange; where the two overlap, ski wins
so pistes stay readable through a dense path network. Trail lines are buffered
to a minimum of two pixels before rasterization for the same reason the
hydrology rasterizer buffers rivers — a sub-pixel line aliases away entirely at
print resolution.

**Server cache.** Rasterized grids are cached under `cache/trails/{key}.npz`
keyed on `(bbox, dim, source, relief_m, width_m)` with a 7-day TTL matching the
`osm` namespace. Projection is deliberately *not* part of the key: it is applied
per request to the cached raw grids, so changing projection never triggers a
refetch. Categories are not part of the key either, since both grids are always
computed.

### City Raster Layer

Uses two separate rasterization endpoints serving different consumers:

| Endpoint | Canvas | Consumer |
|----------|--------|----------|
| `POST /api/cities/raster` | `#layerCityRasterCanvas` | `city-render.js` — standalone city height view |
| `POST /api/composite/city-raster` | feeds `composite-dem.js` | Composite DEM pipeline |

### Composite DEM Layer

```mermaid
flowchart TD
    DEM2["DEM values"] --> COMP2["composite-dem.js"]
    CITY2["City raster deltas"] --> COMP2
    HYDRO2["Hydrology deltas"] --> COMP2
    WATER2["Water mask"] --> COMP2
    COMP2 --> CANVAS["appState.compositeDemSourceCanvas"]
    CANVAS --> LAYER["#layerCompositeDemCanvas"]
    LAYER --> STACK2["#stackViewCanvas"]
```

---

## Zoom & Pan

All layer canvases and `.osm-overlay` share a CSS transform applied by `applyStackedTransform()`. This keeps all layers in pixel-perfect registration during zoom and pan.

```
applyStackedTransform(scale, offsetX, offsetY)
  → CSS: transform = `scale(${scale}) translate(${offsetX}px, ${offsetY}px)`
  → Applied to: all layer canvases + .osm-overlay
  → Scale change > 15%: immediate re-render of active layer
  → Scale change ≤ 15%: 300ms debounced re-render
```

---

## GPU Memory Management

Each hidden canvas element holds a GPU texture backing store proportional to `width × height × 4 bytes`. For a 1024×1024 canvas that's 4 MB of GPU memory per layer — 28 MB if all 7 layers were live simultaneously.

`_freeLayerBuffer(mode)` zeros the canvas dimensions when a layer is deactivated:

```js
function _freeLayerBuffer(mode) {
    const canvas = _getLayerBuffer(mode);
    if (canvas) {
        canvas.width = 0;
        canvas.height = 0;
    }
}
```

Setting `width = 0` causes the browser to release the GPU backing store immediately (verified in Chrome and Firefox). The canvas element itself remains in the DOM — it is reactivated by drawing to it again next time the layer is selected.

---

## Adding a New Layer

To add an 8th layer:

1. **Add a canvas to `index.html`** inside `#layersStack`:
   ```html
   <canvas id="layerMyNewCanvas" style="display:none"></canvas>
   ```

2. **Register in `LAYER_CANVAS_IDS`** in `stacked-layers.js`:
   ```js
   const LAYER_CANVAS_IDS = {
       // ...existing entries...
       MyNew: 'layerMyNewCanvas',
   };
   ```

3. **Add a backend route** in `routers/terrain.py` (or appropriate router) and a `core/` handler.

4. **Add a frontend loader** that fetches data and renders to `#layerMyNewCanvas`, then calls `updateStackedLayers()`.

5. **Add a UI button** for the layer mode switcher. The mode key must match the `LAYER_CANVAS_IDS` key exactly.

6. **Update this doc** — add the new mode key to the registry table above and describe its data pipeline.

---

## Layer System File Map

| Concern | File |
|---------|------|
| Canvas registry + compositing | `app/client/static/js/modules/layers/stacked-layers.js` |
| DEM rendering | `app/client/static/js/modules/dem/dem-loader.js` |
| Water mask rendering | `app/client/static/js/modules/layers/water-mask.js` |
| City overlay rendering | `app/client/static/js/modules/layers/city-overlay.js` |
| City raster rendering | `app/client/static/js/modules/layers/city-render.js` |
| Composite DEM | `app/client/static/js/modules/layers/composite-dem.js` |
| Hydrology overlay | `app/client/static/js/modules/layers/hydrology-overlay.js` |
| Satellite fetch | `app/server/core/sat.py` |
| Hydrology fetch | `app/server/core/hydrology.py` + `app/server/core/hydrorivers.py` |
| City raster fetch | `app/server/routers/cities.py` + `app/server/routers/composite.py` |
| Layer system analysis (historical) | `../audits/layer-system-analysis.md` |
