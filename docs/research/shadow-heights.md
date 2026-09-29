# Shadow-derived building heights (Track A)

> Status: historical research notebook, Aug 2026 (moved from memory-bank/shadow_heights.md, lines 1-477).
> Conclusions live in [../decisions/building-heights.md](../decisions/building-heights.md); roof-shape part in [roof-shape-research.md](roof-shape-research.md).


Status as of 2026-08-28. Experiment lives in the session scratchpad; not yet promoted to a
production provider.

## Correction to the first reported result

The first result posted from this work — MAE 20.28 m, corr +0.770 over 17 accepted footprints —
was scored against a misaligned plate and does not stand. `load_plate(slug, alignment)` takes two
alignment modes, and the misleading one is called `truth`:

| alignment | matrix scale | building-mask IoU | corr(plate, OSM tag) | n |
| --- | --- | --- | --- | --- |
| `truth` | 1.330 | 0.112 | −0.017 | 22 |
| `register` | 0.667 | 0.335 | **+0.801** | 37 |

`truth` builds its matrix from `_ground_truth/{slug}.json` via `truth_in_export_frame`; `register`
reads `meta["register_transform"]["matrix"]`. **Use `register`.** Under it the plate's own scale
estimates cohere — 21.40 least squares, 23.74 median ratio, 22.36 p99 ratio, 21.84 from the
vendor's stated tallest — which is exactly the agreement `calibrate_m_per_unit` documents as its
sanity check. Under `truth` those same three disagree by a factor of 1.75.

The tell was that the plate correlated with the OSM height tags at −0.017 while the shadow
estimates correlated with the same tags at +0.727. When a new method agrees with an independent
reference and the established reference does not, suspect the reference.

## Result on Miami

Scored per footprint against the registered STL plate under `register`, p90 of covered pixels,
same protocol as the other height sources. Estimator is the modelled-exit fit described below.

| source | n | MAE (m) | bias (m) | corr |
| --- | --- | --- | --- | --- |
| Google 3D Tiles | 63 | 14.02 | +1.73 above 100 m | +0.922 |
| **shadow, accepted** | **17** | **17.48** | **−2.44** | **+0.792** |
| Overture | 61 | 20.95 | — | +0.866 |
| panorama | — | 59.85 | — | +0.117 |

The Google and Overture rows are carried over from the earlier satellite comparison. They were not
recomputed here, but they cannot have come from the broken alignment: a plate that correlates with
the OSM tags at −0.017 cannot yield +0.922.

Splits of the shadow result: OSM-tagged n=13 MAE 12.96 corr +0.937; untagged n=4 MAE 32.14. With no
acceptance rule at all, n=57 MAE 65.33 corr +0.332, and on the tagged subset of that, n=32 MAE
26.99 bias +3.54 corr +0.835.

Against the OSM height tags, which are independent of the plate: accepted n=16 MAE 21.61 corr
+0.823; unselected n=36 MAE 33.76 corr +0.713. Note that the two references disagree with **each
other** by 25.98 m MAE (corr +0.794, n=32), so neither supports tuning below roughly that scale,
and a threshold fitted against one does not transfer to the other.

Coverage remains the real limitation: of 122 footprints, 97 yield a measurement, 57 are on the
plate, and 17 survive acceptance.

## Solved geometry, and why it is not circular

No other height source is used anywhere in the chain.

- **Date** from the ESRI World Imagery `identify` endpoint, which returns per-point source-scene
  attributes (`DATE`, `RESOLUTION (M)`, `ACCURACY (M)`, `DESCRIPTION`). Miami's bbox probed on a
  9x9 grid returns a single uniform scene: 2025-01-08, 0.0762 m, Miami-Dade County Orthos. This
  overturned the planning assumption that ESRI is an undated mosaic.
- **Shadow bearing** from the image, by Minkowski shift voting: shift every footprint mask by each
  trial bearing and score darkness on ground that is not itself a building, summed over all
  footprints. Miami solves to 13 deg. A street-grid artefact would peak at both theta and
  theta+180; a shadow peaks at one.
