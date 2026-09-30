# Mesh pipeline

How a DEM plus vector layers becomes one printable solid: the two-stage pipeline, the city model's scale rules, export formats, build speed and caches. Related: [architecture.md](architecture.md), [terrain-dem.md](terrain-dem.md).

### 2026-09-29 — Cold city builds: tiled terrain TIN, prepared point tests, threaded simplify; assembly stays sequential
- **Decision:**
  - `numpy2stl/src/numpy2stl/processing/decimate.py::heightfield_tin` cuts grids larger than 128 px into tiles that share their edge pixels, refined on 4 threads. Same error bound, conforming seams, ~1 % more vertices on the seams.
  - `city2stl/city_model.py::Terrain.cells_inside` prepares the polygons before `shapely.contains_xy` over a geometry array.
  - `city2stl/city_model.py::build_on_terrain` simplifies the merged model and the parts on 4 threads.
  - `city2stl/city_model.py::_separate_contacts` groups vertices with a lexsort of the float32 bits and builds fan centres with `bincount` over the touching faces only.
  - Puzzle and export zips: chunk-formatted OBJ / 3MF rows (`numpy2stl/src/numpy2stl/io/writers.py::_rows`), OBJs streamed into the zip, the 3MF stored (already compressed), zlib level 1.
  - `city2stl/city_model.py::assemble_model` keeps the sequential "layer − claimed, claimed + layer" loop.
- **Why:** profile of Granada (24 k buildings, cache off), 2026-09-29:
  - TIN: the refinement ends in ~17 passes that add < 1,500 vertices each but re-triangulate all 225 k (~0.65 s per pass). Tiled: 19 s → 7 s.
  - Cartagena waterways: unprepared `contains_xy` walked every edge of 20 k-vertex coastline polygons for each candidate cell. 10 s → < 0.1 s.
  - Simplify 22 s → 10 s. `_separate_contacts` 3.5 s → 0.4 s per call (same moves to 1e-13 mm).
  - Assembly measured on the captured Granada layer solids: current loop 19 s (after the contacts fix). One batch union of all layers 5 s alone; per-part subtraction chains without the growing union 34 s; threads no gain (manifold3d holds the GIL); batching small layers into one union no gain.
- **Rejected:**
  - Qhull incremental Delaunay for the TIN tail: 3.3 s to build, 0.7 s per small addition, slower than Triangle's full rebuild (0.3 s).
  - Adding every out-of-bound pixel (or its neighbours) in late TIN passes: flips create new misses, the tail stays 19–34 passes.
  - Rebuilding the merged model from simplified parts: shared surfaces simplify differently on each side and no longer cancel.
- **Supersedes / superseded by:** —
- **Source:** `tests/test_reference_cities.py` (regression set, same session); profiler `Code/agent-scripts/profile_city.py`.

### 2026-09-29 — The reference-city regression set runs on real terrain and gates puzzle volume
- **Decision:** `tests/test_reference_cities.py` (`pytest -m slow`) rebuilds Cartagena, Granada + Alhambra and Breckenridge through the export task, model cache off, test mode off. It fails on a non-watertight STL, a puzzle keeping < 99 % of the volume or an open piece, and faces ±15 % / volume ±3 % against `tests/reference_cities_baseline.json`. Time is logged (`output/regression/reference_cities.jsonl`), not asserted. `app/server/core/puzzle.py::volume_check` puts `kept` and `open_pieces` in every puzzle report; `build_check` warns below 99 %.
- **Why:** F-CITYMODEL success criterion (≥ 99 % volume). Its first run found a real crash (`merge_flat_roofs`: GEOS side-location conflict after mitre closing + simplify, fixed with `make_valid`) and that under test mode `/api/terrain/dem` returns a synthetic gradient, so a regression set inside pytest must switch test mode off.
- **Rejected:** asserting build time — machine-dependent.
- **Supersedes / superseded by:** —

### 2026-09-27 — Every mesh export runs the terrain stage, then the city model
- **Decision:** one two-stage pipeline for every mesh export (terrain STL/OBJ/3MF, preview, puzzle, city model).
  - Terrain stage (raster), `app/server/core/export.py::terrain_stage`: DEM source/merges, composite or edited values, curve edits, 3×3 median, river/lake carve, sea-level cap, scale, label, contours → one heightfield.
  - Feature stage (vector), `city2stl/city_model.py::build_on_terrain`: extrudes / drapes / cuts OSM features on that heightfield and merges in 3D.
  - "Terrain only" = the city model with no feature layers.
  - Composite `osm_*` building / road / wall / waterway channels are 2D-preview and ML only; `app/server/core/export_params.py::mesh_composite_layers` drops them from mesh exports (client filters them again). The legacy merge panel is gone.
  - The city task resolves its DEM through the same request path as other exports (`app/server/core/export_params.py::ExportContext`), not the raw handle.
  - River/lake carve runs after the 3×3 median (a 1 px river does not survive the median).
