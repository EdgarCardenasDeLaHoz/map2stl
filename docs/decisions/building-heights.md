# Building heights

Where per-building heights come from (OSM tags, raster providers, Google 3D Tiles, shadows), how
providers are merged and ranked, and how height accuracy is measured. Related:
[survey-lidar.md](survey-lidar.md) (surveyed lidar references), [roofs-landmarks.md](roofs-landmarks.md)
(roof geometry). Research notebook behind the shadow entries: [../research/shadow-heights.md](../research/shadow-heights.md).

### 2026-10-08 — Cadastre floors (Cartagena, AMB) as an opt-in height source; propiedad horizontal never published
- **Decision:** `city2stl/height/providers/co_catastro.py` reads the AMB cadastre `Construccion`
  layer (datos.gov.co `d7hk-qg8h`: range reads of ~25 MB of a 1.2 GB zip, cleaned layer cached
  90 days) and matches it to model footprints: the largest-overlap polygon or the union of the
  parts lying inside, IoU >= 0.3; floors = the tallest part. Height = floors x storey + roof:
  - storey 3.0 m + 1 m roof; colonial Centro, San Diego and Getsemaní 4.5 m; over 10 floors
    4.13 m (skyline storey calibration) + 3 m;
  - published only for non-PH predios with 1-10 floors (confidence 0.7 for 1-5, 0.6 for 6-10).
    Propiedad horizontal (digit 22 of the predial number is 9) and towers never publish;
  - skyline: `cadastre_heights.py`, site flag `use_cadastre_heights` (off). A publishable match
    replaces the prior, so the 2x rule now withholds a lone drone reading over twice the cadastre
    height; a confident satellite reading >= 40 m that disagrees vetoes it. Cadastre + one drone,
    lean, multiview or stereo >= 40 m reading is `verified_2` (the user's rule). Reports print the
    CC BY-SA 4.0 attribution and share-alike line (`tier_display.height_attributions`).
- **Why (v9 region, 3,018 OSM footprints; tables in the 2026-10-08 report):**
  - Match: 2,092 footprints (69 %), 1,363 publishable, 719 PH. 55 % of the 12,427 cadastre
    polygons in the box touch no OSM footprint (Bocagrande 20 %, Manga 15 %, Centro 3 %).
  - Publishable vs OSM `height` tags: n 8, median abs error 0.5 m, 88 % within 25 % (all under
    10 m, in Espinal). No height tag on a publishable match in the target barrios.
  - PH vs tags: n 16, median abs error 28 m, 19 % within 25 %. Hotel Estelar (52 floors), Gran
    Bay Club (42) and Nautica read 1 floor. Seed_6 drone pano: PH "1 floor" b0748 and b0666 are
    12-15-floor blocks, PH "4 floors" b0791 about 10.
  - Towers: 16 of 48 matched v9 confirmed / tagged rows >= 30 m read <= 3 floors; median abs
    error 29 m. Floors are not a tower source here.
  - R2 floors (`yk9q-cw89`) on non-PH matches: 82 % exact, 97 % within one floor (n 1,175; same
    office, so a consistency check). R2 has no floors for PH units.
  - OSM `building:levels`: 33 % exact, 85 % within one (n 52). In the colonial barrios the
    cadastre is lower in 26 of 47 (OSM 2 / cadastre 1: 13).
  - Satellite (weak at these heights): multi-scene shadows vs <= 5-floor matches, median abs
    error 3.2 m, 26 % within 25 % (shadows read 7-8 m for 1- and 2-floor houses alike);
    stereo / multiview conf >= 0.5, median abs error 2.0-2.5 m; 13 of 249 stereo readings are
    >= 40 m on a "low" match (a tower's podium polygon): hence the veto.
  - On v9 it would change 117 prior rows (median 12.1 m; cadastre median 7 m lower) and 62 lone
    drone readings of 25-233 m on 1-3-floor cadastre houses. Of those 70 singles, 39 have a
    confident satellite reading under 25 m (the building is low), 10 one over 25 m.
- **Not adopted:** a synthetic `building:levels` tag (`height_from_tags` has one 3.2 m storey plus
  a roof level, and the row would count as OSM-tagged and train the prior); `altura_tot` (97 %
  are the 3.0 m default); +1 m for commercial ground floors (`height_m(commercial=)` exists,
  unused: on the shadows commercial and other 1-floor predios read 7.3 and 7.5 m).
- **Open:** colonial floor counts and the 4.5 m storey are unchecked (no tags, no drone or
  satellite coverage of the old town): spot-check 20-30 facades before relying on them there.
  The publishing hook (`withhold_untagged_street_view(cadastre=)`, `region_pdf`) is written out
  in the `cadastre_heights` docstring for the owner of those files.

### 2026-10-08 — Satellite lean under 40 m is never confident; stereo under a confident tall lean is dropped
- **Decision:** `city2stl/height/satellite/readings.py::calibrate` (applied in
  `footprint_readings`; `20_satellite_heights.py --recalibrate` for stored files):
  - a lean under 40 m keeps its height but its confidence is capped at 0.3 (`LOW_LEAN_MAX_CONF`;
    the peak-shape value stays in `extra.conf_peak`);
  - a stereo or multiview reading that a lean of >= 40 m at conf >= 0.7 is 1.25x or more above is
    dropped; its height stays in the lean's `extra` (`stereo_under_lean`).
- **Why:** Cartagena review of v8/v9. Ravello (published 144 m) had lean 38 m at conf 1.0; Allure
  (180 m) had stereo 77 m at conf 0.83 beside a correct lean of 168 m. The confidences are peak
  shapes, not reliabilities. Chicago LiDAR (`S/satellite_cities/val`, stored v1/w4 results):
  - lean 15-40 m at conf >= 0.7 is within 25 % of truth 1 of 61 times (44 % under 0.6x), 3-15 m
    1 of 16; of confident (>= 0.5) leans under 40 m, 57 of 110 are on buildings of 40 m or more;
  - with such a lean 1.25x or more above it, stereo is within 25 % 1 of 20 times, the lean 16 of 20.
  - Effect: stereo >= 40 m within 25 % 0.53 -> 0.57 (n 479 -> 449), multiview 0.80 -> 0.84
    (51 -> 49); the 66 dropped stereo/multiview readings were within 25 % 3 % of the time, their
    leans 55 %. Confident leans under 40 m 108 -> 0. Miami's reference scene has no lean (near
    nadir): no change there.
- **Not podiums:** both Cartagena footprints fit their roofs (lean crops,
  `S/sat_cov/lean_*.png`). Ravello's 38 m is a bright feature part way up the facade; Allure's 77 m
  stereo a lower match. A "footprint much larger than the matched roof" rule would not catch
  either; the cross-method rule does.
- **Consequence:** the cap changes neither fusion (the weights table already drops lean < 40 m)
  nor the tiers (they ignore confidence); a dropped stereo is one satellite reading fewer in both.
  The tower-behind test (`elevated._sat_max`, conf >= 0.5) no longer takes such a
  lean as "satellite says low": 35 of Cartagena's 749 Bocagrande footprints (none tagged) lose
  that cue. Re-check step 6's flags with `seed_experiment.py --remeasure --behind`.
- **Supersedes / superseded by:** refines the 2026-10-07 satellite weights and stereo entries.

### 2026-10-08 — Floor counts flag high-rises and join fusion; they never publish alone
- **Why:** F-SKY26 steps 4 and 5. Untagged towers no drone read fall to the ~12 m prior; floor
  counts (MobileSAM instances, `floor_bands.pano_floors`) say which plots hold towers and give a
  second, pose-free height.
- **Changed:**
  - `floor_bands.calibrate_storey` (a city's storey from OSM *height* tags, never levels;
    leave-one-out sigma_log), `high_rise_plots` (>= 10 floors, a lower bound counts, `low=`
    veto), `floor_readings` (floors x storey + 3 m; lower bound when the base is hidden; sigma_log
    = hypot(calibration sigma, strip spread), weighted as a drone reading at 3300 x sigma m; none
    when the calibration's sigma_log > 0.25), `high_rise_height` (the publisher's hook).
  - `footprint_detect.fuse_heights`: `kind` "floors" readings carry their `seed`; they are one
    source together, counted only when one comes from a seed with no drone reading kept (`group=`
    merges captures from one camera); `floors_only` in the result.
  - `_pano/elevated.py`: `seed_floors` (stage-cached, `ElevatedSeed.floors`), `floors_info`,
    `elevated_estimates` returns an `ElevatedEstimates` list with `.floors` (`high_rise_seen`,
    `floors`, `storey_m` ...) and `.storey`; `orchestrator` puts it in `elevated_state["floors"]`.
- **Base in view** = ground under the mask (`InstanceFloors.base_seen`) **or** the same seed's
  reading of that plot saw its base. The mask test alone left Cartagena 2 calibration samples
  (140 of 153 counts lower bounds); with either: 85 lower bounds, 6 samples, storey 4.13 m,
  sigma_log 0.13 (reliable). Miami: 10 samples, sigma_log 1.01: not reliable, so no Miami floors
  readings (the hook uses the nominal 3.1 m there).
- **Satellite veto on the flag:** Cartagena's 3 flagged tagged plots under 30 m (b0634 10 m,
  b0639 10 m, b0645 6 m) all had their base hidden: the count was the tower behind the low
  building. A plot whose confident satellite readings are all under 40 m (`elevated._sat_max`,
  the tower-behind test's rule) is never flagged; it removes b0634 and b0639 (stereo 5.5 / 7 m).
  b0645 stays (stereo 49 m, shadow 56 m: its 6 m tag may be the wrong one).
- **Measured** (`seed_experiment.py --floors --s45`; Cartagena hi-res states seeds 1/4/5/6/7, truth
  = tags + published; Miami 6 states, truth = confirmed LiDAR; prior for the hook 12 m):

  | high-rise flag (>= 10 floors) | flagged | with truth | precision | recall (truth >= 30 m) | untagged precision | hook within 25 % (prior) |
  |---|---|---|---|---|---|---|
  | Cartagena, no veto | 113 | 11 | 0.73 | 0.36 (22) | n 0 | 0.45 (0.18) |
  | Cartagena, satellite veto | 72 | 9 | 0.89 | 0.36 | n 0 | 0.56 (0.00) |
  | Cartagena, base seen only | 57 | 6 | 1.00 | 0.27 | n 0 | 0.83 (0.00) |
  | Miami (no satellite) | 21 | 12 | 0.92 | 0.11 (101) | 1.00 (n 2) | 0.33 (0.08) |

  - Fusion, drone + floors vs drone only: Cartagena fused within 25 % 0.89 (n 9) -> 1.00 (n 8),
    sigma_log 0.44 -> 0.06; 10 footprints newly disputed, 1 with truth (b1158: drone 183 m, tag
    45 m: caught), 0 false; 24 newly verified (2 with truth, both within 25 %). With the
    satellite readings too: 0.83 -> 0.83 (n 6), sigma_log 0.114 -> 0.119, 9 newly disputed, none
    with truth. Miami: unchanged (no readings).
  - Published towers: the 7 publish their tags, unchanged. Fused values: Gran Bay 159 -> 176
    (170), Portomarine 188 -> 178 (162.5), Nautica 161 -> 159 (160), Ravello 146 -> 164 (144),
    Estelar 198 (202) unchanged; Allure gets a floors-only lower bound of 98 m (180), not
    published.
  - Floors-only footprints: 47 (42 untagged); the 2 with truth are lower bounds reading 37 % low,
    none with the base seen has truth.
- **Verdict:**
  - high-rise flag: >= 10 floors, lower bounds count, satellite veto. Precision 0.89-0.92 on all
    plots with truth, 1.0 on Miami's only 2 untagged confirmed ones (Cartagena has no untagged
    truth): the >= 0.9 target is met only on small samples. 63 untagged Cartagena plots flagged,
    22 of them with no drone reading.
  - floors in fusion: on (`elevated.FLOORS_IN_FUSION`), only where the storey calibration is
    reliable. `FLOORS_PUBLISH_ALONE` = False.
  - publishing: the hook is written, not wired into `withhold_untagged_street_view`. It
    conflicts with the 2026-10-08 rule "a single reading over 2x the prior publishes the prior":
    every high-rise height (>= 10 x 3.1 + 3 = 34 m) is over 2x a 12 m prior, so wiring it needs
    the user's call on exempting `withheld:high_rise` rows from that rule.
