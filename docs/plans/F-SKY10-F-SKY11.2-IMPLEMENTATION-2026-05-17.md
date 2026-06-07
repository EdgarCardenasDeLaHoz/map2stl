# F-SKY10 & F-SKY11.2 Implementation Plan (2026-05-17)

## Overview

Complete implementation and integration of two complementary registration features:
- **F-SKY10**: Cross-view non-ML registration (street view ↔ satellite colour/geometry verification)
- **F-SKY11.2**: Pano bird's-eye registration (monocular IPM for heading recovery)

## Current State Assessment

### F-SKY10: Partially Ready

**Implemented** ✅:
- `cross_view.py` — Module with roof colour consistency scorer (Signal 1)
- Integration skeleton in `region_pdf.py` — Satellite image fetch, per-site config gate
- Matcher integration in `pipeline.py` — Cross-view score blending (0.85 intra / 0.15 cross)
- Demo script skeleton — `scripts/10_cross_view_demo.py` (partial)

**Missing** ❌:
- Signal 2 (geometric width consistency) — deferred but easy to add
- Signal 3 (vertical edge consistency) — deferred but medium effort
- Per-site configuration UI — currently gated by `_load_site_use_cross_view_scoring()`  only checks if explicitly set
- Tests — no unit tests for cross_view module or integration
- Demo script completion — visualization of colour/geometry signals
- Documentation — needs section in city2stl/skyline/README.md

**Effort Estimate**:
- Enable F-SKY10 Signal 1 on Cartagena: **1 hour** (set config, test, measure)
- Implement Signal 2: **1 hour** (copy existing width-ratio logic into cross_view.py)
- Implement Signal 3 (Hough lines): **2-3 hours** (new algorithm)
- Complete demo script: **1 hour**
- Add tests: **1.5 hours**
- Documentation: **0.5 hours**
- **Total**: 7-8.5 hours for full implementation

### F-SKY11.2: Attempted but Failed

**Implemented** ✅:
- `pano_birdseye.py` — IPM algorithm + rotation search
- Demo script — `scripts/13_birdseye_registration_demo.py`
- Architecture and theory — well-designed

**Failed** ❌:
- Water signal too weak — monocular SegFormer water mask only labels foreground bay surface
- Insufficient depth reach — SegFormer can't classify water beyond ~5-7m from camera reliably
- 2-D IoU rotation search has no signal — inner 10m disc too small to constrain rotation at bay scale
- Result: No measurable heading recovery; rotation search produces spurious peaks

**Root Cause** (from plan analysis):
> Monocular SegFormer water has very limited "depth reach" (~5-7 m from camera) because at greater distances the water becomes a thin compressed strip the model can't classify confidently. The 2-D IPM-then-IoU rotation registration only has signal in the inner ~10 m disc, which is too small to constrain a rotation against the bay-scale satellite water shape.

**Effort Estimate for Revival**:
- Debug why monocular water signal fails: **0.5 hours**
- Try stereo water detection (would need two panos): **2 hours (infeasible)**
- Fall back to F-SKY11.1 (per-bearing horizon scoring): **Already done, working**
- Abandon F-SKY11.2: **0 hours (mark as failed experiment)**

**Recommendation**: Document as attempted but not viable; continue using F-SKY11.1 (per-bearing approach, which works).

---

## Implementation Plan

### Phase 1: Enable & Test F-SKY10 Signal 1 (2 hours)

**Goal**: Get F-SKY10 working on Cartagena, measure impact

**Tasks**:
1. **Enable per-site config** — Update Cartagena's region config to set `use_cross_view_scoring: true`
   - File: `city2stl/skyline/sites/cartagena.json`
   - Add: `"use_cross_view_scoring": true`

2. **Verify integration** — Run a single-seed report with cross-view enabled
   - Command: `PYTHONPATH=. python city2stl/skyline/scripts/08_region_skyline_pdf.py --region Cartagena --max-seeds 1`
   - Verify: No errors, cross-view scorer builds, PDF includes diagnostic data

3. **Add diagnostic output** — Update region_pdf.py to log cross-view scores
   - Print Signal 1 (colour score) for each matched segment
   - Include in PDF diagnostic pages (optional visual debug coloring)

4. **Measure impact** — Re-run Cartagena seed_5 (the canonical test case)
   - Baseline (no F-SKY10): Current matching results
   - With F-SKY10: Check if any disputed matches are reranked
   - Document: Which matches improved, if any

**Success Criteria**:
- ✅ Script runs without errors
- ✅ Cross-view scorer builds
- ✅ Measured scores are in [0, 1] range
- ✅ At least one match reranked due to F-SKY10

---

### Phase 2: Implement Signals 2 & 3 (3-4 hours)

**Goal**: Add geometric and edge consistency signals to improve F-SKY10

**Signal 2 — Geometric Width Consistency** (1 hour):

