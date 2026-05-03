# Terrain API Call Stack Audit

_Last updated: 2026-05-03_

Audit of every endpoint in the terrain and composite routers, tracing each call stack from HTTP handler through core modules and library functions to external data sources.

**Post-audit changes applied:**
**Post-audit changes (passes 2026-04 → 2026-05):**
- `/api/terrain/dem/raw` — **removed** (inferior duplicate of `/api/terrain/dem`, unused by frontend)
- `/api/export/preview` alias in terrain.py — **removed** (was shadowing the real handler in export.py; 3D viewer now works)
- `/api/dem/merge` — **moved** to `/api/composite/dem-merge` (composite router)
- `/api/terrain/hydrology/merge` — **moved** to `/api/composite/hydrology-merge` with Pydantic schema + b64 response
- `/api/terrain/esa-land-cover` — **fixed** to call `fetch_water_mask_images()` directly (no longer fetches+discards SRTM/water mask)
- `_fetch_and_rasterize_hydrology()` — **moved** from router to `geo2stl.hydrology`
- `_fetch_dem_array()` — **removed** from terrain router; replaced by `geo2stl.dem.fetch_dem_from_source()` directly
- `_CACHE_AVAILABLE` guard pattern — **removed** from cities router; cache always available, imported unconditionally
- `_load_osm_cache()` / `_save_osm_cache()` wrappers — **removed** from cities router; direct `read_osm_cache()`/`write_osm_cache()` calls
- `core/terrain_raster.py` + `core/osm_cache_policy.py` — converted to **deprecated compatibility wrappers**; implementations live in `geo2stl.raster` and `city2stl.cache_policy`

---

## Endpoint Inventory

### Terrain Router (`/api/terrain/`)

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 1 | GET/POST | `/api/terrain/dem` | DEM preview with optional satellite overlay |
| 2 | GET/POST | `/api/terrain/water-mask` | Binary water mask + ESA land cover |
| 3 | GET/POST | `/api/terrain/esa-land-cover` | ESA WorldCover class data only |
| 4 | GET | `/api/terrain/satellite` | ESRI World Imagery WMTS tiles |
| 5 | GET | `/api/terrain/sources` | Available DEM data sources list |
| 6 | GET | `/api/terrain/hydrology` | River depression raster |

### Composite Router (`/api/composite/`)

| # | Method | Path | Purpose |
|---|--------|------|---------|
| 7 | POST | `/api/composite/city-raster` | Rasterize OSM features to height-delta grids |
| 8 | POST | `/api/composite/dem-merge` | Multi-layer composite DEM |
| 9 | POST | `/api/composite/hydrology-merge` | Merge river depressions into DEM |

---

## 1. `/api/terrain/dem` — DEM Preview

**Handler**: `get_terrain_dem()` (line 154)

```
get_terrain_dem()
├── validation.parse_bbox_query() + validate_bbox() + validate_dim()   — core.validation
├── cache.make_cache_key("dem", ...)                                    — core.cache
├── cache.read_array_cache("dem", key)                                  — core.cache
│   └── [HIT] → geo2stl.dem.make_dem_payload() → return
├── [TEST_MODE] → np.linspace() gradient
│   └── geo2stl.projections.project_grid()
│   └── geo2stl.dem.make_dem_payload()
├── run_sync(geo2stl.dem.fetch_dem_from_source, ...)                    — thread pool
│   └── fetch_dem_from_source(source, N,S,E,W, dim, **kw)
│       ├── "h5_local" → geo2stl.dem.fetch_h5_dem()                    — h5py tile reader
│       │   └── fallback → geo2stl.dem.fetch_opentopo_dem()             — HTTP + rasterio
│       ├── OPENTOPO key → geo2stl.dem.fetch_opentopo_dem()
│       │   └── requests.get(portal.opentopography.org) + rasterio
│       ├── "water_esa" → geo2stl.dem.fetch_esa_water_layer()
│       │   └── geo2stl.sat2stl.fetch_bbox_image() + cv2.resize
│       ├── "local" → geo2stl.dem.fetch_local_dem()
│       │   └── geo2stl.tiles.stitch_tiles_no_rasterio()               — SRTM tile stitch
│       └── projection applied inside fetch_dem_from_source
│           └── geo2stl.projections.project_grid()
├── geo2stl.dem.upsample_dem()                                          — cv2.resize if native < dim
├── geo2stl.dem.make_dem_payload()                                      — b64 encode + stats
├── [show_sat] → run_sync(geo2stl.sat2stl.fetch_sat_overlay, ...)
│   ├── geo2stl.raster.derive_sat_scale(bbox, dim)                     — scale math
│   ├── geo2stl.sat2stl.fetch_sat_overlay()
│   │   └── geo2stl.sat2stl.fetch_bbox_image()
│   │   └── geo2stl.sat2stl.calculate_scale_for_dimensions()
│   └── geo2stl.projections.project_rgb_image()
└── cache.write_array_cache("dem", ...)                                 — core.cache

Classes/objects: None (all free functions)
Modules: geo2stl.dem, geo2stl.sat2stl, geo2stl.projections, geo2stl.raster, geo2stl.tiles, core.cache, core.validation
Libraries: h5py, rasterio, cv2, requests
```

