# Skyline heights — ranked improvement plan (review of the 2026-10-05 results)

**Status:** proposal (task T20; no code changed). **Inputs:**
[results bundle](../../research/skyline-results-2026-10-05/README.md),
[skyline STATUS](../../../city2stl/skyline/docs/STATUS.md),
[F-SKYBENCH](../active/F-SKYBENCH-height-benchmark.md),
[F-WEB2](../active/F-WEB2-commons-skyline-photos.md),
[F-DET](../active/F-DET-detection-quality-and-early-out.md) (F-DET6).
**Follow-up:** [technology review](skyline-technology-review-2026-10-05.md): other approaches,
photogrammetry, and scoring the shipped height merge as the baseline (do that first).

Numbers are from the bundle files unless a section says otherwise. "SV" = the Street View
pipeline. "Confirmed" = survey and 3D Tiles agree within max(3 m, 10 %).

**Where to build it:**
- **Local** = needs this PC: GPU, `runs/` and `cache/` data, Street View, 3D Tiles or Overpass.
- **Cloud** = can be written and unit-tested on synthetic data in a cloud session. The real-data
  check then runs locally.

## Summary

| Rank | Proposal | Path | Expected gain (benchmark) | Cost | Where |
|---|---|---|---|---|---|
| 1 | Withhold SV heights on untagged buildings (stopgap) | SV | Miami untagged MAE 108.6 → ≤ 17 m | S | cloud + one local re-score |
| 2 | Occlusion-aware roof assignment in the SV pano path (port F-DET6 footprint-first) | SV | untagged bias +65…+113 → < ±15 m; pair order 0.42–0.66 → ≥ 0.75 | L | cloud core, local runs |
| 3 | Run photo C2 (T16) on Miami and add it to the scorer | photo | the first photo heights on *untagged* buildings | S | local |
| 4 | Photo weighting: anchor deviation and photo count | photo | photo MAE 23.2 → ~18 m | S | cloud |
| 5 | Locate more photos: viewpoint groups and the rival-set gate (T18, step G) | photo | kept photos 27 → 40+; more buildings with 3+ photos | M | cloud core, local runs |
| 6 | Stepped tops (T17) and camera roll (T19) | photo | the 100 m+ misses (e.g. 155 vs 235 m) | S–M | cloud |
| 7 | Benchmark hygiene: T6 rule re-run, tag filter off, per-source offset, one code path | bench | a trustworthy baseline; Seattle 46 % and Prague 30 % agreement explained | M | local (+ cloud for code) |
| 8 | Drone: near seed wins, cap distance, anchors from photos | drone | seed agreement 22–27 % → ≥ 50 % inside 1 km | M | cloud core, local runs |
| 9 | Tall glass towers read low | SV | Miami 100 m+ bias −30.6 → −10 m | M | local |
| 10 | Drop seed disagreement and confidence as error signals | SV | honest uncertainty in the report | S | cloud |

Order of work: 1 now (it is a strict gain). Then 3 and 4, which are cheap, are on top of T16,
and give the yardstick for 2. Then 2, the real fix. Items 5–10 can run in parallel with 2.

---

## 1. Withhold Street View heights on untagged buildings (stopgap)

**Evidence**
- Miami, 162 confirmed untagged buildings (`miami_buildings.csv`):
  - SV MAE 108.6 m, bias +94.6 m;
  - SV median prediction 112.9 m, against a true median of 11.0 m;
  - 75 % of the SV readings are more than 3× the truth.
- A **constant 11 m beats SV on these buildings: MAE 17.1 m against 108.6 m.**
- Every untagged SV height is `streetview_source = geometric`. No rescue path is involved.
- Every city shows the same bias on untagged buildings (`benchmark_8cities_2026-10-04.json`):

  | City | Untagged bias |
  |---|---|
  | Miami | +104.7 |
  | Chicago | +75.8 |
  | Seattle | +72.6 |
  | Boston | +65.5 |
  | La Défense | +112.9 |
  | Madrid | +83.2 |
  | Prague | +29.5 |

**Proposal**
- Until item 2 lands, use the existing fallback (levels, area/type default, or GBA) for a
  building without an OSM height tag. Do not emit an SV estimate for it.
- Keep the SV value in `heights.json` under a diagnostic key, so the benchmark can still score it.
- Gate it with one flag, so the benchmark can run both ways.

**Expected gain**
- Untagged MAE drops by roughly 90 m in Miami. Expect similar drops in the other cities, since the
  bias is +65 to +113 m everywhere.
