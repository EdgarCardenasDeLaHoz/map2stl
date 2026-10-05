# AI-Proposed Features & Tasks — map2stl

_Last updated: 2026-09-28_

> Completed, denied and superseded items are at the bottom of this file.
> The ordered list of open work is the roadmap, [plans/README.md](plans/README.md). This file
> tracks the life of an idea: proposed, then approved, then done.

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
| F-ARCH | Consolidate duplicated functionality into layered, reusable modules (`app → city2stl / geo2stl → numpy2stl`), plus the two-stage mesh pipeline (`export.terrain_stage` → `city_model.build_on_terrain`). User-approved 2026-09-26 / 2026-09-27. Plan: [plans/active/F-ARCH-consolidation.md](plans/active/F-ARCH-consolidation.md). | `geo2stl/*`, `city2stl/*`, `../numpy2stl/src/numpy2stl/*`, `app/server/core/export.py` | Large | in-progress: mostly done; engrave_text, write_mesh, utils.cache and the remaining burns are open |
| F-TREES | Tell buildings from trees, viaducts and terrain bumps in printed city models: region features (roughness, roof planarity, wall sharpness, shape) + gradient boosting on OSM labels from the registration packs, leave-one-city-out; a small U-Net only if that falls short. User-approved 2026-10-03. Plan: [plans/active/F-TREES-buildings-vs-vegetation.md](plans/active/F-TREES-buildings-vs-vegetation.md). | `../numpy2stl/src/numpy2stl/stl2numpy/buildings.py`, `tools/ml/`, `models/` | Medium | approved (user request) |
| F-NN-WATER | Satellite water / coastline segmentation trained against OSM water polygons, replacing the HSV detector (`skyline/coastline_registration.py::detect_sat_water_mask`, unreliable on dark water, boats, sediment). Small U-Net or a fine-tuned aerial segmentation model; GPU available. | `city2stl/skyline/coastline_registration.py`, `tools/ml/` | Medium | pending |
| F-NN-ROOF | Roof shape from satellite crops: fine-tune an ImageNet MobileNetV3 (the old `RoofNetV2` was removed by ML-3) on ~13k OSM `roof:shape` labels; beat the GBM's 0.79 AUC and the heuristic classifier's majority baseline. | `city2stl/roof_classifier.py`, `tools/` | Medium | pending |
| F-STL2NUMPY | Decompose 3D city models (STL/OBJ/3MF, part packs) into a surface heightmap, a slope-aware terrain heightmap, a building table (footprint, base, height, roof planes), and water/road layers, in `numpy2stl.stl2numpy`; reuse `mesh_to_heightmap`, `building_mask`, `vectorize_buildings`, `prism_decompose` and move `estimate_terrain` / `heightmap_to_polygons` / `plate_water_mask` out of tools/. User-requested 2026-10-03. Plan: [plans/active/F-STL2NUMPY-decompose.md](plans/active/F-STL2NUMPY-decompose.md). | `../numpy2stl/src/numpy2stl/stl2numpy/*`, `../numpy2stl/src/numpy2stl/io/readers.py`, `tools/align_tool/*` | Large | approved (user request) |
| F-CITYMODEL | Vector city model: DEM at 1 px/mm + 3×3 median; auto vertical scale; extruded, draped and cut layers merged with manifold3d into one watertight STL plus a per-layer 3MF; jigsaw puzzle from the merged solid. User-requested 2026-09-26. Plan: [plans/active/F-CITYMODEL-vector-city-model.md](plans/active/F-CITYMODEL-vector-city-model.md). | `city2stl/city_model.py`, `city2stl/model_cache.py`, `app/server/core/city_model_task.py`, `ModelContainer.vue` | Large | in-progress: mostly done; volume check, build spec and regression set are open |
| F-LANDMARK | Roofs (`city2stl/roofs.py`), `building:part` stacking, landmark overrides (nDSM / uploaded mesh), EU survey providers, Landmarks panel, model scoring. User-requested 2026-09-27. Plan: [plans/active/F-LANDMARK-roofs-and-building-parts.md](plans/active/F-LANDMARK-roofs-and-building-parts.md). | `city2stl/roofs.py`, `city2stl/landmarks.py`, `city2stl/height/providers/*`, `app/server/routers/cities.py` | Large | in-progress: §1–§6 done 2026-09-27; validation renders open |
| F-PROJ-EXPAND | Expand map projections to the MapChart guide set. Phases: cylindrical, then pseudocylindrical, then azimuthal/polyconic. User-requested 2026-07-19. Plan: [plans/done/F-PROJ-EXPAND-map-projections.md](plans/done/F-PROJ-EXPAND-map-projections.md). | `geo2stl/projections.py`, `DemSourceSection.vue`, `app/client/static/js/modules/dem/dem-gridlines.js` | Large | done: phase 1 (Miller, Gall); phases 2–3 not pursued (2026-09-29) |
| F-DEMID | DEM handle: `/api/terrain/dem` returns `dem_id`; exports use it instead of re-deriving the cache key. User-approved 2026-09-26. Plan: [plans/active/F-FE1-vue-consolidation.md](plans/active/F-FE1-vue-consolidation.md). | `app/server/core/dem_store.py`, `app/server/core/export_params.py`, `export-handlers.js` | Large | in-progress: handles issued and used by every export; derived-values handles, fallback removal and the degenerate guard are open |
| F-FE1 | One frontend: standardise on Vue (Pinia state, typed settings, components own the engines, no `window.*` glue). User-approved 2026-09-26. Plan: [plans/active/F-FE1-vue-consolidation.md](plans/active/F-FE1-vue-consolidation.md). | `app/client/static/js/**`, `vite.config.js`, `app/server/server.py` | Large | in-progress: step 7 (delete `v2/`) done 2026-09-27; steps 0–6 open |
| F-USAGE | Local usage log: clicks, control values, timings, map box, errors appended to `output/usage/*.jsonl` (local only, pausable) so UI work follows the real flow. User-requested 2026-09-30. Plan: [plans/active/F-USAGE-local-usage-log.md](plans/active/F-USAGE-local-usage-log.md). | `app/client/static/js/modules/core/usage-log.js`, `app/server/routers/usage.py` | Small | in-progress |
| F-DESIGN | Redesign Explore / Edit / Extrude to the shared design guidelines: Beginner / Custom / Everything mode, one ⚙ Settings panel grouped by task, autosave, magic button, one visual style; mockups approved before code. User-requested 2026-10-01. Plan: [plans/active/F-DESIGN-guidelines-redesign.md](plans/active/F-DESIGN-guidelines-redesign.md). | `MainHeader.vue`, `DemSettingsPanel.vue`, `ModelContainer.vue`, `app.css`, `presets.js` | Large | approved, in progress |
| F-REG3 | Region settings inheritance: a per-region "use global defaults" override. | `app/client/static/js/modules/regions/`, `app/server/routers/regions.py` | Medium | approved (not started) |

