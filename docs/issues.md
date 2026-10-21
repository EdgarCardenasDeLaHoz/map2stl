# Known Issues — map2stl

_Last updated: 2026-10-21_

- Live bugs and technical debt only. Planned work is in the roadmap,
  [plans/README.md](plans/README.md); ideas are in [proposals.md](proposals.md).
- Fixed issues move to [history/issues-resolved.md](history/issues-resolved.md), with their
  full write-ups (the fixed §2–§2e, §4, §0, §0d and all older resolved entries are there).
- Section ids `0c` and `3` are cited from code (`city2stl/height/providers/google_3d.py`,
  `city2stl/height/providers/gba.py`): keep them.

## Fixed 2026-10-21: Granada height truth was built from a raster that suppresses buildings

- **Fix:** truth rebuilt CNIG-first (entry in [building-heights](decisions/building-heights.md)); 3,173 of 13,132 records outside the cached rasters still carry REDIAM values (`survey_provider`) until a re-run with network. Original text below.

- `city2stl/skyline/benchmark.py::REGIONS` sends Granada's truth to `rediam_mdhn`. That raster
  suppresses lidar-classified building cells (see `city2stl/height/providers/rediam_mdhn.py`),
  so on ordinary buildings it reads low. On the same eroded footprint, CNIG p95 minus REDIAM p95
  is +5.4 m median on survey-filled rows and +3.3 m on floor-count rows; cell by cell, REDIAM
  is 5.7 m below CNIG where CNIG reads over 3 m, and 16 % of those cells are void.
- Effect: `tools/eval/score_export_heights.py` shows every Granada source about +5 m high, before
  and after the 2026-10-20 height fixes. Ruled out: ground reference (streets agree to 0.4 m),
  misalignment, the scorer's `height_m` read, ring and stat choice, osm_id matching.
- Fix: rebuild Granada truth CNIG-first (REDIAM only where CNIG has no building, as
  `survey_fill` does) with `skyline/scripts/19_refresh_survey_truth.py`, then re-score. A
  CNIG-based truth is not independent of a CNIG fill: hold the survey rows out when ranking
  providers. Not done yet: `city2stl/skyline/` is the panoramic session's area.

## Fixed 2026-10-20: Alcazaba plaza slab, lowered landmarks, +1 roof floor, sliver leftovers

- **Fix:** `city_model.py::is_open_enclosure`, `survey_fill.py::is_landmark` / `_burn_stats`, `heights.py::has_pitched_roof_shape`, `MIN_LEFTOVER_WIDTH_MM`; `MODEL_CACHE_VERSION` 7. Entries: [building-heights](decisions/building-heights.md), [roofs-landmarks](decisions/roofs-landmarks.md).
- **Open:** cached payload floor-count heights keep the old +1 roof floor until a fresh fetch (or survey re-measure); landmark parts only rise, so 19% of floor-count parts stay over 3 m off.

## Fixed 2026-10-18: Alhambra reference swap filled courtyards and dropped OSM buildings

- **Symptom:** with `reference_landmarks` on, the Alcazaba's Plaza de Armas printed solid and
  10 / 6 / 4 OSM features under way/26935518, way/1476959785 and way/117369769 were replaced by
  blurry reference geometry.
- **Cause:** the outlines have no holes, `_depinch` floored every cell at 1 m, and
  `LandmarkPlan.take` removed every feature at least 50% inside an outline.
- **Fix:** `landmarks.py::ndsm_solid` cuts cells under `REFERENCE_OPEN_M`; `LandmarkPlan._kept_osm`
  keeps taller OSM buildings. Tests: `tests/test_landmarks_reference.py::TestCourtyardsAndKeptOsm`.
  Not yet measured on the real Granada data. Open: `building:part` features are still replaced.
  Follow-up: the plaza floor in the raster is about 3 m, so the Alcazaba unit carries `open_m: 5.0`
  in `alhambra_reference_rule.json` (per-unit cutoff, `REFERENCE_SOLID_VERSION` 3); not yet
  re-measured on the real data.

## Fixed 2026-10-18: Alhambra: Palacio de Carlos V and other courtyard buildings missing

- **Symptom:** valid buildings with a courtyard (one inner ring) vanished from the print and were
  counted as `dropped`. Palacio de Carlos V (OSM relation/2640220) went from 402.98 mm2 to 0; 10 of
  90 holed polygons in the Alhambra area were lost.
- **Cause:** `city2stl/city_model.py::feature_polygons` shrank and regrew every holed polygon by
  one `SNAP_MM` step (to open pinched holes) *after* snapping. On the 0.01 mm grid that negative
  buffer returned an empty polygon for some valid holed polygons (without the grid: 401.7 mm2).
- **Fix:** `_depinch` touches only polygons whose hole touches the outline or another hole, runs
  the buffer on the geometry without its precision grid, re-snaps, and keeps the original if the
  result is empty or under 99% of its area. `MODEL_CACHE_VERSION` 5 -> 6 rebuilds cached solids.
  Tests: `tests/test_city_model.py::TestFeaturePolygonsHoles`.