---

## ~~2. `/api/terrain/dem/raw`~~ — REMOVED

Dropped: inferior duplicate of `/api/terrain/dem` with hardcoded projection, no caching, no source flexibility. `compute_raw_dem()` remains in `core/dem.py` for use by `cities.py` (Google 3D height enhancement).

---

## 2. `/api/terrain/water-mask` — Water Mask + ESA

**Handler**: `get_terrain_water_mask()` (line 364)

**Scale parameter (updated):** Query accepts `dim` (int, pixels per side, default 600) — **not** `sat_scale`. The handler computes `sat_scale = max(10, ceil(longer_bbox_m / dim))` using the midpoint-latitude formula, then passes it to `sat.fetch_water_mask`. The cache key uses the `"dim"` field. The response includes `resolution_m: sat_scale` in all paths (cache hit, TEST_MODE, live).

```
get_terrain_water_mask()
├── core.validation.parse_bbox_query() + validate_bbox()
├── geo2stl.raster.clamp_esa_scale(bbox, dim)           — scale math
├── core.cache.make_cache_key("water", ...)
├── core.cache.read_array_cache("water", key)
│   └── [HIT] → b64 encode both arrays → return
├── [TEST_MODE] → synthetic water block
│   └── geo2stl.projections.project_water_arrays()
├── run_sync(geo2stl.sat2stl.fetch_water_mask, ...)
│   └── fetch_water_mask()
│       ├── scale auto-clamping (50MB + 32768px limits)
│       ├── geo2stl.sat2stl.fetch_water_mask_images()
│       │   ├── geo2stl.sat2stl.fetch_bbox_image(dataset="esa")  — Earth Engine
│       │   ├── [jrc] → geo2stl.sat2stl.fetch_bbox_image(dataset="jrc") — Earth Engine
│       │   └── geo2stl.tiles.stitch_tiles_no_rasterio()          — SRTM for bathymetry
│       ├── [jrc] → JRC threshold (>50) → water_mask
│       ├── [esa] → ESA class 80 → water_mask
│       └── [bbox > 30km] → SRTM bathymetry augmentation (elev < -2)
├── geo2stl.projections.project_water_arrays()       — dual-array aligned projection
└── core.cache.write_array_cache("water", ...)

Classes/objects: None
Modules: geo2stl.sat2stl, geo2stl.projections, geo2stl.raster, geo2stl.tiles, core.cache, core.validation
Libraries: cv2, Earth Engine API
```

---

## 4. `/api/terrain/esa-land-cover` — ESA WorldCover Only

**Handler**: `get_terrain_esa_land_cover()` (line 488)

**Scale parameter:** Same as water-mask — query accepts `dim` (pixels per side), handler computes `sat_scale` from bbox + dim via `geo2stl.raster.clamp_esa_scale()`. Response includes `resolution_m`.

