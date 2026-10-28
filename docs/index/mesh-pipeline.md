# Mesh pipeline and export

Locator for the two-stage mesh pipeline (terrain_stage then build_on_terrain), export entry points, tasks, puzzle, preflight and caches. Back to the [index](../INDEX.md); paths are relative to `Code/`.

### The mesh pipeline (two stages)

Every mesh export is `terrain_stage` (raster → heightfield) then `build_on_terrain` (vectors on it).
Walkthrough: [reference/pipeline.md](../reference/pipeline.md); why: [decisions/mesh-pipeline.md](../decisions/mesh-pipeline.md).

- Stage 1, DEM → `TerrainField` (mm, row 0 north): `map2stl/app/server/core/export.py::terrain_stage` (`TerrainField`, `_prepare_dem_array`; rivers in print mm: `RIVER_MIN_WIDTH_MM`, `WATER_SEA`)
- Stage 2, city layers on the terrain → `CityModel`: `map2stl/city2stl/city_model.py::build_on_terrain`
- Vector rivers (reaches cut as solids, not the raster carve; request `vector_rivers` "auto" / true / false): stages for every mesh export `map2stl/app/server/core/export.py::mesh_terrain_stage` (reaches and their print depth `river_features`, header `_vector_rivers_headers`); flag and "auto" rule `map2stl/app/server/core/export_params.py::resolve_vector_rivers` (`VECTOR_RIVERS_AUTO_MIN_SIDE_KM`, projection check `vector_rivers_blocker`); lake-only carve `map2stl/app/server/routers/composite.py::compute_composite_dem` (`water_out["lake_carve"]`); the `rivers` layer `map2stl/city2stl/city_model.py::DEFAULT_LAYERS` (round caps `ROUND_QUAD_SEGS`, per-feature depth `depth_classes`, threaded tiled union `union_polygons`); depth rule `map2stl/geo2stl/water_layers.py::reach_print_depth_mm` (`min_carve_depth_m`, `reach_carve_depth_m`, `drop_shallow_reaches`) — [decisions/osm-water-hydrology.md](../decisions/osm-water-hydrology.md)
  - Terrain-only export = the city model with no layers; old entry `build_city_model` wraps it.
- Export entry points: `map2stl/app/server/core/export.py` (`generate_stl`, `generate_obj`, `generate_3mf`, `_prepare_export_mesh`, `_repair_mesh`, `_write_mesh`)
- Request → `ExportContext`, DEM resolution: `map2stl/app/server/core/export_params.py` (`ExportContext`, `resolve_dem`, `mesh_composite_layers`)
- DEM handoff: `dem_id` handles `map2stl/app/server/core/dem_store.py::DemStore`; the one cache key `map2stl/app/server/core/dem_cache.py::dem_cache_key` (`DEM_CACHE_SCHEMA_VERSION`)
- Background tasks and download: `map2stl/app/server/core/export_tasks.py` (`start_export_task`, `get_task_status`, `get_task_file`), on the shared job runner `map2stl/app/server/core/jobs.py::JobRegistry` (`Job`, `find`; any-kind status `GET /api/jobs/{id}` in `map2stl/app/server/routers/jobs.py`) — [why](../decisions/architecture.md)
- City export task (layers, trails, landmark overrides): `map2stl/app/server/core/city_model_task.py::run_city_model` (`normalize_request`, `city_osm_params`, `wants_ski_trails` = trails on by default where downhill runs exist)
- Panorama (skyline report) heights into a city export, opt-in `panorama_heights`: `map2stl/city2stl/panorama_heights.py::apply_panorama_heights` — [why](../decisions/building-heights.md)
- OSM city layers served from an outdated cache when Overpass is down: `map2stl/app/server/core/city_data.py::_serve_stale_or_raise` (`_stale_payload`)
- Skip the startup cache prune and OSM migration (env `MAP2STL_NO_CACHE_MAINTENANCE=1`; launch config `map2stl-nocache`): `map2stl/app/server/server.py::_startup_cache_maintenance`
- Where the skyline single-use caches live (Street View, satellite, Commons, dated benchmark runs; env `SKYLINE_LOCAL_DIR`; stage cache `SKYLINE_STAGE_CACHE_DIR`; both outside OneDrive since 2026-10-18): `map2stl/city2stl/skyline/local_dirs.py::local_dir`
- Live 3D preview (TIN capped at `PREVIEW_MAX_FACES`, strided DEMs): `map2stl/app/server/core/export.py::generate_mesh_preview` (`_preview_mesh`)
- Terrain puzzle: `map2stl/app/server/core/export.py::generate_puzzle` (`puzzle_spec`); cutting, engraving, plates `map2stl/app/server/core/puzzle.py` (`plan_grid`, `choose_method`, `knob_size`, `cut_pieces`, `cut_to_zip`, `volume_check`)
- Puzzle fit for FDM (clearance 0.45, corner radius, bottom chamfer) and the fit-test coupon: `map2stl/app/server/core/puzzle.py::fit_test_mesh` (`DEFAULT_KNOB_SHAPE` = notched, `DEFAULT_CLEARANCE_MM`, `CORNER_RADIUS_MM`, `CHAMFER_MM`), route `map2stl/app/server/core/export.py::generate_fit_test`, button `export-handlers.js::downloadFitTest`
- Reference-city regression set (Cartagena, Granada, Breckenridge: watertight, puzzle volume, faces/volume vs baseline, time log): `map2stl/tests/test_reference_cities.py::test_reference_city` (`pytest -m slow`), baseline `map2stl/tests/reference_cities_baseline.json`, runs appended to `map2stl/output/regression/reference_cities.jsonl`
- Pre-flight (size vs bed, pieces, faces, filament/time, warnings): `map2stl/app/server/core/preflight.py::preflight` (`print_estimate`, `build_check`); per-layer counts without solids `map2stl/city2stl/city_model.py::layer_preflight` (`layer_polygons`)
- Label engraving, contour lines, cross-section: `map2stl/app/server/core/export.py` (`_apply_label_engraving`, `_apply_contour_lines`, `generate_crosssection`)
