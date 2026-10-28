# Architecture

Package layering (numpy2stl / geo2stl / city2stl / app), where shared constants live, and what is and is not the pipeline. Related: [mesh-pipeline.md](mesh-pipeline.md), [repo-tooling-docs.md](repo-tooling-docs.md).

### 2026-10-28 — numpy2stl.raster.burn_polygons is the one rasteriser; the wrappers stay
- **Decision:** no new rasteriser. F-UNIFY Phase 1 step 5 closes with no code change.
  - `city2stl/rasterize.py::rasterize_city_data`, `app/server/routers/composite.py::_rasterize_buildings`, `city2stl/osm_raster.py::_rasterize_buildings`, `city2stl/registration/osm_water.py::_rasterize` and `tools/ml/data/collect_osm_tiles.py::_rasterize_buildings` already burn through `numpy2stl.raster.burn_polygons`.
  - What differs between them is policy, not the burn. Missing heights become 10 m on `None` or on `0`, or the feature is skipped. `osm_raster` supersamples 4x and keeps cells with 30 % coverage as NaN-backed means. Registration uses `all_touched` and row 0 = south.
- **Why:** a shared `burn_height_grid` would wrap a single `burn_polygons(..., mode="max")` call and still need every policy as a parameter. That is one more layer and no fewer lines.
- **Left as is:** `tools/align_tool/plate_vectors.py::polygons_to_heightmap` fills with `cv2.fillPoly` on rounded vertices, so its edges differ from the cell-centre rule, and it has no test. Moving it onto `burn_polygons` changes plate pixels. It needs a golden test and a registration check first (`docs/issues.md`).
- **Supersedes / superseded by:** —
- **Source:** [F-UNIFY plan](../plans/active/F-UNIFY-one-app.md) Phase 1 step 5.

### 2026-10-28 — The three OSM water modules stay separate: they do different jobs
- **Decision:** no merge. F-UNIFY Phase 1 step 4 closes with no code change.
  - `map2stl/geo2stl/water_layers.py` turns lake and river features (fetched by `city2stl/fetch.py`) into DEM depth grids. It fetches nothing and has no coastline logic; its only sea is the DEM-based `ocean_mask`.
  - `map2stl/city2stl/skyline/osm_water.py` picks coastline and water features out of an already fetched layer and samples keypoints for pano heading scores, in lon/lat.
  - `map2stl/city2stl/registration/osm_water.py::osm_water` is the only one that fetches (through `geo2stl.osm.overpass_query`). It fills the sea side of the coastline and rasterises water for plate registration.
- **Why:** the plan assumed one builder existed three times. A survey found no shared fetch, closure or sea-side code to fold together. The tag sets also differ on purpose (reservoirs for lakes, `place=sea` for skyline, wetlands and docks for registration), so unifying them would change every output.
- **Rejected:** moving the registration coastline fill into geo2stl. Its users (`registration`, `street_place`, align_tool `locate`) all sit in city2stl and tools, so the move would add a layer hop and no second user. Its `_rasterize` is folded into the one rasteriser in step 5.
- **Supersedes / superseded by:** —
- **Source:** [F-UNIFY plan](../plans/active/F-UNIFY-one-app.md) Phase 1 step 4.

### 2026-10-28 — The leftover geo constants import from geo2stl.geo, with no numeric change
- **Decision:**
  - `map2stl/geo2stl/geo.py` also holds `M_PER_DEG_SPHERE` (111 320 m for latitude, the older convention), `EARTH_RADIUS_M` (6 371 000 m) and `EARTH_CIRCUMFERENCE_M`. `geo2stl/projections.py::EARTH_RADIUS` and `geo2stl/imagery.py` take them from there.
  - About 30 skyline, height, photo-label and tools files that re-declared or inlined these numbers import them instead. Where a file kept a local name (`M_PER_DEG_LAT = 111_320.0` in skyline), it now imports `M_PER_DEG_SPHERE as M_PER_DEG_LAT`, so the rest of the file is unchanged.
  - `map2stl/geo2stl/geo.py::lonlat_to_local_m` (moved from `skyline/_core/projection.py`, which imports it) is the one local metric projection.
  - One bug fix: `city2stl/skyline/scripts/17_photo_pipeline.py` measured longitude with a flat 100 000 m per degree; it now uses `m_per_deg_lon(lat)`.
