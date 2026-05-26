# F-SKY Series — Integration Status & Consolidation

**Last Updated**: 2026-05-26  
**Author**: Claude (consolidation pass)  
**Purpose**: Single source of truth for F-SKY feature activation, status, and pipeline integration

---

## Executive Summary

The F-SKY series is a set of 11+ computer-vision improvements to the skyline_cv height estimation pipeline. This document consolidates their status, active/disabled state, measurement results, and integration roadmap.

**Current production**: `retna_pruned.pt` (0.2691 loss, 3.82m MAE)  
**Target improvement**: Cartagena building height accuracy via cross-view registration

**Active features**: F-SKY2, F-SKY4, F-SKY6, F-SKY7, F-SKY8, F-SKY10 (opt-in, diagnostic-only), F-SKY11.1, F-SKY12 (Phase A verifier, done 2026-05-26), F-SKY13 (Phases A/A.2/B/C all done 2026-05-26), F-SKY15 (HTML report, done 2026-05-26)
**Default-off / diagnostic**: F-SKY1 (gated behind compute_floor_period=False as of 2026-05-18; fields are computed only when explicitly requested for diagnostics)
**Removed**: F-SKY3 (measured regression; function deleted 2026-05-18 via F-CLEAN2), F-SKY11.2 (IPM bird's-eye dead-end; code deleted 2026-05-24 via F-CLEAN6)
**Pending**: F-SKY5 (MobileSAM instance head — gating decision pending), F-SKY14 (trained satellite coastline detector)

---

## Pipeline Architecture

The skyline_cv pipeline lives in two core files plus thin helpers:

```
city2stl/skyline_cv/
├── pipeline.py          (~3370 lines, pure math, unit-tested)
├── region_pdf.py        (~3900 lines, orchestration + I/O + PDF rendering)
├── scripts/
│   ├── 08_region_skyline_pdf.py  (production entry point)
│   ├── 09_height_trace.py        (F-SKY1 diagnostic)
│   └── 13_heading_recovery_demo.py  (multi-channel heading research)
└── [helper modules]
    ├── height_trace.py            (F-SKY1)
    ├── height_trace_render.py     (F-SKY1 PDF)
    ├── satellite_footprints.py    (F-SKY8)
    ├── satellite_image.py         (F-SKY8 + cross-view)
    ├── cross_view.py              (F-SKY10)
    ├── coastline_registration.py  (F-SKY11, F-SKY11.1, F-SKY13)
    ├── osm_water.py               (F-SKY13 — OSM coastline fetch + keypoints)
    ├── depth_estimation.py        (F-SKY12 — Depth Anything V2 verifier)
    └── html_report.py             (F-SKY15 — HTML diagnostic report)
```

Deleted: ``pano_birdseye.py`` + script 13 (F-CLEAN6, 2026-05-24), ``config.py``
(F-CLEAN1, 2026-05-24). Post-mortem for F-SKY11.2 in ``docs/plans/archive/``.

### Core Flow (region_pdf.py, simplified)

```python
def generate_region_report(region_name):
    # Load region + OSM buildings
    bbox, buildings = load_region(region_name)
    
    # Propose seed candidates (auto or manual)
    seed_candidates = propose_seeds(bbox, buildings, water_bodies)
    
    # Screen candidates with Street View probes
    viable_seeds = screen_seeds(seed_candidates)
    
    # For each seed: register + extract heights
    for seed in viable_seeds:
        pano = fetch_pano(seed)
        views = spin_views(pano, 12)  # 12 × 30° views
        
        # Segment each view
        masks = [segformer_mask(view) for view in views]
        
        # F-SKY1: Optional floor periodicity detection
        if config.F_SKY1_ENABLED:
            floor_heights = detect_floor_periodicity(masks[0])
        
        # Joint heading recovery + anchor optimization
        anchor = optimize_heading_across_views(masks, buildings)
        
        # Per-view registration + height extraction
        for view, mask in zip(views, masks):
            segments = split_silhouettes(mask)
            
            # F-SKY2: Re-split merged buildings using OSM boundaries
            if config.F_SKY2_ENABLED:
                segments = osm_anchor_silhouettes(segments, buildings)
            
            # F-SKY4: Render segmentation overlay (diagnostic)
            if config.F_SKY4_ENABLED:
                render_mask_overlay(view, mask, page)
            
            # Match segments to buildings
            matches = match_segments_to_buildings(segments, buildings)
            
            # F-SKY6: Optional 1:1 constraint + Hungarian assignment
            if config.F_SKY6_ENABLED:
                matches = hungarian_one_to_one_assignment(matches)
            
            # Extract heights from perspective
            heights = extract_heights(view, matches, intrinsics, anchor)
    
    # Aggregate heights per building
    aggregated_heights = aggregate_heights(all_heights)
    
    # F-SKY11.1: Optional pano-level coastline alignment correction
    if config.F_SKY11_1_ENABLED:
        heading_offset = recover_pano_heading_offset(masks, water_bodies)
        aggregated_heights = apply_heading_correction(aggregated_heights, heading_offset)
    
    # Render PDF report
    generate_pdf(bbox, buildings, aggregated_heights, page)
```

---

## F-SKY Feature Status & Integration

### 🟢 Active (Committed to Pipeline)

#### **F-SKY1: Floor-Strip Periodicity Detection**
- **Status**: Implemented, integrated
- **Location**: `pipeline.py:detect_floor_periodicity()`, `height_trace.py`
- **Purpose**: Detect horizontal banding in building masks → estimate floor count → independent height validation
- **Activation**: Gated by `config.F_SKY1_ENABLED`
- **Current use**: Diagnostic; not yet feeding into main height calculation
- **Last commit**: Part of 2026-05-17 commit (height_trace.py + test_skyline_cv_height_trace.py)

#### **F-SKY2: OSM-Anchored Silhouette Splitting**
- **Status**: Implemented, measured ✅
- **Location**: `pipeline.py:osm_anchor_silhouettes()`
- **Purpose**: Re-split merged buildings using OSM boundary priors
- **How it works**: Detect minimum coverage inside OSM-projected gaps, snap split positions to actual visible separators
- **Activation**: Gated by `config.F_SKY2_ENABLED`
- **Measurement**: Cartagena seed_5 shows improvement (was failing with merged towers, now splits correctly)
- **Integration**: Core part of matcher pipeline; enabled by default

#### **F-SKY4: SegFormer Mask Overlay**
- **Status**: Implemented, diagnostic-only
- **Location**: `region_pdf.py:render_mask_overlay()`
- **Purpose**: Semi-transparent cyan overlay of SegFormer mask on Street View image
- **Use case**: Visual verification — helps separate "model failed" from "matcher failed"
- **Activation**: Gated by `config.F_SKY4_ENABLED`
- **Impact**: No algorithmic change; pure rendering for human inspection

#### **F-SKY6: One-to-One Segment-to-Building Assignment**
- **Status**: Implemented, integrated
- **Location**: `pipeline.py:hungarian_one_to_one_assignment()`
- **Purpose**: Enforce global 1:1 constraint via Hungarian algorithm
- **Problem solved**: Prevents two adjacent segments from claiming the same OSM building
- **Activation**: Gated by `config.F_SKY6_ENABLED`
- **Impact**: Cartagena seed_5 page 31: segments no longer duplicate assignments
- **Integration**: Part of matcher; enabled by default

#### **F-SKY7: Local-Maxima Peak Detection**
- **Status**: Implemented, integrated
- **Location**: `pipeline.py:detect_local_maxima_peaks()`, `region_pdf.py` page layout refactor
- **Purpose**: Segment contours with no sky-valley breaks (monotone bumpy rooflines)
- **Method**: Local-maxima pass on contour relative to smoothed baseline
- **Use case**: Glass towers with no sky gaps (Cartagena seed_5 page 31: 34°/259px gap had invisible towers)
- **Activation**: Gated by `config.F_SKY7_ENABLED`
- **Integration**: Core segmentation pipeline; enabled by default
- **Also**: Per-view PDF page refactor (separate SegFormer mask plot underneath image, removed legend table)

#### **F-SKY8: Satellite-Derived Building Footprints**
- **Status**: Implemented, integrated ✅
- **Location**: `satellite_footprints.py`, `region_pdf.py`
- **Data source**: Microsoft Global ML Building Footprints (open data, ODbL)
- **Purpose**: Second polygon source where OSM is sparse
- **Use case**: Cartagena Bocagrande waterfront (towers visible in SegFormer, OSM has no polygons)
- **Integration**: De-duplicated against OSM, merged into single building set
- **Impact**: Enables matcher to assign previously-unmatchable segments
- **Last commit**: Part of 2026-05-17 commit (satellite_footprints.py)

#### **F-SKY11.1: Pano-Level Coastline Alignment (Phase A)**
- **Status**: Phase A complete ✅, Phase B pending
- **Location**: `coastline_registration.py`, demo script `scripts/12_pano_coastline_demo.py`
- **Purpose**: Single global heading-offset recovery from stitched 360° pano + water mask
- **Method**: Water-distance radial signatures + numbered coastline keypoints + multi-view sweep
- **Previous approach**: F-SKY11 (12 independent per-view best-heading searches)
- **Improvement**: One offset solve using all 24 keypoints simultaneously
- **Measurement**: Cartagena seed_5 recovers 310° vs manual 320° (within 10° tolerance)
- **Phase A**: Scorer + demo script (diagnostic-only)
- **Phase B**: Integration into region_pdf.py main pipeline (pending)
- **Activation**: Gated by `config.F_SKY11_1_ENABLED`
- **Last commit**: Part of 2026-05-17 commit

---

### 🟡 Disabled (Measured Regression, Remains Available)

#### **F-SKY3: OSM-Marker Column Voronoi**
- **Status**: Removed ❌ (2026-05-18, F-CLEAN2)
- **Location**: Function `osm_marker_voronoi_silhouettes` deleted from `pipeline.py`; plan preserved at `docs/plans/F-SKY3-osm-marker-instances.md`
- **Purpose**: Use Voronoi over OSM marker x_px to split merged masks
- **Why removed**: Measured regression on Cartagena
  - MAE: 17.28m → 22.13m (↑ 4.85m)
  - Tagged count: 13 → 8 buildings (↓ 5)
  - Unconditional splitting was too aggressive
- **Replacement direction**: Dedicated instance-segmentation model (MobileSAM or TinySAM) — see F-SKY5

---

### 🟡 Landed / Diagnostic-Only

#### **F-SKY10: Non-ML Cross-View Registration**
- **Status**: Landed, demoted to diagnostic-only
- **Location**: `city2stl/skyline_cv/cross_view.py`; opt-in via `use_cross_view_scoring`
- **What landed**: All 3 signals (colour/width/edges) implemented; blended into matcher at 0.85/0.15 weights; `cv̄=X.XX/min=Y.YY` shown in per-view PDF header (F-CLEAN5, 2026-05-24)
- **Why demoted**: Per-building colour was empirically fragile; F-SKY11.1 supersedes the cross-view registration intent. The scorer stays on disk and is wired behind the opt-in flag; it does NOT drive production heights.
- **Phase B** (production reranking): still pending — measure first whether F-SKY2/6/8 coverage is sufficient

#### **F-SKY12: Depth Anything V2 on Street View Panos**
- **Status**: Phase A landed (verifier only)
- **Location**: `city2stl/skyline_cv/depth_estimation.py`; emits `depth_height_m` + `depth_disagreement` per match
- **Activation**: Does NOT influence aggregated heights; Phase B (confidence weighting/rescue) pending
- **Plan**: `docs/plans/F-SKY12-depth-from-panos.md`

#### **F-SKY13: OSM-Coastline Registration + Footprints Overlay**
- **Status**: Phases A, A.2, B all landed; Phase C in progress
- **Location**: `city2stl/skyline_cv/osm_water.py`, `coastline_registration.py`, `region_pdf.py`
- **Landed**: OSM fetch + 1 km clip + keypoints (`osm_water.py`); minimap OSM coastline + 1 km circle; satellite-image background (opt-in, `SKYLINE_CV_F_SKY13_SAT_BG=1`); pano-projected coastline dots; pano↔OSM IoU annotation
- **Phase C** (`SKYLINE_CV_PHASE_C=1`): OSM-primary sweep replacing satellite-HSV as keypoint source — in progress
- **Plan**: `docs/plans/F-SKY13-osm-coastline-footprints-overlay.md`

#### **F-SKY15: HTML Diagnostic Report**
- **Status**: Landed
- **Location**: `city2stl/skyline_cv/html_report.py`; call site in `region_pdf.py`
- **What it does**: Renders `index.html` + per-seed HTML pages with embedded minimap PNGs; all tabular data lives here (PDF became compact archival artefact via `pano_only_pdf: true`)
- **Plan**: `docs/plans/F-SKY15-html-diagnostic-report.md`

### 🔴 Pending (Not Yet Integrated)

#### **F-SKY5: MobileSAM Instance-Segmentation Head**
- **Status**: Designed, not implemented
- **Location**: Plan in `docs/plans/F-SKY5-mobilesam-instance.md`
- **Purpose**: Replace F-SKY3 Voronoi with real instance-segmentation model
- **Model**: MobileSAM (~10M params) with OSM-projected centroids as point prompts
- **Trigger**: Fires only when SegFormer produces merged blob + ≥2 OSM markers inside
- **Impact**: Solves F-SKY3 regression; enables F-SKY2+F-SKY6 to work on dense skylines
- **Effort**: Large (model integration + inference optimization)
- **Priority**: Pending alignment with F-SKY2/F-SKY6/F-SKY8 effectiveness; may not be needed

#### **F-SKY14: Trained Satellite Coastline Detector**
- **Status**: Proposed, not yet planned
- **Purpose**: Replace heuristic HSV `detect_sat_water_mask` with a small CNN trained on OSM `natural=coastline` linestrings
- **Constraint**: Any satellite-side detector MUST be trained against OSM ground truth, not heuristic (see feedback memory)
- **Priority**: Defer until OSM-sparse regions are encountered in practice; F-SKY13 covers all current coastal seeds

#### **F-SKY11.2: Pano Bird's-Eye Registration (Attempted — Not Viable)**
- **Status**: Experiment failed; code deleted 2026-05-24 (F-CLEAN6)
- **Location**: Code removed; failure analysis: `docs/plans/F-SKY11.2-pano-birdseye-registration.md`
- **Why it failed**: Monocular SegFormer water detection has insufficient depth reach (~5–7m) vs bay scale (1km+). IoU rotation search produces flat signal across all headings, with spurious peaks indistinguishable from noise.

#### **F-SKY-PIPELINE Consolidation**
- **Status**: Phases 0–2 complete; Phase 3 (second region scaffolding) pending
- **Location**: Plan in `docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md`
- **Completed**: All active signals classified; config flags documented; module deletions done; HTML report wired
- **Remaining**: Phase 3 (Miami + Chicago end-to-end with new flags), Phase 4 unit tests for sweep_pano_heading_offset and F-SKY13 edge cases

---

## Measurement Results & Validation

### Cartagena Seed_5 (Bocagrande Waterfront)

| Metric | Before F-SKY | After F-SKY2/6 | Notes |
|--------|--------------|----------------|-------|
| **Visible towers** | 34 | 34 | No change in visible signal |
| **OSM polygons** | 12 | 18 | F-SKY8 satellite addition |
| **Merged segments** | 3 blobs | 1 blob + split residue | F-SKY2 improvement |
| **1:1 matches** | 11 (duplicates) | 13 (clean) | F-SKY6 enforcement |
| **Page 31 gap** | 259px, invisible | 259px, local-maxima carved | F-SKY7 detects |
| **Heading offset** | Manual 320° | Recovered 310° | F-SKY11.1 Phase A |

### Known Gaps

1. **Tall glass towers**: Systematically under-predicted by 50–100m (building-level problem, not F-SKY)
2. **Cross-seed coverage**: ~3 buildings per run (limits statistical significance)
3. **Water boundaries**: Heavy reliance on proximity scoring; can fail in enclosed harbors

---

## Configuration & Activation

All F-SKY features are gated by config flags (not yet unified):

```python
# In region_pdf.py, early in generate_region_report():
config = {
    'F_SKY1_ENABLED': False,        # Floor periodicity (diagnostic)
    'F_SKY2_ENABLED': True,         # OSM-anchored splitting (active)
    'F_SKY3_ENABLED': False,        # Voronoi (disabled after regression)
    'F_SKY4_ENABLED': True,         # Mask overlay (diagnostic)
    'F_SKY5_ENABLED': False,        # MobileSAM (pending)
    'F_SKY6_ENABLED': True,         # 1:1 assignment (active)
    'F_SKY7_ENABLED': True,         # Local-maxima peaks (active)
    'F_SKY8_ENABLED': True,         # Satellite footprints (active)
    'F_SKY10_ENABLED': False,       # Cross-view non-ML (pending)
    'F_SKY11_1_ENABLED': False,     # Pano coastline (Phase A complete, B pending)
}
```

**TODO (F-SKY-PIPELINE consolidation)**: Unify config into single `F_SKY_CONFIG` dict with comments explaining each flag.

---

## Integration Checklist for Phase B & Beyond

- [ ] **F-SKY11.1 Phase B**: Integrate heading correction into height aggregation
- [ ] **F-SKY-PIPELINE consolidation**: Unify all config flags, document in README
- [ ] **F-SKY5 decision**: Measure F-SKY2/6/8 effectiveness first; if insufficient, implement
- [x] **F-SKY10 integration**: Landed as diagnostic-only; `use_cross_view_scoring` opt-in; `cv̄` shown in PDF header
- [ ] **modules.md update**: Document all new helper modules (osm_water, depth_estimation, html_report, etc.)
- [ ] **test_skyline_cv_*.py**: Unit test for `sweep_pano_heading_offset` on synthetic input; unit test for F-SKY13 OSM coastline extraction edge cases
- [ ] **Documentation**: Update `city2stl/skyline_cv/README.md` with consolidated feature list and current state
- [ ] **F-SKY-PIPELINE Phase 3**: Wire Miami + Chicago to new flags; capture per-seed recovery accuracy in STATUS.md

---

## Files & Resources

### Core Implementation
- `pipeline.py` — F-SKY1-8, F-SKY10, F-SKY11/11.1 logic
- `region_pdf.py` — Orchestration + I/O + PDF + HTML call sites
- Helper modules: `height_trace.py` (F-SKY1), `satellite_footprints.py` (F-SKY8), `satellite_image.py`, `cross_view.py` (F-SKY10), `coastline_registration.py` (F-SKY11/11.1/13), `osm_water.py` (F-SKY13), `depth_estimation.py` (F-SKY12), `html_report.py` (F-SKY15)
- Deleted: `pano_birdseye.py` (F-CLEAN6), `config.py` (F-CLEAN1)

### Test & Demo
- `tests/test_skyline_cv_height_trace.py` (F-SKY1 tests)
- `tests/test_skyline_cv_osm_water.py` (F-SKY13 tests)
- `scripts/08_region_skyline_pdf.py` (entry point; uses activated features from config)
- `scripts/09_height_trace.py` (F-SKY1 demo)
- `scripts/10_cross_view_demo.py` (F-SKY10 prep)
- `scripts/11_coastline_demo.py` (F-SKY11 demo)
- `scripts/12_pano_coastline_demo.py` (F-SKY11.1 demo)
- `scripts/13_heading_recovery_demo.py` (multi-channel heading research; replaced former script 13)
- Deleted: `scripts/13_birdseye_registration_demo.py` (F-CLEAN6)

### Documentation & Plans
- `docs/plans/F-SKY*.md` (13 feature plans)
- `docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md` (consolidation roadmap)
- `docs/plans/F-SKY-AUDIT-2026-05-17.md` (current state audit)
- `city2stl/skyline_cv/STATUS.md` (accuracy numbers, open gaps)
- `city2stl/skyline_cv/README.md` (architecture overview, this file)

### References
- Memory: `project_building_heights.md` (height sources and strategy)
- Proposals.md: All F-SKY items tracked with status (in-progress, pending, superseded)

---

## Recommendation for Next Session

1. **F-SKY13 Phase C**: Validate OSM-primary sweep (`SKYLINE_CV_PHASE_C=1`) on Cartagena; calibrate peak floor vs satellite path; drop HSV detector if OSM-primary is consistent
2. **F-SKY-PIPELINE Phase 3**: Wire Miami + Chicago to active flags (`use_satellite_footprints`, `use_pano_coastline_recovery`, `pano_only_pdf`); capture recovered headings + coverage in STATUS.md
3. **F-CLEAN8**: Split `_seed_multiview_registration` (1211 LOC) into 5 named helpers
4. **Tests**: Add unit tests for `sweep_pano_heading_offset` and F-SKY13 OSM edge cases
5. **Decision**: Once Phase 3 coverage measured, decide whether F-SKY5 (MobileSAM) or continued tuning of F-SKY2/6/7 is the better path

**Current Status**: Phases 0–2 complete; F-SKY13 Phase C is the active front; system is coherent with HTML report + all active flags wired.
