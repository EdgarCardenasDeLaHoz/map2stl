# F-SKY1 — Floor-strip periodicity for height + distance estimation

Proposal entry: `docs/proposals.md` F-SKY1

## Goal

Use the **horizontal banding** that floors produce on a tall building's
facade as an independent signal for:

1. **Height** — `n_visible_floors × floor_height_m` (typical floor 3.0–3.4 m).
2. **Distance** — once the per-floor pixel period P is known and the focal
   length f is known: `D = f × floor_height_m / P` (the inverse pinhole
   for a known physical size).

The current height-extraction path is purely geometric: it samples a roof y
from the mask/contour and projects it through the pinhole using the OSM
building's footprint distance. When the OSM distance is wrong (cross-bay,
spatial-prefilter cull, OSM data gap) the result is wrong. Floor-period
is an **OSM-free** distance estimate — it depends only on the image, the
focal length, and an assumed floor height. That makes it a useful
cross-check on per-view geometric height, and a fallback when OSM is
unreliable.

## Approach

1. **Pick the building's mask column band.** For each registered building,
   take the SegFormer building-mask columns inside its `[x_left, x_right]`
   projection, restricted to the y-range from the detected roof down to
   the lower frame edge (or `base_y` if available).
2. **Build a 1D row-mean profile.** For each row in that band, average the
   RGB intensity (or a single channel — use luminance) over the columns
   that ARE building. Skip rows where < 30 % of columns are building.
3. **High-pass filter the profile.** Subtract a running mean (window ≈
   12 px) so that low-frequency illumination gradients (the upper part of
   a tower in shadow vs. lit) don't dominate the FFT.
4. **Estimate the period.** Take the autocorrelation of the high-passed
   profile; find the first peak past lag = 4 px and below lag = 80 px.
   The lag of that peak is the floor period P (px). Reject if the peak's
   normalised height (peak / autocorr[0]) is below 0.20 — that's the
   confidence floor; weak periodicity isn't usable.
5. **Convert to height + distance.**
   - `inferred_distance_m  = focal_length_px × floor_height_m / P`
   - `inferred_floors      = (base_y − roof_y) / P`
   - `inferred_height_m    = inferred_floors × floor_height_m`
   `floor_height_m` defaults to 3.2 m (mid of residential / commercial).
6. **Surface as a diagnostic first**, not as a primary signal. Each
   per-view estimate gets optional `floor_period_px`, `floor_confidence`,
   `inferred_distance_m`, `inferred_height_m` fields. The PDF audit page
   prints them next to the geometric values for cross-comparison. After
   one region of validation, decide whether to promote them into the
   aggregate.

## Target files

- `city2stl/skyline_cv/pipeline.py`
  - New `_floor_period_for_building(image, building_mask, x_range, y_top, y_base) → dict | None`
  - Wire into `estimate_heights_from_registration` so each emitted
    `RegisteredBuildingEstimate` carries the optional floor-period fields.
  - Extend `RegisteredBuildingEstimate` with the four new optional fields.
- `city2stl/skyline_cv/region_pdf.py`
  - Render the per-view diagnostic table to surface `inferred_distance_m`
    next to the OSM `forward_m`.
- `city2stl/skyline_cv/README.md` — add a section under "How it works".

## Success criteria

- Function returns a confidence-tagged period for tall (>50 m) buildings
  with clean glass/window grids; returns None on flat or low-rise
  silhouettes.
- For tagged-height Cartagena/Miami buildings where the function fires,
  `inferred_height_m` should be within ±25 % of the tag (validation
  metric, printed at end of run).
- For the same buildings, `inferred_distance_m` should be within ±25 %
  of the OSM `forward_m` (sanity check on the period detection).
- The new fields are visible in the per-view diagnostic table for at
  least one Cartagena seed.

## Known risks

- **Glass-facade buildings without obvious floor grids** (curtain-wall
  spires) will return low confidence and be skipped — that's correct
  behaviour, not a bug.
- **Camera pitch != 0 distorts the floor period** as a function of y.
  At pitch 0 the floor period is approximately constant in y for distant
  buildings; at non-trivial pitch the period grows toward the bottom of
  the image (perspective foreshortening). For the first pass we ignore
  this and rely on the screening logic (only fire when |pitch| < 4°).
- **Mask noise**: SegFormer-b0 building masks can have salt-and-pepper
  pixels that show up as high-frequency noise in the row-mean profile.
  Mitigate with the moving-mean high-pass + a 3-tap median smoothing.

## Out of scope (deferred)

- Promoting floor-period to the aggregate signal.
- Per-region floor-height priors (commercial vs residential).
- Detecting structural breaks (a 30-story tower with a 5-story podium
  has two periods).