- **Why:** each copy could drift on its own, and a reader could not tell a deliberate 111 320 latitude from a mistake. Naming the convention keeps every output identical while showing which modules still use it.
- **Rejected:**
  - A shared `BBox` type: `RegionBBox`, `BboxQueryParams` and `schemas.BoundingBox` already agree on north/south/east/west, and a common base would only add an import.
  - A shared tile/quadkey helper: quadkeys exist only in `satellite_footprints.py`.
  - Moving these modules to 110 574 m in the same commit. Skyline scores, survey fills and plate registrations were tuned with 111 320 (about 0.7 % apart); that is a separate change with a before/after benchmark.
- **Kept:** `city2stl/osm_raster.py::_M_PER_DEG` (its comment ties it to registration tuning), the WGS84 ellipsoid maths (6 378 137 m), `geo2stl/opentopo.py`'s 6 371.0088 km, and the edge-convention pixel helpers (`composite._make_geo_to_px`, `plate_height_truth.lonlat_to_px`), which are not a swap for `GeoGrid`.
- **Supersedes / superseded by:** extends the 2026-09-26 entry below.
- **Source:** [F-UNIFY plan](../plans/active/F-UNIFY-one-app.md) Phase 1 step 3.

### 2026-10-28 — Every Overpass caller goes through geo2stl/osm.py
- **Decision:**
  - New `map2stl/geo2stl/osm.py::configure_osmnx`. It picks the first healthy mirror, then applies `use_overpass_endpoint`: bounded timeouts, the rate limiter only on overpass-api.de, and the host pin.
  - Its callers are `city2stl/osm_raster.py::_ox`, `tools/align_tool/locate.py::tune_osmnx` (which still turns the limiter off for every mirror), `tools/ml/data/collect_osm_tiles.py` and `tools/eval/eval_roof_tags.py`.
  - `city2stl/registration/osm_water.py::OVERPASS_URLS` is now `OVERPASS_INTERPRETER_URLS`, which adds maps.mail.ru.
  - The raw-QL callers already used `overpass_query`.
- **Why:**
  - osm_raster kept osmnx's default mirror with no health check.
  - The two tools used bare osmnx.
  - `tune_osmnx` had its own probe and turned the host pin off entirely, where `install_dns_pin` keeps an address that connects.
  - There was a second hand-kept mirror list.
- **Rejected:** moving `city2stl/fetch.py`'s layer loop into geo2stl. It owns cancellation and per-layer retry for the app and has no second user.
- **Supersedes / superseded by:** —
- **Source:** [F-UNIFY plan](../plans/active/F-UNIFY-one-app.md) Phase 1 step 2.

### 2026-10-28 — Background jobs share one runner, app/server/core/jobs.py
- **Decision:**
  - `map2stl/app/server/core/jobs.py::Job` holds the lifecycle every job shares: id, params, status, progress, message, error, result, timestamps, cancel event, worker thread. `map2stl/app/server/core/jobs.py::JobRegistry` holds start / join-identical / get / cancel and the TTL sweep, and runs each job in a thread named `<kind>-<id>`.
  - `ExportTask`, `CityFetchTask` and `PlateTask` became subclasses of `Job`. Each keeps its own status words (exports end `complete`, the others `done`), its snapshot and its endpoints, so the client is unchanged.
  - A cancel ends the job at once. A worker that finishes later cannot overwrite it, because `Job.finish` ignores a cancelled job.
  - `GET /api/jobs/{id}` and `POST /api/jobs/{id}/cancel` (`map2stl/app/server/routers/jobs.py`) find a job of any kind. The Tools pages use them.
- **Why:** before this, three copies of a dict + lock + uuid + TTL sweep + thread had drifted apart. Only exports had a heartbeat, only city fetches had a cancel event, and plates had their own cancel race guard. Skyline runs and benchmarks (F-UNIFY Phase 4) would have added two more copies.
- **Rejected:**
  - One status vocabulary: it would have changed three client pollers for no gain.
  - A process pool: jobs share in-memory caches and the GPU lock.
- **Supersedes / superseded by:** —
- **Source:** [F-UNIFY plan](../plans/active/F-UNIFY-one-app.md) Phase 1 step 1.

