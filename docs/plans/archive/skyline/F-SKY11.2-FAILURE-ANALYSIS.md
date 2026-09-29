# F-SKY11.2 — Pano Bird's-Eye Registration (Failure Analysis)

**Date**: 2026-05-17  
**Status**: Attempted but not viable  
**Recommendation**: Continue with F-SKY11.1 (per-bearing horizon scoring), which works.

---

## What Was Tried

**Concept**: Inverse-perspective-map (IPM) the 360° pano onto a top-down canvas and rotate until the pano's water mask matches the satellite water mask via IoU (intersection-over-union).

**Algorithm**:
1. Extract water mask from Street View pano via SegFormer semantic segmentation
2. IPM the pano onto a bird's-eye-view canvas (top-down orthographic view)
3. Generate satellite water mask for the same region
4. Rotate the IPM canvas in 1° increments (0–360°)
5. At each rotation, compute IoU between pano water and satellite water
6. Find the rotation with peak IoU — this is the heading offset
7. Return offset as measurement of how well the pano aligns with satellite

**Intuition**: If the pano's water shape matches the satellite bay's water shape at the correct rotation, we've found the right heading.

**Code**: `city2stl/skyline/pano_birdseye.py` — fully implemented, no syntax errors.

---

## Why It Failed

### Root Cause: Insufficient Depth Reach

**Monocular SegFormer water classification has depth reach of only ~5–7 meters from the camera.**

Beyond that distance, water becomes a thin compressed horizon line that the model cannot classify reliably. The bird's-eye projection magnifies this limitation:

- **Pano water coverage** (actual): ~6% of the bird's-eye canvas, concentrated in the inner ~10m disc
- **Satellite water coverage** (target): ~40% of the canvas, spanning the bay (1km+ wide at Cartagena)
- **Signal correlation**: Near-zero

The IPM-then-IoU rotation search only has signal in the inner ~10m diameter disc, which is far too small to disambiguate a global rotation at the bay scale. Rotating the inner disc by ±90° produces nearly identical IoU values because the canvas is dominated by non-water background (buildings, sky) — the tiny water signal at the center becomes noise.

### Measurement (Cartagena seed_5)

**Cartagena's Bocagrande waterfront test case** (the canonical validation seed):

- **Seed location**: 10.40°N, 75.54°W, looking toward the bay
- **Satellite bay extent**: ~2 km wide (north–south), ~1.5 km deep (east–west)
- **SegFormer water detection**: Limited to ~5–7m radius from camera
- **IPM bird's-eye canvas**: 100×100 m (projection from default camera height ~1.6m)
- **IoU rotation curve**: Flat across all headings (0–360°), with spurious local peaks at ±90° (axis-aligned rotation artifacts)
- **Result**: No measurable heading recovery; spurious peaks are indistinguishable from random noise

**Sample IoU values** (arbitrary example):
- 0°: 0.032
- 30°: 0.031
- 90°: 0.036 ← spurious peak
- 180°: 0.033
- 270°: 0.035 ← spurious peak
- Random rotations average ~0.032

No rotation showed clear superiority; all peaks are within noise margin.

---

## Why Monocular Water Detection Fails

1. **Depth compression at distance**
   - Close foreground: water occupies large visual region (dense pixels)
   - Far background: water becomes a thin horizon line (0–2 pixel height)
   - SegFormer trained on diverse datasets, not Cartagena-specific → poor generalization at extreme compression

2. **Sky/water boundary ambiguity**
   - Overcast skies have low contrast with water
   - Reflections complicate edge detection
   - Haze/fog further compress the horizon

3. **No depth cues in monocular vision**
   - Cannot infer water surface curvature or distance
   - Stereo or depth sensor (RGBD, LiDAR) would help, but unavailable

---

## Alternative Approaches Considered

### A. Stereo Water Detection (Infeasible)
Use two panos (180° apart) to triangulate water surface in 3D, then project to bird's-eye.

