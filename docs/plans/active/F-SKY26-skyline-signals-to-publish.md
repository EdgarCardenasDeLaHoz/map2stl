# F-SKY26 — Skyline research signals into fusion and publishing

**Status**: in progress (2026-10-07)
**Proposal**: [../../proposals.md](../../proposals.md) → Skyline CV → F-SKY26
**Owner module**: `city2stl/skyline/`. The functions involved:
- `floor_bands.py`
- `footprint_detect.py::fuse_heights`
- `_pano/elevated.py::elevated_estimates`
- `_core/height.py::withhold_untagged_street_view`

## Goal
Floor counts, MobileSAM instances and satellite heights appear only on review images. Make them
change the published heights:
- dispute wrong drone readings;
- flag high-rise plots for the fallback;
- fix the tower-behind assignment;
- add satellite readings without letting them outweigh drone readings.

## How drone heights reach publishing today
1. `region_pdf.py::run_region_pdf_report` loads `elevated_seeds`.
2. `_pano/orchestrator.py::_seed_multiview_registration` calls `measure_elevated_seed` once per
   drone seed.
3. `_pano/elevated.py::elevated_estimates` takes the trusted readings, runs `fuse_heights`, and
   drops disputed footprints. For each seed in `used` it emits that seed's raw reading; the fused
   value itself is not published.
4. `_core/height.py::aggregate_building_heights` produces the rows.
5. `withhold_untagged_street_view`:
   - tagged building: the tag;
   - untagged building with a drone reading: the median of the drone readings;
   - otherwise: the T41 prior.
6. The result is written to `heights.json`.

Two constraints follow:
- `elevated_estimates` looks up `by_key[(seed, fid)]` for every key in `used`, so any non-drone
  key needs its own estimate builder.
- Satellite readings in the scratch scorer use `dist_m=100`, so their weight is conf/1e4. That is
  25x a drone reading at 500 m, so a satellite reading always wins.

## Approach, in order (smallest useful step first)
0. **Harness.** Make `Code/claude/scripts/seed_experiment.py` region-aware:
   - `--region` (Miami LiDAR truth from `runs/benchmark/truth/<region>.json`);
   - `--refresh-truth`, `--floors`, `--sat`;
   - new metrics: per-kind σ_log; caught versus false disputes; high-rise precision and recall;
     coverage with independent ≥ 2.
1. **Floors factor-2 check (no new published values).**
   - `floor_bands.calibrate_storey`: a per-city storey height from tag / floors on OSM-tagged
     plots. It does not depend on the camera pose.
   - `footprint_detect.floor_check`: disputes a drone reading that is ≥ 2x off the floors height.
     When the base is hidden, the floors height is a lower bound only.
2. **Shared fusion plumbing.**
   - New reading fields: `kind`, `lower_bound`, `image`.
   - `fuse_heights` anchors on the heaviest reading that is not a lower bound. A lower bound below
     the kept value is ignored. Output gains `independent`.
   - `elevated_estimates` emits only publishable kinds.
3. **Satellite weight.** Calibrate `SAT_REF_DIST_M`: the drone distance at which trusted drone
   readings have σ_log 0.15, the reference in the `weight_scale` formula of decision 2026-10-07.
4. **High-rise flag.**
   - Plots with ≥ 10 floors seen get `high_rise_seen` in `heights.json`.
   - Untagged ones publish max(prior, floors × storey + 3 m).
5. **Floors as weighted readings.** Use the σ table. `FLOORS_PUBLISH_ALONE` is decided by the score.
6. **Floor distance re-credits the tower behind.** A run that climbed onto a farther plot is
   untrusted and re-credited to that plot. This also needs an instance change.
7. **Satellite package.**
   - New `city2stl/height/satellite/`: `scene.py`, `readings.py`, `weights.py`.
   - Offline script `scripts/19_satellite_heights.py`.
   - Opt-in site flag `use_satellite_heights`.
   - Not `height/providers/shadow_height.py`, which is deprecated (2026-08-28).
