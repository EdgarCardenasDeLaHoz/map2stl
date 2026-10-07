# F-LEARNTRUST — Learned trust and correction for skyline height readings

**Status**: pending (feasibility study 2026-10-06; no code in the repo yet)
**Proposal**: [../../proposals.md](../../proposals.md) → Skyline CV → F-LEARNTRUST
**Owner module**: `city2stl/skyline/` (`_pano/elevated.py::trusted`, `footprint_detect.py::measurement_weight`,
`footprint_detect.py::fuse_heights`)

## Goal

Replace the hand-tuned trust and fusion rules with a model trained on per-reading features and
surveyed truth:

- **trust**: P(reading within 25 % of truth), used in place of `trusted()`;
- **weight**: the same probability as the fusion weight, in place of `measurement_weight()`;
- **correction** (optional): a multiplicative factor on the read height.

It must win on held-out cities, not on the city it was tuned on.

## Feasibility study (2026-10-06): verdict

**Not yet.** The method is sound. The data to train it does not exist yet:

- Readings with full features (`footprint_detect.Measured` plus the pano, depth and MobileSAM
  instances) exist only for **Cartagena**: 1,438 readings, of which **84 are labelled**
  (53 footprints, 19 published-tower rows, 65 OSM-tag rows). That is one city with weak truth,
  so no leave-one-city-out is possible.
- The four truth-dense cities (Miami, Chicago, Seattle, Boston) were run only through the
  street-level registration chain. Its `heights.json` keeps, per view, only the height,
  confidence and heading offset. It has no rows, edges, instances or depth.
- Step 1 below (footprint-first readings on the benchmark cities) is therefore the real work.
  The model is cheap once the table exists.

Scripts and tables (scratchpad, not in the repo): `learned_trust/build_cartagena.py`,
`build_streetview.py`, `evaluate.py`, `results.json`, `cartagena_readings.csv`,
`streetview_views.csv`.

### Data found

| Source | Readings | Labelled | Truth | Features |
|---|---|---|---|---|
| Cartagena drone seeds 1/4/5/6/7 (`hires/<seed>_review_state.pkl`) | 117 / 172 / 174 / 580 / 395 = 1,438 | 12 / 7 / 2 / 36 / 27 = 84 | OSM tags; published height where `benchmark.match_known_tower` matches | full |
| Street-level views, `runs/benchmark/2026-10-05_0633/<city>_skyline_report/heights.json` | Miami 952, Chicago 629, Seattle 659, Boston 1,003, Benidorm 168, La Défense 235, Madrid 498, Prague 148 | 334 / 477 / 309 / 815 / 46 / 132 / 172 / 40 = 2,325 | `runs/benchmark/truth/<city>.json`, confirmed only | height, confidence, heading offset; distance rebuilt from seed position (site `seed_urls` / `auto_proposals`) |

- Each street view's label is the building's truth. The labelled views read a median 44-102 m on
  buildings whose truth is a median 10-25 m: most of them read a tower behind the footprint.
  That is an identity error, which no per-reading confidence can repair.
- `10_benchmark.py` scores buildings, not readings. It writes no per-reading table.

### Features built (Cartagena table)

- Geometry: px height, px base-to-top, top/bottom/base elevation, distance, m per px, n_cols,
  width, visible_frac, base_visible, top_edge, roof-fit confidence, camera height, today's weight.
- MobileSAM: the box's dominant instance (share of the box, number of instances, area,
  area / box, column coverage, solidity), whether that instance meets the sky at its top, and
  the gap between the instance top and the read top.
- SegFormer: group above and below the top edge (sky / building / vegetation / water / other),
  with the sky and building fractions.
- Depth: step ratio across the top edge, drift up the facade.
- Floors: `floor_bands.estimate_floors` (accepted, period, visible floors, floors-implied height
  and distance ratios).
