# Backend API Routes — strm2stl

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
| GET | `/` | Serve index.html |
| GET | `/api/coordinates` | Legacy: list regions from coordinates.json |
| POST | `/api/save_coordinate` | Legacy: save new region (used by app.js) |
| GET | `/api/regions` | List all regions |
| POST | `/api/regions` | Create region (body: `RegionCreate`), 201 |
| PUT | `/api/regions/{name}` | Update region bbox + metadata |
| DELETE | `/api/regions/{name}` | Delete region + cascade settings |
| GET | `/api/regions/{name}/settings` | Get saved panel settings (200 + `{}` if none) |
| PUT | `/api/regions/{name}/settings` | Save panel settings |

## DEM / Terrain Routes (`routers/terrain.py`)

Primary `TerrainSession` touchpoints:

- `fetch_dem()` uses `/api/terrain/dem`
- `fetch_water_mask()` and `fetch_esa_landcover()` both use `/api/terrain/water-mask`
- `fetch_satellite()` uses `/api/terrain/satellite`
- `fetch_hydrology()` and `merge_hydrology_with_dem()` should be traced through the terrain route family in the router file
- merge helpers such as `merge_dem()` use `/api/dem/merge`

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/terrain/dem` | Fetch processed DEM |
| GET/POST | `/api/terrain/dem/raw` | Fetch unprocessed DEM array |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover |
| GET/POST | `/api/terrain/satellite` | Fetch satellite imagery |
| GET | `/api/terrain/sources` | List DEM data sources |
| POST | `/api/dem/merge` | Merge multiple DEM layers (`MergeRequest`) |
| POST | `/api/export/preview` | DEM values for Three.js preview (no STL) |

## Export Routes (`routers/export.py`)

Primary `TerrainSession` touchpoints:

- `export_obj()` posts to the export route family
- `verify()` reads the OBJ verification route
- `slice()` posts to the slicer route
- other export helpers should be traced through the same router module

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/export/stl` | Generate + download STL |
| POST | `/api/export/obj` | Generate + download OBJ |
| POST | `/api/export/3mf` | Generate + download 3MF |

`Session_API_Reference.ipynb` also covers the broader export family used by the session client, including split export, OBJ inspection, verification, and slicer endpoints.

## City Routes (`routers/cities.py`)

Primary `TerrainSession` touchpoints:

- `fetch_cities()` uses `/api/cities`
- city raster and export helpers should be traced through this router module

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cities/cached` | Check if OSM bbox is cached |
| POST | `/api/cities` | Fetch OSM data (rejects >15 km diagonal); cached as `.json.gz` |
| POST | `/api/cities/raster` | Rasterize OSM buildings/roads/waterways to a DEM-format height map (`values`, `width`, `height`, `vmin`, `vmax`) — used by `loadCityRaster()` in `city-render.js` |
| POST | `/api/cities/export3mf` | Generate 3MF with terrain + building prisms |

> **Two city rasterization endpoints exist:**
> - `/api/cities/raster` — returns a flat height map in DEM format (direct canvas rendering via `city-render.js`)
> - `/api/composite/city-raster` — returns per-feature height-delta arrays used by the composite DEM pipeline
>
> They serve different consumers: the first is for the CityRaster layer view; the second feeds `composite-dem.js`.

## Composite Routes (`routers/composite.py`)

Primary `TerrainSession` touchpoints:

- `composite_city_raster()` uses `/api/composite/city-raster`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/composite/city-raster` | Rasterize OSM features to height-delta arrays (PIL, ~50× faster than JS) — used by `composite-dem.js` |

## Cache & Settings (`routers/cache.py`, `settings.py`)

Primary `TerrainSession` touchpoints:

- `server_settings()` reads the settings route family
- `cache_status()` uses `/api/cache`
- `clear_cache()` uses `DELETE /api/cache`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/settings` | Full server-authoritative settings payload |
| GET | `/api/cache` | Cache statistics |
| DELETE | `/api/cache` | Clear server cache |
| GET | `/api/cache/check` | Check if specific bbox is cached |
| GET | `/api/settings/projections` | Available projections |
| GET | `/api/settings/colormaps` | Available colormaps |
| GET | `/api/settings/datasets` | Available DEM datasets |
| GET | `/api/global_dem_overview` | Cached global DEM PNG |

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