---

## Performance

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| P-PROJ-CACHE | Plate Carrée cache: store raw (unprojected) rasters and project at response time, so one fetch serves every projection. See [reference/projections.md](reference/projections.md). | `app/server/routers/composite.py`, `app/server/routers/height.py`, `app/server/core/cache.py` | Large | approved, partly done: DEM keys are projection-free; the composite cache key and height rasters still project per request |

---

## Refactoring / Code Cleanup

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| R-CLEAN1 | Replace remaining inline styles with CSS classes. | `app/client/templates/index.html`, `app.css`, modules | Medium | approved, partly done (~25 inline styles left) |
| R-LAYER-LOAD | Shared `loadLayer(name, fetchFn, options)` wrapper in `ui-helpers.js`. **Add Vitest tests of each layer's loading states before moving code.** | `app/client/static/js/modules/core/ui-helpers.js`, `app/client/static/js/modules/dem/dem-main.js`, `app/client/static/js/modules/layers/water-mask.js`, `app/client/static/js/modules/layers/city-overlay.js`, `app/client/static/js/modules/layers/hydrology-overlay.js` | Large | approved (not started) |
| R-LAYERS | LayerBuffer class: one allocate/resize/dirty-track for all layer canvases. | `app/client/static/js/modules/layers/stacked-layers.js` | Large | approved (not started) |
| R-EVENTS-A | Event bus: use `EV.DEM_LOADED` / `EV.REGION_SELECTED` (defined in `app/client/static/js/modules/core/events.js`, never emitted) instead of `window.fn()` calls and ad-hoc `CustomEvent`s. | `events/`, all modules | Large | approved, partly done |
| R-EVENTS-B | Keyboard shortcut registry (`window.registerShortcut(key, label, fn)`). | `app/client/static/js/modules/events/` | Small | approved (not started) |
| R-EVENTS-C | Debounce audit: gate `input` handlers where the target takes > 5 ms. | all modules | Small | approved, partly done (local debounces only) |
| A11Y-1 | Normalise sidebar and settings contrast tokens (the dark sidebar was never checked), then re-verify the accessibility audit ([history/audits/accessibility-audit.md](history/audits/accessibility-audit.md)). From `todos/` (2026-05-02). | `app/client/static/css/` | Small | approved, partly done |

