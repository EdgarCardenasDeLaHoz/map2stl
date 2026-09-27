# F-UX — Workflow and UI follow-ups from the city STL / puzzle SOP

Status: approved 2026-09-27 ("do all now"). Source: `docs/sop/city-stl-and-puzzle-sop.md`
§3–§5, items not covered by F-CITYMODEL. Order: consolidation (F-ARCH) first for anything
that touches library code; client-only items run alongside it.

## Batch 1 — client only (parallel with F-ARCH)

- **Edit tab**: *Load DEM* is the primary action at the top of the panel; the save icon gets
  a label; rarely used DEM sources collapse under "More sources".
- **Buildings panel**: height-source summary (OSM tag / levels / lidar / raster / default)
  with a small histogram; warning when > 20% of buildings use the default height; list of
  the tallest buildings (click to select on the map); per-building height override that
  is sent with the city build as `layer_data`.
- **Extrude tab**: remove the stale "Load a DEM" message while a mesh builds; draw the
  printer-bed outline under the preview; show the vertical exaggeration relative to true
  scale and the model's real-world scale ("1 mm = 3.5 m").
- **Printer selector drives sizing**: the selected bed sets the default puzzle piece size
  and shows "fits / needs N×M pieces".
- **Presets**: City / Mountain / Coast set DEM source, resolution, vertical mode and
  exaggeration, layer toggles and puzzle options in one click.

### Batch 1 — done 2026-09-27 (client only)

- **Edit tab** — `DemSettingsPanel.vue`: `#loadDemBtn` moved to the top row (full-width
  primary); `#saveRegionSettingsBtn` now "💾 Save settings", secondary; "Auto" → "Auto-save".
  `#paramDemSource`: SRTM 30m / Copernicus 30m / Local H5 first, the rest in a "More
  sources" optgroup (`dem-main.js:populateDemSources`, `PRIMARY_DEM_SOURCES`; the markup
  fallback in `FetchLayersSection.vue` matches). Option values unchanged. The async-button
  helper now restores the button's own label after loading instead of "⟳ Load".
- **Buildings panel** — `CityBuildingsPanel.vue` + pure `modules/layers/building-heights.js`:
  source summary (OSM tag / levels / lidar / raster / default / unknown; tooltip lists the
  raw `height_source` values), SVG histogram, > 20 % default warning, tallest-10 list
  (click → `window.selectCityBuilding`), override editor for the selected building.
  Overrides live in `appState.cityHeightOverrides` (reset when a new building set loads)
  and `exportCityModel()` sends `layer_data: {buildings: FeatureCollection}` with
  `height_m` replaced and `height_source: "user_override"` — only when an override exists
  and the Buildings layer is on. The 2D map rendering still shows the fetched heights.
- **Extrude tab** — empty state reads `appState.modelPreviewState` (no DEM → "Load a DEM",
  DEM → "Building mesh…", failure → points at the status line). Bed outline drawn under
  the model (`model-viewer.js:updateBedOutline`, orange + "(too small)" when the model does
  not fit), redrawn on bed change. Scale line under the status: "1 mm = 11 m (1:11,440) ·
  vertical 1× (true scale)", from `modules/export/print-scale.js:modelScale` (a port of
  `city_model.choose_scale`; fit mode uses the DEM's vmin/vmax, the server uses the
  median-filtered DEM, so fit-mode exaggeration is approximate). `exportZMode` and
  `exportMedian` now trigger the auto-rebuild (they did not before). The viewer HUD moved
  to the top-left so it no longer overlaps the status box. `updatePrintDimensions` scale
  and "Bed fit" now use the footprint in mm (they used pixels, ignoring mm/px).
- **Printer drives sizing** — `ModelContainer.vue`: bed change sets `#cityPieceMm` to
  min(W, H) − 10 mm until the user types in it; City Model and Split/Puzzle sections show
  "✓ W×D fits the bed" or "⚠ needs C × R pieces (≤ P mm each)" (`piecesNeeded`, same grid
  rule as `puzzle.plan_grid`); Split/Puzzle has a "Use" button that copies C × R into
  Columns/Rows.
- **Presets** — `modules/ui/workflow-presets.js` (City / Mountain / Coast definitions +
  `applyFields`, which sets controls, fires input+change, skips disabled/missing options
  and returns an undo list). Shown as buttons under Load DEM (`WorkflowPresetBar.vue`) and
  as a "Workflow" group in the existing `#presetSelect`; revert (↩) restores the undo list.
  Not done: presets do not change mm/px, so City at 1000 px and 1 mm/px is a 1 m model cut
  into many pieces — pick the piece count / resolution with the bed readout.
- Tests: `tests/js/printScale.test.js`, `buildingHeights.test.js`, `workflowPresets.test.js`.

## Batch 2 — needs server work (after F-ARCH)

- **Landmark search** for the region box (geocoding endpoint) and a warning when a named
  POI lies within 200 m of the box edge.
- **Source resolution**: DEM response reports the native sample count; the Edit tab shows
  "~30 m → 165×165 real samples, upsampled to 600×600".
- **Default DEM source**: SRTM 30 m (or best available) instead of the ~90 m local file.
- **City fetch as a background job** with per-layer progress, mirror state and cancel.
- **Printability rules** in the city model: minimum wall width (0.8 mm) and slenderness
  cap (height ≤ 8× min width, clamp or flag), reported per build.
- **Pre-flight report + panel**: size vs bed, thinnest feature, tallest spike, piece
  count, estimated filament/print time, watertight — computed server-side, shown before
  download.
- **Decimated preview** (≤ 150k faces) using the adaptive terrain mesh.
- **Puzzle**: fast heightmap-mask cutting for terrain-only puzzles; engraved piece IDs and
  a north arrow on the underside; pieces laid out on the plate; knob shapes (rectangular,
  dovetail, rounded); draggable cut lines (non-uniform grid) in the preview.
