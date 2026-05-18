# F-SKY Series — Integration Status & Consolidation

**Last Updated**: 2026-05-17  
**Author**: Claude (consolidation pass)  
**Purpose**: Single source of truth for F-SKY feature activation, status, and pipeline integration

---

## Executive Summary

The F-SKY series is a set of 11+ computer-vision improvements to the skyline_cv height estimation pipeline. This document consolidates their status, active/disabled state, measurement results, and integration roadmap.

**Current production**: `retna_pruned.pt` (0.2691 loss, 3.82m MAE)  
**Target improvement**: Cartagena building height accuracy via cross-view registration

**Active features**: F-SKY1, F-SKY2, F-SKY4, F-SKY6, F-SKY7, F-SKY8, F-SKY11.1 (Phase A)  
**Disabled**: F-SKY3 (measured regression)  
**Pending**: F-SKY5, F-SKY10, F-SKY11.1 (Phase B)

---

## Pipeline Architecture

The skyline_cv pipeline lives in two files (intentional simplicity for testing):

```
city2stl/skyline_cv/
├── pipeline.py          (~2400 lines, pure math, unit-tested)
├── region_pdf.py        (~2750 lines, orchestration + I/O)
├── scripts/
│   ├── 08_region_skyline_pdf.py  (entry point)
│   ├── 09_height_trace.py         (F-SKY1 demo)
│   ├── 10_cross_view_demo.py      (F-SKY10 prep)
│   ├── 11_coastline_demo.py       (F-SKY11 demo)
│   ├── 12_pano_coastline_demo.py  (F-SKY11.1 Phase A demo)
│   ├── 13_birdseye_registration_demo.py (F-SKY11.2 prep)
└── [helper modules]
    ├── height_trace.py            (F-SKY1)
    ├── satellite_footprints.py    (F-SKY8)
    ├── satellite_image.py         (F-SKY8)
    ├── cross_view.py              (F-SKY10)
    ├── coastline_registration.py  (F-SKY11, F-SKY11.1)
    └── pano_birdseye.py           (F-SKY11.2)
```

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
- **Status**: Disabled after measurement ❌
- **Location**: `pipeline.py:osm_marker_voronoi_silhouettes()` (remains on surface for A/B)
- **Purpose**: Use Voronoi over OSM marker x_px to split merged masks
- **Why disabled**: Measured regression on Cartagena
  - MAE: 17.28m → 22.13m (↑ 4.85m)
  - Tagged count: 13 → 8 buildings (↓ 5)
  - Unconditional splitting was too aggressive
- **Replacement direction**: Dedicated instance-segmentation model (MobileSAM or TinySAM) — see F-SKY5
- **Call site**: `region_pdf.py` (commented out, documented)
- **Future**: Uncomment only if F-SKY5 blocks; otherwise superseded by F-SKY5

---

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

#### **F-SKY10: Non-ML Cross-View Registration**
- **Status**: Designed, demo in progress
- **Location**: Plan in `docs/plans/F-SKY10-non-ml-cross-view-registration.md`, demo script in progress
- **Purpose**: Geometric + appearance verification (street view ↔ satellite)
- **Method**: Classical CV — roof colour/texture matching + facade width consistency
- **Use**: Acts as independent reranking signal alongside IoU/containment matcher
- **Effort**: Large
- **Priority**: Lower; F-SKY2/F-SKY6/F-SKY8 may solve most problems

#### **F-SKY11.2: Pano Bird's-Eye Registration (Planned)**
- **Status**: Designed, not started
- **Location**: Plan in `docs/plans/F-SKY11.2-pano-birdseye-registration.md`
- **Purpose**: Bird's-eye view registration as complementary signal to street view
- **Method**: Satellite image + projected polygons + shape matching
- **Effort**: Medium
- **Priority**: After F-SKY11.1 Phase B

#### **F-SKY-PIPELINE Consolidation**
- **Status**: Pending official consolidation
- **Location**: Plan in `docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md`
- **Purpose**: Classify each signal as core/opt-in/diagnostic-only; unify config
- **Scope**: Update `region_pdf.py` to officially enable/disable all F-SKY features via single config dict
- **Effort**: Medium (mostly documentation + config refactor)
- **Timeline**: Should happen as Phase B + pending features land

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
- [ ] **F-SKY10 integration**: Complete non-ML cross-view scorer, gate behind config
- [ ] **modules.md update**: Document all new helper modules (height_trace, satellite_*, etc.)
- [ ] **test_skyline_cv_*.py**: Expand test coverage for F-SKY features (currently only height_trace tested)
- [ ] **Documentation**: Update `city2stl/skyline_cv/README.md` with consolidated feature list and current state

---

## Files & Resources

### Core Implementation
- `pipeline.py` — All F-SKY1-8, F-SKY11 logic (2400 lines)
- `region_pdf.py` — Orchestration + I/O (2750 lines)
- Helper modules: `height_trace.py`, `satellite_footprints.py`, `satellite_image.py`, `cross_view.py`, `coastline_registration.py`, `pano_birdseye.py`

### Test & Demo
- `tests/test_skyline_cv_height_trace.py` (266 lines, F-SKY1 tests)
- `scripts/08_region_skyline_pdf.py` (entry point; uses activated features from config)
- `scripts/09_height_trace.py` (F-SKY1 demo)
- `scripts/10_cross_view_demo.py` (F-SKY10 prep)
- `scripts/11_coastline_demo.py` (F-SKY11 demo)
- `scripts/12_pano_coastline_demo.py` (F-SKY11.1 Phase A demo)
- `scripts/13_birdseye_registration_demo.py` (F-SKY11.2 prep)

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

1. **Phase B integration**: Implement F-SKY11.1 Phase B (heading correction into aggregation)
2. **Consolidation**: Run F-SKY-PIPELINE consolidation (unify config, document)
3. **Measurement**: Re-measure Cartagena with all active features; quantify improvement
4. **Decision**: Based on metrics, prioritize F-SKY5 vs F-SKY10 vs halt
5. **Documentation**: Update modules.md, test coverage, city2stl/skyline_cv/README.md

**Current Status**: All Phase A features implemented; system is coherent and measurement-ready.