- **Source:** plan F-SKY26 steps 4/5; outputs `<scratch>/out/seed_exp/s45_cart.json`,
  `s45_miami.json`.

### 2026-10-07 — Commons photo screen loosened after the user's review of 38 rejects
- **Why:** the user reviewed 38 Commons photos the photo pipeline had rejected (Miami, Chicago,
  Cartagena; review item set c3): 25 were usable skyline photos.
  - wide (> 3:1) 6 of 6 usable; no location 7 of 8; camera fit failed 7 of 12; "night" 3 of 6;
    camera far from region 2 of 4; not a skyline 0 of 1; portraits 0 of 1.
- **Changed** (`commons_photos.screen`, `pipeline_status`, `fit_prior`, `skyline_quality`):
  - wide panoramas fit as cylindrical views (FOV guessed from the aspect at 35 deg vertical,
    +-60 % free); not split into crops: a crop of a cylindrical image is not pinhole either.
  - unlocated photos, and geotags > 200 km away (Cartagena p107 carries Cartagena, Spain), go
    to a placement queue: linked refinement or the EXIF + 0.1-lead search (the gate from
    "Placing no-GPS Commons photos with trusted heights or LightGlue"), else
    `placement_queue.json` "manual_label" (labels place to +-5 m).
  - cameras up to 20 km outside the region (was 15): the usable Chicago one sat at 16.3 km, the
    two the user agreed were too far at 29.9 and 36.3 km.
  - night: no pixel-dark drop. A low-light photo (sky value < 170) is kept when its skyline
    clarity (edge step across the outline x coverage) is >= 0.11. The three "night" photos the
    user called usable were bright (sky 194-207; EXIF-hour night); the three rejected are
    low-light and score 0.02-0.108; Chicago's usable night panorama scores 0.110 (thin margin,
    tuned on these verdicts). Day photos are not gated on clarity (23 % of day outlines score
    < 0.11 in haze, unreviewed). No previously kept Miami or Chicago photo is lost to it.
  - located photos without EXIF focal length now reach the fit (FOV 55 deg, +-50 % free).
  - camera fit: when the EXIF FOV fails the misfit gate, retry at +-40 % (Commons crops keep
    the full frame's EXIF).
- **Camera-fit failures examined** (the 4 located usable ones, 4 the user agreed were bad as
  controls; refine within 500 m unless stated):
  - p234: a black-and-white photo, SegFormer found no sky (outline flat at row 0, misfit 1.0):
    segmentation, not the camera. Now "flat outline", manual-label list.
  - p78: EXIF 16.8 deg, fits 25 deg at misfit 0.28 (passes at +-40 %); p276: EXIF 74 deg, best
    50 deg (a crop; misfit 0.32 in the pipeline, kept by the OSM-agreement rescue; its bases are
    hidden by a cruise ship); p170: 0.54 at best (unexplained).
  - +-40 % FOV: no control passed (0.40-0.93). A wider radius (1.5 km) moved poses 1.6-2 km
    and turned heading 100 deg: not used.
  - **Roll (T19) refused here:** a +-3 deg roll scan let the control p121 pass (0.93 -> 0.24)
    and p79 nearly (0.67 -> 0.37); it fits wrong poses.
  - the 3 unlocated ones (p2, p3, p15) fail the 0.1 lead at 0.01: manual-label list (p15 is
    still measured through the rescue gate).
- **Result** (re-run on the cached outlines; Chicago's new fits at camera heights 2 and 30 m):
  - screen, Miami: fit 30 -> 62, placement queue 71 -> 169, rejected 199 -> 69; Chicago: fit
    153 -> 439, queue 229 -> 719, rejected 1,315 -> 539.
  - photos fitted / kept: Miami 101 / 27 -> 130 / 44 (towers read 502 -> 844; MAE 14.0 -> 15.3 m,
    pair order 0.92 -> 0.91); Chicago 415 / 147 -> 686 / 277 (towers 2,979 -> 5,884).
  - the 38 reviewed: 25 match the user (was 13); 16 of 25 usable no longer rejected, 9 of 13 bad
    still rejected.
- **Refused:** roll in the fit (above); crops of panoramas; a clarity gate on day photos.
- **Source:** user review 2026-10-07 (review items c3_*, `Code/claude/review_feedback/`); scratch `t40f/`.

### 2026-10-07 — A drone reading of the tower behind is untrusted when the satellite says low and a farther footprint explains its top
- **Decision:** `_pano/elevated.py::tower_behind` leaves a trusted drone reading out of fusion when
  both hold:
  - its footprint's confident satellite readings (lean, ls, stereo, multiview; conf >= 0.5; >= 3 m,
    lower is a failed match) are all under 40 m, the reading is over 2x their largest, and no
    confident shadow says >= 40 m;
  - a footprint >= 1.15x farther over its columns (`behind_map`) would have the reading's top row as
    its top at a height its own evidence matches within 25 %: OSM tag, any seed's trusted drone
    reading, or a satellite reading >= 40 m.
  No re-credit: the reading is dropped, not moved to the tower.
- **Why:** each test alone fails; together they flag no right reading (saved states of Cartagena
  seeds 1/4/5/6/7 and Miami seeds 2/3/4 and spheres, every trusted reading):
  - geometry alone (a farther footprint with matching evidence) flagged 6 of 14 tag-correct
    Cartagena readings and 20 of 37 LiDAR-correct Miami ones: adjacent towers explain each
    other's tops, as the refused shared-top-edge check found;
  - satellite-low alone is unsafe on 40-90 m towers whose stereo failed (b0289, tag 90 m: stereo
    and multiview 2 m); 9 of 16 tagged 40-80 m footprints have every confident reading < 40 m;
  - together: 65 readings on 61 footprints, 0 of 14 tag-correct, 3 of 4 tag-wrong, 51 of 141 over
    2x every satellite reading. Published towers unchanged; tagged within 25 % fused 0.73 -> 0.80.
  - Region run v9 vs v8: single 270 -> 223, drone singles > 2x prior 212 -> 164 (all satellite
    readings < 40 m: 155 -> 111); verified_2 38 -> 32; the 7 towers unchanged.
- **Rejected:**
  - re-crediting the height to the tower behind: the tower is chosen because its evidence agrees,
    so the re-credited reading would verify it by construction (29 of 65 towers already read by
    the same seed);
  - the floor-distance cue of the F-SKY26 design (below, Rejected hypotheses);
  - an instance change as a requirement: present on 7 % of the readings over 2x the satellite.
- **Limits:** needs satellite readings, so Miami (none yet) is unchanged; 158 of v8's 212 drone
  singles stay, mostly with no tagged, satellite-tall or drone-read footprint behind them.
- **Supersedes / superseded by:** extends the tagged-tower `_untrust_behind` (commit `9238e00`).
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) step 6.