- **Sun elevation** by inverting azimuth to time. On a known date at a known latitude, scanning
  daylight minutes for the azimuth matching the observed bearing recovers the acquisition time and
  hence the elevation. Miami: sun azimuth 193 deg, 18:10 UTC, elevation 41.0 deg, so
  height = length x 0.870.

The solar model is a local USNO low-precision implementation (solarpos.py) because no ephemeris
library is installed. Validated against Sentinel-2 scene metadata: Cartagena elevation error
0.156 deg, azimuth 0.161 deg, inverse time error 0.8 min; Miami 0.223 / 0.528 / 1.3 min.

Near solar noon the elevation is insensitive to bearing error, which is what makes the inversion
safe: a +/-10 deg bearing error moves heights by only x0.939 to x1.018.

## Measurement

1. **Shadow index** = local darkness x blue ratio. Shadow is lit by skylight only, so it is both
   darker than its surroundings and relatively bluer. Absolute darkness alone fails: a tower's
   shadow over bright concrete is *paler* than lit asphalt, so any brightness threshold labels
   roads and misses shadows. Index chosen by d-prime against OSM-tagged heights only.
2. **Profile** — slide the footprint mask along the bearing in 2 m steps and take the mean index
   over the free ground it lands on.
3. **Length** — fit the profile's known shape. See below; this replaced a two-level step fit.
4. **Acceptance** — three checks: `near_level >= 0.17` (a dark band must touch the wall),
   `fill >= 1.0` (that band must be continuous from wall to tip), `tail_over_scene <= 2.0` (the
   ground past the tip must be lit).

### The profile is a ramp, not a step, and the ramp is computable

Past the true tip the sliding footprint does not leave the shadow all at once — it slides out over
its own depth, so the profile falls gradually. A least-squares two-level step fitted to that shape
puts its break part-way down the fall, which reads as extra shadow and so as extra height.

Measured against ray-cast shadows where the answer is known exactly, that error is
**0.198 x the footprint's depth along the bearing**, correlating with depth at +0.833 and with
height at only +0.093. It is a geometry artefact, not a height-dependent one.

The fraction of the footprint still inside the shadow at offset u is the footprint's
autocorrelation along the bearing at lag u, computable from the mask by shifting it against itself.
Fitting `profile ~ a * shape(t; L) + b` with that shape fixed and only the tip position L free
removes the bias:

| estimator | MAE | bias | corr |
| --- | --- | --- | --- |
| two-level step | 16.65 | +16.65 | +0.9893 |
| linear ramp of footprint depth | 8.36 | −8.33 | +0.9954 |
| **modelled exit (autocorrelation)** | **1.82** | **−0.92** | **+0.9990** |

Measured on isolated ray-cast shadows, n=57. The linear ramp is the special case for a rectangle
aligned with the bearing, and it overcorrects for anything else.

## Error budget, by ray-cast ablation

Ray-casting the plate heights through the solved sun geometry gives a perfect shadow mask, and
running the same operator on it separates the error sources. Same footprints throughout.

| what the operator was given | n | MAE | bias | corr |
| --- | --- | --- | --- | --- |
| each building's own shadow, alone in the scene | 57 | 1.82 | −0.92 | +0.999 |
| perfect mask of the whole scene | 57 | 32.18 | +29.40 | +0.806 |
| real imagery index | 57 | 65.33 | +47.25 | +0.332 |

On the ten footprints that both the real index and the perfect mask accept: real 14.22, perfect
4.29.

Read as a budget: the operator itself is essentially exact (1.82 m). **Shadow merging costs about
30 m** — downtown shadows all run the same way, so the ground past one building's tip is usually
already dark from a neighbour, and nothing in a single-building measurement distinguishes the two.
**Imperfect segmentation costs about as much again**, roughly doubling the error on top of that.

So both halves are worth attacking and neither alone gets close. A segmentation model addresses the
second half only; the first needs the buildings solved jointly rather than one at a time.

## Approaches tried and rejected

