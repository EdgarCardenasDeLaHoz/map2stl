# Building heights

Where per-building heights come from (OSM tags, raster providers, Google 3D Tiles, shadows), how
providers are merged and ranked, and how height accuracy is measured. Related:
[survey-lidar.md](survey-lidar.md) (surveyed lidar references), [roofs-landmarks.md](roofs-landmarks.md)
(roof geometry). Research notebook behind the shadow entries: [../research/shadow-heights.md](../research/shadow-heights.md).

### 2026-10-09 — Cartagena runs footprint ownership with the cadastre occluders
- **Decision:**
  - Site flags `use_ownership` and `use_cadastre_occluders`, both true in `sites/cartagena.json`.
    `region_pdf` sets them through `ownership.set_site_flags`; the env vars `SKYLINE_OWNERSHIP` and
    `SKYLINE_CADASTRE_OCCLUDERS` still override. The module defaults stay off.
- **Why:** only with the cadastre occluders does Cartagena meet the low-rise gate of "A drone
  reading's top belongs to the footprint line of sight gives it" (used drone readings on non-PH
  1-3-floor plots, gate <= 10).
- **Measured** (replay `Cartagena_v14` against `Cartagena_v13_sat`):
  - the 7 towers unchanged; 10 of 10 tag-correct readings kept;
  - used drone readings on non-PH 1-3-floor plots 53 -> 2; b0490 now 11 m;
  - tiers verified_2 / tag / single / prior 38 / 136 / 127 / 715 -> 34 / 136 / 131 / 718 (v14).
- **Supersedes / superseded by:** the **Default** bullet of the ownership entry, for Cartagena.
- **Source:** commit `86dca7a`; gate script scratch `own2/gate14.py`; tier map
  `runs/region_reports/Cartagena_v14_skyline_report/tiers_map.png`.

### 2026-10-09 — The tower-behind veto counts a low lean with peak confidence >= 0.7 as satellite low
- **Decision:**
  - `_pano/elevated.py` (`_sat_max`, the veto): a low lean whose uncapped peak confidence
    (`extra.conf_peak`) is >= 0.7 counts as "satellite low". Veto only: fusion and the tiers keep
    the calibrated 0.3.
  - Cartagena's `readings.json` re-measured with all 7 scenes' outlines (sha be8da4d5 -> 73515f8a,
    2,922 footprints).
- **Why:** closes the open question of "Stored satellite readings are calibrated on load".
  Honolulu (untagged, 60 m window, `hon2` before / after): leans under 40 m with peak confidence
  >= 0.7: 580 / 350; of those on truth under 40 m: 533 (91.9 %) / 318 (90.9 %); truth >= 40 m:
  47 / 32; truth >= 100 m: 8 / 5 of the 126 towers.
- **Measured** (`Cartagena_v13_sat`, `SKYLINE_OWNERSHIP=0`, against `v12_support`):
  - tiers verified_2 / tag / single / prior 38 / 136 / 127 / 715 against 42 / 134 / 125 / 715;
  - the 7 towers unchanged; 10 of 10 tag-correct readings kept;
  - used drone readings on non-PH 1-3-floor plots 53 (gate <= 50 fails);
  - b0490 100 m -> 10.9 m prior (verified_2 -> prior); b1285 55 -> 10 m (verified_2 -> prior).
- **Supersedes / superseded by:** the **Open** bullet of "Stored satellite readings are calibrated
  on load".
- **Source:** commit `978eee1`; scratch `sat/`.

### 2026-10-09 — Google Open Buildings 2.5D does not replace the T41 prior for low-rises (refused on the pre-registered test)
- **Decision:**
  - `city2stl/skyline/lowrise_prior.py` stays off (`STATUS = "refused"`, site flag
    `use_google_2p5d_prior` unset everywhere).
  - The reader `city2stl/height/providers/google_ob25d.py` is kept for later tests. It is not in
    `_REGISTRY`.
- **Why:** the test was pre-registered (scratch `ob25/notes.md`).
  - Truth: survey p95 on untagged footprints of San Juan (2,221 rows), Mayagüez (2,285, from the 5
    densest 500 m tiles) and Charlotte Amalie (1,471, from 6 tiles); USGS PR/VI 2018.
  - Method: leave-one-city-out; p90 at presence >= 0.7 (chosen by every fold), plus a median
    offset per band of the raw value.
  - Under 15 m, the composite beats T41 in every held-out city and is nearly unbiased:

    | City | Composite MAE | T41 MAE | Composite bias | T41 bias |
    |---|---|---|---|---|
    | San Juan | 2.03 | 3.16 | +0.62 | +2.13 |
    | Mayagüez | 1.91 | 2.46 | +0.06 | +1.36 |
    | Charlotte Amalie | 1.89 | 2.01 | +0.05 | +0.78 |

    Within max(25 %, 2 m): 0.63 / 0.66 / 0.64 against 0.40 / 0.48 / 0.60.
  - It fails four of the five marks (P1, P3, P4, P5):
    - P1, the 1 m margin, in 2 of 3 cities (gain 0.55 in Mayagüez, 0.12 in Charlotte Amalie);
    - P3, the band-2 offset span <= 1 m: 1.89 m (San Juan fold fitted on 27 rows; band 1 span 0.19 m);
    - P4, the Cartagena gate: 38 of 286 prior rows within max(25 % of the cadastre, 2 m) of the
      non-PH cadastre, against >= 143 required (16 today; MAE 7.73 -> 5.05 m; `tiers.agree`: 16 ->
      29; with the 2 m floor 16 -> 36);
    - P5, the 7 OSM tags on non-PH plots within tol: 6 of 7 (b2663: tag 3.0 m, Google corrected
      5.15 m).
  - P2 passes: 15-20 m no worse than T41 (San Juan 6.16 vs 7.01, n 83; Mayagüez 6.62 vs 6.84, n 30;
    Charlotte Amalie n 11 not counted).
  - Offsets (m) B1 / B2: all three cities +0.15 / +0.74; San Juan held out +0.27 / +1.84; Mayagüez
    held out +0.09 / -0.05; Charlotte Amalie held out +0.08 / +0.78.
  - Cartagena, post hoc: on "1-floor" cadastre plots (n 185, median footprint 463 m2) Google p90
    reads 8.5 m against the cadastre's 4.0 m; on 2-floor plots 9.0 against 7.0. No Cartagena survey
    can say which source is wrong.
- **Rejected:**
  - Lowering the margin after seeing the numbers.
  - Fitting the correction on Cartagena's cadastre, which is not survey truth.
  - Using Google above 20 m: its heights are capped at 100 m.
- **Data:** 275,426,892 bytes in 248 logged range requests (5 headers of 64 KB, plus the city
  windows of the 2023 layer), saved in `~/.cache/ob25d/2023/`. The tiles are tiled COGs: 512 px
  blocks, DEFLATE with predictor 3, band-separate planes, 14 overviews.
- **Next evidence that could reopen it:** Medellín LiDAR (review item 14), a Colombian survey; a
  field check of 20-30 "1-floor" Cartagena plots over 300 m2.
- **Supersedes / superseded by:** —. Review 2026-10-09 item 4.
- **Source:** commit `a220104`; scratch `ob25/` (`notes.md`: the pre-registration and one deviation
  made before scoring, smaller truth areas; `results.md`; `fetch_log.md`).