```
get_terrain_esa_land_cover()
├── core.validation.parse_bbox_query() + validate_bbox()
├── geo2stl.raster.clamp_esa_scale(bbox, dim)
├── core.cache.make_cache_key("esa_lc", ...)
├── core.cache.read_array_cache("esa_lc", key)
│   └── [HIT] → b64 encode → return
├── [TEST_MODE] → uniform class array
│   └── geo2stl.projections.project_grid(categorical=True)
├── run_sync(geo2stl.sat2stl.fetch_water_mask, ..., "esa")  ← reuses water mask fetch path
│   └── fetch_water_mask() — discards water_mask (_wm), keeps esa img
│       └── (same stack as water-mask above)
├── geo2stl.projections.project_grid(categorical=True)
│   └── geo2stl.projections.project_coordinates(order=0)
└── core.cache.write_array_cache("esa_lc", ...)

Classes/objects: None
Modules: geo2stl.sat2stl, geo2stl.projections, geo2stl.raster, core.cache, core.validation
Libraries: Earth Engine API
```

**Note**: Fixed — now calls `fetch_water_mask_images()` directly with scale-clamping guards, skipping the water mask + SRTM bathymetry pipeline entirely.

---

## 5. `/api/terrain/satellite` — Real Satellite Imagery

**Handler**: `get_terrain_satellite()` (line 575)

```
get_terrain_satellite()
├── validation.parse_float/parse_int/parse_bool()
├── validation.validate_bbox() + validate_dim()
├── [TEST_MODE] → PIL.Image solid green
│   └── projection.project_rgb_image()
│       └── 3x geo2stl.projections.project_coordinates()
├── run_sync(sat.fetch_satellite_tiles, ...)
│   └── sat.fetch_satellite_tiles()
│       ├── sat._calculate_optimal_zoom()
│       ├── requests.Session → fetch WMTS tiles from server.arcgisonline.com
│       ├── PIL.Image tile stitching + bbox crop
│       ├── sat._mercator_to_plate_carree()          — vertical de-Mercator resample
│       ├── PIL.Image.resize to target dim
│       └── base64 encode JPEG
├── [projection != "none"]
│   ├── base64 decode → PIL → np.array
│   ├── run_sync(projection.project_rgb_image, ...)
│   │   └── 3x geo2stl.projections.project_coordinates()
│   ├── PIL.Image.fromarray → JPEG → base64
└── JSONResponse with b64 JPEG string

Classes/objects: None
Core modules: sat, projection, validation
Libraries: PIL/Pillow, requests, geo2stl.projections
```

**Note**: Only endpoint that returns an image (JPEG b64) rather than array data. No caching.

---

## 6. `/api/terrain/sources` — Data Source List

**Handler**: `get_terrain_sources()` (line 651)

```
get_terrain_sources()
├── config.H5_SRTM_AVAILABLE                         — check h5 file presence
├── config.OPENTOPO_API_KEY                           — check API key
├── config.OPENTOPO_DATASETS                          — iterate dataset registry
└── JSONResponse with sources list

Classes/objects: None
Core modules: config only
Libraries: None
```

**Note**: Pure config read. No computation, no I/O, no caching. Cleanest endpoint.

---

## 7. `/api/composite/dem-merge` — Multi-Layer Composite

**Router**: `composite.py` (moved from terrain.py)

```
merge_dem_layers()
├── schemas.MergeRequest (Pydantic validation)
│   └── contains List[MergeLayerSpec]
│       └── each has ProcessingSpec
├── validate bbox from req.bbox dict
├── [TEST_MODE] → np.linspace gradient → return
├── for each layer in req.layers:
│   ├── run_sync(dem.fetch_layer_data, spec.source, ...)
│   │   └── (same dispatch as #1: h5, opentopo, local, water_esa)
│   ├── run_sync(dem.apply_layer_processing, raw, spec.processing)
│   │   └── dem.apply_layer_processing()
│   │       └── clip → gaussian_filter → sharpen → extract_rivers → normalize → invert
│   │       └── scipy.ndimage, cv2
│   ├── [first layer] → cv2.resize to req.dim → set as composite
│   └── [subsequent] → dem.blend_layers(composite, processed, mode, weight)
│       └── cv2.resize + mode logic (replace/blend/rivers/max/min)
└── validation.b64_encode() + np stats → JSONResponse

Classes/objects: MergeRequest, MergeLayerSpec, ProcessingSpec (Pydantic models)
Core modules: dem, validation, schemas
Libraries: cv2, scipy.ndimage, (+ whatever fetch_layer_data pulls)
```

