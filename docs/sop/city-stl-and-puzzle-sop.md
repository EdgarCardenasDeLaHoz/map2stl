# SOP — City and terrain STLs, and puzzle splits

Written 2026-09-26 from an end-to-end run of Cartagena, Granada + Alhambra and Breckenridge
through the app (UI walkthrough for Cartagena; the same HTTP endpoints, scripted, for all
three). Outputs: `strm2stl/output/renders/<city>/` (gitignored).

## 1. What was produced

| City | Box | Terrain STL | City model (terrain + buildings) | App puzzle 3MF | Jigsaw OBJs |
|---|---|---|---|---|---|
| Cartagena | 5.0 × 5.0 km | 200×199×35 mm, 700k faces, 35 MB, watertight | 3MF + STL, 152×152×**106** mm, 3,055 buildings | 3×3, 66 mm pieces, 9/9 watertight | 3×3, 67 mm, 9/9 watertight, 99.9% of volume, 5.5 min |
| Granada + Alhambra | 2.8 × 2.0 km (box widened east to −3.578 to take in the Alhambra and Generalife) | 200×144×35 mm, 333k faces | 3MF + STL, 161×125×37 mm, 13,897 buildings (529 in the Alhambra/Generalife) | 3×3, 9/9 watertight | 3×2, 66–72 mm, 6/6 watertight, 99.9% of volume, 66 s |
| Breckenridge | 8.2 × 8.9 km | 183×200×35 mm, 471k faces | 3MF + STL, 150×164×35 mm, 3,973 buildings | 3×3, 9/9 watertight | 3×3, 9/9 watertight, 99.8% of volume, 2 min |

Per-city `report.json` holds the mesh checks; `<city>_preview.png` is a shaded view.

## 2. SOP — the process as the app works today

**Prerequisites**: venv set up (`scripts/setup-venv.ps1`); OpenTopography key in
Keys (needed for any 30 m source); server running (`Start 3D Maps.bat`).

1. **Explore — pick the area**
   - Select the region in the left list (or draw a new box: ▭ tool → *+ New Region*).
   - Check the box covers everything wanted. *Granada's saved box stops at −3.5867 and
     cuts through the Alhambra; it had to be widened.*
2. **Edit — terrain**
   - Right panel → *Fetch Layers* → *DEM Source*.
   - Source: **SRTM 30m (Global)** for cities (the default *Local SRTM H5* is ~90 m:
     Cartagena came back as 54×54 real samples upsampled to 600×600). Avoid Copernicus
     **DSM** under OSM buildings: it already contains rooftops and trees.
   - Resolution 600; Depth 0.5 / Water 0.05 / Subtract ✓ (defaults).
   - *Projection* section: Cosine (default) is right for city scale.
   - Click **Load DEM** (~10 s).
3. **Edit — buildings** (skip for pure terrain)
   - *Fetch Layers* → *Cities* → **Load Cities**. First fetch of an area takes **2–10 min**
     (nine OSM layers fetched one after another, then height rasters); later loads are cached.
   - Watch the status line; the button stays greyed while it runs.
4. **Extrude — model parameters**
   - *Fetch* tab: Resolution (mm/px), Height (mm), Base (mm), Exaggeration, Sea-level cap,
     Solid mesh. **Set Resolution so the model fits the bed**: `bed_mm / DEM_pixels`
     (200 / 600 ≈ 0.33). The default 1.0 makes a 600 mm model.
   - Preview renders automatically (can take 20–30 s and freeze the page on 700k faces).
5. **Export**
   - *Export* tab → **STL**: terrain only.
   - Terrain + buildings: *City Export* → **Export 3MF** (always 150 mm wide; ignores
     Resolution). For an STL, open the 3MF in a slicer/Blender and export STL.
   - Puzzle: *Split / Puzzle* → set Cols/Rows → **Export Puzzle 3MF**.
6. **QA before printing**: open in the slicer, check size against the bed, watertight /
   "errors fixed" count, tallest spike, and that buildings sit on the terrain.

## 3. Review of the process — what to change

### Friction observed

- **No single path to "STL with buildings".** Terrain STL, City 3MF and Puzzle 3MF are three
  unrelated buttons with three different size rules (mm/px; fixed 150 mm; mm/px).
- **Load DEM is buried** two collapsed sections deep; the prominent blue button on the Edit
  tab is *Save settings* (icon only). "Auto" means auto-save, not auto-load.
- **Defaults produce an unprintable model**: 1 mm/px → 600 mm; 90 m terrain source.
- **Slow, opaque city fetch**: 9 Overpass layers in series (≈15–20 s each) + nDSM/WSF3D/GHSL
  with 120 s timeouts; the UI shows only "Fetching from OpenStreetMap…".
- **Printer selection doesn't drive anything** (Prusa 250×210 selected; model still 600 mm).
- **Extrude view shows "Load a DEM in the Edit tab"** while a DEM is loaded and the mesh is
  building; the preview mesh was built twice per visit (duplicate request).
