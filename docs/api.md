# Backend API Routes — strm2stl

## Region Routes (`ui/routers/regions.py`)

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

## DEM / Terrain Routes (`ui/routers/terrain.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/terrain/dem` | Fetch processed DEM |
| GET/POST | `/api/terrain/dem/raw` | Fetch unprocessed DEM array |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover |
| GET/POST | `/api/terrain/satellite` | Fetch satellite imagery |
| GET | `/api/terrain/sources` | List DEM data sources |
| POST | `/api/dem/merge` | Merge multiple DEM layers (`MergeRequest`) |
| POST | `/api/export/preview` | DEM values for Three.js preview (no STL) |

## Export Routes (`ui/routers/export.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/export/stl` | Generate + download STL |
| POST | `/api/export/obj` | Generate + download OBJ |
| POST | `/api/export/3mf` | Generate + download 3MF |

## City Routes (`ui/routers/cities.py`)

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

## Composite Routes (`ui/routers/composite.py`)

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/composite/city-raster` | Rasterize OSM features to height-delta arrays (PIL, ~50× faster than JS) — used by `composite-dem.js` |

## Cache & Settings (`ui/routers/cache.py`, `settings.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cache` | Cache statistics |
| DELETE | `/api/cache` | Clear server cache |
| GET | `/api/cache/check` | Check if specific bbox is cached |
| GET | `/api/settings/projections` | Available projections |
| GET | `/api/settings/colormaps` | Available colormaps |
| GET | `/api/settings/datasets` | Available DEM datasets |
| GET | `/api/global_dem_overview` | Cached global DEM PNG |

## Key Pydantic Models (`ui/schemas.py`)

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

## DEM Sources (OPENTOPO_DATASETS in `ui/config.py`)

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
