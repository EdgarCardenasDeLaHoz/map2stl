# SOP — City and terrain models, and jigsaw puzzles

Rewritten 2026-09-27 after the fixes that came out of the first run (2026-09-26). Plans:
`docs/plans/F-CITYMODEL-vector-city-model.md`, `F-ARCH-consolidation.md`,
`F-UX-sop-followups.md`. Outputs of the reference run: `output/renders_v3/<city>/`
(gitignored).

## 1. Reference results (2026-09-27)

Built at 1 DEM pixel = 1 mm, SRTM 30 m, Vertical *auto*, 3×3 median, all city layers,
puzzle pieces ≤ 200 mm.

| City | Box | Model | Scale | Faces | Watertight | Pieces | Build + cut |
|---|---|---|---|---|---|---|---|
| Granada + Alhambra | 2.8 × 2.0 km | 796 × 574 × 77 mm | 1:3,472 true | 932 k | yes | 12 (4×3) | 441 s |
| Cartagena | 5.0 × 5.0 km | 983 × 980 × 49 mm | 1:5,083 true | 513 k | yes | 25 (5×5) | 402 s |
| Breckenridge | 8.2 × 8.9 km | 771 × 841 × 133 mm | 1:10,539 true | 1.08 M | yes | 20 (4×5) | 474 s |

Before the fixes (same inputs): 1.07 M / 630 k / 1.57 M faces, none watertight after an
STL round trip, 588 / 851 / 936 s.

Each zip holds `<city>.stl` (merged solid), `<city>.3mf` (one part per layer, for
multi-colour), `puzzle/*.obj` + `<city>_puzzle.3mf`, and `report.json` (scale, tolerances,
per-layer counts and timings, checks).

## 2. The process

**Prerequisites**: venv (`scripts/setup-venv.ps1`); OpenTopography key in *Keys* (any 30 m
source); server running (`Start 3D Maps.bat`).