8. **Camera pose check from floor distance.** Floor-implied distance does not depend on the
   camera's position, heading or height. Log it first, then use it as a gate or tie-break.
9. **Commons photo signature** (floors and widths). Only after an oracle test ranks the true
   camera in the top 3.

## Target files
- Skyline: `city2stl/skyline/floor_bands.py`, `footprint_detect.py`, `_pano/elevated.py`,
  `_pano/stage_cache.py`, `_pano/orchestrator.py`, `_core/height.py`, `region_pdf.py`.
- New: `city2stl/height/satellite/`, `scripts/19_satellite_heights.py`.
- Tests: `tests/test_floor_bands.py`, `tests/test_skyline_footprint_detect.py`,
  `tests/test_skyline_elevated.py`.
- Harness: `Code/claude/scripts/seed_experiment.py`.

## Success criteria
- **Floors check:**
  - catches ≥ half of the trusted drone readings that are ≥ 25 % off LiDAR or tags (Miami seeds
    2/3/4; Cartagena seeds 1/4/5/6/7);
  - false disputes ≤ 5 % of correct readings;
  - no published Cartagena tower lost.
- **High-rise flag:**
  - precision ≥ 0.9 on untagged confirmed footprints ≥ 30 m;
  - withheld MAE on untagged high-rises goes down on `10_benchmark --score-only`.
- **Satellite:**
  - fused within-25 % on LiDAR is no lower than drones alone;
  - no "sat-sat verified".
- Each step lands with its seed_experiment diff recorded in `docs/decisions/building-heights.md`.

## Risks
- Storey height varies from 3 to 4.8 m (Cartagena luxury towers run 4.3-4.8 m). Calibrate per
  band if σ demands it.
- Floors need ≥ 5 px per floor, about 270 m at low-res and 770 m at hi-res, so coverage is
  near-field only.
- Miami labels are thin until truth is refreshed. Miami has no `elevated_seeds` yet; add the drone
  spheres first.
- Earlier occlusion checks (shared top edge, the "behind" flag) failed, so step 6 must be scored on
  the same known cases first.
- Shadow errors are correlated and one-sided. Commons placement was refused once (2026-10-06).

## Progress
- 2026-10-07: plan written from a design review. Floor counts fixed first: storey height from the
  footprint range, one plot per instance (`611bbf9`).
- 2026-10-07, step 0 done: `seed_experiment.py` region-aware (`--region`, `--floors`, `--sat`,
  caught/false-dispute, high-rise P/R, independent coverage); Cartagena metrics identical to the old
  harness. **Step 1 dry run fails:** the floors factor-2 check caught 0 of 50 wrong trusted Miami
  drone readings (only 5-6 of 91 have a floors reading on the same plot) and falsely disputed 2
  correct towers (One Biscayne, SE Financial: floors 46 / 83 m on partly hidden facades counted as
  full); Cartagena has 1-3 wrong readings per set, too few to judge. Not wired. Needs a `base_seen`
  field in `InstanceFloors` and floors coverage on matched plots first.
  - Storey height: Cartagena 4.32 m (LOO sigma_log 0.11, n=5); Miami 3.08 m but sigma_log 1.08:
    Miami floor counts are unreliable.
  - High-rise flag: Miami precision 0.92 (n=12), recall 0.11; Cartagena precision 0.5-0.73.
  - Satellite with today's weight lowers fused within-25 % (0.73-0.89 -> 0.61-0.67) and stereo
    agreements still count as "sat-sat verified" (57-70): step 3 comes next.
  - Miami trusted drone readings: sigma_log 0.74, bias +0.46, 45 % within 25 % (seed_3 sphere worst,
    seed_4 spin best at 86 %): the roof-fill readings are the main error source.
