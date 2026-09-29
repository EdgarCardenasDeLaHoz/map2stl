# Registration — refinement, wide window, street placement, footprint channel

How a located plate is refined onto the OSM map: mask definitions on both sides, the search window, street-scale placement and its channels.
Related: [registration-plate-location.md](registration-plate-location.md) (finding the plate at all), [registration-validation.md](registration-validation.md) (ground truth, consensus, UI registration), [plate-critic.md](plate-critic.md), [building-heights.md](building-heights.md).

### 2026-09-14 — Parks are "don't care" for the footprint channel
- **Decision:** the footprint channel's map side gets a care mask; cells at least half covered by `leisure=park` are ignored.
  - Correlation is doubly masked (plate mask times care mask): overlap, both sums and both sums of squares vary with the shift, so 6 inverse FFTs per block instead of 3; a shift with overlap under 30% of the plate scores 0.
  - Map smoothed by normalized convolution, standardized over cared cells; with care everywhere it equals the old path to 1e-17.
  - Code: `city2stl/registration/street_place.py::PARK_SELECTOR`, `city2stl/registration/street_place.py::map_parks`, `city2stl/registration/street_place.py::Reference` (its `_masked_surfaces`), wired through `city2stl/registration/street_place.py::_channels` to the footprint channel only. Density search unchanged. Overpass failure leaves the mask off and records `notes["parks"]`.
- **Why:** 20-65% of the remaining red (plate built, no OSM building) sits on OSM wood/park/tree cover (Salzburg 67%, Prague 59%, Valencia 56%).
  - Mean `unique` at pack pose, 14 packs: 7.17 -> 7.69; 11 up, 3 down (Miami -0.39, Salzburg -0.21, Alhambra -0.02). Valencia carries most (8.97 -> 13.56, the Turia park); without it the gain is about +0.25.
  - No-pose test (hide 1000): 15 of 15 within 7 m, identical errors; mean `unique` 6.18 -> 6.66. San Juan 1.40 -> 1.73. No pose moved; not re-exported (the exporter calls street placement itself next export).
- **Rejected:**
  - wood + forest + scrub as don't-care — worse: the hills are what place Salzburg (6.74 -> 6.48) and the Alhambra (4.66 -> 4.40);
  - park + garden — 7.64, the Alhambra's palaces sit inside `leisure=garden` (3.98);
  - park + garden + trees — 7.54; trees only — 7.15, nothing.
- **Note:** the exported `stl_mask` already drops OSM vegetation and elevated roadways after placement (`tools/align_tool/export_align_data.py::semantic_exclusion`; Salzburg coverage 0.561 -> 0.338). The red in overlays is street placement's raw `plate.built`, which the search uses and the export does not.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-14 entry.

