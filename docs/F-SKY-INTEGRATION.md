# F-SKY Series — Integration Status & Consolidation

**Last Updated**: 2026-06-23  
**Author**: Claude (consolidation pass; 2026-06-23 refactor + F-DET review update)  
**Purpose**: Single source of truth for F-SKY feature activation, status, and pipeline integration

---

## Executive Summary

The F-SKY series is a set of 11+ computer-vision improvements to the skyline height estimation pipeline. This document consolidates their status, active/disabled state, measurement results, and integration roadmap.

**Current production**: `retna_pruned.pt` (0.2691 loss, 3.82m MAE)  
**Target improvement**: Cartagena building height accuracy via cross-view registration

**Active features**: F-SKY1 (floor-period rescue in aggregation, 2026-06-10), F-SKY2, F-SKY4, F-SKY5 (wired, requires SKYLINE_CV_F_SKY5=1 + MobileSAM checkpoint), F-SKY6, F-SKY7, F-SKY8, F-SKY10 (opt-in, diagnostic-only), F-SKY11.1 Phase A+B (pano heading recovery → anchor seed, Phase B opt-in via SKYLINE_CV_F_SKY11_1=1, 2026-06-13), F-SKY12 Phase A+B (depth rescue + confidence downweight in aggregation, 2026-06-10), F-SKY13 (Phases A/A.2/B/C all done; Phase C requires SKYLINE_CV_PHASE_C=1), F-SKY15 (HTML report)
**Removed**: F-SKY3 (measured regression; function deleted 2026-05-18 via F-CLEAN2), F-SKY11.2 (IPM bird's-eye dead-end; code deleted 2026-05-24 via F-CLEAN6)
**Pending**: F-SKY14 (trained satellite coastline detector — deferred until OSM-sparse regions encountered)

**F-DET series** (detection quality & early-out, 2026-06-20):
- F-DET1 ✅: Blob-count pano screen — `pano_registration.py`; exit before registration if top-3-view blob count < 6 (saves 30–60 s per bad seed)
- F-DET2 ✅: OSM-backed FOV gate — `seed_selection.py`; reject proposals with < 3 OSM buildings in 80° FOV cone; add FOV-density score bonus
- F-DET3 ✅: Weak quality sub-labels — `html_report.py`; "weak — no detection" / "weak — mismatch" / "weak — low coverage" instead of a flat "weak"
- F-DET5 ✅: Landing page det column — `build_landing_page.py`; Det cell colored red (nseg < 10) / amber (10–19); sub-label filter in quality dropdown
- F-DET4a–c (pending): Type 2 root-cause fixes per-city (satellite footprints, snap tolerance, non-grid geometry)

---

## Pipeline Architecture

**Refactor note (F-CLEAN14 full split, 2026-06-23):** the four over-large skyline
files are now **thin re-export façades** over focused subpackages — every existing
`from city2stl.skyline.<module> import X` import path still works unchanged. The
implementation moved to `_core/`, `_pano/`, `_report_plots/`, `_region_render/`. Split
was behaviour-neutral (773 strm2stl tests + 10 Playwright e2e green); a post-split ruff
audit caught and fixed 3 NameError bugs on untested paths (missing `logger` in two
plot subpackages, missing `np` in `_plot_utils`). See `docs/plans/F-CLEAN14-skyline-file-split.md`.

```
city2stl/skyline/
├── pipeline.py          (façade)  → _core/ {types, util, segmentation, projection,
│                                            skyline, pano, registration, height}
├── pano_registration.py (façade)  → _pano/ {capture, heading, detect, orchestrator}
├── report_plots.py      (façade)  → _report_plots/ {_plot_utils, _view_plots, _pano_plots}
├── region_render.py     (façade)  → _region_render/ {_draw, _pages}
├── region_pdf.py        (orchestration + I/O + PDF rendering)
├── scripts/
│   ├── 08_region_skyline_pdf.py  (production entry point)
│   ├── 09_height_trace.py        (F-SKY1 diagnostic)
│   ├── build_landing_page.py / discover_city_seeds.py / height_diagnostic_report.py
│   └── demos/ {13_heading_recovery, 14_seed5_diagnostic, 15_multires_seg, 16_view_minimap}
└── [helper modules]
    ├── height_trace.py / height_trace_render.py   (F-SKY1)
    ├── satellite_footprints.py / satellite_image.py (F-SKY8 + cross-view)
    ├── cross_view.py              (F-SKY10)
    ├── coastline_registration.py  (F-SKY11, F-SKY11.1, F-SKY13)
    ├── osm_water.py               (F-SKY13 — OSM coastline fetch + keypoints)
    ├── depth_estimation.py        (F-SKY12 — Depth Anything V2 verifier)
    ├── region_data.py / region_types.py / region_config.py  (F-CLEAN14 data/types/flags)
    ├── seed_selection.py          (F-DET2 — standoff proposal + OSM FOV gate)
    ├── web_image_seed.py          (Wikipedia/Wikimedia skyline image seeds)
    └── height/providers/          (shared _cache.py + _raster.py dedup, 2026-06-22)
```

Deleted: ``pano_birdseye.py`` (F-CLEAN6, 2026-05-24), ``config.py`` (F-CLEAN1,
2026-05-24). Demo/diagnostic scripts 13–16 moved to ``scripts/demos/`` (2026-06-23).
Post-mortem for F-SKY11.2 in ``docs/plans/archive/``.

> **numpy2stl (sibling repo) parallel refactor, 2026-06-22/23:**
> `registration/pipeline.py` → orchestrator + `stages/`; `processing/building_simplify.py`
> → package; report CSS → `registration/templates/report.css`; `test_registration.py`
> split per-feature + new offline `test_pipeline_smoke.py`. 173 numpy2stl tests green.

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
- **Status**: Implemented, integrated into aggregation ✅ (2026-06-10)
- **Location**: `pipeline.py:_floor_period_for_building()`, `height_trace.py`, `aggregate_building_heights()`
- **Purpose**: Detect horizontal banding in building masks → estimate floor count → OSM-independent height rescue
- **Activation**: Default ON (`SKYLINE_CV_F_SKY1=1`); `compute_floor_period=_F_SKY1_ENABLED` passed in `pano_registration.py`
- **Current use**: `aggregate_building_heights` collects `inferred_height_m` across views with `floor_confidence ≥ 0.30`; if ≥2 views agree and F-SKY1 median ≥ 1.4× geometric median, `effective_height_m` rescues upward and `effective_height_source = "f_sky1"`
- **New output fields**: `f_sky1_height_m`, `f_sky1_n_views`, `effective_height_m`, `effective_height_source`

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

#### **F-SKY11.1: Pano-Level Coastline Alignment (Phases A + B)**
- **Status**: Phase A complete ✅, Phase B integrated ✅ (2026-06-13)
- **Location**: `coastline_registration.py`, demo script `scripts/12_pano_coastline_demo.py`; Phase B in `region_config.py` + `pano_registration.py:_recover_anchor_offset`
- **Purpose**: Single global heading-offset recovery from stitched 360° pano + water mask; recovered offset seeds the joint anchor optimizer's fine sweep
- **Method**: Water-distance radial signatures + numbered coastline keypoints + multi-view sweep
- **Previous approach**: F-SKY11 (12 independent per-view best-heading searches)
- **Improvement**: One offset solve using all 24 keypoints simultaneously; pano-recovered offset replaces the manual `anchor_offsets_deg` config for water-adjacent seeds
- **Measurement**: Cartagena seed_5 recovers 310° vs manual 320° (within 10° tolerance)
- **Phase A**: Scorer + demo script (diagnostic-only)
- **Phase B**: `_recover_anchor_offset` uses `pano_recovered_offset` as fine-sweep seed when quality gate passes (`sigma ≤ 0.10`, `peak > 0.40`). Falls back to full coarse sweep when coastline signal is absent (inland, flat peak). Per-site `drive_anchor: true` in config still works; `SKYLINE_CV_F_SKY11_1=1` activates globally.
- **Activation**: `SKYLINE_CV_F_SKY11_1=1` (default OFF until validated on Miami); per-site fallback: `drive_anchor: true` in `pano_recovery_state`
- **Validation needed**: Run Cartagena seed_5 with `SKYLINE_CV_F_SKY11_1=1`; confirm `anchor_offset` matches existing `anchor_offsets_deg` value within ±5°; confirm MAE within ±1 m of baseline

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
- **Location**: `city2stl/skyline/cross_view.py`; opt-in via `use_cross_view_scoring`
- **What landed**: All 3 signals (colour/width/edges) implemented; blended into matcher at 0.85/0.15 weights; `cv̄=X.XX/min=Y.YY` shown in per-view PDF header (F-CLEAN5, 2026-05-24)
- **Why demoted**: Per-building colour was empirically fragile; F-SKY11.1 supersedes the cross-view registration intent. The scorer stays on disk and is wired behind the opt-in flag; it does NOT drive production heights.
- **Phase B** (production reranking): still pending — measure first whether F-SKY2/6/8 coverage is sufficient

#### **F-SKY12: Depth Anything V2 on Street View Panos**
- **Status**: Phase A + Phase B landed ✅ (2026-06-10)
- **Location**: `city2stl/skyline/depth_estimation.py`; `pipeline.py:augment_estimates_with_depth()`, `aggregate_building_heights()`
- **Activation**: `SKYLINE_CV_F_SKY12=1` (DA2 inference ~1-2s/view on CPU)
- **Phase A**: `augment_estimates_with_depth` adds `depth_height_m` + `depth_disagreement` to each estimate
- **Phase B (new)**: `aggregate_building_heights` uses depth for two purposes:
  - **Confidence downweight**: when `depth_disagreement=True` AND `depth_height_m < estimated_height_m × 0.70`, confidence halved (geometric may be chasing a false silhouette top)
  - **Rescue**: when ≥2 views have `depth_height_m > geometric × 1.30`, `depth_rescue_height_m` is computed; if it exceeds geometric × 1.40, `effective_height_m` rescues upward
- **New output fields**: `depth_rescue_height_m` (contributes to `effective_height_m`/`effective_height_source`)
- **Plan**: `docs/plans/F-SKY12-depth-from-panos.md`

#### **F-SKY13: OSM-Coastline Registration + Footprints Overlay**
- **Status**: Phases A, A.2, B, C all implemented ✅ (Phase C verified 2026-06-10)
- **Location**: `city2stl/skyline/osm_water.py`, `coastline_registration.py`, `region_pdf.py`, `pano_registration.py`
- **Phase C**: `SKYLINE_CV_PHASE_C=1` — `region_pdf.py` sets `primary_source: "osm"`; `_recover_pano_heading` branches to `osm_keypoints_for_scoring` instead of satellite HSV; same `sweep_pano_heading_offset` runs with OSM-derived `{bearing_deg, distance_m}` keypoints
- **Validation needed**: Run on Cartagena seed_5 with `SKYLINE_CV_PHASE_C=1`; confirm recovered heading ≈ 320° and IoU improves vs Phase B
- **Plan**: `docs/plans/F-SKY13-osm-coastline-footprints-overlay.md`

#### **F-SKY15: HTML Diagnostic Report**
- **Status**: Landed
- **Location**: `city2stl/skyline/html_report.py`; call site in `region_pdf.py`
- **What it does**: Renders `index.html` + per-seed HTML pages with embedded minimap PNGs; all tabular data lives here (PDF became compact archival artefact via `pano_only_pdf: true`)
- **Plan**: `docs/plans/F-SKY15-html-diagnostic-report.md`

### 🔴 Pending (Not Yet Integrated)

#### **F-SKY5: MobileSAM Instance-Segmentation Head**
- **Status**: Fully implemented and wired ✅ (2026-06-10); requires external checkpoint to activate
- **Location**: `pipeline.py:osm_sam_instance_silhouettes()`, `pano_registration.py` Stage 8
- **Purpose**: Split merged blobs (≥2 OSM markers inside) using MobileSAM point prompts
- **Activation**: `SKYLINE_CV_F_SKY5=1` + MobileSAM installed + checkpoint at `~/.cache/mobile_sam/vit_t.pth`
- **Install**: `pip install git+https://github.com/ChaoningZhang/MobileSAM.git` then download vit_t.pth
- **Graceful no-op**: returns segments unchanged when package or checkpoint absent
- **Documented**: installation steps added to `requirements.txt` (comments)

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

## Integration Checklist

- [x] **F-SKY1 production integration**: `aggregate_building_heights` now uses `inferred_height_m` for upward rescue (2026-06-10)
- [x] **F-SKY5 wiring**: `osm_sam_instance_silhouettes` fully wired; activate with `SKYLINE_CV_F_SKY5=1` + MobileSAM checkpoint
- [x] **F-SKY12 Phase B**: depth confidence downweight + rescue in `aggregate_building_heights` (2026-06-10)
- [x] **F-SKY13 Phase C wiring**: OSM-primary sweep complete; activate with `SKYLINE_CV_PHASE_C=1`
- [x] **F-SKY10 integration**: Landed as diagnostic-only; `use_cross_view_scoring` opt-in; `cv̄` shown in PDF header
- [ ] **F-SKY13 Phase C validation**: Run Cartagena seed_5 with `SKYLINE_CV_PHASE_C=1`; confirm heading ≈ 320°
- [ ] **F-SKY5 validation**: Install MobileSAM checkpoint; run on dense Cartagena skyline; confirm MAE ≤ F-SKY2 baseline
- [x] **F-SKY11.1 Phase B**: `_recover_anchor_offset` uses pano-recovered offset as fine-sweep seed when `SKYLINE_CV_F_SKY11_1=1` and quality gate passes (2026-06-13)
- [ ] **F-SKY-PIPELINE Phase 3**: Wire Miami + Chicago to active flags; capture per-seed recovery accuracy in STATUS.md
- [ ] **modules.md update**: Document new helper modules (osm_water, depth_estimation, html_report)
- [ ] **test_skyline_*.py**: Unit test for `sweep_pano_heading_offset` on synthetic input; F-SKY13 OSM coastline edge cases

---

## Files & Resources

### Core Implementation
- `pipeline.py` — F-SKY1-8, F-SKY10, F-SKY11/11.1 logic
- `region_pdf.py` — Orchestration + I/O + PDF + HTML call sites
- Helper modules: `height_trace.py` (F-SKY1), `satellite_footprints.py` (F-SKY8), `satellite_image.py`, `cross_view.py` (F-SKY10), `coastline_registration.py` (F-SKY11/11.1/13), `osm_water.py` (F-SKY13), `depth_estimation.py` (F-SKY12), `html_report.py` (F-SKY15)
- Deleted: `pano_birdseye.py` (F-CLEAN6), `config.py` (F-CLEAN1)

### Test & Demo
- `tests/test_skyline_height_trace.py` (F-SKY1 tests)
- `tests/test_skyline_osm_water.py` (F-SKY13 tests)
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
- `city2stl/skyline/STATUS.md` (accuracy numbers, open gaps)
- `city2stl/skyline/README.md` (architecture overview, this file)

### References
- Memory: `project_building_heights.md` (height sources and strategy)
- Proposals.md: All F-SKY items tracked with status (in-progress, pending, superseded)

---

## Recommendation for Next Session

1. **F-SKY11.1 Phase B validation**: Run `SKYLINE_CV_F_SKY11_1=1` on Cartagena seed_5; confirm `anchor_offset` matches existing `anchor_offsets_deg` value within ±5°; confirm MAE within ±1 m of baseline (13.73 m)
2. **F-SKY13 Phase C validation**: Run `SKYLINE_CV_PHASE_C=1` on Cartagena seed_5; confirm recovered heading ≈ 320° and IoU > Phase B (satellite HSV) baseline
3. **F-SKY5 checkpoint install**: Download `vit_t.pth` → `~/.cache/mobile_sam/`; run `SKYLINE_CV_F_SKY5=1` on Cartagena dense skyline; confirm tagged-building MAE ≤ F-SKY2 baseline
4. **F-SKY-PIPELINE Phase 3**: Wire Miami + Chicago to active flags; capture per-seed recovery accuracy in STATUS.md
5. **Tests**: Add unit test for `sweep_pano_heading_offset` on synthetic keypoints; add F-SKY13 OSM coastline edge-case tests

**Current Status (2026-06-13)**: F-SKY1, F-SKY5, F-SKY12 Phase B, F-SKY11.1 Phase B all wired and tested. F-SKY13 Phase C and F-SKY11.1 Phase B wired; both need on-region validation. Remaining work is validation + second region (Miami) scaffolding.