### 2026-10-09 — A drone reading's top belongs to the footprint line of sight gives it; only tags and cadastre counts take a top away
- **Decision:** `_pano/ownership.py` decides from geometry which footprint owns each trusted
  drone reading's top; `_pano/elevated.py::seed_ownership` runs it after the cached measurement
  (stage-cache keys unchanged) and `elevated_estimates` applies it (`not_owned`,
  `_mark_not_owner`, `recredited`). Switch `ownership.OWNERSHIP` / env `SKYLINE_OWNERSHIP`
  (default: see the end of this entry).
  - Upper plausible height (`upper_heights`): as an occluder the OSM tag; else, with the cadastre
    occluders on (`CADASTRE_OCCLUDERS` / `SKYLINE_CADASTRE_OCCLUDERS`), the non-PH cadastre
    floors x 1.5 x 4.13 m; else the untagged prior's p90 (`prior_upper`: quantile GBM, the
    `untagged_prior` features and training set, scored city left out). As the owner of its own
    top, tag + 25 % (`TAG_MARGIN`). Never a measured top.
  - Claims, near to far (`Scene.owners`): every footprint claims its silhouette at that height
    (`claim_top`). Per core column the top pixel belongs to the first footprint whose claim holds
    it: nearer (4 px of the spin pano, scaled, inside its claimed top and its left/right edges:
    the isovist fails), own, farther (the ray clears the own claim) or none. The footprint keeps
    the top with a third of its columns (`SELF_MIN`, as `measure_footprints`).
  - Rule (`not_owned`): the walk counts, besides the footprint itself, only footprints with a
    hard bound (tag, cadastre count). `owner_nearer`, `owner_farther` and `owner_above_tag` (above
    its own tag + 25 % or cadastre bound, nothing tagged explains it) leave fusion. A prior p90
    claim never takes a top away.
  - Re-credit (`Scene.recredit`): a reading that left, whose top a farther *untagged* footprint's
    claim holds (every claim counted), joins as that footprint's reading when `trusted` accepts it
    with its base hidden (visible share above the nearer claims). Line of sight picks it, so the
    2026-10-07 refusal (owner chosen by agreeing evidence) does not apply; never to a tagged
    footprint (its tag publishes; agreeing re-credits would confirm it by construction).
- **Why:** calibrated depth caught 0 of the 4 known tower-behind cases (depth study 2026-10-09);
  CBHE and OpenFACADES decide ownership by geometry (review §2.6).
- **Measured** (pre-registered marks, review §3 item 3; Miami drone states, LiDAR `confirmed`,
  truth >= 20 m; wrong = more than 25 % off):

  | set | trusted with truth | wrong caught (>= 50 %) | right flagged (<= 5 %) | within 25 %: before -> kept (>= 0.80) |
  |---|---|---|---|---|
  | current pipeline, re-measured | 64 | 20 of 28 (71 %) | 1 of 36 (2.8 %) | 0.56 -> 0.81 (n 43) |
  | older reading set (F-SKY26:111, saved states) | 90 | 42 of 51 (82 %) | 1 of 39 (2.6 %) | 0.43 -> 0.81 (n 47) |

  - The 4 known cases are caught: Kaseya Center (read 138 m, LiDAR 43; the tagged 900 Biscayne
    Bay behind owns the top), The Loft 1 (177 / 82), The Guild (183 / 106), Brickell Key I
    (157 / 68) (above their tags, nothing tagged explains them). All four are tagged; untagged,
    with no tagged footprint behind, they would be kept.
  - The false flag: Venetia (read 83 m, LiDAR 99; prior p90 30 m), whose top the tagged b21280
    (115 m tag, 107 LiDAR) behind it claims.
  - Miami has almost every tower tagged: the rule with or without the p90 claims scores the same.
  - Miami fusion (`seed_experiment.py --region miami --remeasure --own`): 95 of 243 trusted
    readings left out (55 farther, 29 above tag, 11 nearer); fused OSM-tag error within 25 %
    0.76 (n 46) -> 0.85 (n 41); 9 re-credits to untagged footprints, none with LiDAR truth.
  - Cost: 6-13 s a seed on CPU; 25-44 s with the cadastre occluders.
- **Cartagena** (region replay from the stage cache; treatment and control on the same satellite
  readings, `readings.json` sha 79252747, rebuilt by the satellite agent at 10:33):

  | | v11 | control (ownership off) | ownership | ownership + cadastre occluders |
  |---|---|---|---|---|
  | verified_2 / tag / single / prior | 43 / 133 / 124 / 716 | 42 / 134 / 124 / 716 | 40 / 134 / 127 / 716 | 37 / 134 / 131 / 717 |
  | the 7 towers (Estelar 202, Allure 190, Gran Bay 170, Portomarine 188, Nautica 161, Ravello 160, Palmetto 156) | v11 values | unchanged | unchanged | unchanged |
  | tag-correct used readings kept (of 10) | 10 | 10 | 10 | 10 |
  | tag-wrong used readings (b0303 90/6, b0619 131/10, b0639 154/10, ...) | 3 | 4 | 0 | 0 |
  | used readings on non-PH 1-3-floor plots (gate: <= 10) | 50 | 54 | 52 | **2** |
  | trusted readings left out by ownership / re-credits kept | - | - | 45 / 3 | 175 / 19 |
  | verified_2 on non-PH 1-floor plots | b0788, b1761, b0806 | b0788, b1761 | b0788, b1761 | none |

  - Without the cadastre the gate's low-rise mark fails: the 1-3-floor plots are untagged, and
    their prior p90 does not take a top away.
  - Lost corroborations (ownership vs control): b0411 (70 m, drone + stereo) and b0339 (66 m),
    PH plots of 21 and 16 floors, whose tops a tagged tower behind claims: the Venetia case.
  - Re-credits, cadastre off: 3 untagged footprints gain a reading (b0369 81 m, b0271 89-91 m,
    b0458 99 m); none is corroborated; all three publish their prior (a single over 2x it).
  - With the cadastre occluders: 12 footprints behind take a re-credited reading (11 new to that
    seed), 2 corroborated: b0383 (19-floor non-PH plot) becomes verified_2 at 96 m from seeds 5
    and 6 (it had no row in v11); b0896 stays verified_2 (126 -> 130 m). Lost corroborations
    against the control: b0788 (55 m, 1 floor), b1761 (68/84 m, 1 floor), b0490 (91/110 m,
    2 floors) — the review's suspect low-rise verified_2 rows — and b0411, b0339, b1505 (PH,
    21/16/15 floors, 66-70 m: likely right).
  - Tier map: `runs/region_reports/Cartagena_v12_own_cad_skyline_report/tiers_map*.png` (and
    `Cartagena_v12_own_skyline_report/`): v11 and v12 look alike; a few green Bocagrande
    footprints turn single or prior, the labelled towers do not move.
- **Rejected** (measured; see Rejected hypotheses): the own-corner rule; the claims as the run's
  start (`occ[]`); untagged (prior p90) claims as ownership evidence.
- **Limits:** one truth city (the Chicago states have no `confirmed` truth on their 8 trusted
  readings; Honolulu / Fort Lauderdale spheres not captured), so review §4.5's second city is
  open. The rule was set while looking at the Miami readings (iterations in scratch `own/notes.md`);
  the older reading set and Cartagena are the checks. Untagged towers with a low p90 in front of a
  tagged one lose their reading (Venetia, b0411, b0339).
- **Default:** module off (`OWNERSHIP = False`, `CADASTRE_OCCLUDERS = False`): it passes the Miami
  marks, but review §4.5 asks for two truth cities, and Cartagena meets the low-rise gate only
  with the cadastre occluders. Env `SKYLINE_OWNERSHIP=1` (and `SKYLINE_CADASTRE_OCCLUDERS=1`) turn
  it on for a run. **Cartagena has both on through its site flags since `86dca7a`** (entry
  "Cartagena runs footprint ownership with the cadastre occluders", v14).
