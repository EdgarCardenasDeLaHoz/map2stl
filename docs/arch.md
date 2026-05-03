# Architecture — strm2stl

_Last updated: 2026-04-30_

## Single-Page Architecture

strm2stl is intentionally a **single-page application** (SPA). The three top-level views (`Explore`, `Edit`, `Extrude`) are workflow steps, not destinations — a user picks a region on the map, refines it in the DEM editor, then exports. All three steps share the same in-memory state.

### Why SPA and not multi-page

| Concern | Multi-page cost | SPA status quo |
|---------|----------------|----------------|
| Shared live data | Would require serialising ~3-4 MB of canvas/array state (DEM values, water mask, city raster, satellite image) between page loads | State lives in `window.appState` — all views consume it directly |
| Workflow continuity | Users move Explore → Edit → Extrude in sequence; page transitions would feel like a form wizard | `switchView()` transitions in <16 ms; no network round-trip |
| Canvas and WebGL contexts | Three.js globe + Leaflet map + model viewer share one document; recreating GL contexts is expensive | Canvases are always present; inactive ones are hidden and their GPU backing freed via `_freeLayerBuffer()` |
| Region table / cache inspector | These are panel-level concerns, not page-level | Sidebar state machine cycles: `normal → list → table → normal` |

**Multi-page is only worth considering if** the app ever needs to serve regions or terrain data to external consumers (share-a-link to a specific region's DEM). In that case the correct scope is a **shareable URL via `location.hash`** that deep-links into a pre-selected region + view — not a separate HTML page. This would require no architectural changes beyond wiring `switchView()` and `selectCoordinate()` to read `location.hash` on load. See `proposals.md → A-HASH`.

---

## System Overview

### Frontend → Backend → SDK

```mermaid
flowchart LR
    HTML["index.html"] --> APP["app.js"]
    APP --> MAIN["main.js"]
    MAIN --> MODS["modules/<br/>30 ES modules"]
    MODS -->|"fetch /api/*"| ROUTERS["FastAPI<br/>routers/"]
    ROUTERS --> CORE["core/<br/>business logic"]
    SESSION["TerrainSession"] -->|HTTP| ROUTERS
    NB["notebooks/"] --> SESSION
```

### Backend → External Data

```mermaid
flowchart LR
    CORE["core/"] --> OT["OpenTopography<br/>DEM tiles"]
    CORE --> EE["Earth Engine<br/>ESA / JRC"]
    CORE --> OSM["Overpass API<br/>OSM buildings"]
    CORE --> G3D["Google 3D Tiles"]
    CORE --> USGS["USGS 3DEP"]
```

### Frontend Module Groups

```mermaid
flowchart TD
    subgraph Modules ["modules/ (8 subdirs)"]
        CORE_JS["core/ — api, state, events, cache"]
        DEM_JS["dem/ — loader, renderer, merge, gridlines"]
        LAYERS_JS["layers/ — stacked, water, city, composite"]
        MAP_JS["map/ — bbox, compare, globe"]
        UI_JS["ui/ — curves, presets, shortcuts, views"]
        EXPORT_JS["export/ — STL/OBJ/3MF, model viewer"]
        REGIONS_JS["regions/ — list, CRUD"]
        EVENTS_JS["events/ — listeners"]
    end
```

## Frontend

### Module Boundary

```mermaid
flowchart LR
    APP["app.js<br/>(script tag)"] -->|"reads/writes"| G["window.*<br/>appState, events, api"]
    MAIN["main.js<br/>(ES module)"] -->|"exposes to"| G
    MAIN --> M["modules/<br/>(8 subdirs)"]
    M -->|"coordinate via"| G
    HTML["index.html<br/>onclick=…"] -->|"calls"| G
```

**`app.js`** is a plain `<script>` tag (~333 lines of state container + DOMContentLoaded init). It is **not** an ES module. Public functions must stay on `window.*`. HTML `onclick` handlers reference these global names directly.

**`main.js`** (`type="module"`) is the ES module entry point. It imports 30 modules from `modules/` in dependency order. Modules expose functions on `window.*` so `app.js` can call them.

Key cross-module contracts:
- `window.appState` — Proxy-based reactive state (state.js). All modules read/write via this.
- `window.events` / `window.EV` — Event bus + constants (events.js).
- `window.api.*` — All fetch helpers (api.js).
- Modules must **not** import each other — all coordination is via `window.*`.

### View System

```mermaid
stateDiagram-v2
    [*] --> Explore
    Explore --> Edit : switchView('dem')
    Edit --> Extrude : switchView('model')
    Extrude --> Explore : switchView('map')
    Explore --> Extrude : switchView('model')
    Extrude --> Edit : switchView('dem')
    Edit --> Explore : switchView('map')

    state Edit {
        [*] --> DEM
        DEM --> Water : switchDemSubtab
        Water --> LandCover : switchDemSubtab
        LandCover --> Satellite : switchDemSubtab
        Satellite --> Combined : switchDemSubtab
        Combined --> DEM : switchDemSubtab
    }
```

Three top-level tabs:
- **Explore** (`data-view="map"`) — Leaflet map + globe
- **Edit** (`data-view="dem"`) — DEM canvas, water mask, land cover, stacked layers, compare
- **Extrude** (`data-view="model"`) — 3D model viewer, STL/OBJ/3MF export

Within "Edit", DEM sub-tabs (managed by `switchDemSubtab()`):
- `dem`, `water`, `landcover`, `combined`, `satellite`

### Sidebar State Machine

`cycleSidebarState()` → `'normal'` → `'list'` → `'table'` → `'normal'`

### Preset Versioning & Revert

Presets are stored in `localStorage` as JSON objects. `PRESET_VERSION` (currently `1`) guards against stale preset shapes. When a preset is loaded, `_migratePreset(preset)` merges it with `builtInPresets['default']` so that any new settings keys are populated with defaults.

**Revert flow:**

```mermaid
sequenceDiagram
    participant U as User
    participant P as presets.js
    participant LS as localStorage

    U->>P: loadSelectedPreset()
    P->>P: _presetSnapshot = collectAllSettings()
    P->>P: applyAllSettings(preset)
    P->>P: show #revertPresetBtn
    U->>P: click "↩ Revert"
    P->>P: revertPreset()
    P->>P: applyAllSettings(_presetSnapshot)
    P->>P: hide #revertPresetBtn, clear _presetSnapshot
```

When the user saves a new preset, `{ ...getCurrentSettings(), _version: PRESET_VERSION }` is written to `localStorage`. On next load, `_migratePreset()` merges any missing keys from the built-in default.

### Region List Pagination

The regions table in the sidebar supports search and 20-row pagination managed entirely in `region-ui.js`:

- `TABLE_PAGE_SIZE = 20` (module constant)
- `_tablePage` and `_tableSearch` hold current pagination state
- `populateRegionsTable()` filters by search string, slices to current page, and re-renders
- `_renderTablePagination(total, totalPages)` writes Prev/Next buttons into `#regionsPagination`; the div is hidden when total ≤ 20

An `indexByName` Map preserves original region indices (for settings lookups) across filtered/paginated views.



All 7 layer canvases are hidden offscreen buffers. `stackViewCanvas` is the only visible canvas. `updateStackedLayers()` composites the active mode buffers onto `stackViewCanvas`. `setStackMode(mode)` switches active mode.

**Layer canvas registry (`LAYER_CANVAS_IDS`)** in `stacked-layers.js` maps each layer mode key to its DOM canvas ID:

| Mode key | Canvas ID |
|----------|-----------|
| `Dem` | `layerDemCanvas` |
| `Water` | `layerWaterCanvas` |
| `Sat` | `layerSatCanvas` |
| `SatImg` | `layerSatImgCanvas` |
| `CityRaster` | `layerCityRasterCanvas` |
| `CompositeDem` | `layerCompositeDemCanvas` |
| `Hydrology` | `layerHydroCanvas` |

**Canvas lifecycle:** `_getLayerBuffer(mode)` retrieves the canvas by registry lookup. `_freeLayerBuffer(mode)` zeros `width` and `height` to release the GPU backing store when a layer is deactivated. This prevents accumulation of GPU memory for layers not currently in use.

```mermaid
stateDiagram-v2
    [*] --> Inactive
    Inactive --> Active : setStackMode(mode)
    Active --> Composited : updateStackedLayers()
    Active --> Inactive : setStackMode(other) → _freeLayerBuffer()
    Composited --> Active : next frame
```

---

## Backend

The backend (`app/server/`) is a thin layer over two support libraries: `geo2stl` (map projections, tile stitching) and `numpy2stl` (mesh generation). See [reference/libraries.md](reference/libraries.md) for the full import map and integration assessment.

### Structure

See `../CLAUDE.md` for the complete project layout.
<!-- Note: CLAUDE.md lives outside docs/; this link works on GitHub but not MkDocs -->
Backend detail:

```
app/
├── server/                    — HTTP server (Python/FastAPI)
│   ├── server.py    — FastAPI app init, lifespan, router includes
│   ├── schemas.py   — all ~30 Pydantic models
│   ├── config.py    — paths, OPENTOPO_DATASETS, TEST_MODE, API keys
│   ├── core/
│   │   ├── dem.py        — fetch_layer_data, apply_layer_processing, blend_layers
│   │   ├── export.py     — generate_stl/obj/3mf/crosssection
│   │   ├── cache.py      — write/read_array_cache (.npz), write/read_osm_cache (.json.gz), prune
│   │   ├── db.py         — get_db, init_db, WAL mode (SQLite)
│   │   ├── osm.py        — fetch_osm_data, _fill_building_heights, _get_road_width_m
│   │   ├── cities_3d.py  — generate_city_3mf, 3D building mesh
│   │   ├── hydrorivers.py — HydroRIVERS shapefile loading + simplification
│   │   ├── hydrology.py  — river depression grid rasterization
│   │   ├── sat.py        — satellite imagery fetch (ESRI + Earth Engine)
│   │   ├── projection.py — CRS transforms, coordinate utils
│   │   ├── validation.py — input validation helpers
│   │   ├── responses.py  — standardized API response builders
│   │   └── height/       — building height estimation package
│   │       ├── __init__.py     — HeightResult, HeightProvider, merge_height_rasters()
│   │       └── providers/      — wsf3d, google_3d, copernicus, ndsm, lidar_3dep
│   └── routers/
│       ├── terrain.py    — /api/terrain/*
│       ├── regions.py    — /api/regions/* (SQLite-first, JSON fallback)
│       ├── export.py     — /api/export/*
│       ├── cities.py     — /api/cities/*
│       ├── composite.py  — /api/composite/* (city-raster, dem-merge, hydrology-merge)
│       ├── cache.py      — /api/cache/*
│       ├── settings.py   — /api/settings/* + combined /api/settings
│       └── height.py     — /api/height/* (prefix: /api/height)
├── client/                    — browser client (HTML/CSS/JS)
│   ├── static/js/   — main.js, modules/ (30 ES modules in 8 subdirs)
│   ├── static/css/  — app.css
│   └── templates/   — index.html
└── session/                   — Python SDK client (talks to server over HTTP)
    ├── terrain_session.py
    └── viz.py
```

### Key Backend Rules
- Business logic in `core/`, request handling in `routers/`
- Never use `os.chdir()` in handlers — process-global, causes data races
- See `../CLAUDE.md` for async/executor constraints and full editing guidelines
<!-- Note: CLAUDE.md lives outside docs/; this link works on GitHub but not MkDocs -->

### numpy2stl Delegation Pattern

`core/cities_3d.py` and `core/export.py` delegate to `numpy2stl` with availability guards — the library is tried first and a manual fallback runs if unavailable.

```mermaid
flowchart TD
    CALL["Core function called"] --> TRY["Try numpy2stl import"]
    TRY --> AVAIL{"Available?"}
    AVAIL -->|Yes| NUMPY["Call numpy2stl function<br/>(array_to_mesh, polygon_to_prism, etc.)"]
    AVAIL -->|No| FALLBACK["Run built-in fallback<br/>(ear-clip, manual mesh)"]
    NUMPY --> SUPPRESS["Suppress stdout<br/>(progress messages)"]
    SUPPRESS --> RESULT["Return result"]
    FALLBACK --> RESULT
```

Key guard pattern:
```python
_ARRAY_TO_MESH_AVAILABLE = False
try:
    from numpy2stl import array_to_mesh as _array_to_mesh
    _ARRAY_TO_MESH_AVAILABLE = True
except ImportError:
    pass

# In function body:
if _ARRAY_TO_MESH_AVAILABLE:
    import io, sys
    _buf = io.StringIO()
    sys.stdout, _saved = _buf, sys.stdout
    try:
        result = _array_to_mesh(scaled, floor_val=0.0, solid=True)
    finally:
        sys.stdout = _saved
else:
    result = _manual_fallback(...)
```

### Async Export Pipeline

Long-running exports (puzzle 3MF, large STL) use a background-thread task system:

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant BG as Background Thread
    participant FS as Filesystem

    FE->>BE: POST /api/export/start {format, dem_values, ...}
    BE->>BG: threading.Thread(target=_run_export_pipeline)
    BE-->>FE: {task_id}
    loop Poll every 300ms
        FE->>BE: GET /api/export/status/{task_id}
        BE-->>FE: {status, progress, message}
    end
    BG->>FS: Write temp file
    BG->>BE: task.complete(path, filename)
    FE->>BE: GET /api/export/download/{task_id}
    BE-->>FE: FileResponse (temp file deleted after send)
```

Tasks expire after 300 seconds. The temp file is deleted by a `BackgroundTask` after the download response is sent. Direct sync endpoints (`POST /api/export/stl`, etc.) still exist for non-interactive callers.

### Cache
- DEM: `.npz` (float32) + `.json` sidecar under `cache/dem/`
- OSM: `.json.gz` under `cache/osm/`
- Key: `MD5(namespace + ":" + "N{n:.4f}_S..." + ":" + sorted_json(extra))`
- OSM key shorthand: `osm_cache_key(N, S, E, W, tol=0.5, min_area=5.0)` in `core/cache.py`
- Pruned at startup via `prune_all_caches()`

### SQLite
- `data.db` — `regions` + `region_settings` tables, WAL mode
- `region_settings` has `ON DELETE CASCADE` from `regions`
- JSON fallback active when `core.db` unavailable (used in tests)

## Python SDK

`app/session/terrain_session.py` is the Python client that drives the same server used by the browser app.

Use it when the workflow is notebook-driven or when a script needs to reproduce the terrain pipeline without using the UI.

The shortest example path is:

`notebooks/API_Terrain.ipynb` → `app/session/terrain_session.py` → router in `app/server/routers/` → processing in `app/server/core/`

Use these companion docs:

- `sdk-workflow.md` for notebook-to-method-to-route tracing
- `api.md` for the route index
- `task-routing.md` for deciding which layer to edit

---

## HTML Structure

`templates/index.html` — single-page app, all content always in DOM.

```
body
├── #toastContainer
├── #regionNotesModal
└── .page-wrapper
    ├── .sidebar
    │   ├── #sidebarListView → #coordSearch, #coordinatesList
    │   └── #sidebarTableView → #sidebarRegionsTable
    └── .main-content
        ├── .main-header → .tabs (Explore/Edit/Extrude)
        └── .content-area
            ├── #mapContainer → #map, .map-floating-controls, #regionsPanel
            ├── #globeContainer → #globe
            └── [DEM/Model panels]
```

Key IDs: `#floatingDrawBtn`, `#citiesTab`, `#loadCitiesBtn`, `#modelViewer`, `#stackViewCanvas`, `#demImage`, `#layersStack`

---

## Data Flows

### DEM Load

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Browser
    participant BE as FastAPI
    participant OT as OpenTopography

    U->>FE: Click "Load DEM"
    FE->>BE: POST /api/terrain/dem
    BE->>OT: Fetch DEM tile
    OT-->>BE: GeoTIFF
    BE-->>FE: Float32 array + metadata
    FE->>FE: renderDEMCanvas + histogram
```

### Water Mask

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant EE as Earth Engine

    FE->>BE: GET /api/terrain/water-mask
    BE->>EE: Query JRC Water
    EE-->>BE: Mask array
    BE-->>FE: Uint8 mask
    FE->>FE: renderWaterMask
```

### City Overlay

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant OSM as Overpass API

    FE->>BE: POST /api/cities
    BE->>OSM: Overpass query
    OSM-->>BE: Building polygons
    BE-->>FE: GeoJSON + heights
    FE->>FE: renderCityOverlay
```

### STL Export

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI

    FE->>BE: POST /api/export/stl
    BE->>BE: Generate mesh
    BE-->>FE: Binary STL blob
    FE->>FE: Browser download
```

### DEM Load
```
loadDEM() → POST /api/terrain/dem
  → store lastDemData, originalDemValues
  → renderDEMCanvas() → drawColorbar() → drawHistogram() → updateAxesOverlay()
  → setLayerStatus('dem', 'ready')
  → applyCurveTodemSilent() if curve active
```

### Water Mask
```
loadWaterMask() → waterMaskCache.has? return cached : GET /api/terrain/water-mask
  → waterMaskCache.set() → renderWaterMask() → setLayerStatus('water', 'ready')
```

### STL Export
```
downloadSTL() → POST /api/export/stl {dem_values, depth_scale, base, ...}
  → blob → browser download
```

### City Overlay
```
loadCityData() → POST /api/cities → _computeTerrainZ() → store osmCityData
  → renderCityOverlay() [RAF-debounced]
    → _drawCityCanvas(): buildings batched by alpha (8 groups), sub-pixel skipped
    → renderCityOnDEM?() — also paints .city-dem-overlay on DEM canvas

Zoom/pan → applyStackedTransform()
  → CSS transform on all canvases + .osm-overlay
  → scale change >15%: immediate re-render; else: 300ms debounced
```

### Async Export (Puzzle / Large Formats)

```mermaid
sequenceDiagram
    participant FE as Browser
    participant BE as FastAPI
    participant BG as Background Thread

    FE->>BE: POST /api/export/start {format, dem_values, ...}
    BE->>BG: Start _run_export_pipeline in thread
    BE-->>FE: {task_id}
    loop Poll until complete
        FE->>BE: GET /api/export/status/{task_id}
        BE-->>FE: {status, progress 0-100, message}
    end
    FE->>BE: GET /api/export/download/{task_id}
    BE-->>FE: File download (temp file auto-deleted)
```

Sync endpoints (`POST /api/export/stl`, `/api/export/obj`, `/api/export/3mf`) still work for non-interactive callers.

### 3D Preview (Extrude tab)

```mermaid
sequenceDiagram
    participant U as User
    participant FE as Browser
    participant BE as FastAPI

    U->>FE: Click "Preview 3D"
    FE->>BE: POST /api/export/preview {dem_values, exaggeration, ...}
    BE-->>FE: {vertices, faces, z_min, z_max, face_count}
    FE->>FE: _buildMeshFromPreview() → THREE.BufferGeometry
    FE->>FE: _replaceMesh() → modelScene.add()
    FE->>FE: _fitCameraToMesh() → _updateHud()
```

Vertex layout from numpy2stl: `[col, row, z_mm]`. In Three.js space: `x=col`, `y=z_mm` (elevation up), `z=row`. Colormap is applied per-vertex with `window.mapElevationToColor(t, cmap)` from `dem-loader.js`. No server round-trip is needed to change colormap — `rebuildViewerColors(cmap)` recomputes all vertex colors from the already-loaded geometry.

### Satellite Scale Calculation

```
fetch_sat_overlay(N, S, E, W, dim, width_px):
  target_dim = max(width_px, dim or width_px)
  scale = max(30, calculate_scale_for_dimensions(N, S, E, W, target_dim))
    ↳ geo2stl.sat2stl.calculate_scale_for_dimensions()
  → fetch_bbox_image(bbox, scale)
```

`calculate_scale_for_dimensions` computes the appropriate tile zoom level for the bbox diagonal, eliminating the manual scale estimation that previously caused under- or over-resolution satellite tiles.

