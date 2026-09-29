# Pipeline — saved location to printable file

_Last updated: 2026-09-28_

Scope: how a location saved in `map2stl/data.db` becomes an STL, OBJ, 3MF, puzzle zip or city
model zip. It does not cover mesh maths (`numpy2stl/README.md`), library modules
([packages.md](packages.md)), STL registration (`numpy2stl/docs/registration.md`) or the browser
internals ([frontend.md](frontend.md)). The architecture summary is in [overview.md](overview.md).

## Two facts to know first

- **Every mesh export is two stages** — `app/server/core/export.py::terrain_stage` (raster) →
  `city2stl/city_model.py::build_on_terrain` (vector). A terrain-only export is the city model
  with no layers. Why: [mesh-pipeline](../decisions/mesh-pipeline.md).
- **The pipeline is client-orchestrated.** There is no server-side "render this region" call:
  the browser loads the DEM with one request and exports with another, joined by a `dem_id`.
  Exporting without first loading the DEM preview cannot work.

```
browser: select region ─> GET  /api/regions + /api/regions/{name}/settings (settings blob)
browser: load DEM ──────> GET  /api/terrain/dem?...            → grid + dem_id (DemStore) + disk cache
browser: (Composite) ───> POST /api/composite/city-raster ...   → 2D preview channels
browser: preflight ─────> POST /api/export/preflight            → size, faces, warnings (nothing built)
browser: export ────────> POST /api/export/start {format, dem_id | dem_values | composite_layers}
browser: ───────────────> GET  /api/export/status/{task_id}     (poll)
browser: ───────────────> GET  /api/export/download/{task_id}   (FileResponse, then deleted)
```

## Where a saved location lives

- SQLite `data.db`, created by `app/server/core/db.py::init_db`:

| Table | Holds |
|---|---|
| `regions` | name, label, description, bbox (`north/south/east/west`), seven parameter columns |
| `region_settings` | one row per region: `settings_json`, a free-form JSON blob |
| `region_landmarks` | per-building landmark overrides (`osm_id` → `spec_json`) |

- The settings blob has ten groups: `dem`, `projection`, `view`, `water`, `esa`, `satellite`,
  `export`, `split`, `city`, `hydrology`. Server defaults: `app/server/routers/settings.py::_default_region_settings`.
- **The blob is unversioned and unvalidated** (issues.md PA-10; F-FE1 step 3). Old blobs are
  shape-guessed on the client (`app/client/static/js/modules/ui/presets.js::_mergeSettings`).
- Of the seven `regions` parameter columns only `dim`, `depth_scale`, `water_scale` and
  `subtract_water` feed the render path (via the DEM request); export takes `model_height` /
  `base_height` from the request body.

## Stage by stage

### 1. Load a region (browser)

| Step | Anchor |
|---|---|
| Region selected; layer caches flushed | `app/client/static/js/modules/regions/regions.js::selectCoordinate` |
| Defaults + saved settings fetched and deep-merged | `app/client/static/js/modules/ui/presets.js::loadAndApplyRegionSettings` |
| Merged settings applied to the controls | `app/client/static/js/modules/ui/presets.js::applyAllSettings` |
| Region rows / settings blob on the server | `app/server/routers/regions.py` (`/api/regions`, `/api/regions/{name}/settings`) |

### 2. Fetch the DEM and hand it off

| Step | Anchor |
|---|---|
| Client request; snapshots what it asked for | `app/client/static/js/modules/dem/dem-main.js::loadDEM` → `appState.lastDemRequest` |
| Route (GET or POST; client sends GET) | `app/server/routers/terrain.py::get_terrain_dem` |
| Source dispatch (OpenTopography / local tiles / HDF5) | `geo2stl/dem.py::fetch_dem` |
| Raw array cached under the one DEM key | `app/server/core/dem_cache.py::dem_cache_key` |
| Projection, applied after the cache on every request | `geo2stl/projections.py` ([projections.md](projections.md)) |
| Projected grid stored; `dem_id` returned | `app/server/core/dem_store.py::DemStore` |

- `dem_id` names the exact grid the user saw (after projection and clipping); nothing is
  re-derived at export time.
- Handles live in memory, spill to disk, expire after an hour and do not survive a restart;
  an unknown id → `DemGone` → HTTP 410 "reload the DEM".
- Why a handle and one key definition: two hand-built key copies drifted and gave "Missing DEM
  data" — [terrain-dem](../decisions/terrain-dem.md). **Never copy the key.**
- Water mask, satellite, land cover, hydrology and trails are fetched for the preview only; they
  reach the mesh only through a composite spec (below) or the city model.

### 3. Composite (optional, browser → server)

- The Composite panel combines terrain channels (DEM, water depth, land cover, vegetation) and
  OSM channels (buildings, roads, waterways, walls) into a 2D preview
  (`app/client/static/js/modules/layers/composite-dem.js`).
- "Apply" sets the terrain part as the export's DEM spec:
  `app/client/static/js/modules/layers/composite-spec.js::buildCompositeLayerSpec`.
- **OSM channels never reach the mesh terrain.** Client filter:
  `app/client/static/js/modules/layers/composite-spec.js::FEATURE_SOURCES`; server filter:
  `app/server/core/export_params.py::mesh_composite_layers`. The city model builds those
  features as vectors. Why: rasterised buildings in both stages print twice
  ([mesh-pipeline](../decisions/mesh-pipeline.md)).
- Server arithmetic: `app/server/routers/composite.py::compute_composite_dem` (rivers/lakes returned
  as a separate carve grid). Plan and open passes: [F-COMPOSITE3](../plans/active/F-COMPOSITE3-server-side-composite.md);
  design history: [composite-dem-design.md](composite-dem-design.md).