- 2026-10-07, addendum step 2a done: `_core/tiers.py` (`verification_tier`, `independent`,
  `INDEPENDENT`, `tier_fields`); every row of `withhold_untagged_street_view` carries `tier`,
  `tier_methods`, `verified`, `disputed_by`, `prior_disagrees`; `heights.json` has
  `schema_version: 2` and `tier_counts`. Published values unchanged: replaying the old and new
  `withhold_untagged_street_view` on Miami (375 rows) and Cartagena v7 (449 rows) gives identical
  value, source and `street_view_m` on every row. Cartagena v7 tiers: verified_2 3, single 74
  (69 `prior_disagrees`), tag 13, prior 359; Miami (no drone seeds): tag 227, prior 148.
- 2026-10-07, step 2b done: `skyline/survey_heights.py` (`pick_provider`,
  `survey_footprint_heights`, cache `runs/survey/<region>.json`, a failed tile not cached, no 3D
  Tiles); `providers/survey.py::YEARS` / `years_for_bbox`. Same footprints, same tiles: survey_m
  equals the cached truth exactly (Prague 77/77, Benidorm 98/98). **Found:** the p95 depends on
  the tile grid. Prague's newest report groups 79 footprints differently from the run that
  measured the truth: only 30 of 79 equal, one off by 46 m (ČÚZK). 2d must snap tiles to a fixed
  grid, or re-measure truth and survey on one grid, before survey and truth can be compared.
  Miami (USGS EPT 2019), footprints of the run that first measured its truth: 337 of 463
  equal; the rest were re-measured later on other groupings.
- 2026-10-07, step 2c done: every row also has `no_survey_height_m` / `_source` / `_tier` (equal to
  the published value until 2d). `benchmark.py::score_buildings(pred_field=...)` (falls back to
  `effective_height_m` on old reports), `score_survey_rows` (against `tiles_m` only),
  `score_by_tier`, `label_tiers`; `10_benchmark` headline scores `no_survey_height_m` and prints
  "within 25 % by tier"; `benchmark.html` has a tier table. Miami `--score-only` (cache only):
  every headline field identical to the run before the change (n 162, MAE 5.83, median 3.12,
  bias +0.38, within 25 % 0.556). By tier: tag 0.68 (n 81), prior 0.43 (n 81); no drone seeds in
  Miami, so no `verified_2` / `single` rows yet.
- 2026-10-07, step 2b follow-up done: a footprint's survey p95 no longer depends on the other
  footprints in the run (stat version 2, `benchmark.STAT_VERSION`; survey records carry `stat`,
  older ones are re-measured). Three causes, all fixed:
  - tiles were the union of the run's footprints, so the raster's cell size and phase followed the
    grouping. Now `benchmark.tiles_for` uses fixed 500 m world tiles (`tile_cell`) with a fixed bbox
    each (`tile_bbox`: + 60 m pad, edges on the 1 m lattice); a footprint reaching past its tile gets
    its own bbox (`_own_bbox`);
  - ČÚZK's ImageServer answers square-degree pixels over a taller extent; `cuzk_dmp.py` ignored the
    returned transform, so each roof was read 0.56 x its offset from the tile centre too far N/S
    (the 46 m outlier). `_export` now warps from the returned transform; cache namespace `_v2`;
  - the newest EPT project was chosen on the tile bbox (a Keys topobathy project clipping a Miami
    tile won it). Now chosen per footprint: `benchmark.survey_part`, tiles split by project.
  - Proof: same footprint, different sets: Prague 72/72, Benidorm 85/85, Miami 4/4 identical
    (synthetic test too). New vs cached truth `survey_m`: Prague median |d| 2.2 m, p95 88 m (old
    values were misplaced; new values agree with `tiles_m` more often, 25 vs 19 of 84 within 3 m);
    Benidorm median 0.10, p95 5.5 m (69/307 equal); Miami (21 compared, 12 tiles) median 0.03, p95
    1.4 m. Survey truth must be re-measured (Prague above all); keep the cached 3D Tiles values and
    re-read only the survey side (0 3D Tiles requests). Survey fetch: Prague, Benidorm about 1 min
    each; Miami about 1.5 min a tile, about 90 tiles, so 2 to 2.5 h.
  - Cost note: new `footprint_truth` footprints now read full 620 m tiles from 3D Tiles too.