- The printed models stop showing 100 m "towers" on low blocks.

**Cost:** small. One branch in `_core/height.py::aggregate_building_heights`, or where
`effective_height_m` is chosen, plus a flag and tests.

**Verify:** `10_benchmark --score-only` on the cached 2026-10-03 reports, with the flag on and
off. Compare untagged MAE and bias per city. No Street View calls are needed.

**Risk**
- It hides real untagged towers: Miami has 10 untagged buildings over 100 m, and SV already
  reads them 73.9 m low.
- Mitigation: exempt buildings whose SV height agrees with an independent source (GBA, photo C2).

**Where:** the code is cloud work. The re-score needs the local `runs/`.

## 2. Occlusion-aware roof assignment in the SV pano path

**Evidence**
- Pair order inside one view is near chance: Miami 0.438, Seattle 0.478, Madrid 0.442 (per-view
  `pair_order` in the benchmark JSON).
- A camera-height or tilt error would keep the order, so the order points at assignment, not at
  geometry (STATUS).
- Same 34 Miami buildings (photo JSON / STATUS):

  | Source | MAE | Pair order |
  |---|---|---|
  | Photos (roof given only to the tower the outline puts on top) | 13.0 m | 91 % |
  | Street View | 71.8 m | 55 % |

  The geometry is the same; the assignment differs.
- More views do not help until 4+: Miami untagged MAE is 109.0 m for one view, 112.2 m for 2–3
  views and 46.0 m for 4+ (n 4). The error is systematic, not noise.
- F-DET6 already has the mechanism on the drone path:
  - measure footprints nearest first;
  - the tops of nearer buildings form the occluder line;
  - skip surfaces more than 35 % nearer than the footprint;
  - accept a base only when its depth agrees with the footprint distance.

  On Cartagena it cut seed_1 against the OSM tags from 38 m to 16 m.
- The named root cause, from the F-DET6 notes: `match_segments_to_buildings` keeps every candidate
  within 0.10 of the best score, and the nearest wins.

**Proposal**
- In `_pano/detect.py`, replace the nearest-wins match for street panos with footprint-first
  measurement (`footprint_detect.py`), at the camera height of a street pano.
- The roof row for a footprint is credited only where:
  - no nearer, already-measured footprint covers those columns higher; and
  - the depth at the roof row agrees with the footprint's distance, using the 35 % rule.
- A tower roof seen above a low building then goes to the tower behind it.

**Expected gain**
- Untagged bias goes from +65…+113 m to below ±15 m.
- Per-view pair order goes from 0.42–0.66 to ≥ 0.75. The photo path reaches 0.86 with the same
  rule.
- Tagged buildings stop depending on the tag filter.

**Cost:** large. Street panos differ from drone views:
- the camera is 2.5 m up;
- bases are mostly hidden by the street front;
- depth saturates beyond about 1.2 km.

Design and unit tests can come from the F-DET6 tests. Tuning needs real panos.

**Verify**
- Full benchmark, eight cities.
- Targets: untagged bias and MAE per city, per-view pair order, and the tagged MAE with the tag
  filter **off** (item 7).
- Accept when untagged MAE beats the item-1 fallback in at least 6 of 8 cities.

**Risk**
- Hidden bases from the street mean the base row and the distance check have less to work with
  than from a drone.
- The tower behind may be missing from OSM, so its roof stays unassigned. That is correct, but
  coverage falls; report coverage per band.
- **Coordination:** `footprint_detect.py` and `_pano/orchestrator.py` are being changed by the
  local panoramic session (branch `f-det6-report`). Build on that branch after it merges.

**Where:** the core can be built in the cloud on synthetic panos, after `f-det6-report` merges.
Runs and tuning are local.

## 3. Run photo C2 (T16) on Miami and add it to the scorer

**Evidence**
- Photos measure only towers with OSM heights so far (README headline).
- Where OSM has a height, the OSM tag beats the photo: 8.9 m against 23.2 m on 142 buildings, and
  5.9 m against 13.0 m on 34. So photos add information only for untagged buildings and for bad
  tags.
- T16 (`photo_heights.py::implied_heights`, `agreed_heights`; `17_photo_pipeline.py --untagged`)
  is on branch `cloud/T16-photo-heights-untagged`. On a synthetic scene it recovers a 230 m
  untagged building from 3 photos and rejects a decoy. It has not run on real data.

