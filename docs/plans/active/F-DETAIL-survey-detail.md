# F-DETAIL: survey detail in the city model (heights, roofs, lidar trees, reference landmarks)

Brings the four details measured in the 2026-10-17 Granada study into the app's preview and
export. This page is the plan; status lives in the roadmap row in [../README.md](../README.md).

User-requested 2026-10-17 ("granada is missing all the details that we just researched how to
get"). Proposal: [../../proposals.md](../../proposals.md) › F-DETAIL. Study decision:
[../../decisions/mesh-pipeline.md](../../decisions/mesh-pipeline.md) › "Granada detail study";
outputs in `output/detail_study/`.

## State found (2026-10-17)

- **Survey heights:** shipped and working for the `buildings` layer. Granada serves 348 `survey`
  footprints (286 CNIG, 62 REDIAM). Torres Bermejas 19.0 m, Alcazaba 9.5 m.
  - Not filled: the `towers`, `churches`, `fortifications` and `walls` layers.
  - Not filled: footprints whose height is an `osm_tag` of exactly the fallback 10.0 m (Torre de
    la Vela, Torre del Homenaje, Torre de Comares). Check the raw tags first.
  - Height overrides replace the server's filled payload (`app/server/core/city_model_task.py`,
    the `layer_data.buildings` branch) instead of merging onto it.
  - `app/server/core/city_data.py::read_city_layers_any_tolerance` (composite, raster,
    enhance-heights) and `app/server/core/preflight.py` skip the fill.
  - Fill stats are discarded, so a failed fill is invisible.
- **Roof shapes:** only from OSM `roof:shape` (`city2stl/city_model.py::_roof`). The classifier
  `city2stl/roof_classifier.py::classify_roof_shapes` is never called by the server. The quick
  view (`city-quickview.js::buildingPrisms`) draws flat prisms.
- **Lidar trees:** not in the app; the study scripts are gone, the numbers survive in
  `output/detail_study/exp2_trees_roofs/results.md`.
- **Alhambra:** not in the app; the STL (Thingiverse 5141408) is in
  `Cities/granada_stl/Alhambra - 5141408/`, its registration in
  `output/detail_study/shared/thingiverse_to_geo.json`.

## User choices (2026-10-17)

| Question | Answer |
|---|---|
| Roofs for untagged buildings | Auto: classifier on by default, Off switch in Settings |
| Trees in the preview | Coarse in the preview, full canopy in the export |
| Alhambra in exports | Off by default, a switch adds it; no licence note added to the export |
| Alhambra z scale (3.68 vs 4.67 m/mm) | Pick by lidar: score both against CNIG, the smaller error wins, report both |
| D5 mockups (`claude/mockups/2026-10-17-detail/`) | Approved as drawn |
| Trees default | On wherever survey lidar covers the box; off and greyed elsewhere |

## Phases

Sonnet agents execute; at most two at once, on disjoint files. Each phase follows the
documentation checklist in `map2stl/CLAUDE.md` and bumps the cache version it invalidates.

### D1. Survey heights everywhere
- **Files:** `city2stl/height/survey_fill.py`, `app/server/core/city_data.py`,
  `app/server/core/city_model_task.py`, `app/server/core/preflight.py`, tests.
- Fill the landmark layers too (towers, churches, fortifications, walls); treat a bare 10.0 m
  `osm_tag` as default only when the raw tag is not an explicit `height=10`.
- Merge client height overrides onto the filled payload.
- Apply `with_survey_heights` in `read_city_layers_any_tolerance` and preflight.
- Return fill stats (`filled`, `skipped`, `error`, sources) in the city payload.
- **Acceptance:** Torre de la Vela and Torre del Homenaje read a survey height; an override
  export keeps the other survey heights; composite heights equal the payload's.