### 2026-10-07 — A floor count is a lower bound unless ground is seen under the instance
- **Decision:** `floor_bands.InstanceFloors.base_seen` is true only with ground (not building, not
  sky) under the mask; otherwise `lower_bound`. Instances flatter than `MIN_ASPECT` (0.1 rows per
  column) are refused. OSM `building:levels` is not truth for floors.
- **Why:** user review of 16 floor labels: 8 right (including seed_6 inst 63 "16 fl / OSM 2"), 7 too
  few floors with the base or lower floors hidden, 1 a road (seed_6 inst 288, 18 rows over 276
  columns; the flattest accepted building is 0.14). The depth "own podium" test read 0.98-1.03
  under/inside on all 27 instances checked, partial or not, so it does not mark a base seen.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) Progress, 2026-10-07.

### 2026-10-07 — Satellite heights go live in the skyline run, opt-in per site
- **Decision:** the region run reads offline satellite readings when the site sets
  `use_satellite_heights` (on for Cartagena). It never measures or fetches imagery itself.
  - Producer: `city2stl/skyline/scripts/20_satellite_heights.py` (library
    `city2stl/height/satellite/`) writes `runs/satellite/<region>/readings.json`, cached by scene
    names and dates. Free Esri Wayback / World Imagery only, 4 concurrent requests.
  - One reading per footprint and method: lean (reference scene), shadow (all scenes merged to
    one lower-bound reading), pair-consensus stereo, multiview peak (conf >= 0.3, >= 40 m: sigma_log
    0.08 / 0.10 from the Chicago table), and `ls` when lean and shadow agree.
  - Drone fusion: the readings join `fuse_heights` as `sat_*` pseudo-seeds on footprints a drone
    read, at `dist_m = max(100, 3300 sigma_log)`; they can outvote and so dispute a drone reading.
    Only drone readings are published from fusion.
  - Tiers: drone + satellite (>= 40 m) and lean + shadow (> 100 m) count as `verified_2`; two
    satellite readings of one kind never do (`tiers.INDEPENDENT`, unchanged).
  - Untagged rows with no drone reading publish the satellite-only height
    (`withheld:satellite`, tier `single`) only when the best kept reading's sigma_log is <= 0.25;
    otherwise the T41 prior stays and the readings only label the row (`satellite`).
  - Low-rises (user, 2026-10-07): an unverified low-rise publishes its single reading, labelled
    unverified (tier `single`), flagged `prior_disagrees` when it is more than 2x from the prior
    estimate. This was already the behaviour; kept as the rule.
