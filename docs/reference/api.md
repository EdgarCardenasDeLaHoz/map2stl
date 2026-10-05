# Backend API Routes — map2stl

_Last updated: 2026-09-28_

- Routers: `app/server/routers/` — `auth`, `cache`, `cities`, `composite`, `diagnostics`, `export`, `geocode`, `guides`, `height`, `layers`, `regions`, `registration`, `reports`, `settings`, `terrain`, `usage`. Logic lives in `app/server/core/`.
- Page routes (`app/server/server.py`): `GET /` (app), `GET /reports`, `GET /guides`, `GET /guides/{slug}`, `GET /static/{file_path}`.
- Python SDK over these routes: [sdk.md](sdk.md) (method → route map); examples in `../../notebooks/Session_API_Reference.ipynb`, end-to-end in `../../notebooks/API_Terrain.ipynb`.
- Design context: [overview.md](overview.md), [pipeline.md](pipeline.md).

## Region Routes (`app/server/routers/regions.py`)

Primary `TerrainSession` touchpoints:

- `regions()` reads `GET /api/regions`
- `select()` reads `GET /api/regions` and `GET /api/regions/{name}/settings`
- `create_region()` / `update_region()` / `delete_region()` use `POST` / `PUT` / `DELETE`; `save_settings()` uses `PUT .../settings`
- Landmark overrides are stored via `app/server/core/landmarks.py::load_region_overrides` / `save_region_override` / `delete_region_override` (spec checked by `validate_spec`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/regions` | List all regions |
| POST | `/api/regions` | Create region (body: `RegionCreate`), 201 |
| PUT | `/api/regions/{name}` | Update region bbox + metadata. A body `name` different from the path **renames** the region; its settings and landmark overrides move with it (`regions.py::_rename_region_rows`). 404 unknown, 409 name taken, 400 blank name |
| DELETE | `/api/regions/{name}` | Delete region + cascade settings |
| GET | `/api/regions/{name}/settings` | Get saved panel settings (200 + `{}` if none) |
| PUT | `/api/regions/{name}/settings` | Save panel settings |
| GET | `/api/regions/{name}/landmarks` | Stored landmark overrides → `{name, overrides: {osm_id: spec}}` (table `region_landmarks`, F-LANDMARK §3) |
| PUT | `/api/regions/{name}/landmarks/{osm_id}` | Store one override; `osm_id` like `way/123` (path); body `{kind: "osm"\|"mesh"\|"ndsm", ...}` (see City Routes). 400 bad spec, 404 unknown region |
| DELETE | `/api/regions/{name}/landmarks/{osm_id}` | Remove one override (404 if none) |

## DEM / Terrain Routes (`app/server/routers/terrain.py`)

Primary `TerrainSession` touchpoints:

- `fetch_dem()` uses `/api/terrain/dem`
- `fetch_water_mask()` and `fetch_esa_landcover()` both use `/api/terrain/water-mask`
- `fetch_satellite()` uses `/api/terrain/satellite`
- `fetch_hydrology()` uses `/api/terrain/hydrology`
- `merge_dem()` uses `/api/composite/dem-merge`

Terrain bbox parsing is centralized in `app/server/core/validation.py::parse_bbox_query`, so
every endpoint keeps the same query-string semantics and 400 error shapes.

The two `/api/composite/*` merge rows below are served by `app/server/routers/composite.py` (listed here
because they operate on the terrain DEM). DEM previews are under Export Routes.

| Method | Path | Description |
|--------|------|-------------|
| GET/POST | `/api/terrain/dem` | Fetch processed DEM. Also returns `source_resolution: {source, native_resolution_m, native_samples: [rows, cols], grid: [rows, cols], upsample}` — the real samples of the chosen source across the box vs the grid returned (`geo2stl.dem.dem_sampling`; shown in the Edit tab by `DemSamplingInfo.vue`, warning above 4×). |
| GET/POST | `/api/terrain/water-mask` | Fetch water mask + ESA land cover. **Scale param:** `dim` (int, pixels per side, default 600) — server computes resolution and returns `resolution_m` in response. |
| GET/POST | `/api/terrain/esa-land-cover` | Fetch ESA WorldCover classification raster. **Scale param:** `dim` (pixels per side, default 600) — same server-side resolution computation; returns `resolution_m`. |
| GET | `/api/terrain/satellite` | Fetch satellite imagery (ESRI tiles): base64 JPEG `image`, `bbox`, `dimensions` `[h, w]` |
| GET | `/api/terrain/sources` | List DEM data sources; each has `native_resolution_m` (`geo2stl.dem.DEM_SOURCE_INFO`). `default_source` is `SRTMGL1` when an OpenTopography key is configured, else `h5_local` / `local` (`default_dem_source`); the client selects it until a preset, saved settings or the user choose. |
| GET | `/api/terrain/hydrology` | River depression grid for the bbox, united with the open water (sea, lakes; `water_surface`). HydroRIVERS also returns `order_grid_b64` (Strahler order per pixel, `order_water_code` = open water) and `order_counts`; its order grid and water mask are cached without `min_order` / depth / exponent, so changing those is a lookup (`_hydrorivers_payload`) |
| GET | `/api/terrain/trails` | Fetch ski and hiking trail relief grids for bbox. **Params:** `dim` (default 600), `relief_m` (default -2.0, negative engraves), `width_m` (default 8, clamped 1–500), `source` (`osm` \| `usfs` \| `all`), `categories` (comma-separated subset of `ski,hiking`). Returns **both** grids (`ski_grid_values_b64`, `hiking_grid_values_b64`) in one response so client-side category toggles need no refetch. Also returns `ski_area_grid_values_b64` and `hiking_area_grid_values_b64`: display-only 0/1 masks of the interiors of features mapped as closed ways (piste and ski-area polygons). The relief grids carry only linework, boundaries included, so an areal feature can never engrave a filled region into the DEM. Also returns `ski_difficulty_grid_values_b64` - the piste grade per pixel as a 1-based index into `difficulty_classes` (0 means no usable `piste:difficulty` tag), sent as float32 like the other grids because the values are small integers and survive the cast exactly. A reader must treat it as class indices and never interpolate it; the server reprojects it nearest-neighbour for the same reason. Where two pistes cross, the harder grade wins. On an Overpass outage the response is HTTP 200 with null grids, `upstream_error: true`, and an `error` naming the failure - distinct from a region that genuinely holds no trails, which returns null grids with `feature_count: 0` and no `upstream_error`. Nothing is cached in either failure case. |
| GET | `/api/terrain/borders` | Country and state/province borders over the bbox (view only, Edit map's Borders layer). **Params:** `dim` (default 600, the longer side), `projection`, `clip_valid_region`, `maintain_dimensions`. Returns `grid_values_b64` (float32 codes: 0 none, 1 state/province line, 2 country border), `grid_dimensions` [h, w], `codes`, `counts` (line features per kind in the box). Natural Earth 10 m, downloaded once to `cache/natural_earth/`; the raw grid is cached per box and size, the projection applied per request nearest-neighbour. |
| POST | `/api/composite/dem-merge` | Merge multiple DEM layers (`MergeRequest`). Layers are an ordered list: each names a source, a blend mode (`add` raises, `rivers` cuts), a weight and a processing pipeline. Sources are geo2stl's built-ins plus anything the server registered — `osm_buildings`, `osm_roads`, `osm_waterways`, `osm_walls`, and the terrain-relative water sources `hydrorivers`, `natural_earth_rivers`, `lakes` (F-REGION, `geo2stl/water_layers.py`: negative metres below the ground on the base DEM grid, blend `add`; options `min_order`, `width_scale`, `snap` (default true: re-route onto the DEM valley floor) for rivers, `depth_m`, `min_area_m2`, `smooth` (default 3) for lakes). The same spec is what an export sends as `composite_layers`; there the river/lake carve is added after the median filter. A layer after the first whose source fails (ESA water without Earth Engine, an Overpass outage) is skipped and named in the response's `warnings` list; the base layer failing is an error. The grid is the projected base grid, the same one `/api/terrain/dem` returns. |

## Export Routes (`app/server/routers/export.py`)

Every mesh export (sync STL/OBJ/3MF, async `/start`, puzzle, city) runs the same two stages:
`app/server/core/export.py::terrain_stage` (raster heightfield) →
`city2stl/city_model.py::build_on_terrain` (vector features; none for terrain-only).
`/api/export/preview` runs only the terrain stage (`app/server/core/export.py::generate_mesh_preview`), then an
adaptive TIN, so the preview's scale, label and contours match the file.
Why: one path keeps scale, watertightness and simplification consistent — see
[mesh-pipeline decisions](../decisions/mesh-pipeline.md).

- Async dispatch: `app/server/core/export_tasks.py::start_export_task` → `app/server/core/export.py::_run_export_pipeline` (`stl`/`obj`/`3mf`), `app/server/core/export.py::generate_puzzle` (`puzzle`), `app/server/core/city_model_task.py::run_city_model` (`city`)
- Request fields → `app/server/core/export_params.py::ExportContext`; `dem_id` (from `/api/terrain/dem`) is resolved by `app/server/core/export_params.py::resolve_dem`
- Preflight: `app/server/core/preflight.py::preflight`; puzzle cutting: `app/server/core/puzzle.py` (`plan_grid`, `cut_pieces`, `cut_to_zip`); landmark overrides: `app/server/core/landmarks.py::resolve` (city task) and `app/server/core/landmarks.py::preview` (`/api/cities/landmarks/preview`)

Primary `TerrainSession` touchpoints:

- `export_puzzle()` posts `format="puzzle"` to `/api/export/start`, polls status, downloads the zip; `verify()` then checks the pieces locally
- `export_city_model()` posts `format="city"` the same way
- No SDK wrapper for the sync routes or `/preview`

| Method | Path | Description |
|--------|------|-------------|
| — | all `/api/export/*` | An inline `dem_values` grid must be `height` x `width` values with each side ≤ `MAX_DIM` (2000); otherwise 400 (`app/server/core/export_params.py::check_dem_grid`; the async routes answer before queuing a task) |
| POST | `/api/export/stl` | Generate + download STL (sync) |
| POST | `/api/export/obj` | Generate + download OBJ (sync) |
| POST | `/api/export/3mf` | Generate + download 3MF (sync) |
| POST | `/api/export/crosssection` | Generate cross-section OBJ |
| POST | `/api/export/preview` | Adaptive preview mesh for Three.js → `{vertices [col,row,z_mm], faces, face_count, cols, rows, z_min, z_max, …, preview: {adaptive, stride, tolerance_mm, max_error_mm, seconds}, water_idx, water_pct, scale: {z_mm_per_m, elev_min_m, base_mm, sea_level_cap}}` (water vertices and relative carve depth %, 200 = open sea; only with a river/lake carve). Body `river_depth_mm` carves rivers and lakes in print mm (main river that deep, ≥ 0.5 mm wide). ≤ 150 k faces: `heightfield_tin_budget` from the export tolerance, raised until it fits; DEMs over 250 k px are strided first (preview only) |
| POST | `/api/export/preflight` | Same body as `/start` (`format` = `city` \| `puzzle` \| …), nothing built → `{size_mm, bed_mm, fits_bed, scale, vertical_exaggeration, puzzle: {cols, rows, method, col_edges_mm, row_edges_mm, largest_piece_mm, knob_mm}, layers: {name: {polygons, widened, thin, clamped, dropped, thinnest_mm, tallest_mm, …}}, thinnest_feature_mm, tallest_spike_mm, faces_est, estimate: {filament_g, print_hours, printed_cm3, formula}, warnings, seconds}`. OSM layers from the cache only (uncached ones are a warning). `app/server/core/preflight.py::preflight` |
| POST | `/api/export/puzzle` | Start async puzzle export → `{task_id}` (same as `/start` with `format="puzzle"`) |
| POST | `/api/export/start` | Start async export → `{task_id}`; body `"format"` is one of `stl`, `obj`, `3mf`, `puzzle`, `city` (default `stl`; others 400). `format="city"` takes `landmark_overrides: {osm_id: {kind: "mesh", upload_id, fit?, rotation_deg?, scale?, offset_m?, vertical?: "true"\|"fit", height_m?, up_axis?} \| {kind: "ndsm", provider?: "auto"\|name, resolution_m?}}` — resolved before the build (bad mesh / no survey data fails the task naming the landmark), applied by `build_on_terrain`; `report.json` `landmarks` = `{osm_id: {kind, source, status: applied\|failed\|missing, reason?, replaced_features, faces}}`. `format="city"` fails with a clear message when city layers (trails included) are enabled on a bbox over 25 km diagonal, unless the body sets `"allow_large_city": true` (`app/server/core/city_data.py::check_city_area`) |
| GET | `/api/export/status/{task_id}` | Poll async task → `{status, progress, message, alive, elapsed_s, idle_s}` (`alive` = worker heartbeat; the client gives up only after 3 min with no status change and no heartbeat) |
| GET | `/api/export/model-parts/{task_id}` | A finished city build's parts for the Extrude viewer, binary: uint32 header length, JSON header `{parts: [{name, vertices, faces}]}`, then per part float32 vertices (model mm, x east, y north, z up) and uint32 faces (`numpy2stl.io.write_parts_file`). Keeps the task for the download. |
| GET | `/api/export/download/{task_id}` | Download result of completed async task (file auto-deleted after send) |

> **Puzzle fields** (`format="puzzle"`, flat; the city build takes the same keys under `puzzle`, see `app/server/core/puzzle.py`): `split_cols`/`split_rows` or `piece_mm`, or `col_edges_mm`/`row_edges_mm` (cut positions from the west / south edge, `[0, …, size]`, strictly increasing, ends within max(0.5 mm, 1 %) of the model size), `knob_width_mm`, `knob_depth_mm`, `knob_shape` (`classic` \| `dovetail` \| `rectangular`), `clearance_mm`, `puzzle_method` (`auto` \| `mask` \| `boolean`; auto = mask for terrain-only models), `engrave_ids` (default true: id + north arrow 0.6 mm into the underside), `layout` + `bed_mm` (adds `<name>_plate<N>.3mf`). The download carries `X-Puzzle-Info` (grid, method, timings). The city `report.json` gains `puzzle` and `check` (size vs bed, faces, watertight, widened/clamped, filament and time estimate).

> **Sync vs async export:** Sync endpoints (`/stl`, `/obj`, `/3mf`) block until the file is ready and stream it directly. Async endpoints (`/start`, `/puzzle`) start a background thread and return a `task_id` for polling. The async path is preferred for large DEMs and puzzle exports. Finished tasks are cleaned up after 300 s (`app/server/core/export_tasks.py::_TASK_TTL`).

There are no split-export, OBJ-inspection or slicer routes; `/api/export/*` is exactly the table above.

## City Routes (`app/server/routers/cities.py`)

Primary `TerrainSession` touchpoints:

- `fetch_cities()` uses `/api/cities`
- city raster and export helpers should be traced through this router module

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/cities/cached` | Check if OSM bbox is cached |
| POST | `/api/cities` | Fetch OSM data (rejects >15 km diagonal); cached as `.json.gz`. Blocking; kept for the SDK — the browser uses the background variant below |
| POST | `/api/cities/start` | Same body and size guard as `POST /api/cities`, run as a background task (`app/server/core/city_fetch_tasks.py`); returns the status shape below. An identical fetch still running is joined |
| GET | `/api/cities/status/{task_id}` | `{task_id, status: running\|done\|error\|cancelled, layers: [{name, state}], mirror, message, error, elapsed_s, diagonal_km}`; layer state `pending\|fetching\|done\|failed\|cached\|cancelled` (plus a `heights` row for building-height enhancement). 404 once expired (5 min after finishing) |
| GET | `/api/cities/result/{task_id}` | The finished payload (same body as `POST /api/cities`); 409 unless `done` |
| POST | `/api/cities/cancel/{task_id}` | Cancel: layers not yet started are skipped; Overpass queries in flight finish in the worker and are discarded. Returns the status |
| POST | `/api/cities/raster` | Rasterize OSM buildings/roads/waterways to a DEM-format height map (`values_b64` little-endian float32, `width`, `height`, `vmin`, `vmax`, `bbox`) — used by `loadCityRaster()` in `city-render.js` |
| POST | `/api/cities/export3mf` | Generate 3MF with terrain + building prisms |
| GET | `/api/cities/google3d-available` | Check if Google 3D Tiles are available for the current bbox |
| POST | `/api/cities/enhance-heights` | Refine building heights using an alternative height provider (e.g. Google 3D Tiles) |
| POST | `/api/cities/landmarks` | `LandmarksRequest {buildings, tallest_n=10, region?}` → `{landmarks: [{osm_id, index, name, category (worship\|civic\|historic\|attraction\|tallest), building, height_m, height_source, parts, roof_shapes, area_m2, centroid, bbox}], survey_sources: [{name, label, resolution_m, available, note}], overrides, ids_missing}` (`city2stl.landmarks.list_landmarks`; `ids_missing` = data cached before OSM ids were kept) |
| GET | `/api/cities/survey-sources?north&south&east&west` | Surveyed nDSM providers and whether each covers the bbox (`city2stl.height.providers.survey`, no network) |
| POST | `/api/cities/landmarks/preview` | `LandmarkPreviewRequest {buildings, osm_id, override?}` → `{vertices, faces (flat lists, mm), size_mm, mm_per_m, report: {landmarks, buildings, watertight}}` — that landmark alone, true scale, no slenderness cap, on a 2 mm plinth, built by `build_on_terrain`. 400 with the reason for a bad mesh / no survey data / unknown id |

> **Two city rasterization endpoints exist:**
> - `/api/cities/raster` — returns a flat height map in DEM format (direct canvas rendering via `city-render.js`)
> - `/api/composite/city-raster` — returns per-feature height-delta arrays used by the composite DEM pipeline
>
> They serve different consumers: the first is for the CityRaster layer view; the second feeds `composite-dem.js`.

## Geocode Routes (`app/server/routers/geocode.py`)

Landmark search and the POI-near-edge warning (F-UX batch 2). Both results are cached
on disk (`geocode` / `landmarks` namespaces of `geo2stl.cache`); tests mock the network.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/geocode?q=&limit=5` | Nominatim search (`geo2stl.geocode.search_places`: `format=jsonv2`, identifying User-Agent, ≤ 1 request/s process-wide, cached 30 days). Returns `{query, results: [{name, display_name, lat, lon, bbox: {north, south, east, west} \| null, class, type, osm_type, osm_id}]}`; 502 on upstream failure. The client searches on submit only (the usage policy forbids autocomplete) |
| GET | `/api/geocode/edge-landmarks?north&south&east&west[&warn_m=200&band_m=400]` | Named notable OSM features (place of worship, town hall, castle / historic=*, tourism attraction / museum / viewpoint) in a `band_m` band centred on the box edge — one Overpass query over the four edge strips (`geo2stl.landmarks`, cached 7 days) — reported when within `warn_m` of the edge, inside or outside: `{landmarks: [{name, class, type, lat, lon, position: inside\|outside\|crosses, edge, distance_m, message}], warn_m, band_m}`, nearest first (crossings first). `message` reads "Alhambra is 120 m outside the east edge". Boxes over 60 km diagonal return `skipped` without a query; 502 on upstream failure |

## Composite Routes (`app/server/routers/composite.py`)

Primary `TerrainSession` touchpoints:

- `composite_city_raster()` uses `/api/composite/city-raster`

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/composite/city-raster` | Rasterize OSM features to height-delta arrays (PIL, ~50× faster than JS). Supports `projection` and `clip_valid_region` for uniform pipeline alignment — used by `composite-dem.js` |

## Cache & Settings (`app/server/routers/cache.py`, `app/server/routers/settings.py`)

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

## Diagnostics (`app/server/routers/diagnostics.py`)

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/diagnostics` | Status snapshot for the Diagnostics panel: `{ok, auth, dem_sources, cache: {path, size_mb}}`; optional `north/south/east/west` add a `region_probe` (DEM coverage check for the selected region) |

## Auth & Data Sources (`app/server/routers/auth.py`)

Backs the 🔑 Keys panel in the header. Everything here writes to
`map2stl/config.json` and re-binds the running process, so no restart is needed.

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
desktop tool. The dialog blocks, so it runs through `app/server/core/validation.py::run_sync`; a server
with no display returns **501** and the client falls back to typing the path. Returns
`{"supported": true, "cancelled": true}` when the user dismisses the dialog.

## Height Routes (`app/server/routers/height.py`)

Building height rasters from multiple providers. Router prefix `/api/height`; request models
`HeightSourcesRequest` / `HeightFetchRequest` live in the router, which imports the provider
registry from `city2stl/height/service.py` (`provider_infos`, `_select_providers`).

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/height/sources` | List height providers and whether each covers the bbox (`provider_infos`) |
| POST | `/api/height/fetch` | Fetch and merge building heights from the selected provider(s) (`city2stl.height::merge_height_rasters`) |

- `TerrainSession.fetch_building_heights()` does **not** use these routes; it runs the providers in-process ([sdk.md](sdk.md)).
- Providers: [height-providers.md](height-providers.md); history: [height-pipeline-plan.md](../plans/done/height-pipeline-plan.md).

## Pipeline Report Routes (`app/server/routers/reports.py`)

Browse the skyline pipeline's rendered artifacts. The page itself is `GET /reports`
(template `app/client/templates/reports.html`, behaviour in `app/client/static/js/reports.js`).

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/reports/index` | Inventory of every region report, height report, trace and PDF on disk, with per-seed rows and artifact URLs |
| GET | `/api/reports/heights/{region_dir}` | Summary of one region's `heights.json` (source counts and height percentiles, not the building list) |
| GET | `/api/reports/registration` | Plate-registration reports and align-tool packs (F-REGION §5): `{roots, reports, packs, totals}` |
| GET | `/reports/files/{root}/{path}` | One artifact file. `root` is `region`, `height`, `trace`, or a registration root (below); paths are resolved and checked for containment, and only viewable extensions are served |

The inventory is rebuilt by scanning directories on every request rather than read from
`build_landing_page.py`'s static `index.html`, which has to be re-run after each batch.

Registration roots (`app/server/routers/reports.py::_REG_ROOTS`, read-only; override the whole set with
`MAP2STL_REGISTRATION_REPORT_ROOTS="key=path;key=path"`, relative to `Code/`):

- `registration` — `Code/_reports/` (numpy2stl batch reports, `index.html` + `summary.html`)
- `registration_regen` — `Code/_reports_regen/` (skipped when absent)
- `micropolitan` — `Cities/micropolitan/reports/`
- `mesh_import` — `cache/mesh_imports/reports/` (the app's per-import auto-register reports)
- `align` — `tools/align_tool/data/`: one entry per pack from its `meta.json` (placement
  source, refinement, street placement, tile-consensus verdict, thumbnails); `.npy` arrays
  are never served

The page shows them under a **Registration** tab and in the sidebar.

## Usage log (`app/server/routers/usage.py`)

Local-only record of how the app is used (F-USAGE), written by
`app/client/static/js/modules/core/usage-log.js`.

| Method | Path | Description |
|---|---|---|
| POST | `/api/usage` | `{session, events: [...]}` (≤ 1000 events, ≤ 512 kB): each event appended as one line to `output/usage/<YYYY-MM-DD>.jsonl` with `session`. A `value` whose control id / name / label mentions pass, key, token, secret, auth or credential is stored as `[redacted]`. `MAP2STL_USAGE_LOG=0` makes it a no-op, `MAP2STL_USAGE_DIR` moves the folder |
| GET | `/api/usage/status` | `{enabled, dir, files: [{name, bytes}]}` |

## Guides (`app/server/routers/guides.py`)

The SOPs in `docs/guides/*.md` ([guides/](../guides/)), rendered in the app. The page is `GET /guides` (and
`GET /guides/{slug}`, which opens that guide first; 404 for an unknown slug) in `app/server/server.py`,
template `app/client/templates/guides.html`, behaviour in `app/client/static/js/guides.js`. Deep links:
`/guides#<slug>/<anchor>` or `/guides/<slug>#<anchor>`; anchors are heading ids and `step-N`.

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/guides` | `[{slug, title, summary}]` for every `docs/guides/*.md` (title = first `# ` heading, summary = first paragraph, plain text); a new file appears without registration |
| GET | `/api/guides/{slug}` | `{slug, title, html, toc: [{id, text, level}]}`. `toc`: `##`/`###` headings plus the numbered steps of the "process" section (`step-N`, level 3) |
| GET | `/guides/img/{path}` | A screenshot from `docs/guides/img/` (images only, traversal-guarded, read-only) |

Rendered server-side with `markdown` (tables, fenced_code, toc with permalinks, attr_list,
sane_lists; `tab_length=3` because the SOPs indent step continuations by three spaces).
Relative `img/...` URLs become `/guides/img/...` and `other-sop.md#x` links become
`/guides/other-sop#x`. Slugs must match `[A-Za-z0-9][A-Za-z0-9_-]*` and name a file directly
in `docs/guides/`. Renders are cached per file mtime, so an edited SOP shows without a restart.

## Mesh Import and Plate Registration (`app/server/routers/layers.py`, `app/server/routers/registration.py`)

Mesh import (`/api/layers/mesh/*`, F-MESHIMPORT): upload, heightmap, manual point-pair
register, library browse and per-city location sidecars.

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/layers/mesh/upload` | Store an uploaded mesh (`.stl`, `.obj`, `.glb`, `.gltf` with embedded buffers; glTF is for landmark meshes) → `upload_id` |
| POST | `/api/layers/mesh/{upload_id}/heightmap` | Convert the upload to a heightmap for a bbox (`MeshHeightmapRequest`) |
| POST | `/api/layers/mesh/{upload_id}/register` | Fit a 2D affine from clicked point pairs and warp the heightmap (`MeshRegisterRequest`) |
| DELETE | `/api/layers/mesh/{upload_id}` | Remove a stored upload |
| POST | `/api/layers/mesh/{upload_id}/auto-register` | Geocode + OSM registration. Response adds `scores` (`rmse_m, mae_m, bias_m, pearson_r, spearman_r, coverage_pct, footprint_iou, match_score, n_buildings, building_p95_abs_m, building_median_abs_m, height_scale_used`) and `report_url` / `report_dir` (numpy2stl HTML report in `cache/mesh_imports/reports/<city>_<hash>/`; body `write_report: false` skips the ~10 s report) |
| GET | `/api/layers/mesh/library` | Mesh-library files grouped by city |
| POST | `/api/layers/mesh/library/{rel_path}/heightmap` | Heightmap for a library file + bbox (cached) |
| POST | `/api/layers/mesh/library/{rel_path}/register` | Point-pair register, for a library file |
| POST | `/api/layers/mesh/library/{rel_path}/auto-register` | Same as auto-register, for a library file |
| POST | `/api/layers/mesh/library/{rel_path}/location` | Save the sidecar bbox; optional `placement` object is stored beside it (the panel saves pack, centre, turn, size, verdict) |

Plate registration and the model critic (`/api/registration/*`, F-REGION §5 / F-LANDMARK §6):

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/registration/packs` | Align-tool packs: `{slug, city, region, window, placeable, scorable, surveyed, verdict}` |
| GET | `/api/registration/match?rel_path=` | The pack a mesh-library file depicts (city-name match, miniature-aware), or `null` |
| POST | `/api/registration/plate/start` | `{slug, place=true, fix=true, rel_path?}` → task snapshot; an identical running task is joined |
| GET | `/api/registration/plate/status/{task_id}` | `{status: running/done/error/cancelled, stage, progress, message, elapsed_s, result}`; `result` = `{placement, verdict{status, reasons, lead, endorsing, voting, corrected_m, ...}, geometry{center, corners, turn_deg, width_m, height_m, bbox}, matrix, window}` |
| POST | `/api/registration/plate/cancel/{task_id}` | Mark cancelled (a running placement finishes in its thread; its result is discarded) |
| GET | `/api/registration/critic/references?north&south&east&west` | Packs overlapping the bbox (best overlap first) plus the 3DEP nDSM option |
| POST | `/api/registration/critic/score` | `{reference: {kind: pack, slug} or {kind: ndsm, bbox, resolution}, model: {kind: buildings, features} or {kind: stl, upload_id, bbox, up_axis, m_per_unit?}}` → `{buildings{n, median_abs_error_m, p90_abs_error_m, mean_error_m, within_2m_pct, within_5m_pct}, footprint{iou, precision, recall}, cells{mae_m, rmse_m, bias_m, pearson_r}, roofs{median_shape_error_m, median_relief_ref_m, median_relief_model_m, resolvable, note}, scale, reference, model}` |

Nothing under `tools/align_tool/data/` is written by these routes; the placement is saved
only when the client posts it to the location route above.

## Key Pydantic Models (`app/server/schemas.py`)

- `BoundingBox` — `{north, south, east, west}`
- `RegionCreate(BoundingBox)` — `+ name, label?, description?`
- `CityRequest(BoundingBox)` — `+ layers: list[str], simplify_tolerance, min_area`
- `MergeRequest` — `{bbox, dim, layers: list[MergeLayerSpec]}`
- `MergeLayerSpec` — `{source, blend_mode, weight, processing: ProcessingSpec, options}` (river/lake sources: `blend_mode: "add"`, see `/api/composite/dem-merge`)
- `ProcessingSpec` — `{clip_min, clip_max, smooth_sigma, sharpen, normalize, invert, extract_rivers, river_max_width_px}`
- `MeshAutoRegisterRequest` — `{filename_hint?, resolution, min_region_iou, write_report}`
- `PlateRegistrationStartRequest` — `{slug, place, fix, rel_path?}`
- `CriticScoreRequest` — `{reference: CriticReference, model: CriticModel}`
- `LandmarksRequest` — `{buildings, tallest_n, region?}`; `LandmarkPreviewRequest` — `{buildings, osm_id, override?}`
- Also defined there: `EnhanceHeightsRequest`, `CityRasterRequest`, settings item models (`ProjectionInfo`, `ColormapInfo`, `DatasetInfo`), mesh-import request models (`MeshHeightmapRequest`, `MeshRegisterRequest`, `MeshLibrarySetLocationRequest`, `MeshLibraryHeightmapRequest`, …), `CriticReference` / `CriticModel`
- Only request models are declared: responses are plain dicts / `JSONResponse` (the unused response and terrain/export models were deleted 2026-09-30). The saved-settings blob is free-form JSON.
- Most export routes take a raw JSON body (`await request.json()`), parsed by `app/server/core/export_params.py::ExportContext`, not a Pydantic model.

## DEM Sources

OpenTopography keys come from `geo2stl/opentopo.py::OPENTOPO_DATASETS` (re-exported by
`app/server/config.py`); native resolutions for every source are in `geo2stl/dem.py::DEM_SOURCE_INFO`.

| Key | Description |
|-----|-------------|
| `SRTMGL1` | SRTM 30m global |
| `SRTMGL3` | SRTM 90m global |
| `AW3D30` | ALOS World 3D 30m |
| `COP30` | Copernicus DSM 30m |
| `COP90` | Copernicus DSM 90m |
| `SRTM15Plus` | SRTM15+ bathymetry + land |
| `local` | Local tile store (`config.json` `ocean_root`, via `geo2stl/dem.py::make_dem_image`); the project's store is GEBCO 2025, 15″ ≈ 460 m |
| `h5_local` | Local SRTM3 HDF5 store, 3″ ≈ 90 m |
| `water_esa` | ESA WorldCover water mask band (merge layer source, not a terrain DEM) |