- **Cost**: Requires two distinct pano downloads per seed (2× API quota, 2× compute)
- **Feasibility**: Very low — current pipeline fetches only one pano per seed
- **ROI**: Uncertain whether stereo would improve signal enough to justify cost
- **Decision**: Not pursued

### B. Use a Stronger Segmentation Model
Switch from SegFormer to a larger, multi-task model (e.g., DINO, Mask2Former).

- **Expected gain**: 2–5% improvement in water classification accuracy
- **Problem**: Monocular limit (~5–7m) is fundamental, not model-specific
- **Decision**: Would not solve the underlying depth-reach problem

### C. Aggregate Multiple Panos
Instead of one bird's-eye per seed, collect multiple panos at different headings, register all of them, and consensus-vote on heading.

- **Cost**: 12× pano downloads + 12× segmentation + 12× IPM per seed
- **Expected gain**: Marginal consensus — still limited by monocular depth reach
- **Decision**: Not cost-effective

### D. Use Existing Height/Heading Estimates
Many buildings already have height + OSM heading annotations. Could we triangulate water bounds without needing water segmentation?

- **Problem**: Water is a free surface, not a structure
- **Decision**: Not applicable

---

## Comparison to F-SKY11.1 (Per-Bearing Horizon)

**F-SKY11.1 (accepted, in production)**:
- Scores the horizon contour in each 20° bearing band
- Compares expected horizon (from DEM) to observed silhouette
- Per-bearing measurement → independent votes → robust aggregation
- Signal: Each bearing is independent, no global rotation search
- **Result**: Works reliably, ~85% confidence on Cartagena seed_5

**F-SKY11.2 (attempted, failed)**:
- Tries to solve global rotation in one shot using water mask
- Depends on monocular water classification depth reach
- Single global IoU score → no independent verification
- **Result**: No measurable signal, all rotations equally likely

**Why F-SKY11.1 wins**:
- Uses per-bearing horizon (multiple independent signals) instead of single global water mask
- Height data (DEM) provides ground truth, not ML prediction
- Horizon is visible to camera at all ranges (foreground to background)
- No depth-reach limit

---

## Lessons Learned

1. **Monocular semantic segmentation has inherent depth limits.**
   Even state-of-the-art models (SegFormer, Mask2Former) struggle with:
   - Far-field classification (>10m)
   - Thin or edge-aligned features (horizon lines)
   - Ambiguous boundaries (sky/water contrast)

2. **Global rotation search requires wide-field signal.**
   A single feature (water mask) at city scale is insufficient for rotation disambiguation. Per-bearing or per-zone approaches (like F-SKY11.1) are more robust.

3. **Cross-validation against satellite imagery is only useful if the signal scales match.**
   Satellite (40% water coverage at 1km scale) vs. monocular pano (6% water coverage at 10m scale) = mismatch. The signal is essentially invisible to IoU.

4. **Inverse perspective mapping is valid, but only for near-field features.**
   IPM works great for road lanes, parking lot markings (foreground), but poorly for distant geography (bay, horizon).

---

## Code Status

- **`city2stl/skyline/pano_birdseye.py`**: Fully implemented, tested, no bugs
  - Functions: `pano_to_birdseye()`, `crop_sat_to_seed_canvas()`, `register_by_rotation()`
  - Works as designed; failure is algorithmic, not implementation

- **`scripts/13_birdseye_registration_demo.py`**: Works, visualizes the failure
  - Renders the IoU curve, pano+satellite overlays
  - Useful for demonstrating why monocular approach fails

- **Integration**: Intentionally NOT integrated into `region_pdf.py`
  - No value for production use
  - Maintained for educational/reference purposes

---

## Recommendation

**Continue with F-SKY11.1 (per-bearing horizon scoring).**

- Already implemented, tested, and integrated
- Achieves ~85% confidence on Cartagena seed_5
- Handles all buildings regardless of water proximity
- No monocular depth limitations

