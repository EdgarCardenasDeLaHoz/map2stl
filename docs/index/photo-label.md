# Locator: photo-label tool (`tools/photo_label/`)

Scope: the local hand-pairing and photo-registration tool (photo point to map or Google 3D
point, camera solve) and its "our model over Google 3D" overlay. Skyline heights themselves
are in [skyline.md](skyline.md); the export pipeline the overlay reuses is in
[mesh-pipeline.md](mesh-pipeline.md) and [city2stl.md](city2stl.md).

Run: `~/.venvs/map2stl/Scripts/python.exe tools/photo_label/server.py [--port 8767] [--region granada] [--osm <path>] [--dem <tif>] [--photos <dir>]`
(the flags override the default region's entry in `regions.json`),
then open `http://localhost:<port>/`. Endpoint list: the module docstring of `server.py`.

- **Regions (one server, several regions):** `tools/photo_label/regions.json` (label, osm, dem, photos
  key -> folder, optional view {lat, lon, ground_m}); `server.py::Regions` (lazy `Config` per region,
  shared key and tile session, `get`, `listing`, `tiles`), `load_regions`, `build_regions` (CLI
  overrides), `UnknownRegion` (400). Region-specific GETs take `?region=`, POSTs `"region"`; an empty
  photo key adds no path prefix (Granada labels keep their names). Multi-root photos:
  `_photo_files`, `photo_rel`, `list_photos`, `safe_photo`. Page: `index.html::switchRegion`,
  `clearRegion`, `api`, `postJson`, `homeMap`, `centre3d`, `disposeModel`; region kept in `#region=<id>`.
- **Saved camera:** `api_save_labels` stores `camera` + `camera_source` in the label file; page
  `saveLabels` (also after Solve / Auto register), restore in `index.html::loadPhoto`.
- **Map lines toggle** (fov wedge, sight ray per pair, cyan auto rays, buildings in view recoloured
  to match the photo): `index.html::drawMapLines`, `fpBase`, checkbox `rayChk`.
- **Buildings projected onto the photo:** `terrain_solve.py::project_buildings` (`MAX_VISIBLE`
  nearest in view, `MIN_DIST_M`, `MIN_VISIBLE_PX`).
- **Server, routes, config:** `tools/photo_label/server.py` — `Config` (paths, cached DEM and
  footprints, model build state), `LabelHandler.do_GET` / `do_POST` (route table), `make_server`,
  `main`; labels `api_get_labels`, `api_save_labels`, `label_path`; photos `list_photos`,
  `safe_photo`; footprints `overpass_to_geojson` (building ways plus building multipolygon relations, outer
  rings stitched by `_stitch_rings`, ids `r<relation>`), `geojson_footprints`; start of the 3D view
  outside Granada `start_view` (the region's `view`, else the DEM centre).
- **Camera solve from pairs:** `server.py::api_solve` (terrain fit seeded by
  `city2stl/skyline/camera_solver.py::solve_pose`), `server.py::api_project`;
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
  DEM), `solve_pose`, `register_step`; routes `server.py::api_register_candidates`,
  `api_register_step`; page side `index.html::autoRegister`, `renderAt`, `settleTiles`.
  When the start guess scores below `SWEEP_IF_BELOW` inliers, `index.html::sweepHeadings` renders
  `SWEEP_HEADINGS` headings at `SWEEP_HFOV`, then narrows to `SWEEP_FOVS`, and iterates from the best.
  Renders are serialised by `renderLock` (`renderAt` queues, `renderNow` renders); the offscreen viewer
  `ces.rv` is kept across photos and disposed only in `clearRegion`. The 35 mm focal length from EXIF
  (`server.py::photo_focal_35mm`) narrows the f range in `api_register_candidates`.
- **Start-pose guesses (preloaded, shown on the map):** `server.py::api_register_candidates` seeds the
  search with request viewpoints, the title viewpoint, photo GPS (as a viewpoint, never trusted alone)
  and footprint viewpoints; `photo_hints` (credit.json title vs region `landmarks` in `regions.json`,
  "from/desde X" = viewpoint, other match = subject, `_bearing_deg`), `_tag_candidates` (source/label),
  heading re-rank toward the subject, the title viewpoint searched alone and pinned first, cache `<stem>_guesses.json`. Street View frames take their known
  pose instead: `pano_seed` (metadata.json, heading from the file name + median `heading_offset_deg`,
  the file's real size). Panorama-pipeline frames (`runs/<region>/03_seeds/seed_<id>/` with views.json +
  pose.json; Granada `panoview` frames via `manifest.json`; roots `seeds` / `panoview` in `regions.json`)
  take `pano_seed`'s other branches (`_seed_frame_pose`, `_panoview_pose`): source `pano_anchor`, `elev_fitted`
  when pose.json has `cam_elev_m` (drone); the page treats it like `pano` (`index.html::isPanoStart`: no heading
  sweep, mesh-ground height unless `elev_fitted`); `_photo_files` hides strip/mask/seg files (`_is_seed_nonframe`),
  `fillPhotoSel` groups seed frames in an optgroup per folder. Page: `index.html::loadGuesses`, `applyGuess`, `drawGuesses`, `searchHere`
  (drag), `clearGuesses`, checkbox `guessChk`.
- **Flag a bad photo:** `POST /api/photo/flag` (`<annotations>/<region>_photo_flags.json`), hidden by
  `list_photos` (`?all=1` lists them with `bad`), `server.py::api_photo_flag`, `_set_flag`, `flags_path`; page `index.html::flagPhoto`, button `flagBtn` with Undo; checkbox `flagChk` "Show flagged" lists them marked ⚑
  (`loadPhotoList` with `?all=1`, `fillPhotoSel`, `photoOpt`, `syncFlagBtn`: the button becomes Unflag).
- **Auto screening (street-level frames, stitched panoramas):** `tools/photo_label/screen.py` —
  `screen_from_labels` (rules: ground share >= `GROUND_MAX`, aspect >= `PANORAMA_ASPECT`), `screen_photo`
  (cache `<stem>_screen.json`, SegFormer only when the aspect rule did not fire; photos with a `pano_seed`
  pose are skipped by `server.py::_has_known_pose`, never auto-flagged); job
  `server.py::api_photos_screen` / `_screen_region` / `_screen_flag` (GET state `api_photos_screen_state`,
  flags through `_set_flag`; no CUDA and > `SCREEN_CPU_MAX` photos = aspect only); page `index.html::screenPhotos`
  (polls from `switchRegion`, Undo unflags).
- **Footprints speed:** server `Config.footprints_bytes` (serialised once, 6 decimals, gzip); page
  draws on one canvas `fpRenderer` and keeps only those near the view on the map, `fpWindow`
  (`fpAll`, `FP_MIN_ZOOM`).
- **Geoid:** a region's measured `geoid_m` in `regions.json` (`Config.geoid_m`, `server.py::_start_geoid`)
  is used as is (`register_step(fit_geoid=False)`); without one, `register.py::geoid_from_render` (every
  depth cell) and `autoRegister` re-renders once when the measured geoid moves > 3 m.
- **Pano camera height:** `index.html::meshGround` (lowest Google mesh point within 8 m) + `PANO_CAM_HEIGHT_M`,
  applied in `autoRegister` to a `pano` start.
- **Start pose without pairs (skyline vs DEM horizon):** `tools/photo_label/auto_pose.py` —
  `candidate_poses`, `photo_skyline`, `dem_horizon`, `horizon_rows`.
- **Our city model over Google 3D** (opacity, Model/Google/Both, dz nudge, live geoid):
  `tools/photo_label/model_glb.py` — `build_or_load` (cache `runs/photo_label_model/<region>_<key>.glb`
  + `.json`), `choose_inputs` / `find_city_payload` (app OSM cache payload overlapping the DEM
  most, read only; else `footprint_layers`), `resample_dem`, `build_model`
  (`city2stl/city_model.py::build_city_model`, true scale, `cache=False`), inversion
  `model_to_lonlath` / `model_to_enu`, geodesy `lonlath_to_enu` / `enu_rotation`, glb writer
  `model_glb`, geoid sample points `ground_samples`. Routes `server.py::api_model_info`,
  `_build_model`, `GET /api/model.glb`. Page: `index.html::loadModel`, `modelMatrix`,
  `modelShader`, `measureGeoid`, `applyModelShow`.
- **Depth view (server half) and tile cache:** `server.py::api_depth` (GET, `<stem>_depth.npy`, `_block_mean`),
  `api_depth_compute` / `_compute_depth` (GPU thread, `_depth_state`), `api_depth_fit` (metres = a / (disp - b));
  Google tile disk cache `tile_cache_get`, `tile_cache_put`, `tile_cache_trim`, `_max_age`, `TILES_CACHE_DIR`
  (`runs/photo_label_tiles`, 2 GB cap), used by `LabelHandler._proxy_tiles`. Tests: `tests/test_photo_label_depth.py`.
- **Page (single file):** `tools/photo_label/index.html` — 2D map `mapClick`, `setMapMarker`;
  3D `initCesium`, `setMode3d`, `pick3d`, `groundUnder`, `viewFromCamera`; photo canvas `draw`,
  `drawBuildings`; pairs `renderRows`, `select`, `removePair`.
- **Orientation (roof / wall / ground, wall facing, roof pitch, per building):** `tools/photo_label/orient.py` —
  `camera_axes`, `backproject`, `normals`, `classify`, `building_summary`, `rasterize`; route
  `server.py::api_depth_orient` (`_orient_source`, `_orient_ground`, side file `<stem>_orient.json`).
  Page tab `or`: `index.html::dvOrient`, `orDecode`, `orRgb`, `orSet`, `orMarks`, `orHover`.
- **Depth view (photo / photo depth / Google depth / difference tabs):** page only, `index.html::dvLayer`, `drawBase`, `dvChrome`,
  `setTab`, `dvRefresh`, `dvLoadDepth`, `dvGoogle` (via `renderAt(cam, geoid, gridW)`), `dvFit`, `photoM`, `googleM`, `turbo`, `divRgb`;
  server calls `/api/depth`, `/api/depth/compute`, `/api/depth/fit`.
- **Register a photo against our own model (edges, no textures):** `tools/photo_label/register_model.py` -- `refine`, `ModelScene`, `model_wireframe`, `render_depth`, `visible_samples`, `photo_features`, `evaluate`; CLI `--region R --eval-panos|--eval-labels`, overlays in `runs/photo_label_model_register/`.
- **Tests:** `tests/test_photo_label_server.py`, `tests/test_photo_label_regions.py`, `tests/test_photo_label_register.py`,
  `tests/test_photo_label_model.py`, `tests/test_photo_label_auto_pose.py`, `tests/test_photo_label_depth.py`,
  `tests/test_photo_label_orient.py`, `tests/test_photo_label_guesses.py`, `tests/test_photo_label_screen.py`.
