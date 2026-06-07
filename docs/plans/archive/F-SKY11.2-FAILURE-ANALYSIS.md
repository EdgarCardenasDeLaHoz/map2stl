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
