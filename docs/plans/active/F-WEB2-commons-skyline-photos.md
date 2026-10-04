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

## Revised steps (2026-10-04, user: use the unlocated and the wide photos)

5a. **Identify, then measure**: for a photo with a solved pose, measure each identified
    building at its own columns (the projected footprint), instead of the segment matcher.
5b. **Locate unlabelled photos** by feature matching (OpenCV SIFT) against located photos
    (Commons with coordinates, the labelled photo, Street View views), transferring
    identifications through the matches, then `solve_pose`.
5c. **Skyline matching** for photos nothing matches: search position and heading for the
    OSM-predicted tower outline (OSM heights only, never the benchmark truth).
6.  **Wide photos**: cylindrical projection in the solver (done) and in the measurement step;
    FOV solved, not read from EXIF (crops keep the full-frame EXIF).
