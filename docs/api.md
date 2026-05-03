# Backend API Routes — strm2stl

_Last updated: 2026-05-03_

For notebook and Python SDK tracing, pair this document with `sdk-workflow.md` and `../notebooks/Session_API_Reference.ipynb`.

Use `../notebooks/API_Terrain.ipynb` when you want the end-to-end workflow instead of route-by-route examples.

If you opened the docs folder directly, `README.md` is the preferred docs index.

## Region Routes (`routers/regions.py`)

Primary `TerrainSession` touchpoints:

- `regions()` reads `GET /api/regions`
- `select()` reads `GET /api/regions` and `GET /api/regions/{name}/settings`
- region save/update helpers should be traced through the same route family

| Method | Path | Description |
|--------|------|-------------|
| GET | `/` | Serve index.html (`server.py`) |
| GET | `/api/regions` | List all regions |
| POST | `/api/regions` | Create region (body: `RegionCreate`), 201 |
| PUT | `/api/regions/{name}` | Update region bbox + metadata |
| DELETE | `/api/regions/{name}` | Delete region + cascade settings |
| GET | `/api/regions/{name}/settings` | Get saved panel settings (200 + `{}` if none) |
| PUT | `/api/regions/{name}/settings` | Save panel settings |

## DEM / Terrain Routes (`routers/terrain.py`)

Primary `TerrainSession` touchpoints: `fetch_dem()`, `fetch_water_mask()`, `fetch_satellite()`, `fetch_hydrology()`.

**Library dispatch (post-refactor):**
- DEM fetch → `geo2stl.dem.fetch_dem_from_source()` (source-dispatching: local tiles, OpenTopography, H5)
- Water mask → `geo2stl.sat2stl.fetch_water_mask()` → Earth Engine (JRC + ESA WorldCover)
- Satellite → `geo2stl.sat2stl.fetch_sat_overlay()` → ESRI tile fetch, scale via `geo2stl.raster.derive_sat_scale()`
- Projection (all types) → `geo2stl.projections.project_grid()` / `project_water_arrays()` / `project_rgb_image()`
- Bbox parse → `core.validation.parse_bbox_query()` (centralized)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/terrain/dem` | Fetch processed DEM |
| GET/POST | `/api/terrain/dem/raw` | Fetch unprocessed DEM array |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover. **Scale param:** `dim` (int, pixels per side, default 600) — server computes resolution and returns `resolution_m` in response. |
| GET/POST | `/api/terrain/esa-land-cover` | Fetch ESA WorldCover classification raster. **Scale param:** `dim` (pixels per side, default 600) — same server-side resolution computation; returns `resolution_m`. |
| GET | `/api/terrain/satellite` | Fetch satellite imagery (ESRI tiles) |
| GET | `/api/terrain/sources` | List DEM data sources |
| GET | `/api/terrain/hydrology` | Fetch HydroRIVERS depression grid for bbox |
| POST | `/api/composite/hydrology-merge` | Merge hydrology depression into DEM array |
| POST | `/api/composite/dem-merge` | Merge multiple DEM layers (`MergeRequest`) |
| POST | `/api/export/preview` | DEM values for Three.js preview (no STL) |

## Export Routes (`routers/export.py`)

Primary `TerrainSession` touchpoints:

- `export_obj()` posts to the export route family
- `verify()` reads the OBJ verification route
- `slice()` posts to the slicer route
- other export helpers should be traced through the same router module

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/export/stl` | Generate + download STL (sync) |
| POST | `/api/export/obj` | Generate + download OBJ (sync) |
| POST | `/api/export/3mf` | Generate + download 3MF (sync) |
| POST | `/api/export/crosssection` | Generate cross-section OBJ |
| POST | `/api/export/preview` | DEM values for Three.js preview (no mesh file) |
| POST | `/api/export/puzzle` | Start async puzzle 3MF export → `{task_id}` |
| POST | `/api/export/start` | Start async export (any format) → `{task_id}`; body must include `"format"` field |
| GET | `/api/export/status/{task_id}` | Poll async task → `{status, progress, message}` |
| GET | `/api/export/download/{task_id}` | Download result of completed async task (file auto-deleted after send) |

> **Sync vs async export:** Sync endpoints (`/stl`, `/obj`, `/3mf`) block until the file is ready and stream it directly. Async endpoints (`/start`, `/puzzle`) start a background thread and return a `task_id` for polling. The async path is preferred for large DEMs and puzzle exports. Tasks expire after 300 s.

`Session_API_Reference.ipynb` also covers the broader export family used by the session client, including split export, OBJ inspection, verification, and slicer endpoints.

## City Routes (`routers/cities.py`)

Primary `TerrainSession` touchpoints: `fetch_cities()`, city raster, export helpers.

