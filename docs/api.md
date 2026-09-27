# Backend API Routes — strm2stl

_Last updated: 2026-05-14_

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

Primary `TerrainSession` touchpoints:

- `fetch_dem()` uses `/api/terrain/dem`
- `fetch_water_mask()` and `fetch_esa_landcover()` both use `/api/terrain/water-mask`
- `fetch_satellite()` uses `/api/terrain/satellite`
- `fetch_hydrology()` and `merge_hydrology_with_dem()` should be traced through the terrain route family in the router file
- merge helpers such as `merge_dem()` use `/api/composite/dem-merge`

Terrain bbox parsing is now centralized in `core/validation.parse_bbox_query()`, so
the terrain router keeps the existing query-string semantics and 400 error shapes
without repeating the four bbox parsers in every endpoint.

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/terrain/dem` | Fetch processed DEM. Also returns `source_resolution: {source, native_resolution_m, native_samples: [rows, cols], grid: [rows, cols], upsample}` — the real samples of the chosen source across the box vs the grid returned (`geo2stl.dem.dem_sampling`; shown in the Edit tab by `DemSamplingInfo.vue`, warning above 4×). |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover. **Scale param:** `dim` (int, pixels per side, default 600) — server computes resolution and returns `resolution_m` in response. |
| GET/POST | `/api/terrain/esa-land-cover` | Fetch ESA WorldCover classification raster. **Scale param:** `dim` (pixels per side, default 600) — same server-side resolution computation; returns `resolution_m`. |
| GET | `/api/terrain/satellite` | Fetch satellite imagery (ESRI tiles) |
| GET | `/api/terrain/sources` | List DEM data sources; each has `native_resolution_m` (`geo2stl.dem.DEM_SOURCE_INFO`). `default_source` is `SRTMGL1` when an OpenTopography key is configured, else `h5_local` / `local` (`default_dem_source`); the client selects it until a preset, saved settings or the user choose. |
| GET | `/api/terrain/hydrology` | Fetch HydroRIVERS depression grid for bbox |
| GET | `/api/terrain/trails` | Fetch ski and hiking trail relief grids for bbox. **Params:** `dim` (default 600), `relief_m` (default -2.0, negative engraves), `width_m` (default 8, clamped 1–500), `source` (`osm` \| `usfs` \| `all`), `categories` (comma-separated subset of `ski,hiking`). Returns **both** grids (`ski_grid_values_b64`, `hiking_grid_values_b64`) in one response so client-side category toggles need no refetch. Also returns `ski_area_grid_values_b64` and `hiking_area_grid_values_b64`: display-only 0/1 masks of the interiors of features mapped as closed ways (piste and ski-area polygons). The relief grids carry only linework, boundaries included, so an areal feature can never engrave a filled region into the DEM. Also returns `ski_difficulty_grid_values_b64` - the piste grade per pixel as a 1-based index into `difficulty_classes` (0 means no usable `piste:difficulty` tag), sent as float32 like the other grids because the values are small integers and survive the cast exactly. A reader must treat it as class indices and never interpolate it; the server reprojects it nearest-neighbour for the same reason. Where two pistes cross, the harder grade wins. On an Overpass outage the response is HTTP 200 with null grids, `upstream_error: true`, and an `error` naming the failure - distinct from a region that genuinely holds no trails, which returns null grids with `feature_count: 0` and no `upstream_error`. Nothing is cached in either failure case. |
| POST | `/api/composite/hydrology-merge` | Merge hydrology depression into DEM array |
| POST | `/api/composite/dem-merge` | Merge multiple DEM layers (`MergeRequest`). Layers are an ordered list: each names a source, a blend mode (`add` raises, `rivers` cuts), a weight and a processing pipeline. Sources are geo2stl's built-ins plus anything the server registered — `osm_buildings`, `osm_roads`, `osm_waterways`, `osm_walls`. The same spec is what an export sends as `composite_layers`. |
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

Primary `TerrainSession` touchpoints:

- `fetch_cities()` uses `/api/cities`
- city raster and export helpers should be traced through this router module

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cities/cached` | Check if OSM bbox is cached |
| POST | `/api/cities` | Fetch OSM data (rejects >15 km diagonal); cached as `.json.gz`. Blocking; kept for the SDK — the browser uses the background variant below |
| POST | `/api/cities/start` | Same body and size guard as `POST /api/cities`, run as a background task (`core/city_fetch_tasks.py`); returns the status shape below. An identical fetch still running is joined |
| GET | `/api/cities/status/{task_id}` | `{task_id, status: running\|done\|error\|cancelled, layers: [{name, state}], mirror, message, error, elapsed_s, diagonal_km}`; layer state `pending\|fetching\|done\|failed\|cached\|cancelled` (plus a `heights` row for building-height enhancement). 404 once expired (5 min after finishing) |
| GET | `/api/cities/result/{task_id}` | The finished payload (same body as `POST /api/cities`); 409 unless `done` |
| POST | `/api/cities/cancel/{task_id}` | Cancel: layers not yet started are skipped; Overpass queries in flight finish in the worker and are discarded. Returns the status |
| POST | `/api/cities/raster` | Rasterize OSM buildings/roads/waterways to a DEM-format height map (`values`, `width`, `height`, `vmin`, `vmax`) — used by `loadCityRaster()` in `city-render.js` |
| POST | `/api/cities/export3mf` | Generate 3MF with terrain + building prisms |
| GET | `/api/cities/google3d-available` | Check if Google 3D Tiles are available for the current bbox |
| POST | `/api/cities/enhance-heights` | Refine building heights using an alternative height provider (e.g. Google 3D Tiles) |