- **Same fault in the flat-roof merge:** `merge_flat_roofs` opened the holes of merged groups the
  same way, so a merged block around a patio could print as nothing while its members counted as
  merged. It now calls `_depinch` too.

## Use case: four 600 mm city prints, driven through the API (2026-10-17)

The user asked for Granada, Breckenridge (town, ski trails), San Francisco and Cartagena at about
600 mm on the longest side, cut into printable pieces. An agent drove the export API in-process
(FastAPI `TestClient`, the endpoints the UI calls: `GET /api/terrain/dem`, `POST
/api/export/start`, status, download). For an agent the API is easier than the UI; every gap
below is one that also costs a UI user a step.

- **No offline fallback when Overpass is down.** All mirrors failed their `/status` probe. The
  cache held v4 payloads for Granada, Breckenridge and SF, but `city2stl/cache_policy.py` refuses
  them as stale, so the export failed. The agent had to monkeypatch `CITY_PIPELINE_VERSION`.
  Wanted: serve a stale cache with a warning when no endpoint answers. Cartagena had no cache at
  all and stayed blocked.
- **Export cannot be requested by region name.** The bbox had to be copied from `data.db` by
  hand, and the wrong San Francisco region was picked first ("San Fran" is the Bay Area,
  "SanFranciso" is the city and peninsula). Wanted: `region_id` on `/api/export/start`.
- **No "longest side = N mm" input.** The agent derived `mm_per_px` from the DEM dimension.
  Wanted: a target size that picks `mm_per_px` (the UI has a print-scale helper; the API does
  not).
- **No region preset for layers.** The composite layer spec came from a script helper
  (`render_regions.layers`), not the API.
- **Size guard:** the SF region's 26.5 km diagonal is over the 25 km limit, so the export needs
  `allow_large_city`. The error says so, but the UI has no way to pass it.
- **Coarse cache wins silently.** The only SF cache was the Cities panel's coarse load
  (tolerance 3 m, at least 1200 m²), so small buildings are missing and nothing says so.
- **Trails have their own 7-day cache** (`trails_osm`), so Breckenridge needed a live fetch of
  about 7 minutes even though its buildings were cached.
- **No headless preview.** There is no endpoint that returns a PNG of an export, so the agent
  wrote its own renderer to check the result.

## Edit map: changing the projection changed nothing visible (fixed 2026-10-14)

- **Symptom:** picking another projection in Edit › Canvas re-fetched every layer, but the map kept
  the same shape.
- **Cause:** `stacked-layers.js::updateStackedLayers` letterboxed the height map to the bbox's
  cosine-corrected shape whatever the projection, so each projected raster (Mercator 573×600,
  Sinusoidal 600×435) was stretched back into one frame.
- **Fix:** `graticule.js::frameAspect` uses the raster's own aspect when a projection is set and
  "Keep fixed canvas shape" is off; tests in `tests/js/graticule.test.js`.
- **Still open:**
  - Two tabs on one region overwrite each other's settings: each autosave PUTs the whole blob, so
    a stale tab puts the old projection back (seen as interleaved PUTs from two connections).
  - ~~The change re-fetches every layer (about 15 s for Europa) with no preview meanwhile.~~
    **Fixed 2026-10-14:** the loaded layers are warped into the new projection in the browser at
    once (`modules/dem/projection-preview.js`, under 0.2 s for Europa) with a "Preview · loading
    layers…" pill; each layer swaps to its real data as it lands. Still not warped: the city
    building overlay and the hover tooltip (hidden until the DEM lands).
  - **Fixed 2026-10-14 (server side):** the real data now lands fast too. Each projection sampled
    its own source grid, so a switch re-decoded the DEM GeoTIFF (3.6 s per request) and re-carved
    every river (river_carve 9.9 s; composite 12.9 s, hydrology 14.4 s). The base DEM is now read
    once onto a projection-independent shared grid (`geo2stl/dem.py::shared_base_grid`) and the
    rivers are carved once on it, then pooled per projection without losing 1-px channels
    (`geo2stl/water_layers.py::pool_river_stack`, `pool_deepest`). A new projection with warm
    region caches: hydrology 0.14-0.26 s, composite 0.8-1.05 s. Carve masks agree with the old
    per-projection carve to within 1 px for 99 % of river pixels. Still open: the final
    categorical (nearest) `project_grid` can drop thin N-S rivers where a projection stretches x;
    lakes are still fetched per projection.
  - **Fixed 2026-10-14 (client side):** a switch took 3.3-5.3 s on 1000 px Colombia, and Everest
    held the pill 31 s. Nothing was requested for 460 ms (full-size snapshot and warp, then the
    80 ms debounce, then synchronous stack redraws); Borders, Rivers & lakes and the composite
    dem-merge waited for the DEM to land; long tasks came from a composite recompute on the
    cleared DEM, four passes over 1M values in `_applyDemResult` / `drawHistogram`, and
    repeated redraws. Now every request goes out at the change event
    (`event-listeners-map.js::_switchProjection`), the warp runs at 600 px after them, stack
    redraws coalesce per frame (`ui-helpers.js::emitStackUpdate`), and the stats are one pass
    (`dem/dem-stats.js`). The preview ends when each layer has settled for the current pick
    (`projection-preview.js::settlePreviewLayer`), not when a new canvas appears, so a layer
    with nothing in the box (no rivers on Everest) no longer holds it. Not yet measured live;
    `window.DEBUG_PROJ_TIMING = true` logs `[proj-timing]` per request.
  - **Fixed 2026-10-14 (cold open and DEM switches):** Colombia's first open spent 29 s in
    river_carve: 21 s rebuilding a missing full HydroRIVERS parquet that an order 3+ read never
    uses (`geo2stl/hydrology.py::_read_region_parquets` now skips it), and ~8 s carving. The carve
    is faster with output unchanged bit for bit: vectorised valley snapping
    (`water_layers.py::snap_reaches_to_valley`), GeoJSON built from one ragged array
    (`numpy2stl/src/numpy2stl/raster/burn.py::_ragged_mappings`), and a worker pool started before the reaches
    are read (`water_layers.py::start_carve_pool`); Colombia's cold rivers step is now ~7.6 s plus
    0.9 s for the DEM. `GET /api/terrain/dem` decoded the whole GeoTIFF on every switch (Colorado
    4-5.7 s, Norway 4.3 s); it now cuts the projection's grid from `shared_base_grid`
    (`geo2stl/dem.py::fetch_dem`, `shared_dim=`, 0.01-0.05 s) and reads ESA water at the shared
    grid's scale, so no switch asks Earth Engine for a finer image (Norway Mercator: 7 s). The
    shared grid's cap is a cell budget (`projections.py::shared_source_dim`), so Norway's cosine,
    equidistant and Mercator no longer fall back to a per-projection carve (11-15 s each).
    Still open: the DEM disk cache key holds the projection's source_dim
    (`app/server/core/dem_cache.py`), so each new projection still writes its own entry; the DEM
    route's grid now differs from the old direct decode by a mean 2-6 m (resampled twice).