```python
def score_geometric_width_consistency(
    sv_image: np.ndarray,
    seg: dict,
    building_polygon_lonlat: list[tuple[float, float]],
    height_estimate_m: float,
    pinhole_intrinsics: dict,
) -> float:
    """Compare expected vs observed facade width.
    
    - Project OSM polygon's 3D corners into Street View
    - Extract expected width at roof-y elevation (existing: _width_ratio_score)
    - Compare to actual segment width
    - Return 1 - |W_obs - W_exp| / W_exp
    """
```

Implementation:
- Extract 3D projection logic from existing `match_segments_to_buildings._width_ratio_score`
- Make it height-aware (currently only uses x-extent, ignoring 3D shape)
- Add to `cross_view.py`; wire into `make_cross_view_scorer()`
- Weight: 0.3 (from plan: 0.5/0.3/0.2 for signals 1/2/3)

**Signal 3 — Vertical Edge Consistency** (2-3 hours):

```python
def score_vertical_edge_consistency(
    sv_image: np.ndarray,
    seg: dict,
    building_polygon_lonlat: list[tuple[float, float]],
    pinhole_intrinsics: dict,
) -> float:
    """Match detected facade edges to projected polygon edges.
    
    - Detect vertical building-edge segments via Hough lines
      (on masked edge image: left/right facade edges)
    - Project OSM polygon's vertical corner edges into Street View
    - For each projected edge, find nearest detected vertical Hough line
    - Score = proportion of projected edges with nearby Hough matches
    """
```

Implementation:
- Use `cv2.HoughLinesP()` on edge-detected masked region
- Match projected edges to Hough lines (within ±3 px tolerance)
- Add to `cross_view.py`; wire into `make_cross_view_scorer()`
- Weight: 0.2 (plan: 0.5/0.3/0.2)

**Update Blending**:
```python
# In make_cross_view_scorer() return dict:
combined = (
    0.5 * color
    + 0.3 * width_consistency
    + 0.2 * edge_consistency
)
```

**Success Criteria**:
- ✅ Both signals implemented and tested
- ✅ Combined score in [0, 1] range
- ✅ Cartagena remeasure shows improvement

---

### Phase 3: Complete Demo Script (1 hour)

**Goal**: Create visualization tool for auditing F-SKY10 decisions

**File**: `scripts/10_cross_view_demo.py` (expand existing skeleton)

**Output**: Multi-page PDF per seed showing:
```
For each matched segment:
  [Street View image with segment outlined]
  [SV roof strip crop | Satellite poly crop]
  [Colour comparison]  [Width comparison]     [Edge comparison]
  [Signals: 0.85, 0.72, 0.61] → [Combined: 0.75]
```

**Implementation**:
- Load the segment, projection, and satellite image
- Call `score_roof_color_consistency()`, `score_geometric_width_consistency()`, `score_vertical_edge_consistency()`
- Render RGB swatches (for colour signal)
- Render width diagrams (expected vs observed)
- Render edge overlays (Hough lines on projections)
- Render per-segment score and combined F-SKY10 contribution

**Success Criteria**:
- ✅ PDF renders without errors
- ✅ Visual diagnostic clearly shows signal contributions
- ✅ Can audit individual matches

---

### Phase 4: Tests & Documentation (2 hours)

**Unit Tests** (1 hour):

```python
# tests/test_skyline_cross_view.py (new file)

def test_median_rgb_valid_patch():
    """Median RGB of valid patch returns float tuple."""
    
def test_median_rgb_empty():
    """Empty patch returns None."""
    
def test_street_view_roof_strip():
    """Roof strip extraction returns correct region."""
    
def test_score_roof_color_consistency_perfect_match():
    """Identical colours → score ≈ 1.0"""
    
def test_score_roof_color_consistency_opposite():
    """Black vs white → score ≈ 0.0"""
    
def test_score_geometric_width_consistency():
    """Correct width → score ≈ 1.0"""
    
def test_score_vertical_edge_consistency():
    """Projected edges matching Hough lines → score ≈ 1.0"""
    
def test_make_cross_view_scorer_integration():
    """Scorer built and called, returns dict with combined score."""
```

**Documentation** (1 hour):

1. **city2stl/skyline/README.md** — Add section:
   ```
   ### F-SKY10: Cross-View Non-ML Registration
   
   Verifies each segment-to-building match by comparing appearance
   in satellite (top-down) vs Street View (side-on) using three
   classical-CV signals:
   
   - **Signal 1** (Roof colour, 50%): Median RGB of roof pixels match
   - **Signal 2** (Geometric width, 30%): Facade width consistent with projection
   - **Signal 3** (Vertical edges, 20%): Facade corners match Hough-detected edges
   
   Combined score blends intra-view (0.85) and cross-view (0.15) to
   rerank candidates when F-SKY2/F-SKY6 produce ambiguous matches.
   ```

2. **Inline code comments** — Document signal calculations