> **Two city rasterization endpoints exist:**
> - `/api/cities/raster` — returns a flat height map in DEM format (direct canvas rendering via `city-render.js`)
> - `/api/composite/city-raster` — returns per-feature height-delta arrays used by the composite DEM pipeline
>
> They serve different consumers: the first is for the CityRaster layer view; the second feeds `composite-dem.js`.

## Geocode Routes (`routers/geocode.py`)

Landmark search and the POI-near-edge warning (F-UX batch 2). Both results are cached
on disk (`geocode` / `landmarks` namespaces of `geo2stl.cache`); tests mock the network.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/geocode?q=&limit=5` | Nominatim search (`geo2stl.geocode.search_places`: `format=jsonv2`, identifying User-Agent, ≤ 1 request/s process-wide, cached 30 days). Returns `{query, results: [{name, display_name, lat, lon, bbox: {north, south, east, west} \| null, class, type, osm_type, osm_id}]}`; 502 on upstream failure. The client searches on submit only (the usage policy forbids autocomplete) |
| GET | `/api/geocode/edge-landmarks?north&south&east&west[&warn_m=200&band_m=400]` | Named notable OSM features (place of worship, town hall, castle / historic=*, tourism attraction / museum / viewpoint) in a `band_m` band centred on the box edge — one Overpass query over the four edge strips (`geo2stl.landmarks`, cached 7 days) — reported when within `warn_m` of the edge, inside or outside: `{landmarks: [{name, class, type, lat, lon, position: inside\|outside\|crosses, edge, distance_m, message}], warn_m, band_m}`, nearest first (crossings first). `message` reads "Alhambra is 120 m outside the east edge". Boxes over 60 km diagonal return `skipped` without a query; 502 on upstream failure |

## Composite Routes (`routers/composite.py`)

Primary `TerrainSession` touchpoints:

- `composite_city_raster()` uses `/api/composite/city-raster`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/composite/city-raster` | Rasterize OSM features to height-delta arrays (PIL, ~50× faster than JS). Supports `projection` and `clip_nans` for uniform pipeline alignment — used by `composite-dem.js` |

## Cache & Settings (`routers/cache.py`, `settings.py`)

Primary `TerrainSession` touchpoints:

