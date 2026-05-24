# AI-Proposed Features & Tasks — strm2stl

_Last updated: 2026-05-17_

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
| F-EXP1 | Export progress indicator — spinner/bar during STL generation (poll `/api/export/status` or streaming) | `export/export-handlers.js` | Medium | done |
| F-REG1 | Region list pagination — virtual scroll or 20-per-page for 50+ regions | `regions/region-ui.js` | Medium | done |
| F-REG2 | Region import/export — download all as `regions.json`; import via file picker | `regions/regions.js`, `regions/region-ui.js` | Small | done |
| F-REG3 | Region settings inheritance — "use global defaults" override per region | `regions/regions.js`, `app/server/routers/regions.py` | Medium | approved |
| F-UX1 | Consolidate region creation — keep only `floatingDrawBtn`; add empty-state hint to panel | `map/`, `index.html`, `events/event-listeners.js` | Small | done |
| F-UX-M | Lazy-allocate hidden layer canvases — create/destroy canvas elements on show/hide | `layers/stacked-layers.js`, `index.html` | Medium | done |
| F-FEAT | Preset undo — snapshot slider values before loading a preset; expose `window.revertPreset()` | `ui/presets.js` | Small | done |
| F-SKY1 | Floor-strip periodicity for skyline_cv height + distance estimation — detect the per-floor horizontal banding in a building's mask region (1D FFT / autocorrelation on a row-mean intensity profile), report the dominant pixel period, and back out floor count, height, and an independent distance check. Plan: [plans/F-SKY1-floor-periodicity.md](plans/F-SKY1-floor-periodicity.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Medium | in-progress |
| F-SKY2 | OSM-anchored silhouette splitting/merging — use projected OSM footprint x-ranges as structural priors to split mask-merged segments (one wide silhouette covering 3 adjacent towers → 3 segments) and merge over-split ones (two contour peaks on one tower → 1 segment). Fixes the matcher's failure mode where SegFormer merges adjacent buildings in dense skylines and the segment count drops below the OSM count, making 1:1 matching impossible. Plan: [plans/F-SKY2-osm-anchored-segments.md](plans/F-SKY2-osm-anchored-segments.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Medium | in-progress |
| F-SKY3 | OSM-marker column Voronoi for instance indexing — SegFormer is semantic-only (no instance head), so adjacent buildings merge into one mask blob. Heuristic Voronoi over OSM marker x_px was implemented and **disabled after measurement** (regressed Cartagena MAE 17.28 → 22.13, tagged count 13 → 8 — unconditional splitting was too aggressive). Function `osm_marker_voronoi_silhouettes` remains on the module surface for A/B; call site in region_pdf.py is commented out. Replacement direction: dedicated small instance-segmentation model (MobileSAM ~10M params or TinySAM ~5M, using OSM centroids as point prompts) — see F-SKY5. Plan: [plans/F-SKY3-osm-marker-instances.md](plans/F-SKY3-osm-marker-instances.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Medium | superseded |
| F-SKY4 | SegFormer building-mask overlay on per-view PDF pages — semi-transparent colour overlay of the raw building mask on top of the Street View image so the user can visually verify "is the segmentation working before any post-processing?" Helps separate "model misclassified" failures from "matcher dropped" failures. No model change, no pipeline behavioural change — pure rendering. Plan: [plans/F-SKY4-mask-overlay.md](plans/F-SKY4-mask-overlay.md). | `city2stl/skyline_cv/region_pdf.py` | Small | in-progress |
| F-SKY8 | Satellite-derived building footprints as a second polygon source — fetch Microsoft Global ML Building Footprints (or Google Open Buildings) for the region's bbox, parse to BuildingRecord shape, de-duplicate against OSM polygons by overlap, and pass the merged set into the existing matcher. Fixes the central seed_5 gap on Cartagena (the cyan-mask panel from F-SKY7 shows obvious towers there but OSM has no polygons for them, so the matcher has nothing to assign). No model change, no pipeline algorithm change — pure data enrichment that the existing F-SKY2 / F-SKY6 machinery automatically picks up. Plan: [plans/F-SKY8-satellite-footprints.md](plans/F-SKY8-satellite-footprints.md). | `city2stl/skyline_cv/region_pdf.py`, new `city2stl/skyline_cv/satellite_footprints.py` | Medium | in-progress |
| F-SKY10 | Non-ML cross-view registration (skyline ↔ satellite, no learned features) — geometric + appearance verification step that scores each candidate match by how well the building's satellite-roof colour/texture matches its street-view-roof colour/texture, plus geometric consistency checks (polygon edges' projected lengths vs detected facade widths). Classical CV, no deep models. Acts as an independent reranking signal alongside the IoU/containment matcher. Plan: [plans/F-SKY10-non-ml-cross-view-registration.md](plans/F-SKY10-non-ml-cross-view-registration.md). | `city2stl/skyline_cv/pipeline.py` + new helpers | Large | in-progress |
| F-SKY11 | Image-level coastline-alignment registration via radial water signatures + numbered coastline keypoints — detects water in both views (HSV for ESRI satellite, SegFormer for SV), builds a per-bearing radial water-distance signature from the seed, detects water→land transition key points, sweeps the candidate heading and reports the alignment score. Diagnostic-only at this point; the same numbered key points appear in both the satellite (top-down) and street view (side-on) so the user can visually verify the heading recovery. Standalone demo at `scripts/11_coastline_demo.py`. | `city2stl/skyline_cv/coastline_registration.py`, `scripts/11_coastline_demo.py` | Medium | in-progress |
| F-SKY11.1 | Pano-level coastline alignment — single global heading-offset recovery from the stitched 360° pano + water mask vs the F-SKY11 coastline keypoints. Replaces F-SKY11's 12 independent per-view best-heading searches with one offset solve that uses all 24 keypoints simultaneously. Aims to obsolete the manual `anchor_offsets_deg` overrides Cartagena currently relies on. Phase A landed 2026-05-17 (demo script + scorer; Cartagena seed_5 recovers 310° vs manual 320°). Phase B pending. Plan: [plans/F-SKY11.1-pano-coastline-alignment.md](plans/F-SKY11.1-pano-coastline-alignment.md). | `city2stl/skyline_cv/coastline_registration.py`, `scripts/12_pano_coastline_demo.py`, `city2stl/skyline_cv/region_pdf.py` (Phase B) | Medium | in-progress |
| F-SKY11.2 | Pano bird's-eye registration via inverse perspective mapping — rotate IPM canvas until monocular water mask matches satellite water mask (IoU). **Attempted 2026-05-17, found not viable.** Monocular SegFormer water has insufficient depth reach (~5–7m) vs bay scale (1km+); IoU rotation search produces flat signal. Continue with F-SKY11.1 instead. Failure analysis: [plans/F-SKY11.2-FAILURE-ANALYSIS.md](plans/F-SKY11.2-FAILURE-ANALYSIS.md). Code retained for reference in `city2stl/skyline_cv/pano_birdseye.py`, `scripts/13_birdseye_registration_demo.py`. | `city2stl/skyline_cv/pano_birdseye.py`, `scripts/13_birdseye_registration_demo.py` | Medium | denied |
| F-SKY-PIPELINE | Consolidate F-SKY1..F-SKY11.1 into one canonical pipeline with each signal classified as core / opt-in / diagnostic-only. **Status snapshot in [docs/F-SKY-INTEGRATION.md](F-SKY-INTEGRATION.md)** (consolidated May 17). Refactor lands in phases as F-SKY11.1 Phase B + pending features + config unification. | `city2stl/skyline_cv/region_pdf.py`, `city2stl/skyline_cv/README.md` | Medium | in-progress |
| F-SKY15 | HTML diagnostic report alongside the PDF — adds a per-seed HTML page that mirrors what the PDF shows but as a navigable DOM (Leaflet minimap, inline tables of `SeedViewRegistration` fields, toggleable overlays). Same data sources as the PDF (no second source of truth — just a second renderer). Keeps the PDF path untouched. Motivation: AI iteration speed (HTML is greppable and diffable; PDF rendering is opaque), plus on-the-fly layer toggling and per-segment hover tooltips. Output goes to `output/skyline_cv/<region>/seed_<n>.html` (one file per seed, plus an `index.html` linking them). Plan: [plans/F-SKY15-html-diagnostic-report.md](plans/F-SKY15-html-diagnostic-report.md). | `city2stl/skyline_cv/html_report.py` (new), `city2stl/skyline_cv/region_pdf.py` (call site only) | Medium | in-progress |
| F-SKY14 | Trained satellite-coastline detector supervised by OSM ground truth — replaces the heuristic HSV-threshold `detect_sat_water_mask` (deemed unreliable, see [feedback memory](../../../.claude/projects/c--Users-eac84-OneDrive-Documents-Projects-3D-Maps-Code/memory/feedback_satellite_coastline_hsv_unreliable.md)) with a small CNN that segments water vs land in satellite tiles, trained on OSM `natural=coastline` linestrings (rasterised onto matching satellite tiles for supervision). Needed only when OSM coastline coverage is sparse (rural / remote / outdated tiles) so the satellite imagery still has to provide the coastline. Until then, F-SKY13's OSM-primary path covers all coastal seeds we care about. Constraint from user: any satellite-side detector MUST be trained against OSM, not heuristic. No plan filed yet — defer until OSM-sparse regions are encountered in practice. | `city2stl/skyline_cv/coastline_registration.py` (replace `detect_sat_water_mask`), training scripts under `scripts/`, new model under `models/` | Large | pending |
| F-SKY13 | OSM-coastline registration on pano + footprints-view overlay — uses **OSM `natural=coastline` / `natural=water` polygons** as the **primary** registration ground truth for pano↔geography alignment. Supersedes the unreliable HSV satellite coastline currently used by F-SKY11.1 (demoted to opt-in debug fallback; see feedback memory). Restricts the consideration window to 1 km radius from the seed. Renders the OSM coastline + pano-projected coastline on the per-seed footprints minimap, with a satellite image background under the polygons, so the user can visually verify pano↔OSM agreement. Plan: [plans/F-SKY13-osm-coastline-footprints-overlay.md](plans/F-SKY13-osm-coastline-footprints-overlay.md). Future: a trained satellite-coastline detector supervised by OSM ground truth is tracked separately (proposed as F-SKY14, not yet filed). | `city2stl/skyline_cv/coastline_registration.py`, `city2stl/skyline_cv/region_pdf.py`, new `city2stl/skyline_cv/osm_water.py` | Medium | in-progress |
| F-SKY12 | Depth Anything V2 on Street View panos for height cross-verification — run DA2 (already used in `city2stl/height/predict.py` for satellite tiles) on each spin view, calibrate relative depth to metres using the known anchor distance to OSM buildings, and use the depth-derived height as an independent second estimate against the pinhole-y geometric height. Acts as a verifier — flags or downweights matches where the two estimates disagree by > threshold. Phase A (this round): verifier only, no behavioural change to geometric height extraction. Plan: [plans/F-SKY12-depth-from-panos.md](plans/F-SKY12-depth-from-panos.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Medium | in-progress |
| F-SKY7 | Local-maxima peak detection in contour + per-view page layout refactor — `detect_building_silhouettes` currently splits only at sky-valley peaks; a contiguous SegFormer mask covering a row of glass towers shows a monotone-but-bumpy contour with no sky breaks, and no segments are emitted there (observed on Cartagena seed_5 page 31: 34° / 259 px central gap with clearly-visible towers). Add a local-maxima pass on the contour itself (relative to a smoothed baseline) so monotone bumpy rooflines get carved into per-tower segments. Also refactor the per-view PDF page: separate the SegFormer mask into its own plot underneath the image (currently overlaid as cyan tint), remove the unused diagnostic legend table, reclaim its space. Plan: [plans/F-SKY7-local-max-peaks-and-layout.md](plans/F-SKY7-local-max-peaks-and-layout.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Medium | in-progress |
| F-SKY6 | One-to-one segment-to-building assignment + all-projections diagnostic overlay — the current matcher picks each segment's best candidate independently, allowing two adjacent segments to claim the same OSM building (observed on Cartagena seed_5 page 31: segments 1 & 2 both match b0268). Add a global 1:1 constraint (Hungarian over the (combined) scores) so each building wins at most one segment. Also surface ALL projections (not just the matched ones) as faint background markers on each per-view minimap so we can see candidates the matcher rejected — direct debugging aid for cases like seed_5 page 31's 259-px central gap. Plan: [plans/F-SKY6-one-to-one-matching.md](plans/F-SKY6-one-to-one-matching.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Medium | in-progress |
| F-SKY5 | MobileSAM instance head gated on OSM markers — replaces F-SKY3's failed Voronoi heuristic with a real small instance-segmentation model. MobileSAM (~10 M params) takes the cached Street View image plus the OSM-projected centroids of buildings in a merged mask blob as point prompts and returns per-instance masks with confidence scores. Fires only when SegFormer produces a merged blob with ≥ 2 OSM markers (the cases where F-SKY2 gap-splits and the matcher's containment fallback don't already solve it). Total model footprint: SegFormer-b0 + MobileSAM ≈ 14 M params, vs 632 M for original SAM. Plan: [plans/F-SKY5-mobilesam-instance.md](plans/F-SKY5-mobilesam-instance.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py`, `requirements*.txt` | Large | pending |
| F-CLEAN1 | Delete unreferenced `city2stl/skyline_cv/config.py` (123 LOC dataclasses + factories, superseded by JSON sites/ system). Cleanup audit: [plans/F-SKY-AUDIT-2026-05-17.md](plans/F-SKY-AUDIT-2026-05-17.md). | `city2stl/skyline_cv/config.py` | Trivial | pending |
| F-CLEAN2 | Remove `osm_marker_voronoi_silhouettes` (F-SKY3, disabled by measurement 2026-05-16, no remaining callers). Cleanup audit: [plans/F-SKY-AUDIT-2026-05-17.md](plans/F-SKY-AUDIT-2026-05-17.md). | `city2stl/skyline_cv/pipeline.py` | Small | pending |
| F-CLEAN3 | Inline trivial `_make_sky_mask_from_bool` helper (single-call wrapper, 12 LOC). | `city2stl/skyline_cv/pipeline.py` | Trivial | pending |
| F-CLEAN4 | Either surface F-SKY1 floor-period (`floor_period_px`, `inferred_distance_m`, etc.) in the per-view PDF audit table, OR gate the compute behind a `compute_floor_period=False` default. Currently computed per-segment per-view and never read. Cleanup audit: [plans/F-SKY-AUDIT-2026-05-17.md](plans/F-SKY-AUDIT-2026-05-17.md). | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Small | pending |
| F-CLEAN5 | Surface F-SKY10 `cv` score (color/width/edges + combined) in the per-view PDF audit table. All three signals now exist in `cross_view.py` (2026-05-17) but the renderer doesn't show the field. | `city2stl/skyline_cv/region_pdf.py` | Small | pending |
| F-CLEAN6 | Delete `pano_birdseye.py` + `scripts/13_birdseye_registration_demo.py` (F-SKY11.2 failed experiment, 672 LOC). Plan + post-mortem already preserved in [plans/F-SKY11.2-pano-birdseye-registration.md](plans/F-SKY11.2-pano-birdseye-registration.md). | `city2stl/skyline_cv/pano_birdseye.py`, `city2stl/skyline_cv/scripts/13_birdseye_registration_demo.py` | Small | pending |
| F-CLEAN7 | Consolidate the 8 copy-pasted `_load_site_*` helpers in `region_pdf.py` into one parameterised `_load_site_value(region_name, key, default)` + typed wrappers. ~150 LOC of boilerplate. | `city2stl/skyline_cv/region_pdf.py` | Small | pending |
| F-CLEAN8 | Split `_seed_multiview_registration` (918 LOC) into ~5 named helpers (capture, pitch-correct, anchor-recover, register-each-view, stitch-pano). Largest function in the module; obvious responsibility splits exist. | `city2stl/skyline_cv/region_pdf.py` | Medium | pending |
| F-CLEAN9 | Rewrite `city2stl/skyline_cv/STATUS.md` top section to reflect current run metrics (seed_extracted ~593, override status: seed_1 droppable per Path B measurement) instead of the old MAE-19m baseline. Convert the Phase A–D roadmap to a completed-work archive linking the relevant F-SKY*.md plans. | `city2stl/skyline_cv/STATUS.md` | Small | pending |
| F-CLEAN10 | Archive `city2stl/skyline_cv/docs/cartagena-audit-2026-05.md` (78 LOC, pre-aerial-detection era, cites stale MAE≈151m). Move to `docs/archive/` or delete. | `city2stl/skyline_cv/docs/cartagena-audit-2026-05.md` | Trivial | pending |
| F-CLEAN11 | Archive `city2stl/skyline_cv/docs/implementation-plan.md` (158 LOC; all listed issues resolved). | `city2stl/skyline_cv/docs/implementation-plan.md` | Trivial | pending |
| F-CLEAN12 | Update `city2stl/skyline_cv/docs/glass-roof-height-fix-plan.md` header to note Phase 1 complete (height_trace shipped); Phase 2 deferred. | `city2stl/skyline_cv/docs/glass-roof-height-fix-plan.md` | Trivial | pending |
| F-CLEAN13 | Run the production pipeline end-to-end on `chicago.json` and `miami.json` and capture metrics + any breakage in STATUS.md (the new feature flags have never been exercised on these regions). | `city2stl/skyline_cv/sites/chicago.json`, `city2stl/skyline_cv/sites/miami.json`, `city2stl/skyline_cv/STATUS.md` | Small | pending |

---

## Performance

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| P-PERF6B | Web Worker for city polygon rendering (Part A — Float32Array buffers; Part B — OffscreenCanvas) | `layers/city-render.js`, `workers/city-worker.js` | Large | done |
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
| B-MULTI | Print-bed multi-piece export — auto-tile large DEMs into N×M pieces with alignment tabs | `app/server/core/export.py`, `export/export-handlers.js` | Large | done |
| B-OPENAPI | OpenAPI schema validation in dev — auto-generate JSON Schema from `/openapi.json`; validate in `api.js` | `core/api.js` | Small | done |

---

## Library Integration Debt

See [arch.md](arch.md) for the full import map and thin-wrapper assessment.

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| B-LIB1 | Refactor `cities_3d._terrain_mesh` → `numpy2stl.array_to_mesh(solid=True)` — ~90 lines of duplicate mesh generation | `app/server/core/cities_3d.py`, `numpy2stl/generate.py` | High | done |
| B-LIB2 | Refactor `cities_3d._extrude_ring` / `_ear_clip` → `numpy2stl.polygon_to_prism` + `numpy2stl.polygon.triangulate_polygon` | `app/server/core/cities_3d.py`, `numpy2stl/polygon.py`, `numpy2stl/generate.py` | High | done |
| B-LIB3 | Fix legacy projection in `dem.py:compute_raw_dem` → use `core/projection.project_grid` instead of raw `geo2stl.projections.proj_map_geo_to_2D` | `app/server/core/dem.py`, `app/server/core/projection.py` | Medium | done |
| B-LIB4 | Use `geo2stl.sat2stl.calculate_scale_for_dimensions` in `core/sat.py` — currently computes tile scale manually | `app/server/core/sat.py`, `geo2stl/sat2stl.py` | Medium | done |
| B-LIB5 | Route `terrain.py` `make_dem_image` import through `core/dem` — router bypasses core layer | `app/server/routers/terrain.py`, `app/server/core/dem.py` | Small | done |

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

---

## Skyline CV

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| SCV-1 | Trace one tall glass tower failure end-to-end (view → segment → y_px → predicted height) to diagnose the under-prediction root cause. Pick the worst Miami CTBUH miss (Marquis Miami pred=75 m vs survey=271 m); instrument `estimate_heights_from_registration` to emit the chosen y_px, matched building, forward_m, and implied height for that building on each seed view. | `city2stl/skyline_cv/pipeline.py`, `city2stl/skyline_cv/region_pdf.py` | Small | approved |
| SCV-2 | Fix heading registration multi-modal optima — add 180°-symmetric tie-breaker (if best and best+180° score within 10% of each other, use URL `h_token` heading to disambiguate) and optionally consume Photo Sphere pose metadata heading as a strong prior. Goal: eliminate the need for manual `anchor_offsets_deg` on 3/5 Cartagena seeds. | `city2stl/skyline_cv/region_pdf.py` | Medium | approved |
| SCV-3 | Add a third city (`sites/newyork.json` or `sites/chicago.json`) with real Photo Sphere seed URLs targeting the dense downtown skyline. Validate that the pipeline produces ≥50 cross-seed buildings without any manual anchor overrides. | `city2stl/skyline_cv/sites/`, `city2stl/skyline_cv/region_pdf.py` | Medium | approved |

---

## Denied / Deferred

| ID | Reason |
|----|--------|
| F-P6 | Denied — multi-material band export adds complexity with limited demand. Standard STL/3MF export covers the use case. |
| A-OBJ-TEX | Denied — OBJ cross-section export with UV map + PNG texture. |