- Cross-seed (label-free): other seeds reading the footprint, nearest relative disagreement,
  number within 25 %, agreement with trusted others, log ratio to the others' median, distance rank.

### Results

**Street-level views, leave-one-city-out** (HistGradientBoosting; never trained on the scored city).

- Rule = the closest per-view proxy of today's chain: the view agrees within 25 % with another
  view of the same building. The MAD outlier filter is not stored per view.
- "GBT @ rule precision" = the readings the model keeps at the rule's precision in that city,
  and how many of them are correct.

| City | n | Base rate | Rule kept / correct (prec.) | GBT @ rule prec. kept / correct | AUC GBT / confidence | Correct @ 50 % prec. GBT / conf. | Median rel. err: raw / corrected / prior only |
|---|---|---|---|---|---|---|---|
| Miami | 334 | 6 % | 118 / 7 (6 %) | 334 / 21 | 0.82 / 0.80 | 5 / 1 | 7.34 / 0.81 / **0.76** |
| Chicago | 477 | 21 % | 278 / 65 (23 %) | 414 / 97 | 0.74 / 0.68 | 8 / 1 | 0.97 / **0.47** / 0.52 |
| Seattle | 309 | 16 % | 200 / 38 (19 %) | 263 / 50 | 0.79 / 0.64 | 4 / 1 | 2.59 / **0.44** / 0.60 |
| Boston | 815 | 20 % | 430 / 112 (26 %) | 614 / 160 | 0.76 / 0.71 | 2 / 2 | 1.35 / **0.37** / 0.48 |
| Benidorm | 46 | 33 % | 3 / 0 | 46 / 15 | 0.42 / 0.62 | 0 / 11 | 0.39 / 0.51 / 0.85 |
| La Défense | 132 | 5 % | 38 / 2 | 114 / 6 | 0.91 / 0.73 | 0 / 2 | 7.29 / **0.43** / 0.47 |
| Madrid | 172 | 10 % | 69 / 6 | 172 / 17 | 0.69 / 0.64 | 0 / 0 | 0.99 / **0.47** / 0.61 |
| Prague | 40 | 18 % | 20 / 2 | 40 / 7 | 0.75 / 0.79 | 4 / 5 | 0.62 / **0.33** / 0.47 |

- The learned ranking beats the pipeline's confidence in 6 of 8 cities (AUC +0.03 to +0.18).
  At the rule's precision it keeps 30-200 % more correct readings.
- That precision is 6-26 %, which is useless. At a usable 50 % precision the model finds
  0-8 correct readings per city. These views are mostly misassigned, and ranking cannot create
  correct ones.
- The correction model cuts median error from 1-7x to 0.37-0.81, but it does so mostly as a
  height prior. A regressor on building features alone (area, view count; no reading) comes
  within 0.04-0.16 of it, and beats it in Miami. The reading adds little, which matches the
  existing T41 untagged prior.
- Verified by two seeds agreeing: 0-25 buildings per city whichever gate is used (Boston 25 / 25 / 23
  for all views / rule / GBT; 6 / 5 / 5 of the labelled ones right). Street views rarely come
  from two seeds. The gate cannot change this count; more seeds can.
- Permutation importance (AUC drop, mean over the four truth-dense cities): confidence 0.126,
  read height 0.068, footprint area 0.048, then elevation angle, agreement and heading offset
  under 0.005 each.

**Cartagena drone readings**: grouped 5-fold by footprint, so no tag is both trained on and
scored; 10 repeats. This is a held-out-footprint test, not a held-out-city one.

| | Kept | Correct | Precision | AUC |
|---|---|---|---|---|
| (a) `trusted()` | 15 | 10 | 0.67 | 0.76 (as binary) |
| `measurement_weight` as a score | | | | 0.73 |
| (b) GBT, threshold picked on the training folds | 2.5 | 0.8 | 0.32 | 0.74 |
| (b) GBT at the rule's precision (threshold set on the test folds, optimistic) | 6 | 4 | 0.67 | |
| L2 logistic, 11 features (C = 0.1) | | | | 0.76 |