## Europa: hollow seas and no rivers in Rivers & lakes (2026-10-14)

The Edit-tab 2D "Rivers & lakes" layer on Europa showed water only as a 20-40 px band along
the coasts (the open sea showed bathymetry colours, -1552 m at 64.8 N 2.6 E) and no river.

- **Hollow sea.** The rivers preview had been cached without its water surface, so the layer
  fell back to `/api/terrain/water-mask`, which counted only ESA WorldCover class 80. ESA maps
  land tiles only: class 80 is a coastal strip, the open sea is its no-data (0), and the DEM sea
  fallback is dead on a PC without the local GEBCO store (`ocean_root`). **Fixed:** water =
  80 or 0 (`geo2stl/sat2stl.py::esa_water`, as `dem.water_fraction_for_dem` already did); the
  water-mask cache key is now `v: 2`.
- **No rivers.** pyarrow 26 needs NumPy 2 (numpy is pinned to 1.26), so every HydroRIVERS
  parquet build and read failed, returned None, and looked like a box without rivers. The
  river carve cache, the preview JSON cache and the composite cache all kept that. **Fixed:**
  `requirements.txt` pins `pyarrow[s3]>=14,<26`; a failed region raises
  `geo2stl/hydrology.py::HydroRiversUnavailable` (the composite skips and retries the layer);
  `water_layers.py::river_carve_layers` never caches an empty carve;
  `app/server/routers/terrain.py::_print_payload_complete` keeps incomplete previews out of
  the cache; `COMPOSITE_CACHE_VERSION` 8 drops composites cut without rivers.
- **Still open:** the running server must be restarted to load the fix and the pyarrow 25 DLL
  (then delete `site-packages/~yarrow`, left by the in-use upgrade). The legacy
  `terrain.py::_hydrorivers_layers` path (no `dem_source`) still caches `water=None` as zeros.
  This PC has no `ocean_root` store, so the DEM sea fallback never runs here.

## Projections: wings printed as a flat slab (open; crop is opt-in, 2026-10-17)

- **Symptom:** sinusoidal / laea / lcc grids are full rectangles whose cells outside the bbox are
  NaN (curved "wings" joined to the border). `prepare_dem` filled them with the lowest elevation, so
  the print had a slab in the corners; `composite.py` also turned them into 0 m.
- **Status:** still open. The slab prints by default until the projected frame (Cover mode)
  lands. A crop exists but is opt-in (`dem.crop_wings`, default false): on the Europe region
  under laea it kept 452 x 478 of 800 x 622 cells (43% of the area) and shrank the print to
  452 mm, so the user chose the frame instead (2026-10-17).
- **Opt-in crop:** with `dem.crop_wings` true, `ExportContext.from_request`
  (`app/server/core/export_params.py::_crop_wings`) crops the grid, `carve_m`, `lake_carve_m` and
  `still_water` to the largest wing-free rectangle (`geo2stl/projections.py::largest_valid_rect`) and
  shrinks `bbox` by the kept fraction of columns / rows so the mm-per-m scale holds. Interior NaN voids
  still get the fill. Composite keeps wings NaN (`COMPOSITE_CACHE_VERSION` 10).