3. **Update F-SKY-INTEGRATION.md** — Mark Signal 1/2/3 status

---

### Phase 5: F-SKY11.2 Documentation (0.5 hours)

**Goal**: Record F-SKY11.2 as attempted but failed experiment

**Updates**:
1. **Update proposals.md** — Mark F-SKY11.2 as "denied" (or "attempted-failed")
   ```
   | F-SKY11.2 | ... | ... | ... | denied |
   ```
   Add note: "IPM monocular water signal insufficient; F-SKY11.1 (per-bearing) preferred."

2. **Create F-SKY11.2-FAILURE-ANALYSIS.md** (new doc):
   ```
   # F-SKY11.2 — Pano Bird's-Eye Registration (Failure Analysis)
   
   ## What Was Tried
   Inverse-perspective-map (IPM) the 360° pano onto a top-down canvas
   and rotate until pano water mask matches satellite water mask via IoU.
   
   ## Why It Failed
   Monocular SegFormer water classification has limited depth reach
   (~5-7m). Beyond that distance, water is a thin horizon strip the
   model can't reliably classify. The bird's-eye IoU rotation search
   only has signal in the inner ~10m disc, too small to disambiguate
   global rotation at bay scale (Cartagena: 1km+ wide).
   
   ## Measurement
   - Cartagena seed_5: No non-zero IoU peak; spurious peaks at ±90°.
   - Pano water coverage: 6% of canvas (inner disc only)
   - Satellite water coverage: 40% of canvas (bay-scale)
   - Signal correlation: Near-zero
   
   ## Recommendation
   Continue with F-SKY11.1 (per-bearing horizon scoring), which works.
   IPM approach viable only with stereo water detection (requires two panos),
   which is infeasible in the current pipeline.
   
   ## Code Status
   - `pano_birdseye.py`: Fully implemented, no errors
   - `scripts/13_birdseye_registration_demo.py`: Works, shows the failure
   - Not integrated into `region_pdf.py` (intentionally; no value)
   ```

3. **Update F-SKY-INTEGRATION.md**:
   - Mark F-SKY11.2 as "attempted but not viable"
   - Link to failure analysis
   - Reaffirm F-SKY11.1 as the preferred approach

---

## Work Order

### Recommended Priority

1. **Phase 1** (2h) — Enable F-SKY10 Signal 1, verify integration, measure baseline
2. **Phase 4 partial** (0.5h) — Add basic unit tests (sanity checks)
3. **Phase 5** (0.5h) — Document F-SKY11.2 failure
4. **Phase 2** (3-4h) — Implement Signals 2 & 3 (if Phase 1 shows promise)
5. **Phase 3** (1h) — Complete demo script
6. **Phase 4 complete** (1.5h) — Comprehensive tests + docs

**Timeline**: 
- **Quick win** (2h): Get F-SKY10 Signal 1 working + basic tests
- **Full implementation** (7-8h): All signals, demo, comprehensive tests

---

## Success Metrics

- [ ] F-SKY10 enabled on Cartagena; cross-view scorer builds without errors
- [ ] Measurable impact: at least 1 disputed match reranked per region
- [ ] All tests pass (663 + new cross-view tests)
- [ ] Demo script renders and shows signal contributions clearly
- [ ] F-SKY11.2 failure documented; proposal marked accordingly
- [ ] Code review complete; documentation updated

---

## Risks & Mitigations

**Risk**: Cross-view scores are noise (colour variations don't correlate with correctness)
- **Mitigation**: Measure on Cartagena seed_5 (known problem cases); if no improvement, signals are unreliable

**Risk**: Signal 3 (Hough edges) is noisy on reflective buildings
- **Mitigation**: Start with Signals 1 & 2 only; add Signal 3 only if it improves metrics

**Risk**: F-SKY11.2 "revival" attempts consume time without success
- **Mitigation**: Skip it; document failure and move on (IPM monocular is fundamentally limited)

---

## Files Modified

### New Files
- `tests/test_skyline_cross_view.py`
- `docs/plans/F-SKY11.2-FAILURE-ANALYSIS.md`

### Updated Files
- `city2stl/skyline/cross_view.py` — Add Signals 2, 3
- `city2stl/skyline/scripts/10_cross_view_demo.py` — Expand visualization
- `city2stl/skyline/sites/cartagena.json` — Enable use_cross_view_scoring
- `city2stl/skyline/README.md` — Document F-SKY10
- `docs/F-SKY-INTEGRATION.md` — Update F-SKY11.2 status
- `docs/proposals.md` — Mark F-SKY11.2 as denied/attempted

### No Changes Needed
- `city2stl/skyline/pipeline.py` — Integration already complete
- `city2stl/skyline/region_pdf.py` — Infrastructure already complete
- `city2stl/skyline/pano_birdseye.py` — Fully implemented (no integration)

---

## Next Steps

Ready to begin Phase 1 (enable F-SKY10 Signal 1). Proceed?
