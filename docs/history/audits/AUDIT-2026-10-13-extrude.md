# Extrude tab audit — 2026-10-13

Audit of the Extrude tab: the panel (`app/client/static/js/vue/components/views/ModelContainer.vue`),
its control wiring, and the 3D viewer (`app/client/static/js/modules/export/model-viewer.js`,
`city-fullmodel.js`). Three parallel reviewers (control wiring, viewer code, design rules) plus a
live click-through on the Granada city region. This is a snapshot: fixes move to
[issues.md](../../issues.md), work items to the plans. Line numbers are as of this date.

## Fixed the same day

- **The finished City Model was drawn inside out.** `city-fullmodel.js::toViewerFrame` swapped
  every triangle's winding on the assumption that (x, y, z) → (x, z, N − y) mirrors the mesh. It
  is a rotation (determinant +1). Measured on Granada's parts file: every part's signed volume
  was positive in model mm and negative in the viewer frame (buildings +31,242 → −31,242). With
  front-face culling the viewer drew each solid's inner faces, which is the "buildings are only
  outlines" look. The swap was removed and a signed-volume test added
  (`tests/js/cityFullmodel.test.js`). The exported 3MF was always correct (all nine parts have a
  positive signed volume).

- **Every item under Bugs, Risks and Inconsistent defaults below** was fixed later the same day
  (viewer: `model-viewer.js`, `city-fullmodel.js`; panel: `export-handlers.js`,
  `event-listeners-export.js`, `ModelContainer.vue`, `app/server/core/export.py`). The
  cross-section is now cut from the model's own heightfield. Defaults follow the server.
- **Granada Cathedral printed as one 45 m prism.** The `churches` layer re-extruded the
  cathedral's outline over its 41 building parts. Fixed by
  `city2stl/city_model.py::drop_building_copies`. In the live build the model has no `churches`
  part and the buildings within 20 mm of the cathedral stand at 105 different heights.
- **Puzzle cut lines vanished under the finished model.** The lines are drawn without depth
  testing, and three.js orders equal render orders by material id, so the City Model parts
  (whose materials are made after the cuts) painted over them until a puzzle setting forced a
  redraw. `model-viewer.js::updatePuzzlePreview` now gives the lines `renderOrder = 1`.

## Bugs: wrong output

- `modules/export/export-handlers.js:499-512` — the cross-section request sends neither
  `mm_per_pixel` nor `z_mode`. The server falls back to 1.0 mm/px (`app/server/core/export.py:761`),
  so the slab's length and thickness ignore the Width slider, and it is always scaled to
  `model_height × exaggeration`, unlike the model in True/Auto mode.
- `export-handlers.js:263-268` — Clearance 0 exports as 0.3 mm (`parseFloat(...) || 0.3`); the
  field allows `min="0"`. `model-viewer.js:494` already handles this case for the preview.

## Bugs: broken behaviour

- `model-viewer.js:1091-1097` — changing the colormap in Normals mode throws
  (`MeshNormalMaterial` has no `.color`). After Satellite, the saved material never gets
  `vertexColors` back, so turning Normals off shows untextured terrain.
- `model-viewer.js:1124-1141` — satellite race: the texture is applied after the fetch without
  checking that the colormap is still `satellite`.
- `model-viewer.js:798, 826, 836` — a new preview key only arms a 1.5 s timer and leaves
  `_full.key` on the old key; an old build finishing in that window is drawn with the new frame
  and labelled "finished model as printed".
- `model-viewer.js:831` vs `:653` — a failed preview does not stop the background build; its
  poll overwrites the error status with stale text, and `_showCityFull` can still hide the
  terrain.
- `model-viewer.js:167-168` — auto-rotate turns only `terrainMesh`. Buildings, city parts, cut
  lines and bed outline stay still; with the finished model shown it rotates a hidden mesh.
- `modules/events/event-listeners-export.js:99-103` — Wireframe does not set `needsRender`, does
  not reach the quick view or the city parts, and is lost when Normals is turned off.
- `model-viewer.js:1149-1161` — Normals covers `terrainMesh` only, which is hidden while the
  finished model is shown.
- `ModelContainer.vue:199-215` — editing a City model layer (on/off, multiplier) does not update
  the 3D view: nothing listens to `cityLayer_*`, and layers are not in the preview body, so the
  duplicate-request check would skip a re-preview anyway.
- `event-listeners-export.js:88-91` — changing knob shape or clearance does not redraw the
  puzzle cut lines (`_puzzleGrid` reads both; the redraw list omits them).
- `ModelContainer.vue:615-625` — a saved "Custom…" bed is restored with its W×H row hidden:
  `onMounted` fires `change` before `setupEventListeners` attaches (`app.js:238`).

