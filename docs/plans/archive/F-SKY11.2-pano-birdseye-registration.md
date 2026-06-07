# F-SKY11.2 — Pano → bird's-eye → 2-D rotation registration

Proposal entry: `docs/proposals.md` F-SKY11.2 (to add)

Status: **EXPERIMENT FAILED — recorded for posterity (2026-05-17)**.
Phase A implemented end-to-end (module + script + IoU rotation search)
and run on Cartagena seed_5. Several rounds of bug fixes
(satellite-crop unit conversion, IPM gap-filling via reverse
projection, focal_y correction from 75° vertical to the actual
~66°, camera_h sweeps from 1.7→6m) couldn't get the bird's-eye
water mask above ~6 % of canvas coverage, and even that small disc
didn't yield a non-zero IoU peak.

**Root cause**: monocular IPM of the water mask only labels pixels
where SegFormer reliably classifies water — which is the foreground
bay surface (rows ~350+ in a Cartagena pano). The vast majority of
canvas distances (5m to 500m) map to pano rows just below the horizon
(rows 225-300), where the camera is actually looking at the FAR
SHORE buildings of Bocagrande or at sky/haze. SegFormer correctly
labels those as building/sky, not water — but my IPM still puts those
"distant water" rays in the bird's-eye, where they correctly read False
because there's no water at the sampled pixel.

In other words: monocular SegFormer water has very limited "depth
reach" (~5-7 m from camera) because at greater distances the water
becomes a thin compressed strip the model can't classify confidently.
The 2-D IPM-then-IoU rotation registration **only has signal in the
inner ~10 m disc**, which is too small to constrain a rotation against
the bay-scale satellite water shape.

This iteration projects the pano back into a bird's-eye view of the
ground around the seed using inverse perspective mapping, then matches
that 2-D water shape against the (already top-down) satellite water
mask via a 1-D rotation search. Same projection, comparable directly.

## Why this should work better

1. **Same projection.** Both views are now top-down in metres around
   the seed. Registration becomes "rotate one image until its water
   shape matches the other", which is a well-defined 1-D problem
   instead of a per-column heuristic.
2. **Radial structure preserved.** Distance from camera to a water
   pixel maps 1-to-1 to bird's-eye distance from seed. The shape of
   bays, capes, and waterfront curvature carries through — those are
   the features that disambiguate "looking across the bay" from
   "looking down the bay".
3. **2-D IoU is naturally constrained.** Random rotations score
   poorly (water pixels land in non-water sat regions); the correct
   rotation scores high (water overlaps water). Wrong-by-180° is
   penalised hard because the satellite is asymmetric.

## Algorithm

### Inverse perspective mapping

Each pano pixel `(c, y)` below the horizon represents a sea-level
ground point at:

    elev_below_horizon = atan((y - horizon_y) / focal_y)
    ground_distance_m  = camera_h_m / tan(elev_below_horizon)
    bearing_pano_rad   = radians(headings_per_col[c])
    dx_m = ground_distance_m * sin(bearing_pano_rad)
    dy_m = ground_distance_m * cos(bearing_pano_rad)    # north

Rendered into a top-down canvas `(2R/m_per_px + 1)` square centred on
the seed at `R` metres radius, `m_per_px` ground resolution.

Notes:
- `horizon_y = H/2 - tan(pitch_rad) * focal_y` from the spin's effective pitch
- `focal_y = H / (2 tan(75°/2))` — same focal a 75° FOV view uses
- Excluding `elev_below_horizon < 0.5°` keeps the near-horizon pixels
  (where 1 row of pano = hundreds of m of ground) from saturating
  the canvas with junk
- Excluding `ground_distance > 1.5 R` keeps the far horizon pixels
  from being written outside the canvas

### Bird's-eye in pano frame, not world frame

The bird's-eye is rendered using `headings_per_col` as-is — i.e., the
pano's columns map to bearings in whatever frame the API headings
were captured in. If those are already true geo bearings, the
recovered rotation = 0°; if they're offset by N°, the recovered
rotation = N°. That offset *is* what `anchor_offsets_deg` and the
joint-anchor optimizer are trying to recover.

### Rotation registration

```
crop the satellite water mask to the same canvas: ±R metres around
the seed, resampled to m_per_px metres per pixel.

for each candidate rotation θ in [0, 360°) step 1°:
    rotate the birdseye water mask by -θ around the seed (canvas centre)
    score(θ) = IoU(rotated_birdseye, sat_birdseye)
             restricted to the union of both views' "valid" regions
             (excluding canvas pixels neither view sampled)

best_offset_deg = argmax(score)
```

Optional ±5° refine at 0.1°.

## Why this isn't F-SKY11.1 with more arrows

F-SKY11.1 compared 1-D horizon curves. F-SKY11.2 compares 2-D water
shapes. Different problem, different scoring, completely independent
of the prior heading-recovery code.

## Diagnostic deliverable (script 13)

`city2stl/skyline/scripts/13_birdseye_registration_demo.py` — 4 pages:

1. **Satellite reference**: the satellite + cyan water mask + seed.
2. **Bird's-eye rendering**: the pano-derived bird's-eye water mask
   alone, with sample-coverage shown. User can see how much of the
   canvas got filled and where the coverage thins out.
3. **Side-by-side at recovered rotation**: satellite water mask + the
   rotated bird's-eye water mask overlaid, plus the IoU value and the
   recovered offset.
4. **Rotation-search score curve**: IoU vs θ over [0, 360°), with the
   recovered peak marked. Sharp peak = confident; flat / multi-peak
   = weak signal (inland or low water coverage).

## What this does NOT need