- **Still wrong:** the client's printed-size label (`modules/export/print-scale.js::modelScale`,
  `widthMm = cols * mmPerPx`) is computed from the uncropped grid, so it over-reports the print;
  buildings / vector features place through the (now shrunk) bbox as a plate carree, which is already
  inexact for non-linear projections. The "projected frame" feature replaces this crop.

## Active bugs — Extrude tab audit (2026-10-13)

Source and full list: [history/audits/AUDIT-2026-10-13-extrude.md](history/audits/AUDIT-2026-10-13-extrude.md).
All its bugs and risks were fixed on 2026-10-13, as was Granada Cathedral printing as one
prism (`city2stl/city_model.py::drop_building_copies`). Still open: its Design section (duplicate
controls, old form style, jargon, view controls in the panel, per-part show/hide and colour).

- **Towers on the Alhambra walls are still buried (2026-10-14).** Walls and fortifications now
  print as ground-following outline rings (`city2stl/city_model.py::split_on_ground`,
  [decision](decisions/roofs-landmarks.md)); 83 of 85 precinct buildings stand clear. Torre de la
  Bruja (way/92528760, 10 m) and Torre de las Damas (way/196500564, 12 m) sit on the 12 m wall
  with heights at or below it, so the wall covers them. Needs a real height for them (landmark
  override) or a rule that a building on a wall stands on the wall top.
- **Cached cities need a refetch for unmerged landmarks.** `CITY_PIPELINE_VERSION` 5 marks
  version-4 payloads buildings-only stale; until the buildings layer is refetched, named
  buildings stay merged (Granada: 26 into "Palacio de Carlos V").

Found in the live check the same day:
- **Error toast covers the 3D view controls.** On a machine without Earth Engine
  authentication, the "Failed to load: Earth Engine is not authenticated" toast sits over the
  Wireframe / Normals / Auto-rotate row at the bottom of the Extrude panel, and it comes back after
  being dismissed (each new composite request raises it again). Seen on Granada, 1014×922 window.
  **Fixed 2026-10-14:** the stack is centred in the gap between the side panels
  (`app/client/static/js/modules/core/ui-helpers.js::toastLane`), a message already shown is not
  stacked again, and a closed error stays closed until the page reloads (`showToast`).

## Active bugs — from the full code audit (2026-10-05)

Source and full list: [history/audits/AUDIT-2026-10-05.md](history/audits/AUDIT-2026-10-05.md).
Its silent wrong-answer bugs were all fixed the same day, the last one by T15
([issues-resolved](history/issues-resolved.md)).

## Active bugs — from the pipeline audit

Source: [history/audits/pipeline-audit-2026-08-26.md](history/audits/pipeline-audit-2026-08-26.md)
(ids = its numbering). Re-checked against the code 2026-09-28: 12 findings fixed, 2 obsolete,
the rest below.

- **PA-9 Region create resets parameters** (update fixed): `create_region` uses the
  `RegionParameters()` defaults (dim 200 / height 10 / base 2). The DB defaults are
  600 / 25 / 5 (`app/server/schemas.py::RegionParameters` vs `app/server/core/db.py`).
- **PA-10 Settings are not validated or versioned**: `save_region_settings_route` stores the
  raw dict; there is no settings model (the unused `RegionSettings` was deleted 2026-09-30) and no
  `schema_version`. See F-FE1 step 3.
- **PA-14 Silent zero-fill** (partly fixed; ESA/EE and all-tiles-failed now raise):
  - `geo2stl/dem.py::fetch_dem` (local source) returns zeros on error.
  - `geo2stl/hydrology.py` river rasteriser returns zeros on error.
  - A single failed tile stays black, logged at DEBUG only (`geo2stl/imagery.py::fetch_tile`).
- **PA-16 Non-atomic cache writes** (partly fixed: `geo2stl/cache.py` array / OSM / composite
  writes are atomic). Still written in place, so a truncated file stays cached:
  - `geo2stl/opentopo.py::fetch_opentopo_dem`
  - the HydroRIVERS shapefile extract in `geo2stl/hydrology.py`
  - `geo2stl/sat2stl.py` (joblib)
  - `geo2stl/imagery.py::fetch_tile`
- **PA-3 Binary STL writer**: `numpy2stl/src/numpy2stl/io/writers.py::writeSTL` opens the final
  path directly (no temp + rename) and joins the whole mesh in memory. The app's mesh path uses
  trimesh; `writeSTL` is still reached from `geo2stl/dem.py`, `geo2stl/write.py` and numpy2stl
  `numpy2stl/src/numpy2stl/core/solid.py`.
- **PA-12 Two caches escape `CACHE_ROOT`** — fixed 2026-09-30: `geo2stl/opentopo.py::CACHE_PATH`,
  `geo2stl/sat2stl.py::CACHE_DIR` and `app/server/config.py::EE_CACHE_DIR` sit under
  `geo2stl.cache.CACHE_ROOT` (so `$MAP2STL_CACHE` moves them); `EE_CACHE_DIR` had pointed at
  `Code/cache/ee`, which sat2stl no longer wrote. The unused `OPENTOPO_CACHE_PATH` was removed.