### 2026-09-30 — One-release shims were removed and library progress goes through logging
- **Decision:**
  - Removed with their last callers re-pointed: `city2stl/skyline/pipeline.py` (star-import façade), `city2stl/skyline/height/` shim, `app/server/core/height/service.py` (re-export plus an uncalled cached fetch), `tools/align_tool/{osm_model,street_place}.py` `sys.modules` aliases, numpy2stl `applications/{cities,lidar}.py` tombstones and `registration/align/segmentation.py`, `geo2stl/__init__.py` re-exports (import the submodule).
  - Library code logs (`logger = logging.getLogger(__name__)`; info for progress, warning for problems) instead of printing: skyline, `geo2stl/tiles.py`, `numpy2stl/src/numpy2stl/core/solid.py::validate_object`, and the SDK `app/session/terrain_session.py` (report methods such as `settings_table`, `check_alignment`, `verify` still print). CLIs under `tools/` and `scripts/` keep `print` and call `logging.basicConfig` where they drive library code.
  - Remaining inline metres-per-degree copies in app / registration code call `geo2stl.geo` (shifts ≤ 0.7 %: the old code used a 6371 km sphere, 111 000 or 111 320 m for latitude where `M_PER_DEG_LAT` is 110 574 m).
- **Why:** the shims had outlived their one release with no callers; the `tools/align_tool/street_place.py` alias could not be imported under `python -m`, and a broad `except` in `export_align_data.py` turned that into street placement silently off.
- **Supersedes / superseded by:** closes the one-release shims of the 2026-09-26 / 2026-09-27 entries below.

### 2026-09-27 — numpy2stl registration takes a reference source, and the old geo modules raise
- **Decision:**
  - `register_city_stl` takes a `numpy2stl/src/numpy2stl/registration/reference.py::ReferenceSource` (`StaticReference` for in-memory arrays). map2stl's `city2stl/registration/__init__.py::OSMReference` implements it over `city2stl/osm_raster.py`.
  - For one release, `numpy2stl.applications.cities` / `lidar` raise an ImportError naming `city2stl.osm_raster` / the 3DEP EPT provider (removed 2026-09-30, with the `registration.align.segmentation` alias). `numpy2stl/tests/test_geo_free.py` fails on any osmnx / requests / pdal / map2stl import.
  - The OSM raster maths (111 320 m/degree, 3.5 m/level) stays as it was so registrations do not move.
- **Why:** numpy2stl must not fetch or know lon/lat, yet registration needs the OSM raster at three resolutions, a tight frame sized from the STL, masks, an nDSM and a centre-search ring.
- **Rejected:** precomputed arrays only — loses the multi-resolution fetch and centre search; lazy numpy2stl → map2stl import — forbidden by the layering rule; forwarding shims — numpy2stl cannot import map2stl.
- **Open:** switching the OSM raster to `geo2stl.geo` / `height_from_tags` is a separate, measurable change.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 entry; [F-ARCH plan](../plans/active/F-ARCH-consolidation.md) step 13.

### 2026-09-26 — Metres per degree are defined once, in geo2stl.geo
- **Decision:** `map2stl/geo2stl/geo.py::M_PER_DEG_LAT` = 110 574 m and `map2stl/geo2stl/geo.py::M_PER_DEG_LON_EQ` = 111 320 m × cos φ (WGS84, spherical-cos approximation), plus bbox helpers and `map2stl/geo2stl/geo.py::GeoGrid`.
- **Why:** ~70 inline `111_320·cos(lat)` copies (some 111 000), 6 transform helpers and 4 bbox-size helpers disagreed.
- **Exceptions kept:** `city2stl/osm_raster.py` and the survey provider keep 111 320 so registrations do not move (see entry above).
- **Supersedes / superseded by:** —
- **Source:** [F-ARCH plan](../plans/active/F-ARCH-consolidation.md) "Defaults taken without asking".