### 4. Export request (browser)

- Body built by `app/client/static/js/modules/export/export-handlers.js::_demSettings` (DEM
  snapshot + `dem_id`) plus `_exportParams`, `_cityExtra`, `_puzzleExtra`.
- Terrain-only exports (`downloadSTL`, `downloadModel`), `exportPuzzle` and `exportCityModel` all
  go through `_asyncExport` → `POST /api/export/start`.
- `runPreflight` → `POST /api/export/preflight` first reports size vs bed, faces, thin features,
  filament and print time (`app/server/core/preflight.py::preflight`; nothing is built).

### 5. Server: resolve the DEM

- `app/server/core/export_params.py::ExportContext.from_request` picks the DEM in priority order:
  1. composite spec (`composite_layers`, feature channels dropped) → `compute_composite_dem`
  2. inline `dem_values` (an edited grid)
  3. `app/server/core/export_params.py::resolve_dem`: `dem_id`, else the rebuilt cache key
- A composite that fails to build falls back to the plain DEM and sets `composite_error`, which
  is returned as the `X-Composite-Error` header — a model silently missing its layers looks correct.

### 6. Terrain stage (raster)

`app/server/core/export.py::terrain_stage` → `TerrainField` (heightfield in mm, row 0 north).

| Step | Anchor |
|---|---|
| NaN fill (lowest real elevation) + 3×3 median | `city2stl/city_model.py::prepare_dem` |
| River / lake carve, **after** the median (a 1 px channel would not survive it) | `app/server/core/export.py::_prepare_dem_array` |
| Sea-level cap (raise below-zero to 0) | `app/server/core/export.py::_prepare_dem_array` |
| Vertical scale: `auto` = true scale × exaggeration under 20 km diagonal, fit above | `city2stl/city_model.py::choose_scale` → `ModelScale` |
| Label engraving, contour lines | `app/server/core/export.py::_apply_label_engraving`, `::_apply_contour_lines` |

- 1 DEM px = 1 mm by default (`mm_per_pixel`). Why: [mesh-pipeline](../decisions/mesh-pipeline.md)
  ("The city model is 1 px per mm, true vertical scale…").

### 7. Feature stage (vector)

`city2stl/city_model.py::build_on_terrain(z_mm, bbox, scale, layers, …)` → `CityModel`
(`.merged` mesh + report).

- Terrain solid: adaptive TIN within the print tolerance
  (`city2stl/city_model.py::terrain_solid` → `numpy2stl/src/numpy2stl/core/heightfield.py::tin_solid`).
- Layers (city export only): buildings, roads, waterways, walls, towers, churches, trails… —
  extruded, draped or cut per `city2stl/city_model.py::LayerStyle`, printability rules applied
  (min width, height caps), merged with manifold3d.
- Landmark overrides replace chosen buildings with an uploaded mesh or a survey nDSM solid
  (`city2stl/landmarks.py::resolve_overrides`).
- Cached per layer and for the terrain TIN in `city2stl/model_cache.py`.

### 8. Outputs

| Format | Path | Result |
|---|---|---|
| `stl` / `obj` / `3mf` | `app/server/core/export.py::_run_export_pipeline` → `_prepare_export_mesh` | one watertight solid; `X-Watertight` / `X-Face-Count` headers |
| `puzzle` | `app/server/core/export.py::generate_puzzle` → `app/server/core/puzzle.py::cut_to_zip` | zip: OBJ per piece, 3MF in place, optional per-bed 3MFs |
| `city` | `app/server/core/city_model_task.py::run_city_model` | zip: STL, 3MF, optional puzzle, `report.json` (build report + preflight `check`) |
| preview | `app/server/core/export.py::generate_mesh_preview` | JSON vertices/faces for the Three.js viewer |

- Tasks: `app/server/core/export_tasks.py::start_export_task` (daemon thread, 12-char id); files go
  to the OS temp directory and are deleted after download — no output directory.
- Sync routes `/api/export/stl`, `/obj`, `/3mf` (`generate_stl` / `generate_obj` / `generate_3mf`)
  share `_prepare_export_mesh`; the browser uses only `/api/export/start`. Why they share one
  path: [mesh-pipeline](../decisions/mesh-pipeline.md) ("Every export format goes through one mesh path").
- User-facing walkthroughs with reference renders: [city STL and puzzle guide](../guides/city-stl-and-puzzle-sop.md),
  [large-region guide](../guides/large-region-sop.md).

## Not on this pipeline

- `app/session/terrain_session.py::TerrainSession` — an HTTP SDK over these same routes, not the
  pipeline's state ([sdk.md](sdk.md)).
- `geo2stl/write.py::savefile` — notebook-era writer, not on the app path.
- `/api/composite/dem-merge` — kept as a server route; the browser merge panel that used it is gone.

## Known defects

- Open pipeline bugs (resolution cap, SRTM voids, settings versioning, non-atomic writes, the
  binary STL writer): [issues.md](../issues.md) § "Active bugs — from the pipeline audit".
- The original audit: [pipeline-audit-2026-08-26](../history/audits/pipeline-audit-2026-08-26.md).

## Testing

- Unit / integration: `tests/test_export.py`, `tests/test_preflight.py`,
  `tests/test_puzzle_export.py`, `tests/test_city_model.py`, `tests/test_dem_store.py`,
  `tests/test_region_settings.py`, `tests/test_terrain.py`, `tests/test_session_e2e.py`.
- Browser end-to-end: `tests/e2e/` — uvicorn on a free port, Playwright, a strict console-error
  gate and a per-session temp cache dir (`tests/e2e/conftest.py::live_server_url`).
