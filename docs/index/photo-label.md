# Locator: photo-label tool (`tools/photo_label/`)

Scope: the local hand-pairing and photo-registration tool (photo point to map or Google 3D
point, camera solve) and its "our model over Google 3D" overlay. Skyline heights themselves
are in [skyline.md](skyline.md); the export pipeline the overlay reuses is in
[mesh-pipeline.md](mesh-pipeline.md) and [city2stl.md](city2stl.md).

Run: `~/.venvs/map2stl/Scripts/python.exe tools/photo_label/server.py [--port 8767] [--region granada] [--osm <path>] [--dem <tif>] [--photos <dir>]`
(the flags override the default region's entry in `regions.json`),
then open `http://localhost:<port>/`. Endpoint list: the module docstring of `server.py`.

- **Regions (one server, several regions):** `tools/photo_label/regions.json` (label, osm, dem, photos
  key -> folder, optional view {lat, lon, ground_m}); `regions.py::Regions` (lazy `Config` per region,
  shared key and tile session, `get`, `listing`, `tiles`), `load_regions`, `build_regions` (CLI
  overrides), `UnknownRegion` (400). Region-specific GETs take `?region=`, POSTs `"region"`; an empty
  photo key adds no path prefix (Granada labels keep their names). Multi-root photos:
  `_photo_files`, `photo_rel`, `list_photos`, `safe_photo`. Page: `tools/photo_label/static/photos.js::switchRegion`,
  `clearRegion`, `api`, `postJson`, `homeMap`, `centre3d`, `disposeModel`; region kept in `#region=<id>`.
- **Saved camera:** `api_save_labels` stores `camera` + `camera_source` in the label file; page
  `saveLabels` (also after Solve / Auto register), restore in `tools/photo_label/static/photos.js::loadPhoto`.
- **Map lines toggle** (fov wedge, sight ray per pair, cyan auto rays, buildings in view recoloured
  to match the photo): `tools/photo_label/static/map2d.js::drawMapLines`, `fpBase`, checkbox `rayChk`.
- **Buildings projected onto the photo:** `terrain_solve.py::project_buildings` (`MAX_VISIBLE`
  nearest in view, `MIN_DIST_M`, `MIN_VISIBLE_PX`).
- **Mask heights (auto-correct loop, plan F):** `tools/photo_label/mask_heights.py` - per frame `analyse_frame` (`pose_gate` bad_pose check, `column_top` mask top edge, `classify` ok / too_short / too_tall, `unmatched_blobs`, `column_height` elevation angle x `ray_ring_dist`); across frames `aggregate` / `agree_group` (2+ views within ln 1.25), `find_reassignments`; output `to_readings` (method `photo_mask`), `merge_production` (backup + merge), `score`, `google3d_yardstick` (TODO). Runner `claude/scripts/run_mask_heights.py`; tests `tests/test_photo_label_mask_heights.py`.
- **Server, routes, config:** `tools/photo_label/server.py` — `LabelHandler.do_GET` / `do_POST`
  (route table), `make_server`, `main`, and re-exports of the names `app/server/core/photo_solve.py`
  and `claude/scripts` import. The work lives in sibling modules (split 2026-11-01):
  `paths.py` (photo and side-file paths: `photo_rel`, `safe_photo`, `label_path`, `depth_path`, `flags_path`),
  `footprints.py` (`overpass_to_geojson` with building multipolygon relations stitched by `_stitch_rings`,
  ids `r<relation>`; `geojson_footprints`, `tag_sources`, `load_heights`, `apply_heights`),
  `regions.py` (`Config`: paths, cached DEM and footprints, model build state, screen and depth job
  state; `Config.glb_path`), `photos.py` (`list_photos`, flags, `api_get_labels`, `api_save_labels`),
  `seeds.py` (`pano_seed`, `PANO_CAM_HEIGHT_M` — the server's copy is the source; `index.html` mirrors it),
  `solve.py` (`api_solve`, `api_project`, `api_register_candidates`, `api_register_step`, `start_view`:
  the region's `view`, else the DEM centre), `screening.py`, `depth.py`, `tiles.py` (pooled client, tile
  cache), `modelapi.py` (`api_model_info`), `latlon.py` (`dist_m`, `bearing_deg`, flat-earth).
- **Per-building heights (plan F-PHOTOPANO step 6):** a region's `heights` key in `regions.json`
  (Cartagena: `runs/cartagena/03_seeds`) -> `regions.py::Config.heights_path`; `load_heights` reads every
  `seed_*/heights.json` (`height_m` = measured, `published_m`, `tier`, `name`, `lat`, `lon`; one entry per
  building, medians over seeds), `apply_heights` merges them onto the footprints by point-in-polygon
  (seed names `b0564` do not index the pipeline footprints; else nearest bounding box within 12 m) in
  `Config.footprints`. Footprint props: `height` (drawn: OSM tag, then published, then OSM levels, else none
  -> `terrain_solve.building_height` 9 m), `height_source` (`osm_tag` / `osm_levels` / `published` /
  `measured_withheld` / `default`), `tier`, `measured_m` / `published_m` when they differ from the drawn
  height. `measured_withheld`: measured >= `WITHHELD_RATIO` (1.5) x published and `WITHHELD_MIN_M` (10 m)
  above it. `terrain_solve.py::project_buildings` returns the source and figures per building, a dashed
  `measured_outline` for withheld ones, and with `measured=True` (`/api/project` `heights: "measured"`,
  `drawn_height`) draws them at the measured height. Page: `tools/photo_label/static/map2d.js::heightText`, `fpTip`,
  `drawMeasuredOutlines`, the `#hModeSel` Heights select. Satellite readings
  (`runs/satellite/<region>/readings.json`) are not read yet (no file exists). Tests:
  `tests/test_photo_label_server.py::test_heights_merge_priority_and_withheld`,
  `test_heights_nearest_footprint_and_seed_median`, `test_region_heights_reach_served_footprints`,
  `test_project_buildings_outlines_withheld_at_measured_height`.
- **Camera solve from pairs:** `solve.py::api_solve` (terrain fit seeded by
  `city2stl/skyline/camera_solver.py::solve_pose`), `solve.py::api_project`;
  `tools/photo_label/terrain_solve.py` — `solve_terrain`, `project_buildings`, `label_priors`,
  `building_height`.
- **Google 3D tiles proxy (key stays on the server):** `server.py::LabelHandler._proxy_tiles`,
  `tiles_upstream_url`, `rewrite_tile_uris`, `load_api_key`. Do not change without reason: the
  key must never reach the browser. Speed (a view is ~1,000 tile requests): upstream keep-alive
  pool `pooled_urlopen` / `_PooledResponse` (`_idle`), local HTTP/1.1 keep-alive
  (`LabelHandler.protocol_version`), and a `[::1]` listener `loopback_v6` so `localhost` does not
  wait ~300 ms per connection for the IPv4 fallback.
- **Auto registration against Google 3D renders:** `tools/photo_label/register.py` — `match`
  (SIFT), `lift` (render depth to lat/lon/h), `estimate_geoid` (low percentile of mesh minus
  DEM), `solve_pose`, `register_step`; routes `solve.py::api_register_candidates`,
  `api_register_step`; page side `tools/photo_label/static/registration.js::autoRegister`, `renderAt`, `settleTiles`.
  When the start guess scores below `SWEEP_IF_BELOW` inliers, `tools/photo_label/static/registration.js::sweepHeadings` renders
  `SWEEP_HEADINGS` headings at `SWEEP_HFOV`, then narrows to `SWEEP_FOVS`, and iterates from the best.
  Renders are serialised by `renderLock` (`renderAt` queues, `renderNow` renders); the offscreen viewer
  `ces.rv` is kept across photos and disposed only in `clearRegion`. The 35 mm focal length from EXIF
  (`seeds.py::photo_focal_35mm`) narrows the f range in `api_register_candidates`.
- **Start-pose guesses (preloaded, shown on the map):** `solve.py::api_register_candidates` seeds the
  search with request viewpoints, the title viewpoint, photo GPS (as a viewpoint, never trusted alone)
  and footprint viewpoints; `photo_hints` (credit.json title vs region `landmarks` in `regions.json`,
  "from/desde X" = viewpoint, other match = subject, `_bearing_deg`), `_tag_candidates` (source/label),
  heading re-rank toward the subject, the title viewpoint searched alone and pinned first, cache `<stem>_guesses.json`. Street View frames take their known
  pose instead: `pano_seed` (metadata.json, heading from the file name + median `heading_offset_deg`,
  the file's real size). Panorama-pipeline frames (`runs/<region>/03_seeds/seed_<id>/` with views.json +
  pose.json; Granada `panoview` frames via `manifest.json`; roots `seeds` / `panoview` in `regions.json`)
  take `pano_seed`'s other branches (`_seed_frame_pose`, `_panoview_pose`): source `pano_anchor`, `elev_fitted`
  when pose.json has `cam_elev_m` (drone); the page treats it like `pano` (`tools/photo_label/static/registration.js::isPanoStart`: no heading
  sweep, mesh-ground height unless `elev_fitted`); `_photo_files` hides mask/seg files (`_is_seed_nonframe`) but lists the strip `pano.png`,
  `fillPhotoSel` groups seed frames in an optgroup per folder. Page: `tools/photo_label/static/guesses.js::loadGuesses`, `applyGuess`, `drawGuesses`, `searchHere`
  (drag), `clearGuesses`, checkbox `guessChk`.
- **Flag a bad photo:** `POST /api/photo/flag` (`<annotations>/<region>_photo_flags.json`), hidden by
  `list_photos` (`?all=1` lists them with `bad`), `photos.py::api_photo_flag`, `_set_flag`, `flags_path`; page `tools/photo_label/static/photos.js::flagPhoto`, button `flagBtn` with Undo; checkbox `flagChk` "Show flagged" lists them marked ⚑
  (`loadPhotoList` with `?all=1`, `fillPhotoSel`, `photoOpt`, `syncFlagBtn`: the button becomes Unflag).
- **Pano strip as a photo (`03_seeds/seed_N/pano.png`, 2688x540):** `tools/photo_label/pano_camera.py::PanoCamera`
  (cylindrical camera, same `project_ll` / `project_dirs` / `backproject` as `PinholeCamera`, `cylindrical = True`;
  heading = col0 + col*deg_per_col, col0 180 so north is mid-strip; row = horizon_row - f tan(elev)),
  `load_seed_camera` (pano.json + pose.json; `weak` for pose_branch `overhead`, seed 6),
  `fit_col0_heading` (re-fits col0 from the 12 frames by template match; Cartagena seeds 1/4/5/6 give 179.87 /
  179.78 / 180.15 / 180.07), `seed_frame_cameras` (views.json -> pinhole dicts), `strip_to_pinhole` /
  `pinhole_to_strip` / `pinhole_dirs`. Server: `paths.py::_is_seed_strip`, `_seed_strip_pose` (via `pano_seed`;
  listing adds `projection: "pano_cylindrical"`), `_camera`, `_solve_pano` (`api_solve` with
  `camera.projection`), strip branch of `api_register_step` (`body["strip"]`: resample strip to the frame, run
  `register.register_step`, map pairs back to strip px). `terrain_solve.py::project_buildings` / `_cyl_polys` /
  `_solve(pano=True)` accept either camera. Page: `tools/photo_label/static/draw.js::layoutWide` (body.widePhoto), `autoRegisterStrip`.
- **Auto screening (street-level frames, stitched panoramas):** `tools/photo_label/screen.py` —
  `screen_from_labels` (rules: ground share >= `GROUND_MAX`, aspect >= `PANORAMA_ASPECT`),
  `geometry_reason` (known pose: camera under 5 m above ground, nearest building under 50 m;
  `measure_pose` via `terrain_solve.py::project_buildings(min_dist_m=)`), `screen_photo`
  (cache `<stem>_screen.json`, SegFormer only when no earlier rule fired; a known pose
  (`screening.py::_pose_geometry`) skips only the aspect rule); calibration
  `claude/scripts/screen_metrics.py` -> `runs/screen_metrics.json`; job
  `screening.py::api_photos_screen` / `_screen_region` / `_screen_flag` (GET state `api_photos_screen_state`,
  flags through `_set_flag`; no CUDA and > `SCREEN_CPU_MAX` photos = aspect only); page `tools/photo_label/static/screening.js::screenPhotos`
  (polls from `switchRegion`, Undo unflags).
- **Footprints speed:** server `Config.footprints_bytes` (serialised once, 6 decimals, gzip); page
  draws on one canvas `fpRenderer` and keeps only those near the view on the map, `fpWindow`
  (`fpAll`, `FP_MIN_ZOOM`).
- **Geoid:** a region's measured `geoid_m` in `regions.json` (`Config.geoid_m`, `solve.py::_start_geoid`)
  is used as is (`register_step(fit_geoid=False)`); without one, `register.py::geoid_from_render` (every
  depth cell) and `autoRegister` re-renders once when the measured geoid moves > 3 m.
- **Pano camera height:** `tools/photo_label/static/registration.js::meshGround` (lowest Google mesh point within 8 m) + `PANO_CAM_HEIGHT_M`,
  applied in `autoRegister` to a `pano` start.
- **Start pose without pairs (skyline vs DEM horizon):** `tools/photo_label/auto_pose.py` —
  `candidate_poses`, `photo_skyline`, `dem_horizon`, `horizon_rows`.
- **Our city model over Google 3D** (opacity, Model/Google/Both, dz nudge, live geoid):
  `tools/photo_label/model_glb.py` — `build_or_load` (cache `runs/photo_label_model/<region>_<key>.glb`
  + `.json`), `choose_inputs` / `find_city_payload` (app OSM cache payload overlapping the DEM
  most, read only; else `footprint_layers`), `resample_dem`, `build_model`
  (`city2stl/city_model.py::build_city_model`, true scale, `cache=False`), inversion
  `model_to_lonlath` / `model_to_enu`, geodesy `lonlath_to_enu` / `enu_rotation`, glb writer
  `model_glb`, geoid sample points `ground_samples`. Routes `modelapi.py::api_model_info`,
  `_build_model`, `GET /api/model.glb`. Page: `tools/photo_label/static/model.js::loadModel`, `modelMatrix`,
  `modelShader`, `measureGeoid`, `applyModelShow`.
- **Depth view (server half) and tile cache:** `depth.py::api_depth` (GET, `<stem>_depth.npy`, `_block_mean`),
  `api_depth_compute` / `_compute_depth` (GPU thread, `_depth_state`), `api_depth_fit` (metres = a / (disp - b));
  Google tile disk cache `tile_cache_get`, `tile_cache_put`, `tile_cache_trim`, `_max_age`, `TILES_CACHE_DIR`
  (`runs/photo_label_tiles`, 2 GB cap), used by `LabelHandler._proxy_tiles`. Tests: `tests/test_photo_label_depth.py`.
- **Page:** `tools/photo_label/index.html` (markup) plus ES modules in `tools/photo_label/static/`, served
  by `server.py` at `/static/<file>` (`.js`/`.css` only). `main.js` boots and wires events; shared state in
  `state.js` (`st`, `dv`, `app`, `fp`, `ces`, `mdl`). 2D map `labels.js::mapClick`, `map2d.js::setMapMarker`;
  3D `cesium3d.js::initCesium`, `setMode3d`, `pick3d`, `groundUnder`, `viewFromCamera`; photo canvas
  `draw.js::draw`, `drawBuildings`; pairs `labels.js::renderRows`, `select`, `removePair`, autosave
  `labels.js::scheduleSave` (500 ms debounce, header state `chrome.js::setSaved`).
- **Beginner mode and Settings:** `static/ui.js` — `setMode` (Beginner / Custom / Everything), `setTool`
  (elements marked `data-need="<tool>"`), `setAutoScreen` (region load screens only when on, default on),
  `applyUi`, `openSheet`; persisted with `util.js::prefSet`. Design: `docs/design-guidelines.md`; mockups
  `Code/claude/mockups/2026-11-01-photo-label/`.
- **Orientation (roof / wall / ground, wall facing, roof pitch, per building):** `tools/photo_label/orient.py` —
  `camera_axes`, `backproject`, `normals`, `classify`, `building_summary`, `rasterize`; route
  `depth.py::api_depth_orient` (`_orient_source`, `_orient_ground`, side file `<stem>_orient.json`).
  Page tab `or`: `tools/photo_label/static/orient.js::dvOrient`, `orDecode`, `orRgb`, `orSet`, `orMarks`, `orHover`.
- **Depth view (photo / photo depth / Google depth / difference tabs):** page only, `tools/photo_label/static/depth-view.js::dvLayer`, `drawBase`, `dvChrome`,
  `setTab`, `dvRefresh`, `dvLoadDepth`, `dvGoogle` (via `renderAt(cam, geoid, gridW)`), `dvFit`, `photoM`, `googleM`, `turbo`, `divRgb`;
  server calls `/api/depth`, `/api/depth/compute`, `/api/depth/fit`.
- **Register a photo against our own model (edges, no textures):** `tools/photo_label/register_model.py` -- `refine`, `ModelScene`, `model_wireframe`, `render_depth`, `visible_samples`, `photo_features`, `evaluate`; CLI `--region R --eval-panos|--eval-labels`, overlays in `runs/photo_label_model_register/`.
- **Tests:** `tests/test_photo_label_server.py`, `tests/test_photo_label_regions.py`, `tests/test_photo_label_register.py`,
  `tests/test_photo_label_model.py`, `tests/test_photo_label_auto_pose.py`, `tests/test_photo_label_depth.py`,
  `tests/test_photo_label_orient.py`, `tests/test_photo_label_guesses.py`, `tests/test_photo_label_screen.py`,
  `tests/test_photo_label_pano_camera.py`, `tests/test_photo_label_pano_seeds.py`,
  `tests/test_photo_label_mask_heights.py`, `tests/test_photo_label_register_model.py`.