- **Built 2026-10-17 (KO):** `fill_layer_heights`, `fillable`, `merge_layer_data`, `survey_fill`
  stats in the payload and `report.json`; no cache bump (fill on read). Granada: 427 of 440
  filled; Vela 28.8 m, Homenaje 23.0 m (towers layer). Left: the buildings-layer Homenaje
  footprint is unmeasured (stays 10.0); 13 features unmeasured; optional `fetch.py` keeps raw
  `height`. Decision: [building-heights.md](../../decisions/building-heights.md) 2026-10-17.

### D2. Roof shapes (after D1: shares `city_model_task.py`)
- **Files:** `app/server/core/city_model_task.py` (or `city_data.py`), `city2stl/roof_classifier.py`
  call site, region settings default `roof_shapes: "auto"`, `city-quickview.js::buildingPrisms`
  (simple gable / hip prisms from `roof:shape` and `roof:height`), tests.
- **Acceptance:** untagged Granada buildings get classified roofs in the finished model; the quick
  view shows pitched roofs; `roof_shapes: "off"` restores today's output byte-identically.
- **Built 2026-10-17 (KO):** `city_data.py::with_roof_shapes` / `roof_shapes_mode` (on read after
  the survey fill, memoised per bbox + footprints; no cache bump), `roof_shapes` in the export
  body, `CityRequest` and the `/api/cities/start` task; stats `roof_shapes` beside
  `survey_fill` in the payload and `report.json`; `merge_layer_data` keeps classified roofs;
  quick view draws gable / hip (`city-quickview.js::roofKind`, `roofHeightM`). "Off" is
  byte-identical (`tests/test_roof_shapes_server.py`). Classifier: the `roof_shape_gbm`
  checkpoint only (flat / gabled), its own zoom-18 tiles; `roof:height` stays the 30 % default.
  Results cached on disk per footprint (`city2stl/roof_cache.py`), batched predicts and a
  "Classifying roofs N of M" message: Granada cold 124 s, after a restart 3.4 s.
  **Open:** not yet run on real Granada (count, timing); hip on concave outlines stays flat in
  the quick view.

### D3. Lidar tree canopy (in parallel with D1)
- **Files:** new `city2stl/canopy.py`; `city2stl/city_model.py` (`trees` layer in
  `LAYER_PRIORITY` / `DEFAULT_LAYERS`, builder, cut in `assemble_model`); tests. Rewrite
  `F-CITYTREES` to point here for lidar canopy.
- Rebuild the study's algorithm from `exp2_trees_roofs/results.md`: nDSM > 2.5 m, outside
  buildings buffered 2 m, inside OSM green or ExG > 0.115; open 0.8 mm, drop < 1 mm², fill holes
  < 0.5 mm²; clip 30 m, sigma 0.4 mm; seat 0.6 mm below the surface; heightfield TIN at
  0.25 mm (export) and a coarse tolerance (preview, about 10x fewer faces).
- Only where a survey provider covers the box; off otherwise.
- **Acceptance:** Granada export within 10 % of the study (372k faces, 3,212 solids, 65 ha),
  watertight, no overlap with buildings; preview variant about 10x fewer faces.
- Check REDIAM's licence and record it in `docs/reference/survey-sources.md`.
- **Built 2026-10-17 (KO):** `city2stl/canopy.py` (`canopy_layer`, `canopy_mask`,
  `canopy_solids`, `CanopyParams`, `CANOPY_VERSION`), `trees` layer and
  `city_model.py::_canopy_solid`; export opt-in via `layers.trees.enabled: true`
  (`city_model_task.py::wants_trees`, `preview` for the coarse variant). Granada, OSM green
  only (no satellite tiles cached, so no ExG street trees): 38.2 ha, 1,274 solids, 171,932 faces,
  all watertight; face density 4 % above the study on the same area; preview 168 solids /
  23,664 faces (7.3x fewer). Outline simplify 0.3 mm (0.2 gave 25 % too many faces).
  App export through `/api/cities` (dim 600, 0.192 mm/m, live satellite ExG): 58.0 ha, 2,370
  solids, 205k faces, model 800k faces watertight; top-down render shows canopy on the
  Alhambra woods, Sacromonte and gardens, clear of buildings. Below the study's 65 ha / 3,212 /
  372k, mostly from the smaller scale (0.192 vs 0.217 mm/m drops crowns under the 1 mm² floor).
  **Open:** the 65 ha / 372k / 3,212 check with real imagery at the study's scale (ExG formula and 0.115 threshold
  unverified); REDIAM licence not confirmed (survey-sources.md); the UI toggle is D5.