---

## Architecture

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| A-SW | Service worker: stale-while-revalidate for `/api/terrain/dem` and `/api/terrain/satellite`. | new service worker | Medium | pending |

---

## Backend

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| B-STREAM | Streaming STL generation (generators + `StreamingResponse`) to cut peak RAM. | `app/server/core/export.py`, `app/server/routers/export.py` | Medium | approved (not started) |
| F-COMPOSITE3 | Server-side composite: ordered layer list, `add` blend, layer-source registry, per-layer projection, direct tests. User-requested 2026-09-06. Plan: [plans/active/F-COMPOSITE3-server-side-composite.md](plans/active/F-COMPOSITE3-server-side-composite.md). | `geo2stl/processing.py`, `geo2stl/dem.py`, `app/server/routers/composite.py`, `composite-dem.js` | Large | in-progress: pass 1 done 2026-09-06; pass 2 (land cover + parity test), pass 3 (satellite vegetation) open |

---

## Skyline CV

Open skyline work is listed once, in the
[skyline README → Open items](../city2stl/skyline/README.md#open-items).

| ID | Description | File(s) | Effort | Status |
|----|-------------|---------|--------|--------|
| F-SKYBENCH | Skyline height benchmark: per-footprint truth from survey lidar and Google 3D Tiles, cross-checked, on Miami, Chicago, Seattle, Boston, Benidorm, La Défense, Madrid, Prague; one scoring command; persisted seeds; baseline in STATUS. User-requested 2026-10-03. Plan: [plans/active/F-SKYBENCH-height-benchmark.md](plans/active/F-SKYBENCH-height-benchmark.md). | `city2stl/skyline/benchmark.py`, `scripts/10_benchmark.py`, `seed_selection.py`, `sites/*.json` | Medium | approved (user request) |
| F-DET | Detection quality & early-out. F-DET1/2/3/5 done; F-DET4a–c (Type 2 per-city fixes) pending, assumptions challenged 2026-06-23. Plan: [plans/active/F-DET-detection-quality-and-early-out.md](plans/active/F-DET-detection-quality-and-early-out.md). | `city2stl/skyline/_pano/`, `seed_selection.py`, `html_report.py` | Medium | in-progress |
| F-SKY5 | MobileSAM instance head gated on OSM markers. Implemented, opt-in; validation open. Plan: [plans/done/skyline/F-SKY5-mobilesam-instance.md](plans/done/skyline/F-SKY5-mobilesam-instance.md). | `city2stl/skyline/` | Large | done (opt-in); validation open |
| F-SKY13 | OSM-coastline registration and footprints overlay. Phases A–C landed; Phase C (behind `SKYLINE_CV_PHASE_C=1`) not validated. Plan: [plans/done/skyline/F-SKY13-osm-coastline-footprints-overlay.md](plans/done/skyline/F-SKY13-osm-coastline-footprints-overlay.md). | `city2stl/skyline/osm_water.py`, `coastline_registration.py` | Medium | done; Phase C validation open |
| F-SKY14 | Trained satellite-coastline detector supervised by OSM ground truth (replaces HSV `detect_sat_water_mask`). Any satellite-side detector MUST be trained against OSM. Defer until OSM-sparse regions come up. | `city2stl/skyline/coastline_registration.py` | Large | pending |
| F-SKY16 | Coastline-ICP heading registration. Phase A (measure-only) done; Phase B (consensus gate + asymmetric tiebreaker) open. Plan: [plans/done/skyline/F-SKY16-coastline-icp-heading.md](plans/done/skyline/F-SKY16-coastline-icp-heading.md). | `city2stl/skyline/coastline_registration.py` | Medium | Phase B open |
| F-SKY18 | Bearing landmarks: depth-snap + vegetation. Phase 3 open. Plan: [plans/done/skyline/F-SKY18-vegetation-landmarks-depth-snap.md](plans/done/skyline/F-SKY18-vegetation-landmarks-depth-snap.md). | `city2stl/skyline/coastline_registration.py`, `osm_water.py` | Large | Phase 3 open |
| SCV-1 | Trace one tall glass-tower failure end to end (worst Miami CTBUH miss, Marquis Miami 75 m vs 271 m). | `city2stl/skyline/_core/height.py` | Small | approved |
| SCV-3 | Third city with Photo Sphere seeds; ≥ 50 cross-seed buildings with no manual anchor overrides. `city2stl/skyline/sites/chicago.json` exists and ran; New York not added. | `city2stl/skyline/sites/` | Medium | approved, partly done |

---

## Completed

| ID | Description | Status |
|----|-------------|--------|
| F-UX | SOP workflow and UI follow-ups (Load DEM first, buildings panel, bed outline + scale, printer sizing, presets, landmark search, source resolution, background city fetch, pre-flight, decimated preview, fast puzzle). Leftovers are in the [roadmap](plans/README.md#frontend-and-ux-city-workflow). Plan: [plans/done/F-UX-sop-followups.md](plans/done/F-UX-sop-followups.md). | done (2026-09-27) |
| F-REGION | Large regions and rivers: HydroRIVERS / Natural Earth / lakes as terrain-stage sources, Region preset, size guard, three reference renders + SOP, registration in the UI. Plan: [plans/done/F-REGION-large-areas-hydrology.md](plans/done/F-REGION-large-areas-hydrology.md). | done (2026-09-27) |
| F-TRAILS1 | Ski and hiking trails layer (OSM + USFS). Now also a City Model layer. Plan: [plans/done/F-TRAILS1-ski-hiking-trails-layer.md](plans/done/F-TRAILS1-ski-hiking-trails-layer.md). | done (2026-08-27) |
| F-MESHIMPORT | Import STL/OBJ as a registered layer (manual + auto registration, mesh library). Plan: [plans/done/F-MESHIMPORT-stl-obj-layer-import.md](plans/done/F-MESHIMPORT-stl-obj-layer-import.md). | done (2026-07-19) |
| F-COMPOSITE2 | Composite DEM rebuild (DEM as a channel, city sub-layers, histograms, two-tier city gate, split view). Plan: [plans/done/F-COMPOSITE2-rebuild.md](plans/done/F-COMPOSITE2-rebuild.md). | done (2026-07-19) |
| F-PROJ-DIMS | Variable-dimension projection output (`maintain_dimensions` default off). Plan: [plans/done/F-PROJ-DIMS-variable-projection-output.md](plans/done/F-PROJ-DIMS-variable-projection-output.md). | done (2026-07-19) |
| F-CLEAN14 | Split the over-large skyline files into `_core/`, `_pano/`, `_report_plots/`, `_region_render/`. Plan: [plans/done/F-CLEAN14-skyline-file-split.md](plans/done/F-CLEAN14-skyline-file-split.md). | done (2026-06-23) |
| F-CLEAN13 | Run the skyline pipeline on Chicago and Miami; metrics in `city2stl/skyline/docs/STATUS.md`. | done |
| SCV-2 | 180° heading tiebreaker (asymmetric tiebreak in `city2stl/skyline/_pano/detect.py`). | done |
| F-CLEAN8 | Split `_seed_multiview_registration` into 5 named helpers. | done (2026-05-26) |
| F-SKY1, 2, 4, 6, 7, 8, 10, 11, 11.1, 12, 15 | Skyline signals (floor periodicity, OSM-anchored segments, mask overlay, 1:1 matching, local-max peaks, satellite footprints, cross-view check, coastline alignment, depth from panos, HTML report). Plans: [plans/done/skyline/](plans/done/skyline/F-SKY1-floor-periodicity.md) (one file per ID); status table in the [skyline README](../city2stl/skyline/README.md#feature-status). | done (2026-05 to 2026-06) |
| F-SKY-PIPELINE | Consolidate F-SKY1..13 into one pipeline (core / opt-in / diagnostic). Plan archived: [plans/archive/skyline/F-SKY-PIPELINE-CONSOLIDATION.md](plans/archive/skyline/F-SKY-PIPELINE-CONSOLIDATION.md). | superseded (2026-06) |
| F-SKY17 | Register Microsoft footprints to OSM before dedup. Plan archived: [plans/archive/skyline/F-SKY17-ms-osm-registration.md](plans/archive/skyline/F-SKY17-ms-osm-registration.md). | failed, archived |
| P-PLANB-DEM | Off-thread DEM pixel loop (`app/client/static/js/workers/dem-render-worker.js`). | done |
| ML-1 | Height provider wiring (`city2stl/height/` registry, `/api/height/*`). From `todos/`. | done |
| ML-2 | Tall-building accuracy gap of the CNN height model. Superseded: no CNN is wired at runtime; tall buildings come from Google 3D Tiles / Overture (see [issues.md](issues.md) §0c). From `todos/`. | superseded |
| ML-3 | Remove legacy RoofNet-era training code: `tools/ml/` (the four live `eval_*.py` scripts moved to `tools/eval/`), `city2stl/roof_nets.py` and the roof classifier's CNN tier. `city2stl/height/predict.py` / `train.py` stay while the SDK still imports them. Why: [decisions/ml-height.md](decisions/ml-height.md). | done (2026-10-05) |
| TEST-1 | Tests for export and cities endpoints (`tests/test_export.py`, `test_cities.py`, `test_puzzle_export.py`). From `todos/`. | done |
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
| F-CLEAN6 | Delete the pano bird's-eye module and its demo script | done (2026-05-24) |
| F-CLEAN7 | Consolidate 8 _load_site_* helpers into _read_site_config | done (already consolidated) |
| F-CLEAN9 | Rewrite STATUS.md top section with current run metrics | done (2026-05-24) |
| F-CLEAN10 | Archive cartagena-audit-2026-05.md (stale MAE≈151m) | done (2026-05-24) |
| F-CLEAN11 | Archive implementation-plan.md (all issues resolved) | done (2026-05-24) |
| F-CLEAN12 | Update glass-roof-height-fix-plan.md: Phase 1 complete | done (2026-05-24) |

---

## Denied / Deferred

| ID | Reason |
|----|--------|
| F-P6 | Denied — multi-material band export adds complexity with limited demand. Standard STL/3MF export covers the use case. |
| A-OBJ-TEX | Denied — OBJ cross-section export with UV map + PNG texture. |
| F-SKY3 | Superseded — OSM-marker column Voronoi implemented and disabled after measurement (Cartagena MAE 17.28 → 22.13, tagged count 13 → 8). Function removed 2026-05-18 (F-CLEAN2). Replaced by F-SKY5 (MobileSAM). Plan preserved at [plans/archive/skyline/F-SKY3-osm-marker-instances.md](plans/archive/skyline/F-SKY3-osm-marker-instances.md). |
| F-SKY11.2 | Denied — Pano bird's-eye IPM registration attempted 2026-05-17, found not viable. Monocular SegFormer water has insufficient depth reach (~5–7m) vs bay scale (1km+). Code deleted 2026-05-24 (F-CLEAN6). Post-mortem at [plans/archive/skyline/F-SKY11.2-FAILURE-ANALYSIS.md](plans/archive/skyline/F-SKY11.2-FAILURE-ANALYSIS.md). |