- **PA-M Medium, still open**:
  - `app/server/core/db.py`: `_CREATE_REGIONS` lacks continent / source / city / tags.
  - `app/server/core/db.py::get_db` connections are never closed.
  - Region routes: no validators, `_ensure_db()` on every request, and blocking sqlite in
    `async def` routes.
  - Export temp files leak on failure or restart (`export._write_mesh`; nothing after `yield`
    in the lifespan).
  - Half-pixel convention mismatch: the projection grid uses nodes, `burn_polygons` uses edges.
  - `blend_layers` checks shape only; `verify_layer_alignment` has no caller.
  - `water`, `esa_lc` and `hydrology` cache keys have no version token.
  - No export concurrency limit (`export_tasks.start_export_task`, one thread per request).
  - `read_text` / `write_text` / `writeOBJ` without an explicit encoding.
  - `city2stl/fetch.py` still calls osmnx `features_from_bbox`.
  - A contour-engraving failure is still downgraded to a warning by a blanket `except`
    (`export._apply_contour_lines`).
- **PA-S Structural, still open**:
  - Two city-raster routes (`app/server/routers/composite.py` and `app/server/routers/cities.py`).
  - Session:
    - `terrain_session.py` is 3.1k lines.
    - `enrich_buildings_with_heights` has no callers.
    - `_kill_stale_server` kills any owner of port 9090.
  - Dead code:
    - `geo2stl/write.py::savefile` (row-flipped, notebook-only).
    - `create_dem_model` / `process_region` are notebook-only.
  - Presets:
    - `satImgResolution` is not re-applied.
    - `hydrology.scale_m` is not collected.
    - `wat.dim ?? wat.sat_scale` lets `dim` override the legacy `sat_scale`.
  - The `rotation` setting has no consumer.
- **PA-F Frontend**: two client stacks (`main.js` + `vue/`); three.js r128 loads from a
  CDN (`app/client/templates/index.html`). See F-FE1.

## Mesh and export faults found in the Granada detail study (2026-10-17)

- **`numpy2stl` `generate.py::array_to_mesh` is not watertight on pinched masks:** two
  cells that touch only at a corner produce a non-manifold edge.
- **`city2stl` `shade.py::color_for` matches part names by substring,** so a part whose name
  contains another part's name gets the wrong colour.
- **The city cache holds duplicate outlines:** the same footprint appears once as `building`
  and again as `building:part`, so both are extruded.

## F-DETAIL: open faults after the first full Granada export (2026-10-17)

- **Roof classification was slow on a cold cache — fixed 2026-10-17 (KO):** the first full
  Granada export spent ~10 min in "Loading OSM layers", inside `city_data.py::with_roof_shapes`
  (23,969 buildings), with no progress message, and every server restart repeated it.
  - Now each answer is stored per footprint in `<CACHE_ROOT>/roof_shapes.sqlite`
    (`city2stl/roof_cache.py`); only footprints never seen are classified, the model predicts
    in batches of 512, and the fetch and export tasks show "Classifying roofs N of M".
  - Granada (tiles on disk): cold 124 s, after a restart 3.4 s (23,867 from the cache, same
    histogram: 19,263 flat, 4,604 gabled). The 23 skipped buildings are retried on each read.
  - Still open: a cold city still waits ~5 ms a building before its first preview; serving flat
    roofs while classifying in the background would remove the wait.
- **Reference landmark `relation/2640220` reports "missing"** (17 of 18 units applied). Several
  `ref:` free-standing units show `replaced_features: 0`; check that this is expected.
- **Alcazaba, Abencerrajes, Sultana** are swapped although the study flagged their footprints
  for a check.
- **Edit panel:** Reset in Buildings & roads › Composite skips the Trees row; changing Roof shapes
  takes effect only on the next buildings reload.
- **Extrude viewer left out the trees — fixed 2026-10-18 (TM):** `model-viewer.js` builds its
  finished-model request synchronously, and on a region's first load the survey coverage was
  still 'unknown', so `cityDetailFields` dropped `layers.trees`. The viewer now rebuilds once
  `window.citySurveyCoverageReady` resolves (Granada: 9 parts, trees 793,816 faces).
  - Still open: the first, tree-less build keeps running on the server to its end (wasted work).
- **US trees failed on a dict bbox — fixed 2026-10-18 (TM):** `canopy.py::_default_ndsm` passes
  `{north, …}` to `covers()`, and `lidar_3dep.py::_is_in_us` unpacked it into its key strings
  ("'<' not supported between 'str' and 'float'"); every US build with trees failed. It now
  uses `_survey.intersects`.
- **Cartagena's sea renders as flat green land:** no water part and no sea colour on the 5 km
  box. Not investigated.

## Vector rivers — known faults (2026-10-17)

Rivers cut as vector solids (`app/server/core/export.py::river_features`, request
`vector_rivers`; on by default for regions ≥ 200 km):
- **No valley snapping:** reaches follow HydroRIVERS lines, not the DEM valley floor the
  raster carve snaps to, so a channel can cross a canyon wall where the two disagree.