**Library dispatch (post-refactor):**
- OSM cache staleness predicates → `city2stl.cache_policy` (directly imported, no wrapper)
- City raster projection → `geo2stl.projections.project_city_raster()`
- City height enhancement → `core.height.service.enhance_city_data()`
- Cache read/write → `core.cache.read_osm_cache()` / `write_osm_cache()`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cities/cached` | Check if OSM bbox is cached; accepts `m_per_level`, `simplify_tolerance`, `min_area` to match the correct cache entry |
| POST | `/api/cities` | Fetch OSM data (rejects >15 km diagonal); cached as `.json.gz`. Key params: `m_per_level` (floor height, default 3.5 m), `simplify_tolerance`, `min_area` — all included in cache key |
| POST | `/api/cities/raster` | Rasterize OSM buildings/roads/waterways to a DEM-format height map (`values`, `width`, `height`, `vmin`, `vmax`) — used by `loadCityRaster()` in `city-render.js`. Accepts `m_per_level` for OSM cache lookup |
| POST | `/api/cities/export3mf` | Generate 3MF with terrain + building prisms |
| GET | `/api/cities/google3d-available` | Check if Google 3D Tiles API key is configured |
| POST | `/api/cities/enhance-heights` | Re-run building height enrichment on existing OSM data |

> **Two city rasterization endpoints exist:**
> - `/api/cities/raster` — returns a flat height map in DEM format (direct canvas rendering via `city-render.js`)
> - `/api/composite/city-raster` — returns per-feature height-delta arrays used by the composite DEM pipeline
>
> They serve different consumers: the first is for the CityRaster layer view; the second feeds `composite-dem.js`.
> Both endpoints accept `m_per_level`, `simplify_tolerance`, and `min_area` to resolve the correct OSM cache entry.

> **`m_per_level` (floor-to-floor height):** Default is 3.5 m. Use 3.0–3.5 for Southern Europe (3.4 for Granada), 3.5–4.0 for Northern Europe/US. This parameter is part of the OSM cache key — changing it produces a separate cache entry with correctly scaled `osm_levels`-derived heights.

## Composite Routes (`routers/composite.py`)

Primary `TerrainSession` touchpoints: `composite_city_raster()`.

**Library dispatch (post-refactor):**
- City raster burn → `city2stl.rasterize.rasterize_composite_layers()` (extracted to library)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/composite/city-raster` | Rasterize OSM features to height-delta arrays (PIL, ~50× faster than JS). Accepts `m_per_level`, `simplify_tolerance`, `min_area` for OSM cache lookup. Supports `projection` and `clip_nans` for uniform pipeline alignment — used by `composite-dem.js` |

## Cache & Settings (`routers/cache.py`, `settings.py`)

Primary `TerrainSession` touchpoints: `server_settings()`, `cache_status()`, `clear_cache()`.

**Cache inspection** (tree building, region grouping, metadata read) is implemented in `core/cache_inspector.py`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cache` | Cache statistics |
| DELETE | `/api/cache` | Clear server cache |
| DELETE | `/api/cache/region` | Clear cache for a specific region bbox |
| GET | `/api/cache/check` | Check if specific bbox is cached |
| GET | `/api/cache/inventory` | Full filesystem inventory tree for the cache UI |
| GET | `/api/settings/projections` | Available projections |
| GET | `/api/settings/colormaps` | Available colormaps |
| GET | `/api/settings/datasets` | Available DEM datasets |
| GET | `/api/settings` | Combined settings payload for SDK/bootstrap clients |
| GET | `/api/global_dem_overview` | Cached global DEM PNG (served by `server.py`) |

## Height Routes (`routers/height.py`)

Building height estimation from multiple data sources. Router uses prefix `/api/height`.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/height/sources` | List available height data sources for a bbox |
| POST | `/api/height/fetch` | Fetch building height raster from specified provider(s) |

See [reference/libraries.md](reference/libraries.md) for the height provider architecture,
[todos/height-pipeline-improvement-plan.md](todos/height-pipeline-improvement-plan.md) for current open work,
and [completed/height-pipeline-plan.md](completed/height-pipeline-plan.md) for the historical implementation plan.

## Key Pydantic Models (`schemas.py`)

- `BoundingBox` — `{north, south, east, west}`
- `RegionCreate(BoundingBox)` — `+ name, label?, description?`
- `RegionSettings` — arbitrary settings blob `{dim?, colormap?, projection?, elevation_curve_points?, ...}`
- `DEMRequest(BoundingBox)` — `+ dim, depth_scale, height, base, ...`
- `DEMResponse` — `{values, width, height, min, max, bbox, ...}`
- `WaterMaskResponse` — `{water_mask_values, water_mask_dimensions, esa_values, esa_dimensions, ...}`
- `ExportRequest(BoundingBox)` — `+ dim, depth_scale, height, base, subtract_water, ...`
- `CityRequest(BoundingBox)` — `+ layers: list[str], simplify_tolerance, min_area`
- `MergeRequest` — `{bbox, dim, layers: list[MergeLayerSpec]}`
- `MergeLayerSpec` — `{source, blend_mode, weight, processing: ProcessingSpec}`
- `ProcessingSpec` — `{clip_min, clip_max, smooth_sigma, sharpen, normalize, invert, extract_rivers, river_max_width_px}`

## DEM Sources (OPENTOPO_DATASETS in `config.py`)

| Key | Description |
|-----|-------------|
| `SRTMGL1` | SRTM 30m global |
| `SRTMGL3` | SRTM 90m global |
| `AW3D30` | ALOS World 3D 30m |
| `COP30` | Copernicus DSM 30m |
| `COP90` | Copernicus DSM 90m |
| `SRTM15Plus` | SRTM15+ bathymetry + land |
| `local` | Local SRTM tiles via `make_dem_image()` |
| `water_esa` | ESA WorldCover water mask band |