- **Threshold crossing** with a 25 m local-contrast blur: 9 of 63 measured, corr +0.15. The blur
  window was far smaller than a 226 m shadow, and brightness thresholds are the wrong
  discriminator (see above). Replaced by the step fit, then by the exit fit.
- **Greedy occlusion ordering** — measure tallest first, each building claiming the ground its
  shadow covers so shorter buildings downwind cannot reuse it. Made results worse: 59 to 36
  scorable, MAE 62.5 to 75.5, corr +0.455 to +0.248. Removing claimed ground starves later profiles
  instead of correcting them. The ray-cast ablation says the instinct was right and the mechanism
  wrong: merging really is the dominant error, but it has to be handled by fitting heights jointly,
  not by deleting evidence.
- **Quantile profile instead of mean**, on the theory that undigitised sunlit roofs intrude on the
  swept ground. Worse at q=0.5, 0.6 and 0.75. The lit pixels past the tip are the measurement, not
  contamination.
- **First-local-edge length** instead of the global fit. Fixes short buildings (truth 22 m: 346 m
  estimate becomes 43 m) but destroys tall ones (truth 217 m: 245 m becomes 37 m), because
  undigitised structure produces spurious near edges everywhere.
- **Fitting the acceptance thresholds against the OSM tags** rather than the plate. Picks a
  different rule (`near_level 0.0`) that scores MAE 35.85 against the plate where the plate-fitted
  rule scores 17.48. With the two references disagreeing by 26 m, threshold search is fitting
  reference noise; the thresholds should be justified physically rather than searched.

## Prior implementation

`city2stl/height/providers/shadow_height.py` is deprecated and should stay that
way, but it was deprecated on impression rather than measurement — its eval script
(`tools/ml/eval/eval_shadow_heights.py`) reports component counts with no error metric and no
ground truth. Five of its defects independently prevent the method from working: the sun elevation
is guessed as June/10:00 for every image and latitude (x1.89 height bias at Miami); the azimuth is
never used, length being the bounding-box long side; shadows are never associated with footprints;
components with aspect >8:1 are rejected, which is exactly a tall building's shadow; heights are
clamped to 1-50 m, discarding the tall tail that is the method's only reason to exist.

Worth reusing from it: `_fetch_rgb_for_bbox`, the `HeightResult` / `_cache` / `covers()` plumbing,
and `_shadow_length_to_height`.

## U-Net proof of concept (CPU)

A three-level U-Net, 16 base channels, 118,913 parameters, trained on the Miami scene alone. It
predicts a per-pixel shadow mask, not a per-building height: a dense target avoids the
marginal-mean collapse that sank `train_retna.py`, because the network cannot satisfy a per-pixel
loss by emitting one number. Labels come from ray-casting the plate heights (66 footprints), OSM
tags (6) and a 25 m default (50) through the solved sun geometry. Anything downsun of a defaulted
building is masked out of training, since 41% of the label would otherwise be fiction.

Trains in about 8 minutes on this machine. It works, in the weak sense that it learns something
real, and it loses to the hand-built index.

| index on held-out tiles | IoU |
| --- | --- |
| hand-built darkness x blue ratio | 0.251 |
| **U-Net** | **0.204** |
| predict shadow everywhere | 0.104 |

Downstream is what matters, since the segmenter exists to feed the length operator. On the 13
held-out footprints that are on the plate, the same operator run on three different indices:

| index | rule | n | MAE | bias | corr |
| --- | --- | --- | --- | --- | --- |
| hand-built | none | 13 | 59.63 | +40.44 | -0.192 |
| U-Net | none | 13 | 56.76 | -37.56 | +0.454 |
| ray-cast label | none | 13 | 30.75 | +26.12 | +0.552 |
| hand-built | acceptance | 6 | 14.44 | -10.01 | +0.811 |
| U-Net | acceptance | 5 | 30.00 | -14.64 | +0.855 |
| ray-cast label | acceptance | 11 | 11.19 | +5.72 | +0.948 |

No separation worth claiming at n=13. The U-Net correlates better unfiltered and its bias flips
sign — it under-segments where the hand index over-segments — but it does not produce better
heights, and the acceptance rule keeps fewer of its measurements. The ray-cast row is the ceiling
any segmenter could reach, and the gap to it is large in both cases.