- **0.05 mm steps** where depth classes meet (a tributary entering its river).
- **River mouths dent the sea floor:** the channel solids are not clipped by the ocean mask.
- **Non-linear projections** (mercator, equal-area, `cosine` with `maintain_dimensions`)
  fall back to the raster carve (`X-Vector-Rivers: raster river carve: …`).
- **Previews** always show the raster carve.

## Active data limitations

### Overpass `/status` probe rejects every mirror (2026-10-17)
- `geo2stl.osm.healthy_overpass_endpoints` gave every mirror a 20 s `/status` probe; overpass-api.de
  timed out, kumi answered 500, mail.ru 504 with the osmnx user agent, so a city fetch failed with
  "No Overpass endpoint answered its /status probe" although mail.ru served the real queries.
  Workaround used for Cartagena: a scratch run that put mail.ru first. A cached city now falls
  back to an outdated copy with `stale_cache_warning` (`city_data._serve_stale_or_raise`), but only
  when that copy has every requested layer; a cache with some layers still fetches the rest.
- **Fixed 2026-10-18:** the probe already sent osmnx's user agent. When no mirror passes it,
  `city2stl.fetch.fetch_osm_data` now tries them all (`geo2stl.osm.unconfirmed_overpass_endpoints`:
  a mirror that answered HTTP first, one that failed a real query last) and fails over per layer.
  `geo2stl/trails.py` still uses the strict list. A partial cache still does not serve stale.

### 0h. The local SRTM h5 store is sparse; missing tiles were a silent flat 0 (2026-10-14)
- `strm_h5/strm_data.h5` holds 94 tiles (of ~870 SRTM land tiles). "Amazon" (N15 S-19
  E-45 W-85, `h5_local`) needs 56 and the store has 12 (the Andes), so 44 tiles came back as
  0: one flat basin with a few rectangles of relief, cached like a good result.
- **Fixed 2026-10-14:** `geo2stl/dem.py::fetch_h5_dem` reports missing tiles (`coverage=`),
  and `geo2stl/dem.py::_fetch_h5_with_fallback` fills them from `_h5_fill_source`: the local
  GeoTIFF folder (GEBCO, `config.json` `ocean_root`), else OpenTopography SRTMGL3 (SRTM15Plus
  for boxes over 4.05M km²; a failure is not retried for 15 min). With no fallback the gap
  stays 0 and `source_note` says "Incomplete coverage" (and the grid is not cached). DEM cache
  schema v5 (`app/server/core/dem_cache.py`) drops the old gap-filled entries.
- Still open: `config.json` `ocean_root` points at the other PC's GEBCO folder (per-PC
  folders: F-KEYS); `h5_local` is meant for city boxes (<15 km) but accepts a continent and
  then reads 12 tiles at 90 m only to average them to ~2.5 km; composite layers
  (`fetch_layer_data` without `notes`) only log the coverage warning.

### 0g. Without Earth Engine, the DEM lowers only the sea (2026-10-13)
- Water subtraction reads lakes and rivers from ESA WorldCover, which needs Earth Engine (or a
  cached image). This machine is not authenticated, so `/api/terrain/dem` lowers only the sea
  found in the elevation data and says so in `source_note`
  (`geo2stl/dem.py::water_fraction_for_dem`). Fix: authenticate Earth Engine (Keys panel).

### 0e. Google 3D Tiles has no buildings over Cartagena (surveyed 2026-08-28)
- Outside Google's photorealistic cities, the endpoint serves a global base mesh with no
  buildings. The fetch "works" (99 % filled, fine geometric error) but is flat.
- Relief inside a 1 km window tells the two cases apart: 35–105 m where buildings exist
  (Miami, Rio, Buenos Aires, Santiago, Medellín), 1–4 m where they do not (Cartagena, Lima,
  Panama City, Barranquilla).
- `_looks_built` now returns an empty result with a warning instead of a flat raster.
- **Still open:** in data-poor cities the best source is missing.
  - In the export merge, Cartagena falls back to GlobalBuildingAtlas (`gba.py`, complete, 3 m,
    flattens towers) and Overture (`open_buildings.py`, ~9 % of footprints).
  - No registered provider reads Google Open Buildings 2.5D. The "~91 %, capped at 99.5 m"
    figure came from a read no code performs today.
  - `google_ob25d.py` (2026-10-09) reads it as a skyline low-rise prior only, and its heights
    are capped at 100 m.
  - Nothing measured so far reaches the Bocagrande towers.
- Full measurements: [history/issues-resolved.md](history/issues-resolved.md) (§0d) and §0c
  below.

## Active technical debt

### 0. Guide screenshots are stale (found 2026-10-05)
- `claude/scripts/sop_screenshots.py` (which renders `docs/guides/img/`) still drives the Edit
  panel from before the 2026-10-04 rebuild: `.lp-seg` / `.lp-btn` / `#lpDemSource` /
  `#lpRiverDepth`, `window.setUiTools`, and the removed Style setups (`apply_style`). It cannot
  run as is, so the city and large-region guides' Edit screenshots predate the Layers list,
  the View / Fetch / Composite tabs and the Canvas row.
