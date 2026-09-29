# Registration — locating a plate (scale, rotation, water/building solves)

How the align tool finds where a vendor plate sits on the ground (scale/span, centre, rotation) before fine registration. Related: [registration-refinement.md](registration-refinement.md), [registration-validation.md](registration-validation.md), [plate-critic.md](plate-critic.md), [osm-water-hydrology.md](osm-water-hydrology.md), [projections-raster.md](projections-raster.md).

**Current scale rule:** `tools/align_tool/export_align_data.py::plate_scale_m_per_unit` = plate XY extent against `PLATE_COVERAGE_M` (2000 m) or `PLATE_COVERAGE_OVERRIDE_M`; a span from `tools/align_tool/locate.py::measured_span_m` overrides the nominal coverage. Plan done: [scale-window-sizing-plan.md](../plans/done/scale-window-sizing-plan.md).

### 2026-08-29 — Building correlation floors local variance relative to its median
- **Decision:** `tools/align_tool/locate_buildings.py::ncc_surface` floors variance at `VAR_FLOOR_FRAC` (1e-4) x median local variance (was absolute 1e-9); failing positions score 0; clip to [-1, 1] only as backstop.
- **Why:** Paris miniature reported mean r 5.473 — FFT cancellation error in `fs2 - fs*fs/n` on near-uniform windows. Real peaks sit 1.45-2.95x median; runaways ~1e-11x. 12 of 13 plates bit-identical after.
- **Rejected:** clamp to 1 alone — a degenerate position would read as a perfect match and pass `MIN_MEAN_R` more easily.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-29 entry "relative variance floor"

### 2026-08-29 — Residual clip is a parameter and the align tool segments at p85
- **Decision:** `numpy2stl/src/numpy2stl/raster/segment.py::_adaptive_residual_threshold` accepts any `multiotsu_pNN`. Both align call sites (`tools/align_tool/export_align_data.py::plate_relief`, `tools/align_tool/locate_buildings.py::plate_buildings`) use p85 together — locate and export must see the same mask.
- **Why:** Philadelphia miniature's tower tail (residual p95 8.055 vs 2.25-3.87) pushed the p95 cut to 1.90. Wide-phase vote: p95 3/14 at 5.19 km, p85 5/16 at 0.11 km, p80 same (plateau). Full solver: passes 6/9, 117 m from City Hall. Water controls unchanged; Boston miniature 15/16 to 13/16.
- **Caveats:** the pass rests on 9 crops (letterboxed plate); its -9.33 deg rotation is not evidence (street grid implies about -4.5; `ROTATIONS` spans only +-12).
- **Rejected:** adaptive clip with a feedback loop — not needed, harder to reason about.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-29 entry "residual clip percentile"

### 2026-08-29 — Plate scale is 20 m per model unit
- **Decision:** about 20 m per unit (2000 m across a ~100-unit plate); the old registration's 0.6667 scale was 7% off, `pipeline_guess` 0.7133 right. Export raster is south-up.
- **Why:** footprint-IoU sweep gives 20.00-20.05 on Prague, Barcelona, Valencia, Salzburg; south-up IoU 0.60-0.71 vs 0.15-0.30. Coverage ceiling 0.822.
- **Rejected:** ECC affine on top (Prague 0.492 to 0.462); per-footprint sliding (walks into the next block).
- **Supersedes / superseded by:** — (consistent with the current scale rule)
- **Source:** decisions.md 2026-08-29 entry "Plate scale is 20 m per model unit"

### 2026-08-28 — The building solver searches wide then fine
- **Decision:** wide phase: 7x window, coarse cell (`tools/align_tool/locate_buildings.py::coarse_cell_for`, 16 m for 2 km); fine phase re-centres at 2.5x, 8 m, and applies the gates. Filled mask.
- **Why:** a 3x margin gave 2 km reach vs geocoder misses of 3.8 km (Lisbon, Miami). The coarse cell also regularises: Prague went from 4/16 with a spurious +9.5 deg to 16/16, 18 m. Filled beats outline on Bilbao (16/16 vs 4/16).
- **Rejected:** one pass at 8 m (reach vs 2048-px cap); outline mask.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 entry "two phases"

