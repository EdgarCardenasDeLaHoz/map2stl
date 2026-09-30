# F-CITYMODEL — Vector city model: one scale, seated features, merged solid

Status: mostly done (2026-09-27): merged watertight solid, per-layer 3MF, jigsaw puzzle,
printability rules and build speed are in; volume check and the reference-city regression set
done 2026-09-29. Open: build spec round-trip ([roadmap](../README.md#mesh-pipeline-and-city-model)).
Replaced the old `city2stl.mesh` 3MF generator and the app's height-strip puzzle. Findings
behind it: [city guide](../../guides/city-stl-and-puzzle-sop.md).

## Decisions (user, 2026-09-26)

- **Horizontal**: 1 DEM px = 1 mm by default (models ≤ 1000 × 1000 mm); 3×3 median filter on
  the DEM to remove blocky upsampling artefacts.
- **Vertical**: auto — true scale × exaggeration when the bbox diagonal is < 20 km, fit to a
  target relief height above; always overridable. Buildings and every other layer use the
  same vertical scale as the terrain.
- **Seating**: extruded features sit on the highest DEM point under the footprint, with a
  skirt down to the lowest point under it (nothing floats, nothing is buried).
- **Vector, not raster**: features stay polygons → prisms (or terrain-following slabs) and
  are merged as 3D solids (manifold3d booleans), so no pixel checkerboard is introduced.
- **Layers**: buildings, walls, fortifications, towers, churches, roads, water, green,
  rail, trails — all on by default, each toggleable, each with its own raised / engraved /
  extruded mode and size.
- **Outputs**: one merged watertight STL *and* a 3MF with a non-overlapping part per layer.
- **Puzzle**: cut the merged model with interlocking jigsaw cutters; per-piece OBJ + 3MF.
- **Simplification**: restore and finish the lossless (coplanar-region) simplifier and use
  it on flat regions.

## Approach

1. `city2stl/city_model.py`: `ModelScale` (auto/true/fit), DEM → median → mm grid →
   `array_to_mesh` terrain solid; georeference vectors with the DEM's own pixel mapping
   (cosine / none projections); clip to the model rectangle; per-layer builders:
   - *extrude*: `extrude_polygon`, floor = max terrain under footprint, skirt to min − ε,
     roof = floor + height × z-scale (lines buffered by a wall width);
   - *raised / engraved*: terrain-following slab (polygon triangulated at ~1 px, top/bottom
     offset from the terrain surface); union or difference;
   - *water*: flat cut to (min terrain under polygon − depth).
2. Merge with manifold3d (batch union / difference); 3MF parts = layer − terrain −
   higher-priority layers, so parts never overlap.
3. Printability: drop footprints < 0.3 mm², minimum feature height 0.4 mm, report counts.
4. Puzzle: numpy2stl `make_jigsaw_cutters` + `cut_jigsaw` (manifold3d) on the merged solid,
   grid chosen from piece size (bed), volume check.
5. App: async export task producing a zip (merged STL, 3MF, puzzle OBJs, report.json);
   Extrude-tab controls for scale mode, layers and puzzle.

## Success criteria

- Merged STL watertight, positive volume, winding consistent; 3MF parts disjoint.
- No feature outside the model rectangle; no floating or buried footprint.
- Cartagena, Granada + Alhambra, Breckenridge rebuilt at 1 px/mm and cut into puzzles with
  ≥ 99% volume conservation.

## Follow-ups from the v2 renders (2026-09-27)

Measured on Granada + Alhambra (797×575 px, 1:3472, 588 s, 1.07 M faces, 12 pieces):

- **Merged STL is not watertight** (success criterion missed): parts touch, so edges are
  used 4×. Finish with one manifold union of all parts (or the 3MF parts only) and gate the
  build on the watertight check. 29 buildings are still rejected as non-manifold.
- **Draped layers outweigh the terrain**: trails 425 k faces, green 233 k, roads 193 k vs
  terrain 200 k. Drape on the adaptive TIN (not the pixel grid) and dissolve each layer's
  touching slabs before the union.
- **Printability by scale**: at 1:3472 a 2 m trail is 0.6 mm wide. Per layer: widen to a
  minimum printed width (0.8 mm) or drop, reported as counts.
- **Time**: fetch + height enhancement dominate; one Overpass mirror hung on a 300 s connect
  timeout. Connect timeout ≈ 10 s with fast fail-over. Lossless simplify (86 s) and
  assemble (52 s) run per part in parallel.
- **Reproducibility**: write the full build spec into `report.json` and accept it back
  (`TerrainSession.build(spec)` / CLI), and keep the three cities as a slow regression set
  tracking faces, time, watertight and volume.

### Done (2026-09-27)

- Draped slabs are triangulated from the outline plus the adaptive terrain's own vertices
  (no fixed-area refinement), and each layer's touching features are dissolved into one
  slab first. Granada, all layers: 915 k faces (was 1.07 M), build 123 s (was ~240 s).
  The trails layer is still the largest: 4,293 trails, ~95 m of outline at model scale,
  which is geometry, not missed simplification.
- Watertight as STL: vertices that manifold3d keeps separate where solids touch at a point
  or edge are moved 0.001 mm apart (`_separate_contacts`), so welding cannot fuse them;
  the report's `watertight` is now the welded check. Granada: 0 non-manifold edges.
- Pitched roofs on concave footprints (29 Granada buildings) fall back to a flat roof at
  mid-roof height instead of being dropped.
- Printability: features narrower than 0.8 mm are widened (`widened` per layer); extruded
  heights are capped at 8 x footprint width (`clamped`). Both per-layer overridable
  (`min_width_mm`, `max_slenderness`).
- Overpass: 10 s connect timeout separate from the 300 s query budget (`geo2stl/osm.py`).
- Two-stage pipeline: `build_on_terrain` is the feature stage; `export.terrain_stage`
  the terrain stage; STL/OBJ/3MF/puzzle/city all run both (see F-ARCH).

### Done (2026-09-27, build speed: reduction + caches)

Granada (23,937 buildings, trails on, cached DEM + OSM): build 138 s -> 101 s cold,
8 s unchanged rebuild; pre-flight 37 s -> 21 s cold / 14 s warm; 3MF 31 s -> 5 s;
puzzle 39 s -> 17 s; OSM at other panel settings 979 s (refetch) -> 2 s (derived);
trails 40 s per build (Overpass) -> 0 s (cached).

- **OSM reuse** (`app/server/core/city_data.lookup_city_layers`): a missing
  (tolerance, min_area) entry is derived from a finer entry for the same or an
  enclosing bbox (features intersecting the bbox kept whole; buildings under min_area
  dropped; buildings / waterways re-simplified). Entries now carry a `{key}.params.json`
  sidecar (`geo2stl.cache.list_osm_cache_params`); older ones are found by probing
  common values. Same staleness rules; a buildings-only-stale candidate supplies its
  other layers. Pre-flight uses the same lookup.
- **Trails**: `OsmTrailsLayer.fetch` cached 7 days (`trails_osm`); the layer is off by
  default and fetched only when enabled; fetch keeps paths / tracks / bridleways /
  hiking routes and footways with `sac_scale` / `trail_visibility` / a name (no
  sidewalks, crossings, steps); `city_model.filter_trails` drops trails > 50 % within
  60 m of buildings or 6 m of roads (`trails_kept` / `trails_dropped`). Granada: 4,293
  -> 219 fetched -> 136 built.
- **Print-scale reduction** (`city_model.merge_flat_roofs`): flat, ground-standing,
  non-part buildings whose absolute top rounds to the same 0.1 mm layer and whose
  outlines are < 0.4 mm apart become one prism (union, closing 0.2 mm, simplify
  0.1 mm, lowest skirt to top). Why the absolute top: two roofs in the same print
  layer print as one surface; a wall between them is invisible. Hillside cities merge
  little (Granada 779 -> 580 solids); flat Cartagena 475 of 3,065 -> 229 (the
  fetch already dissolves touching same-height buildings in lon/lat).
- **Caches** (`city2stl/model_cache.py`; `MODEL_CACHE_VERSION` in every key, bump on
  geometry changes): layer polygons (shared with the pre-flight), layer union solids
  (keyed by polygons key, style, heightfield digest, landmark overrides / replaced
  footprints), terrain TIN, and the finished model. Polygons keep their GEOS
  precision grid across the cache (WKB drops it; slabs from grid-less copies failed).
- **Triangle removed from draped slabs** (`triangulate_polygon`): qhull Delaunay plus
  cavity re-triangulation with GEOS for outline edges it misses (no added points, so
  the same face count as Triangle's CDT: Granada roads 77,316, green 96,552 faces,
  identical; 0 rejected). A clip-the-TIN approach was tried first and rejected: 3x the
  faces and 3-6x slower. `heightfield_tin` keeps Triangle: it only triangulates
  unique integer pixel points with "Q" (no "p", no segments), and
  `segmentintersection()` runs only when inserting segments.
- **Vectorised**: `numpy2stl.core.extrude.prisms` (25k flat roofs in one pass),
  `Terrain.cells_inside` (replaces rasterio rasterize in `ranges_under`: 8 s -> 0.3 s),
  `assemble_parts` (only outlines overlapping a part, one padded union + difference),
  `numpy2stl.io.write3MF` (streamed string formatting, zlib level 1).
- Left: lossless simplification (35 s) and assembly (20 s) on a cold build; the
  "Topological inconsistency" message still printed comes from Triangle in
  `numpy2stl.processing.simplify.GEOS constrained Delaunay (no Triangle)` (caught; the region is kept as-is).

### Done (2026-09-29)

- Regression set: `tests/test_reference_cities.py` (`pytest -m slow`), baseline
  `tests/reference_cities_baseline.json`, run log `output/regression/reference_cities.jsonl`.
  All three cities watertight; puzzles keep 99.64–99.71 % of the volume (success criterion met).
- It found a `merge_flat_roofs` GEOS crash on Granada (fixed) and that test mode fakes the DEM.
- Cold build: Cartagena 108 → 47 s, Granada 183 → 92 s, Breckenridge 150 → 58 s (tiled TIN,
  prepared point tests, threaded simplify, faster contact separation and writers). Why:
  [mesh-pipeline decisions](../../decisions/mesh-pipeline.md).
