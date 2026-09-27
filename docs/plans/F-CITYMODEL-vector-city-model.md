# F-CITYMODEL — Vector city model: one scale, seated features, merged solid

Status: in progress 2026-09-26. Replaces `city2stl.mesh.generate_city_3mf` and the app's
height-strip puzzle. Findings behind it: `docs/sop/city-stl-and-puzzle-sop.md`.

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