### 2026-08-28 — A plate with no water is located from its buildings by crop voting
- **Decision:** `tools/align_tool/locate.py::resolve_center` falls back to `tools/align_tool/locate_buildings.py::solve_plate` when the water solve is absent or fails. Locally normalised correlation; overlapping crops each vote for the centre; agreement is the gate; span and rotation searched (`fit_rotation`).
- **Why:** plain building correlation peaks at z 3.1 (it finds density). Normalising doubles peaks (Paris 7.1 to 17.3). Agreement reads 16/16 right vs 2-7/16 wrong. A 2 deg rotation error broke fits with every gate green; rotation fit recovers within 0.32 deg.
- **Rejected:** whole-plate plain correlation; peak-height gating.
- **Supersedes / superseded by:** supersedes "rotation fixed at 0" for building solves ([water entry](#2026-08-26--plate-centres-are-solved-by-water-correlation)); water solves still fix it.
- **Source:** decisions.md 2026-08-28 entry "A plate with no water"

### 2026-08-27 — A hand alignment measures span only if it resized the plate
- **Decision:** `tools/align_tool/refine_guess.py::span_is_measured` compares the saved scale with the `pipeline_guess` in the same ground-truth file (over 1% = measured).
- **Why:** only Salzburg's of six alignments moved scale (0.6667 to 0.5488); others were nominal saved back. Valencia, Prague, Barcelona turn out 8-10% larger than nominal — refine sweep and water solver agree independently (2196/2156/2066 vs 2160/2160/2040 m).
- **Later (code):** the sweep may overrule even a measured span (`SPAN_OVERRIDE_R`, `SPAN_OVERRIDE_Z`); Salzburg's resize went ~3.5% too far.
- **Supersedes / superseded by:** refines [2026-08-26 span entry](#2026-08-26--span-is-searched-but-only-a-hand-alignment-sets-scale-superseded)
- **Source:** decisions.md 2026-08-27 entry

### 2026-08-26 — Plate centres are solved by water correlation
- **Decision:** `tools/align_tool/locate.py::solve_center` (water cut-out vs OSM water, FFT correlation, span swept 0.78-1.20, rotation 0) replaces the geocoded centroid; a rejection returns None and the geocoder path runs.
- **Why:** 7x window needed (Miami is 3.8 km from its centroid). Gate `MIN_PEAK_Z` 8.0, `MIN_MARGIN_Z` 2.5; correct answers score z 15.2-24.0, margin 4.1-13.1. The margin rejects the filled-sea plateau (Miami filled z 2.3, margin 0, 5 km off; outline z 16.8, 8 m).
- **Rejected:** geocoded centroid; 3x window; peak-shape tests.
- **Supersedes / superseded by:** supersedes the [straight-coastline claim](#2026-08-12--a-straight-coastline-cannot-constrain-along-shore-translation-superseded); rotation part superseded for buildings by [2026-08-28](#2026-08-28--a-plate-with-no-water-is-located-from-its-buildings-by-crop-voting)
- **Source:** decisions.md 2026-08-26 entry "Plate centres are solved by water correlation"

### 2026-08-26 — Span is searched, but only a hand alignment sets scale [superseded]
- **Decision:** the water solver sweeps span to help position; export scale comes only from a hand alignment via `measured_span_m`.
- **Why:** Salzburg's plate is 1651 m, not 2000; the sweep reads Lisbon 10% high, worse than no correction.
- **Supersedes / superseded by:** supersedes [2026-08-06 2 km frame](#2026-08-06--scale-is-per-city-anchored-to-the-vendors-2-km-frame-superseded). Superseded by [2026-08-27](#2026-08-27--a-hand-alignment-measures-span-only-if-it-resized-the-plate) and [2026-08-28](#2026-08-28--a-plate-with-no-water-is-located-from-its-buildings-by-crop-voting) (building-solver span is also trusted).
- **Source:** decisions.md 2026-08-26 entry "Span is searched"

### 2026-08-06 — Scale is per-city, anchored to the vendor's 2 km frame [superseded]
- **Decision:** replace the 0.667 scale lock with per-city scale anchored to the vendor's 2 km LARGE frame; open question was constant m/unit vs constant coverage.
- **Supersedes / superseded by:** superseded by the current scale rule (constant nominal coverage, per-plate extent, measured span override) — see [2026-08-26](#2026-08-26--span-is-searched-but-only-a-hand-alignment-sets-scale-superseded) and [2026-08-27](#2026-08-27--a-hand-alignment-measures-span-only-if-it-resized-the-plate).
- **Source:** decisions.md 2026-08-06 entry; [scale-window-sizing-plan.md](../plans/done/scale-window-sizing-plan.md)

### 2026-08-06 — The tallest-building over z-max scale estimator is invalid
- **Decision:** bypass it; the align tool passes scale from `plate_scale_m_per_unit`. It remains in `numpy2stl/src/numpy2stl/registration/pipeline.py::register_city_stl`.
- **Why:** z-max = base + terrain + building, not a building height; windows ranged 386 m to 3245 m vs ~2 km.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-06 entry

### 2026-08-05 — Serve the align tool over HTTP
- **Decision:** local http.server preview (port 8766), not file://.
- **Why:** the 8.2 MB embedded data script is refused over file://.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-05 entry

### 2026-08-05 — Drag-align keeps the STL stationary and the export inverts
- **Decision:** the user drags the OSM/satellite layer over a fixed STL; export inverts to the pipeline's STL-to-OSM convention.
- **Rejected:** moving the STL — the opposite of what the user asked.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-05 entry

### 2026-08-05 — ECC refine is self-contained in the align tool
- **Decision:** `tools/align_tool/server.py::api_refine` does not call `numpy2stl/src/numpy2stl/registration/align/ecc.py::refine_transform` (wrong seed direction; swallows `cv2.error`). Recipe: inverted seed, `MOTION_AFFINE`, blurred edges in a coarse-to-fine ladder, rungs judged by edge-IoU, guardrails, and a noise floor before touching a hand alignment.
- **Rejected:** fixing the shared library mid-investigation; Euclidean motion; ECC cc as arbiter (a 375 px divergence scored high).
- **Supersedes / superseded by:** — (see [registration-refinement.md](registration-refinement.md))
- **Source:** decisions.md 2026-08-05 entry "ECC refine recipe"

### 2026-08-04 — The registration learning stack is layered, not end-to-end
- **Decision (user):** manual ground truth, then U-Net STL segmentation with classic registration, then template selection if needed, direct transform regression last.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-04 entry; [registration-learning-plan.md](../plans/active/registration-learning-plan.md)

## Rejected hypotheses

### 2026-08-29 — Plates do not carry pitched-versus-flat roof information
- **Hypothesis:** Prague/Salzburg AUC 0.528/0.596 closes the plate route. **Measured:** that was at 3.1-4.3 m/px; from the mesh at 0.4-0.5 m/px, 0.837/0.796. **Verdict:** refused — state the resolution before concluding a source lacks a signal.

### 2026-08-12 — A straight coastline cannot constrain along-shore translation [superseded]
- **Hypothesis:** Barcelona, Lisbon, Miami (and Valencia) were structurally unplaceable. **Measured:** ordinary defects (small window, sea missing from mask, fake hand alignment); fixed, they place to 8-72 m, Valencia z 16.5 after `MIN_PLATE_WATER_FRAC` 0.0008. **Verdict:** wrong, cost a fortnight — rule out cheap defects before calling a failure structural. Superseded by [2026-08-26](#2026-08-26--plate-centres-are-solved-by-water-correlation).
