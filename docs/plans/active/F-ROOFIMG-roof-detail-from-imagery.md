# F-ROOFIMG — Roof detail from imagery

_Approved 2026-10-20. Status: built 2026-10-20 (ridge on; hip and courtyards measured and gated off, see [decision](../../decisions/roofs-landmarks.md)). Proposal: [F-ROOFIMG](../../proposals.md)._

Uses the zoom-18 tiles the roof classifier already caches to set ridge direction, gabled vs
hipped, and courtyard holes. Heights still come from lidar and tags; imagery never sets a height.

## Today

- Ridge direction is `roofs.py::_long_axis`, turned 90 degrees only by `roof:orientation=across`.
- The classifier writes every pitched roof as `gabled` (Granada: 0 hipped).
- Footprints with holes go flat (the `poly.interiors` guard in `building_solids`).
- The offset between imagery and OSM footprints has never been measured. PNOA is rectified to
  bare ground, so roofs probably lean 1–3 m away from their footprints.

## Approach (new module `city2stl/roof_imagery.py`, numpy + cv2, no GPU)

1. `register`: search a shift of up to 5 px that best matches the footprint's edge band to image
   gradients, pulled toward the tile median. Skip the building if the match is weak.
2. `ridge_axis`: structure-tensor coherence plus the brightness split across the candidate ridge.
   Emit `roof:orientation=across` only when both agree and confidence is above `T_ridge`.
3. `hip_score`: contrast of the end triangles plus diagonal edge energy; above `T_hip`,
   a classifier `gabled` becomes `hipped`.
4. `courtyards`: dark or vegetated enclosed components (area at least `A_min`, at least 1 m from
   the outline, width at least 2.5 m, solidity at least 0.6), turned into polygons at 0.5 m.
5. `lidar_veto`: read the survey nDSM through `providers/survey.py` (read-only). Drop holes whose
   cells read roofed. Without lidar, holes need the stricter `T_hole_nolidar`.
6. `apply`: batch per tile with an LRU of decoded tiles. A new sqlite table `roof_imagery` in
   `roof_cache.py`, keyed on IMAGERY_VERSION, provider, zoom, geometry and `roof:shape`, stores raw
   candidates; the veto and thresholds run after the lookup.

Precedence: OSM tags > survey (F-ROOFLIDAR) > imagery > classifier. Tagged values are never
overwritten.

## Seam (after the `city_model.py` / `survey_fill.py` work of 2026-10-20 lands)

- `city_data.py::with_roof_shapes`: call `roof_imagery.apply` after `_classify_buildings`; add the
  version to the `_roof_memo` key. `roof_shapes: "off"` stays byte-identical.
- `roofs.py::building_solids`: allow interiors; `_planes` uses `outline.boundary` so courtyard
  edges become eaves. Manifold failures fall back to flat at mid-roof.
- `city_model.py`: no change.

## Success criteria (Granada)

- Ridge: agreement with a CNIG two-plane ridge (pitched footprints of 150 m² or more) beats the
  long axis by at least 5 points on the confident subset, and is never worse overall.
- Hip: grouped folds on Granada's tagged `gabled` / `hipped` roofs.
- Courtyards: imagery-only precision at least 0.85 against nDSM below 2 m.
- Warm Granada at most 3.5 s. Report the median registration shift.

Thresholds (`T_ridge`, `T_hip`, dark-pixel cut, `A_min`, solidity, shift gate, minimum hole in mm)
are measured on Granada, not chosen. Evaluation script: `tools/eval/eval_roof_imagery.py`.

## Tests

`tests/test_roof_imagery.py` with synthetic RGB: ridge along and across, four-face hipped render,
an inner patio and one touching the edge (not cut), lidar veto, a recovered 3 px shift, cache
round trip, tagged values untouched, and a holed gabled footprint giving a manifold solid.

## Risks

Lean and offset; sun direction faking a brightness split; light marble patios (missed) and
shadow on flat roofs (false cut, vetoed only where lidar exists); holes below the print gap;
terraced rows sharing one ridge; ordering against F-ROOFLIDAR in `with_roof_shapes`.

Effort: M, about 3–4 days.