- 2026-10-07, survey truth re-measured at stat 2: `benchmark.refresh_survey_truth` +
  `scripts/19_refresh_survey_truth.py` re-read `survey_m` for every cached truth record of the eight
  cities (5,031 records, all re-read, 0 3D Tiles requests); `tiles_m` kept, status reclassified,
  old caches in `runs/benchmark/truth/<region>.stat1.json`, shift in `<region>.refresh.json`.
  - Confirmed before -> after: Prague 24 -> 30, Benidorm 89 -> 92, Madrid 111 -> 109, La Défense
    97 -> 94, Seattle 165 -> 163, Chicago 933 -> 927, Boston 743 -> 741, Miami 763 -> 774.
  - Median |d survey_m|: Prague 2.19 m (p95 88 m, the ČÚZK misplacement), Benidorm 0.10, the rest
    0.02-0.04. Within 3 m of `tiles_m`: Prague 19 -> 25 of 84 (median diff 9.8 -> 5.3 m), Miami
    716/892 -> 731/910 (26 footprints gained a survey reading: 22 tiles_only -> confirmed); the
    others move by at most 5.
  - Headline (`--score-only --no-tiles`, newest reports, a truth correction, not a model change):
    Miami n 162 -> 168, MAE 5.83 -> 5.62 m, within 25 % 0.556 -> 0.571; Prague n 22 -> 28, MAE
    31.0 -> 33.8, within 25 % 0.09 -> 0.11; every other city within 1.3 m MAE and 1 point.
  - Run notes: 3 processes x 4 tile reads plus EPT's 16 node threads drew S3 connection resets
    (about 15 % of tiles, the Chicago Loop tile three times); failed tiles are not cached and were
    re-read one at a time, the last ones with `lidar_3dep_ept_laspy._WORKERS` = 4. Submitting every
    tile future up front held every raster (4-5 GB a region); `survey_heights._bounded` now keeps
    only the reads in flight.
- 2026-10-07, step 7 done (9563867, 4e41a0d): `city2stl/height/satellite/` (`scene.py`,
  `measure.py`, `readings.py`, `weights.py`), `scripts/20_satellite_heights.py` ->
  `runs/satellite/<region>/readings.json`, adapter `skyline/satellite_fusion.py`, site flag
  `use_satellite_heights` (on for Cartagena), wiring in `elevated_estimates(satellite=)` and
  `withhold_untagged_street_view(satellite=)`.
  - Reproduction: scratch `v1_single.py` + `w4_multi.py` and the library on the same Cartagena
    inputs (67 footprints, 27 tagged >= 40 m, 7 Wayback scenes): 0 differences (lean 65,
    single-scene shadow 30, per-scene shadows 143, multiview 67, consensus 67, combine 62). Readings
    layer vs `seed_experiment.load_sat` on `all_buildings_multi.json`: 1225/1225 identical. The
    library ports the generalised v1/w4 code; against the older Cartagena-only s28/s29 values it
    agrees within 25 % on 68 % of shadows and 52 % of stereo.
  - Cartagena readings: 756 footprints on the cached Bocagrande tiles, 749 with readings (lean 479,
    shadow 358, stereo 715, multiview 274, ls 13), 22 min; scene geometry from scratch s27 (LG01 sun
    fixed 320 / 58.07); `fit_lean` / `solve_sun` ported but not run on Cartagena.
