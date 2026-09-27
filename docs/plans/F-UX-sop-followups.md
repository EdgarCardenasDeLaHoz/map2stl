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

- ~~**Landmark search** for the region box (geocoding endpoint) and a warning when a named
  POI lies within 200 m of the box edge.~~ Done (part A, below).
- ~~**Source resolution**: DEM response reports the native sample count; the Edit tab shows
  "~30 m → 165×165 real samples, upsampled to 600×600".~~ Done (part A).
- ~~**Default DEM source**: SRTM 30 m (or best available) instead of the ~90 m local file.~~
  Done (part A).
- ~~**City fetch as a background job** with per-layer progress, mirror state and cancel.~~
  Done (part A).
- **Printability rules** in the city model: minimum wall width (0.8 mm) and slenderness
  cap (height ≤ 8× min width, clamp or flag), reported per build.
- ~~**Pre-flight report + panel**: size vs bed, thinnest feature, tallest spike, piece
  count, estimated filament/print time, watertight — computed server-side, shown before
  download.~~ Done (part B, below).
- ~~**Decimated preview** (≤ 150k faces) using the adaptive terrain mesh.~~ Done (part B).
- ~~**Puzzle**: fast heightmap-mask cutting for terrain-only puzzles; engraved piece IDs and
  a north arrow on the underside; pieces laid out on the plate; knob shapes (rectangular,
  dovetail, rounded); draggable cut lines (non-uniform grid) in the preview.~~ Done (part B).

### Batch 2 part A — done 2026-09-27

- **Landmark search** — `GET /api/geocode?q=` (`routers/geocode.py` → `geo2stl/geocode.py:
  search_places`): Nominatim `format=jsonv2`, identifying User-Agent (`map2stl/0.1 (+repo
  URL)`), ≤ 1 request/s process-wide, results cached 30 days (`geocode` namespace; new
  `json_cache_key` / `read_json_cache` / `write_json_cache` in `geo2stl/cache.py`). Returns
  `{query, results: [{name, display_name, lat, lon, bbox, class, type, osm_type, osm_id}]}`.
  `LandmarkSearch.vue` on the Explore map searches on submit only (the usage policy forbids
  autocomplete), pans/zooms to a result with a marker, and offers "Extend the box to include
  it" (`modules/map/landmarks.js:extendBboxToInclude`: the place's own extent if ≤ 3 km
  across, else its point, plus 100 m; the region is updated but not saved or reloaded).
- **POI-near-edge warning** — `GET /api/geocode/edge-landmarks` (`geo2stl/landmarks.py`):
  one Overpass query (`geo2stl.osm.overpass_query`) over the four 400 m strips centred on
  the edges for named `amenity=place_of_worship|townhall`, `historic=*` (minus memorial,
  boundary_stone, milestone, wayside_*, marker, district, road, yes — too many and not
  landmarks), `tourism=attraction|museum|viewpoint`, `out tags bb` so an area's extent (not
  just its centre) is measured. Features within 200 m inside/outside, or crossing the edge,
  are reported nearest first with a message ("Alhambra is 120 m outside the east edge";
  "… crosses the east edge (24 m sticks out)"); cached 7 days per rounded bbox; boxes over
  60 km diagonal are skipped. Client: new `EV.BBOX_CHANGED` (from `setBboxRectangle`, the
  mini-map drag and a drawn rectangle); `EdgeLandmarkWarnings.vue` debounces 1.5 s, writes
  `appState.edgeLandmarks`, toasts the first warning, and shows the list on the Explore map
  and (compact) under the Edit panel's preset bar.
- **Source resolution** — `geo2stl.dem.DEM_SOURCE_INFO` (arc-seconds + nominal metres per
  source; `OPENTOPO_DATASETS` gained `arcsec`), `dem_sampling()`; `/api/terrain/dem` returns
  `source_resolution: {source, native_resolution_m, native_samples, grid, upsample}` (test
  mode too), `/api/terrain/sources` gives each source `native_resolution_m`.
  `DemSamplingInfo.vue` under Fetch Layers → Resolution: "~30 m → 41×43 real samples,
  upsampled to 478×600", warning above 4×. **Correction:** the `local` source is the GEBCO
  2025 store (15″ ≈ 460 m), not 30 m as `/api/terrain/sources` used to say.