### 2026-09-26 — Raster row 0 is north by default
- **Decision:** rasters use the image convention (row 0 = north). Mesh and registration code flips explicitly at its boundary: `numpy2stl/src/numpy2stl/stl2numpy/heightmap.py::mesh_to_heightmap` defaults to `row0="north"`; the registration pipeline, `building_simplify` and the align tool pass `row0="south"`.
- **Why:** one default across libraries; the flip is visible at the one place that needs it instead of implied by a module's history.
- **Supersedes / superseded by:** supersedes [2026-08-06 — Rasters are row 0 = south everywhere](projections-raster.md#2026-08-06--rasters-are-row-0--south-everywhere-and-the-satellite-layer-was-flipped-superseded).
- **Source:** decisions.md 2026-09-26 F-ARCH entry; F-ARCH step 12.

### 2026-09-26 — numpy2stl is geo-free, and each duplicated capability gets one home
- **Decision:** (user)
  - Dependency rule: `app → city2stl / geo2stl → numpy2stl`; nothing lower imports higher; libraries never import `app.*`.
  - OSM and lidar fetching move to `city2stl`; registration takes rasters/polygons as input.
  - Tag heights: 3.2 m per level, plus one level for the roof when `roof:levels` is absent (`city2stl/heights.py::height_from_tags`, replacing copies at 3.0 / 3.4 / 3.5 m).
  - Migrate in small steps with one-release shims at old paths, tests and a commit per step; city-model fixes first.
  - Defaults taken without asking: keep both mesh→heightmap methods (`method="bin"|"raycast"`); retire v2; delete the notebook-only city2stl buildings.py.
- **Why:** a read-only audit found 11 polygon burns, 4 tag-height copies, 5 Overpass clients and libraries importing `app.*`.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-26 F-ARCH entry; [F-ARCH plan](../plans/active/F-ARCH-consolidation.md) (lists what was consciously left).

### 2026-09-25 — Both repos install editable, layering is one-way, and shims were removed
- **Decision:**
  - numpy2stl uses a `src/` layout with `pyproject.toml`; map2stl's `pyproject.toml` declares `app`, `geo2stl`, `city2stl`. No `sys.path` bootstraps; tools run as `python -m tools.x.y`.
  - Paths defined once: `map2stl/app/paths.py::REPO_ROOT`, `numpy2stl/src/numpy2stl/_paths.py`, `map2stl/tools/align_tool/paths.py`.
  - numpy2stl never imports map2stl; libraries take keys as parameters instead of importing `app`.
  - No import guards for declared dependencies; guards only for genuinely optional ones (torch, transformers, mobile_sam, napari).
  - Internal skyline code imports the defining module, never the pipeline.py façade.
  - Launch logic in `map2stl/scripts/`; `.bat` files are one-line wrappers; hooks in `map2stl/.githooks/`.
- **Why:**
  - Imports depended on the working directory and a sibling-folder hack.
  - ~40 `.parent`-counted roots silently broke when tools/ml was split.
  - A missing declared package must fail loudly.
  - A star-import façade drops private names and snapshots mutable globals — it silently disabled multi-res SegFormer refinement.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-25 entry.

### 2026-08-26 — TerrainSession is an SDK, not the pipeline
- **Decision:** `map2stl/app/session/terrain_session.py::TerrainSession` is a `requests` client that starts a uvicorn subprocess and calls the same REST routes as the browser. Trace the pipeline through the routers and [pipeline reference](../reference/pipeline.md), not this file. SDK docs: [sdk.md](../reference/sdk.md).
- **Why:** its size (~3100 lines, rechecked 2026-09-28) and name make it look like the core. It imports only `geo2stl.geo` helpers, not the mesh code, and nothing on the browser render path touches it.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-26 entry.

### 2026-08-26 — The saved-location render pipeline is client-orchestrated [superseded]
- **Decision (then):** there is no server-side "render this region" call. `POST /api/terrain/dem` caches the DEM under a key from bbox + DEM settings; `POST /api/export/start` rebuilt that key by hand. Any key change had to touch both places.
- **Why:** the hand-rebuilt key had drifted twice (`dim` 200 vs 600; `maintain_dimensions`), giving "Missing DEM data".
- **Supersedes / superseded by:** the key drift is closed by [one DEM cache key](terrain-dem.md#2026-08-26--the-dem-cache-key-has-exactly-one-definition); since F-DEMID (2026-09-27) exports send the `dem_id` returned by the DEM request. The two-request, client-orchestrated shape remains.
- **Source:** decisions.md 2026-08-26 entry; [pipeline audit](../history/audits/pipeline-audit-2026-08-26.md).
