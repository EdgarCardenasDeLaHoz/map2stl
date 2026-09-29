# Registration — validation, ground truth, consensus

How a plate placement is checked, and which checks were tried and refused, so a placement is only believed on evidence that could have disagreed with it.
Related: [registration-plate-location.md](registration-plate-location.md), [registration-refinement.md](registration-refinement.md), [plate-critic.md](plate-critic.md), [survey-lidar.md](survey-lidar.md), [building-heights.md](building-heights.md). Ground-truth plan: [registration-learning-plan.md](../plans/active/registration-learning-plan.md).

### 2026-09-27 — Registration is a library in city2stl, and the UI never writes to the align-tool data
- **Decision:** The align tool's placement and check were promoted into `city2stl/registration/` (`street_place`, `osm_model`, `osm_water`, `correlate`, `consensus`, `align_paths`) and are called in-process, not as subprocesses (F-REGION §5).
  - Self-contained modules moved whole; the tool file became a `sys.modules` alias (keeps private names and module state).
  - Split where only a section was needed: locate's OSM-water part → `city2stl/registration/osm_water.py::osm_water`; auto_register's consensus → `city2stl/registration/consensus.py::crop_consensus`.
  - Tool CLIs unchanged.
- **Decision:** A UI placement is returned to the client and saved only through the existing library location route (bbox = the plate's unrotated extent about the placed centre; turn, size and verdict in a new optional `placement` record). Nothing is written to `tools/align_tool/data/`.
- **Decision:** Mesh auto-register writes its HTML report under the cache, one folder per import source; `/reports` lists it as the `mesh_import` root. Never into the batch `Code/_reports/`.
- **Why:** The tool modules used bare sibling imports (`import locate`), so the app could not import them without `sys.path` edits.
- **Rejected:** Star-import facades — lose private names and module state. Subprocess calls — the reason for the move.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-27 entry (F-REGION §5 part); [F-REGION plan](../plans/done/F-REGION-large-areas-hydrology.md)

### 2026-09-04 — The city wall is a separate registration channel, sharper than buildings
- **Decision:** Walls (`barrier=city_wall` vs surveyed ramparts) are matched as their own channel, not merged into the building mask.
- **Why:** Old San Juan survey, seed 952 m off, window twice the plate:
  - buildings only → 25 m, peak 6.5 sigma;
  - walls only → 66 m, peak 23.3 sigma (over 3x sharper, on a tenth of the area);
  - both channels → 15 m (three cells).
- **Rejected:** Folding walls into the building mask — loses the separate, sharper peak.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry "The wall is the strongest registration cue Old San Juan has"

### 2026-08-31 — The building solver scales to a small sparse plate (the Alhambra)
- **Decision:** Five changes in `tools/align_tool/locate_buildings.py`, all properties of small sparse plates, not of Granada:
  - **Wide-phase cell is a fraction of span:** `COARSE_CELL_PER_SPAN` = 1/125, 3 m floor (was fixed 16 m). Every existing pack keeps its cell; Alhambra (592 m) gets 4.6 m / 128 px instead of 73 px with its 5 m wall at a third of a pixel. At 16 m the wide phase settled 1.7 km off.
  - **Agreement tolerance answers to the step sizes:** `agree_px_for` adds the crop displacement caused by half a span step and half a rotation step (swing about the plate centre). Fixed `AGREE_PX` = 6 could not register a plate between two wide-phase rungs (half-steps displace an outer crop ~9 px).
  - **Wide phase returns runners-up:** per-angle bests, deduplicated by ground position at half a span, capped at `WIDE_CANDIDATES` = 4 (each costs an Overpass call). Runners-up do not have to clear the wide gate — the narrow phase re-examines them.
  - **Candidates ranked by share of surviving crops that agree, not accepted on survival:** Alhambra's first runner-up survives at 7/10 on ground 2.5 km away; truth reads 9/10 and lands 4 m from the overlay. Share, not count, because empty ground kills crops before they can disagree. `DECISIVE_AGREE_FRAC` = 0.8 stops early; every earlier pack clears it on its first candidate.
  - **Consensus tile asks for its share of the plate:** `MIN_TILE_OVERLAP` now means share of the plate present in the tile, not tile fill (a turned or outline-cut plate does not fill its box). Improved every pack: Boston 12→16 voting tiles, Denver 7→11, Philadelphia miniature lead 0.81→0.90.
- **Why:** Summed correlation broke a 6/10 tie between the true Alhambra and a denser stretch of Granada 1.2 km away; the information separating them is computed one call later.
- **Rejected:** "First candidate the narrow phase confirms" — surviving is common.
- **Config:** Alhambra coverage 592 m, not 900 m (plate stops short of the Generalife; 32 probes agree within 12 m). Region "Alhambra, Granada, Spain" ("Granada, Spain" geocodes to the cathedral, 991 m off); keeps the `granada_spain` slug via `SLUGS`.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-31 entry

### 2026-08-30 — Registration validates itself by per-tile consensus
- **Decision:** `city2stl/registration/consensus.py::crop_consensus` cuts the plate footprint into a 4x4 grid of OSM tiles and correlates each against the placed plate; a correct placement puts every tile's peak at zero shift. Driven by `tools/align_tool/auto_register.py::register`.
- **Why:** A hand alignment does not exist for most packs, and a correlation score says nothing about whether another position would score as well (in a uniform city one usually does). The Paris miniature passed every export gate sitting a kilometre off.
- **Non-obvious choices:**
  - Tile is the template, plate the fixed image — cropping the reference and re-searching rewards moving inward, so crop votes are biased.
  - Grid over the plate footprint, not the window — 3x3 over the window left Barcelona 3 of 9 voting tiles; over the footprint 11 of 16.
  - Decides on r-weighted lead over the best rival cluster, not the fraction agreeing — scattered dissent is not an alternative. Philadelphia miniature: 3 tiles at zero (r 0.70-0.78) vs 4 weak tiles pointing four different ways → counting says 3/7 fail, lead says 0.80 pass. Barcelona 1.00; Barcelona +60 m → 0.00.
- **It corrects as well:** `city2stl/registration/consensus.py::correct` subtracts the rival cluster's offset (sign found empirically) and keeps the move only if the lead improves. Spoiled by 100/300/700 m → recovered to 0-4 m; over 35% of span (`MAX_CORRECTION_FRAC`) is refused — the window itself is wrong.
- **Two failure kinds reported separately:** dissent that agrees with itself = wrong spot, fixable; dissent agreeing with nothing = wrong window (Denver miniature 10 tiles in 10 directions, Paris miniature 16 in 16), translation cannot help.
- **Calibration:** thresholds fixed before the Denver/Paris miniature exports existed; they came back FAIL and the other 11 PASS unadjusted. Negative controls fail from 60 m up; 3° rotation (~21 m at a tile centre, inside the 34-41 m tolerance) is not flagged, 8° is.
- **Undeclared packs:** `tools/align_tool/auto_register.py::discover_packs` walks pack roots; `tools/align_tool/auto_register.py::adopt` adds them to `CITIES` for the process only (region guessed from folder name, never written back until the plate registers against it). `DEFAULT_TALLEST_M` = 200 is safe — the height channel is normalised before correlation.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry "Registration validates itself by asking pieces of the map separately"

### 2026-08-30 — A solve is checked against random positions for distinctiveness
- **Decision:** Diagnostic: correlate each plate where the solver put it and at 4000 random positions in the same window (same mask and rotation, letterbox bars excluded). Measures whether the answer is *distinguished*, not whether it is right (shares the solver's metric).
- **Why:** Half the plates have no hand alignment and three water plates are scored against the pipeline's own cached solve, so metres of error do not always mean what they seem.
- **Findings:**
  - Every accepted city plate: 4.3-11.0 sigma, beaten by 0/4000.
  - Philadelphia miniature: ACCEPTED at 1.0 sigma, 18% of random positions score higher — weakest no-water pass, first to re-examine if the gate tightens. Position still corroborated: micropolitan Philadelphia solves the same window to within 130 m, both on City Hall.
  - Denver miniature: rejected at 3/16 crops, yet 4.1 sigma and unbeaten, 6.3 km from downtown — a strong wrong optimum, which is what a self-similar window produces. The gate correctly refuses what correlation alone would endorse.
- **Lesson:** Three rendering faults (row 0 = south mirrored everything; OpenCV BGR legend; window only required to contain the plate centre) were in the diagnostic, not the pipeline. A diagnostic disagreeing with a validated result is more likely wrong; attack it first.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry "Ranking each solve against random positions"

### 2026-08-27 — The opening transform is refined against the images, with four acceptance gates
- **Decision:** `tools/align_tool/refine_guess.py::refine_arrays` fits the plate footprint to the OSM building mask: translation always, span only where nobody measured it (`tools/align_tool/refine_guess.py::span_is_measured`).
  - Masked normalised cross-correlation (Pearson statistics inside the plate support) makes scores at different spans comparable; plain FFT correlation zero-means over the whole frame and favours a larger template.
  - Water is excluded from the span sweep (a river scores the same at every span once it can slide along itself) and used only in the translation fit and as a veto.
- **Gates:** coarse peak z ≥ 4 and margin ≥ 1; ≥ 5 of 8 starts in one basin scattering < 4 px; movement < 25 px; water overlap may not fall > 0.01. Starts > 0.01 below the best are stalled descents, not rivals (Valencia has one at r 0.11 vs 0.35).
- **Why:** The geometric placement is only as good as the centre and span behind it. Result: 7 of 8 accepted; Lisbon rejected on a weak peak and keeps its geometric placement (known aspect-ratio defect).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry "The opening transform is refined against the images"

### 2026-08-27 — The drag tool opens on a geometric placement, not on the registration backend
- **Decision:** `tools/align_tool/export_align_data.py` computes `pipeline_guess` arithmetically: the OSM window is `osm_margin` (1.5) times the plate footprint on the same centre, so scale = 1/1.5 = 0.6667, tx = ty = 85.33 at 512 px, rot = 0. The backend transform is kept as `meta.register_transform` for comparison only.
  - Scale read from the pipeline's `known_scale`; deriving it from `plate_span_m` came out 0.3% short (rounded at different points).
- **Why:** The centre already came from `locate` under a confidence gate and the window was built around it; a backend that moves the plate only adds error. Measured on eight plates (water IoU and building correlation): geometry wins everywhere, e.g. Lisbon 0.375 vs 0.013 (the backend invented a -26.59° rotation — "why does Lisbon look flipped?"), Prague 0.138 vs 0.000, Paris 0.186 vs 0.011; Bilbao and Miami tie.
  - Against rebased hand alignments: within 5.4 m on every real one (Bilbao 5.4, Lisbon 3.3, Miami 4.6, Salzburg 3.4). Paris 0.1 m is too exact to count (record is the guess plus a nudge; redo it). Barcelona 2500 m because its record was a fake — see [plate-critic.md](plate-critic.md).
- **Rejected:** Opening on `rep.registration.transform`.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry "The drag tool opens on a geometric placement"

### 2026-08-27 — The align export merges into align_data.js and never replaces it
- **Decision:** Full and subset export runs both merge; a city is removed from align_data.js only deliberately.
- **Why:** An overwriting full run deleted three cities' good exports when their OSM fetch failed.
- **Related:** the export's water overlay gets fewer Overpass attempts than a solve (it is a human-facing overlay the tool works without); a solve keeps the full budget.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-27 entry "The align export merges into align_data.js"

## Rejected hypotheses

### 2026-09-04 — Comparing a plate's buildings against a lidar survey is not evidence
- **Hypothesis:** Ten cities have a surveyed surface over the plate window; the plate's building layer can be scored against it.
- **Measured:** Terrain half passes (bare earth within 0.18 m of France's survey, 0.35 m Massachusetts, 1.06 m Andalucia, at 20 m spacing). Building half fails three ways:
  - IoU ≈ 0 — surveys return the top of whatever stands (Alhambra 54% vs the plate's 9% built): mostly woodland.
  - One-way confirmation (28-74%) tracks the survey's standing share — any mask of that size scores it.
  - Chance-corrected confirmation favours the least-built plate; a three-quarter turn of the Alhambra beat truth 4:1.
  - **Control:** score each plate vs the same plate mirrored and quarter-turned. Truth ranks first in 6/10 by ~1 point (noise); on peak sharpness 3/30. Against OSM's own footprints (the friendliest reference) cut masks rank truth first in 0/20; full-plane FFT 0/10 with best offsets of 1296 m (Denver) and 2467 m (Paris miniature). Correlating heights instead of cut masks: 3/10, peaks 3.2-4.4 sigma, but only after moving 170-300 m.
- **Side fixes worth keeping, neither the fault:** anchor metres-per-unit on the one-cell-eroded maximum, not a lone spike (moves Prague a third, most cities < 5%); subtract a 60 m-disc opening to remove the residual's 1.5-8 m pedestal (Boston built share 78% → 18%, Alhambra 30% → 8%).
- **Verdict:** Do not report any plate-versus-survey building number until a mirror/rotation control passes. The surveys are sound; the blocker is upstream registration of the `_align_tool/data` plates. Correlate heights, never cut masks. See [survey-lidar.md](survey-lidar.md).

### 2026-08-29 — Widening the rotation sweep would help failing plates
- **Hypothesis:** Vendors rotate the city to square the grid to the plate, so `ROTATIONS` (-12..+12°, 2° steps, in `tools/align_tool/locate_buildings.py`) may clamp failing plates (Philadelphia miniature reported -9.33°).
- **Measured:** Dominant grid axis of plate vs window from a gradient-orientation histogram (shares no code with the solver), folded to (-45, 45]: Denver needs 0.00° (most trustworthy row, sharpness 4.42/4.23); Paris miniature -0.5°; Boston miniature 0°. The only row beyond the sweep (micropolitan Philadelphia +14.5°) is a plate that already passes at 0° — low sharpness, mixed grids.
- **Verdict:** Refused; `ROTATIONS` stays. Denver is square to its window and still cannot localise — supports the self-similar-window diagnosis. The Philadelphia miniature's -9.33° stays flagged untrustworthy; a wrong angle moves corners, not the centre.

### 2026-08-29 — A tuning lever can recover the Denver miniature
- **Hypothesis:** Some span, rotation, erosion or clip setting will make Denver localise.
- **Measured:** 17 span rungs all at noise; rotation to ±12°; erosion walks window fill past the plate's with no peak; clip percentiles 95-80 leave it at 3-4 of 16, 6.3 km out. Rendered: the plate mask is good (LoDo's rotated grid visible); the window is a uniform residential texture with no coast, wide river or large park.
- **Verdict:** A fourth failure mode — self-similar window — a property of the place. The gate refuses it correctly. Recovery needs a new signal (street centrelines, block orientation), i.e. a new matcher, not tuning.

### 2026-08-29 — The miniature packs share a common print scale
- **Hypothesis:** Boston miniature solves at 2884 m (~1:16,900 on 170.5 mm), so the others would solve if held at that scale.
- **Measured:** At Boston's 2773 m rung Denver 2/16, Paris miniature 2/16, Philadelphia miniature 2/14 — noise. Boston has a clean isolated spike (13/16 vs 3); no other pack spikes on any of 17 rungs.
- **Verdict:** Refused. The span sweep does necessary work and cannot be replaced by a constant.

### 2026-08-29 — Matching plate and window building density makes a plate solve
- **Hypothesis:** The one solving miniature has window fill closest to its plate's, so erode the window until they agree.
- **Measured:** Philadelphia miniature gets worse when matched (3/14 at 7 km at fill 0.146 vs plate 0.156; 4/14 at 2.3 km unmatched). Micropolitan Philadelphia degrades monotonically with erosion (9→7→3→4→4 of 16). Decisive: micropolitan Philadelphia passes 14/16 at fill ratio 0.69 while the Paris miniature fails at 0.60.
- **Verdict:** Refused — fill ratio does not predict whether a plate solves.

### 2026-08-29 — The assumed span distorts the plate mask
- **Hypothesis:** `numpy2stl/src/numpy2stl/raster/segment.py::terrain_residual` sizes its opening in metres from an assumed 2000 m coverage, so an unknown-coverage plate is segmented wrongly.
- **Measured:** Plate fill moves ≤ 0.03 across a 4.4x range of assumed span (Boston 0.215-0.229; Philadelphia miniature 0.189-0.156).
- **Verdict:** Refused — a nominal span is harmless; the sweep may look anywhere.