- **Default DEM source** — `geo2stl.dem.default_dem_source`: `SRTMGL1` with an
  OpenTopography key, else `h5_local` if the H5 store exists, else `local`. Used by
  `/api/settings/default` and the new `default_source` of `/api/terrain/sources`;
  `populateDemSources` selects it until a preset, saved region settings (`presets.js` marks
  `data-dem-source-chosen`) or the user pick a source.
- **City fetch as a background job** — `core/city_fetch_tasks.py` (own small registry, the
  `export_tasks.py` pattern: lock + 5 min TTL after finishing; an identical running request
  is joined). `POST /api/cities/start` → status; `GET /api/cities/status/{id}` →
  `{task_id, status, layers: [{name, state}], mirror, message, error, elapsed_s,
  diagonal_km}` with layer states pending / fetching / done / failed / cached / cancelled
  (+ a `heights` row for height enhancement); `GET /api/cities/result/{id}` (payload, 409
  unless done); `POST /api/cities/cancel/{id}`. Hooks: `city2stl.fetch.fetch_osm_data(...,
  progress=, on_mirror=, should_cancel=)` and `_fetch_layers(..., progress, should_cancel)`
  (`FetchCancelled`; a layer that has not started is skipped, in-flight Overpass queries
  finish and are discarded); `core/city_data.get_city_layers` passes them on and reports
  `cached` layers. Client: `modules/layers/city-fetch.js:runCityFetch` (start → poll 750 ms
  → result; aborting the signal cancels the server task), `loadCityData()` uses it,
  `CityFetchProgress.vue` shows the per-layer list, mirror host, elapsed time and Cancel
  (`window.cancelCityFetch`). The synchronous `POST /api/cities` is unchanged for the SDK.
- Tests: `tests/test_geocode.py`, `test_dem_sources.py`, `test_city_fetch_tasks.py`,
  additions to `test_osm_fetch.py` (all network mocked); `tests/js/landmarks.test.js`,
  `demSampling.test.js`, `cityFetch.test.js`.
- Not done / follow-ups: the SDK (`TerrainSession`) still uses the blocking
  `/api/cities`; `terrain_session.py` and the export side still default `dem_source` to
  `local` when a caller passes none; the edge warning is not re-run when only the saved
  region list changes without a box move.

### Batch 2 part B — done 2026-09-27 (pre-flight, decimated preview, puzzle)