- **Why:** the weights (entries below) were validated on 7 LiDAR cities; the library reproduces
  the validated scratch code exactly (Cartagena, 67 footprints, 7 scenes: 0 differences in lean,
  per-scene shadows, multiview, consensus and combine; readings layer 1225/1225 identical to the
  harness `load_sat`). Opt-in because readings exist only where scenes are cached and solved.
- **Not done / rejected:**
  - publishing noisy satellite-only readings (sigma_log > 0.25: low-rise shadows at conf < 0.7,
    15-40 m shadows) over the prior;
  - the Cartagena-only scratch variant (`s28`/`s29`, behind the old `all_buildings_multi.json`):
    the library ports the generalised v1/w4 code the weights were scored on. On Cartagena the two
    agree within 25 % on 68 % of shadow readings and 52 % of stereo readings (stereo under 40 m is
    dropped anyway).
  - the Cartagena scene geometry comes from scratch `s27` (lean from satellite heights; LG01 sun
    fixed at bearing 320, el 58.07, visually verified), not from `scene.fit_lean` / `solve_sun`
    (ported, not yet run on Cartagena).
- **Consequence (Cartagena v8 vs v7):** same 7 published towers and values (all tagged); towers
  verified 1 -> 5 (Estelar drone seed_1 + seed_6; Gran Bay drone + lean; Portomarine two drones;
  Nautica drone + stereo; Ravello drone + stereo). Region tiers (all rows, v7 via
  `benchmark.label_tiers`): verified_2 3 -> 38, tag 148 -> 143, single 80 -> 270, prior 353 -> 237
  (584 -> 688 rows: seed_6 and hi-res spheres read more footprints). 52 rows publish a satellite
  height. Open: 212 drone `single` rows are > 2x the prior (median 102 m); on 160 of them every
  satellite reading is under 40 m, which suggests tower-behind credit (F-SKY26 step 6), but those
  satellite readings are too noisy to dispute a drone at 400-900 m.
- **Supersedes / superseded by:** extends the two satellite entries below.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) step 7 and 8.

### 2026-10-07 — Two agreeing satellite shadows do not verify a height; satellite readings are weighted by method and height
- **Decision:** a building counts as verified by two images only when independent methods agree:
  two drone seeds, drone and satellite, or satellite lean and shadow on a tall building. Shadow
  agreement across scenes alone does not count. In fusion, a satellite reading gets
  `weight_scale = min(1, (0.15 / sigma_log)^2)` by method and band:
  - lean over 100 m: 1;
  - lean 40-100 m: 0.35-0.9 by confidence;
  - lean under 40 m: dropped;
  - shadow over 40 m: 1;
  - shadow 15-40 m: 0.05;
  - shadow under 15 m: 0.03-0.5 by confidence.
  A shadow is a lower bound and never outvotes a higher photo reading. Several shadow scenes of
  one building count as one reading.
- **Why:** tested on 1,794 LiDAR-confirmed buildings in 7 cities (Prague, Miami, Seattle,
  Madrid, Benidorm, La Défense, Chicago).
  - One satellite reading is within 25 % of truth 37 % of the time; two agreeing readings 64 %.
  - Miami shadows from different scenes agreed on 8 towers over 100 m and 1 was right: a shadow
    cut by a podium or neighbour is cut the same way in every scene. Shadows read 20-40 % low.
  - Lean over 100 m is within 25 % 94 % of the time (Chicago WV03). Where lean and shadow agree
    on a building over 100 m, 26 of 27 are right.
- **Rejected:** counting any two agreeing satellite scenes as verified. On Cartagena that gave
  214 "sat-sat verified" buildings, overstating the result.
- **Supersedes / superseded by:** —
- **Source:** scratchpad satellite_cities/validation.md (2026-10-07), copied to
  `Code/claude/scratch_backup_2026-10-07/`. Multi-scene stereo is unvalidated: Miami's archive is
  near-nadir orthos, and Chicago's scenes are not fetched yet.

### 2026-10-06 — Drone fusion and trust rules for elevated seeds
- **Decision:** `city2stl/skyline/footprint_detect.py::fuse_heights` overrules by a factor `overrule=3`.
  - A reading 3x off the other seeds' agreeing value is dropped.
- **Decision:** `city2stl/skyline/_pano/elevated.py::trusted` accepts a roof reading only with confidence >= 0.5 and distance <= 1000 m.
- **Decision:** the overhead camera fit (`_pano/elevated.py::overhead_pose`) is used when the waterline camera height is < 15 m, the waterline fit fails, or the height is > 130 m.
- **Why:**
  - Gran Bay: seed_4 read 20 m at a depth edge, seed_6 read 170 m from a confident roof fit; the tag agrees with 170.
  - Untrusted readings are wrong (median 200-400 % against tags), so the gates are not relaxed.
- **Rejected:** looser trust gates — coverage has to come from more images, not weaker gates (see Rejected hypotheses, 2026-10-06).
- **Supersedes / superseded by:** —
- **Source:** T43 in [TASKS](../agents/TASKS.md); `city2stl/skyline/_pano/roof_fit.py::fit_roof_heights`.

### 2026-10-03 — Skyline height accuracy is judged on surveyed truth, not on Cartagena
- **Decision:** skyline (street-view) height changes are scored on eight cities with per-footprint
  truth from survey lidar and Google 3D Tiles, cross-checked: Miami, Chicago, Seattle, Boston,
  Benidorm, Paris La Défense, Madrid Cuatro Torres, Prague Pankrác. Cartagena stays a heading and
  registration smoke test only.
- **Why:** user, 2026-10-03: Cartagena "has poor ground truth for building heights, it might
  confuse results". Its truth is 22 OSM `building:levels` × 3.4 m values and no open survey exists.
  Earlier skyline decisions rested on about 20 such buildings.
- **Rejected:** OSM height tags as the yardstick (sparse, floor-count estimates); one truth source
  alone (the user chose both, so new construction and ground-estimate errors show as disputes).
- **Supersedes / superseded by:** —
- **Source:** [F-SKYBENCH](../plans/active/F-SKYBENCH-height-benchmark.md)

### 2026-09-27 — Google 3D Tiles is allowed as a height and geometry source
- **Decision:** Google Photorealistic 3D Tiles stay a supported provider
  (`city2stl/height/providers/google_3d.py::Google3DProvider`), usable for heights, roof geometry and
  as a label source (ray-cast shadow labels, roof-pitch measurement).