**Proposal**
- Run `17_photo_pipeline.py --region miami --untagged` on the cached run-2 photos.
- Score the `untagged` rows against confirmed truth, split by `n_photos` and by `spread_m`.
- Also write them into the `heights.json` the benchmark reads, with source `photo_c2`.

**Expected gain**
- The first measured heights on untagged buildings that do not come from SV.
- The F-WEB2 success criterion, "untagged MAE < 30 m, pair order ≥ 70 %", becomes checkable.
- Gives item 1 its exemption list.

**Cost:** small (one local run, about an hour on the cached photos).

**Verify:** `summary["untagged"]["photo_vs_truth"]` against confirmed truth; compare with SV on
the same buildings and with the constant-11 m baseline.

**Risk:** few untagged buildings are in 2+ kept photos. Today 79 of 142 measured buildings are
in 2+ photos, but those are tagged. If C2 returns fewer than 20 buildings, item 5 comes first.

**Where:** local (cached photos and masks).

## 4. Weight photos by anchor deviation and photo count

**Evidence** (`miami_photos.csv`, `miami_buildings.csv`)

Per kept photo:
- anchor deviation (median |photo − OSM tag| on the photo's anchors) correlates with the photo's
  own MAE at **r = 0.45** (27 kept);
- kept photos median 14.8 m anchor deviation, rejected 24.3 m;
- the winning margin did not predict accuracy (STATUS).

Per building, MAE by number of photos:

| Photos | n | MAE | Within 25 % |
|---|---|---|---|
| 1 | 63 | 29.3 m | — |
| 2 | 29 | 20.0 m | — |
| 3+ | 50 | 17.5 m (median 7.5) | 0.90 |

- Photo spread does not correlate with error (−0.04), so spread is not the weight to use.
- By truth band: 0–60 m 28.8 m, 60–100 m 28.1 m, 100–150 m 20.4 m, 150 m+ 19.6 m.

**Proposal**
- Aggregate per building with weights 1 / max(anchor deviation, 5 m), instead of a plain median.
- Report single-photo buildings at lower confidence, or withhold them when the photo's anchor
  deviation is above about 20 m.
- Calibrate the cut with leave-one-out over photos.

**Expected gain:** photo MAE on confirmed buildings from 23.2 m to about 18 m. The bad
single-photo tail is the target; "Downtown Miami (8110579514)" alone reads 74.8 m off.

**Cost:** small. The aggregation in `17_photo_pipeline.py::finish`, plus tests.

**Verify:** `17_photo_pipeline.py --rescore` (no re-solve) for MAE, pair order and coverage by
number of photos. Keep coverage within 5 % of today's 142.

**Risk**
- The anchors are OSM tags (Miami tags against truth: 7.7 m MAE), so a photo of badly tagged
  towers is under-weighted.
- n = 27 photos is small; check that r holds on Chicago and Boston.

**Where:** cloud, with a local re-score.

## 5. Locate more photos: viewpoint groups and the rival-set gate

**Evidence** (`miami_photos.csv`, 101 candidates)

| Route | Kept / candidates | Gate passed |
|---|---|---|
| Labels | 1 / 1 | — |
| Recorded | 16 / 30 | 14 |
| Linked | 1 / 3 | — |
| Search | **9 / 67** | **1** |

Search is the largest pool and the weakest route. Earlier validation (STATUS): with the field of
view fixed from EXIF, 3 of 12 located within 300 m. The runner-up margin separates right from
wrong (right 0.17–0.19, wrong 0.009–0.035).

**Proposal**
- **T18:** a runner-up counts as a rival only when it `identify`s a different set of towers. Two
  poses that read the same towers are not ambiguous for heights.
- **Step G:** group photos by skyline outline (shift and zoom search). A group with a located
  member passes its camera on, and each member is then refined locally. A group with none gets
  one joint search.

**Expected gain**
- More search-route photos kept: 9 → 20+, and 27 → 40+ overall.
- More buildings with 3+ photos, the band where MAE is 17.5 m. This feeds items 3 and 4.

**Cost:** medium (T18 small; G medium).

**Verify**
- The located-photo validation (place a photo with its location hidden; distance and heading
  error). Target from F-WEB2: ≥ 70 % within 300 m and 3°.
- Then `17_photo_pipeline` kept counts and photo MAE: no rise in MAE.

**Risk**
- Grouping errors propagate a wrong pose to the whole group. F-WEB2 keeps a gallery for user
  review.
- Elevated cameras (ship decks) need the camera-height search.

**Where:** cloud for the code and synthetic tests; the validation needs the cached photos (local).

## 6. Stepped tops (T17) and camera roll (T19)

**Evidence**
- Southeast Financial Center reads 155 m against 235 m: the core median lands on a lower step
  (STATUS, F-WEB2 C1).
- Two towers read about 68 m against about 168 m: they own only 18–24 columns.
- Roll is not modelled: `refine`, `_score_all_headings` and `solve_pose` assume a level
  horizon. A rolled hand-held shot shifts the roof row linearly across the frame, which the
  tilt fit cannot absorb. This is unmeasured in the bundle: add the fitted roll to
  `miami_photos.csv` to size it.

**Proposal**
- **T17:** measure the core median and the peak of `y_top`. Choose per tower by leave-one-out
  agreement with the other anchors, or per photo by agreement across photos.
- **T19:** add roll (±3°) to the pose fit.

**Expected gain**
- The 150 m+ band is 19.6 m MAE today; mainly the tall stepped tops gain.
- Roll mostly helps wide hand-held shots.

**Cost:** small to medium; the tasks are already on the board.

**Verify:** `--rescore` and per-band MAE; the known-answer labels photo (19 towers, 29.0 m MAE).

**Risk:** the peak picks up spires and antennas. OSM `height` usually excludes them, the p95
truth often includes them. Report both choices against truth.

**Where:** cloud.

## 7. Benchmark hygiene before the next baseline

**Evidence**
- **The T6 50 %-cells rule postdates the truth in the bundle** (README). Miami's lidar covers only
  the coast: 338 tiles_only against 332 confirmed in the CSV.
- Disputes:
  - Seattle 185 disputed against 156 confirmed;
  - Madrid 129 against 64;
  - Prague 54 against 23;
  - Benidorm 62 against 33.
- Systematic offsets (STATUS):
  - Seattle 3D Tiles is +3.9 m over lidar;
  - CNIG is +5.4 / +5.6 m below 3D Tiles in both Spanish cities;
  - Prague's 3D Tiles gives one flat 104.15 m.
- **The tag filter makes tagged scores look good:**
  - tagged bias: Miami −15.3, Chicago +3.2, Boston +4.5, Madrid −2.6;
  - Miami tagged SV against tags: 23.9 m (`run_vs_tag`);
  - OSM tags against truth: 7.7–10.2 m.
- Six of eight site files carry Cartagena opt-ins (`pano_only_pdf`, cross-view, coastline); Miami
  and Chicago do not. The benchmark runs two code paths (F-SKYBENCH progress).

**Proposal**
- Re-measure truth with `--refresh-truth` under the T6 rule.
- Add a per-source median offset (3D Tiles minus survey, from the city's agreeing footprints) to
  the cross-check, so a constant +5.5 m CNIG gap does not dispute every short building. Report
  the offset per city.
- Run one baseline with the tag filter **off**. Item 2 must be judged without it.
- Drop or unify the Cartagena opt-ins in the benchmark sites. That is the user's choice; it needs
  a new baseline.

**Expected gain**
- Spanish confirmed counts: Benidorm 33 and Madrid 64 → likely 2× (the offset explains most
  disputes).
- An honest tagged score.
- One code path, so differences come from the code.

**Cost:** medium. The offset is a small change in `benchmark.py::footprint_truth`. The rest is
running time.

**Verify:** confirmed counts per city; the F-SKYBENCH success criterion "≥ 80 % agreement";
two back-to-back runs within 1 m MAE.

**Risk:** an offset correction can hide a real ground-model error (Seattle hills). Apply it only
where the IQR of the offset is under about 5 m.

**Where:** the code is cloud work; the runs are local (3D Tiles, survey reads).

## 8. Drone path: near seed wins, cap distance, anchors from photos

**Evidence** (`cartagena_drone_footprints.csv`, `cartagena_drone_cameras.json`; no truth)

Per seed:

| Seed | Footprints | Median distance | Base visible | Camera fit |
|---|---|---|---|---|
| seed_4 | 187 | 738 m | 58 % | moved 320 m W, 200 m S; misfit 0.62° → 0.16° |
| seed_5 | 199 | 702 m | 52 % | recorded position; misfit 0.39° |
| seed_1 | 107 | 505 m | 60 % | `source = ground`; misfit 1.02° (no usable waterline) |

Seed agreement within 25 %:

| Pair | Shared | Agree | Far seed taller (of disagreements) | Both bases visible: agree |
|---|---|---|---|---|
| seed_4 / seed_5 | 93 | 27 % | 57 % | 22 % (n 49) |
| seed_4 / seed_1 | 14 | 21 % | 82 % | — |
| seed_5 / seed_1 | 7 | 14 % | 100 % | — |

- **A visible base does not raise agreement,** so the error is the top, not the base.
- Median height by what ended the top:

  | Seed | `sky` | `depth` |
  |---|---|---|
  | seed_4 | 85 m | 42 m |
  | seed_5 | 125 m | 52 m |
  | seed_1 | 96 m | 21 m |

  A top read against sky means nothing was found behind. That is the case where a farther tower
  can merge in.
- Fused against the 18 OSM tags: median 33.6 m off. The tags are partly planned heights (Allure).
- 79 of 384 fused footprints are disputed.

**Proposal**
- In `footprint_detect.fuse_heights`, when seeds disagree, prefer the **nearer** seed, not "most
  reliable" by visibility. Drop readings beyond 1.2 km, where depth saturates (STATUS).
- Flag `top_edge = sky` readings taller than the next footprint behind them as suspect.
- Measure Cartagena drone heights against photo C2 heights (item 3) from Cartagena Commons
  photos, as a truth substitute.

**Expected gain**
- Seed agreement inside 1 km from 22–27 % to ≥ 50 %.
- Fewer disputed footprints.
- A truth proxy for a city with no survey.

**Cost:** medium. Fusion is small; the Cartagena photo run reuses `17_photo_pipeline`.

**Verify:**
- agreement on shared footprints within 1 km (only 5 pairs today: more seeds near the same
  blocks are needed);
- drone against photo C2 where both exist;
- Hotel Estelar against 202 m (Wikidata).

**Risk**
- Few shared near pairs, so the metric stays noisy (F-DET6: "cross-seed agreement doesn't show
  progress any more").
- **Coordination:** `footprint_detect.py` is in the panoramic session's do-not-touch list. Build
  after `f-det6-report` merges, or hand the change to that session.

**Where:** cloud for the fusion rules and unit tests; local for runs and photos.

## 9. Tall glass towers read low

**Evidence**
- Towers over 100 m read low in every city with tall towers: Miami −30.6 m (benchmark), −15 to
  −34 m across cities (STATUS).
- Miami untagged towers over 100 m: −73.9 m (n 10).
- Hypotheses and trace tooling exist: `scripts/09_height_trace.py`, glass-roof plan in
  `docs/plans/done/skyline/`.

**Proposal:** run the trace on the Miami 100 m+ confirmed set after item 2. Assignment may fix
part of it, since a near building can block the tower's own columns. Then test mask reach on
reflective tops, comparing the SegFormer b1 mask against b3 on these towers only.

**Expected gain:** Miami 100 m+ bias from −30.6 m to about −10 m. This is a small share of the
total error now.

**Cost:** medium. **Verify:** benchmark 100+ m band per city. **Risk:** b3 costs about 3× and was
rejected before on Cartagena-only evidence (F-SKYBENCH).

**Where:** local (GPU, panos).

## 10. Drop seed disagreement and confidence as error signals

**Evidence**
- Miami SV: seed disagreement median 0, correlation with error 0.0.
- `corr(log area, error)` = −0.17.
- F-SKYBENCH: confidence against error corr 0.02 / 0.16.
- Photo spread against error: −0.04.

**Proposal**
- Stop presenting SV confidence as accuracy in the report and the UI.
- Use the signals that do predict error:
  - for photos, anchor deviation (r 0.45, item 4);
  - for SV, the number of views (4+ views 46 m against 109 m for one) and the item-2 depth
    consistency.

**Cost:** small. **Verify:** correlation of the new signal with |error| on the benchmark
(want > 0.3). **Risk:** none to heights; report wording only.

**Where:** cloud.

## Not proposed (and why)

- **More SV views per seed:** 2–3 views score worse than one (112.2 against 109.0 m untagged).
  Views help only once assignment is fixed.
- **Bigger segmentation model as a general fix:** relative order is near chance at any mask
  quality while assignment is nearest-wins; b3 stays a 100 m+ experiment (item 9).
- **Metric depth model (ZoeDepth / Metric3D):** it helps far towers (beyond 1.2 km), but the
  dominant error is near low buildings. Revisit after items 2 and 9.