### D4. Reference landmark (Alhambra)
- **Files:** `city2stl/landmarks.py` (new kind `reference`, reusing `ndsm_solid` with a stored
  raster), an offline builder script under `tools/` that z-buffers the STL through the stored
  4x4 into a geo-referenced building-height raster, region setting (off by default), tests.
- Z scale: build both rasters (3.68 and 4.67 m/mm), score each against CNIG on the swapped
  footprints, keep the lower median |Δ|, record both numbers in `docs/decisions/roofs-landmarks.md`.
- Swap rule from `exp4_merge/rule_table.csv` (22 swapped, 11 flagged for footprint checks).
- **Acceptance:** with the switch on, the finished model matches the study's method B (22 of 22
  improved, median error about 2.4 m); with it off, output unchanged.
- **Built 2026-10-17 (KO):** `city2stl/landmarks.py::reference_landmarks` (kind `reference`,
  free-standing `footprint_lonlat` units, `ndsm_solid` now tops at local ground + height and
  crops to the footprint); data `data/landmarks/alhambra_reference.npz` (uint16 cm) + rule JSON
  (18 units, 22 labels); builder `tools/landmarks/build_reference_raster.py`; export body
  `reference_landmarks: true`, stats `report.json` `landmarks_reference`. Z scale: isotropic
  3.683 m/mm kept (median |Δ| 2.37 m, 18 of 19 scored improved) over the terrain fit 4.671 m/mm
  (3.93 m, 14 of 19). Raster alone: 21 of 22 improved, median 2.45 m (Torre del Criado level
  with OSM). **Open:** the flagged-but-swapped Alcazaba, Abencerrajes, Sultana
  (mapping checked 2026-10-23, see issues.md); rebuilding needs the gitignored study grids.

### D5. Settings UI (after D2-D4)
- PNG mockups first (`claude/mockups/2026-10-17-detail/`): Roof shapes (Auto / Off), Trees
  (On / Off, coverage note), Alhambra model (Off / On) in the Extrude More settings.
- Then build, `npm run build`, click-through in the app.
- **Built 2026-10-17 (KO):** `modules/layers/city-detail.js` (coverage lookup, `surveyCoverageReady`
  awaited before export), three rows in `layerGroups.ts`; app defaults Auto / Trees on / Alhambra
  off confirmed in the Edit panel. Full Granada export through the API with all four details:
  839k faces, watertight, 599 x 344 x 42.8 mm, 778 s. Roofs 23,867 classified (4,604 gabled);
  reference 17 of 18 units applied; survey fill 465 of 483; trees 2,370 solids, 58 ha.
  Open faults: [../../issues.md](../../issues.md) › "F-DETAIL: open faults" (cold roof
  classification about 10 min, also in the preview).

## Risks

- **Faces:** trees add about 370k faces to Granada (about 1.33 M in all); the preview variant is
  required.
- **Licence:** the Alhambra is CC BY-SA. By the user's choice no licence note is added; sharing an
  export with it switched on would need the author credit added by hand.
- **Tree heights:** REDIAM is a vegetation model and may under-read crowns; licence unverified.
- **Registration:** hand-fitted; z scale ambiguous (D4 settles it by lidar).
- **Open mesh bugs:** the `array_to_mesh` corner pinch and the duplicate `building` /
  `building:part` outlines ([../../issues.md](../../issues.md) › "Mesh and export faults found in
  the Granada detail study"). D3 and D4 depinch their heightfields until the pinch is fixed.