- **Why:** user decision 2026-09-27, reversing the earlier "off limits / not approved" note. The
  project owner had already determined in Aug 2026 that Google's terms do not bar this use, the work
  being non-commercial research ([shadow notebook](../research/shadow-heights.md#google-3d-tiles-as-a-label-source)).
  Scope of that determination: research use, not redistribution of a derived dataset or a
  commercial product built on one. Revisit if the project's purpose changes.
- **Rejected:** treating the tiles as barred by ToS — the restriction was never written down
  anywhere in the repo (only listed as an unexplained item in
  [AUDIT-2026-05-17](../history/audits/AUDIT-2026-05-17.md)).
- **Supersedes / superseded by:** supersedes the "Google 3D Tiles probe: not approved, do not start"
  line in claude/memory-bank/activeContext.md and the AUDIT-2026-05-17 "can't be used (ToS)" item. No
  decisions.md entry had forbidden it.
- **Source:** memory project_building_heights.md; claude/memory-bank/activeContext.md (2026-09-27);
  [F-LANDMARK plan](../plans/active/F-LANDMARK-roofs-and-building-parts.md) "Where it stands".

### 2026-09-04 — The WSF3D per-tile endpoint is a 453-tile sample, so a global fallback reads the BigTIFF
- **Decision:** when every one-degree WSF3D tile 404s, `city2stl/height/providers/wsf3d.py::WSF3DProvider`
  falls back to range reads of the single global BigTIFF (`city2stl/height/providers/wsf3d_global.py::read_grid`,
  86.58 m/px, Int16 × 0.1), cached under the same namespace. A failed global read returns the old empty
  raster — a coarse provider that raises would take the whole merge down.
- **Why:** the tiles directory lists only 453 entries; Frankfurt, Rotterdam, Panama, Miami, Dubai
  all 404'd and were silently read as "no settlements". Range reads fetch a few hundred kB per 3.5 km box.
  Frankfurt after the fix: 245 099 cells, p90 33.1 m, max 226.7 m, 21 s cold / 1.4 s cached.
- **Rejected:** opening it with GDAL `/vsicurl/` — the Windows GDAL build's schannel fails the DLR
  chain's revocation check and every CA/verify env var failed; disabling certificate verification
  was deliberately not done. Reads use `tifffile` + `imagecodecs` over a small range adapter.
- **Note:** changes no current output where fine providers exist — `resolution_priority(90)` = 0.236,
  below GBA's 0.55, so a 90 m product never outranks a 3 m one.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry (landed 2026-09-05).

### 2026-09-04 — An OSM height is a floor, not an estimate
- **Decision:** treat tagged OSM heights as a lower bound when comparing sources.
- **Why:** in Old San Juan, buildings with a height or storey tag claim 3.48 m less than the lidar
  survey at the median (MAE 4.99 m), with a consistent sign. Part is the per-storey constant, part
  roof structure the survey sees.
- **Note:** the survey was measured when levels × 3.0 m was the rule; F-ARCH made it 3.2 m per level
  plus one roof level (`city2stl/heights.py::height_from_tags`,
  [F-ARCH](../plans/active/F-ARCH-consolidation.md)), which closes part of the gap. The number was
  not re-measured.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 "OSM building heights in Old San Juan run about three and a half metres short".

### 2026-09-04 — A two-stage high-rise router works only fitted per city, and is not landed
- **Decision:** do not ship a low/high-rise classifier that routes tall footprints to
  `max(fine, wsf3d)`. If revisited, fit it per city on that city's own OSM tags.
- **Why:**
  - Oracle bound on Cartagena: MAE 6.43 → 4.62 (tall 24.83 → 14.87); break-even at ~80 % specificity / 60 % sensitivity.
  - Rebuilt on height-independent features (footprint geometry, neighbourhood, satellite
    appearance), cross-validated in Cartagena: 5.15 at sens 0.70 / spec 0.96 — 71 % of the oracle gain.
  - Does not transfer: Miami-trained model gives 6.51 in Cartagena. Miami leans on `log_area`
    (0.564), Cartagena on `halo_dark_p90` (0.608).
  - Gain is 1.01 ± 0.89 m with 60 labels (110 tagged, 20 tall) — one standard deviation. Needs a
    second city with both WSF3D and OSM tags to validate.
- **Rejected:** using Overture/GBA samples (or their presence flags) as features — AUC 0.914 but
  zero gain; routing gain correlated −0.415 with the model's confidence. A model that corrects an
  estimator must never see that estimator's output, since coverage is itself a proxy for "the
  estimator works here".
- **Lesson:** shadow presence is useless as a regressor (MAE 265.84, corr −0.347 at 59° sun) yet the
  best single classifier feature: detection survives conditions that destroy measurement.
- **Supersedes / superseded by:** the router approach is superseded by the [high-rise mask](plate-critic.md#2026-09-04--the-high-rise-mask-is-a-real-signal-with-no-downstream-consumer); the "do not ship the router" decision stands.
- **Source:** decisions.md 2026-09-04 entry (scripts were scratch highrise_*.py, not kept).

### 2026-09-04 — The merge stays per-pixel; Cartagena's tall bias is missing data, not policy
- **Decision:** keep `city2stl/height/__init__.py::merge_height_rasters` per-pixel with priority =
  confidence × `resolution_priority`.
- **Why:** Miami (1492 tagged footprints vs Cartagena's 110) settles it: the same merge reads Miami's
  tall band at +5.18 m bias, MAE 10.36, and wins overall (2.62 vs footprint-max 2.79). Overture covers
  23.9 % of Miami up to 265 m; in Cartagena 1.6 % and GBA tops out at 87 m. Where a
  building-resolution product has data the merge is right; where not, nothing recovers the towers
  without inflating the low city.
- **Rejected (all measured in Cartagena):**
  - lifting `BUILDING_RESOLUTION_LIMIT_M` (10 m) to admit WSF3D — no measurable change;
  - provider reordering — a 90 m source can never outrank a 3 m one, identical numbers;
  - per-footprint priority selection — worse in both bands;
  - WSF3D only on large footprints (area gate) — smooth trade, never a win (1600 m² gate: tall
    24.83 → 17.85, short 2.35 → 4.79, all 6.43 → 7.16); old-town blocks are large and short.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-09-04 — The IQR outlier fence was removed from the height merge
- **Decision:** `city2stl/height/__init__.py::_filter_outliers` keeps only the below-ground and >600 m
  checks; the `iqr_scale` parameter was deleted (not defaulted off) so no caller can reintroduce it.
- **Why:** quartiles over all pixels track the ground (most pixels ≈ 0 m), so Q3 + 3·IQR cut off
  the skyline. Unfiltered: Miami MAE 45.61 → 35.48, corr +0.306 → +0.662, tallest 147 → 233 m (tag
  max 228); Cartagena MAE 34.68 → 20.09, corr +0.345 → +0.773, merged max 36.5 → 190 m.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-09-04 entry.

### 2026-08-30 — GlobalBuildingAtlas admitted at confidence 0.55, below Overture
- **Decision:** GBA.LoD1 is a registered provider (`city2stl/height/providers/gba.py::GBAProvider`),
  read from the Source Cooperative GeoParquet mirror, ranked below Overture (0.60).
- **Why:** it is the only globally complete per-footprint source. Against Miami tags Overture scores
  MAE 5.86 / corr +0.912, GBA 25.90 / +0.489 and under-predicts by 67.4 m above 50 m — but pixels
  above 50 m are 18 % of Miami, 0.31 % of Cartagena, 0 % of Tunis, where GBA is actually needed.
  The parquet has per-row-group bbox stats (pyarrow pushdown); it is WGS84 despite the upstream README
  claiming EPSG:3857.
- **Rejected:** upstream HuggingFace GeoJSON / mediaTUM zips (5° tiles of 0.7-2 GB, no index);
  scaling confidence by the `var` column or correcting the tall bias — both would be fitted on one
  city, the same mistake as the per-city `roof:height` calibration.
- **Rule:** score per-footprint sources against the plate's `osm_heights` (same grid, zero
  registration error), never the warped plate. The warped plate reversed this ranking (GBA 22.0 vs
  Overture 39.8, both corr ≈ 0.1); the symptom was the two providers agreeing with each other
  (+0.519) far better than with the reference.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Height provider priority accounts for resolution, and sources coarser than 10 m are refused
- **Decision:**
  - merge ranks on `confidence × min(1, (5 / resolution_m) ** 0.5)` (`city2stl/height/__init__.py::resolution_priority`);
  - `city2stl/height/service.py::enhance_city_data` refuses providers coarser than
    `BUILDING_RESOLUTION_LIMIT_M` (10 m), so a city with only coarse sources keeps the 10 m fallback;
  - the emitted confidence stays unscaled — resolution decides who wins a pixel, not whether it is
    usable, so downstream `min_confidence` gates keep their tuned meaning.
- **Why:** a 30 m nDSM (0.80) outranked 5 m Open Buildings (0.60); it averages roofs with streets
  (median 2.0-2.4 m), which `city2stl/heights.py::enhance_buildings_with_raster`'s 3 m clamp turned
  into a field of 3 m boxes. Plain 10 m fallback beat it in 7 of 7 plate cities. Re-ranking alone was a
  near no-op (nDSM covers 100 % of every plate, Overture 20-61 %); the 10 m gate moved the error.
  Evidence tables: bug 3 in [issues.md](../issues.md).
- **Rejected:** a global shrinkage toward the constant — best k spans 0.00-0.90 across seven cities,
  fitted on plates already documented as untrustworthy referees ([plate-critic](../research/plate-critic.md)).
- **Principle:** for per-building heights, resolution matters and source reputation is no substitute.
  Median absolute error cannot referee "is this estimator informative" — at low correlation a constant
  beats an unbiased noisy estimate by construction; use correlation and best-k.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-30 — Height coverage is measured from height_source, weighted by built area
- **Decision:** coverage metric = share of built area whose `height_source` is real OSM (`osm_tag`,
  `osm_levels`) or our estimate (`merged`) vs the 10 m constant (`default`); set in
  `city2stl/heights.py::_fill_heights`.
- **Why:** needs no ground truth, so works for every city, not just the eight with plates. Weight by
  area: a thousand tagged sheds and one untagged tower are not 99.9 % coverage.
- **Rejected:** measuring coverage against a plate.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-30 entry.

### 2026-08-28 — Shadow heights stay a research track; the old shadow provider stays deprecated
- **Decision:** do not promote satellite shadow heights to a production provider;
  `city2stl/height/providers/shadow_height.py` stays deprecated.
- **Why (Miami, ESRI 2025-01-08 scene, 41° sun):**
  - accepted estimates: n=17 of 122 footprints, MAE 17.48 m, corr +0.792 vs the plate — coverage is the
    real limit;
  - ray-cast ablation: the length operator is near exact (1.82 m on isolated shadows); shadow merging
    downtown costs ~30 m, imperfect segmentation about as much again;
  - a U-Net segmenter only helps if its receptive field spans the shadow reach
    (`max_height / tan(elevation)`); 2 m/px was the working point; segmentation IoU does not predict
    height error (shown three times);
  - cross-city transfer failed on the class prior; sun elevation has to be an input.
- **Findings worth keeping:** ESRI's `identify` endpoint returns the scene date (Google Static does
  not); sun elevation is solved from the shadow bearing + date; score plates with the `register`
  alignment, not `truth` (the latter correlated −0.017 with OSM tags). Old provider had five disqualifying
  defects (fixed June/10:00 sun, azimuth unused, no footprint association, long shadows rejected,
  heights clamped to 1-50 m).
- **Rejected:** greedy tallest-first occlusion claiming (MAE 62.5 → 75.5); quantile profiles;
  first-local-edge length; fitting acceptance thresholds to OSM tags (fits reference noise — plate and
  tags disagree by 26 m).
- **Supersedes / superseded by:** see the Cartagena failure under [Rejected hypotheses](#2026-09-04--satellite-shadow-heights-work-in-cartagena).
- **Source:** claude/memory-bank/shadow_heights.md → [../research/shadow-heights.md](../research/shadow-heights.md).

### 2026-08-28 — Google 3D Tiles fetch is level-order, depth tied to the output cell, faces sampled, tiles binned one at a time
- **Decision:** in `city2stl/height/providers/google_3d.py`:
  - walk the tile tree level-order, fetching each level's routing documents together;
  - target geometric error = 6 × output cell (`_target_error_m`, `_ERROR_PER_CELL`);
  - sample each face in proportion to its area (`_surface_points`), keeping vertices for exact ridges;
  - download in bounded chunks and fold each mesh into one accumulator (`_accumulate_mesh`).
- **Why:**
  - depth-first: 1505 requests / 131 s for a 1500-tile budget; level-order 31 s, and an exhausted
    budget leaves a coarser but complete surface instead of one detailed corner;
  - finer targets are worse under a fixed budget (Miami: 32 m target → 99 % fill in 28 s, MAE 14.01;
    8 m → 82 % in 240 s, MAE 14.72); 6× sits at the coarse end of the flat range (64/32/16 m →
    13.79/14.01/14.02) so the choice does not rest on noise;
  - vertex-only binning filled 19 % of the grid at coarse levels; face sampling 99 % on the same fetch;
  - holding every mesh until the end made a 4000-tile run die in trimesh's cache hashing.
- **Rejected:** the original fixed 4 m target (from the tiles' own resolution, not the raster's).
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsections inside "Vegetation, not bridges" (misfiled there).

### 2026-08-28 — Outside Google's photorealistic coverage the provider returns empty, not flat
- **Decision:** `city2stl/height/providers/google_3d.py::_looks_built` measures relief in block-sized
  windows (250 m); if under 10 % of windows exceed 8 m, return an empty result with a warning.
- **Why:** outside covered cities the same endpoint serves a global base mesh that looks like a
  successful fetch — over Cartagena a surface flat to 1 m for a skyline of 40-storey towers. A wrong
  height that looks like a measurement is worse than a missing one. Measured relief: ≥35 m in every
  covered city, ≤4.3 m in every uncovered one.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection "An empty raster is preferred to a flat one".

### 2026-08-28 — Provider accuracy is scored with the tag filter disabled and split by tagged/untagged
- **Decision:** score every height source on the `nofilter` region report and split every claim by
  whether OSM tags the building.
- **Why:** the skyline tag-disagreement filter makes accuracy against tagged buildings partly
  self-fulfilling. This test established Overture as independent of OSM: better on untagged
  buildings than tagged, and matching no OSM tag exactly.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-28 — Google 3D Tiles ground comes from the mesh, not from a DEM
- **Decision:** ground = a low percentile (5th) of mesh altitudes in a ~200 m neighbourhood
  (`city2stl/height/providers/google_3d.py::_ground_from_dsm`); a caller may pass a DEM to override.
- **Why:** tiles are WGS84 ellipsoidal; project DEMs are orthometric; the geoid separation is tens of
  metres and city-specific (≈ −26 m at Miami), so DEM subtraction adds an uncalibratable bias. Mesh
  ground cancels the datum.
- **Limit:** assumes terrain varies less across the neighbourhood than buildings rise; fails in hills,
  where DEM + geoid model is the honest answer.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-28 — An unmeasured building ground means the camera's ground, not sea level
- **Decision:** skyline heights default a building's unknown ground to the camera's ground plane,
  flagged by `city2stl/skyline/_core/types.py::BuildingRecord` `terrain_known`; elevation lookups
  return `None` on failure, not 0.0.
- **Why:** 0.0 is silently wrong (adds the camera's elevation to every height; plausible at the
  coast, gated away inland). Same-ground cancels the absolute term; error is bounded by local slope.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-28 subsection.

### 2026-08-05 — STL and OSM height units are deliberately different; do not "fix" them
- **Decision:** STL heightmaps keep raw mesh Z units, OSM heightmaps metres;
  `city2stl/osm_raster.py::derive_scale_m_per_unit` is not applied to the STL heightmap.
  `numpy2stl/src/numpy2stl/registration/compare.py::compare` reconciles them by robust regression
  (`stl_m = stl * height_scale + offset`), recorded as `ComparisonResult.height_scale_used`.
- **Why:** Barcelona's 3.83 vs 100.0 max is expected; the fitted scale should land near 26 m/unit. A
  value near 1.0 means too few buildings matched — a registration symptom, not a units bug.
  Segmentation never compares the two heights and its threshold is Z-scale invariant.
- **Measured:** Barcelona coverage OSM 0.642 vs STL 0.754 — over-segmentation ≈ 0.11, replacing the
  earlier "83.3 % of the raster" figure.
- **Status 2026-09-28:** still holds after F-ARCH (numpy2stl is geo-free; registration takes rasters).
  The F-ARCH 3.2 m/level change affects OSM tag heights only, not this units split.
- **Supersedes / superseded by:** —
- **Source:** decisions.md 2026-08-05 entry.

## Rejected hypotheses

### 2026-10-06 — Relaxing the trust gates to get more coverage
- **Hypothesis:** loosening the confidence and distance gates of `_pano/elevated.py::trusted` adds usable readings.
- **Measured:** untrusted readings are wrong: median error 200-400 % against tags.
- **Verdict:** refused; coverage must come from more images (more seeds, the satellite cross-check T44), not looser gates.

### 2026-10-06 — A shared-top-edge occlusion check
- **Hypothesis:** a trusted reading whose top row matches a farther footprint's top row is an occluder and wrong.
- **Measured:** caught none of the 5 wrong trusted readings and flagged 25 good ones.
- **Verdict:** refused.

### 2026-10-07 — Floor-implied distance alone as the tower-behind test
- **Hypothesis:** an accepted floor instance covering a reading's top, matched to a farther plot,
  shows the run climbed onto that plot (F-SKY26 step 6 design).
- **Measured:** 0 wrong readings matched, 3 right ones did (none of either with the instance change
  required); seeds have 0-38 accepted floor instances.
- **Verdict:** refused; the satellite + evidence test is used instead.

### 2026-10-06 — The "behind" flag for occluded tops
- **Hypothesis:** flag a tower top that lies behind a nearer surface.
- **Measured:** over-flagged (good readings marked).
- **Verdict:** removed.

### 2026-10-06 — Street-fit camera cost from SegFormer road labels
- **Hypothesis:** fit the drone camera to the street network using SegFormer road pixels.
- **Measured:** the cost surface is flat; the road mask is too patchy.
- **Verdict:** refused; the overhead roof fit (`_pano/elevated.py::overhead_pose`) is used instead.

### 2026-10-07 — Benchmark survey truth re-measured with the stat-2 survey p95
- **Decision:** every cached truth record's survey side is re-read with stat version 2
  (`benchmark.refresh_survey_truth`); the cached 3D Tiles side is kept and each record reclassified.
- **Why:** stat-1 survey values depended on the run's footprint grouping (tile bbox set the raster's
  cell size and phase), ČÚZK rasters were read 0.56 x their N/S offset off (Prague, up to 46 m),
  and the EPT project was chosen per tile (commit `3c1a017`). Re-reading only the survey side
  costs no 3D Tiles requests (~81 % of the monthly free cap used).
- **Measured:** confirmed records Prague 24 -> 30, Miami 763 -> 774, other cities -6 to +3;
  survey within 3 m of 3D Tiles Prague 19 -> 25 of 84. Headline: Miami MAE 5.83 -> 5.62 m, within
  25 % 0.556 -> 0.571 (n 162 -> 168); Prague MAE 31.0 -> 33.8 m (n 22 -> 28); other cities move
  under 1.3 m MAE. This is a truth correction, not a model change.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) Progress, 2026-10-07.

### 2026-10-07 — Low-rises: survey LiDAR where free, tiers everywhere, survey-blind benchmark
- **Decision:**
  - A region run publishes the per-footprint survey nDSM height (p95 inside the footprint shrunk
    by 1 m) ahead of the OSM tag, drone and satellite, unless it is suspected stale.
  - Every published row carries a tier: `survey`, `verified_2`, `tag`, `single` or `prior`.
    `single` rows are published and labelled unverified.
  - Near-field street views are studied as a second image for low-rises.
  - The benchmark headline scores the survey-blind answer (`no_survey_height_m`). Survey rows are
    scored only against 3D Tiles.
- **Why:** user choice (2026-10-07: all three options). Satellite data can't verify low-rises (the
  stereo entry above). Free survey LiDAR covers the US, Spain, France, Czechia and Andalusia.
  Scoring survey heights against survey-based truth would be circular.