- **Source:** commit `b2fb7d6`; harness `Code/claude/scripts/seed_experiment.py --own [--own-cad]`;
  review `Code/claude/research/panorama-pipeline-review-2026-10-09.md` §3 item 3.

### 2026-10-09 — A tall satellite reading supports a single within 1.5x, not the strict `agree`
- **Decision:** `_core/tiers.py::SUPPORT_RATIO = 1.5`. `single_support` keeps a single over 2x the
  prior when a validated satellite reading (`validated_satellite`: lean >= 0.7, multiview >= 0.3,
  `ls`; >= 40 m) is within 1.5x of it (`|ln a/b| <= ln 1.5`). Corroboration (`verified_pair`,
  `tag_witness`, the tag check, `disputed_by`) keeps `agree` (ratio 1.25).
- **Why:** the agreement change (next entry but one) made `agree` stricter, and `single_support`
  used it: b0691 (drone 55.9 + lean 72.5, conf 1.0, ratio 1.30) lost support and published an
  18.9 m prior. The user chose a looser ratio for support only.
- **Evidence:** labelled stereo singles (no drone building has a validated satellite reading in
  `buildings.json`) supported by a validated reading: ratio 1.25 n 126, 84.9 % right; 1.33 n 135,
  84.4 %; 1.5 n 147, 83.7 % (the 21 added by 1.5: 16 right).
- **Not changed:** the validation bar. b0806's real lean is 48.4 m at conf 0.37, so it is not
  validated and still publishes the 15.6 m prior.
- **Measured:** Cartagena `v12_support` (GPU, `SKYLINE_OWNERSHIP=0`) against `v12_tiers`: one row
  changes, b0691 prior 18.9 -> single 55.9 m (lean 72.5); tiers verified_2 42 / tag 134 / single
  125 / prior 715; the 7 towers unchanged.
- **Supersedes / superseded by:** settles the "To decide" item of the agreement entry (b0691, b0806).
- **Source:** commits `45f5c41`, `8e26925`; scratch `support/`.

