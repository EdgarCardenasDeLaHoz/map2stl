# AI-Proposed Features & Tasks — strm2stl

_Last updated: 2026-05-26_

> Completed and denied items are archived at the bottom of this file.

> **How to use:**
> - Set `Status` to `approved` to queue an item for implementation.
> - Set `Status` to `denied` to permanently drop it (AI will not re-propose).
> - Set `Status` to `deferred` to skip for now without closing the idea.
> - Leave `pending` for items not yet reviewed.
>
> When you approve an item, Claude will implement it in the next session and mark it `done` here.
> Claude will **not** implement any item whose status is not `approved`.

---

## New Features

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| F-REG3 | Region settings inheritance — "use global defaults" override per region | `regions/regions.js`, `app/server/routers/regions.py` | Medium | approved |
| F-SKY1 | Floor-strip periodicity for skyline height + distance estimation — detect the per-floor horizontal banding in a building's mask region (1D FFT / autocorrelation on a row-mean intensity profile), report the dominant pixel period, and back out floor count, height, and an independent distance check. Implemented; gated `compute_floor_period=False` (diagnostic-only, never feeds production heights). Plan: [plans/F-SKY1-floor-periodicity.md](plans/F-SKY1-floor-periodicity.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py` | Medium | in-progress |
| F-SKY2 | OSM-anchored silhouette splitting/merging — use projected OSM footprint x-ranges as structural priors to split mask-merged segments. Implemented and measured (Cartagena seed_5 improved). Core pipeline, enabled by default. Plan: [plans/F-SKY2-osm-anchored-segments.md](plans/F-SKY2-osm-anchored-segments.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py` | Medium | in-progress |
| F-SKY4 | SegFormer building-mask overlay on per-view PDF pages — semi-transparent colour overlay of the raw building mask on top of the Street View image. Implemented, diagnostic-only. Plan: [plans/F-SKY4-mask-overlay.md](plans/F-SKY4-mask-overlay.md). | `city2stl/skyline/region_pdf.py` | Small | in-progress |
| F-SKY5 | MobileSAM instance head gated on OSM markers — replaces F-SKY3's failed Voronoi heuristic with a real small instance-segmentation model. MobileSAM (~10 M params) takes the cached Street View image plus the OSM-projected centroids of buildings in a merged mask blob as point prompts and returns per-instance masks with confidence scores. Fires only when SegFormer produces a merged blob with ≥ 2 OSM markers. Plan: [plans/F-SKY5-mobilesam-instance.md](plans/F-SKY5-mobilesam-instance.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py`, `requirements*.txt` | Large | pending |
| F-SKY6 | One-to-one segment-to-building assignment + all-projections diagnostic overlay — global 1:1 constraint (Hungarian) so each building wins at most one segment. Also surfaces all projections (including rejected) as faint markers on per-view minimaps. Implemented, enabled by default. Plan: [plans/F-SKY6-one-to-one-matching.md](plans/F-SKY6-one-to-one-matching.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py` | Medium | in-progress |
| F-SKY7 | Local-maxima peak detection in contour + per-view page layout refactor — local-max pass on the roofline contour so monotone bumpy rooflines get carved into per-tower segments. Per-view PDF page refactored: SegFormer mask as separate plot, legend table removed. Implemented, enabled by default. Plan: [plans/F-SKY7-local-max-peaks-and-layout.md](plans/F-SKY7-local-max-peaks-and-layout.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py` | Medium | in-progress |
| F-SKY8 | Satellite-derived building footprints as a second polygon source — Microsoft Global ML Building Footprints de-duplicated against OSM, passed into the existing matcher. Implemented (`satellite_footprints.py`), opt-in per region via `use_satellite_footprints`. Plan: [plans/F-SKY8-satellite-footprints.md](plans/F-SKY8-satellite-footprints.md). | `city2stl/skyline/region_pdf.py`, `city2stl/skyline/satellite_footprints.py` | Medium | in-progress |
| F-SKY10 | Non-ML cross-view registration — colour/width/edges scorer blended into matcher at 0.85/0.15 weights; all 3 signals in `cross_view.py`; per-view PDF header shows `cv̄=X.XX/min=Y.YY`. Demoted to diagnostic-only (no production height influence). Phase B (production rerank) remains pending. Plan: [plans/F-SKY10-non-ml-cross-view-registration.md](plans/F-SKY10-non-ml-cross-view-registration.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/cross_view.py` | Large | in-progress |
| F-SKY11 | Image-level coastline-alignment registration via radial water signatures + numbered coastline keypoints — diagnostic tool; standalone demo at `scripts/11_coastline_demo.py`. Superseded by F-SKY11.1 for production heading recovery. | `city2stl/skyline/coastline_registration.py`, `scripts/11_coastline_demo.py` | Medium | in-progress |
| F-SKY11.1 | Pano-level coastline alignment — single global heading-offset recovery from the stitched 360° pano + water mask vs F-SKY13 OSM keypoints. Phase A (scorer + demo) and Phase B (integration into `_seed_multiview_registration` as joint-anchor coarse seed, gate calibrated σ ≤ 0.10 AND peak > 0.40) both landed. Phase C (OSM-primary sweep — SKYLINE_CV_PHASE_C env flag) in progress. Plan: [plans/F-SKY11.1-pano-coastline-alignment.md](plans/F-SKY11.1-pano-coastline-alignment.md). | `city2stl/skyline/coastline_registration.py`, `scripts/12_pano_coastline_demo.py`, `city2stl/skyline/region_pdf.py` | Medium | in-progress |
| F-SKY12 | Depth Anything V2 on Street View panos for height cross-verification — Phase A landed: `depth_estimation.py` calibrates relative depth against OSM anchors and emits `depth_height_m` + `depth_disagreement` flag per match. Does NOT influence aggregated heights yet (verifier only). Phase B (confidence weighting/rescue) pending. Tests: `tests/test_skyline_depth.py`. Plan: [plans/F-SKY12-depth-from-panos.md](plans/F-SKY12-depth-from-panos.md). | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py` | Medium | done (Phase A, 2026-05-26) |
| F-SKY13 | OSM-coastline registration on pano + footprints-view overlay — OSM `natural=coastline` / `natural=water` polygons as primary registration ground truth. Phase A (osm_water.py + minimap OSM coast + 1 km circle), Phase A.2 (satellite-image background opt-in, OSM keypoint adapter), Phase B (pano-projected coastline overlay + pano↔OSM IoU annotation), and Phase C (OSM-primary sweep behind `SKYLINE_CV_PHASE_C=1`) all landed. Tests: `tests/test_skyline_osm_water.py`. Plan: [plans/F-SKY13-osm-coastline-footprints-overlay.md](plans/F-SKY13-osm-coastline-footprints-overlay.md). | `city2stl/skyline/coastline_registration.py`, `city2stl/skyline/region_pdf.py`, `city2stl/skyline/osm_water.py` | Medium | done (Phase A–C, 2026-05-26) |
| F-SKY14 | Trained satellite-coastline detector supervised by OSM ground truth — replaces the heuristic HSV-threshold `detect_sat_water_mask` with a small CNN trained on OSM `natural=coastline` linestrings. Needed only when OSM coastline coverage is sparse. Until then F-SKY13's OSM-primary path covers all coastal seeds. Constraint: any satellite-side detector MUST be trained against OSM, not heuristic. Defer until OSM-sparse regions encountered in practice. | `city2stl/skyline/coastline_registration.py`, training scripts under `scripts/`, new model under `models/` | Large | pending |
| F-SKY15 | HTML diagnostic report alongside the PDF — `html_report.py` renders per-seed HTML pages with embedded minimap PNGs; `index.html` links them. All tabular data lives in HTML; PDF is the compact archival artefact. Landed as part of pipeline consolidation (A3 step). Phase A complete: call site in `region_pdf.py`, full renderer with F-SKY12 depth columns + per-view gallery. Tests in `tests/test_skyline_html_report.py`. Plan: [plans/F-SKY15-html-diagnostic-report.md](plans/F-SKY15-html-diagnostic-report.md). | `city2stl/skyline/html_report.py`, `city2stl/skyline/region_pdf.py` | Medium | done (2026-05-26) |
| F-SKY16 | Coastline-ICP heading registration — register the pano-projected coastline against the OSM coastline by seed-centred rotation (bearings trusted, ranges ignored) to recover the heading offset. Phase A landed `coastline_icp_offset()` + a measure-only comparison log; synthetic test recovers known offsets ±2° under radial compression. Cartagena measurement: ICP is COMPLEMENTARY to the keypoint sweep (fixes seed_4's 136° miss → −18°, but regresses seed_5 onto the 180° bay-symmetry mirror). Conclusion: the 180° symmetry is the real blocker; Phase B should be a consensus gate + an asymmetric (building-side) tiebreaker rather than a drop-in ICP. Plan: [plans/F-SKY16-coastline-icp-heading.md](plans/F-SKY16-coastline-icp-heading.md). | `city2stl/skyline/coastline_registration.py`, `city2stl/skyline/region_pdf.py` | Medium | in-progress (Phase A done, measure-only) |
| F-SKY17 | Register Microsoft ML footprints to OSM before dedup (F-SKY8 fix). The OSM and MS sources are independently georeferenced, so the same building is positionally offset between them; the area-IoU≥0.5 dedup then fails to merge the pair and keeps both (visible as offset grey OSM + light-brown MS twins in the minimap, and a matcher hazard — it can lock onto the offset MS twin). Estimate the global offset as the median centroid displacement of near-neighbour OSM/MS pairs, translate all MS polygons by it, then run the existing IoU dedup. Implemented: `_estimate_ms_osm_offset` + `register_to_osm=True` default in `merge_satellite_into_osm`. Synthetic test confirms offset twins now collapse (20→10). Plan: [plans/F-SKY17-ms-osm-registration.md](plans/F-SKY17-ms-osm-registration.md). | `city2stl/skyline/satellite_footprints.py` | Small | implemented (2026-05-28), awaiting Cartagena run |
| F-SKY18 | Bearing landmarks — depth-snap + vegetation. Pano-projected coastline dots are radially wrong (monocular range error) but their bearings are exact. (A) Depth-snap: replace each point's range with the distance to the nearest OSM feature along its bearing so dots land on the real coast. (B) Add vegetation as a second landmark class (SegFormer green classes + OSM parks/grass/forest) for more, asymmetric bearing anchors to help the 180° heading symmetry. Plan: [plans/F-SKY18-vegetation-landmarks-depth-snap.md](plans/F-SKY18-vegetation-landmarks-depth-snap.md). | `city2stl/skyline/coastline_registration.py`, `pipeline.py`, `osm_water.py`, `region_pdf.py` | Large | in-progress (Phase 1) |
| F-SKY-PIPELINE | Consolidate F-SKY1..F-SKY13 into one canonical pipeline with each signal classified as core / opt-in / diagnostic-only. Phases 0–2 complete (see [docs/plans/F-SKY-PIPELINE-CONSOLIDATION.md](plans/F-SKY-PIPELINE-CONSOLIDATION.md)). Phase 3 (second region scaffolding: Miami + Chicago) and Phase 4 docs/tests partially remaining. | `city2stl/skyline/region_pdf.py`, `city2stl/skyline/README.md` | Medium | in-progress |
| F-CLEAN8 | Split `_seed_multiview_registration` into 5 named helpers (`_capture_pano_views`, `_recover_pano_heading`, `_recover_anchor_offset`, `_register_views`, `_stitch_pano_composite`). Orchestrator is now ~60 LOC calling them in sequence. Pure refactor — no behaviour change. | `city2stl/skyline/region_pdf.py` | Medium | done (2026-05-26) |
| F-CLEAN13 | Run the production pipeline end-to-end on `chicago.json` and `miami.json` and capture metrics + any breakage in STATUS.md (the new feature flags have never been exercised on these regions). | `city2stl/skyline/sites/chicago.json`, `city2stl/skyline/sites/miami.json`, `city2stl/skyline/STATUS.md` | Small | pending |
| F-CLEAN14 | Split the three over-large skyline files into focused modules — verbatim moves + re-exports, no behaviour change. **Phase H done** (html_report 2882→1217 + new report_plots 1710). **Phase R done** (region_pdf 6524→698; new region_types/region_config/region_data/streetview_io/seed_selection/region_render/pano_registration). Surfaced + fixed a latent `logger` NameError. **Phase P deferred** (in-place decomposition of 2 giant pipeline.py fns). Pending: one paid Cartagena smoke run. Plan: [plans/F-CLEAN14-skyline-file-split.md](plans/F-CLEAN14-skyline-file-split.md). | `city2stl/skyline/*.py` | Large | done — Phase H+R (2026-06-07); Phase P pending |

---

## Performance

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| P-PLANB-DEM | Off-thread DEM pixel loop — post `{values, lut}` to Worker, receive `ImageBitmap` | `dem/dem-main.js`, new `workers/dem-render-worker.js` | Medium | approved |
| P-PROJ-CACHE | Plate Carrée cache refactor — store raw (unprojected) raster for all layers (DEM, water, ESA, satellite, height); apply `project_grid` at response time rather than at write time. Removes `proj`/`cn` from all cache keys so one fetch serves every projection. See `docs/projections.md` for trade-off analysis. Target: 600×600 rasters. | `routers/terrain.py`, `routers/height.py`, `routers/composite.py`, `core/cache.py` | Large | approved |

---

## Refactoring / Code Cleanup

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| R-CLEAN1 | Replace remaining inline styles with CSS utility classes (index.html, misc JS) | `index.html`, `app.css`, various | Medium | approved |
| R-LAYER-LOAD | Shared `loadLayer(name, fetchFn, options)` wrapper in `ui-helpers.js` — consolidates `setLayerStatus` calls, error-toast handling, `isLayerCurrent()` guards, and stale canvas ref clearing. **Regression plan required before any code movement**: add explicit Vitest tests covering each layer's loading-state transitions (idle → loading → loaded / error) for `dem-main.js`, `water-mask.js`, `city-overlay.js`, `hydrology-overlay.js` before refactoring. | `app/client/static/js/modules/ui/ui-helpers.js`, `dem/dem-main.js`, `layers/water-mask.js`, `layers/city-overlay.js`, `layers/hydrology-overlay.js` | Large | approved |
| R-LAYERS | LayerBuffer class — unified canvas allocate/resize/dirty-track across all layer canvases | `layers/stacked-layers.js` | Large | approved |
| R-EVENTS-A | Event bus consolidation — add `EV.DEM_LOADED`, `EV.REGION_SELECTED`; replace direct `window.fn()` calls | `events/`, all modules | Large | approved |
| R-EVENTS-B | Keyboard shortcut registry — replace `keydown` switch with `window.registerShortcut(key, label, fn)` | `events/keyboard-shortcuts.js` | Small | approved |
| R-EVENTS-C | Debounce audit — gate `input` handlers where target takes >5ms | all modules | Small | approved |

---

## Architecture

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| A-SW | Service worker for API response caching — stale-while-revalidate for `/api/terrain/dem` and `/api/terrain/satellite` | new `sw.js` | Medium | pending |
| A-OBJ-TEX | OBJ cross-section export with UV map + PNG texture from current colormap | `app/server/core/export.py`, `export/export-handlers.js` | Large | denied |

---

## Backend

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| B-STREAM | Streaming STL generation — Python generators + `StreamingResponse` to reduce peak RAM | `app/server/core/export.py`, `app/server/routers/export.py` | Medium | approved |

---

## Completed

| ID | Description | Status |
|----|-------------|--------|
| F-UX2 | Text labels on floating map buttons | done |
| F-UX3 | Clarify sidebar 3-state toggle | done |
| F-REG1 | Region list pagination (20-per-page, search filter) | done |
| F-REG2 | Region import/export as JSON | done |
| F-UX1 | Consolidate region creation UI, draw-first empty state | done |
| F-UX-M | Lazy canvas allocation via LAYER_CANVAS_IDS registry | done |
| F-FEAT | Preset undo / revert snapshot | done |
| P-RAF | RAF-gate `applyCurveTodemSilent` | done |
| P-PERF6B | Web Worker for city polygon rendering | done |
| R-MAP2 | Bbox drag handle keyboard accessibility | done |
| B-LIB1 | Refactor cities_3d._terrain_mesh | done |
| B-LIB2 | Refactor _extrude_ring/_ear_clip | done |
| B-LIB3 | Fix legacy projection in dem.py | done |
| B-LIB4 | Use geo2stl scale calculation | done |
| B-LIB5 | Route terrain.py through core/dem | done |
| A-ARCH4 | Vite bundler setup | done |
| A-ARCH5 | Vitest unit tests for pure functions | done |
| B-OPENAPI | OpenAPI schema validation in dev | done |
| B-MULTI | Print-bed multi-piece export | done |
| F-EXP1 | Export progress indicator | done |
| F-CLEAN1 | Delete unreferenced config.py (123 LOC, superseded by JSON sites/) | done (2026-05-24) |
| F-CLEAN2 | Remove osm_marker_voronoi_silhouettes (F-SKY3 disabled; no callers) | done (2026-05-18) |
| F-CLEAN3 | Inline trivial _make_sky_mask_from_bool helper | done (already removed) |
| F-CLEAN4 | Gate F-SKY1 floor-period behind compute_floor_period=False default | done (2026-05-24) |
| F-CLEAN5 | Surface F-SKY10 cv score in per-view PDF header (cv̄=X.XX/min=Y.YY) | done (2026-05-24) |
| F-CLEAN6 | Delete pano_birdseye.py + scripts/13_birdseye_registration_demo.py | done (2026-05-24) |
| F-CLEAN7 | Consolidate 8 _load_site_* helpers into _read_site_config | done (already consolidated) |
| F-CLEAN9 | Rewrite STATUS.md top section with current run metrics | done (2026-05-24) |
| F-CLEAN10 | Archive cartagena-audit-2026-05.md (stale MAE≈151m) | done (2026-05-24) |
| F-CLEAN11 | Archive implementation-plan.md (all issues resolved) | done (2026-05-24) |
| F-CLEAN12 | Update glass-roof-height-fix-plan.md: Phase 1 complete | done (2026-05-24) |

---

## Skyline CV

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| SCV-1 | Trace one tall glass tower failure end-to-end (view → segment → y_px → predicted height) to diagnose the under-prediction root cause. Pick the worst Miami CTBUH miss (Marquis Miami pred=75 m vs survey=271 m); instrument `estimate_heights_from_registration` to emit the chosen y_px, matched building, forward_m, and implied height for that building on each seed view. | `city2stl/skyline/pipeline.py`, `city2stl/skyline/region_pdf.py` | Small | approved |
| SCV-2 | Fix heading registration multi-modal optima — add 180°-symmetric tie-breaker (if best and best+180° score within 10% of each other, use URL `h_token` heading to disambiguate) and optionally consume Photo Sphere pose metadata heading as a strong prior. Goal: eliminate the need for manual `anchor_offsets_deg` on 3/5 Cartagena seeds. | `city2stl/skyline/region_pdf.py` | Medium | approved |
| SCV-3 | Add a third city (`sites/newyork.json` or `sites/chicago.json`) with real Photo Sphere seed URLs targeting the dense downtown skyline. Validate that the pipeline produces ≥50 cross-seed buildings without any manual anchor overrides. | `city2stl/skyline/sites/`, `city2stl/skyline/region_pdf.py` | Medium | approved |

---

## Denied / Deferred

| ID | Reason |
|----|--------|
| F-P6 | Denied — multi-material band export adds complexity with limited demand. Standard STL/3MF export covers the use case. |
| A-OBJ-TEX | Denied — OBJ cross-section export with UV map + PNG texture. |
| F-SKY3 | Superseded — OSM-marker column Voronoi implemented and disabled after measurement (Cartagena MAE 17.28 → 22.13, tagged count 13 → 8). Function removed 2026-05-18 (F-CLEAN2). Replaced by F-SKY5 (MobileSAM). Plan preserved at [plans/F-SKY3-osm-marker-instances.md](plans/F-SKY3-osm-marker-instances.md). |
| F-SKY11.2 | Denied — Pano bird's-eye IPM registration attempted 2026-05-17, found not viable. Monocular SegFormer water has insufficient depth reach (~5–7m) vs bay scale (1km+). Code deleted 2026-05-24 (F-CLEAN6). Post-mortem at [plans/F-SKY11.2-pano-birdseye-registration.md](plans/F-SKY11.2-pano-birdseye-registration.md). |