- **Pre-flight report + panel** — `POST /api/export/preflight` (`core/preflight.py`), same
  body as `/api/export/start` (`format` = `city` | `puzzle` | …). Runs the terrain stage and
  `city_model.layer_preflight` (new: `feature_polygons` + the printability rules, no
  solids — the `widened` / `clamped` / `dropped` counts equal the build's) on the OSM
  cache only (uncached layers and a stale cache payload are warnings, never fetched).
  Returns size vs bed (`bed_mm` in the body), scale, vertical exaggeration, piece grid and
  method (`puzzle.grid_edges` / `choose_method`), per-layer counts, thinnest feature,
  tallest spike (terrain: height above a 5 mm mean; extruded: top above the lowest ground
  under it), estimated faces (terrain TIN of a strided grid × √stride + layers at 4 per
  outline vertex), filament and print time, warnings. Formula (in the response): printed
  = shell + 15 % × (volume − shell), shell = area × 0.9 mm (2 perimeters × 0.45 mm);
  grams = printed × 1.24 g/cm³; hours = printed / 8 mm³/s. The city build writes the
  same figures from the real mesh as `report.json` `check` (`build_check`), plus
  `puzzle`. Client: `PreflightPanel.vue` at the top of the Export tab (City model /
  Terrain puzzle, stale marker after any form change or dragged cut),
  `export-handlers.js:runPreflight` sends exactly the build's body.
  Measured (Cartagena 590×600, all layers): 2.6 s city, 0.5 s puzzle; filament 1430 g
  estimated vs 1442 g from the built mesh, faces 240 k estimated vs 206 k built.
- **Decimated preview** — `generate_mesh_preview` → `_preview_mesh`:
  `numpy2stl.processing.decimate.heightfield_tin_budget` (new) starts at the export
  tolerance (`city_model.terrain_tolerance`) and relaxes it in ×3 steps until ≤ 150 k
  faces fit; DEMs over 250 k px are strided for the preview only; solid = walls + a
  bottom fanned from its centre. Same JSON (`vertices` [col, row, z], `faces`, …) plus
  `preview: {adaptive, stride, tolerance_mm, max_error_mm, seconds}`; the viewer already
  drew from the faces given (no grid assumption); the HUD shows the bound reached.
  Also: `heightfield_tin` re-rasterises only the triangles each pass changed (Delaunay
  insertion touches its cavity only) with an affine-form rasteriser — same bound, the
  1000×1000 export TIN went 29.8 s → 8.7 s. Measured, 1000×1000 DEM (warm): **11.3 s,
  65.5 MB, 2.0 M faces → 3.3 s, 2.5 MB, 85 k faces** (bound 0.18 mm at stride 2).
- **Puzzle** (`core/puzzle.py`, `numpy2stl/applications/puzzle.py`):
  - *Mask path* for terrain-only models (automatic; `puzzle_method` / `puzzle.method`
    forces `mask` or `boolean`): `heightfield_pieces` takes the terrain TIN vertices
    inside each outline (one label raster, `raster.burn_polygons`) plus the outline
    split at the pixel spacing, triangulates them together (constrained), walls on the
    exact outline, flat bottom triangulated from the outline — so edges follow the
    outline, not the pixel grid. Volume check as for `cut_jigsaw` (TIN volume − gap
    area × mean gap height; overlap rejected). The city build uses it when no layer
    produced a solid. 1000×1000, 4×4 pieces: **18.0 s (mask) vs 26.1 s (boolean)** end to
    end; the mask time is mostly the TIN (8 s) and writing OBJ/3MF (8 s).
  - *Engraved id + north arrow*: `underside_marks` (matplotlib `TextPath`, DejaVu Sans
    Bold, height min(8 mm, 20 % of the piece), shrunk until it sits 1 mm inside the
    outline, mirrored to read with the piece turned over) cut 0.6 mm deep with
    manifold3d (`engrave_underside`); skipped where the piece is < 1 mm thick. Default
    on (`engrave_ids` / `puzzle.engrave`); removes exactly text area × 0.6 mm (tested).
  - *Plates*: `layout: true` + `bed_mm` → `<name>_plate<N>.3mf`, shelf-packed with a 5 mm
    gap and margin; oversize pieces get a plate each and are listed in the report.
  - *Knob shapes*: `knob_shape` = `classic` (rounded head on a neck; the app default),
    `dovetail`, `rectangular` (the library default, the old tab). The groove is the
    tongue grown by the clearance.
  - *Non-uniform grid*: `col_edges_mm` / `row_edges_mm` (from the west / south edge,
    validated by `validate_edges`: strictly increasing, ends within max(0.5 mm, 1 %) of
    the model size and snapped to it, every piece big enough for its knobs). Client:
    the preview's red cut lines can be dragged (`model-viewer.js`, arithmetic in
    `puzzle-cuts.js`), stored as `appState.puzzleEdges` for that grid, sent by both
    puzzle routes; *Reset cuts* in Split / Puzzle.
- Tests: numpy2stl `test_puzzle.py` (knob shapes watertight and tiling, explicit edges,
  mask pieces = boolean pieces and conserve volume, uncovered model detected, engraving
  volume, plate layout), `test_decimate.py` (budget); strm2stl `tests/test_preflight.py`
  (preflight counts = build counts, edges, warnings, 400; mask vs boolean, explicit
  edges, engraving, plates; preview capped and watertight), `tests/js/puzzleCuts.test.js`.
- Not done / follow-ups: the preview does not show the city layers (terrain only, as
  before); face estimates are ±40 %; the print-time model is one flow rate; the
  thinnest-feature figure includes slivers clipped at the model edge; the viewer's
  "Simplify" and "Surface groups" controls call `window.applySimplification` /
  `applySurfaceGroups`, which nothing defines (pre-existing, dead).
- The "Printability rules" item above was implemented with F-CITYMODEL (widened /
  clamped per layer); part B only reports it ahead of the build.
