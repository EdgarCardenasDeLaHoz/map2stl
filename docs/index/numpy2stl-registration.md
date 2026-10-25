# numpy2stl, registration and align tool

Locator for numpy2stl meshes, plate registration (library and map2stl side) and the align tool. Back to the [index](../INDEX.md); paths are relative to `Code/`.

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
- Jigsaw puzzle (knob shapes, heightfield pieces without booleans, underside engraving, plate layout): `numpy2stl/src/numpy2stl/applications/puzzle.py::heightfield_pieces` (`make_jigsaw_cutters`, `jigsaw_outlines`, `chamfered_prism`, `fit_test_pieces`, `engrave_underside`, `plate_layout`)
- Models as named parts (3MF objects, pack files, the viewer's parts file; stdlib 3MF reader): `numpy2stl/src/numpy2stl/io/parts.py::load_parts` (`part_role`, `write_parts_file`, `read_parts_file`), `numpy2stl/src/numpy2stl/io/readers.py::read3MF`
- Ground (DTM) under a surface heightmap (F-STL2NUMPY): `numpy2stl/src/numpy2stl/raster/terrain.py::estimate_dtm` (`ground_mask_steps`, `ground_mask_pmf`, `neighbour_edges`, `estimate_terrain`); building table: `numpy2stl/src/numpy2stl/stl2numpy/buildings.py::building_table`; exact fast surface grid: `mesh_to_heightmap(method="zbuffer")`; scored on our own City Models by `claude/scripts/dtm_eval.py` (fixtures: `claude/scripts/stl2numpy_fixtures.py`)
- STL → raster (the one implementation, `method="bin"|"raycast"`, `row0`): `numpy2stl/src/numpy2stl/stl2numpy/heightmap.py::mesh_to_heightmap`; voxels, slices, point clouds, analysis in `numpy2stl/src/numpy2stl/stl2numpy/`
- Raster helpers: residual / building mask / split `numpy2stl/src/numpy2stl/raster/segment.py::building_mask` (`terrain_residual`, `split_touching_buildings`); vectorise `numpy2stl/src/numpy2stl/raster/vectorize.py::vectorize_buildings`; the one rasteriser `numpy2stl/src/numpy2stl/raster/burn.py::burn_polygons` (shapely to GeoJSON mappings: `_ragged_mappings`); NaN fill `numpy2stl/src/numpy2stl/raster/fill.py::fill_nan`

### numpy2stl — registration

Design: `numpy2stl/src/numpy2stl/registration/docs/ARCHITECTURE.md`; usage: `numpy2stl/docs/registration.md`.

- Pipeline entry and output dir: `numpy2stl/src/numpy2stl/registration/pipeline.py::register_city_stl` (`_default_out_dir`)
- Config and result types: `numpy2stl/src/numpy2stl/registration/config.py::RegistrationConfig`; `numpy2stl/src/numpy2stl/registration/types.py::RegistrationResult`
- Reference-source protocol (OSM data comes in through it): `numpy2stl/src/numpy2stl/registration/reference.py::ReferenceSource` (`StaticReference`); centre search `numpy2stl/src/numpy2stl/registration/center_search.py::find_best_target`
- Align backends (`numpy2stl/src/numpy2stl/registration/align/`): `register.py::register`, `global_search.py::register_global`, `fourier_mellin.py::fourier_mellin_register`, `ecc.py::refine_transform`, `scale.py::estimate_scale`, `lines.py::rotation_from_angle_histograms`, `metrics.py::score_alignment`, `polygon_register.py::register_polygons`, `polygon_icp.py::refine_registration_polygons`
- Stages, lock gate, landmark check: `numpy2stl/src/numpy2stl/registration/stages/_common.py::_is_locked_registration` (`_sweep_sharpness`, `_landmark_check`, `_inpaint_stl_nan`)
- Comparison and report: `numpy2stl/src/numpy2stl/registration/compare.py::compare`; `numpy2stl/src/numpy2stl/registration/html_report.py::write_registration_report`

### Registration — plates (map2stl side)

Why: [decisions/registration-plate-location.md](../decisions/registration-plate-location.md), [registration-refinement.md](../decisions/registration-refinement.md), [registration-validation.md](../decisions/registration-validation.md). Plan: [plans/active/registration-learning-plan.md](../plans/active/registration-learning-plan.md).

- OSM wrapper round numpy2stl: `map2stl/city2stl/registration/__init__.py::register_city_stl` (`OSMReference`)
- OSM rasters for registration (3.5 m/level, area-coverage burn, cache): `map2stl/city2stl/osm_raster.py::get_osm_building_heightmap` (`get_osm_semantic_masks`, `_rasterize_buildings`, `derive_scale_m_per_unit`, `get_city_bbox`)
- CLIs: `map2stl/city2stl/registration/scripts/run_registration.py::main`, `benchmark_micropolitan.py`, `robustness_test.py`
- Street-scale placement (footprint, water, terrain channels; mesa plate; parks don't-care): `map2stl/city2stl/registration/street_place.py::place_plate` (`built_height`, `mesa_additions`, `built_mask`, `map_buildings`, `map_parks`, `density_search`, `refine`, `agreement`)
- OSM coverage rasters and tag classes: `map2stl/city2stl/registration/osm_model.py::coverage` (`building_class`, `STANDING`, `road_width`)
- OSM water (sea by coastline voting, widths, direct Overpass): `map2stl/city2stl/registration/osm_water.py::osm_water` (`_coast_layer`, `_tag_width_m`, `_overpass`)
- NCC, peaks, height channel: `map2stl/city2stl/registration/correlate.py::ncc_surface` (`find_peaks`, `height_channel`)
- Tile consensus verdict: `map2stl/city2stl/registration/consensus.py::crop_consensus` (`verdict`, `correct`, `score_export`, `record_verdict`)
- Pack and cache locations (`ALIGN_DATA_DIR`): `map2stl/city2stl/registration/align_paths.py::data_dir`
- Model critic (per-building error, IoU, roof shape; references): `map2stl/city2stl/registration/critic.py::score_model` (`score_heights`, `pack_reference`, `ndsm_reference`, `stl_raster`) — why not learned: [research/plate-critic.md](../research/plate-critic.md)
- App: plate panel `map2stl/app/server/core/plate_registration.py::run_plate_registration` (`list_packs`, `pack_for_library_path`, `placement_geometry`); routes `map2stl/app/server/routers/registration.py::critic_score`; mesh auto-register `map2stl/app/server/core/mesh_import.py::auto_register` (`comparison_scores`); panels `PlateRegistrationSection.vue`, `ModelScorePanel.vue`, `MeshImportSection.vue`

### Align tool (`map2stl/tools/align_tool/`)

Manual drag-align, ground truth and batch export. Refinement: [reference/align-refinement.md](../reference/align-refinement.md).

- Drag UI and server: `map2stl/tools/align_tool/drag_align.html`; `map2stl/tools/align_tool/server.py::AlignHandler` (`ROUTES`, `api_save`, `api_refine`, `api_refetch`)
- Pack export (plate at three stages, semantic exclusion after refinement, row 0 = south): `map2stl/tools/align_tool/export_align_data.py::main` (`CITIES`, `SLUGS`, `PACK_ROOTS`, `plate_building_heights`, `semantic_exclusion`)
- Opening-transform refinement: `map2stl/tools/align_tool/refine_guess.py::refine_arrays` (`solve_span`, `span_is_measured`, `apply_to_disk`)
- Evaluation against ground truth: `map2stl/tools/align_tool/eval_registration.py::evaluate_city` (`truth_in_export_frame`, `truth_is_unverified`, `segmentation_scores`)
- Water locate (span sweep, confidence gate, terrain cross-check): `map2stl/tools/align_tool/locate.py::resolve_center` (`solve_center`, `seed_center`, `MIN_PEAK_Z`, `measured_rotation_deg`, `validate`)
- Building locate (no water: normalised crops that vote): `map2stl/tools/align_tool/locate_buildings.py::solve_plate` (`vote`, `fit_rotation`, `ROTATIONS`, `agree_px_for`, `coarse_cell_for`)
- Batch without ground truth: `map2stl/tools/align_tool/auto_register.py::register` (`discover_packs`, `adopt`)
- Plate as vectors / terrain + buildings: `map2stl/tools/align_tool/plate_vectors.py::heightmap_to_polygons` (`polygons_to_heightmap`, `plate_to_model`, `model_to_heightmap`)
- Ground truth on disk: `map2stl/tools/align_tool/ground_truth/`; surveyed pack example `map2stl/tools/align_tool/data/san_juan_puerto_rico/`