- 2026-10-05: the two step-2 shots (`03-city-preset.png`, `02-region-preset.png`) showed the
  removed Style row and were dropped with their captions; the guide text describes Canvas →
  Fetch instead.
- Fix: port the walker to `LayerSettings.vue` (`.ls-tab`, `.ls-btn`, rows by label as in
  `claude/scripts/edit_panel_check.py::row`), select `canvas` for Projection / Detail, and
  rerun both guides.

### 0a. Skyline height pipeline — known weaknesses (audit 2026-08-28)
Found while fixing the elevation-datum bug below. All are reported, none are fixed:

- **The tag-disagreement filter is circular.** `city2stl/skyline/_core/height.py` drops any prediction more
  than `min(2 x height_tag, 50 m)` from the OSM tag, so accuracy measured against tagged
  buildings is partly self-fulfilling, and the "over-prediction is rare" argument used to
  justify the upward-only rescue is derived from filtered data.
- **The outlier-seed rule bites at exactly 3 seeds.** A 1.5 x MAD threshold over three
  values nearly always downweights one of them, whether or not it deserves it.
- **`n_views` / `views` count MAD-rejected views.** The reported view list includes
  estimates that were dropped from the mean, with no marker saying so.
- **`enhance_buildings_with_raster` never revises an `osm_levels` height**, even where the
  raster clearly disagrees.
- **`_footprint_roof_y_from_mask` needs half the columns** (`cols // 2`) to be building,
  which misses narrow spires and setback towers.
- **Depth is treated as horizontal distance.** `depth_height_from_segment` uses the
  metric depth as `forward_m`; it is really slant range. Small near the horizon, growing
  with the elevation angle.

### 0b. `city2stl/skyline/runs/` is ~6 GB on disk
Gitignored (not in history) but a OneDrive-sync burden. As of 2026-07-26 it is
~6 GB, dominated by regenerable caches: `image_cache` (2.6 GB),
`satellite_image_cache` (1.9 GB), `region_reports` (1.4 GB PDFs). Run
**`make clean-runs`** to drop the cache dirs + stale `*.log` (~4.6 GB); it keeps
`region_reports/` and `height_traces/`. See [history/audits/AUDIT-2026-06-07.md](history/audits/AUDIT-2026-06-07.md).

### 0f. Height benchmark and survey truth (found 2026-10-09)
Survey-only regions score single-source since 2026-10-09 (`benchmark.py::truth_mode`; the old n = 0
also came from truth keyed on full-precision rings, now `report_key`). Open:

- **Raw roof max is noise on the Miami topobathy survey.** 24 % of 914 Miami records have a max
  more than 20 m over the p95 (up to 294 m; Honolulu 1.7 %). Silhouettes are scored against p99
  (`METHOD_STAT`); the max is stored but should not be used as truth without a spike filter.