The constraint is label volume, not architecture: 209 training tiles from one city. Nothing about
128 x 128 crops of a single scene supports a stronger conclusion than "a small CNN can be trained
here at all".

### Three bad splits before an honest one

Worth recording because each failure looked like a model result and was not.

1. Split at the 60th percentile of valid columns: the boundary landed east of nearly every tall
   building, the held-out side had no shadow, and every IoU came out near 0.01. The tell was
   `always-shadow IoU 0.000` — a degenerate test set, not a bad model.
2. Split on cumulative shadow mass over the valid region: same outcome. Mass was measured over
   valid pixels, but tiles must also pass the reliability filter, and most of that mass sits in
   tiles later discarded for being downsun of a defaulted building.
3. Split at a quantile of shadowed-tile columns: 79 train tiles against 235 test, 27 shadowed
   against 37. The gap band ran straight through the densest column range.

The fix is to build the eligible tiles first and then scan both axes for the boundary that leaves
the most shadowed tiles on the thinner side. Miami's usable region is wider than it is tall, so the
row axis divides it far better: 209 train / 136 test, 6.6% and 10.4% shadow. Always check the
trivial baseline; if predicting one constant scores zero, the split is broken.

### Retrained on Google 3D Tiles labels

Same network, same split machinery, same scene; the only change is where the label comes from.
Ray-casting the Google DSM instead of the plate heights reverses the verdict.

| index on held-out tiles | plate label | Google DSM label |
| --- | --- | --- |
| hand-built darkness x blue ratio | 0.251 | 0.555 |
| **U-Net** | 0.204 | **0.683** |
| predict shadow everywhere | 0.104 | 0.263 |

656 training tiles against 209, and no reliability mask needed, because no part of the DSM is a
25 m default. 60 epochs, 28 minutes on this machine.

The plate label was never the right target, only the available one. Measured against the observed
imagery, the DSM label agrees at IoU 0.378 and the plate label at 0.127. The plate casts from about
122 digitised footprints; the DSM includes trees and undigitised structure, which average 8.6 m
outside the footprints and cast real shadow that the plate label marks as background. Training
against it penalised the network for finding shadow that is there.

Both columns are honest held-out numbers, but they are not the same test set -- the DSM label
admits far more of the scene -- so the right reading is that the U-Net beats the hand-built index
on the better-specified problem, not that it improved by 0.48 IoU.

### Better segmentation, worse heights

The segmenter exists to feed the length operator, so the number that decides anything is height
error on held-out buildings against the plate. It went the other way.

| index | rule | n | MAE | bias | corr |
| --- | --- | --- | --- | --- | --- |
| hand-built | acceptance | 5 | 14.83 | -9.51 | +0.809 |
| U-Net probability | acceptance | 5 | 82.66 | -76.34 | +0.420 |
| ray-cast DSM label | acceptance | 7 | **12.30** | -9.88 | **+0.982** |

The label row is the best downstream figure recorded anywhere in this work, and it runs through the
same operator with the same thresholds, so the operator is not the problem. Two explanations were
tested and both are wrong:

- **Scale.** The acceptance rule was fitted on the hand-built index, whose values run 0.13 to 0.28,
  while a probability runs 0 to 1. Replacing every index by its percentile rank over the scene puts
  all three on one scale. It does not help: the rule becomes vacuous on the probability (it accepts
  all 12 buildings, MAE 65.32) and the hand-built index gets worse (23.03 against 14.83).
- **Softness.** A probability could terminate the ramp fit early where a hard mask would not.
  Thresholding at 0.30, 0.50, 0.75 and 0.90 makes the U-Net output the same kind of object as the
  label. Every threshold lands in the same place: MAE 61 to 68, bias -37 to -56.

The real cause is the receptive field. Recall against the label, measured on the held-out side by
distance from the nearest structure in the DSM:

| distance | label px | recall | precision |
| --- | --- | --- | --- |
| 0-10 m | 710,556 | 0.800 | 0.845 |
| 20-40 m | 25,750 | 0.519 | 0.975 |
| 40-60 m | 7,597 | 0.350 | 0.993 |
| 80-100 m | 1,864 | 0.321 | 1.000 |
| 100-130 m | 1,744 | **0.000** | 0.000 |

Precision rises to 1.00 as recall falls to 0.00. The network is never wrong about the far end of a
long shadow; it never sees it. Tiles are 128 px at 1 m a pixel and Miami's shadow reach is 292 m,
so a shadow longer than a tile has stretches with no caster anywhere in the crop and nothing local
to key on. Measured length saturates near 50 to 100 m, height saturates near 45 to 90 m, and the
tall towers that matter are 100 to 200 m. That is the -45 m bias, arriving by construction.

The lesson generalises past this network: **a shadow segmenter's receptive field has to span the
shadow reach**, which is `max_height / tan(elevation)` and grows without bound as the sun gets low.
IoU hides this completely, because 96% of shadow pixels sit within 20 m of something standing.

### Fixing it: pixel size as a receptive-field control

The receptive field is fixed in pixels, so coarsening the pixel widens it in metres for free. The
same three-level network was retrained at 2 m and 4 m a pixel, with the ray cast stepping in metres
so the label stays the same physical shadow at every scale.

| | 1 m/px | 2 m/px | 4 m/px |
| --- | --- | --- | --- |
| receptive field | 41 m | 82 m | 164 m |
| tile span | 128 m | 256 m | 512 m |
| train tiles | 1113 | 272 | 30 |
| segmentation IoU | 0.683 | 0.638 | 0.647 |
| recall at 40-60 m | 0.350 | 0.762 | 0.491 |
| recall at 100-130 m | **0.000** | **0.743** | (too few px) |
| height MAE, no rule | 61.90 | **46.07** | 83.31 |
| height bias | -45.73 | **+20.48** | -22.55 |

At 2 m the recall curve is flat across every band out to 130 m; the collapse is gone, not merely
pushed further out. The bias flips sign, which is what a fixed shortfall in measured length looks
like when the shortfall stops happening. Segmentation IoU falls slightly because a 2 m pixel blurs
the shadow edge, confirming again that IoU and height error do not move together.

4 m widens the receptive field further and still scores worse, for a different reason: the pixel is
now comparable to the thing being measured. Only 30 tiles survive at that scale, a footprint spans a
few pixels, and just 6 of the 16 held-out buildings probe at all. Context and measurement pull in
opposite directions on pixel size, and 2 m is where they balance for Miami's geometry.

The remaining gap is no longer segmentation. At 2 m the U-Net's unfiltered height error (46.07)
matches what its own ray-cast label achieves through the same operator (47.60), so the segmenter has
stopped being the bottleneck. The acceptance rule is: it keeps 7 of 9 buildings on the label and
lands at 15.54 m, but accepts all 9 on the U-Net output and so filters nothing. Fitting a rule on
nine buildings is not possible, which is the direct argument for the cross-city setup, where Miami
is never trained on and every scorable footprint in it can be used.

### Depth does not substitute for a wider crop

If the receptive field is the constraint, deepening the network should lift it. A six-level variant
(`DeepUNet`, 1.97 M parameters, nominal field about 390 px) was trained at 1 m a pixel on 256 px
tiles. It produced the best segmentation recorded anywhere in this work and did not fix the heights.

| | 3 levels, 1 m | 6 levels, 1 m | 3 levels, 2 m |
| --- | --- | --- | --- |
| best IoU | 0.683 | **0.695** | 0.638 |
| recall at 40-60 m | 0.350 | 0.529 | 0.762 |
| recall at 100-130 m | 0.000 | **0.000** | 0.743 |
| height MAE, mask, no rule | 61.90 | 60.16 | **46.07** |

The 100 to 130 m band is still exactly zero. Context is bounded by the smaller of the receptive
field and the tile, and the tile is 256 px at 1 m: a pixel in the middle of one has about 128 m of
image on the side the light comes from, so the caster of a 200 m shadow is outside the crop no matter
how deep the network is. Covering Miami's reach at 1 m needs a 512 px tile, which leaves roughly
fifteen usable training tiles in this scene at four times the cost each. Coarsening the pixel buys
the same context for a quarter of the compute, which is why 2 m is the operating point.