## Risks

- `model-viewer.js:884` — the preview terrain is hidden even when the parts file has no
  `terrain` part, leaving the scene without ground.
- `model-viewer.js:795` → `:635` — an exception in `_showCityFull` lands in
  `previewModelIn3D`'s catch: a good preview is marked failed, export is disabled, and the
  geometries already built are not disposed.
- `model-viewer.js:165-175` — the render loop never pauses while the tab is hidden.
- `model-viewer.js:155-163` — no ResizeObserver; a window resize on another tab leaves the
  canvas at the wrong size.
- `model-viewer.js:597, 959` — a preview superseded by `clearViewerModel` never settles its
  ticket, so an identical later body is skipped as a duplicate.
- Reset camera (`model-viewer.js:304`) fits the terrain only, not the buildings or city parts.

## Inconsistent defaults

| Control | Markup | JS fallback | Server |
|---|---|---|---|
| `exportBaseHeight` | 10 | 5 (`model-viewer.js:511`) | 5 (`export_params.py:139`); cross-section 3 |
| `mmPerPixel` | 0.35 | 1.0 (`model-viewer.js:513`, `ModelContainer.vue:434`) | 1.0 |
| `exportModelHeight` | 30 | 30 | 20 (`export_params.py:138`, `export.py:757`) |
| `splitCols` / `splitRows` | 4 | 3 (`export-handlers.js:274`, `model-viewer.js:1215`) | — |
| `cityPieceMm` | 210 | 200 (`export-handlers.js:311`, `model-viewer.js:1218`) | — |

## Design (against `design-guidelines.md` and `docs/design-guidelines.md`)

- **Missing, asked for by the user:** a show/hide switch and a colour per model part. Part
  colours are fixed in `city-fullmodel.js::PART_COLORS`; only buildings follow a control, and
  that control is on the Edit tab (`layerBuildingsColor`). Proposed home: a "Model parts" group
  in the 3D view tool, built on `settings/SetRow.vue` like the Edit tab's View rows. Each part
  mesh already carries its layer name (`mesh.name`).
- **Duplicates:** the City model layers table and Edit › City Composite write the same
  `cityLayer_*` controls under two names ("Extrude ×" / "Building height"); the Terrain/City
  puzzle checkboxes repeat the Model card's Split switch; Resolution/Exaggeration/Fit height
  repeat the Width and Height sliders as raw numbers; Edit's "Height" (`paramDepthScale`) and
  Extrude's "Height" (`exportExaggeration`) are different settings with one name; the bed-fit
  line appears twice; the scale line appears in the card and the viewer overlay.
- **Wrong section shown:** City model layers shows on terrain regions; Terrain puzzle is offered
  on city regions (and then the preview draws a grid the download ignores); Puzzle details shows
  while Split is off; the format selector stays visible while Split is on, though the puzzle zip
  ignores it.
- **Old form style:** six of seven optional sections use `param-grid` / plain checkboxes instead
  of the iOS rows (`SetRow.vue`). Hard-coded hex colours instead of tokens; card radius 18 px vs
  14 px in ToolSwitches.
- **Jargon:** "True scale × exag.", "3×3 median", "Sea-level cap", "Solid mesh", "Viridis";
  Cross-section asks for a typed latitude, and its labels read "Latitude (horizontal)".
- **View controls in the panel:** Reset view, Auto-rotate and Wireframe belong in a corner of the
  canvas; colours stay in the panel.
- **Initial camera** frames the Granada model so tight that its edge and the axis labels are cut
  off.
- **Accessibility:** Columns, Rows, Knob width/depth, Clearance, Cut along, Cut at, Slab depth and
  the label text field have no associated label; two buttons lack `type="button"`; the step tabs
  carry no `aria-selected`.

## Checked and fine

- Every control id in the panel is read by a module; no module reads a missing id; no duplicate
  ids.
- Preview field names match the server (`export_params.py`); city layer modes and
  `height_scale` / `offset_mm` match `city2stl/city_model.py::DEFAULT_LAYERS`.
- Quick view, finished model and puzzle cut lines share one coordinate frame.
- Disposal in `clearViewerModel`, `_replaceMesh`, `_removeCityFull` and `_removeCityQuick` is
  complete.
- The jigsaw drawn in the preview (`puzzle-cuts.js::jigsawCuts`) is a port of the only
  parameterised jigsaw in the Python code, `numpy2stl/applications/puzzle.py::jigsaw_outlines`
  (knob width, depth, shape, clearance, border, per-cut edges); `notebooks/cut_stl.ipynb` only
  has straight plane cuts.