- **Nested OSM parts have no survey truth.** A `building:part` under a taller footprint reads the
  top of the stack (Honolulu: 241 of 1,138 published rows with truth; Miami 48 of 292, 32 of the
  headline's 168). The band tables leave them out (`nested_keys`); the headline (`score_buildings`)
  still scores them, so it did not move.
- **San Juan report rows outside the truth strips have no truth** (44 survey tiles, Santurce):
  `19_refresh_survey_truth --new-truth --regions san_juan --from-report
  runs/benchmark/2026-10-09_regions/san_juan_skyline_report/heights.json` (two `--shard` readers,
  about 75 min).
- **Single-source truth can show demolished or new buildings.** San Juan b21404 is tagged 34 m and
  reads 0.06 m in the 2018 survey (flown after Hurricane Maria); OSM `start_date` cannot flag it.
- **Ground p5 is relative.** `ground_p5_m` is the nDSM p5 in a 4 m ring, not an elevation: the
  providers drop the DTM (`lidar_3dep_copc.py::grid_points_ndsm` returns DSM - DTM only). Needs a
  provider change to store the DTM.
- **OSM `start_date` is nearly absent** in the truth cities (San Juan 3, Honolulu 7, Fort
  Lauderdale 1 buildings), so the truth-age flag is `unknown` for almost every record; buildings
  finished after the 2018 PR/FL flights are only caught where tagged.
- **Region runs write `city2stl/skyline/_pano/runs/seed_resolution_cache.json`** (tracked in git):
  the three truth-city runs added 17 seed entries. Commit it or git-ignore it.

### 1. `<script>` vs module boundary
`app.js` is still a plain script and modules still coordinate through `window.*`. Inline
handlers are gone (except the dev-only debug overlay). The fix is F-FE1 steps 1, 2 and 6
([plans/active/F-FE1-vue-consolidation.md](plans/active/F-FE1-vue-consolidation.md)).

## Measurements and fixes cited from code

### 0c. Satellite height sources beat the panorama pipeline (measured 2026-08-28)
Scored per footprint against the registered Miami STL plate, same
p90-of-covered-pixels rule, with the pipeline's tag filter disabled:

| Source | n | MAE | bias | corr |
|---|---|---|---|---|
| `google3d` (Google 3D Tiles) | 63 | **14.02 m** | +7.28 | **+0.922** |
| `open_buildings` (Overture) | 61 | 20.95 m | +8.40 | +0.866 |
| Skyline panorama pipeline | — | 59.85 m | — | +0.117 |
| `ndsm` | 10 | 55.88 m | −55.88 | +0.563 |

Google 3D Tiles is the most accurate height source in the project, and the gap
widens on exactly the buildings that matter most. On the 23 footprints above
100 m -- the class the panorama pipeline reads as a third of true height -- it
scores MAE 9.29 m at a bias of +1.73 m, and it recovers a tallest building of
254 m against the plate's 256 m. It has no height ceiling and no resolution
floor, which is what separates it from both footprint sources.

It passes the same independence test as Overture: split by whether OSM tags a
height, MAE is 15.93 m on the 36 tagged footprints and 11.48 m on the 27
untagged ones. Better where OSM says nothing.

The Overture result is not an echo of the OSM tags it partly ingests: split by
whether OSM tags a height, it scores MAE 24.03 on the 35 tagged footprints and
MAE 16.81 on the 26 untagged ones, and it matches zero OSM tags to within
0.5 m. Accuracy is *better* where OSM says nothing.

For data-poor cities the sources complement each other. Over Cartagena, Overture answers 31
of 329 registered footprints (9 %) and reaches 190 m uncapped. GBA is complete but loses tall
buildings (decision "GlobalBuildingAtlas admitted at confidence 0.55").

The "Google Open Buildings 2.5D answers 298 of 329 (91 %), capped at 99.5 m" claim that stood
here has no provider behind it: `open_buildings.py` is Overture. Google 2.5D is read today
only by `google_ob25d.py`, as a low-rise prior. Its rasters do stop at 99.5 m over Cartagena:
that is the maximum in the 2023 window, so the cap is real.

The Bocagrande towers stay unrepresented by any raster source, and they are exactly the
buildings the panorama pipeline gets most wrong.

### 3. Raster height enhancement made buildings shorter than the 10 m fallback — fixed 2026-08-30
- Kept here because code cites it: a provider's ranking was tuned on an argument, not a
  measurement.
- Cause: the merge ranked providers by a flat confidence constant and ignored resolution.
  A 30 m nDSM beat 5 m Open Buildings and pushed buildings to the 3 m clamp.
- Fix: rank by confidence × `resolution_priority(res)`; per-building enhancement refuses
  sources coarser than 10 m (`BUILDING_RESOLUTION_LIMIT_M`).
- Full write-up with the seven-city tables:
  [history/issues-resolved.md](history/issues-resolved.md) (§3).

## Audits to re-verify

- Re-verify the colour-contrast and UX audit items against the running UI:
  [history/audits/accessibility-audit.md](history/audits/accessibility-audit.md),
  [history/audits/ux-audit.md](history/audits/ux-audit.md). They were archived as-is on
  2026-09-28 and some items may still be open (see proposal A11Y-1).

## Carried over from the memory bank (2026-09-28)

Open items that lived only in `Code/claude/memory-bank/activeContext.md`. Dates are when each was
last measured.

- **Height model default path is off by one** (found 2026-09-28):
  `city2stl/height/predict.py` sets `_MODELS_DIR` from `Path(__file__).resolve().parents[3]`,
  which is `Code/models/`, not `map2stl/models/`. The default `height_unet.pt` is also absent
  from both, so the U-Net default checkpoint never loads.
- **Cache sweep reaches ~1 % of the cache** (measured 2026-09-04; deferred by decision):
  - `prune_all_caches` (`app/server/core/cache.py`) walks only the `NAMESPACE_TTL` keys, once
    at startup, and is non-recursive.
  - Never swept: hydrorivers (4.8 GB), ndsm (1.1 GB), roof_tiles (392 MB), ee, google3d, the
    loose root-level Overpass `.json` files.
  - The opentopo tif cache (6.9 GB) has orphaned entries since `dim` left its key.
  - Fix: sweep the tree that exists. Decide per directory whether a refetch is cheap before
    deleting anything.
- **Height/roof loose ends** (2026-09-06, not re-checked since):
  - GHSL WMS and the Copernicus EU building-height WCS fail on every bbox (Copernicus: HTTP 400).
  - `shadow_conf` saturates at 1.0 (`city2stl/roof_features.py`).
  - `_attach_building_terrain` (`city2stl/skyline/region_data.py`) is called only from a demo
    script, never in production.
  - The Miami roof call-rate job segfaults (native import-order clash), so the third call-rate
    row in [reference/roof-shape-model.md](reference/roof-shape-model.md) is empty.
- **Registration water data** (2026-09-14): a partial water fetch degrades registration
  silently, and a cached empty layer is never retried (the Lisbon case in
  [decisions/registration-refinement.md](decisions/registration-refinement.md)). Nine plates
  have an empty water mask image.