One detail from the deep run is worth keeping. Its probability, unfiltered, comes out at bias +0.06
with MAE 55.61: over- and under-measurement cancel almost exactly across the twelve buildings while
each individual estimate stays wrong. Aggregate bias is therefore not evidence that a segmenter is
working, and the spread is what the acceptance rule has to cut.

### Solving geometry in a city with no footprints

Miami solved its shadow bearing against a digitised footprint set. The cities this is meant for have
none, so `city_geom` solves both the bearing and the DSM's orientation from the DSM alone: anything
standing more than 3 m above ground counts as a building, and the vertical flip is whichever of the
two produces a label that agrees better with the dark pixels. That is not circular, because the
bearing is re-solved for each orientation and only the two best labels are compared.

| city | date | bearing | elevation | reach | DSM missing | valid | IoU vs dark |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Rio | 2025-05-25 | 230.0 | 26.2 | 310 px | 1.1% | 78.1% | 0.445 |
| Buenos Aires | 2026-01-20 | 269.0 | 38.5 | 197 px | 1.6% | 84.9% | 0.442 |
| Santiago | 2025-09-11 | 246.0 | 24.9 | 255 px | 1.1% | 80.9% | 0.318 |
| Medellin | 2025-01-29 | 115.0 | **5.1** | 1281 px | **46.8%** | 28.1% | **0.161** |

Medellin is the failure mode to watch for. Google's photorealistic coverage there has a hole across
47% of the tile, and a solve on a holed DSM does not fail loudly: it returns a well-formed bearing
and a sun 5.1 degrees above the horizon, which implies a 1281 px reach and a label that barely
agrees with the imagery. Two floors reject it, `MIN_ELEVATION = 15` and `MIN_IOU = 0.25`, and both
are needed: the elevation alone would pass a subtler failure and the agreement alone is noisy on a
city with genuinely low sun. Missing cells are only filled within two cells of real data, so a
coverage hole is reported as unusable area rather than invented terrain that then casts shadows.

Rio and Santiago carry a 25-degree sun against Miami's 41, which means 300 m shadows in the training
set. That is harder than the test city, not easier, and it is the right direction for the transfer
to work.

### Cross-city transfer, and two corrections it forced

Holding out half of Miami answers a weaker question than the one that matters, because the plan is
to train where Google has buildings and apply where it does not. So the segmenter was retrained on
Rio, Buenos Aires and Santiago at 2 m a pixel, 812 tiles, and tested on the whole of Miami, which
supplied no training data at all. That also raised the scorable footprint count from 16 to 66.

**Segmentation transfer failed.** IoU 0.293 on Miami, against 0.418 for the hand-built index and
0.219 for calling everything shadow. The failure has a shape: recall 0.995 at 80 to 100 m from the
nearest structure with precision 0.022. The network paints shadow nearly everywhere far from
buildings. The cause is the class prior. The three training cities carry 26 to 38 degree suns and
average 47% shadow; Miami's sun is 42 degrees and 21% of it is shadow. Nothing in an RGB tile says
which regime it is in, so the network learned the training mix and applied it.

That prior is not a property of a city, it is a function of sun elevation, and the elevation is
known before inference from the acquisition date. Supplying `tan(elevation)` as a fourth, constant
input channel makes it an input rather than something memorised. This matters beyond transfer:
Cartagena's bbox returns three ESRI scenes with three different dates, so a model that cannot be
told the sun would need retraining per scene.

**The bearing solve is the larger error source.** `solve_shadow_bearing` correlates a building mask
against a dark mask, which implicitly treats every building as the same height. On Miami it returns
5.0 degrees where the footprint-solved answer is 13.0, and that 8 degree error is worth about 30 m
of height error -- more than any segmentation change measured in this work.