---

## 8. `/api/terrain/hydrology` — River Depression Grid

**Handler**: `get_terrain_hydrology()` in terrain.py

```
get_terrain_hydrology()
├── validation.parse_float/parse_int/parse_bool()
├── validation.validate_bbox() + validate_dim()
├── [TEST_MODE] → synthetic river band
│   └── projection.project_grid()
├── run_sync(hydrology.fetch_and_rasterize_hydrology, ...)  — core module
│   ├── [hydrorivers]
│   │   ├── hydrorivers.fetch_hydrorivers()
│   │   │   └── pyogrio/geopandas read from parquet cache or .shp download
│   │   └── hydrorivers.rasterize_hydrorivers()
│   │       └── shapely geometry simplify + rasterio.features.rasterize
│   │       └── scipy.ndimage.gaussian_filter for smooth edges
│   └── [natural_earth]
│       ├── hydrology.fetch_natural_earth_rivers()
│       │   └── requests.get(naciscdn.org) → zipfile → geopandas.read_file
│       ├── hydrology.filter_rivers_by_bbox()         — manual coord bounds check
│       └── hydrology.rasterize_rivers_with_buffering()
│           └── shapely.geometry.shape + .buffer()
│           └── rasterio.features.rasterize + rasterio.transform.from_bounds
├── projection.project_grid()
│   └── geo2stl.projections.project_coordinates()
└── JSONResponse with b64 river grid

Classes/objects: None
Core modules: hydrology, hydrorivers, projection, validation
Libraries: geopandas, shapely, rasterio, scipy.ndimage, requests, pyogrio
```

---

## 9. `/api/composite/hydrology-merge` — Merge Rivers into DEM

**Router**: `composite.py` (moved from terrain.py)

```
merge_hydrology()
├── schemas.HydrologyMergeRequest (Pydantic validation)
├── np.array().reshape() for dem_arr and river_arr
├── shape match check
├── [TEST_MODE] → return DEM b64 unchanged
├── run_sync(hydrology.merge_rivers_with_dem, ...)
│   └── np.minimum(dem, dem + river_depression)       — element-wise merge
└── JSONResponse with b64-encoded merged DEM

Classes/objects: HydrologyMergeRequest (Pydantic model)
Core modules: hydrology, validation
Libraries: numpy only
```

---

## ~~10. `/api/export/preview` alias~~ — REMOVED

The terrain router's alias was shadowing the real `generate_mesh_preview()` handler in the export router. Removed — the export router's handler (returns vertices+faces for the 3D viewer) is now the sole handler for `POST /api/export/preview`.

---

## Cross-Endpoint Analysis

### Shared Function Usage Matrix

| Function | DEM | Raw | Water | ESA | Sat | Sources | Merge | Hydro | HydroMerge |
|----------|:---:|:---:|:-----:|:---:|:---:|:-------:|:-----:|:-----:|:----------:|
| `validate_bbox()` | x | x | x | x | x | | | x | |
| `validate_dim()` | x | x | | | x | | | x | |
| `parse_float/int/bool()` | x | x | x | x | x | | | x | |
| `b64_encode()` | x | x | x | x | | | x | x | |
| `make_cache_key()` | x | | x | x | | | | | |
| `read/write_array_cache()` | x | | x | x | | | | | |
| `project_grid()` | x | x | | x | x | | | x | |
| `project_water_arrays()` | | | x | | | | | | |
| `project_rgb_image()` | | | x (sat overlay) | | x | | | | |
| `fetch_layer_data()` | x | | | | | | x | | |
| `fetch_local_dem()` | x | | | | | | | | |
| `fetch_h5_dem()` | x | | | | | | x | | |
| `fetch_opentopo_dem()` | x | | | | | | x | | |
| `fetch_esa_water_layer()` | | | | | | | x | | |
| `upsample_dem()` | x | | | | | | | | |
| `make_dem_payload()` | x | | | | | | | | |
| `compute_raw_dem()` | | x | | | | | | | |
| `apply_layer_processing()` | | | | | | | x | | |
| `blend_layers()` | | | | | | | x | | |
| `fetch_water_mask()` | | | x | x | | | | | |
| `fetch_sat_overlay()` | x | | | | | | | | |
| `fetch_satellite_tiles()` | | | | | x | | | | |
| `fetch_natural_earth_rivers()` | | | | | | | | x | |
| `fetch_hydrorivers()` | | | | | | | | x | |
| `merge_rivers_with_dem()` | | | | | | | | | x |
| `error_response()` | x | x | x | x | x | | x | x | x |
| `run_sync()` | x | x | x | x | x | | x | x | x |