- `server_settings()` reads the settings route family
- `cache_status()` uses `/api/cache`
- `clear_cache()` uses `DELETE /api/cache`

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cache` | Cache statistics |
| DELETE | `/api/cache` | Clear server cache |
| DELETE | `/api/cache/region` | Clear cache for a specific region bbox |
| GET | `/api/cache/check` | Check if specific bbox is cached |
| GET | `/api/settings/projections` | Available projections |
| GET | `/api/settings/colormaps` | Available colormaps |
| GET | `/api/settings/datasets` | Available DEM datasets |
| GET | `/api/settings/default` | Grouped browser-client defaults; `dem.dem_source` is `SRTMGL1` with an OpenTopography key, else the local store |
| GET | `/api/settings` | Combined settings payload for SDK/bootstrap clients |
| GET | `/api/global_dem_overview` | Cached global DEM PNG (served by `server.py`) |

## Auth & Data Sources (`routers/auth.py`)

Backs the 🔑 Keys panel in the header. Everything here writes to
`strm2stl/config.json` and re-binds the running process, so no restart is needed.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/auth/status` | Earth Engine and OpenTopography authentication state |
| POST | `/api/auth/opentopo-key` | Save an OpenTopography API key |
| GET | `/api/auth/tile-store` | Where the local `.tif` tiles are, and how many there are |
| POST | `/api/auth/tile-store` | Repoint `ocean_root` at a folder of `.tif` tiles |
| POST | `/api/auth/tile-store/browse` | Open a native folder picker on the server's desktop |
| POST | `/api/auth/earth-engine/start` | Begin the EE OAuth flow; returns a URL to open |
| POST | `/api/auth/earth-engine/complete` | Exchange the pasted code for credentials |

`POST /api/auth/tile-store` returns 400 unless the folder exists and holds at least
one `.tif` directly inside it, so a typo cannot replace a working store with a broken
one; when tiles are one level down the error names the subfolder that has them. On
success it resets geo2stl's memoized tile list, so the `local` source becomes usable
immediately. See `app/server/core/tile_store.py`.

`POST /api/auth/tile-store/browse` raises a `tkinter.filedialog.askdirectory()` dialog
on the machine running the server, because a browser cannot report an absolute
filesystem path (`webkitdirectory` yields only relative names) and an absolute path is
exactly what `ocean_root` needs. This is sound only because the app is a localhost
desktop tool. The dialog blocks, so it runs through `core.validation.run_sync`; a server
with no display returns **501** and the client falls back to typing the path. Returns
`{"supported": true, "cancelled": true}` when the user dismisses the dialog.

## Height Routes (`routers/height.py`)

Building height estimation from multiple data sources. Router uses prefix `/api/height`.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/height/sources` | List available height data sources for a bbox |
| POST | `/api/height/fetch` | Fetch building height raster from specified provider(s) |

See [arch.md](arch.md) for the height provider architecture and
[height-pipeline-plan.md](height-pipeline-plan.md) for implementation status.

## Pipeline Report Routes (`routers/reports.py`)

Browse the skyline pipeline's rendered artifacts. The page itself is `GET /reports`
(template `reports.html`, behaviour in `static/js/reports.js`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/reports/index` | Inventory of every region report, height report, trace and PDF on disk, with per-seed rows and artifact URLs |
| GET | `/api/reports/heights/{region_dir}` | Summary of one region's `heights.json` (source counts and height percentiles, not the building list) |
| GET | `/reports/files/{root}/{path}` | One artifact file. `root` is `region`, `height` or `trace`; paths are resolved and checked for containment, and only viewable extensions are served |

The inventory is rebuilt by scanning directories on every request rather than read from
`build_landing_page.py`'s static `index.html`, which has to be re-run after each batch.

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
| `local` | Local tile store (`config.json` `ocean_root`, via `make_dem_image()`); the project's store is GEBCO 2025, 15″ ≈ 460 m |
| `h5_local` | Local SRTM3 HDF5 store, 3″ ≈ 90 m |

Native resolutions (arc-seconds and nominal metres) live in `geo2stl.dem.DEM_SOURCE_INFO`.
| `water_esa` | ESA WorldCover water mask band |