1. **Pick the area** (Explore): select or draw the region. Check that landmarks are
   inside the box with margin (Granada's saved box cut through the Alhambra).
2. **Load terrain** (Edit): click a preset — *City*, *Mountain* or *Coast* — then
   **Load DEM** (top of the panel). Presets set source, resolution, vertical mode, layers
   and puzzle options; ↩ reverts. Use SRTM 30 m or Copernicus 30 m (the local file is
   ~90 m). Avoid Copernicus **DSM** under OSM buildings: it already contains roofs.
3. **Terrain edits** (optional, Edit → Composite): water depth, land cover, curve edits.
   These are the *terrain stage*. The Composite's buildings / roads / waterways / walls
   toggles are 2D preview only; in 3D those come from the City Model layers.
4. **City data** (Edit → Cities → *Load Cities*): first fetch 1–5 min, cached after.
   Check the **Buildings panel**: height sources, histogram, the "> 20 % default height"
   warning, the tallest list. Fix wrong heights with the per-building override — it is
   sent with the build.
5. **Model** (Extrude): the scale line shows "1 mm = X m" and the vertical exaggeration;
   the bed outline shows fit. Vertical: *auto* = true scale below a 20 km diagonal, fit
   to *Height* above; override with *true* × exaggeration or *fit*. Smoothing 3×3.
6. **Pre-flight** (Export → ✈ Pre-flight check, *City model* or *Terrain puzzle*,
   seconds): size vs the bed, scale and vertical exaggeration, piece grid, per-layer
   shapes with the `widened` / `capped` counts the build will report, thinnest feature,
   tallest spike, estimated faces, filament (g PLA) and print time, and a warning list.
   Fix what it flags before building (layers not yet cached are listed, not counted).
7. **Puzzle options** (Export → Split / Puzzle; they apply to the City Model puzzle too):
   knob shape (*classic* rounded, *dovetail*, *rectangular*), engraved piece ids + north
   arrow on the underside (on by default), *Lay out on plates* (one 3MF per bed). With a
   puzzle on, the preview draws the cut lines in red: drag one to move that cut (it stops
   where a piece would get too small for its knobs); *Reset cuts* goes back to equal pieces.
8. **Build** (Export → City Model): toggle layers per situation (see §3), keep *Puzzle*
   on with max piece = bed − 10 mm (set from the printer). One build writes the merged
   STL, the per-layer 3MF, the puzzle (pieces in place + laid-out plates) and
   `report.json`.
9. **QA**: open `report.json` — `check` (size vs bed, faces, watertight, widened/clamped
   per layer, filament and time from the real mesh, warnings), `merged.watertight`,
   `scale`, `puzzle` (grid, method, timings). In the slicer: size vs bed, no "errors
   fixed", tallest spike.

Terrain-only models (no city) use the same route: *Export → STL/OBJ/3MF* or *Puzzle* build
the city model with no layers, so they get the same adaptive mesh and scale. A terrain-only
puzzle is cut the fast way (pieces meshed straight from the heightfield, see §4).

The Extrude preview is the adaptive mesh capped at 150 k faces (tolerance raised until it
fits); the HUD shows the tolerance reached. The downloaded file is always the full-tolerance
mesh.

## 3. Layer choices

| Situation | Layers on | Notes |
|---|---|---|
| Historic city (Granada) | all | trails are the heaviest layer (4,293 paths); drop them for a smaller file |
| Coastal city (Cartagena) | buildings, landmarks, roads, water, green | Coast preset caps the sea at 0 m |
| Mountain town (Breckenridge) | buildings, roads, water, trails | green is 612 k faces on steep ground — leave it off |
| Region > 20 km | none (terrain), water | see §6 |

Per layer (Export → City Model table): enabled, mode (extrude / raised / engraved / water),
height or depth, line width. Printability rules apply to every layer: features narrower
than 0.8 mm are widened, extruded heights capped at 8 × footprint width (reported).

## 4. How the model is built (for troubleshooting)

1. **Terrain stage** (raster): DEM from the request (composite spec, edited values, or
   the loaded DEM handle) → fill → median → sea-level cap → vertical scale → label and
   contour engraving → heightfield in mm.
2. **Terrain mesh**: adaptive TIN within max(0.05 mm, half the source's 1 m step at model
   scale) of every pixel — 200 k faces instead of 1.8 M for Granada.
3. **Feature stage** (vector): OSM outlines projected to mm, simplified 0.05 mm, snapped
   0.01 mm, clipped to the box; extruded features sit on the highest ground under them
   with a skirt down; draped layers are one slab per connected area built on the
   terrain's own vertices; water is cut flat.
4. **Merge** (manifold3d): union → contacts separated by 0.001 mm (watertight after STL
   welding) → lossless coplanar merge.
5. **Puzzle**: piece outlines from the bed size (or dragged cut positions), then
   - terrain-only (*mask* path, automatic): each piece's top is the terrain TIN's vertices
     inside the outline plus the outline itself (split at the pixel spacing), triangulated
     together, walls on the exact outline, flat bottom — no boolean engine;
   - with city layers (*boolean* path): manifold3d intersection of the merged model with
     the cutter prisms.
   Both check the volume (pieces must sum to the model minus the clearance gaps). Then the
   id and north arrow are cut 0.6 mm into each piece's underside (mirrored so they read
   with the piece turned over), and the pieces are packed onto beds for the plate 3MFs.
   `puzzle_method` / `puzzle.method` forces either path.

## 5. Known limits

- Pitched roofs on concave footprints are flat at mid-roof height; no `building:part`
  (towers, domes, spires) yet — cathedrals and city halls print as their footprint prism.
- Draped layers duplicate terrain detail on steep ground (Breckenridge green).
- Heights outside Spain/US lidar coverage are thin; check the default-height warning.
- The city fetch is a single blocking step (no per-layer progress yet).

## 6. Large regions (> 20 km)

Buildings are below print resolution at these scales; build terrain only, Vertical *auto*
(fit to *Height*), rivers from the composite water depth or the `waterways` layer. A
dedicated large-region and hydrology workflow is the next SOP section to write.

## History

2026-09-26 run found: city export terrain inside-out, buildings exaggerated 16× and not
clipped, buildings floating on slopes, landmark layers ignored, city export ignoring the
size settings, jigsaw cutter x/y swap dropping 40 % of non-square models, dead puzzle
settings. All fixed by F-CITYMODEL (vector city model, one scale, jigsaw puzzles).