| index, all 66 footprints, acceptance rule | at 5.0 deg | at 13.0 deg |
| --- | --- | --- |
| ray-cast DSM label | 68.97 | **39.89** |
| U-Net mask | 47.13 | **40.29** |
| hand-built index | 25.71 | **20.86** |

**The small-sample numbers were flattering.** At the correct bearing the ray-cast label scores
13.97 m on the five southern buildings it accepts, correlation +0.998, and 39.89 m on the 32 it
accepts scene-wide, correlation +0.577. The honest label ceiling is about 40 m, not about 13. Every
figure in the two sections above was computed on 5 to 12 buildings and should be read as an
optimistic subset rather than a result. These are also raw ramp lengths from `shadow_probe`, not the
modelled-exit fit that produced the 17.48 m headline, so they are not comparable to it either.

With both corrections applied, on all 66 footprints:

| index | n | MAE | bias | corr |
| --- | --- | --- | --- | --- |
| hand-built index | 14 | **20.86** | -4.32 | +0.770 |
| U-Net mask | 29 | 40.29 | +8.40 | +0.749 |
| ray-cast DSM label | 32 | 39.89 | +15.88 | +0.577 |

A segmenter scoring IoU 0.293, barely above the trivial baseline, still produces heights that match
what its own ground-truth label produces through the same operator, at nearly double the hand-built
index's coverage. That is the third independent demonstration that IoU does not predict height
error. The hand-built index stays more accurate where it fires, on 21% of buildings against 44%,
which is a coverage-against-accuracy trade rather than a ranking.

## Google 3D Tiles as a label source

Google's Photorealistic 3D Tiles are already a production height provider
(`city2stl/height/providers/google_3d.py`, 1 m DSM from the mesh, 14.4 m MAE over 61 Miami footprints). The
unexploited use is different: ray-casting that DSM through solved sun geometry yields a dense
per-pixel shadow mask with no footprint database and no plate, in every covered city. That is the
direct fix for the label volume above.

Coverage is the limit, and it is not where the mesh fetch succeeds. Buildings are present in Miami,
Rio, Buenos Aires, Santiago and Medellin; Cartagena, Lima, Panama City and Barranquilla return
terrain only; Bogota returns no geometry. Outside the photorealistic cities the same endpoint
serves a global base mesh at fine geometric error, so the fetch looks healthy and the raster is
flat. Test coverage by local relief inside a block-sized window, never by depth of the tile tree.

That split is what makes the idea worth something: train where Google has buildings, apply where it
does not, which is exactly Cartagena.

**Licensing, resolved by the project owner.** [AUDIT-2026-05-17](../history/audits/AUDIT-2026-05-17.md) (line 186 at the time) listed "Why Google 3D
Tiles can't be used (ToS)" as one bullet in a list of things no document explains; the restriction
was written down nowhere in the repository while `google_3d.py` ran in production. The owner has
determined that Google's terms do not bar this use, the work being academic research rather than
commercial. The scope of that determination is worth keeping in view if the project's purpose
changes: it covers non-commercial research use, not redistribution of a derived dataset or a
commercial product built on one.

For 2D imagery the answer is no: the shadow method needs the acquisition date to invert sun
elevation, ESRI's `identify` endpoint returns it per scene, and Google Static satellite does not
publish one. ESRI wins here despite lower resolution.

## Next

- **Joint fit (analysis by synthesis).** Solve all heights at once by ray-casting the current
  estimates and matching the predicted shadow mask to the observed one. This is the direct attack
  on the 30 m merging cost, and the ray-cast machinery for it already exists.
- **Dense labels from Google 3D Tiles.** Ray-cast the 1 m DSM in the covered cities to train the
  segmenter on far more than one scene, then apply it where the mesh has no buildings. In progress.
- Promote to a production provider shadow_fit.py; retire `shadow_height.py` once beaten.
- Track B (Sentinel-2 multi-temporal) on Miami, gate MAE < 30 m.
- Cross-check Track A against Track B on Cartagena, where no ground truth exists.
- Fuse with Open Buildings / Overture. Shadow is strongest exactly where those are weakest (the
  tall tail), so the fusion should be height-dependent.