- 2026-10-07, step 8 (Cartagena v8 region run, `runs/region_reports/Cartagena_v8_skyline_report/`):
  seeds 1/4/5/6 elevated (seed_6 moved into `seed_urls`), hi-res spheres (seed_6 at its cached
  pitches -10/-36/-62), Street View cache-only (0 misses), satellite on, 36 min.
  - Tiers v7 -> v8: verified_2 3 -> 38, tag 148 -> 143, single 80 -> 270, prior 353 -> 237.
  - Towers: the 7 published values unchanged (all tagged); verified 1 -> 5.
  - 139 rows changed source (79 prior -> drone, 45 prior -> satellite, 8 drone -> prior,
    7 drone -> satellite); 52 rows publish a satellite height.
  - Low-rise rule (user, 2026-10-07): single readings published, labelled unverified, flagged
    `prior_disagrees` when > 2x the prior: 259 of 270 single rows are flagged. 212 are drone readings
    (median 102 m); on 160 of these all satellite readings are under 40 m: step 6 (tower-behind)
    is the next lever.
  - Tier map: `tiers_map.png`, `tiers_map_bocagrande.png` in the v8 report folder (the HTML report
    shows no tiers yet: 2f).
- 2026-10-07, step 6 done (`b198c17`; floors fix `deb0a26`): `elevated.behind_map` (per seed: the
  footprints >= 1.15x farther over a trusted reading's columns and the height its top row gives
  each) and `elevated.tower_behind`: a reading is left out of fusion when its footprint's confident
  satellite readings (lean/ls/stereo/multiview, conf >= 0.5, >= 3 m; a confident shadow >= 40 m
  blocks) are all under 40 m with the reading > 2x, **and** a farther footprint's tag, any seed's
  trusted drone reading or satellite >= 40 m agrees within 25 % with the implied height.
  `elevated_estimates` takes the raw satellite readings (`region_pdf` passes them now). Harness:
  `seed_experiment.py --remeasure --behind`.
  - Cartagena (saved states, seeds 1/4/5/6/7, 305 trusted readings): 65 readings on 61 footprints
    untrusted; tag-wrong 3 of 4 caught (b0634 69/10 m, b0628 105/42, b0622 74/6); 51 of 141
    readings > 2x every satellite reading; 11 unlabelled; **0 of 14 tag-correct readings** and 0 of
    16 readings near a confident satellite reading flagged. Published towers: same 7 values;
    tagged within 25 %: drone trusted 0.82 -> 0.88, fused 0.73 -> 0.80; verified by 2 seeds
    16 -> 14 (b0116, b0896: two seeds reading the same tower behind, satellite 5.5 / 23.5 m).
  - Miami: no satellite readings, so 0 flagged; every metric identical (trusted within 25 %
    unchanged).
  - Region run v9 (`runs/region_reports/Cartagena_v9_skyline_report/`, cache-only, 0 Street View
    misses): 79 readings on 72 footprints untrusted. Tiers v8 -> v9: verified_2 38 -> 32, tag
    143 -> 143, single 270 -> 223, prior 237 -> 248 (rows 688 -> 646). Singles > 2x prior 259 ->
    211; drone ones 212 -> 164 (median 102.8 -> 98.5 m), with every satellite reading < 40 m
    155 -> 111. Of v8's 212: 12 now prior, 1 satellite, 41 have no drone estimate left (no row in
    heights.json), 158 unchanged. The 7 towers keep values and tiers (5 verified). Map:
    `tiers_map_bocagrande.png` (v8 beside v9).
  - Not done: re-credit to the tower behind. 29 of 65 flagged readings already have the tower
    read by the same seed; where it is tagged the implied height is 1.04-1.26x the tag (6 of 7,
    one 4.5x), but the tower is chosen because its evidence agrees, so a re-credited reading would
    count as a second source by construction. The floors cue (an accepted instance on the top
    matched to a farther plot, + instance change) matched 0 wrong readings and 3 right ones.
  - Still open: 158 of v8's drone singles stay > 2x the prior; most have no farther footprint whose
    evidence explains the top, and Miami has no satellite cue at all.
  - Floors (user review of 16 labels): `InstanceFloors.base_seen` / `.lower_bound` (ground under the
    mask only; the depth podium test reads 0.98-1.03 on every instance), flat strips refused
    (`MIN_ASPECT` 0.1: seed_6 inst 288, a road). OSM levels are not truth (inst 63 "16 fl / OSM 2"
    was right).