- No keypoint detection (the 2-D water shape carries the signal
  directly).
- No per-bearing horizon curve.
- No per-keypoint y projection (the inverse perspective handles all y
  in one pass).
- No changes to the existing pipeline — purely a new tool.

## Target files (this iteration)

| File | Change |
|---|---|
| `city2stl/skyline/pano_birdseye.py` (new) | Inverse perspective mapping + rotation search |
| `city2stl/skyline/scripts/13_birdseye_registration_demo.py` (new) | Stand-alone visualisation |

## Success criteria (Phase A — visualisation)

For Cartagena seed_5:
- The bird's-eye water mask resembles the seed's actual bay shape
  visible in the satellite (peninsula + bay arc on the west side).
- The rotation-search peak is **sharp** (top quartile of scores
  noticeably above the median).
- The recovered offset is within **±5°** of the manual
  `anchor_offsets_deg` (320° for seed_5), tighter than F-SKY11.1's
  ±10°.

## Known risks

1. **Camera not exactly 1.7 m above sea level.** On a peninsula like
   Castillo Grande the local ground may be 3-10 m above sea level,
   so `camera_h = 1.7` is wrong by that local offset. Effect: a
   scaling error in `ground_distance` that distorts the bird's-eye
   radially. Mitigation: try `camera_h` ∈ {1.7, 5, 8, 12} and pick
   the value with the highest peak score.
2. **Buildings violate the ground-plane assumption.** A pano pixel
   that's actually a 50 m building façade gets back-projected as a
   ground point 30 m away (way too close). This puts spurious
   "non-water" content in the bird's-eye where the satellite shows
   water past the building. Mitigation: use the water mask only —
   building-misclassified-as-ground pixels stay False, contribute
   nothing to the water IoU.
3. **Pano stitching seams** introduce vertical lines in the bird's-eye
   as radial rays. Cosmetic, doesn't affect rotation-IoU significantly.
4. **Near-camera pixels dominate the canvas.** A column of 200 pano
   pixels at distance 30 m maps to 200 canvas samples in a tight
   radial spread, while distance 300 m maps to 5 samples in a wide
   spread. The natural canvas averaging handles this, but the
   bird's-eye is densest near the seed and sparser at the rim.
   Mitigation: weight the IoU by `valid_mask` so unsampled regions
   don't count.

## Out of scope (this iteration)

- Multi-pitch handling (assume uniform pitch per spin, as today).
- Production integration — F-SKY11.2 is Phase A only until the visual
  check confirms the bird's-eye actually looks like the bay.
- Texture-based matching beyond water masks (could match buildings or
  road networks later if the water-only signal stays weak).

## Why this lands now and not as part of F-SKY11.1 Phase B

The F-SKY11.1 Phase A demo PDF made it clear the per-bearing approach
was lossy enough that even visual confirmation was ambiguous. F-SKY11.2
addresses the root cause (1-D collapse of 2-D structure). Phase B of
F-SKY11.1 should be put on hold until we know whether F-SKY11.2 is the
better primary signal — likely yes, in which case Phase B integrates
F-SKY11.2 instead.

## Post-mortem (2026-05-17)

What we learned that wasn't obvious from the plan:

1. **Monocular depth via IPM has a hard reach limit set by the mask
   model's per-class confidence at compressed-near-horizon pixels.**
   SegFormer's water class is high-recall at the textured foreground
   (rows 350-540 of a 540-tall pano) and zero-recall at the horizon
   strip (rows 220-300) where distant water visually blends with
   buildings + sky. The IPM math correctly maps canvas distances to
   pano rows but the resulting samples come back False at most
   distances because there's nothing to classify.
2. **Camera height above sea level is genuinely uncertain.** For
   seed_5 on Castillo Grande, the local ground is ~3-5 m above sea
   level, so camera-above-sea is ~5-7 m. The plan called this out as
   a risk; testing showed even at camera_h = 5 m the IPM still
   couldn't capture water past ~10 m.
3. **The 2 D bird's-eye view DID render correctly** — page 2 of the
   demo PDF shows a reasonable polar-fan of the immediate camera
   surroundings. The math is right; the labels are wrong (or
   absent).
4. **F-SKY11.1 (1 D per-bearing) is actually less affected by this
   problem** because it uses the SV's per-column "water at the
   bottom of the frame" coverage, which IS reliably classified
   (the foreground IS textured water at the bottom of the pano).
   F-SKY11.1's accuracy was ±10° on Cartagena seed_5 — useful, not
   precise.

## What to try next instead

Two paths neither of which requires a depth signal at distance:

A. **Skyline-profile registration.** For each pano column, compute the
   y of the topmost non-sky pixel — the actual silhouette of land /
   buildings against the sky. Match against a satellite-derived
   expected skyline profile (use OSM/MS Buildings polygons + heights
   to predict the y at each bearing for the seed). This shifts the
   matching feature from "water below" (which IPM can't reach
   distantly) to "land above" (the visible far-shore boundary), which
   the model classifies well.

B. **Hybrid F-SKY11.1 + joint anchor optimizer.** Use F-SKY11.1's
   per-bearing recovery to seed the joint-anchor IoU optimizer's
   centre, restricting its ±8° fine sweep to the F-SKY11.1 peak. This
   doesn't try to be a standalone solution — it just gives the
   existing optimizer a better starting point on water-adjacent
   seeds. Cheap to ship, low regression risk.

Path B is the more practical near-term. Path A is the more correct
long-term answer but is a meaningful new module + plan.

F-SKY11.2 stays disabled (the script and module remain on disk for
reference, the consolidation plan no longer marks it as a candidate
for production integration).