- None of the 7 untrusted-but-right readings (b0539 among them) was recovered.
- Verified by two seeds: rule 10 (2 labelled, both right), GBT 1.
- Correction model: median relative error 1.62 → 0.50, but within-25 % falls from 20 % to 16 %.
- Single features with signal (AUC, or 1 − AUC where inverted): depth step at the top 0.80,
  distance 0.72, instance column coverage 0.73, instance meets sky 0.71, top edge sky 0.67,
  floors accepted 0.68.
- Trees fit at n = 84 make in-sample permutation importances that are all near zero
  (distance 0.02, instance area / box 0.004). The trees are not underfitting: the label count
  is the limit, so an MLP is not warranted.

## Plan (if approved)

1. **Readings on the truth cities.** Run the footprint-first measurement
   (`footprint_detect.measure_footprints` with Depth Anything and MobileSAM) on the street-level
   panoramas of Miami, Chicago, Seattle and Boston. Use a 1.7 m camera, and set the heading from
   the existing registration or the coastline fit. Save each seed's state (pano, pose, depth,
   inst, measured, fids) in the `_pano/stage_cache.py` format. Label with
   `benchmark.footprint_truth`, confirmed only.
   - Target: 2,000 or more labelled readings per city.
   - GPU work: run when the GPU is free (`wait_for_gpu`).
2. **Table builder.** Move `build_cartagena.py` into `tools/eval/reading_table.py`, generalised to
   any seed state. Add `city` and `camera` (street / drone) columns.
3. **Models.**
   - (b) HistGradientBoosting P(error < 25 %).
   - (c) HistGradientBoosting on log(truth / read), always compared with a prior-only model on
     the same buildings.
   - Leave-one-city-out across the 4 + 4 benchmark cities, plus Cartagena's published towers as
     an extra held-out check. The threshold is picked on the training cities' out-of-fold scores.
4. **Wire-in** (only if it passes): `trusted()` and `measurement_weight()` read
   `models/reading_trust.joblib` behind `SKYLINE_LEARNED_TRUST=1`. The hand rules stay as the
   fallback when the model is missing.

### Success criteria (all on held-out cities)

- At the rule's precision on drone/footprint-first readings (≥ 0.67), at least 1.3x the correct
  trusted readings in 3 of the 4 truth-dense cities. No city drops below the rule's precision
  by more than 5 points.
- Buildings verified by two images: no loss, and a gain of 10 % or more in at least two cities.
- Correction: beats the prior-only model by 0.05 or more in median relative error in 3 of the
  4 cities. Otherwise ship trust only.
- Cartagena published towers: no regression (6 of 7 within 25 % today).

### Evaluation protocol and risks

- **Leakage**:
  - Grouping is by city, and within a city by footprint. A tag or truth value is never in the
    training split of the reading it scores.
  - `SKYLINE_TAG_FILTER=1` drops views far from their OSM tag before `heights.json`, so a tagged
    building's surviving views are tag-selected. Score untagged buildings separately, or run
    with the filter off.
  - Cross-seed agreement features are label-free, but they look at the other seeds of the same
    city. That is allowed at run time, but they must be computed inside the scored city only.
- **City shift**:
  - The registration-chain views read 2-9x truth in some cities and 1-2x in others (base rate
    5-33 %). A model trained on one regime miscalibrates on another (Benidorm: AUC 0.42).
  - The ML-height history ([../../history/ml-height/README.md](../../history/ml-height/README.md):
    Retna went from MAE 3.8 m on its own tiles to 7.6 m on 15 cities) and the class-prior
    transfer failure in [building-heights.md](../../decisions/building-heights.md) say the same.
  - Mitigation: small trees, features that are physical and unit-free (ratios, angles,
    px / floor), a threshold set per training city, and a report of the worst city, not just
    the mean.