- 2026-10-08, steps 4 and 5 done (library and fusion; publishing hook not wired): see decision
  "Floor counts flag high-rises and join fusion; they never publish alone".
  - `floor_bands`: `calibrate_storey` (height tags only), `high_rise_plots` (>= 10 floors, lower
    bounds count, satellite-low veto), `floor_readings`, `high_rise_height` (hook);
    `fuse_heights` floors independence (same seed: one source); `elevated.seed_floors` (stage
    cache `floors`), `floors_info`, `ElevatedEstimates.floors`; orchestrator stores it in
    `elevated_state["floors"]`. Harness: `seed_experiment.py --floors --s45` (floors caches
    refreshed for `base_seen`).
  - High-rise flag: Cartagena precision 0.73 -> 0.89 with the satellite veto (n 9, recall 0.36),
    Miami 0.92 (n 12, recall 0.11), untagged 1.0 (Miami n 2; Cartagena no untagged truth). Hook
    height within 25 %: Cartagena 0.56, Miami 0.33, prior 0.0 / 0.08.
  - Storey: Cartagena 4.13 m (n 6, sigma_log 0.13, base seen by mask or by the seed's reading);
    Miami not reliable (sigma_log 1.01): no Miami floors readings.
  - Fusion: Cartagena drone + floors within 25 % 0.89 -> 1.00, 1 caught, 0 false disputes; with
    satellite 0.83 -> 0.83. The 7 published towers unchanged. `FLOORS_PUBLISH_ALONE` False.
  - **Publisher wiring open:** in `withhold_untagged_street_view`, untagged, no drone reading, no
    publishable satellite, `floors[fid]["high_rise_seen"]` -> `floor_bands.high_rise_height(prior,
    floors[fid])`, source `withheld:high_rise`, with `region_pdf` passing
    `floors=elevated_state["floors"]`. Blocked on the user's call: the single-over-2x-prior rule
    (4a73d5a) would turn every such value back into the prior.
- 2026-10-08, satellite coverage and confidence (review of the Cartagena v8/v9 reports):
  - Palmetto Eliptic (b0598) had satellite readings all along (`readings.json`: lean 136.4 m conf
    0.92, ls 136.1, shadow >= 135.3, stereo 101.5). heights.json lost them: tagged footprints no
    seed read get their row in `region_pdf._write_heights_json` without `satellite` (none of the
    131 / 132 `measured: false` rows of v8 / v9 has it). The `satellite=` argument to `_write_heights_json` (tag
    witness, in progress in `region_pdf.py`) fixes it.
  - Coverage: Wayback tiles only covered the drone-seed area (756 of 3,018 region footprints,
    27 of 34 towers tagged >= 40 m). `20_satellite_heights.py --fetch --add` now fetches each
    scene inside its own outline in its release (`scene.scene_polygon`; 2023-02-19_WV02 covers
    1,274 footprints, the other six all) and measures only the new footprints.
    First pass (`--fids`: every footprint a seed sees and every tower): 922 of 3,018 measured
    (916 with readings, was 749). Seed-seen footprints with readings 393 -> 553 of 559, with a
    lean / ls >= 40 m at conf >= 0.5 32 -> 44. Towers tagged >= 40 m (tags and levels, 34):
    readings 27 -> 34, lean / ls 9 -> 12, lean or multiview 11 -> 14, stereo 11 -> 17. The 7 new
    ones are levels-tagged 45-64 m and read 35-88 m (b1088 ls 61 / 58 m, b2616 ls 53 / 51).
    The whole-region pass (2,269 footprints, one process, ~2.5 s each) writes `readings.json`
    when it finishes; `--recalibrate` follows.
  - Confidence (decision "Satellite lean under 40 m is never confident; stereo under a confident
    tall lean is dropped"): Ravello's lean 38 m at conf 1.0 and Allure's stereo 77 m at 0.83 are
    not podium footprints (both outlines fit their roofs) but peak-shape confidences. Chicago
    LiDAR: lean 15-40 m at conf >= 0.7 within 25 % 1 of 61; stereo 1.25x or more under a confident
    tall lean 1 of 20 (the lean 16 of 20). `readings.calibrate` caps the one and drops the other:
    Chicago stereo >= 40 m within 25 % 0.53 -> 0.57, multiview 0.80 -> 0.84. Cartagena: Allure's
    and Palmetto's stereo (77 / 101.5 m) dropped, Ravello's lean conf 0.3; 35 untagged footprints
    lose "satellite says low" for the tower-behind test (re-check step 6's flags).
  - seed_1 hi-res lost Palmetto's drone reading through its pose, not a gate:
    `refine_on_outline`'s fine pass moves the camera 10 m W for 0.115 deg of misfit (a flat
    valley), turning the 454 m-away footprint 1.3 deg off the tower; 27 of 54 core columns then
    hit a depth step at 36 m, the 27/27 tie goes to "depth" and the median reads 51 m (untrusted).
    Every other pose in the valley reads 128-131 m, trusted. Not the fill cap, tower_behind, the
    trust thresholds, a seam or the outline gate. Undoing small nudges (gain < 0.3 deg) for seeds
    1/4/5 brings Palmetto (131 m) but loses Ravello and adds Allure at 61 m from two seeds: not
    landed. Open: make `measure_footprints` tolerate 1-2 deg of bearing error per footprint.
    Also lost since the r3 spin states: b0776 (tag 100, depth edge, roof fit conf 0.37), b1114
    (tag 190, trusted fill 197 m outvoted by satellite stereo 289 m); Allure, b0628, b1158 lost
    wrong readings.

- 2026-10-08, cadastre floors (low-rise heights): `height/providers/co_catastro.py` and
  `skyline/cadastre_heights.py` (opt-in `use_cadastre_heights`, off); validation and the
  propiedad-horizontal finding in decision "Cadastre floors (Cartagena, AMB) as an opt-in height
  source". Not wired into `withhold_untagged_street_view` / `region_pdf` yet (busy with v10): the
  hook is in the `cadastre_heights` docstring.
- 2026-10-08, publishing rules (the user's choices; decisions "A single reading over 2x the prior
  publishes the prior unless supported", "An OSM height tag one agreeing image reading confirms is
  verified_2", "Footprints a drone seed measured without a usable reading get a prior row"):
  - `tiers.single_withheld` (`4a73d5a`): a single over 2x the prior publishes the prior, the
    reading kept as evidence; `region_pdf._drone_seen_rows` / `_fill_unread_heights`: rows for
    drone-seen footprints with no usable reading; their prior from `_chain_fallbacks` (`c92f224`).
  - `tiers.tag_witness` (`53bb58b`): osm_tag + one drone / lean / multiview / stereo >= 40 m
    reading is verified_2. The high-rise hook is wired (`withhold_untagged_street_view(floors=)`,
    source `withheld:high_rise`), exempt from the 2x rule: the "Publisher wiring open" item above
    is done.
  - `elevated.untrusted_reason` / `_mark_tower_behind` (`2c91aff`): the seed pages say why a
    reading is left out.
  - Cartagena v10: rows 646 -> 1,016; verified_2 32 -> 40, tag 143 -> 133, single 223 -> 42,
    prior 248 -> 801; 302 singles withheld (median 69 m), most of Bocagrande at the ~12 m prior.
- 2026-10-09, refined 2x rule and the lost drone readings (decisions "A single reading over 2x the
  prior publishes the prior unless supported", "A floor count its own seed's reading contradicts
  leaves the fusion"):
  - `tiers.single_support` (`050ccbc`): a single over 2x stays when a validated satellite reading
    >= 40 m agrees (lean conf >= 0.7, ls, multiview conf >= 0.3) or floors flagged a high-rise,
    never on a satellite-low plot; rows carry `single_support`, `high_rise_seen`, `floors`,
    `storey_m`.
  - b0426, b0582 (Hotel Cartagena Plaza), b0635, b0075 lost their drone readings in v10: a floors
    count contradicting its own seed's reading of the plot anchored the fusion.
    `elevated.drop_contradicted_floors` (`72b2477`) leaves such counts out (20 of 145). The saved
    review states (`seed_experiment.py --remeasure`) could not show it (older captures, no
    reliable storey): the cause came from replaying v10's fusion on the run's stage-cache entries.
  - Cartagena v11 (10 min: every elevated stage hit the stage cache): verified_2 43, tag 133,
    single 124, prior 716; withheld 302 -> 218 (median 77 m); 90 singles kept with support (51
    satellite, 39 high-rise flag only); Bocagrande rows >= 40 m 60 -> 127 (v9 215, unverified
    drone singles included); b0426 / b0582 / b0635 verified_2 again, b0075's reading back but
    withheld (unsupported). The 7 towers unchanged. Still withheld: satellite low 90, no
    confident satellite 45, stereo-only agreement 47, lean at conf 0.5-0.69 19.
  - Rejected: a joint camera position fit (decision "A joint camera position / heading / height
    fit ...") and the render-fit height optimiser (decision "A render-fit height optimiser over
    the drone images"; kept as a checker idea only).

## Addendum (2026-10-07): survey LiDAR and verification tiers
The user chose all three ways to handle low-rises: near-field street views (being studied), tiered
verification, and survey LiDAR where it is free. Design review findings:
- **Where survey LiDAR is used today:**
  - the app merge uses 3DEP COPC only, to fill `default` heights (p50);
  - skyline publishing never uses survey LiDAR;
  - every benchmark city has survey coverage.

Steps:
- **2a. Tier labels, no value changes.**
  - `_core/tiers.py::verification_tier` / `::independent`.
  - Tiers: `survey`, `verified_2`, `tag`, `single`, `prior`.
  - `verified_2` counts: drone+drone; drone+satellite (lean, multiview, or stereo at 40 m or
    more); lean+shadow over 100 m; floors+drone from different seeds.
  - `verified_2` does not count: shadow+shadow, stereo+stereo, floors+drone from the same seed,
    street+street.
  - A `single` reading more than 2x from the prior gets `prior_disagrees`.
  - `heights.json` gets `schema_version: 2` and `tier_counts`.
- **2b. Per-footprint survey reader.**
  - `skyline/survey_heights.py`: same p95 statistic as the truth (`benchmark.footprint_stat`).
  - Provider years in `providers/survey.py`.
  - Cached in `runs/survey/<region>.json`. No 3D Tiles.
- **2c. Benchmark stays honest (before 2d).**
  - Every run also writes `no_survey_height_m/_source/_tier`.
  - The `10_benchmark` headline scores the survey-blind value.
  - Survey rows are scored only against `tiles_m`.
  - `score_by_tier` checks that the tiers mean what they say.
- **2d. Publish survey heights.**
  - Opt-in `use_survey_heights`.
  - Survey beats tag, drone and satellite unless suspected stale: OSM `start_date` at or after the
    survey year, `building=construction`, an empty lot later built, or a verified value at least
    2x higher.
- **2e.** Extend tiers as new reading kinds land.
- **2f.** Display tiers in the HTML report, the PDF and the app, with survey attribution lines.

Success criteria:
- the survey-blind headline is identical to before on all 8 cities;
- fresh survey rows are within 25 % of `tiles_m` at least 90 % of the time;
- within-25 % falls in tier order (`verified_2` at least 0.85);
- no `verified_2` rests only on correlated methods.