- **Rejected:**
  - 3D Tiles as a run-time source: ~81 % of the monthly free cap was used by 2026-10-07;
  - counting correlated pairs as verified.
- **Supersedes / superseded by:** extends the 2026-10-03 truth entry.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) addendum.

### 2026-10-07 — Multi-date satellite stereo verifies mid- and high-rises, not low-rises
- **Decision:** stereo (roof shift between dated WorldView scenes) is dropped below 40 m. It is
  weighted 0.6 at 40-100 m and 0.4 above 100 m. A confident multiview peak (conf >= 0.3) and
  stereo + lean agreement get up to 1.0 / ~0.7. Satellite readings take a drone-equivalent
  distance `dist_m = max(100, ~3300 * sigma_log)` instead of a fixed 100 m. The 3300 constant is
  provisional until drone sigma is measured against distance.
- **Why:** Chicago, 882 LiDAR buildings, 5 scenes, 9 pairs.
  - Stereo is within 25 % for 10 % of buildings under 15 m and 34 % at 15-40 m. It reaches 59 % at
    40-100 m.
  - Multiview at conf >= 0.3 is within 25 % 84 % of the time above 40 m (sigma_log 0.08-0.12).
  - Multiview plus lean agreement: 93-100 %, but on only 28 buildings.
  - A 10 m roof shifts 3-9 m between scenes, and low roofs have too little texture to match.
