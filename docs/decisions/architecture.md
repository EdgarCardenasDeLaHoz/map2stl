# Architecture

Package layering (numpy2stl / geo2stl / city2stl / app), where shared constants live, and what is and is not the pipeline. Related: [mesh-pipeline.md](mesh-pipeline.md), [repo-tooling-docs.md](repo-tooling-docs.md).

### 2026-09-27 — numpy2stl registration takes a reference source, and the old geo modules raise
- **Decision:**
  - `register_city_stl` takes a `numpy2stl/src/numpy2stl/registration/reference.py::ReferenceSource` (`StaticReference` for in-memory arrays). map2stl's `city2stl/registration/__init__.py::OSMReference` implements it over `city2stl/osm_raster.py`.
  - For one release, `numpy2stl.applications.cities` / `lidar` raise an ImportError naming `city2stl.osm_raster` / the 3DEP EPT provider. `numpy2stl/tests/test_geo_free.py` fails on any osmnx / requests / pdal / map2stl import.
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
