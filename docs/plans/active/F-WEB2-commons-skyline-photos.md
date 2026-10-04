# F-WEB2 — Skyline photos from Wikimedia Commons, with a solved camera

**Status**: in progress (user-requested 2026-10-04; Miami first)
**Builds on**: F-WEB1 (`city2stl/skyline/web_image_seed.py`), F-SKYBENCH (the yardstick)

## Goal

Measure building heights from a few good skyline photos per city, taken by people from anywhere
(waterfront, cruise ships, rooftops, stations), not only Street View at street height. Judge them
mainly on **relative** heights (is the taller building taller?), which a printed model needs most.

Why now (2026-10-04):
- The best skyline shots are not Street View, and their cameras are not 2.5 m above the street.
- On the Miami baseline, Street View views order building pairs correctly only 44 % of the time
  (Boston 54 %, Chicago 65 %): no better than chance, inside a single view.
- Google image search is not usable: automated fetching of the results page is against Google's
  terms, and the Custom Search JSON API takes no new customers and closes on 2027-01-01. User:
  "find other solutions, wikimedia we only need a few images per city".
- Commons has the data a camera needs. Miami's skyline categories (9, 312 files): 85 photos carry a
  camera location, 138 a 35 mm-equivalent focal length, 18 a compass direction; 21 are located,
  ≥ 2000 px wide and taken 2019 or later.

## Approach

1. **Relative metrics in the benchmark** (`benchmark.py::score_buildings`): pair order and Spearman
   per view and per city, beside the metre errors. Done first: it is the yardstick for the rest.
2. **Commons finder** (`city2stl/skyline/commons_photos.py`): the city's "skyline" categories →
   files with camera location, EXIF focal length (35 mm equivalent → FOV), compass direction,
   date, size, licence and author. Rank: located, taken after the survey, ≥ 2000 px, daytime,
   compass present. Download the top N (default 5) at ~4000 px into `runs/commons_cache/`.
   Replaces F-WEB1's hand-written `_KNOWN_VIEWPOINTS` guess when a photo has its own location.
3. **Skyline filter**: SegFormer must find sky across the top and buildings rising into it;
   aerial (no sky) and night shots drop out. Reuses `seed_selection.py::_screen_score_from_image`.
4. **Into the pipeline as web seeds**: a `SkylinePoint(source="web")` per photo, pose from EXIF;
   the existing heading sweep / registration refines heading against OSM.
5. **Camera height and tilt fit** per photo: after registration, solve camera height `h` and
   pitch `φ` from anchor buildings (`H = h + d · tan(θ − φ)`; anchors = OSM height tags, or survey
   truth in benchmark cities only for testing). With no anchor, keep relative heights only.

## Target files

- New: `city2stl/skyline/commons_photos.py`, `tests/test_skyline_commons.py`.
- Edited: `benchmark.py` (relative metrics), `web_image_seed.py` (Commons pose when present),
  `_core/height.py` or a new `_core/camera_fit.py` (h, φ fit), `sites/miami.json` (opt-in flag),
  skyline README / STATUS, `docs/INDEX.md`.

## Success criteria

- Miami: ≥ 3 Commons photos pass the filter and register (≥ 10 matched buildings each).
- Per-photo pair order on confirmed buildings ≥ 70 % (Street View baseline: 44 %).
- With anchors, absolute heights on those buildings beat the Street View baseline MAE (102 m).

## Risks

- Phone GPS is ±10–50 m and phone compasses ±10–20°; the registration sweep must absorb both.
- Wide stitched panoramas (one is 12,869 px) are cylindrical, not pinhole; first version skips
  images wider than ~3:1 or treats them separately.
- Elevated cameras (cruise ships, stations) put the horizon well above the skyline base; the
  `h, φ` fit needs ≥ 2 anchors spread in distance, otherwise it is ill-conditioned.
- Licences: Commons files are free-licensed but need attribution; record author + licence per file.
- Same mis-assignment as Street View (a near low building credited with a far tower) can still
  happen; distant telephoto shots should suffer less (towers separate in angle).

## Progress

- 2026-10-04: plan written; Miami Commons survey above.
- 2026-10-04: steps 1-2 done (relative metrics `25f510a`; finder `181cd9e`, filter review
  `2703a0f`). Review of Miami's rejects (user): over-filtering. EXIF-hour "night" was wrong for
  8 of 19 (camera clocks); now judged from sky brightness. Still dropped and wanted: 215
  unlocated photos and 8 wide (> 3:1) skylines.
- 2026-10-04: Commons photos through the existing matcher: of 3 that passed the screen, 1
  registered, 36 % pair order (Street View views 42 %). The matcher, not the photo, is the limit.
- 2026-10-04: camera solver (`camera_solver.py`, `photo_localize.py`, `1ea4663`). Known-answer
  photo: "Common Downtown shot 2011 with buildings tagged" (labels transcribed to
  `sites/annotations/`). With its EXIF focal length (66 mm eq., 30.5 deg) it places the camera
  on Watson Island, 25.78513,-80.17930 +-5 m, heading 215, 3.9 px RMS. Without the focal length
  the position is +-820 m along the line of sight (telephoto: distance and zoom trade off).
- 2026-10-04: heights at those identified towers (roof row at each label tip, distance from
  the solved pose, tilt and camera height fitted): 82 % pair order, leave-one-out MAE 13.5 m
  over 5 confirmed towers (Street View on Miami: 96.6 m). Correct identification is the lever.

## Phase 2 plan: heights from photos, identify then measure (2026-10-04)

User asks: use the unlocated and the wide photos; the labelled photo is valuable.