- **Consequence:** satellite data cannot provide the second image for low-rises. Verifying
  low-rises needs near-field imagery (drone or street level) or survey LiDAR.
- **Also found:** the sun-time search started at ~04:00 UTC the next day (the time zone was
  subtracted twice). That, more than missing tiles, broke the Miami sun solve; it is fixed in the
  scratch `w3_geom.py`. Wayback release 2016 WV02 serves the 2017 image (duplicate).
- **Supersedes / superseded by:** extends the 2026-10-07 satellite weights entry.
- **Source:** scratchpad `satellite_cities/validation.md`, backed up to
  `Code/claude/scratch_backup_2026-10-07/`.

### 2026-10-07 — Far and overhead drone spheres are dropped from height readings
- **Decision:** only drone spheres close to their towers with a waterline fit are used (Cartagena
  seeds 1/4/5/6, Miami seed_4). Far drones (0.7-6.6 km) and overhead fits other than seed_6 are
  not read.
- **Why:** their failure is the camera position, which no cue we have resolves.
  - A Photo Sphere's recorded position can be 100 m+ off (a pilot or ground position). On seed_7,
    near towers and a far landmark need headings 18 deg apart (parallax).
  - Heading, height and position then trade off against the ground score, the lowest-foot residual
    and the tower outline. All gave flat or ambiguous minima (seed_7, Chicago wickerSKY, Miami
    Martinez).
  - Benidorm izalko reads 2-3x too tall from footprint distance, not from pitch or scale: the error
    shrinks as 0.96 deg + 1949/d, and the footprint bases project into the sea.
  - seed_6 reads well only because its recorded position happens to be right.