- **Region box can't be checked against landmarks** before a long fetch (Alhambra cut-off).

### Recommended process

1. **One "Model" step with a target size**: choose printer (or W×D mm) → the app sets
   mm/px, shows the scale bar ("1 mm = 25 m") and a true-scale vertical option.
2. **A single "Build" that makes the chosen outputs** from one set of settings: terrain STL,
   city STL/3MF (buildings unioned onto terrain), puzzle pieces, all at the same scale.
3. **Presets**: *City* (SRTM 30 m, buildings on, 0.33 mm/px, 1.5× vertical), *Mountain*
   (SRTM 30 m, buildings off, 2× vertical), *Coast* (sea-level cap, water depth).
4. **Background jobs with a progress panel** for city fetch and exports (per-layer ticks,
   cancel, retry on mirror failure); parallelise the Overpass layers.
5. **A pre-flight check panel** before export: size vs bed, thinnest feature (mm), tallest
   spike, piece count, estimated print time/filament, watertight.

### UI changes

- **Edit tab**: make **Load DEM** the primary button at the top; label the save icon;
  collapse rarely-used sources; show source resolution ("~90 m → 54×54 real samples").
- **Box editor**: landmark search ("Alhambra") that snaps/extends the box; show the box
  over satellite tiles; warn when a named POI lies within 200 m of an edge.
- **Buildings panel**: height-source summary ("160 OSM, 2127 raster, 768 default 10 m") with
  a histogram; clamp slider ("cap at N m"); list/select tallest buildings; toggle walls,
  fortifications, churches into the model.
- **Extrude tab**: bed outline drawn under the preview; decimated preview (≤150k faces)
  so the page stays responsive; show vertical exaggeration relative to true scale.
- **Export tab**: one *Outputs* checklist (STL / 3MF / OBJ / puzzle) + one **Build** button;
  file names include city, scale and date.

## 4. STL review — findings and improvements

### Findings (measured)

- **Terrain STLs are clean**: single watertight bodies, consistent winding.
- **Terrain is over-dense and under-informed**: 330k–700k faces from 30 m data upsampled
  3–4× (Cartagena: ~165 real samples across 600 px). Flat areas (sea is 55% of Cartagena's
  top surface) are fully tessellated. 35 MB for a mostly flat model.
- **Vertical scale is arbitrary**: relief is always normalised to *Height* (30 mm).
  Cartagena (169 m relief), Granada (248 m) and Breckenridge (1,349 m) all come out 30 mm
  tall; true scale at 200 mm across is ~7 mm, 18 mm and 30 mm respectively.
- **City Export terrain is inside-out**: `_terrain_mesh` returns a closed mesh with negative
  volume (all normals inward), and the terrain simplification then leaves the winding
  inconsistent (97–100% of top faces point down in all three 3MFs). Slicers may repair or
  misprint it.
- **Buildings exaggerated ~16× relative to the ground**: `building_z_scale` 0.5 mm per metre
  against ~0.03 mm per metre horizontally → Cartagena's 202 m towers are 101 mm needles.
- **Buildings are not clipped to the box**: Granada's buildings span 161×125 mm on a
  150×108 mm base — they overhang the edge.
- **Buildings float or sink on slopes**: each base is set at one terrain height; on the
  Albaicín/Alhambra hills part of every footprint is in the air.
- **Junk slivers**: 31 of 3,186 building bodies in Cartagena are open (24 single triangles).
- **Heights are thin outside Spain**: Granada 98% from OSM levels; Cartagena 25% default
  10 m; **Breckenridge 95% default 10 m** — no raster height source covered it.
- **Landmark structures are dropped**: City Export uses only `buildings`; the Alhambra's
  walls and towers (`walls`, `fortifications` layers, already fetched) never reach the model.
- **City Export ignores the size settings**: always 150 mm wide, terrain downsampled to
  150 px, while the terrain STL is 200 mm — the two cannot be combined or compared.

### Improvements (in priority order)

1. **Fix correctness**: flip City Export terrain faces (or `fix_normals`) and make the
   simplifier preserve winding; clip buildings to the box; drop degenerate bodies.
2. **One scale for everything**: horizontal mm/px from the bed size; vertical =
   true scale × exaggeration (default 1.5–2× for cities, 1× for mountains); buildings on the
   same vertical scale (optionally with their own multiplier, default ≤ 2×).
3. **Seat buildings properly**: extrude each footprint down to below the lowest terrain point
   under it (or to the base) and boolean-union with the terrain → one watertight body.
4. **Printability rules**: minimum footprint 1.2 × nozzle (≈0.5 mm) after scaling — drop or
   merge smaller; cap slenderness (height ≤ 8× min width) or clamp tall towers; minimum
   wall 0.8 mm.