What the evidence says so far:
- The segment matcher is the limit, not the photos (36 % pair order on a Commons photo).
- With correct identification the same geometry gives 82 % pair order and 13.5 m MAE.
- SIFT recognises only copies of one shot; skyline outline matching recovers a pose from the
  outline alone (known-answer photo: 152 m, heading 3.5 deg off, FOV right).
- Per-tower identification at a found pose is still noisy, so the outline misfit ranks poses.

### Pipeline (one script per city: `scripts/12_photo_heights.py --region miami`)

1. **Collect.** Commons finder, now including unlocated and wide photos; 1600 px; brightness
   filter; SegFormer masks on the GPU (one GPU job at a time, batch 4).
2. **Locate**, by what the photo carries:
   - labels: `photo_localize.solve_from_labels` (known answer);
   - GPS (+ EXIF FOV and compass): `skyline_match.refine` in a ±500 m window around the prior;
   - nothing: `skyline_match.locate` (full search);
   - wide (> 3:1): the same with the cylindrical projection, FOV solved.
   - **Gate:** keep a pose only when its misfit is below tau and beats the runner-up by delta.
     tau and delta are calibrated on the located-photo validation (distance error vs misfit).
3. **Identify.** At the pose, the towers that form the skyline over at least N degrees. Each
   tower's columns are its projected footprint span. A roof is credited only to the tower the
   model puts on top in those columns. This is the change that addresses the Street View
   mis-assignment (+65 to +113 m on untagged buildings).
4. **Measure.** The roof row is the photo skyline's median over the central 60 % of the
   tower's columns (edges are shared with neighbours). Elevation angle from the row; distance
   from the pose to the footprint's nearest point; `H = h_cam + d * tan(e + tilt)`.
5. **Calibrate tilt and camera height** per photo on **anchors**: towers in view with OSM height
   tags (never the benchmark truth). With no anchors, report relative heights only.
6. **Aggregate** across photos: per-building median, spread, number of photos.
7. **Score** with `scripts/10_benchmark.py --report` (write a compatible `heights.json`).
   Report untagged buildings separately: anchors are tagged, so the untagged ones are the honest
   score.

### Work order (parallel where independent)

- A. Located-photo validation (running): distance and heading error vs misfit -> tau, delta.
- B. Speed: the outline model loops over 561 towers per grid cell in Python (~3-5 min per
  photo). Vectorise over towers, then a process pool within the shared CPU budget (<= 4).
- C. `photo_heights.py`: identify + measure + calibrate (steps 3-5), tested on the labelled
  photo (its labels are the identification truth) and a synthetic city.
- D. Wide photos through C (cylindrical columns and rows).
- E. Miami end to end (step 7), then Chicago and Boston (the strongest truth).

- G. **Group photos by viewpoint before locating** (user, 2026-10-04): unlocated photos taken
  from the same spot are located together.
  - Similarity: the skyline outline (shift + zoom search between two photos; robust to year,
    light and camera), SIFT inliers for exact duplicates.
  - A group with a located member (GPS or labels) passes its camera to the rest, each then only
    refined locally (the reliable case: 82 m and 29 m in the EXIF-FOV validation).
  - A group with none gets one joint search: a pose must fit every member, which a sliver of
    skyline cannot fake. One search per group instead of per photo.
  - Members solving to different places flag a bad group or a bad solve.
  - Gallery of groups for user review before anything relies on them.
- Speed idea for later (usage steward, 2026-10-04): the position x FOV search is independent
  FFTs; batch it on the GPU (torch.fft) if profiling shows refinement dominates.

### Success criteria

- Validation: >= 70 % of located photos placed within 300 m and 3 deg of heading.
- Miami photo heights on confirmed untagged buildings: pair order >= 70 % and MAE < 30 m
  (Street View: 42 % and 116 m).
- >= 100 confirmed buildings measured from photos in Miami.

### Risks

- Anchor heights are OSM tags (Miami tags vs truth: 13 m MAE); calibrated heights inherit
  their bias. Relative metrics do not.
- Photo age: towers built after the photo are in OSM but not in the photo (or the reverse).
  Prefer photos from 2015 on; flag older ones.
- Glass tops reflect the sky, so the roof row under-reads (the known glass-tower problem).
- Distance, tilt and camera height trade off; anchors spread in distance are needed.

### Phase 2 progress

- B done (`a8bb933`): outline vectorised, 101.7 -> 3.9 ms per grid cell on Miami.
- C1 done (`photo_heights.py`): towers identified by the outline owner, measured, tilt and
  camera height fitted leave-one-out on the other towers' OSM heights. "Downtown Miami skyline
  May 2011" at its known pose: 19 confirmed towers, 74 % pair order, MAE 29.0 m (bias -9.5 m);
  Street View on Miami: 42 %, 97 m. Misses: two towers read ~68 m vs ~168 m (they own only
  18-24 columns in the model; something nearer, probably untagged, tops them in the photo) and
  Southeast Financial Center 155 vs 235 m (stepped top: the core median lands on a lower step;
  try the peak).
- Gap found: one photo can identify only towers with OSM heights (the outline needs a height to
  know who is on top). **C2: untagged buildings from 2+ located photos** — the building that
  really forms the skyline gives the same implied height from every photo; wrong candidates
  do not. No height guess needed.

### Findings kept from the earlier revised steps

- 5b feature matching (SIFT, 209 unlocated photos vs the labelled one): only copies of the same
  shot match (3,700-4,800 inliers); everything else is 20-66, at chance level. Not pursued;
  a learned matcher (LightGlue) stays an option for later.
