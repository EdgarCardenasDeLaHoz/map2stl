# Index — 3D Maps

_Last updated: 2026-09-28_

The one index for developers and agents. Three parts:
1. [Doc map](#1-doc-map) — which document answers which kind of question.
2. [Where to edit](#2-where-to-edit) — task → the files that own it.
3. [Locator](#3-locator) — topic → `file::symbol`.

Conventions
- Code paths are relative to `Code/` (the workspace root): `map2stl/...`, `numpy2stl/src/numpy2stl/...`.
- Code is cited as `path::symbol`, never `path:line` (line numbers drift within weeks).
  - Resolve a symbol from `Code/`: `~/.venvs/graphify/Scripts/graphify explain "<symbol>"`, or grep `def <symbol>`.
- A bare name in backticks after a path, e.g. `path` (`a`, `b`), is another symbol in that file.
- Missing a topic? Search for it, then add a bullet here before finishing.

---

## 1. Doc map

Links are relative to `map2stl/docs/`.

**Start here**
- This file — the index.
- [../CLAUDE.md](../CLAUDE.md) — project rules, workflow, editing rules.
- [../../AGENTS.md](../../AGENTS.md) — multi-repo git and OneDrive rules (workspace root).

**Folders**
- [guides/](guides/) — **user** SOPs, served in the app at `/guides` (every `.md` there becomes a guide).
  - [city-stl-and-puzzle-sop.md](guides/city-stl-and-puzzle-sop.md), [large-region-sop.md](guides/large-region-sop.md), [printing-prusaslicer.md](guides/printing-prusaslicer.md)
- [reference/](reference/) — how the code works **now**.
  - [overview.md](reference/overview.md) — architecture, backend layout, two-stage mesh pipeline, caches.
  - [pipeline.md](reference/pipeline.md) — saved location → printable file, stage by stage.
  - [api.md](reference/api.md) — every route and its Pydantic models.
  - [sdk.md](reference/sdk.md) — notebooks → `TerrainSession` → routes.
  - [frontend.md](reference/frontend.md) — Vue + ES modules, `window.appState` ↔ Pinia, control ownership.
  - [frontend-modules.md](reference/frontend-modules.md) — JS/Vue module map and function index.
  - [packages.md](reference/packages.md) — geo2stl / city2stl / numpy2stl boundaries, app → library calls.
  - [layer-system.md](reference/layer-system.md), [projections.md](reference/projections.md), [composite-dem-design.md](reference/composite-dem-design.md)
  - [height-providers.md](reference/height-providers.md), [survey-sources.md](reference/survey-sources.md), [roof-shape-model.md](reference/roof-shape-model.md), [align-refinement.md](reference/align-refinement.md)
- [decisions/](decisions/README.md) — **why** the code is the way it is; one file per topic, entry format in its README.
- [plans/](plans/README.md) — the roadmap (every open item, one line each); plan files in `plans/active/`, `plans/done/`, `plans/archive/`.
- [research/](research/) — experiments whose findings still matter: [plate-critic.md](research/plate-critic.md), [shadow-heights.md](research/shadow-heights.md), [roof-shape-research.md](research/roof-shape-research.md).
- [issues.md](issues.md) — live bugs and technical debt.
- [proposals.md](proposals.md) — proposal tracker (pending → approved → done).
- [history/](history/README.md) — finished, abandoned or superseded work (audits, refactors, ML height training). Not how the code works now.

**Outside `map2stl/docs/`**
- Skyline (street-view heights): `map2stl/city2stl/skyline/README.md` (the one skyline doc), `map2stl/city2stl/skyline/docs/STATUS.md`.
- numpy2stl: `numpy2stl/README.md`, registration usage `numpy2stl/docs/registration.md`, registration design `numpy2stl/src/numpy2stl/registration/docs/ARCHITECTURE.md`.
- Checkpoints on disk and which are live: `map2stl/models/README.md` (only the roof-shape GBM runs; no CNN height model at runtime).
- Session state (unversioned): `claude/memory-bank/activeContext.md`.

---

## 2. Where to edit

Paths in this part are relative to `map2stl/`.

### First decision

| If the task is about... | Start here |
|---|---|
| Route shape, request payloads, validation | [reference/api.md](reference/api.md), then `app/server/routers/<family>.py` |
| Processing, caching, export generation, data fetching | `app/server/core/`, or the library below it (`geo2stl/`, `city2stl/`, numpy2stl) |
| The mesh itself (terrain, buildings, roads, water, puzzle) | [reference/pipeline.md](reference/pipeline.md); `app/server/core/export.py::terrain_stage` → `city2stl/city_model.py::build_on_terrain` |
| Notebook / Python SDK | [reference/sdk.md](reference/sdk.md), `app/session/terrain_session.py` |
| Browser UI | [design-guidelines.md](design-guidelines.md) (how pages look and behave; read first), [reference/frontend.md](reference/frontend.md), [reference/frontend-modules.md](reference/frontend-modules.md) |
| Building heights | [reference/height-providers.md](reference/height-providers.md), `city2stl/height/` |
| Roofs, building parts, landmarks | `city2stl/roofs.py`, `city2stl/landmarks.py`, `city2stl/city_model.py` |
| Plate registration / align tool | [§ Registration](#registration--plates-map2stl-side), `city2stl/registration/`, numpy2stl `registration/` |
| Skyline (street-view heights) | `city2stl/skyline/README.md` |
| User guides (/guides) | `docs/guides/*.md` (content), `app/server/routers/guides.py` (rendering) |
| Tests | `tests/conftest.py`, the matching `tests/test_*.py`; JS tests in `tests/js/`; while developing `scripts/quicktest.py` (affected tests only) |

### Router or core?
- Edit `app/server/routers/` when the HTTP shape, validation boundary or endpoint orchestration changes.
- Edit `app/server/core/` (or the library) when processing, caching, export or fetch internals change.
- Libraries stay app-free: numpy2stl is geo-free; geo2stl/city2stl never import `app`. [why](decisions/architecture.md)

### Route families (`app/server/routers/`)

| Concern | Router |
|---|---|
| DEM (`dem_id`), water, land cover, satellite, hydrology, trails, sources | `terrain.py` |
| STL/OBJ/3MF, preview, preflight, puzzle, async export tasks | `export.py` |
| OSM city layers, fetch tasks, city raster, height enhancement, landmarks | `cities.py` |
| Server composite (city raster, DEM merge, hydrology merge) | `composite.py` |
| Saved regions, settings blob, landmark overrides | `regions.py` |
| Mesh import (upload / library) → heightmap → registration | `layers.py` |
| Plate registration packs + tasks, model critic | `registration.py` |
| Height sources | `height.py` |
| Guides, reports, usage log | `guides.py`, `reports.py`, `usage.py` |
| Settings, cache, API keys / tile folder, place search, diagnostics | `settings.py`, `cache.py`, `auth.py`, `geocode.py`, `diagnostics.py` |

### Frontend by view (`app/client/static/js/`)
- Markup is Vue (`vue/components/{layout,sidebar,views,dem,shared}/`); behaviour is mostly ES modules (`modules/`).
  - Control ids are the interface between them: grep an `id=` before renaming it.
  - `.vue`/`.ts` edits need `npm run build`; `modules/**` is served raw.
- Explore: `modules/map/`, `modules/regions/`, `app/client/static/js/vue/components/views/LandmarkSearch.vue`.
- Edit: `modules/dem/`, `modules/layers/`, `modules/ui/`, `vue/components/dem/`.
- Extrude: `modules/export/`, `app/client/static/js/vue/components/views/ModelContainer.vue`.
- Shared state: `window.appState` (bridged into the Pinia store `app/client/static/js/vue/stores/app.ts`) — [reference/frontend.md](reference/frontend.md).

### Quick examples

| I want to... | Start here |
|---|---|
| Add a DEM source or option | `geo2stl/dem.py`, `app/server/routers/terrain.py`, `app/client/static/js/modules/dem/dem-main.js` |
| Change a city layer's geometry in the print | `city2stl/city_model.py` (`feature_polygons`, `build_layer`) |
| Add an export format | `app/server/core/export.py`, `app/server/routers/export.py`, then `modules/export/` |
| Add a composite layer | `app/server/routers/composite.py`, `app/client/static/js/modules/layers/composite-spec.js`, `app/client/static/js/vue/components/dem/CompositeDemSection.vue` |
| Add a height provider | `city2stl/height/providers/`, register in `city2stl/height/service.py` |
| Fix region save/load | `app/server/routers/regions.py`, `modules/regions/`, `app/client/static/js/modules/ui/presets.js` |
| Add a notebook helper | `app/session/terrain_session.py` |
| Write or fix a user guide | `docs/guides/` (keep file names: they are the `/guides/<slug>` URLs) |

### Before editing
- Rules: [../CLAUDE.md](../CLAUDE.md). Open work: [plans/README.md](plans/README.md), [issues.md](issues.md).
- A significant unrequested feature needs a [proposals.md](proposals.md) entry and a plan first.

---

## 3. Locator

### The mesh pipeline (two stages)

Every mesh export is `terrain_stage` (raster → heightfield) then `build_on_terrain` (vectors on it).
Walkthrough: [reference/pipeline.md](reference/pipeline.md); why: [decisions/mesh-pipeline.md](decisions/mesh-pipeline.md).

- Stage 1, DEM → `TerrainField` (mm, row 0 north): `map2stl/app/server/core/export.py::terrain_stage` (`TerrainField`, `_prepare_dem_array`; rivers in print mm: `RIVER_MIN_WIDTH_MM`, `WATER_SEA`)
- Stage 2, city layers on the terrain → `CityModel`: `map2stl/city2stl/city_model.py::build_on_terrain`
  - Terrain-only export = the city model with no layers; old entry `build_city_model` wraps it.
- Export entry points: `map2stl/app/server/core/export.py` (`generate_stl`, `generate_obj`, `generate_3mf`, `_prepare_export_mesh`, `_repair_mesh`, `_write_mesh`)
- Request → `ExportContext`, DEM resolution: `map2stl/app/server/core/export_params.py` (`ExportContext`, `resolve_dem`, `mesh_composite_layers`)
- DEM handoff: `dem_id` handles `map2stl/app/server/core/dem_store.py::DemStore`; the one cache key `map2stl/app/server/core/dem_cache.py::dem_cache_key` (`DEM_CACHE_SCHEMA_VERSION`)
- Background tasks and download: `map2stl/app/server/core/export_tasks.py` (`start_export_task`, `get_task_status`, `get_task_file`)
- City export task (layers, trails, landmark overrides): `map2stl/app/server/core/city_model_task.py::run_city_model` (`normalize_request`, `city_osm_params`)
- Live 3D preview (TIN capped at `PREVIEW_MAX_FACES`, strided DEMs): `map2stl/app/server/core/export.py::generate_mesh_preview` (`_preview_mesh`)
- Terrain puzzle: `map2stl/app/server/core/export.py::generate_puzzle` (`puzzle_spec`); cutting, engraving, plates `map2stl/app/server/core/puzzle.py` (`plan_grid`, `choose_method`, `cut_pieces`, `cut_to_zip`, `volume_check`)
- Reference-city regression set (Cartagena, Granada, Breckenridge: watertight, puzzle volume, faces/volume vs baseline, time log): `map2stl/tests/test_reference_cities.py::test_reference_city` (`pytest -m slow`), baseline `map2stl/tests/reference_cities_baseline.json`, runs appended to `map2stl/output/regression/reference_cities.jsonl`
- Pre-flight (size vs bed, pieces, faces, filament/time, warnings): `map2stl/app/server/core/preflight.py::preflight` (`print_estimate`, `build_check`); per-layer counts without solids `map2stl/city2stl/city_model.py::layer_preflight` (`layer_polygons`)
- Label engraving, contour lines, cross-section: `map2stl/app/server/core/export.py` (`_apply_label_engraving`, `_apply_contour_lines`, `generate_crosssection`)

### city2stl — city model (buildings, roads, water, trails)

- Scale ("1 mm = X m", auto true scale): `map2stl/city2stl/city_model.py::choose_scale` (`ModelScale`)
- Layer set and priority: `map2stl/city2stl/city_model.py::resolve_layers` (`LayerStyle`, `LAYER_PRIORITY`)
- Terrain in model space (projection, sampling, DEM cells under footprints): `map2stl/city2stl/city_model.py::Terrain` (`prepare_dem`, `terrain_tin`, `terrain_solid`, `terrain_tolerance`)
- Features → print polygons (line widths, min width, slenderness): `map2stl/city2stl/city_model.py::feature_polygons` (`_line_width_m`, `_widths`)
- Flat roofs merged at one print layer (< 0.4 mm apart): `map2stl/city2stl/city_model.py::merge_flat_roofs`
- Draped slab triangulation without Triangle (qhull + GEOS cavity recovery): `map2stl/city2stl/city_model.py::_slab` (`triangulate_polygon`, `_recover_segments`)
- One layer → solid, landmark swaps, parts: `map2stl/city2stl/city_model.py::build_layer` (`_layer_solid`, `is_part`, `assemble_parts`)
- Trails in the build (town / road filter): `map2stl/city2stl/city_model.py::filter_trails`
- Merged watertight solid, 3MF parts, lossless simplify: `map2stl/city2stl/city_model.py::assemble_model` (`welded_watertight`, `lossless_simplify`, `_separate_contacts`)
- Model caches (polygons, solids, terrain TIN, finished model; `MODEL_CACHE_VERSION`): `map2stl/city2stl/model_cache.py` (`polygons_key`, `solid_key`, `terrain_key`); finished model `map2stl/city2stl/city_model.py` (`_read_model`, `_write_model`)
- Roof solids (hipped, gabled, skillion, revolved domes/spires): `map2stl/city2stl/roofs.py::building_solids` (`convex_pieces`, `_revolved`, `_skillion_plane`)
- Vectorised flat prisms: `numpy2stl/src/numpy2stl/core/extrude.py::prisms`; streamed 3MF writer `numpy2stl/src/numpy2stl/io/writers.py::write3MF`
- Road widths (legacy helper module): `map2stl/city2stl/roads.py::get_road_width_m`

### city2stl — OSM fetch and city data

- Layer fetch (buildings, roads, water, POIs), concurrent (`_MAX_CONCURRENT_LAYERS`), mirror switched between passes, failed layers retried alone: `map2stl/city2stl/fetch.py::fetch_osm_data` (`_fetch_layers`, `_layer_jobs`)
- Fetch hooks `progress` / `on_mirror` / `should_cancel` → `map2stl/city2stl/fetch.py::FetchCancelled`
- Exhausted mirror list raises (`OverpassUpstreamError`); "nothing matched" vs "mirror refused": `map2stl/city2stl/fetch.py::OverpassUpstreamError` (`_features_or_none`) — [why](decisions/osm-water-hydrology.md)
- Buildings keep `osm_id` + landmark tags, parts kept: `map2stl/city2stl/fetch.py::_fetch_buildings` (`_reduce_buildings_keeping_parts`); tag columns `map2stl/city2stl/heights.py::LANDMARK_TAG_COLS`
- OSM lakes for the composite `lakes` layer: `map2stl/city2stl/fetch.py::fetch_osm_lakes`
- Metric reprojection (local UTM, not Mercator): `map2stl/city2stl/fetch.py::_to_metric`
- Cache version and staleness (`CITY_PIPELINE_VERSION`): `map2stl/city2stl/cache_policy.py::CITY_PIPELINE_VERSION` (`city_cache_missing_height_source`, `city_cache_stale_buildings_only`)
- Cache-first layers, size guard (no city layers > `CITY_LAYERS_MAX_DIAGONAL_KM` unless allowed), reuse of finer/enclosing entries: `map2stl/app/server/core/city_data.py::get_city_layers` (`check_city_area`, `lookup_city_layers`, `_candidates`)
- Coarse-tier building area floor: `map2stl/app/server/config.py::COARSE_MIN_BUILDING_AREA_M2`
- City fetch as a background job: `map2stl/app/server/core/city_fetch_tasks.py::start_city_fetch` (`cancel_task`); routes `map2stl/app/server/routers/cities.py` (`start_city_fetch`, `city_fetch_status`, `city_fetch_result`, `cancel_city_fetch`); client `map2stl/app/client/static/js/modules/layers/city-overlay.js::loadCityData` (`cancelCityFetch`), progress `map2stl/app/client/static/js/vue/components/dem/CityFetchProgress.vue`
- Overpass health probe (osmnx user agent; overpass-api.de 406s python-requests), pacing, raw QL: `map2stl/geo2stl/osm.py::healthy_overpass_endpoints` (`overpass_wait`, `overpass_backoff`, `overpass_query`)
- City raster for the composite (buildings max + holes, roads, waterways, walls): `map2stl/app/server/routers/composite.py::_rasterize_city` (`_rasterize_buildings`); library version `map2stl/city2stl/rasterize.py::rasterize_city_data`

### city2stl — building heights

Overview and ranking: [reference/height-providers.md](reference/height-providers.md); why: [decisions/building-heights.md](decisions/building-heights.md).

- OSM tag heights, the one rule (`height` with units, else levels × 3.2 m + roof level): `map2stl/city2stl/heights.py::height_from_tags` (`METRES_PER_LEVEL`, `min_height_from_tags`)
- Height fill and raster enhancement per building (`height_source`): `map2stl/city2stl/heights.py::enhance_buildings_with_raster` (`_fill_heights`)
- Provider protocol and merge by resolution-scaled confidence; sources coarser than `BUILDING_RESOLUTION_LIMIT_M` refused: `map2stl/city2stl/height/__init__.py::merge_height_rasters` (`HeightProvider`, `HeightResult`, `resolution_priority`)
- Registry, selection, enhancement (3DEP lidar first in the US): `map2stl/city2stl/height/service.py::enhance_city_data` (`_REGISTRY`, `_select_providers`, `_enhance_from_lidar`)
- Routes `/api/height/sources`, `/api/height/fetch` (import the registry from `city2stl.height.service` directly): `map2stl/app/server/routers/height.py::height_fetch` (`height_sources`)
- Providers (`map2stl/city2stl/height/providers/`):
  - Overture (module `open_buildings`): `map2stl/city2stl/height/providers/open_buildings.py::OpenBuildingsProvider`
  - GlobalBuildingAtlas (ranking vs Overture, tall-building deficit in its docstring): `map2stl/city2stl/height/providers/gba.py::GBAProvider` (`_fetch_buildings_for_bbox`, `_available_tiles`)
  - Google Photorealistic 3D Tiles: `map2stl/city2stl/height/providers/google_3d.py::Google3DProvider` (`_bv_intersects_bbox`, `_target_error_m`, `_surface_points`, `_accumulate_mesh`, `_looks_built`, `_ground_from_dsm`)
  - WSF3D 90 m + global BigTIFF fallback: `map2stl/city2stl/height/providers/wsf3d.py::WSF3DProvider` (`tiles_for_bbox`, `_global_result`), `map2stl/city2stl/height/providers/wsf3d_global.py`
  - USGS 3DEP lidar per footprint (Planetary Computer COPC): `map2stl/city2stl/height/providers/lidar_3dep_copc.py::footprint_heights` (`ndsm_for_bbox`); EPT nDSM `map2stl/city2stl/height/providers/lidar_3dep_ept.py::get_ndsm`
  - GeoTIFF reader shared by providers: `map2stl/geo2stl/raster.py::read_geotiff` (wrapped in `map2stl/city2stl/height/providers/_raster.py`)
- Survey nDSM providers, one interface `ndsm_for_bbox(bbox, res)`: `map2stl/city2stl/height/providers/survey.py::ndsm_for_bbox` (`PROVIDERS`, `available_for_bbox`); contract, grid, sanity checks, cache `map2stl/city2stl/height/providers/_survey.py` (`lonlat_grid`, `read_geotiff_array`, `cached_ndsm`)
  - France IGN `ign_lidarhd.py`, Andalucía `rediam_mdhn.py`, Spain CNIG `cnig_mdsn.py`, Czechia `cuzk_dmp.py`; per-city sources [reference/survey-sources.md](reference/survey-sources.md)
- Height-gap infill (IDW, nearest): `map2stl/city2stl/height/infill.py::infill_idw` (`infill_nearest`)
- Georeferenced STL → heightmap: `map2stl/city2stl/height/stl_import.py::stl_to_heightmap`
- CNN height prediction/training: `map2stl/city2stl/height/predict.py::predict` — not used at runtime; see [history/ml-height/README.md](history/ml-height/README.md)
- Provider accuracy and defect history: [issues.md](issues.md), [decisions/building-heights.md](decisions/building-heights.md)

### city2stl — roofs and landmarks

- Roof shape for untagged buildings (trained GBM, then the signal cascade): `map2stl/city2stl/roof_classifier.py::classify_roof_shapes` (`_classify`, `_estimate_roof_height_from_elev`); model [reference/roof-shape-model.md](reference/roof-shape-model.md)
  - Checkpoint loader and per-building call: `map2stl/city2stl/roof_model.py::load` (`RoofShapeModel`); features (order must match the checkpoint) `map2stl/city2stl/roof_features.py::FEATURES` (`extract`)
  - Zoom-18 crops and concurrent prefetch: `map2stl/city2stl/roof_tiles.py::crop_for_ring` (`prefetch_bbox`)
  - Optional CNN checkpoint (skipped unless passed): `map2stl/city2stl/roof_classifier.py::_resolve_cnn_model` (`_load_roof_checkpoint`); nets `map2stl/city2stl/roof_nets.py`
- Landmarks (F-LANDMARK): listing `map2stl/city2stl/landmarks.py::list_landmarks` (`landmark_category`); overrides `map2stl/city2stl/landmarks.py::resolve_overrides` (`load_mesh`, `fit_mesh_xy`, `mesh_solid`, `ndsm_solid`); swap inside `build_layer` `map2stl/city2stl/landmarks.py::LandmarkPlan`
  - Server glue (table `region_landmarks`, preview on a plinth): `map2stl/app/server/core/landmarks.py::resolve` (`save_region_override`, `preview`); routes `map2stl/app/server/routers/cities.py` (`list_city_landmarks`, `survey_sources`, `preview_city_landmark`), `map2stl/app/server/routers/regions.py` (`get_region_landmarks`, `save_region_landmark`)
  - Panel `map2stl/app/client/static/js/vue/components/dem/CityLandmarksSection.vue`; helpers `map2stl/app/client/static/js/modules/layers/landmark-overrides.js`
- Why: [decisions/roofs-landmarks.md](decisions/roofs-landmarks.md)

### geo2stl — DEM, imagery, projections, water, trails

- Metres per degree (the one home), bbox size, `GeoGrid` lon/lat ↔ pixel: `map2stl/geo2stl/geo.py::GeoGrid` (`m_per_deg_lon`, `bbox_size_m`, `bbox_diagonal_km`)
- DEM for a bbox from any source (local / h5 → SRTMGL3 fallback / OpenTopography): `map2stl/geo2stl/dem.py::fetch_dem` (`fetch_local_dem`, `fetch_h5_dem`)
- Source native resolution, real samples, default source: `map2stl/geo2stl/dem.py::dem_sampling` (`native_resolution_m`, `default_dem_source`)
- Layer sources registered by the server (terrain-relative ones get `base=`): `map2stl/geo2stl/dem.py::register_layer_source` (`fetch_layer_data`, `is_terrain_relative_source`)
- DEM image/payload: `map2stl/geo2stl/dem.py::make_dem_payload` (`make_dem_image`, `compute_raw_dem`)
- OpenTopography key, datasets, request: `map2stl/geo2stl/opentopo.py::fetch_opentopo_dem` (`get_api_key`, `set_api_key`, `request_geotiff`)
- Disk cache (root `$MAP2STL_CACHE`, keys, TTLs, atomic writes, OneDrive placeholders read as a miss): `map2stl/geo2stl/cache.py::make_cache_key` (`NAMESPACE_TTL`, `_atomic_write`, `_is_cloud_placeholder`, `write_json_cache`, `read_json_cache`); app pruning `map2stl/app/server/core/cache.py::prune_cache` (`clear_bbox_cache`)
- Local tile store (is `local` usable, folder picker): `map2stl/app/server/core/tile_store.py::status` (`set_path`, `pick_folder`); routes `map2stl/app/server/routers/auth.py` (`get_tile_store`, `save_tile_store`, `browse_tile_store`)
- Tile discovery, crop, stitch: `map2stl/geo2stl/tiles.py::get_tile_files` (`stitch_tiles_no_rasterio`)
- Satellite (ESRI tiles): `map2stl/geo2stl/imagery.py::fetch_rgb` (`choose_zoom`, `fetch_tile`, `stitch_tiles`); app JPEG `map2stl/geo2stl/sat2stl.py::fetch_satellite_tiles`; Earth Engine `map2stl/geo2stl/sat2stl.py::initialize_earth_engine` (`fetch_bbox_image`, `fetch_water_mask`)
- Projections: dispatch `map2stl/geo2stl/projections.py::project_coordinates`; grids `map2stl/geo2stl/projections.py` (`project_grid`, `project_water_arrays`, `project_rgb_image`); checks `map2stl/geo2stl/projections.py::verify_layer_alignment` (`expected_aspect_ratio`) — [reference/projections.md](reference/projections.md)
- Layer processing and blend modes (`add` / `rivers` = raise / cut): `map2stl/geo2stl/processing.py::blend_layers` (`ProcessingSpec`, `apply_layer_processing`)
- Rivers and lakes as terrain-relative layers (F-REGION): `map2stl/geo2stl/water_layers.py::register_water_layer_sources`
  - Hydraulic geometry from discharge / order: `map2stl/geo2stl/water_layers.py::river_size_m` (`order_discharge`)
  - Valley snapping (least-cost path in a corridor): `map2stl/geo2stl/water_layers.py::snap_reaches_to_valley` (`snap_radius_m`)
  - River depth raster, thin-channel-safe resize: `map2stl/geo2stl/water_layers.py::rasterize_river_depth` (`resize_relative`)
  - Lakes flat at shore minimum minus depth: `map2stl/geo2stl/water_layers.py::lake_depth_grid` (`make_lakes_source`)
- HydroRIVERS / Natural Earth: `map2stl/geo2stl/hydrology.py::fetch_hydrorivers` (`rasterize_hydrorivers_orders`, `order_depth_grid`, `rasterize_hydrorivers`, `water_surface_mask`, `union_water_surface`, `fetch_natural_earth_rivers`, `HydrologyService`); route cache `map2stl/app/server/routers/terrain.py::_hydrorivers_layers` (`_hydrorivers_payload`)
- Trails (OSM ski + hiking, USFS), difficulty grid, mirror failover (`TrailsUpstreamError`), cache: `map2stl/geo2stl/trails.py::TrailsService` (`OsmTrailsLayer`, `UsfsTrailsLayer`, `rasterize_trails`, `_burn_difficulty`, `trails_cache_key`)
- Place search (Nominatim, ≤ 1 req/s): `map2stl/geo2stl/geocode.py::search_places`; landmarks near a box edge `map2stl/geo2stl/landmarks.py::edge_landmarks` (`fetch_edge_features`, `edge_proximity`); routes `map2stl/app/server/routers/geocode.py` (`geocode_search`, `edge_landmarks`)
- Dead code: `map2stl/geo2stl/write.py::savefile` (flips rows the live export does not)

### Server composite and layers

- Composite request (ordered layer list, failing non-base layers skipped with warnings, `RETRY_SKIPPED_S`, `COMPOSITE_CACHE_VERSION`): `map2stl/app/server/routers/composite.py::compute_composite_dem` (`merge_dem_layers`, `merge_hydrology`)
- Rivers/lakes carve kept apart (`split_carve`) and added after the export's median filter: `map2stl/app/server/routers/composite.py::compute_composite_dem`, `map2stl/app/server/core/export_params.py::mesh_composite_layers`
- City and water layer sources for geo2stl: `map2stl/app/server/routers/composite.py::register_city_layer_sources` (`register_water_sources`)
- River controls (one source for preview and print: source, min order, Width ×): `map2stl/app/client/static/js/modules/layers/hydrology-print.js::readHydrologyRiverControls` (`hydrologyPrintQuery`); composite sync `map2stl/app/client/static/js/modules/layers/composite-dem.js::_syncRiverParamsFromHydrology`
- Client spec and panel: `map2stl/app/client/static/js/modules/layers/composite-spec.js::buildCompositeLayerSpec` (`RIVER_SOURCES`); `map2stl/app/client/static/js/modules/layers/composite-dem.js::applyCompositeToDem`; `map2stl/app/client/static/js/vue/components/dem/CompositeDemSection.vue`
- Design and status: [reference/composite-dem-design.md](reference/composite-dem-design.md), [decisions/composite.md](decisions/composite.md)

### Regions, settings, DEM request (server side)

- Saved regions and settings blob: `map2stl/app/server/routers/regions.py` (`list_regions`, `get_region_settings`, `save_region_settings_route`); schema `map2stl/app/server/core/db.py::init_db`
  - The one place the retired `clip_nans` key is still read (renamed to `clip_valid_region` as saved settings load): `map2stl/app/server/routers/regions.py::_rename_legacy_clip_nans`
- Default settings (`dem_source` from `default_dem_source`): `map2stl/app/server/routers/settings.py::get_default_settings`
- DEM route (returns `dem_id`, `source_resolution`, empty-DEM warning): `map2stl/app/server/routers/terrain.py::get_terrain_dem` (`_fetch_dem_array`, `_dem_empty_warning`)
- Server settings, cache paths, limits: `map2stl/app/server/config.py` (`EE_CACHE_DIR` under `geo2stl.cache.CACHE_ROOT`, `CACHE_DIRS`, `CACHE_MAX_FILES`)
- Shared in-flight dedupe (hydrology, trails): `map2stl/app/server/core/inflight.py::dedupe`
- FastAPI app, page routes, run helper: `map2stl/app/server/server.py::app` (`guides_page`, `reports_page`, `run_server`)

### Frontend

Full map: [reference/frontend-modules.md](reference/frontend-modules.md).

- DEM load and the request snapshot export sends (`lastDemRequest`): `map2stl/app/client/static/js/modules/dem/dem-main.js::loadDEM`; source list `map2stl/app/client/static/js/modules/dem/dem-main.js::populateDemSources` (`PRIMARY_DEM_SOURCES`); sampling readout `map2stl/app/client/static/js/vue/components/dem/DemSamplingInfo.vue`
- Export requests, polling, cancel: `map2stl/app/client/static/js/modules/export/export-handlers.js::_asyncExport` (`_demSettings`, `exportCityModel`, `exportPuzzle`, `runPreflight`, `cancelExport`); stall watch `map2stl/app/client/static/js/modules/export/export-poll.js::createStallWatch`
- 3D preview, bed outline, draggable puzzle cuts: `map2stl/app/client/static/js/modules/export/model-viewer.js::previewModelIn3D` (`updateBedOutline`, `updatePuzzlePreview`, `_startCutDrag`); edge maths `map2stl/app/client/static/js/modules/export/puzzle-cuts.js`
- Print scale, bed parsing, piece count (mirror `choose_scale` / `plan_grid`): `map2stl/app/client/static/js/modules/export/print-scale.js::modelScale` (`parseBedSize`, `piecesNeeded`, `fillBedMmPerPx`)
- Optional settings sections ("More settings" switches per page): `map2stl/app/client/static/js/vue/stores/uiMode.ts::useUiModeStore`; list `map2stl/app/client/static/js/vue/components/shared/ToolSwitches.vue` (was ⚙ sheet `map2stl/app/client/static/js/vue/components/layout/SettingsSheet.vue`
- Region → Extrude loads the DEM and fills the bed (replaced ✨ Make it printable): `map2stl/app/client/static/js/modules/ui/view-management.js::_ensureDemForExtrude`
- Edit layers (what prints = what shows): `map2stl/app/client/static/js/vue/stores/editLayers.ts::EDIT_LAYERS`; panels `EditLayersPanel.vue`, `LayerProperties.vue`; write controls by id `map2stl/app/client/static/js/vue/dom-fields.ts::setField`
- Live composite in preview/export (no Apply for DEM/water/rivers/lakes): `map2stl/app/client/static/js/modules/export/export-handlers.js::_demSettings`
- New-region flow (search → suggested box, live size, map card, save selects): `map2stl/app/client/static/js/modules/map/new-region.js`, `map2stl/app/client/static/js/vue/components/views/NewRegionCard.vue`
- Region size text ("2.0 × 2.0 km") for pill, list, map card, editor: `map2stl/app/client/static/js/modules/regions/region-geometry.js::formatBboxDims`
- Settings collect/apply/auto-save: `map2stl/app/client/static/js/modules/ui/presets.js::collectAllSettings` (`applyAllSettings`, `setupAutoSave`)
  - Legacy keys in saved settings / presets renamed before apply (`projection.clip_nans` → `clip_valid_region`): `map2stl/app/client/static/js/modules/ui/settings-compat.js::normalizeSettingsKeys`
- Workflow presets City / Mountain / Region / Coast: `map2stl/app/client/static/js/modules/ui/workflow-presets.js::applyWorkflowPreset` (`WORKFLOW_PRESETS`, `regionDemSource`)
- Layer stack, render order, auto-fetch on show (`LAYER_AUTOLOAD`), graticule: `map2stl/app/client/static/js/modules/layers/stacked-layers.js::LAYER_STACK` (`LAYER_AUTOLOAD`, `getLayerOrder`, `moveLayer`, `drawLayerGrid`); rack `map2stl/app/client/static/js/vue/components/dem/LayerViewSection.vue`; per-layer view controls `map2stl/app/client/static/js/vue/components/dem/LayerDisplaySections.vue`
- Building heights panel (sources, histogram, overrides): `map2stl/app/client/static/js/modules/layers/building-heights.js::summarizeBuildingHeights` (`buildingsWithOverrides`); `map2stl/app/client/static/js/vue/components/dem/CityBuildingsPanel.vue`
- Bounding box (the one writer): `map2stl/app/client/static/js/modules/map/bbox-panel.js::setBboxRectangle`
- Terrain overlay on the Leaflet map (resampled to Mercator): `map2stl/app/client/static/js/modules/map/map-globe.js::buildGlobalDemOverlay` (`ensureDemOverlayPane`, `toggleTerrainOverlay`)
- Views and DEM subtabs: `map2stl/app/client/static/js/modules/ui/view-management.js::switchView` (`setupDemSubtabs`, `deleteRegion`); region boxes on the map + the shared viewport set `map2stl/app/client/static/js/modules/regions/region-boxes.js::refreshRegionViewSet` (rule `map2stl/app/client/static/js/modules/regions/viewport-regions.js::selectViewportRegions`, styles/size `map2stl/app/client/static/js/modules/regions/region-geometry.js::regionBoxStyle`); region editor (rename, bounds, delete, notes) `map2stl/app/client/static/js/modules/regions/region-editor.js::openRegionEditor`; rename route `map2stl/app/server/routers/regions.py::update_region`; Explore "Load DEM ›" `map2stl/app/client/static/js/modules/ui/view-management.js::loadSelectedRegionDem`; continent of a point `map2stl/app/client/static/js/modules/regions/continent.js::detectContinent`
- Error messages from FastAPI `detail`: `map2stl/app/client/static/js/modules/core/api.js` (`_describeFailure`); HTML escaping `map2stl/app/client/static/js/modules/core/ui-helpers.js::escapeHtml`
- Place search, POI-near-edge warnings: `map2stl/app/client/static/js/vue/components/views/LandmarkSearch.vue`, `map2stl/app/client/static/js/vue/components/views/EdgeLandmarkWarnings.vue`, `map2stl/app/client/static/js/modules/map/landmarks.js`
- Sidebar mode (`window.setSidebarMode`) and region editor visibility (`window.setRegionEditorOpen`): `map2stl/app/client/static/js/vue/components/sidebar/SidebarPanel.vue`
- Pinia store and the `window.appState` bridge: `map2stl/app/client/static/js/vue/stores/app.ts::useAppStore`, `map2stl/app/client/static/js/vue/main-vue.ts::installAppStateBridge`

### Guides page (/guides) and reports (/reports)

- Guides: sources `map2stl/docs/guides/*.md` (+ `img/`); render, URL rewrite, step ids, routes `map2stl/app/server/routers/guides.py::load_guide` (`_rewrite_url`, `_GuideTreeprocessor`, `guide_path`, `guides_index`, `guide_detail`, `guide_image`); page `map2stl/app/client/templates/guides.html`, `map2stl/app/client/static/js/guides.js`; deep links `map2stl/app/client/static/js/modules/ui/guide-links.js`; tests `map2stl/tests/test_guides_router.py`
- Usage log (F-USAGE, local only): browser `map2stl/app/client/static/js/modules/core/usage-log.js`; route `map2stl/app/server/routers/usage.py::post_usage` (`usage_status`, `_scrub`); log files `map2stl/output/usage/*.jsonl`; flow report `Code/claude/scripts/usage_report.py`; tests `map2stl/tests/test_usage_router.py`
- Reports: inventory, registration reports + packs (`MAP2STL_REGISTRATION_REPORT_ROOTS`), traversal guard `map2stl/app/server/routers/reports.py::reports_registration` (`_default_registration_roots`, `_scan_report_root`, `_pack_entry`, `_safe_path`); page `map2stl/app/client/static/js/reports.js` (`renderRegistration`); skyline row parser `map2stl/city2stl/skyline/report_index.py`

### Python SDK

- `TerrainSession` (drives the server over HTTP): `map2stl/app/session/terrain_session.py::TerrainSession` — method ↔ route map in [reference/sdk.md](reference/sdk.md)
  - City export with optional roof classification, local tag edits sent along: `TerrainSession.export_city_model`, `TerrainSession.classify_roof_shapes`, `TerrainSession.fetch_building_heights`

### numpy2stl — meshes (geo-free)

Package overview: `numpy2stl/README.md`. Geo-free is enforced by `numpy2stl/tests/test_geo_free.py`.

- Heightmap → mesh: `numpy2stl/src/numpy2stl/core/generate.py::array_to_mesh` (`array2faces`, `polygon_to_prism`, `perimeter_to_walls`)
- `Solid` container, normals, validation, open edges: `numpy2stl/src/numpy2stl/core/solid.py::Solid` (`calculate_normals`, `validate_object`, `get_open_edges`, `simplify_object_3D`)
- Polygon helpers (perimeters, area/orientation, triangulation): `numpy2stl/src/numpy2stl/core/polygon.py::triangulate_polygon` (`get_ordered_perimeter`, `get_area`, `set_orientation`, `rotation_matrix_from_vertices`)
- Extrusion (prisms, sloped tops, robust triangulation with holes): `numpy2stl/src/numpy2stl/processing/extrusion.py::make_prism_solid` (`extrude_solid_polygon`, `make_sloped_prism_solid`, `robust_triangulate`); vectorised prisms `numpy2stl/src/numpy2stl/core/extrude.py::prisms`
- Booleans and manifold puzzle cutting: `numpy2stl/src/numpy2stl/processing/boolean.py::union` (`cut_puzzle_pieces_manifold`, `clean_mesh`)
- Heightfield TIN (+ face-budget variant for the preview), Hausdorff decimation: `numpy2stl/src/numpy2stl/processing/decimate.py::heightfield_tin` (tiled above `TIN_TILE_PX`; `heightfield_tin_budget`, `decimate_to_tolerance`)
- Lossless coplanar merge (used by `lossless_simplify`): `numpy2stl/src/numpy2stl/processing/simplify.py::simplify_mesh_surfaces` (`_triangulate_regions`)
- Building simplification and prism LOD: `numpy2stl/src/numpy2stl/processing/building_simplify/decimate.py::simplify_building_mesh` (`decimation_sweep`, `flatten_roof_clutter`); `numpy2stl/src/numpy2stl/processing/building_simplify/prism.py::prism_decompose`
- Mesh checks: `numpy2stl/src/numpy2stl/processing/verify.py::check_model_status` (`diagnose_mesh`)
- I/O: `numpy2stl/src/numpy2stl/io/writers.py::writeSTL` (`write3MF`, `writeOBJ`); `numpy2stl/src/numpy2stl/io/readers.py::load_mesh`
- Jigsaw puzzle (knob shapes, heightfield pieces without booleans, underside engraving, plate layout): `numpy2stl/src/numpy2stl/applications/puzzle.py::heightfield_pieces` (`make_jigsaw_cutters`, `jigsaw_outlines`, `engrave_underside`, `plate_layout`)
- STL → raster (the one implementation, `method="bin"|"raycast"`, `row0`): `numpy2stl/src/numpy2stl/stl2numpy/heightmap.py::mesh_to_heightmap`; voxels, slices, point clouds, analysis in `numpy2stl/src/numpy2stl/stl2numpy/`
- Raster helpers: residual / building mask / split `numpy2stl/src/numpy2stl/raster/segment.py::building_mask` (`terrain_residual`, `split_touching_buildings`); vectorise `numpy2stl/src/numpy2stl/raster/vectorize.py::vectorize_buildings`; the one rasteriser `numpy2stl/src/numpy2stl/raster/burn.py::burn_polygons`; NaN fill `numpy2stl/src/numpy2stl/raster/fill.py::fill_nan`

### numpy2stl — registration

Design: `numpy2stl/src/numpy2stl/registration/docs/ARCHITECTURE.md`; usage: `numpy2stl/docs/registration.md`.

- Pipeline entry and output dir: `numpy2stl/src/numpy2stl/registration/pipeline.py::register_city_stl` (`_default_out_dir`)
- Config and result types: `numpy2stl/src/numpy2stl/registration/config.py::RegistrationConfig`; `numpy2stl/src/numpy2stl/registration/types.py::RegistrationResult`
- Reference-source protocol (OSM data comes in through it): `numpy2stl/src/numpy2stl/registration/reference.py::ReferenceSource` (`StaticReference`); centre search `numpy2stl/src/numpy2stl/registration/center_search.py::find_best_target`
- Align backends (`numpy2stl/src/numpy2stl/registration/align/`): `register.py::register`, `global_search.py::register_global`, `fourier_mellin.py::fourier_mellin_register`, `ecc.py::refine_transform`, `scale.py::estimate_scale`, `lines.py::rotation_from_angle_histograms`, `metrics.py::score_alignment`, `polygon_register.py::register_polygons`, `polygon_icp.py::refine_registration_polygons`
- Stages, lock gate, landmark check: `numpy2stl/src/numpy2stl/registration/stages/_common.py::_is_locked_registration` (`_sweep_sharpness`, `_landmark_check`, `_inpaint_stl_nan`)
- Comparison and report: `numpy2stl/src/numpy2stl/registration/compare.py::compare`; `numpy2stl/src/numpy2stl/registration/html_report.py::write_registration_report`

### Registration — plates (map2stl side)

Why: [decisions/registration-plate-location.md](decisions/registration-plate-location.md), [registration-refinement.md](decisions/registration-refinement.md), [registration-validation.md](decisions/registration-validation.md). Plan: [plans/active/registration-learning-plan.md](plans/active/registration-learning-plan.md).

- OSM wrapper round numpy2stl: `map2stl/city2stl/registration/__init__.py::register_city_stl` (`OSMReference`)
- OSM rasters for registration (3.5 m/level, area-coverage burn, cache): `map2stl/city2stl/osm_raster.py::get_osm_building_heightmap` (`get_osm_semantic_masks`, `_rasterize_buildings`, `derive_scale_m_per_unit`, `get_city_bbox`)
- CLIs: `map2stl/city2stl/registration/scripts/run_registration.py::main`, `benchmark_micropolitan.py`, `robustness_test.py`
- Street-scale placement (footprint, water, terrain channels; mesa plate; parks don't-care): `map2stl/city2stl/registration/street_place.py::place_plate` (`built_height`, `mesa_additions`, `built_mask`, `map_buildings`, `map_parks`, `density_search`, `refine`, `agreement`)
- OSM coverage rasters and tag classes: `map2stl/city2stl/registration/osm_model.py::coverage` (`building_class`, `STANDING`, `road_width`)
- OSM water (sea by coastline voting, widths, direct Overpass): `map2stl/city2stl/registration/osm_water.py::osm_water` (`_coast_layer`, `_tag_width_m`, `_overpass`)
- NCC, peaks, height channel: `map2stl/city2stl/registration/correlate.py::ncc_surface` (`find_peaks`, `height_channel`)
- Tile consensus verdict: `map2stl/city2stl/registration/consensus.py::crop_consensus` (`verdict`, `correct`, `score_export`, `record_verdict`)
- Pack and cache locations (`ALIGN_DATA_DIR`): `map2stl/city2stl/registration/align_paths.py::data_dir`
- Model critic (per-building error, IoU, roof shape; references): `map2stl/city2stl/registration/critic.py::score_model` (`score_heights`, `pack_reference`, `ndsm_reference`, `stl_raster`) — why not learned: [research/plate-critic.md](research/plate-critic.md)
- App: plate panel `map2stl/app/server/core/plate_registration.py::run_plate_registration` (`list_packs`, `pack_for_library_path`, `placement_geometry`); routes `map2stl/app/server/routers/registration.py::critic_score`; mesh auto-register `map2stl/app/server/core/mesh_import.py::auto_register` (`comparison_scores`); panels `PlateRegistrationSection.vue`, `ModelScorePanel.vue`, `MeshImportSection.vue`

### Align tool (`map2stl/tools/align_tool/`)

Manual drag-align, ground truth and batch export. Refinement: [reference/align-refinement.md](reference/align-refinement.md).

- Drag UI and server: `map2stl/tools/align_tool/drag_align.html`; `map2stl/tools/align_tool/server.py::AlignHandler` (`ROUTES`, `api_save`, `api_refine`, `api_refetch`)
- Pack export (plate at three stages, semantic exclusion after refinement, row 0 = south): `map2stl/tools/align_tool/export_align_data.py::main` (`CITIES`, `SLUGS`, `PACK_ROOTS`, `plate_building_heights`, `semantic_exclusion`)
- Opening-transform refinement: `map2stl/tools/align_tool/refine_guess.py::refine_arrays` (`solve_span`, `span_is_measured`, `apply_to_disk`)
- Evaluation against ground truth: `map2stl/tools/align_tool/eval_registration.py::evaluate_city` (`truth_in_export_frame`, `truth_is_unverified`, `segmentation_scores`)
- Water locate (span sweep, confidence gate, terrain cross-check): `map2stl/tools/align_tool/locate.py::resolve_center` (`solve_center`, `seed_center`, `MIN_PEAK_Z`, `measured_rotation_deg`, `validate`)
- Building locate (no water: normalised crops that vote): `map2stl/tools/align_tool/locate_buildings.py::solve_plate` (`vote`, `fit_rotation`, `ROTATIONS`, `agree_px_for`, `coarse_cell_for`)
- Batch without ground truth: `map2stl/tools/align_tool/auto_register.py::register` (`discover_packs`, `adopt`)
- Plate as vectors / terrain + buildings: `map2stl/tools/align_tool/plate_vectors.py::heightmap_to_polygons` (`polygons_to_heightmap`, `plate_to_model`, `model_to_heightmap`)
- Ground truth on disk: `map2stl/tools/align_tool/ground_truth/`; surveyed pack example `map2stl/tools/align_tool/data/san_juan_puerto_rico/`

### Skyline (street-view building heights)

Everything is in `map2stl/city2stl/skyline/README.md` (overview, pipeline shape, where things live, dead ends, feature status, open items). Entry points:
- CV/geometry primitives: `map2stl/city2stl/skyline/_core/` (types, segmentation, projection, skyline, pano, registration, height). There is no façade: every caller imports the defining `_core/` or `_pano/` module.
- Per-view heights and aggregation: `map2stl/city2stl/skyline/_core/height.py::estimate_heights_from_registration` (`aggregate_building_heights`, `_ground_elev_m`)
- View registration: `map2stl/city2stl/skyline/_pano/detect.py::_register_views`
- Depth cross-check: `map2stl/city2stl/skyline/depth_estimation.py::calibrate_pano_depth` (`depth_height_from_segment`, `compare_heights`)
- Region data (bbox from the regions table or `sites/<region>.json`, OSM → records, terrain): `map2stl/city2stl/skyline/region_data.py::_osm_to_building_records` (`_load_region_bbox`, `_attach_building_terrain`)
- Region PDF: `map2stl/city2stl/skyline/scripts/08_region_skyline_pdf.py`

### Entry points, scripts, tests

- Launchers: `Start 3D Maps.bat`, `Stop 3D Maps.bat` (workspace root) → `map2stl/scripts/start.ps1`, `map2stl/scripts/stop.ps1`
- Venv: `map2stl/scripts/setup-venv.ps1` (creates `~/.venvs/map2stl`); git database link `map2stl/scripts/link-gitdir.ps1`
- Tests: `map2stl/pytest.ini` also collects `numpy2stl/tests`; `-m integration`, `-m slow` and `-m ml` (torch) are opt-in; `tests/e2e/` (playwright) and `tests/manual/` are never collected; `-n 6` runs in parallel (pytest-xdist)
- Pre-push hooks: `map2stl/.githooks/pre-push` (pytest `-n 6`, vitest, eslint), `numpy2stl/.githooks/pre-push` (numpy2stl's own tests); `core.hooksPath` set by `map2stl/scripts/setup-venv.ps1` and `numpy2stl/scripts/link-gitdir.ps1`
- Test fixtures from `numpy2stl/tests/conftest.py` apply to *every* test when collected from map2stl (pytest scopes conftests outside the rootdir globally): prefix benchmark fixtures `bench_`, and don't request `monkeypatch` in its autouse fixtures
- Helper scripts for agents (renders, screenshots, doc link checker): `claude/scripts/README.md`