### Groups by Reuse Pattern

**High reuse** (6+ endpoints):
- `validate_bbox`, `parse_float/int/bool`, `run_sync`, `error_response` — used by nearly every endpoint. Well-factored into `core/validation.py` and `core/responses.py`.

**Medium reuse** (3-4 endpoints):
- `project_grid` — DEM, Raw, ESA, Hydrology. Consistent interface, clean delegation to geo2stl.
- `b64_encode` — DEM, Raw, Water, ESA, Merge, Hydrology. Standard serialization.
- `read/write_array_cache` — DEM, Water, ESA only. Three endpoints cache; six do not.

**Low reuse / single-use**:
- `compute_raw_dem` — Raw only. Hardcodes projection, duplicates stitch+scale logic.
- `apply_layer_processing` + `blend_layers` — Merge only.
- `fetch_satellite_tiles` — Satellite only.
- `project_rgb_image` — Satellite only (+ DEM's optional sat overlay).
- `project_water_arrays` — Water mask only.
- `merge_rivers_with_dem` — Hydrology merge only.

### Endpoints That Deviate Completely

1. **`/api/terrain/satellite`** (#5) — Entirely different data pipeline: WMTS tile fetcher, PIL image stitching, Mercator-to-Plate-Carrree resampling, JPEG encoding. Shares nothing with the array-based DEM/water endpoints except validation and projection.

2. **`/api/terrain/sources`** (#6) — Pure config read. No computation, no I/O, no shared patterns.

3. **`/api/composite/hydrology-merge`** (#9) — Pydantic-backed composite endpoint that receives pre-computed arrays from the frontend and merges them server-side. It still stands apart from the fetch-style terrain routes because it operates on already-fetched grids rather than pulling source data itself.

### Endpoints That Share the Most

- **DEM** (#1) and **DEM Merge** (#7) share `fetch_layer_data()` as their primary data source dispatcher. Both can pull from local SRTM, h5, OpenTopography.
- **Water mask** (#2) and **ESA land cover** (#3) share the ESA WorldCover data source via `fetch_water_mask_images()`.

---

## Quality Assessment

### Well-Built

| Endpoint | Why |
|----------|-----|
| `/api/terrain/dem` (#1) | Complete pipeline: validation → cache → fetch → upsample → project → serialize → cache write. Handles all DEM sources, optional sat overlay, projection. Proper error handling. The gold standard for the codebase. |
| `/api/terrain/water-mask` (#2) | Good auto-scaling (50MB + 32768px guards), dual-array projection alignment, caching. Smart scale clamping prevents EE request failures. |
| `/api/terrain/satellite` (#4) | Smart zoom calculation, tile-count clamping, Mercator-to-Plate-Carree correction, sensible fallback on tile failures. |
| `/api/terrain/sources` (#5) | Does exactly what it should with minimal code. |

### Remaining Issues

| Endpoint | Issues |
|----------|--------|
| `/api/composite/dem-merge` (#8) | No projection step means merged output won't align with projected DEM previews. No caching. Good Pydantic validation though. |
| `/api/terrain/hydrology` (#6) | No caching despite network-heavy Natural Earth fetches. |
| Satellite (#4) | No caching. |

---

## Remaining Recommendations

1. **Caching** should be added to Satellite, Hydrology, and DEM Merge endpoints — all involve expensive network I/O.
2. **DEM Merge** should support a projection step so output aligns with projected DEM previews.