### 2026-09-13 — Plate built edges are cut at half the local maximum height
- **Decision:** `city2stl/registration/street_place.py::built_mask` keeps a native cell only where its height exceeds both `FLOOR_M` and half the highest height within `EDGE_REACH_M` (10 m). A blurred step crosses half its height at the true edge whatever the building's size.
- **Why:** the fixed 6 m erosion deleted small buildings outright (Old San Juan's row houses, the Alhambra). San Juan hidden no-pose `unique` was 1.48 on old code, 0.31 with the remodel; variants pinned it on the erosion (no erosion 1.54, no mesa 0.10, old plate + new map 1.51).

  | edge rule | mean unique (14, hide 0) | min | mean agree | San Juan hidden |
  |---|---|---|---|---|
  | erode 6 m | 6.74 | 3.86 | 0.736 | 0.31 |
  | half-max, 6 m reach | 6.91 | 4.62 | 0.716 | 1.36 |
  | **half-max, 10 m reach** | **7.17** | **4.66** | 0.719 | 1.40 |
  | half-max 10 m OR 6 m-eroded core | 6.51 | 4.32 | 0.726 | 1.34 |

  - No pose moved (all 14 within 0 m, Philadelphia Miniature 8 m). No-pose test: 15 of 15 within 7 m including San Juan.
- **Rejected:** union with an eroded core (meant to keep a low building beside one twice its height, which half-max cuts — visible as light blue inside Barcelona and Lisbon blocks) — scored worse.
- **Supersedes / superseded by:** supersedes [the 6 m erosion](#2026-09-13--plate-edges-are-trimmed-by-a-fixed-6-m-erosion-superseded).
- **Source:** decisions.md 2026-09-13 entry, last part.

### 2026-09-13 — Plate edges are trimmed by a fixed 6 m erosion [superseded]
- **Decision:** erode the native plate mask by `PLATE_EDGE_M` = 6 m before the majority vote, removing the red line along every street (the exporter blurs relief 6 m before its residual, so the built mask stands about one native cell proud).
- **Why:** mean `unique`, 14 packs: 0 m 5.44, 4 m 6.30, **6 m 6.74**, 8 m 6.72, 12 m 6.48. No pose moved. 12 m collapses Salzburg (1.03). Old-to-final that day: mean unique 4.43 -> 6.74, Barcelona agreement 0.66 -> 0.74.
  - No-pose check (hide 1000): 14 of 14 within 8 m; `unique` up in 13 of 14 (Miami 2.47 -> 9.35, Paris 1.74 -> 5.73). Lisbon size margin 0.79 -> 3.01.
- **Rejected:** —
- **Supersedes / superseded by:** superseded the same day by [half-max edge rule](#2026-09-13--plate-built-edges-are-cut-at-half-the-local-maximum-height): a fixed erosion deletes small buildings (San Juan 0.31).
- **Source:** decisions.md 2026-09-13 entry, "a third change" and no-pose check.

### 2026-09-13 — Footprint channel remodelled, standing coverage map and mesa plate
- **Decision:** both sides of the footprint channel were rebuilt; disagreement colours were traced to the models, not the data.
  - Map side (`city2stl/registration/osm_model.py::coverage`): coverage raster (4x supersampled, centre sampling, area-averaged) instead of all_touched; relation holes (courtyards) kept; only classes that stand above the street (`city2stl/registration/osm_model.py::STANDING`: drops construction, ruins, underground, building=no). Road/rail decks that are bridges or on a positive layer are added (Miami: 17% of the red).
  - Raw Overpass elements cached as gzipped JSON per layer and window, so a new tag model costs no query. `city2stl/registration/street_place.py::map_buildings` fetches only what can be a deck (`["highway"]["bridge"]`, `["highway"]["layer"]`, rail): a plain `["highway"]` query times out on the ~4 km hidden-test windows.
  - Plate side (`city2stl/registration/street_place.py::built_height`): reads big roofs back from relief. The exporter's residual (`numpy2stl/src/numpy2stl/registration/align/segmentation.py`, 6 m blur minus 80 m opening) calls any roof wider than ~80 m ground. Mesa additions: relief above a flat 300 m opening at 8 m cells, components > 8 m high not already built, kept if rim drops >= 0.6 of median height, area <= 200,000 m2, and 150 m-smoothed terrain slope < 0.1 (an ungated version flooded Granada and Salzburg).
  - Plate cell is built where most native cells are, not where mean height clears 2 m.
- **Why:** old map burned every footprint with all_touched at 8 m: ~40% more built than the city, narrow streets closed. Light blue (OSM building the plate misses) was 91-99% class "building" on big roofs.
  - Mean `unique` at each pack's pose: 4.43 -> 5.44 (Prague 1.67 -> 3.00, Paris 2.02 -> 3.72, Philadelphia Miniature 4.14 -> 6.66). Prague's size margin 0.96 -> 2.15 (now confident). Only Granada loses (4.68 -> 4.09).
  - The map change alone hurt Philadelphia Miniature (4.14 -> 2.80): the two changes need each other.
- **Rejected:** tree canopy (OSM trees as 4 m discs, wood, forest) as a half-weight map layer — small gains, cost Granada 1 sd; a 5 m plate floor instead of 2 m — better IoU, less `unique`.
- **Supersedes / superseded by:** plate edge handling refined by [half-max edge rule](#2026-09-13--plate-built-edges-are-cut-at-half-the-local-maximum-height); parks added [2026-09-14](#2026-09-14--parks-are-dont-care-for-the-footprint-channel).
- **Source:** decisions.md 2026-09-13 entry; module docstring of `city2stl/registration/street_place.py`.

### 2026-09-11 — Street placement adds water and terrain channels and a no-pose search
- **Decision:**
  - Water channel: z surface against the plate's water cut-out. OSM water corrected: islands (inner rings) removed as dry land, bridges stroked out at 14 m (`city2stl/registration/street_place.py::map_water_bridges`). Paris water z 2.5 -> 4.2.
  - Terrain channel only where the DEM's 5th-95th percentile spread under the plate is >= 25 m (`TERRAIN_MIN_RELIEF_M`); otherwise noise. Map side: cached 12 km COP30 tile; plate side: relief minus building residual (replaced a 200 m opening that smeared hillsides; terrain z Salzburg 1.2 -> 2.0, Alhambra 1.4 -> 2.0). No-pose search reads terrain from the local SRTM store (no OpenTopography call).
  - No-pose path: density search returns up to 4 distinct peaks (>= half a plate apart, within 3 sd of best), each gets a coarse street refine, best goes to full refine. Uniqueness = min(refine's own, gap to second candidate). Ranking refine reaches 700 m and re-centres up to two hops when it ends past 60% of reach; candidates within 80 m merge.
  - Confidence policy: position and size trusted separately. `unique >= 1` moves the pack; size used only if size margin >= 1 too, else pack scale (1.0). Lets Prague and the Alhambra (size margin 0.9-0.96) be positioned without a weak size.
- **Why:** a single density winner was wrong for Barcelona, Lisbon, Miami, Alhambra from 1 km off; a density peak is a district and its error can exceed 400 m (Philadelphia Miniature 421 m, Valencia 625 m). Result: 14 of 14 packs recovered from 1 km off with no pose.
- **Rejected:** roughness filter (local std over height) for Salzburg's canopy — roof edges are rough and canopy is smooth at 8 m (see also [texture hypothesis](#2026-09-14--plate-texture-can-tell-tree-canopy-from-roofs)); Lisbon and Miami `_ground_truth` records — refining from them hits the 400 m reach and they agree worse (0.57, 0.53) than the placements (0.76, 0.70); Barcelona's ground truth is a copy of its old guess, not evidence.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-11 (later) entry.

### 2026-09-11 — Grid cities are placed at street scale, locally, from the pack pose
- **Decision:** refine from the pack's recorded pose (or from a confident density answer) by comparing the plate's footprint mask with the map's raw, unmerged footprints smoothed by one 8 m cell, so streets survive. Search only 400 m around the start, turn +/-6 deg in 1 deg steps, size x0.85-1.20 in 0.025 steps (`city2stl/registration/street_place.py::refine`). A coarse density search (`city2stl/registration/street_place.py::density_search`) is only the fallback for a pack with no usable pose. The exporter calls this at export (`STREET_PLACE=0` turns it off).
- **Why:** street-scale matching aliases every block, so it must be local; near the truth it is sharp.
  - All 14 packs, 2 min on 4 processes: every placement within 93 m of start (most 0-22 m), agreement up in every city (Valencia 0.60 -> 0.68), peak >= 1.48 sd above anything > 80 m away; size margin >= 1.0 sd except the Alhambra (0.49). Refined sizes 0.95-1.13 of `geometric_guess.scale`.
  - The packs' poses were already close; the 09-08..09-10 failures came from the solver (oversized plate, heading ignored, density-scale criterion, global search).
- **Rejected:** cutting stroked streets out of the merged density map — Boston Miniature 0 -> 6570 m, Barcelona 75 -> 2693 m; density search limited to half a plate — heading and size still pinned at the limits (pack pose agreed better: Philadelphia 0.75 vs 0.58).
- **Supersedes / superseded by:** refines [2026-09-10](#2026-09-10--search-plates-are-drawn-at-the-exporter-scale-and-heading-not-oversized); extended by [channels and no-pose search](#2026-09-11--street-placement-adds-water-and-terrain-channels-and-a-no-pose-search).
- **Source:** decisions.md 2026-09-11 entry.

### 2026-09-10 — Search plates are drawn at the exporter scale and heading, not oversized
- **Decision:** a wide-window search draws the plate at `gscale = geometric_guess.scale` (0.667; the OSM raster covers 1.5x the plate span) and turned BY `geometric_guess.rot_deg` with `cv2.getRotationMatrix2D`, as the exporter does. Heading comes from the pack, never a free search.
  - Accept a search placement only when unique >= 1.0 and turn margin >= 0.9; otherwise keep the pack's position.
- **Why:** every study from 09-08 (calib, log-polar, widewin, widefine, whichfield, wideturn) drew the plate at the OSM cell size — 1.5x oversized; the log-polar "scale ~1.4" was this error, not a granularity artefact (that conclusion is withdrawn). Miniatures are turned (Denver 135.2, Paris 44.0, Alhambra -22.9, Philadelphia 8.9); a full-circle sweep on Paris Miniature peaks at 45 deg, confirming the sign.
  - Fixed: Paris Miniature 11 m (was 4137), Boston Miniature 0 m (was 7124), Bilbao 11, Paris 86, Prague 64 m, all confident. Seven cities still pinned at search limits with unique <= 0.7 — building density alone cannot place dense grids.
- **Rejected:** free full-circle heading search — grid cities take a wrong heading at turn margin 0.04-0.07.
- **Supersedes / superseded by:** corrects the per-city numbers of [2026-09-08](#2026-09-08--the-search-window-must-be-wide-enough-to-contain-the-answer) (its method conclusions stand); the density criterion is replaced by [street-scale placement](#2026-09-11--grid-cities-are-placed-at-street-scale-locally-from-the-pack-pose).
- **Source:** decisions.md 2026-09-10 entry.

### 2026-09-08 — Self-consistency is not evidence a placement is right
- **Decision:** the truth-free displaced-and-resolved test is kept only to catch an unstable search; it must not be read as correctness.
- **Why:** with the wide window 13 of 14 packs recover 6 of 6 known displacements — and Philadelphia scores 6 of 6 while sitting 4 km away across the river. Density smoothed to 120 m matches one dense grid to another kilometres away; a wider window gives it more room to be confidently wrong.
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-08 entry, "Self-consistency is not correctness".

### 2026-09-08 — The search window must be wide enough to contain the answer
- **Decision:** search over a window wide enough that the plate never leaves the frame (the align tool's 7x, `tools/align_tool/locate.py::SEARCH_MARGIN`), holding the cell constant (`tools/align_tool/locate.py::TARGET_CELL_M` 8 m) and letting the grid grow, instead of the pack export's 1.5x (`numpy2stl/src/numpy2stl/registration/config.py::DEFAULT_OSM_MARGIN`).
- **Why:** at 1.5x a plate centre can travel only (margin - 1) / 2 plate widths (218-1239 m). 12 of 14 packs place outside that room; only Lisbon and Prague land inside. The pipeline's centre-search ring probe did not fix them.
  - "Shrinks the STL within the grid" only holds at a fixed pixel count; 7x on a 2 km plate at 8 m/cell is a 1755 grid.
  - The overlap bias (best score rises as overlap falls by chance) exists only because a 1.5x frame cannot offer reach; in a wide window every candidate is scored over 100% of the plate and bare correlation needs no floor or null.
- **Rejected:** overlap floor (47-49 of 84 displacements recovered); chance-calibrated z against mirrored maps (38 of 84); fixed evaluation region trimmed by the reach (46 of 78) — each traded one bias for another.
- **Supersedes / superseded by:** per-city numbers corrected by [2026-09-10](#2026-09-10--search-plates-are-drawn-at-the-exporter-scale-and-heading-not-oversized) (plate was 1.5x oversized); method stands.
- **Source:** decisions.md 2026-09-08 entry.

### 2026-08-30 — Plate grid angle is read from the FFT ridge over the central half
- **Decision:** measure a plate's street-grid angle from the ridge angle of the FFT power spectrum over the central half of the raster only.
- **Why:** it is the only estimator that reconciled with whole-plate correlation.
- **Rejected:** Sobel gradient-angle histogram mod 90 over the whole plate — the letterbox padding's horizontal edges dominate (every plate ~ -1.5 deg); same after eroding the valid region 12 px — buildings clipped at the boundary leave straight edges.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry, "Two grid-angle estimators failed".

### 2026-08-30 — rot_deg is the negation of the turn angle, and the solved rotation must reach the export
- **Decision:** the convention stands: `rot_deg` is the negation of the angle passed to `tools/align_tool/locate_buildings.py::turn` (the sweep turns by `-deg` and records `deg`; `tools/align_tool/locate_buildings.py::fit_rotation` is in the same convention). The defect was that `solve_center` returned only lat/lon and the exporter hardcoded `rot_deg = 0.0`. The fix is to ship the rotation; the exporter now builds the guess with `cv2.getRotationMatrix2D` from `tools/align_tool/locate.py::measured_rotation_deg`.
- **Why:** Philadelphia Miniature needs +9 deg (grid angle -1.25 vs OSM -9.25; masked NCC 0.481 at +9 vs 0.118 at 0). The solver exported -9.33, i.e. a turn of +9.33 — right. Micropolitan Philadelphia is already in the city frame (0.537 at 0). Only plates not drawn in their city's own frame are affected.
  - The contact-sheet render that showed the wrong rotation applied `rot_deg` directly and un-negated: wrong twice.
- **Rejected:** "fix" the sign in `fit_rotation` or the `rot_deg` sum — it is coherent; changing it would break a working function.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-28 — The pipeline does not depend on hand alignment
- **Decision:** hand-tuning tables stay empty (`PLATE_CENTER`, `CENTER_OVERRIDE`, `PLATE_COVERAGE_OVERRIDE_M` are `{}`); the only per-city human number is `tallest_m`, which no longer drives scale.
- **Why:** blind centre solves land 8-23 m from hand alignments on 4 of 5 cities (Lisbon 72 m). Solver-only Barcelona, Prague, Valencia score mean 0.704 vs 0.618 for the hand-aligned five. What makes a city work is a water cut-out plate.
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28, Trails-entry registration subsection.

### 2026-08-28 — A plate's span must be measured, not assumed (Philadelphia is about 1560 m)
- **Decision:** `tools/align_tool/export_align_data.py::PLATE_COVERAGE_M` (2000 m for every plate) is an assumption to be replaced by a measured span; Philadelphia exported at 1560 m.
- **Why:** Philadelphia IoU 0.233 (range 0.521-0.737). Two causes: no `*_L_Water.stl`, so `tools/align_tool/locate.py::resolve_center` fell through to the geocoder centroid ~5.5 km off; and the span. A wide building search reads 1560 m; the fine sweep pins at its 1700 m floor. At City Hall + 1560 m: refined IoU 0.444, precision 0.852 (highest of any city), r 0.574, z 14.7, margin 9.9, sweep reads 1.010. Still refused by the fan-reach gate (1 of 8 starts agree).
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28, Trails-entry registration subsection.

### 2026-08-28 — The fine stage needs a start within about 50 px and an axis-aligned plate
- **Decision:** treat the fine stage's reach and rotation-blindness as limits, not tuning: widening the coarse `SEARCH_MARGIN` requires raising `tools/align_tool/locate.py::MAX_SEARCH_GRID` in the same change; the start-agreement gate is known to measure the start fan's reach.
- **Why (stress harnesses, controls reproduce shipped behaviour to 0.01 px):**
  - Coarse stage minds a coarser haystack, not a bigger one: margin 7 -> 14 at 8 m cells lands every city (Lisbon's margin 4.1 -> 1.8 would be refused while right); under the 2048 cap the cell coarsens to 13.7 m and Lisbon is lost by 2316 m.
  - Fine-stage basin: 50 px disc start -> 81% recovered, 100 px -> 50%. Failures recur at the same few distances (1.86, 2.86, 5.52, 17.87, 20.74 px): fixed rival optima.
  - Rotation is not detected: 2 deg destroys the answer (12% recovered) while gates accept 94%; acceptance only collapses past 5 deg. It works only because packs are axis-aligned.
  - `tools/align_tool/refine_guess.py::refine_arrays` fans 8 starts within 12 px; a further optimum is reached by one start, `n_agree` reads 1 and a correct fit is refused (Miami at 100 px: 100% recovered, 0% accepted).
- **Rejected:** —
- **Supersedes / superseded by:** superseded in practice by [street-scale placement](#2026-09-11--grid-cities-are-placed-at-street-scale-locally-from-the-pack-pose), which searches +/-6 deg and 400 m explicitly.
- **Source:** decisions.md 2026-08-28, registration subsection inside the "Trails: fetch settings versus view settings" entry.

### 2026-08-28 — A partial OSM fetch is never stored or used as a complete one
- **Decision:** a partial result must not be representable as a valid complete one.
  - `city2stl/osm_raster.py::_rasterize_semantic` and `city2stl/osm_raster.py::_rasterize_elevated_roadways` return `(mask, ok)`; `city2stl/osm_raster.py::get_osm_semantic_masks` caches only when all three fetches answered.
  - The water fetch keeps the best attempt (not the last), writes `osm_building_coverage` and `osm_water_missing` into `meta.json` always, and warns that the placement was fitted against a partial river.
  - `tools/align_tool/eval_registration.py::truth_in_export_frame` rebases the STL side as well as the OSM side, so cross-resolution comparisons work.
- **Why:** Lisbon was cached with vegetation, water and elevated roadways all zero across a frame containing the Tagus; a cache hit never retries. Re-fetched: 0.056 exclusion coverage, IoU 0.503 -> 0.521. Paris at 1024 read 1659 m of corner error from the un-rebased truth; rebased, 35.6 m (vs 36.3 at 512).
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 "Vegetation, not bridges" (failed Overpass fetch) and "Registration is finished" (two silent-failure bugs).

### 2026-08-28 — Registration is finished; what remains is content [superseded]
- **Decision (at the time):** stop working on placement; mean plate-frame IoU 0.650 is near what the two mask definitions can agree on. Remaining gap = content (unmapped buildings, differing footprints, building-scale terrain) → segmentation (Layer 1).
- **Why:** local block matching (64 px blocks, 6 px search) shows no coherent residual warp: coherence 0.17-0.36 in every city (blocks move in unrelated directions); giving every block its own optimal shift (40+ free parameters) buys only 0.003-0.021 IoU. The magenta/yellow fringe on Salzburg's south bank is not a local shift.
- **Rejected:** —
- **Supersedes / superseded by:** superseded by later placement work that found real errors the 8-city eval could not see: [wide window](#2026-09-08--the-search-window-must-be-wide-enough-to-contain-the-answer), [1.5x correction](#2026-09-10--search-plates-are-drawn-at-the-exporter-scale-and-heading-not-oversized), [street placement](#2026-09-11--grid-cities-are-placed-at-street-scale-locally-from-the-pack-pose), [footprint channel](#2026-09-13--footprint-channel-remodelled-standing-coverage-map-and-mesa-plate), [parks](#2026-09-14--parks-are-dont-care-for-the-footprint-channel), and [UI registration](registration-validation.md#2026-09-27--registration-is-a-library-in-city2stl-and-the-ui-never-writes-to-the-align-tool-data).
- **Source:** decisions.md 2026-08-28 "Registration is finished; what remains is content".

### 2026-08-28 — Vegetation and elevated roadways are excluded from the plate mask after refinement
- **Decision:** the export subtracts OSM vegetation and elevated roadways (already fetched by `city2stl/osm_raster.py::get_osm_semantic_masks`) from the plate building mask (`tools/align_tool/export_align_data.py::semantic_exclusion`). Order: refine, then exclude, once.
  - The exclusion is not intersected with "OSM does not call this a building".
- **Why:** the plate mask is a white top-hat, so wooded slopes, viaducts and flats all read built; OSM's labels already existed and were thrown away. Mean plate-frame IoU 0.590 -> 0.650 (as re-exported; Salzburg 0.363 -> 0.537, Miami 0.522 -> 0.577).
  - Vegetation carries almost all of it (65% of Salzburg's false positives, 41% Prague's); elevated roadways matter only in Miami (20%). The Seine/Ronda ribbons were trees, not bridges. Cost: 1.2-4.7% of true building area.
  - Guarding with OSM buildings would inflate the refinement's moving image and contaminate a learned segmenter's target: land cover is side information, footprints are the thing predicted.
  - Must follow refinement: masks arrive in the OSM frame and need the refined transform; the geometric guess is tens of px out.
  - The exporter's `cv2.warpAffine` and the evaluator's NaN-aware `numpy2stl/src/numpy2stl/registration/align/transform.py::apply_transform` disagree by 0.000 of frame on all eight cities.
- **Rejected:** see [second refinement pass](#2026-08-28--refining-again-on-the-cleaned-mask-moves-the-placement) and [more OSM tags](#2026-08-28--further-osm-tags-can-remove-the-remaining-false-positives).
- **Supersedes / superseded by:** park don't-care in the street-placement search added [2026-09-14](#2026-09-14--parks-are-dont-care-for-the-footprint-channel).
- **Source:** decisions.md 2026-08-28 "Vegetation, not bridges, is what the plate mask gets wrong" (registration part, up to the cv2 warp subsection).

### 2026-08-28 — The evaluator also scores segmentation under the predicted transform
- **Decision:** `eval_registration.py` reports a second segmentation block warped by the prediction; read it wherever `overlap_iou` shows pred ahead of truth.
- **Why:** the truth-warped block only isolates segmentation while truth is the better placement; refinement now beats truth on all six verified cities (mean seg IoU 0.429 truth-warped vs 0.550 pred-warped).
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

### 2026-08-28 — A measured span is evidence, not a lock
- **Decision:** the span sweep always runs, even where a hand alignment measured the span; it may overrule the measurement only by winning on both correlation and peak confidence (`tools/align_tool/refine_guess.py::SPAN_OVERRIDE_R` 1.10, `tools/align_tool/refine_guess.py::SPAN_OVERRIDE_Z` 2.0).
- **Why:** skipping the sweep made the measurement unfalsifiable. Salzburg's window was built at 1651 m where the sweep reads 1598 m: r 0.34 vs 0.15, z 12.0 vs 5.5, mask IoU 0.359 vs 0.256.
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

### 2026-08-28 — The agreement gate looks for a rival optimum, not a count of starts
- **Decision:** `tools/align_tool/refine_guess.py::MIN_AGREE` = 3 (was 5), contender tolerance proportional (`tools/align_tool/refine_guess.py::RIVAL_TOL_FRAC` = 0.05, was a flat 0.01). Three is the floor: two starts are seeded on placements the export already believes, so three means one blind start found it independently.
- **Why:** MIN_AGREE 5 rejected Lisbon at z 11.2, margin 7.7, spread 0.2 px. Across eight cities a start either reaches the winner (within 0.001 score, 0.2 px) or lands 6-40 px away scoring 22-49% worse — nothing in between. The count measured how many Nelder-Mead descents didn't stall.
- **Rejected:** —
- **Supersedes / superseded by:** fan-reach limit found later: [fine stage limits](#2026-08-28--the-fine-stage-needs-a-start-within-about-50-px-and-an-axis-aligned-plate).
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

### 2026-08-28 — Flat precision across a threshold sweep means misregistration
- **Decision:** diagnostic rule: if precision stays flat as the threshold rises, the fault is placement, not segmentation.
- **Why:** wrong placement makes the "false positives" real buildings in the wrong place. Lisbon pinned at 0.52 and Salzburg at 0.31; both had per-city ceilings far below the healthy 0.64-0.69 and local roughness separated TP/FP everywhere but there.
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

### 2026-08-28 — The plate's top-hat residual is thresholded by multi-Otsu on a clipped residual
- **Decision:** white top-hat (opening with a metre-sized disc) gives height above local ground; threshold with multi-Otsu after clipping the residual at a high percentile (p95 at the time).
- **Why:** plates carry terrain, so no global deck height. Multi-Otsu lets a heavy tail claim its own class (Miami cut at 1.17 m vs 0.28-0.67 elsewhere). Clipping at p95: Miami 74% -> 99% of the best threshold's IoU, no untailed city moves > 2%. Mean IoU 0.580 vs per-city ceiling 0.588.
- **Rejected:** multiotsu4, clip at p98, log, triangle, triangle_log, p50, matching OSM coverage exactly.
- **Supersedes / superseded by:** clip later tightened: exporter and building locator ask for `multiotsu_p85` (reason in the docstring of `numpy2stl/src/numpy2stl/raster/segment.py::_adaptive_residual_threshold`).
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

### 2026-08-28 — OSM buildings are rasterized by area coverage, and the cache key says so
- **Decision:** 4x supersample, `all_touched=False`, area-averaged down, a cell built where >= 30% covered (`city2stl/osm_raster.py::_RASTER_SUPERSAMPLE`, `city2stl/osm_raster.py::_MIN_CELL_COVERAGE`). The OSM cache stores the rendered raster, so the key carries the supersample factor and coverage cut.
- **Why:** `all_touched=True` at ~5.9 m welds whole blocks; Barcelona's largest welded blob fell 16.7% -> 5.5% of the mask and blob counts roughly doubled.
- **Rejected:** —
- **Supersedes / superseded by:** the same model was adopted for street placement's map on [2026-09-13](#2026-09-13--footprint-channel-remodelled-standing-coverage-map-and-mesa-plate).
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

### 2026-08-28 — Segmentation is scored in the plate frame, warping a building indicator
- **Decision:** warp OSM through `inv(pipeline_guess.matrix)` into the plate grid before precision/recall/IoU; warp a 0/1 building indicator and cut at a half, never the heights (`tools/align_tool/eval_registration.py::segmentation_scores`).
- **Why:** the OSM window is 1.5x wider, so the plate fills ~44% of it and raw fill fractions are incomparable (every early "OSM coverage too low" reading was that). Warping heights with NaN-aware `apply_transform` erodes masks: about a third of the OSM mask vanished (Barcelona 0.317 vs true 0.469). Two independent implementations now agree to 0.015 IoU.
- **Rejected:** —
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 "Building segmentation and the two gates it exposed".

## Rejected hypotheses

### 2026-09-14 — Plate texture can tell tree canopy from roofs
- **Hypothesis:** a height-texture feature separates vegetation from buildings among plate-built cells.
- **Measured:** AUC ("vegetation scores higher") at each pack's placement: height 0.26, abs Laplacian 0.45, 3x3 std 0.46, gradient 0.46, flat share 0.40, height vs 5x5 top 0.39, max gradient 0.28, on height above ground 0.50-0.51. No consistent direction across packs; weakest on wooded packs.
- **Verdict:** refused; third failed roughness filter (after 2026-08-28 Salzburg and 2026-09-11).

### 2026-09-05 — Open space between a plate's buildings can stand in for its missing water
- **Hypothesis:** for packs with an empty plate water mask (nine), water can be taken from the gaps between buildings: distance to the nearest building separates a river (100 m+) from a street (10-20 m), and large-distance seeds are grown back through their gap.
- **Measured:** needed three fixes to work at all: the cut in metres against a residual in mesh units; growth by a fixed distance, not by connected component (every street joins the river); the cut swept until both sides agree on open fraction (the plate builds ~2/3 of its window against the map's ~1/3). Phase correlation against OSM water, bounded to 400 m, with the mirror and quarter-turn control: truth ranked first in 7 of 13 windows against 2.2 by chance. That was the first plate-versus-map score to beat chance. Wins agree with water (Prague 47 m, Salzburg 59 m, Alhambra 55 m); losses are mostly plates water already called misplaced, so placement and metric quality cannot be separated.
  - Restricting to low ground separates river from park cleanly (Paris, Prague, Salzburg, Miami) but scored 6 of 12 and loses Boston, whose harbour was cut out of the plate.
- **Verdict:** not trusted unsupervised; the low-ground variant is not the default. No pack carried a road raster yet (roads came later, with [street placement](#2026-09-11--grid-cities-are-placed-at-street-scale-locally-from-the-pack-pose)).

### 2026-09-05 — The pack placements are already registered
- **Hypothesis:** judged by water (the one landmark with no threshold and nothing for vegetation to confuse), the packs' own placements are right.
- **Measured:** water IoU at the pack placement with no shift, and the move to best fit: Salzburg 0.18 / 229 m (misses only the small streams), Paris 0.17 / 100 m, Prague 0.14 / 286 m, Barcelona 0.20 / 223 m, Miami 0.34 / 331 m, Lisbon 0.34 / 244 m (coarse band, poor), Bilbao 0.06 / 332 m (unrelated at any offset). Eight plates cannot be judged: the plate water mask is empty for Boston, Denver, both Philadelphias, Paris Miniature, the Alhambra; Valencia has almost none; Old San Juan has no water raster.
- **Verdict:** half right. Plates are on the right ground and the right way up (the rivers prove it), but most sit 100-300 m out; only Salzburg is within a couple of cells. (Later work traced the offsets to the solver, not the packs: [2026-09-11](#2026-09-11--grid-cities-are-placed-at-street-scale-locally-from-the-pack-pose).)

### 2026-09-05 — Building-mask overlap or phase correlation can judge a plate's orientation and placement
- **Hypothesis:** an orientation control (fraction of the plate's built mask landing on the reference's, versus mirrors and turns), or phase correlation, shows whether a plate is placed right.
- **Measured:** drawing the comparison overturned the tabulated verdict: the Seine, Vltava, Salzach and Boston harbour fall in the same place on plate and map. Once the plate mask covers half the window or more (up to 84% before the pedestal was removed), every orientation lands on most of the reference, so a mirror scores what the truth scores; the control only discriminated on sparse masks. Phase correlation lifts peaks from ~3 sigma to 7-17 but the surfaces are visibly noise, and it put Paris 347 m out where the river says it is not (a repeating street grid correlates with itself at every block multiple).
- **Verdict:** refused; use water instead. Building masks at 512 cells stay unusable for scoring (the plate merges a block where OSM resolves its buildings). The residual fixes of the same day (erode before the maximum, subtract the 60 m opening) stand as real defects.

### 2026-08-30 — Building fill can place Paris
- **Hypothesis:** blur, span, rotation or dihedral tuning lets building-density correlation find Paris.
- **Measured:** the Eiffel Tower anchors the true centre at 48.86141, 2.29760 (plate max 14.74 vs median 3.29, standing in the Champ de Mars void; row 0 = south confirmed). Pipeline answered 1.1 km ESE. Truth r 0.046 (1.0 sigma) vs a false peak 2.8 km away r 0.221 (4.9 sigma); truth never above 1.8 sigma over blur 0-400 m, span 1400-2400 m, +/-20 deg, four dihedral views. Plate 64.2% built, OSM 65.8%: saturated.
- **Verdict:** refused; not a tuning problem. (Paris was later placed by street-scale matching: [2026-09-11](#2026-09-11--grid-cities-are-placed-at-street-scale-locally-from-the-pack-pose).)

### 2026-08-30 — A miniature plate's water can be recovered from its building mask or height field
- **Hypothesis:** where `Water_v2.stl` is a 684-byte placeholder box (all four miniatures), the river can be extracted from the plate itself.
- **Measured:** `tools/align_tool/locate_buildings.py::plate_buildings` (multi-Otsu) puts water surface and quay walls into the building class: the Seine survives as 0.023 km2 of ~0.3 expected.
  - Largest connected void: 0.131 km2, correlation 8-10 sigma landing 8.8 km from the tower.
  - Distance transform + reconstruct: pulls in the whole street network (31%).
  - Close then open: fuses everything (56%); order must be open then close — which works (elongation separates river from square; recovers Boston Common, Philadelphia squares) but not the Seine, already lost.
  - Low, level, large from the height field: Boston 0.093 km2, Philadelphia 0.097 km2, false positives on Denver; fails on Paris (height range 3.86 vs Boston 9.29 — uniform Haussmann height, tolerance falls inside print noise).
- **Verdict:** refused for Paris; recorded so the attempts are not repeated.

### 2026-08-28 — Buildings are a drop-in replacement for the water cue
- **Hypothesis:** a plate without a water cut-out can be centred by a wide building-mask search.
- **Measured:** on Philadelphia the search peaked at z 3.1, margin 0.9, against the water solver's gates of z >= 8.0 and margin >= 2.5; plausible (~220 m from City Hall) but unverifiable. Same position for every plate lacking water (Philadelphia, Boston/Denver/Paris miniatures with a 12-triangle backing slab).
- **Verdict:** refused as a sole cue.

### 2026-08-28 — Higher raster resolution will raise the plate-OSM agreement
- **Hypothesis:** disagreement at 512 px (5.88 m/px) is quantization.
- **Measured:** round-trip through 256: plate mask loses 0.046 IoU, OSM mask ~0.15 — OSM holds detail the plate lacks, so more resolution accrues to one side. Salzburg's L plate is 4.7 triangles per cell at 512, 1.2 at 1024.
- **Verdict:** predicted to score worse, not better; untested (worth one run when Overpass is healthy).

### 2026-08-28 — Salzburg's leftover false positives are separable by shape
- **Hypothesis:** after vegetation exclusion the leftover is hillside blobs separable by shape.
- **Measured:** 366 components: disagreeing ones are more solid (0.95 vs 0.90), more rectangular (extent 0.85 vs 0.70), smaller (612 vs 1624 m2), same height (0.59 vs 0.56). 141 small building-shaped components. Paris control is separable (846 vs 8460 m2; height 0.60 vs 0.84). Salzburg's sampling is the finest (4.85 m/px).
- **Verdict:** refused; no shape rule for Salzburg.

### 2026-08-28 — Further OSM tags can remove the remaining false positives
- **Hypothesis:** more tag sets (beyond vegetation and bridges) excluded from the plate mask lift IoU.
- **Measured:** ten tag sets; the worthwhile union is net negative, mean 0.647 -> 0.642. `amenity=parking` swallows 12.5% of Miami's true footprints (multi-storey parking); Salzburg gains 0.006. `building:part` explained 0.002 of Salzburg's FP while covering 0.060 of its true buildings.
- **Verdict:** refused; Salzburg's remaining 0.101 of frame (fortress ridge, terraced hillside) needs a slope/shape discriminator, not another query.

### 2026-08-28 — Refining again on the cleaned mask moves the placement
- **Hypothesis:** a second refinement pass after vegetation exclusion finds a better placement.
- **Measured:** 8 cities: mean 0.650 -> 0.650, every city within 0.001, never upward.
- **Verdict:** refused; the pass was written and removed. Cleaning the mask changes what the placement scores, not where it sits.

### 2026-08-28 — A separate x and y scale improves the converged placements
- **Hypothesis:** the placement search is losing IoU; anisotropic scale (e.g. Lisbon's plate aspect) would help.
- **Measured:** refitting mask IoU over (sx, sy, tx, ty) from the refined transform: Lisbon 0.503 -> 0.512, Paris 0.672 -> 0.702, Salzburg 0.363 -> 0.373 with ~1 px shifts, almost all isotropic; sy/sx 1.0022-1.0057 adds 0.001-0.004. Lisbon's per-quadrant IoU spread (0.160) is the smallest checked: uniformly mediocre, not locally broken.
- **Verdict:** refused; what remains is mask content (bridge decks, filled courtyards, forest).

### 2026-08-28 — A threshold can lift Salzburg's vegetation floor
- **Hypothesis:** a roughness or height cut removes Salzburg's forested slopes from the plate mask.
- **Measured:** plate carries twice OSM's building-like area. After the span fix roughness separates roofs (median 0.075) from forest (0.174), but every cut costs more recall than it gains precision; IoU falls at all of them.
- **Verdict:** refused; telling a tree from a roof needs shape or land cover (land cover adopted: [vegetation exclusion](#2026-08-28--vegetation-and-elevated-roadways-are-excluded-from-the-plate-mask-after-refinement)).