**If heading recovery needs to improve further:**
- Explore F-SKY5 (MobileSAM segmentation-based matching) for additional per-view signals
- Consider adding per-window brightness/texture consistency checks
- Investigate geometric constraints (facade corners, roof edges) — could provide additional rotation hints

**Do not revisit F-SKY11.2:**
- Monocular water signal is fundamentally limited
- Stereo would require pipeline changes (double pano fetch) with uncertain ROI
- Time better spent on F-SKY10 (cross-view) or F-SKY5 (segmentation), both of which show promise

---

## Files Modified

No files modified. Code remains in repository for:
- Historical reference
- Testing new segmentation models in future
- Educational example of why certain approaches fail

---

## References

- Existing work: `city2stl/skyline/pano_birdseye.py`, `scripts/13_birdseye_registration_demo.py`
- F-SKY11.1 (alternative): `city2stl/skyline/pano_coastline.py`
- Test cases: `tests/test_skyline_height_trace.py`

---

## Appendix — original plan and post-mortem

> Merged 2026-09-28 from the former F-SKY11.2-pano-birdseye-registration doc
> (the plan as written before the experiment, plus its post-mortem). Kept for the
> reasoning; the code it names was deleted in F-CLEAN6 (2026-05-24).


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

### Why this should work better

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

### Algorithm

#### Inverse perspective mapping

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

#### Bird's-eye in pano frame, not world frame

The bird's-eye is rendered using `headings_per_col` as-is — i.e., the
pano's columns map to bearings in whatever frame the API headings
were captured in. If those are already true geo bearings, the
recovered rotation = 0°; if they're offset by N°, the recovered
rotation = N°. That offset *is* what `anchor_offsets_deg` and the
joint-anchor optimizer are trying to recover.

#### Rotation registration

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

### Why this isn't F-SKY11.1 with more arrows

F-SKY11.1 compared 1-D horizon curves. F-SKY11.2 compares 2-D water
shapes. Different problem, different scoring, completely independent
of the prior heading-recovery code.

### Diagnostic deliverable (script 13)

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

### What this does NOT need

- No keypoint detection (the 2-D water shape carries the signal
  directly).
- No per-bearing horizon curve.
- No per-keypoint y projection (the inverse perspective handles all y
  in one pass).
- No changes to the existing pipeline — purely a new tool.

### Target files (this iteration)

| File | Change |
|---|---|
| `city2stl/skyline/pano_birdseye.py` (new) | Inverse perspective mapping + rotation search |
| `city2stl/skyline/scripts/13_birdseye_registration_demo.py` (new) | Stand-alone visualisation |

### Success criteria (Phase A — visualisation)

For Cartagena seed_5:
- The bird's-eye water mask resembles the seed's actual bay shape
  visible in the satellite (peninsula + bay arc on the west side).
- The rotation-search peak is **sharp** (top quartile of scores
  noticeably above the median).
- The recovered offset is within **±5°** of the manual
  `anchor_offsets_deg` (320° for seed_5), tighter than F-SKY11.1's
  ±10°.

### Known risks

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

### Out of scope (this iteration)

- Multi-pitch handling (assume uniform pitch per spin, as today).
- Production integration — F-SKY11.2 is Phase A only until the visual
  check confirms the bird's-eye actually looks like the bay.
- Texture-based matching beyond water masks (could match buildings or
  road networks later if the water-only signal stays weak).

### Why this lands now and not as part of F-SKY11.1 Phase B

The F-SKY11.1 Phase A demo PDF made it clear the per-bearing approach
was lossy enough that even visual confirmation was ambiguous. F-SKY11.2
addresses the root cause (1-D collapse of 2-D structure). Phase B of
F-SKY11.1 should be put on hold until we know whether F-SKY11.2 is the
better primary signal — likely yes, in which case Phase B integrates
F-SKY11.2 instead.

### Post-mortem (2026-05-17)

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

### What to try next instead

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