5. **Adaptive terrain mesh**: triangulate from the real-resolution grid (or simplify with an
   error bound, e.g. 0.05 mm) instead of 1 vertex per upsampled pixel — expect 5–20× smaller
   files with no visible loss.
6. **Heights**: use USGS 3DEP lidar nDSM for US areas (Breckenridge), keep OSM levels first,
   then WSF3D/GHSL; show the default-height fraction as a warning above ~20%.
7. **Landmarks**: include walls, fortifications, towers and churches as extruded features
   (with their own height rules); allow a manual height override per building.

## 5. Puzzle splitting — steps, settings, improvements

### Steps today

1. Load DEM (and set model parameters) as above.
2. *Export → Split / Puzzle* → Cols, Rows, and the border options → **Export Puzzle 3MF**.
3. The server slices the *terrain heightmap* only (no buildings) into Cols×Rows rectangles
   and writes one 3MF with one object per piece.
4. There is **no OBJ output** and no per-piece files; to get OBJs, split the 3MF in a slicer
   or Blender. (For this review the numpy2stl jigsaw cutter was run directly — below.)

### Settings (UI → request field → what it actually does)

| UI | Field | Default | Actual effect |
|---|---|---|---|
| Cols / Rows | `split_cols` / `split_rows` | 4 / 4 (server default 3) | Straight grid slicing; max 64 pieces |
| Connector size (mm) | `connector_size_mm` | 50 | **No effect** — computed into an unused variable |
| Connectors / edge | `connectors_per_edge` | 10 | Sets the depth (px) of the edge strips; not a count of anything |
| Border height (mm) | `border_height_mm` | 1 | Raised lip height on edge strips |
| Border offset (mm) | `border_offset_mm` | 5 | **No effect** (read, never used) |
| Include border | `include_border` | ✓ | Adds the lip |
| (none) | model params | from Extrude | Resolution/Height/Base/Exaggeration apply |

"Tabs" and "slots" are raised and lowered **height strips** along the cut edges; pieces do
not interlock sideways. Pieces are laid out in place (not spread for printing).

### The better path that already exists

`numpy2stl.applications.puzzle.make_puzzle_model` + `processing.boolean.cut_puzzle_pieces`
make real tongue-and-groove pieces (0.4 mm clearance) by boolean intersection. Run on the
terrain STLs here: every piece a single watertight body, one OBJ each
(`<city>_terrain_jigsaw_<cols>x<rows>/`). Cost: 1–6 min per model with pymeshlab (6 min for
Cartagena's 700k faces); manifold3d would be faster but is not installed.

**Bug found**: `make_puzzle_model` builds its cutters with x and y exchanged relative to the
`width` tuple. On a square model this is invisible; on Granada (200×144 mm) the cutters
covered 150×205 mm, the eastern 50 mm strip was never cut, and **40% of the model was
silently dropped** — every surviving piece still reported watertight. Passing `(y, x)`
fixes it (99.9% of volume kept). Any automated cut must check that the pieces' total
volume matches the source.
Also: square pieces on a non-square model leave slivers (Granada 3×3 → 11 mm strips);
the grid must follow the aspect ratio (Granada → 3×2).

### Improvements

1. **Wire the jigsaw path into the app** and replace the height-strip "tabs"; expose knob
   width, knob depth, clearance (default 0.3–0.4 mm) and shape (rectangular / dovetail /
   classic rounded).
2. **Cut the heightmap, not the mesh**: rasterise each piece's outline (with knobs) into a
   mask and mesh each masked grid directly — seconds instead of minutes, always watertight,
   no boolean engine needed. Keep booleans only for city models with buildings.
3. **Size pieces from the bed**: "pieces must fit 200×200 mm" → the app picks Cols×Rows;
   show the cut lines over the preview and let the user drag them (avoid cutting through
   landmarks like the Alhambra palace or a summit).
4. **Outputs**: per-piece OBJ/STL + a 3MF, pieces spread on the plate, engraved piece IDs
   (r1c2) and a north arrow on the underside, optional frame/tray.
5. **Remove or fix the dead settings** (`connector_size_mm`, `border_offset_mm`) and rename
   the UI labels to what they do.
6. **Include buildings** in puzzle pieces (cut the unioned city model), keeping each
   building whole on one piece where possible.

## 6. Bugs found during the run

- City Export terrain inside-out; simplifier breaks winding (`city2stl/mesh.py`).
- Buildings not clipped to the bbox (Granada overhang).
- Preview mesh built twice per Extrude visit (two identical `/api/export/preview` calls).
- `read_array_cache` fails with `[Errno 22] Invalid argument` for a WSF3D key (log line).
- Puzzle `connector_size_mm` and `border_offset_mm` unused; leftover `print(tol)` in
  `numpy2stl/applications/puzzle.py`.
- `make_puzzle_model` cutters have x/y swapped → silent loss of up to 40% of a
  non-square model.
- Two of three Overpass mirrors refuse requests (406/429) — health check falls through
  correctly, but the UI does not show it.