- **Tag noise**:
  - OSM tags are often roof or level counts. Cartagena tag rows are 9 % correct against
    published rows at 58 %, which is either the readings or the tags.
  - Use survey/3D Tiles truth (confirmed status) for training. Use Cartagena tags only as a
    report.
- **Identity errors**: most wrong street readings measure the wrong building. Trust can reject
  them but cannot repair them, so coverage still comes from more and better-placed images
  (2026-10-06 decision in [building-heights.md](../../decisions/building-heights.md)).

## Heavier options considered

### MobileSAM decoder fine-tuned on OSM-projected footprint masks (weak labels)

- **Idea**: project each OSM footprint into the pano with the fitted pose and use it as a mask
  label. Fine-tune the MobileSAM decoder (the encoder stays frozen), or train T42's distilled
  building-only decoder on the masks MobileSAM keeps.
- **For**: instance quality is the strongest learned-feature signal on Cartagena (column
  coverage 0.73, meets sky 0.71). Instance changes are where `_column_run` decides the top.
- **Against**:
  - Projected footprints are wrong exactly where readings fail: the pose error is 4-6° in
    bearing before the outline fit, there is no roof or top extent, and occlusion makes the label
    the union of the near and far buildings.
  - Training against them teaches the model the pose error.
  - Gains are measured only through height error, which needs the same truth cities as above.
- **Verdict**: do it as part of T42 (speed). Accept it only if IoU against today's masks stays
  ≥ 0.9 and drone-reading error does not rise. Do not pursue it as an accuracy project before
  step 1 exists.

### End-to-end height CNN

- **Verdict**: no.
- The project's own history (Retna: dataset-specific; Phase G/H not transferable; never wired)
  and the label counts here (hundreds of readings per city, against the tens of thousands a
  CNN needs) rule it out.
- It would also hide the geometry, `d·(tan e_top − tan e_base)`, which is exact when the
  footprint and edges are right.

## Option 2 — Low-res to hi-res: teacher-student distillation (assessment only)

User question (2026-10-06): can a network be fine-tuned so that low-resolution images give the
same results as high-resolution ones? Assessed only: no training now (GPU busy).

- **Teacher**: the pipeline on hi-res panoramas. 30° views in two pitch rows, ~21 px/deg
  (`elevated.HIRES_FOV_DEG`, `capture_sphere_pano`).
- **Student**: the same pipeline on the default 75° spin, ~7.3 px/deg.

### Data

- **Real pairs**: Cartagena seeds 1, 4 and 5 have both captures. Hi-res views are in the Street
  View image cache. Earlier runs are in `scratchpad/hires/seed_*_hi-res.pkl` against
  `seed_*_default.pkl`.
  - That is 3 seeds and ~460 readings, a few dozen of them labelled. Enough to measure the gap,
    not to train a segmentation head.
- **Synthetic pairs**: downsample any hi-res panorama by 21/7.3 ≈ 2.9x (seeds 6 and 7, later the
  benchmark cities once captured hi-res). Unlimited in count, but see the domain gap.

### Candidate targets