- **Rejected:** a tower-outline heading cue for overhead seeds (ambiguous minima, 90 tower columns
  on seed_6); a camera-height scan (entry below).
- **Open:** a joint position fit from near and far landmarks would be needed. The outline step
  should also reject fits at its search edge or on fewer than about 100 columns (Benidorm's
  150 m move passed a "> 150" check).
- **Source:** scratch `r8/` (2026-10-07).

### 2026-10-07 — Overhead drone camera height from a lowest-foot scan
- **Hypothesis:** scanning camera height (30-800 m) for the best match between predicted and observed
  building feet fixes the overhead fits that sat at the 260 m grid top (Chicago wickerSKY, Miami
  Martinez) without the bases fixed point locking onto nearby edges.
- **Measured:**
  - Cartagena seed_6: 189 -> 198 m, tagged within 25 % 1.00 -> 0.75.
  - Cartagena seed_7: gained a wrong trusted reading (5x its tag).
  - Chicago and Miami do move off the grid top (254 m, 362 m), but residuals are 2-4 deg, and
    height trades against heading on seed_7.
  - Benidorm izalko reads 1.9x too tall even at the scan's 35 m, so its error is the horizon, not
    the height.
- **Verdict:** refused (reverted; patch in scratch `r7/height_scan.patch`). It needs a sharper
  heading cue first (the tower outline); far drones stay unreliable.

### 2026-10-07 — Placing no-GPS Commons photos with trusted heights or LightGlue
- **Hypothesis:** with LiDAR heights (instead of OSM tags) the skyline outline search places Commons
  photos; or DISK+LightGlue matches to the Miami drone panoramas plus PnP on tower blocks does.
- **Measured** on 53-57 Miami photos with known locations (location hidden):
  - outline search with LiDAR heights and EXIF focal: 15 % within 300 m (OSM tags 11 %), true
    place first 5 of 27, median rank 145;
  - a score lead of >= 0.1 over the runner-up placed 5 photos, 4 correctly. That is a precise gate
    for about 15 % of photos;
  - LightGlue: 1 of 55 within 300 m. The references are 2026 drone views 86-260 m up at only 4
    places; the matches land on 2-4 towers, so PnP is degenerate.
- **Verdict:** refused for the pipeline. At most, keep the EXIF + 0.1-lead gate as a high-precision
  placer. Learned matching would need references like the photos (Street View or posed Commons
  photos), not drone panoramas. Labels plus EXIF remain the reliable route (±5 m).

### 2026-10-06 — Placing no-GPS Commons photos by outline search, SIFT, building_groups voting or photo_columns
- **Hypothesis:** a Commons photo with no GPS can be placed by searching its outline, by SIFT matches, by `building_groups` voting, or by `city2stl/skyline/photo_columns.py::photo_columns`.
- **Measured:** the oracle (best possible choice) got only 4/7 headings and 1/17 rank.
- **Verdict:** refused.

### 2026-09-04 — Satellite shadow heights work in Cartagena
- **Hypothesis:** the shadow pipeline fails in Cartagena because of a wrong shadow bearing.
- **Measured:** bearing swept 0-345° in 15° steps over the 400 largest footprints: `near_level` p50
  0.037-0.060 against a 0.17 gate at every bearing, `length_m` p50 168-254 m, 0-10 of ~330 accepted.
  No bearing shows short runs with high near_level.
- **Verdict:** refused; the cause is physical. The ESRI scene was taken at 59° sun elevation
  (10.4° N, 2026-02-10, ~11:00), so shadows are ~0.6 × height in a dark, wet, vegetated scene; the
  other acquisitions are no better. Do not use shadow heights as a reference at low latitude
  without checking the acquisition's sun elevation first.
- **Source:** decisions.md 2026-09-04 entry.

### 2026-08-30 — Granada's export is flat because it lacks height data
- **Hypothesis:** Granada exports flat because OSM has little height data there.
- **Measured:** OSM carries `building:levels` on 4272 of 4476 buildings in the window (95.4 %,
  median 4); only 0.5 % carry `height`. Overture covers 55.6 % of pixels, GBA 31.8 %. The shipped
  3MF had 1026 distinct building heights (1.50-32.50 model units), every level count 1-8 at its own
  height, nothing floored to the 10 m default.
- **Cleared on the way:** the empty 3.5-7.1 m band in the vertex histogram belongs to the terrain
  object (base underside to lowest terrain), not the buildings; the 3.9 m/level in the old
  buildings module sat in dead code (deleted 2026-09-01), so 3.2 m/level is the only rule.
- **Verdict:** refused. Whatever is wrong with a Granada export, missing heights are not it.
- **Also measured:** GBA does not help Granada. It adds 8276 pixels Overture lacks against 70 689
  the other way, and reads 7.5 m lower on the median (corr +0.646), the same downward bias as in
  Miami. nDSM's 100 % coverage there is ground at 30 m. GBA stays a Cartagena/Tunis source.
- **Source:** claude/memory-bank/activeContext.md, 2026-08-30 (moved 2026-09-28).