- **Why:** the user asked "are we using the composite pipeline to get to the 3D obj" and chose two stages. One route keeps scale, watertightness and simplification consistent; buildings in both the composite and the city model would print twice.
- **How to apply:** new terrain modifiers go in the terrain stage (raster); new features go in the city model (vector).
- **Rejected:** keeping building/road rasters in the mesh path — pixel checkerboard, double printing, second scale rule.
- **Supersedes / superseded by:** refines [2026-09-04 — Every export format goes through one mesh path](#2026-09-04--every-export-format-goes-through-one-mesh-path).
- **Source:** decisions.md 2026-09-26 F-ARCH entry (2026-09-27 bullet); [F-ARCH plan](../plans/active/F-ARCH-consolidation.md) "one two-stage mesh pipeline"; reference renders in [city SOP](../guides/city-stl-and-puzzle-sop.md) and [large-region SOP](../guides/large-region-sop.md).

### 2026-09-27 — City builds are fast through print-layer merging, content-keyed caches and no Triangle in draped slabs
- **Decision:**
  - Flat, ground-standing buildings whose absolute top rounds to the same 0.1 mm layer and whose outlines are < 0.4 mm apart merge into one prism (closing 0.2 mm, simplify 0.1 mm): `city2stl/city_model.py::merge_flat_roofs`.
  - Caches for layer polygons, layer unions, terrain TIN and the finished model live in the library (`city2stl/model_cache.py`), keyed by content digests plus `city2stl/model_cache.py::MODEL_CACHE_VERSION` (bump on any geometry change).
  - Polygons keep their GEOS precision grid through the cache.
  - Draped slabs: qhull Delaunay + GEOS cavity re-triangulation for missed outline edges (`city2stl/city_model.py::triangulate_polygon`). Same vertices, so the same face count as Triangle's CDT.
  - Trails are off by default (user); the fetch keeps paths / tracks / signposted footways; `city2stl/city_model.py::filter_trails` drops trails > 50 % within 60 m of buildings or 6 m of roads.
  - OSM entries at another (tolerance, min_area) are derived locally from a finer / enclosing entry (`app/server/core/city_data.py::lookup_city_layers`, params sidecar; legacy keys found by probing common values).
- **Why:**
  - Absolute top: two roofs in one print layer print as one surface; the wall between them is invisible.
  - Merge gains are small on real cities (Granada 779 → 580 solids, Cartagena 475 of 3,065 → 229) because the OSM fetch already dissolves touching same-height buildings. The speed came from vectorising (`numpy2stl/src/numpy2stl/core/extrude.py::prisms`, cells_inside 8 s → 0.3 s, assemble_parts) and caching.
  - Granada (23,937 buildings): build 138 → 101 s cold, 8 s unchanged rebuild; 3MF 31 → 5 s; puzzle 39 → 17 s; OSM at other panel settings 979 s → 2 s.
  - WKB drops the precision grid; grid-less copies made 2 Granada waterway slabs non-manifold.
- **Rejected:**
  - Clipping the terrain TIN to the polygon — robust but 3× the faces and 3-6× slower.
  - Conforming Delaunay by splitting — narrow strips double every round.
  - Caches in app code — the library owns the geometry, so it owns the key.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 city build speed entry; [F-CITYMODEL plan](../plans/active/F-CITYMODEL-vector-city-model.md) "build speed".

### 2026-09-26 — The city model is 1 px per mm, true vertical scale, features seated on the DEM
- **Decision:** (user)
  - 1 DEM px = 1 mm by default; 3×3 median on the DEM against blocky upsampling.
  - Vertical: true scale × exaggeration when the bbox diagonal is < 20 km, fit to a target relief above; always overridable. Every layer uses the terrain's vertical scale.
  - Extruded features sit on the highest DEM point under the footprint, with a skirt to the lowest (nothing floats, nothing is buried).
  - Features stay vector (prisms / terrain-following slabs) merged with manifold3d.
  - Simplification bounded by print resolution: terrain 0.05 mm, outline 0.05 mm, snap 0.01 mm.
  - Printability: features narrower than 0.8 mm are widened, extruded heights capped at 8 × footprint width (per-layer overridable); contact vertices separated by 0.001 mm (`city2stl/city_model.py::_separate_contacts`) so the welded STL is watertight.
- **Why:** one scale for buildings and terrain; vector merge avoids the raster checkerboard; at 1:3472 a 2 m trail is 0.6 mm wide, below what prints.
- **Rejected:** rasterising features into the heightfield — checkerboard edges and a second scale rule.
- **Supersedes / superseded by:** replaces the old `generate_city_3mf` mesher and the height-strip puzzle.
- **Source:** decisions.md 2026-09-26 F-ARCH entry; [F-CITYMODEL plan](../plans/active/F-CITYMODEL-vector-city-model.md).

### 2026-09-04 — Every export format goes through one mesh path
- **Decision:** `/api/export/{stl,obj,3mf}` share `app/server/core/export.py::_prepare_export_mesh` and `app/server/core/export.py::_mesh_response_headers`; the background pipeline (`app/server/core/export.py::_run_export_pipeline`) repairs every format. `X-Watertight` / `X-Face-Count` are measured after repair on all three.
- **Why:** the copies had drifted — OBJ and 3MF skipped label engraving, contours and trimesh repair, so the same request gave a different model per extension.
- **How to apply:** anything that changes the exported heightfield goes in the shared path, never in one format's branch.
- **Rejected:** deleting the direct routes — unreachable from the client but documented API, and one shared helper keeps them correct.
- **Supersedes / superseded by:** refined by [2026-09-27 — Every mesh export runs the terrain stage, then the city model](#2026-09-27--every-mesh-export-runs-the-terrain-stage-then-the-city-model).
- **Source:** decisions.md 2026-09-04 entry.

### 2026-09-04 — DEM preparation had three geometry defects, all fixed in v1
- **Decision:** in `app/server/core/export.py::_prepare_dem_array`:
  - Sea-level cap is `np.maximum(im, 0)` (was `np.minimum`, which flattened all land).
  - Meshes are built with `floor_val=0.0`, so `base_height` is a real thickness.
  - Exaggeration is applied after min-max normalisation: relief = `model_height × exaggeration` mm.
  - The function returns source extents in metres; contour callers pass `model_height × exaggeration`.
  - Client: `_describeFailure` reads FastAPI `detail` (string or 422 list).
- **Why (measured on a synthetic ridge −460 … +500 m):** cap on → 46.0 % flat, matching the 46.0 % below sea level; 30 mm relief on 5 mm base → 35.00 mm, floor at 0; 1× / 2× / 3× → 30 / 60 / 90 mm (all were 30 mm). A positive constant cancels through normalisation, so reordering the clamp could not fix it.
- **Safe because:** label engraving, contours and puzzle slots already clamp at ≥ 0.1 mm.
- **Rejected:** porting v2's throwing `request()` — v1's `{data, error}` contract is checked at every call site; a rewrite for no behaviour change. (v2 has since been retired.)
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 "three geometry defects" entry.

### 2026-09-04 — A wall's width comes from its barrier kind
- **Decision:** `city2stl/registration/osm_water.py::BARRIER_WIDTH_M` gives `city_wall` 6 m and `wall` 1.5 m, used only after a way's own width tag. City walls stay in the registration reference.
- **Why:** 650 wall ways across Old San Juan, Cartagena, Granada and Prague carry one width tag (0.4 m). `barrier=city_wall` averages 300 m/way (San Juan) and 854 m (Cartagena) vs ~45 m for `barrier=wall`. City walls are large, distinctive and correctly placed — what registration wants.
- **Rejected:** filtering city walls out of the reference.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 wall-width entry.

### 2026-09-04 — Courtyards are holes, bridged into the exterior before ear clipping
- **Decision:** interior rings are kept. The old OSM mesher spliced them into the exterior along a mutually visible cut (exterior forced CCW, holes CW, largest hole first), with a tolerance-based vertex compare.
- **Why:** the mesher took only `coordinates[0]`, so every cloister and light well in every city printed solid. GeoJSON producers ignore the winding rule, so orientation must be forced. Verified watertight: solid 36000.0, one hole 32000.0, two holes 31360.0 mm³.
- **Rejected:** trusting input winding — the first bridged ring added area.
- **Supersedes / superseded by:** the old mesher (city2stl mesh.py) is gone; the city model now extrudes shapely polygons with holes ([2026-09-26 city model](#2026-09-26--the-city-model-is-1-px-per-mm-true-vertical-scale-features-seated-on-the-dem)).
- **Source:** decisions.md 2026-09-04 courtyards entry.

### 2026-09-04 — Rings are sanitised before triangulation, because Triangle faults instead of raising
- **Decision:** every ring is cleaned first: drop non-finite points, collapse duplicates within 1e-3 mm, reject < 3 points or near-zero area, repair self-intersection with `buffer(0)` keeping the largest polygon. City 3MF export timeout raised to 1800 s.
- **Why:** Seville killed the server twice (Windows access violation in Triangle, exit 139, no traceback); a `try/except` cannot catch a C fault. Repair, not rejection, because courtyard buildings are legitimate. Seville meshing legitimately takes 305 s, Cordoba 598 s (old limit 120 s).
- **Supersedes / superseded by:** the old mesher is gone; the lesson stands — draped slabs no longer use Triangle ([2026-09-27 build speed](#2026-09-27--city-builds-are-fast-through-print-layer-merging-content-keyed-caches-and-no-triangle-in-draped-slabs)).
- **Source:** decisions.md 2026-09-04 degenerate-rings entry.