### 2026-10-09 — The benchmark scores by height band and tier, survey-only cities score, and roof percentiles are stored
- **Decision:**
  - Beside the unchanged headline (`10_benchmark` table, confirmed truth, survey-blind height),
    every scored report carries `bench` (`benchmark.py::bench_tables`), twice: `measured` (the
    rows the run measured, the headline's set) and `published` (every published row, tag-only
    rows too: a region report lists every OSM-tagged building as a `measured: False` row).
  - Bands: the review's <15 / 15-40 / 40-100 / >100 m (`REVIEW_BANDS`) and EUBUCCO's 0-5 / 5-10 /
    10-20 / 20+ (`EUBUCCO_BANDS`). Per band x tier and per band x method x camera-distance band
    (<500 / 500-1,000 / >1,000 m, drone and street seeds; `DISTANCE_BANDS`): n, MAE, median AE,
    signed bias, P90 AE, tol (within max(25 %, 2 m) of truth: `TOL_REL`, `TOL_ABS_M`), shares off
    by > 5 m and > 10 m (`band_errors`).
  - Pipeline metrics (`pipeline_metrics`): coverage (non-prior published rows, of rows and of all
    footprints), false-corroborated rate (`verified_2` / `corroborated` rows off by more than
    tol), withhold precision (withheld readings that were wrong) and recall (wrong readings that
    were withheld).
  - Single-source truth: a region whose footprints have no 3D Tiles reading (San Juan, Honolulu,
    Fort Lauderdale: monthly cap) scores its `survey_only` records (`truth_mode`,
    `scoring_truth`), printed in a table of its own; its headline row stays n = 0 as before.
  - Truth statistics: each survey record stores p50 / p70 / p90 / p95 / p99 / max roof heights and a
    ground p5 (`footprint_stats`, `ROOF_STATS`, `ROOF_STATS_VERSION` 2). p95 stays the headline;
    the stat version (`STAT_VERSION` 2) is unchanged, old caches stay readable. Readings are also
    scored against their method's statistic (`METHOD_STAT`): silhouettes (drone, street, lean,
    multiview, stereo, shadow) against p99, floor counts against p70.
  - Truth age: `temporal` = `may_postdate` (OSM `start_date` in or after the survey's last year),
    `predates` or `unknown` (`temporal_flag`); single-source scoring leaves `may_postdate` out.
  - Survey-only truth is keyed on the ring as a report writes it (6 decimals, `report_key`).
  - Nested footprints (an OSM `building:part` with half its area under a footprint whose height
    tag is higher, or under a larger one when untagged) are left out of the band tables
    (`nested_keys`, `NESTED_COVER` 0.5): the survey sees only the top of the stack.
- **Why:**
  - Review 2026-10-09 §3 item 1 and §4.4: one aggregate (Miami MAE 5.62 m) hides which band fails,
    and no drone change could be proven outside the Miami harness. The acceptance rule (§4.5)
    needs n, MAE and tol per band and the false-corroborated rate.
  - Tag-only rows are most of a report (San Juan 17,902 of 17,916 rows, Miami 27,612 of 27,987);
    a change to the tag rule or the prior moves them, so the tables must see them. The headline
    keeps its rows so it does not move.
  - The raw max is noise-bound on some surveys: Miami's 2019 topobathy has max > p95 + 20 m on
    24 % of 914 records (up to 294 m; Honolulu 1.7 %); p99 2.8 % (Honolulu 1.0 %). The max is
    stored as the review asked; silhouettes are matched on p99.
  - The 2026-10-09 scratch truth keyed full-precision OSM rings, which no report row has (San
    Juan 0 of 1,121 keys matched): that, not only the confirmed-only rule, made those cities n = 0.
  - Nested parts: Honolulu 241 of 1,138 published rows with truth; with them the tag tier's MAE at
    40-100 m was 23.1 m (bias -20.5), without them 15.1 m.
- **Measured (pre-registered in scratch `bench/notes.md` before the runs):**
  - Miami `--score-only --no-tiles`: headline n 168, MAE 5.62 m, every 2026-10-07 summary key
    identical. Passed.
  - Stored p95 = cached stat-2 `survey_m` on 914 / 914 Miami, 2582 / 2582 Honolulu, 1121 / 1121 San
    Juan, 442 / 442 Fort Lauderdale records (pass mark >= 99 %), for roof-stat versions 1 and 2.
    Passed.
  - Survey-only cities score in every band with truth (published rows): San Juan 10,726 scored,
    Fort Lauderdale 700, Honolulu 897. Passed. On measured rows only, the San Juan EUBUCCO 10-20 m
    band has one truth record, nested and left out, so n = 0 there (a fail of the pre-registered
    wording; the nested rule came after it).
  - Published rows, review bands, all tiers (n / MAE / bias / tol), nested parts left out:

    | city (truth) | <15 | 15-40 | 40-100 | >100 | all |
    |---|---|---|---|---|---|
    | Miami (two-source) | 155 / 3.3 / +2.3 / 48 % | 47 / 7.1 / -1.4 / 53 % | 26 / 6.2 / +3.4 / 88 % | 16 / 11.8 / -4.4 / 94 % | 244 / 4.9 / +1.2 / 57 % |
    | San Juan (survey only) | 9,839 / 1.7 / -1.2 / 74 % | 737 / 4.9 / -4.4 / 69 % | 150 / 11.0 / -8.7 / 79 % | no truth (max 85.9 m) | 10,726 / 2.0 / -1.5 / 74 % |
    | Fort Lauderdale (survey only) | 508 / 4.2 / +3.1 / 39 % | 98 / 5.2 / -1.0 / 72 % | 72 / 9.6 / -0.8 / 81 % | 22 / 29.6 / -23.5 / 73 % | 700 / 5.7 / +1.3 / 49 % |
    | Honolulu (survey only) | 361 / 3.5 / +2.1 / 42 % | 180 / 5.5 / -2.9 / 70 % | 279 / 15.3 / -12.1 / 76 % | 77 / 27.3 / -18.4 / 75 % | 897 / 9.6 / -5.1 / 61 % |

  - Measured rows (the headline's set), all tiers: Miami 136 scored (+32 nested = the headline's
    168), MAE 5.7 m, tol 54 %; San Juan 11, MAE 12.8 m; Fort Lauderdale 254, MAE 4.6 m, tol 32 %
    (242 prior rows: +4.5 m bias under 15 m); Honolulu 142, MAE 9.1 m, tol 58 %.
  - Pipeline metrics, published rows: coverage (non-prior rows / all footprints) Miami 85.4 %, San
    Juan 79.5 %, Fort Lauderdale 6.0 %, Honolulu 35.9 %; withhold precision 94.7 % / 100 % / 93.0 %
    / 98.3 %, recall 100 % everywhere (every wrong Street View reading was withheld; no image
    reading is published in these reports); false-corroborated: n = 0 everywhere.
  - Final run `runs/benchmark/2026-10-09_1333` (Miami, San Juan, Fort Lauderdale, Honolulu,
    `--score-only --no-tiles`); tables in scratch `bench/final_tables.txt`.
- **Rejected:**
  - Bumping `STAT_VERSION`: the p95 does not change; `roof_stats` versions the new fields.
  - Ground p5 as an absolute elevation (3DBAG): the providers return only the nDSM (the DTM is
    dropped in `lidar_3dep_copc.py::grid_points_ndsm`), so ground p5 is the p5 of the nDSM in a 4 m
    ring outside the footprint (~0 where the survey's ground model fits; median 0.02-0.03 m in all
    four cities; > 2 m on 13 % of Miami, 7 % Honolulu, 2 % San Juan, 5 % Fort Lauderdale records).
  - Scoring tag-only rows in the headline: it would move Miami's headline (n 168 -> 292).
- **Supersedes / superseded by:** refines the survey-only handling of "Low-rises: survey LiDAR
  where free, tiers everywhere, survey-blind benchmark" (Rejected hypotheses).
- **Source:** review `claude/research/panorama-pipeline-review-2026-10-09.md` §3 item 1, §4.4;
  commits `0a3fa6d`, `7f4cb9a`, `36ae357`, `5d94ade`, `d25201d`, `9c76611`, `348a513`, `57ca936`,
  `7334a93`, `de058ff`; scratch `bench/`.

### 2026-10-09 — Readings agree within 25 % of the smaller, per band; a measurement that disagrees with a published tag is shown beside it
- **Decision:**
  - `_core/tiers.py::agree`: two readings agree when the larger is at most `1 + tol` times the
    smaller (`|ln a/b| <= ln(1 + tol)`), `tol` from `AGREE_TOL` by the smaller value's band
    (`AGREE_BANDS`: <15 / 15-40 / 40-100 / >100 m). Fitted table: 25 % in every band. It was 25 %
    of the larger (a ratio up to 1.33; now 1.25). `tag_witness`, `verified_pair` (closest pair
    by ratio), the tag check, `disputed_by`, `single_support` and `cadastre_heights` use it
    (`single_support` later moved to its own 1.5x ratio: previous entry).
  - The 2 m floor below 15 m is implemented (`agree(..., floor_m=)`, `AGREE_FLOOR_M`,
    `REVIEW_FLOOR_M = 2.0`) but **off** (`AGREE_FLOOR_M = 0`).
  - `tiers.py::tag_measurement`: a tagged row whose independent agreeing pair (`verified_pair`)
    has a mean more than 10 % (`TAG_DISAGREE_REL`, on the smaller) from the published tag keeps the
    tag and carries `measured_m`, `measured_methods`, `tag_disagrees`; shown by
    `tier_display.py::tag_note` (in `tier_hover`), listed by `tag_disagreements` and in
    `heights.json` `tag_disagreements`. The user decides any switch.
  - `tiers.py::selection_reason`: every published row says why its value was chosen
    (`selection_reason`, `SELECTION_REASONS`), as 3DBAG's selection reason; shown in `tier_hover`.
- **Why:**
  - Labelled pairs (scratch `tiers/pairs.py`, 1,946 buildings with confirmed LiDAR / 3D Tiles truth;
    528 pairs of a kind the independence rules can corroborate: tag + witness, drone + drone,
    lean + shadow over 100 m): the 2026-10-07 satellite validation (7 cities, readings rebuilt
    with `readings.footprint_readings`) and the Miami drone harness (6 review states, trusted
    readings, median per camera). "Right" = within max(25 %, 2 m) of truth. Floor off:

    | band (smaller) | n pairs | old rule: agree / P(both right) | 25 % of smaller: agree / P | held out by city |
    |---|---|---|---|---|
    | < 15 m | 59 | 1 / 1.00 | 1 / 1.00 | 0 agreeing |
    | 15-40 m | 53 | 7 / 0.57 | 7 / 0.57 (no tol 5-25 % reaches 0.85) | 3 / 0.33 |
    | 40-100 m | 138 | 65 / 0.82 | 54 / 0.89 | 40 / 0.93 |
    | > 100 m | 278 | 234 / 0.97 | 216 / 0.99 | 215 / 0.99 |

  - False-corroborated rate (corroborated buildings whose published value is off truth by more
    than max(25 %, 2 m)): 6 of 218 (2.8 %) before, 5 of 207 (2.4 %) after. 40-100 m 3/38 -> 2/34;
    > 100 m 1/173 -> 1/166 (the same building; bootstrap 90 % interval of the change +0.00 to
    +0.08 points); 15-40 m 2/6 both. The 11 buildings that lose corroboration are a tag + one
    reading 1.25-1.33x apart (10 of them with a right tag, so coverage falls more than errors).
  - Measured vs tag: Palmetto's lean 136.4 m and shadow 135.3 m agree; the tag says 156 m.
    The review asked to publish what was measured; the user keeps the tag until they decide.
- **Cartagena replay** (`Cartagena_v12_tiers`, GPU, `SKYLINE_OWNERSHIP=0`, about 10 min, 0 Street
  View cache misses; HEAD 57ca936 with other agents' uncommitted edits in the tree):
  - The 7 towers are unchanged (same footprint, same value, all `verified_2`: Estelar 202, Allure
    190, Gran Bay 170, Portomarine 188, Nautica 161, Ravello 160, Palmetto 156).
  - Tiers verified_2 43 -> 42, tag 133 -> 134, single 124 -> 124, prior 716 -> 716; 1,016 rows in
    both runs.
  - Caused by the rule (4 rows; the same 4 come out of v11's own readings):
    - b0348, tag 51 vs lean 39.7 (1.28x): verified_2 -> tag; the value stays 51;
    - b0605, 117 m2, drone seed_5 135 + seed_4 101 (1.33x), satellite all low (stereo 11.8):
      verified_2 -> single -> withheld (satellite-low veto): 118 -> 10.1 m (prior);
    - b0691, drone seed_6 55.9 + lean 72.5 at conf 1.0 (1.30x): verified_2 -> single, the lean no
      longer supports it under the 2x rule -> withheld: 55.9 -> 18.9 m (prior) (later restored
      by `SUPPORT_RATIO`);
    - b0806, drone seed_4 62.8 + lean 48.4 at conf 0.37 (1.30x): verified_2 -> single -> withheld:
      62.8 -> 15.6 m (still, the lean is not validated).
  - Caused by `d2f2d5e` (satellite readings calibrated on load; not this rule): a low lean now has
    conf 0.3, so the tower-behind test no longer drops a drone reading: b0490 prior -> verified_2
    100.3 (drone 90.8 + 109.8), b0561 prior -> verified_2 111.3 (drone 104.6 + 118.0), b1505 prior
    -> verified_2 67.2 (drone 67.2 + lean 72.5), b0259 prior -> single 66.6 (supported by
    high_rise_floors); b0486 single 3.7 (satellite) -> prior 9.6 (a new drone reading of 115 m,
    over 2x the prior and unsupported); stereo under a confident tall lean dropped: b0777 single
    43.8 -> 41.1, b1591 single 47.3 -> 51.8.
  - Prior drift: 60 rows prior -> prior, mostly within 1-15 % (the T41 prior's neighbour set
    changed with those rows). Not this rule.
  - `tag_disagrees`, whole run: 1 row, Palmetto Eliptic b0598: measured 135.8 m (lean 136.4 +
    shadow 135.3) against the tag 156 m (-12.9 %); `verified_2`, tag published. Ravello is not
    flagged: drone 145.5 + stereo 161, mean 153 against the tag 160 (4 %).
  - `selection_reason` on all 1,016 rows: 231 prior with no usable reading, 221 prior after a
    withheld reading, 98 satellite-only, 46 osm_tag, 45 drone median, 8 high-rise; the rest are
    osm_levels / street.
  - 2 m floor what-if (not on): 1 row changes, b2663 (tag 3 m + multiview 1.5 m, a ground match):
    tag -> verified_2.
  - Review §4.5 sanity: the 7 towers move 0 m; no tagged row changes value; used readings removed
    by this rule are 3 drone medians (b0605, b0691, b0806), all untagged (b0605 is a tower-behind
    pattern; b0691 and b0806 were doubtful, see the support entry). The tier map
    `tiers_map.png` / `tiers_map_bocagrande.png` (v11 left, v12 right) was rendered and looked at:
    alike at map scale.
- **Rejected / not accepted:**
  - The 2 m floor (the review's max(25 %, 2 m)): on the labelled pairs its only new
    corroboration is wrong (Seattle: tag 3 m + lean 4.2 m, truth 12.5 m), the overall rate rises
    2.75 % -> 2.88 % with it on, and the < 15 m band has 2 corroborated buildings, short of the
    30 the acceptance rule (review §4.5) needs. `AGREE_FLOOR_M = REVIEW_FLOOR_M` turns it on once
    low-rise labels exist (Honolulu, San Juan, Fort Lauderdale drone runs).
  - Bands looser than 25 %: 40-100 m stays >= 0.85 only up to 30 %, and the review set 25 % as
    the rule; looser values were scored for information only.
  - The claim "corroborated >= 0.85 within tol" (F-SKY26 criterion) holds only from 40 m up;
    under 40 m too few readings agree to tell (8 agreeing pairs in 112).
- **Open:** the other "25 %" call sites still use their own formulas (fusion `fuse_heights`,
  satellite `readings._agree`, elevated `CREDIT_TOL` and `FLOORS_SAME_SEED_AGREE`); each is
  tighter on one side under `tiers.agree` (ratio 1.33 -> 1.25) and needs its owner's check with
  `seed_experiment.py` (fusion, elevated) and `test_satellite_heights.py` plus the 7-city tables
  (readings). The PDF / HTML-table display lines for `tag_disagrees` are also not wired.
- **Supersedes / superseded by:** the "within 25 %" of the 2026-10-07 tier entry (25 % of the
  larger). The `verified_2` rename is a later entry.
- **Source:** commit `b04b22f`; scratch `tiers/` (scripts and logs); Cartagena replay
  `runs/region_reports/Cartagena_v12_tiers_skyline_report/`.

### 2026-10-09 — An untagged footprint's search window does not follow its drone or floors reading (refused)
- **Tried:** `measure.search_cap`: untagged, the shadow / lean / sweep windows (60 / 60 / 80 m)
  grow to 1.6x the highest drone or floors reading, and to at least 220 m when a seed reads over
  80 m (review 2026-10-09 item 2). `20_satellite_heights.py --hints <heights.json>` re-measures
  only the footprints whose window changes (Cartagena: 256 hinted, 241 windows changed, 2,554 s).
- **Pre-registered** (scratch `sat/notes.md`, before any run): Honolulu survey p95 (stat 2), tags
  stripped for measuring (scene geometry fitted with the tags), the "after" run with a 100 m hint
  on every footprint (a 220 m window: the regime the lift applies to; for low plots the worst
  case). Tol = max(25 %, 2 m) of the survey.
  - H1: lean or multiview within tol on >= 80 % of the 126 footprints >= 100 m.
  - H2: leans >= 40 m on <= 2 % of the 1,982 footprints under 30 m.
- **Result (Honolulu; reference scenes 2025-02-01 WV02 and 2025-05-11 WV03, 4 older scenes; all
  126 and 1,974 of 1,982 measured):**

  | | tagged (reference) | untagged, 60 / 80 m | untagged, 220 m |
  |---|---|---|---|
  | H1 lean or multiview within tol, of 126 | 37 (29.4 %) | 2 (1.6 %) | **47 (37.3 %)** |
  | lean within tol / with a lean | 32 / 55 | 0 / 59 | 36 / 79 |
  | lean within tol at conf >= 0.7 | 22 | 0 | 19 |
  | lean > 1.25x survey | 6 | 0 | 21 |
  | own-outline towers (tag within 25 %), of 78 | 35 | 2 | 39 |
  | H2 lean >= 40 m on < 30 m, of 1,982 | - | 219 (11.1 %) | **706 (35.6 %)** |
  | ... at conf >= 0.5 / >= 0.7 | - | 113 / 64 (3.2 %) | 412 / 209 (10.5 %) |
  | multiview / stereo >= 40 m on < 30 m | - | 16 / 177 | 62 / 314 |

  - H1 fails (37.3 % against 80 %), H2 fails (35.6 % against 2 %; the 60 m window already
    fails it at 11.1 %). Even tagged, lean and multiview reach 29.4 %: Waikiki's lean is missing on
    71 of 126 towers, and 48 of the 126 have no OSM tag within 25 % of the survey, mostly podium
    slices or building:parts inside a tower outline that read the tower's p95.
  - Why (single-scene diagnostic, 64 towers in the WV02 outline, scratch `sat/hon_reasons.py`):
    tagged, 24 leans; missing because "roof outline hidden by neighbours" 14 (the tagged
    neighbours' occlusion labels), conf < 0.15 13, "facade bottom hidden" 11. Untagged with 220 m:
    44 leans (hidden 1) but only 18 within tol: without labels the neighbours' roof edges are read
    as the footprint's own. The same mechanism gives H2.
- **Chicago** (second city, pre-registered H3; lean only, 2025-04-24 WV03 core, repo
  `measure_single`, truth = LiDAR and 3D Tiles agreeing (cached), scratch `sat/chi_lean.py`):

  | | tagged (reference) | untagged, 60 m | untagged, 220 m |
  |---|---|---|---|
  | lean within tol, 100-200 m (of 244) | 140 (57.4 %) | 0 | **124 (50.8 %)** |
  | ... at conf >= 0.7 | 87 | 0 | 66 |
  | lean >= 40 m on < 30 m (of 223) | 45 (20.2 %) | 45 (20.2 %) | **112 (50.2 %)** |
  | ... at conf >= 0.7 | 10 | 12 | 28 |

  H3a fails (50.8 % against 80 %), H3b fails (50.2 % against 2 %; 20.2 % even tagged).
- **Cartagena replay** (`Cartagena_v12_sat`, hinted + recalibrated readings): the 7 towers and the
  10 tag-correct readings unchanged, but 6 rows published above 205 m (the city's tallest is
  Estelar, 202 m): b0655 256 single (lean 255.6 at conf 1.0 + floors flag), b0549 233 verified_2,
  b0586 231 single, b0646 223 verified_2 (lean 241.8), b0282 219 single, b1152 206 verified_2
  (stereo 249.5); b0060 203 verified_2 rests on a lean of 195 m at conf 0.21. 26 hinted rows
  gained a lean >= 40 m at conf >= 0.7. The window a drone reading opens lets the lean find a
  peak that then corroborates that reading (tiers ignore confidence).
- **Decision:** refused for publishing (two truth cities fail both marks; review 4.5). `search_cap`
  and `--hints` stay as a measuring tool (hints only widen windows when passed); Cartagena's
  `readings.json` stays unhinted (the hinted file is scratch `sat/cart_readings_hinted.json`).
- **Would need, before trying again:** occlusion labels for untagged neighbours (their drone,
  floors or prior height instead of 10 m), and a hinted reading counting only when it does not
  depend on the reading it supports (or at conf >= 0.7 and under the site's
  `max_plausible_height_m`). Pre-register H2 at conf >= 0.7 for that.
- **Supersedes / superseded by:** none.
- **Source:** commits `d2f2d5e`, `78f06b9`; scratch `sat/`.

### 2026-10-09 — Stored satellite readings are calibrated on load; Cartagena's file recalibrated
- **Decision:**
  - `readings.load(calibrate=True)` applies `readings.calibrate` to every footprint and reports
    `meta["n_recalibrated"]`; `satellite_fusion.load_region` logs a warning when it is not 0.
    `--recalibrate` reads the file raw. `--add` / `--hints` no longer restore old readings for a
    re-measured footprint that now has none.
  - `20_satellite_heights.py --recalibrate` run on Cartagena's region file: 218 of 2,911
    footprints changed (178 leans under 40 m capped at 0.3; 40 stereo and 14 multiview readings
    dropped under a confident tall lean).
- **Why:** the file mixed calibrated and uncalibrated footprints (an earlier `--add` kept old
  readings as stored): 178 leans under 40 m kept conf > 0.3, and on 32 rows the "satellite low"
  verdict (`_core/height.py::_satellite_low`, `elevated.tower_behind`) rested only on such a lean
  (review 2026-10-09 item 2, 2026-10-08 decision).
- **Replay** (`Cartagena_v12_recal` against the control `Cartagena_v12_ctrl`: same tree, the
  pre-change file loaded raw; tree hash in scratch `sat/cart_v12_*.log`):
  - 7 towers unchanged; 10 of 10 tag-correct used drone readings kept;
  - satellite-low rows 537 -> 505: exactly the 32, none newly low; tower-behind 94 readings on
    84 footprints -> 86 on 76;
  - tiers verified_2 39 -> 42, prior 719 -> 716 (single 124, tag 134); new verified_2: b0561
    111 m (two drones, no cadastre match), b0490 100 m (two drones; non-PH cadastre 2 floors,
    7 m), b1505 67 m (drone + lean; PH cadastre 65 m);
  - **used drone readings on non-PH 1-3-floor plots 50 -> 54** (b0246 133 m 2 fl, b0484 106 m
    1 fl, b0486 115 m 1 fl, b0490 91 m 2 fl): review 4.5 "do not rise" fails by 4.
  - Against v11 (other agents' changes included): verified_2 43 -> 42, tag 133 -> 134, single
    124, prior 716.
- **Open:** on Honolulu (untagged, 60 m window) a lean under 40 m with peak confidence >= 0.5 is on
  a building under 40 m 90 % of the time (719 of 798; base rate 79.7 %), on one >= 100 m 2.3 %
  (base 4.9 %). Chicago's 52 % (2026-10-08) came from a downtown. As a tower-behind veto the
  uncalibrated low lean carried some signal in a beach city; whether the veto should read
  `extra.conf_peak` was left to the owner of `elevated.py` (it would bring back b0490):
  **done 2026-10-09, see "The tower-behind veto counts a low lean with peak confidence >= 0.7 as
  satellite low".**
- **Supersedes / superseded by:** applies the 2026-10-08 calibration entry to stored files.
- **Source:** commit `d2f2d5e`; scratch `sat/`.

### 2026-10-09 — A scene's outline is every polygon of it in the region
- **Decision:** `scene.scene_outlines(cfg, release, bbox)` queries the release's source layer
  with the region bbox and merges each scene's polygons; `scene_polygon(..., bbox=)` uses it,
  identify at points only as a fallback; `20_satellite_heights.py --outlines` re-queries the
  cached `polygons.json`.
- **Why:** a scene is often several polygons in one release, and identify at one point returned
  one of them. Honolulu: 2025-05-11 WV03 218 -> 515 of 3,114 footprints, 2025-02-01 WV02
  1,559 -> 2,571, 2022-10-03 WV02 1,856 -> 3,086, 2024-01-14 WV03 1,819 -> 3,113. Cartagena:
  2023-02-19 WV02 1,274 -> 3,018 (the other six unchanged). Cartagena has 3,017 footprints after
  the water filter.
- **Done later:** Cartagena's stored readings were re-measured with all 7 scenes' outlines
  (`readings.json` sha be8da4d5 -> 73515f8a, 2,922 footprints). Earlier
  they used the old 2023-02-19 outline.
- **Source:** commit `f52145d`.

### 2026-10-09 — Seed pages show a reading's reason when its footprint has no measured row
- `html_report.py::_reading_status`: the segment's `untrusted_reason` (or tower behind / top
  edge) is shown when the footprint's row is missing from the report rows; "no published row"
  only when there is no reason. Cartagena v11: 308 trusted drone readings, and 39 readings showed
  only "no published row" on the seed pages; 0 in v12_ctrl / v12_recal.
- The row itself is still missing from `published_by_id`: the report gets `building_heights`
  without the unmeasured-tag rows, so "published m" and the tier stay empty for them. The fix is in
  `region_pdf.py` (write `heights.json` first and pass its rows to `write_region_report`); not
  applied.
- **Source:** commit `d2f2d5e`.

### 2026-10-09 — A single reading over 2x the prior publishes the prior unless supported
- **Decision:** an untagged `single` row (one drone median or a satellite-only height, no
  independent agreeing pair) more than 2x its prior publishes the prior (`_core/tiers.py::single_withheld`,
  `SINGLE_WITHHOLD_FACTOR`; flag `SKYLINE_WITHHOLD_SINGLE`, default on): tier `prior`,
  `prior_disagrees`, the reading kept as `single_reading_m` / `single_source` / `single_methods`,
  `withheld_reason` "single over 2x prior". It stays `single`, with `single_support`, when
  supported (`tiers.py::single_support`):
  - a validated satellite reading of 40 m or more agrees within 25 %, or is the single itself:
    lean at conf >= 0.7, lean + shadow (`ls`), multiview at conf >= 0.3 (`SUPPORT_MIN_CONF`);
    support `["lean", ...]`;
  - floor counts flagged the plot a high-rise (`floor_bands.high_rise_plots`, satellite veto
    inside): `["high_rise_floors"]`. This also keeps the `withheld:high_rise` rows (an untagged
    plot with no drone reading and no publishable satellite height publishes
    `floor_bands.high_rise_height`), exempt since 2026-10-08.
  Never when the plot's confident satellite readings are all under 40 m (`_core/height.py::_satellite_low`,
  the tower-behind test's rule). Rows floors counted carry `high_rise_seen`, `floors`, `storey_m`.
- **Why:** the user's choices: the rule on 2026-10-07/08, the high-rise exemption on 2026-10-08,
  the refinement on 2026-10-09.
  - Rule: on v9 the tower-behind check showed a lone drone reading far over the prior is often a
    farther tower's top; v9 had 164 drone singles over 2x the prior at a median of ~99 m.
  - Refinement: v10 withheld 302 singles (median reading 69 m; 152 drone, 150 satellite) and
    Bocagrande, a tower district, published mostly at the ~12 m prior. Many had a validated
    satellite reading behind them (b1429 lean 60.4 at conf 1.0 + stereo 64.2; b0112 lean 67.4 at
    0.9 + stereo 65.5), and the high-rise flag reached only plots with no drone reading.
  - Thresholds from Chicago LiDAR (entries 2026-10-07 below): lean over 100 m within 25 % 94 %,
    multiview over 40 m 84 %; stereo at 40-100 m only ~59 %, so stereo alone never supports
    (b1211: lean 53.5 at conf 0.64 + stereo 54 stays withheld).
- **Measured** (Cartagena region report, cache-only; v9 2026-10-07, v10 2026-10-08, v11
  2026-10-09 with this rule and the floors fix below):

  | | v9 | v10 | v11 |
  |---|---|---|---|
  | rows | 646 | 1,016 | 1,016 |
  | verified_2 / tag / single / prior | 32 / 143 / 223 / 248 | 40 / 133 / 42 / 801 | 43 / 133 / 124 / 716 |
  | singles over 2x withheld (drone / satellite), median reading | 0 | 302 (152 / 150), 69 m | 218 (142 / 76), 77 m |
  | kept with `single_support` (satellite / high-rise flag only) | - | - | 90 (51 / 39) |
  | `withheld:high_rise` rows | 0 | 8 | 8 |
  | Bocagrande rows >= 40 m (of 588 in v10/v11) | 215 of 366 | 60 | 127 |

  - Support kinds: lean 34, high-rise flag only 39 (11 of them drone singles), lean + flag 6,
    lean + ls 5, ls 3, multiview 2, lean + ls + flag 1.
  - The 218 still withheld: satellite says low 90; no confident satellite reading 45 (drone);
    only stereo agrees 47; a lean at conf 0.5-0.69 agrees 19; satellite >= 40 m disagrees 15; no
    satellite reading 2.
  - The 7 published towers keep their values and tiers (Estelar, Gran Bay, Portomarine, Nautica,
    Ravello verified_2 by drone / satellite pairs; Allure and Palmetto verified_2 by tag + lean
    and lean + shadow).
- **Rejected:** counting plain stereo as support (~59 % at 40-100 m: it would keep 47 more);
  publishing every single (v9: 164 drone singles over 2x at median ~99 m, the tower-behind
  readings among them).
- **Supersedes / superseded by:** the 2026-10-08 rule (no support) is this entry without the
  refinement; the high-rise exemption is the floors branch.
- **Source:** commits `4a73d5a`, `53bb58b`, `050ccbc`; [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md)
  Progress 2026-10-08 / 2026-10-09; maps `runs/region_reports/Cartagena_v11_skyline_report/tiers_map_bocagrande.png`.

### 2026-10-08 — An OSM height tag one agreeing image reading confirms is verified_2
- **Decision:** `_core/tiers.py::tag_witness`: an `osm_tag` height (never `osm_levels`) that one
  trusted drone reading, satellite lean or multiview, or a stereo reading of 40 m or more agrees
  with within 25 % is `verified_2`, methods `["osm_tag", <reading>]`. Shadow (a lower bound),
  floors and Street View never witness; an independent pair that disputes the tag blocks it.
  Unmeasured tagged rows in `heights.json` carry their satellite readings so a lean can witness.
- **Why:** the user's rule (2026-10-08). A tag is an independent source, so tag + one image reading
  is two sources. v10 and v11: 9 tag rows verified_2 this way (lean 6, multiview 2, stereo 1),
  Allure (190 m, osm_tag + lean 168 m) among them; Palmetto's unmeasured row got its satellite
  readings back and verifies by lean + shadow.
- **Rejected:** levels-derived tags (one storey guess per building, not a measurement).
- **Supersedes / superseded by:** —
- **Source:** commit `53bb58b`.

### 2026-10-08 — Footprints a drone seed measured without a usable reading get a prior row
- **Decision:** `region_pdf.py::_drone_seen_rows` adds a row for every untagged footprint an
  elevated seed measured that has none (its reading untrusted, left out by the tower-behind check
  or disputed in fusion), `drone_seen` naming the seeds; `withhold_untagged_street_view` gives it
  the prior (or a publishable satellite / high-rise height). Their prior comes from a second prior
  over both record sets (`_chain_fallbacks`): `untagged_prior.fallback_for` predicts only the
  records it was built on.
- **Why:** v9 dropped 41 singles and 3 verified rows of v8 whose only drone reading was left out:
  no estimate, no row, so the map showed holes where a seed had looked. v10 run 1 added 368 rows
  but, without the wider prior, dropped all but the satellite / high-rise ones again; v10 run 2:
  376 rows added, 646 -> 1,016 rows.
- **Supersedes / superseded by:** —
- **Source:** commits `4a73d5a`, `c92f224`.

### 2026-10-09 — A floor count its own seed's reading contradicts leaves the fusion
- **Decision:** `_pano/elevated.py::drop_contradicted_floors` (called in `floors_info`): a floors
  reading of a plot leaves the fusion when the same seed's base-visible reading of that plot,
  trusted or not, differs from it by more than 25 % (`FLOORS_SAME_SEED_AGREE`). The high-rise flag
  and the storey calibration are unchanged.
- **Why:** Cartagena v10 lost the drone heights of b0582 (Hotel Cartagena Plaza), b0426, b0635
  and b0075 (v9: verified_2, verified_2, verified_2, single). Replaying v10's fusion from the run's
  own stage-cache entries (measured and floors of seeds 1/4/5/6) found the cause: floors readings
  in fusion (commit `1c9a182`). One count, entered as a fully seen drone reading at 440-510 m
  (sigma_log 0.13-0.16), outweighed the trusted drone reading, anchored the fusion and outvoted it:
  - b0582: seed_1 66 m (base in view; satellite shadow 66, stereo 61) vs seed_1's own count, 35
    floors = 148 m;
  - b0426: seed_4 72 m, stereo 74.5 vs seed_5's count 98.5 m (seed_5's own reading 72 m);
  - b0635: seed_4 44 m, lean 53.5 at conf 1.0, stereo 44 vs seed_6's count 73 m (its own roof
    fit 49 m);
  - b0075: seed_4 67 m vs seed_5's count 35 m (its own reading 118 m).
  Every one contradicts its own seed's reading of the plot. One image cannot overrule itself: the
  count is an instance matched to the plot by range, and it usually takes its "base in view" from
  that very reading, so a count far from it is another building's facade.
- **Measured** (the v10 replay): 20 of 145 floors readings drop. Footprints with a drone height:
  without floors 256; with floors as in v10 256 (7 lost, 7 gained); with the filter 261 (2 lost,
  both to another seed's count: b0170, b0403; 7 gained).
  The four plots get their reading back; the b1158 catch (drone 183 m) and every tagged plot are
  unchanged. Stage-cache keys are untouched (the filter is outside the hashed functions).
- **Rejected:**
  - the saved review states as evidence (`seed_experiment.py --remeasure`): older captures (137-186
    footprints a seed vs the run's 176-276) with an unreliable storey (n 1), so no floors readings
    and every reading kept;
  - re-weighting floors readings (a plot-match term in sigma_log): needs truth on wrong-plot counts
    that we do not have;
  - dropping only counts against the same seed's *trusted* reading: restores b0582 only.
- **Supersedes / superseded by:** refines "Floor counts flag high-rises and join fusion" below.
- **Source:** [F-SKY26](../plans/active/F-SKY26-skyline-signals-to-publish.md) Progress 2026-10-09.

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
- **Supersedes / superseded by:** publishing wired, and the flag supports a single over 2x the
  prior (entry "A single reading over 2x the prior publishes the prior unless supported",
  2026-10-09); counts their own seed contradicts leave the fusion (entry "A floor count its own
  seed's reading contradicts leaves the fusion", 2026-10-09).
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
- **Note (re-measured 2026-10-09):** with today's rule (levels x 3.2 m plus a roof level) and
  survey-only truth from `USGS_LPC_PR_PRVI_E_2018` (all 25 Old San Juan tiles read; truth keyed on
  the report ring), the 822 tagged Old San Juan footprints sit 2.04 m under the survey p95 at the
  median (MAE 3.25 m, tol 0.65), down from 3.48 m / 4.99 m under 3.0 m per level:
  - `osm_levels` (527): -2.91 m median vs p95 (MAE 3.84 m, tol 0.56); against the p70 (the
    floor-matched statistic) -1.11 m (MAE 2.52 m, tol 0.72);
  - `osm_tag` height tags (295): -0.75 m vs p95 (MAE 2.21 m, tol 0.81).
  - Condado / Miramar for comparison (877 tagged): -1.17 m vs p95 (MAE 2.91 m), -0.18 m vs p70.
  - Still a floor: the sign holds, but against the floor-matched p70 the gap is ~1 m for levels
    and ~0 for height tags. (F-ARCH made the rule 3.2 m per level plus one roof level:
    `city2stl/heights.py::height_from_tags`, [F-ARCH](../plans/active/F-ARCH-consolidation.md).)
    Script scratch `bench/osj_tag_gap.py`.
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

### 2026-10-09 — The own-corner rule: a trusted top shows a vertical edge at its footprint's projected corners
- **Hypothesis:** a right top shows a sky gap, a non-building label, a MobileSAM instance boundary
  or a depth step at the footprint's projected left or right corner column, at the row its roof
  has there (CBHE).
- **Measured** (Miami states, LiDAR; `ownership.Scene.corner_edges`: column +-3 px, row +-3 px,
  6 rows below, 60 % of rows differing, depth step 1.15):
  - current readings: no edge at 10 of 36 right readings (28 %); an edge at 18 of 28 wrong ones;
  - older reading set: 8 of 39 right flagged, 16 of 51 wrong caught;
  - all 4 known tower-behind cases show an edge (b6428's left corner shares Four Seasons' edge);
  - why: at 1-3 km OSM footprints include low wings (Villa Regina's corner 35 px right of its
    tower), MobileSAM merges a block into one instance, the spin panos give a building a few px.
- **Verdict:** refused as a requirement; kept as a diagnostic (`Scene(..., diagnose=True)`).

### 2026-10-09 — Claims as the run's start (occ[] from footprint geometry)
- **Hypothesis:** `measure_footprints` starts each run above the nearer footprints' claims at their
  upper plausible heights instead of above the nearer measured tops (review §3 item 3).
- **Measured** (Miami, scratch monkeypatch of `measure_footprints`, ownership rule after): trusted
  readings with truth 64 -> 67, within 25 % 0.56 -> 0.45; after the rule 0.74 kept (fails 0.80).
- **Verdict:** refused. The claims act in the ownership walk after the measurement;
  `footprint_detect.py` is unchanged.

### 2026-10-09 — A footprint's prior p90 as evidence that a top is not its own
- **Hypothesis:** an untagged footprint whose reading clears its prior p90 claim, with a farther
  footprint's p90 claim holding the top, read that farther footprint (and likewise a nearer
  untagged footprint's p90 claim hides a top).
- **Measured:** Miami scores the same with or without it (towers tagged). Cartagena, same
  satellite readings: it left out 150 of 400 trusted readings (45 with tags and cadastre only) and
  verified_2 fell 42 -> 34; the lost rows were mostly PH plots of 15-21 floors read 55-81 m with a
  stereo or lean reading agreeing (b0191, b0339, b0411, b0447, b1434, b1505, b0637).
- **Verdict:** refused: the p90 holds 0-22 % of untagged buildings over 60 m; only a tag or a
  cadastre count takes a top away. The p90 still names the re-credit target.

### 2026-10-08 — A render-fit height optimiser over the drone images
- **Hypothesis:** render every footprint as a prism from the saved drone cameras and move the
  heights (and each camera's heading, pitch and height) until the render matches the MobileSAM
  instances and the sky labels; a "handover" step (lower a footprint to its prior, raise the one
  behind it) fixes readings of the tower behind. It would replace per-footprint column readings
  plus fusion.
- **Measured** (scratch `renderfit/`, Cartagena seeds 1/4/5/6, spin and sphere, fit v3; truth = OSM
  height tags plus the published towers):
  - published towers within 25 %: 5 of 7;
  - on the footprints fusion also reads (n 12): within 25 % 0.58, fusion 0.75; median error 8.9 %
    vs 4.4 %; bias -0.34 vs +0.14 (log);
  - where only the fit reads (n 23): 0.52 vs 0.48 for its start heights;
  - as a checker of fused readings (`checker.py`): Miami (LiDAR, n 50, 27 wrong) handover gain
    > 50 flags 9 readings with truth, 7 of them wrong (20 wrong missed); any flag (sky, split,
    handover) 26 flagged, 16 wrong. Cartagena: 3 wrong of 12; the handover flags only wrong ones
    (2-3), but any flag also marks 6 right ones.
- **Verdict:** refused as a height source: worse than fusion where both read, and it loses 2
  published towers. Recommended as a checker only: the handover flag marks readings for review
  (mostly wrong ones in Miami), not an automatic dispute until it is measured on more truth.

### 2026-10-07 — A joint camera position / heading / height fit from feet, water edge and tower outline
- **Hypothesis:** fitting a drone seed's position, heading and height together (observed building
  feet, the water edge, the tower outline) makes the far and overhead seeds usable (the "Open"
  item of the far-drone entry above).
- **Measured** (scratch `jointfit/`; Cartagena seeds 4/6/7, Chicago chi1 and wickerSKY, Miami mia2
  and mia3, Benidorm izalko; held-out tag / survey folds):
  - none of the 6 user-kept seeds became usable;
  - seed_6, the control (its recorded position is right: tags exact), moved 42 m and read 2.2x
    the tags;
  - Benidorm izalko: a constant ~34 m camera-height error plus ~1 deg of pitch, not position.
- **Verdict:** refused. The cues cannot place a camera within the ~10 m these seeds need; a cue
  that could is footprint corners matched to MobileSAM instance edges.

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