| | Target | Cost | Expected value |
|---|---|---|---|
| (a) | Super-resolution front end (Real-ESRGAN / SwinIR, untrained baseline first) before SegFormer and MobileSAM | No training for the baseline; ~9x the pixels into SegFormer, MobileSAM and Depth Anything, which gives back the speed that was the point | Low. It can sharpen edges that are sampled, but it invents floor bands and rooflines that are not. Use it as a quick baseline only |
| (b) | Fine-tune the MobileSAM decoder and/or SegFormer head so their masks on low-res input match the teacher's masks (distillation loss on the up-sampled teacher masks) | Medium: GPU, synthetic pairs, mask IoU as the training target | Medium for masks: the top edge and instance boundaries move by up to ~1 px at 7.3 px/deg. Bounded by the information limit below |
| (c) | The learned trust/correction model (this plan) fed low-res features, predicting the hi-res reading (target = teacher height, or the teacher's trust) | Low: trees on the reading table, no GPU | Highest value per hour: it labels every low-res reading with its teacher, with no survey truth needed. It learns *when* low-res is enough (near, tall, sky-topped) rather than trying to recover pixels |

### Information limits

| | 7.3 px/deg (student) | 21 px/deg (teacher) |
|---|---|---|
| One pixel of height at 1.3 km | 1,300 · tan(0.137°) ≈ **3.1 m** | ≈ 1.1 m |
| One pixel at 500 m | 1.2 m | 0.4 m |
| A 3.2 m floor at 1.3 km | ~1.0 px | ~2.9 px |
| Farthest floor bands readable at 2 px/floor | ~670 m | ~1.9 km |
| At `floor_bands`' 5 px/floor acceptance | ~270 m | ~770 m |

- Below 2 px per floor the floor period is under the Nyquist limit. No network can recover it,
  and SR or a distilled head would hallucinate a period.
- So floor-band verification beyond ~670 m is out of reach at low-res, whatever is trained.
- Top-edge height error from ±1 px quantisation is ±3 m at 1.3 km. That is under 25 % for
  anything over ~12 m, so trust and height for tall buildings are not resolution-limited at
  that distance. Edge *identity* is the limit: which instance or roof the top belongs to.

### Domain gap

- Downsampling a hi-res panorama is not a real low-res capture. Street View serves each FOV from
  its own pyramid level, with its own JPEG quality, sharpening and stitching seams. The 75° spin
  is also stitched from fewer views with a different projection per column.
- A student trained only on synthetic pairs must be checked on the real pairs (seeds 1, 4, 5)
  before anything is believed.
- Mitigation:
  - synthesise with the API's own path: request the same pano at both FOVs where possible;
  - add JPEG re-compression and mild blur to the downsampled views;
  - hold out one real seed.

### Value case

- **Speed**: ~(21/7.3)² ≈ 8-9x fewer pixels per panorama for MobileSAM, SegFormer and Depth
  Anything. Also 12 API images per seed instead of 24 (hi-res needs two pitch rows).
- **Sources that cannot be recaptured hi-res**: Wikimedia Commons photos (F-WEB2), video frames,
  old Street View captures, other users' panoramas. For these, (c) is the only option that
  helps.

### Gating measurement

The actual hi-res vs low-res gap on seeds 4 and 5 with the current code (another agent is
measuring it now). Rules:

- If low-res readings are already within ~10 % of the hi-res ones on trusted footprints, and
  trust agrees, there is nothing to distill. Use low-res for speed and hi-res for verification.
- If the gap is mainly *which* footprints are trusted or found, do (c).
- If it is mask boundaries on near and mid-range buildings (< 700 m), consider (b).
- Do (a) only as a free baseline.

## Recommendation

1. **Do step 1 first.** Footprint-first readings with saved states on the four truth-dense
   cities. Without them, no learned trust can be trained or judged out of city. The Cartagena
   table (84 labels) shows that learned trust only *matches* the hand rule (AUC 0.74-0.76 against
   0.76).
2. **Then (b) + (c) as trees on the reading table**: cheap, auditable and CPU-only, with the
   hand rules kept as the fallback. Expect the gain to come from ranking within the trusted pool
   (fusion weights) and from depth-step and instance features, not from rescuing readings the
   rules reject today: none of the 7 untrusted-but-right Cartagena readings was recoverable.
3. **Not now**: MobileSAM fine-tuning for accuracy (do it inside T42, for speed, gated on mask
   IoU), an end-to-end height CNN (the project history and the label counts both rule it out),
   and SR front ends (they hallucinate below 2 px/floor).
4. **Low-res distillation**: wait for the seeds 4/5 gap measurement. If a gap exists, start
   with option (c) on teacher labels, which needs no survey truth and no GPU.
