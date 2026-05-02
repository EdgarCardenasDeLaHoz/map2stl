# Linked Todo Index

_Last updated: 2026-04-30_

Completed and open work items linked to their detail docs.

| Status | Todo | Detail Links |
|---|---|---|
| done | Web Worker Part B (OffscreenCanvas) | [proposals.md](proposals.md), [layers TODO](../app/client/static/js/modules/layers/TODO.md), [ux-audit.md](ux-audit.md), [open-todo-plans.md](open-todo-plans.md#1-web-worker-part-b--offscreencanvas-perf6b-continuation) |
| done | Curve editor bugs and presets versioning | [ui TODO](../app/client/static/js/modules/ui/TODO.md), [open-todo-plans.md](open-todo-plans.md#2-curve-editor-bugs--presets-versioning) |
| done | Refactor cities_3d._terrain_mesh (B-LIB1) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Refactor _extrude_ring/_ear_clip (B-LIB2) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Fix legacy projection in dem.py (B-LIB3) | [proposals.md](proposals.md), [projections.md](projections.md), [issues.md](issues.md) |
| done | Use geo2stl scale calculation (B-LIB4) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Route terrain.py through core/dem (B-LIB5) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Export progress indicator (EXP-1) | [export TODO](../app/client/static/js/modules/export/TODO.md), [proposals.md](proposals.md) |
| done | Replace inline styles with CSS (CLEAN-1) | [ui TODO](../app/client/static/js/modules/ui/TODO.md), [proposals.md](proposals.md), [../TODO.md](../TODO.md), [open-todo-plans.md](open-todo-plans.md#3-replace-inline-styles-with-css-clean-1--r-clean1) |
| done | Lazy-allocate hidden layer canvases (UX-M) | [layers TODO](../app/client/static/js/modules/layers/TODO.md), [proposals.md](proposals.md), [ux-audit.md](ux-audit.md), [layer_system_analysis.md](layer_system_analysis.md), [open-todo-plans.md](open-todo-plans.md#4-lazy-allocate-hidden-layer-canvases-ux-m) |
| done | Consolidate region creation entry (UX-1) | [map TODO](../app/client/static/js/modules/map/TODO.md), [proposals.md](proposals.md), [ux-audit.md](ux-audit.md), [open-todo-plans.md](open-todo-plans.md#5-consolidate-region-creation-entry-point-ux-1) |
| done | Region list pagination (REG-1) | [regions TODO](../app/client/static/js/modules/regions/TODO.md), [proposals.md](proposals.md), [open-todo-plans.md](open-todo-plans.md#6-region-list-pagination-reg-1) |
| done | Region import/export JSON (REG-2) | [regions TODO](../app/client/static/js/modules/regions/TODO.md), [proposals.md](proposals.md) |
| done | Event bus consolidation (R-EVENTS-A) | [events TODO](../app/client/static/js/modules/events/TODO.md), [proposals.md](proposals.md), [layer_system_analysis.md](layer_system_analysis.md), [open-todo-plans.md](open-todo-plans.md#7-event-bus-consolidation-r-events-a) |
| done | Height pipeline Phase 1a (4 providers + router + merge) | [height-pipeline-plan.md](height-pipeline-plan.md), [3D_plan1.md](../app/server/core/3D_plan1.md) |
| done | Height pipeline Phase 1b (GHSL, Open Buildings, shadow) | [height-pipeline-plan.md](height-pipeline-plan.md) |
| done | Height pipeline Phase 3 (STL import + IDW infill) | [height-pipeline-plan.md](height-pipeline-plan.md) |
| done | Composite DEM Phase 2 (city rasterization) | [composite_dem_design.md](composite_dem_design.md), [height-pipeline-plan.md](height-pipeline-plan.md), [open-todo-plans.md](open-todo-plans.md#8-composite-dem-phase-2--city-rasterization) |
| done | Off-thread DEM pixel rendering (Plan A) | [dem TODO](../app/client/static/js/modules/dem/TODO.md), [proposals.md](proposals.md), [open-todo-plans.md](open-todo-plans.md#9-off-thread-dem-pixel-rendering-plan-a) |
| done | Bbox coord validation bugs (5 issues) | [bbox_editing_options.md](bbox_editing_options.md) |
| done | Print-bed multi-piece export (B-MULTI) | [proposals.md](proposals.md) |
| done | OpenAPI schema validation in dev (B-OPENAPI) | [proposals.md](proposals.md) |
| done | Google 3D height raster → city buildings integration | [proposals.md](proposals.md), [height-pipeline-plan.md](height-pipeline-plan.md) |
| done | Chrome DevTools perf profile (P0) | [ux-audit.md](ux-audit.md), [open-todo-plans.md](open-todo-plans.md#10-chrome-devtools-perf-profile-p0) |
| done | Preserve OSM roof tags + write inferred heights back (ROOF-1) | [building-roof-pipeline-plan.md](building-roof-pipeline-plan.md#phase-1-preserve-osm-roof-tags--write-heights-back-roof-1) |
| done | Roof shape detection from satellite imagery (ROOF-2) | [building-roof-pipeline-plan.md](building-roof-pipeline-plan.md#phase-2-roof-shape-detection----satellite-inference-roof-2), [roof-ml-architecture.md](roof-ml-architecture.md) (detailed ML plan) |
| done | Non-flat roof mesh generation (ROOF-3) | [building-roof-pipeline-plan.md](building-roof-pipeline-plan.md#phase-3-non-flat-roof-mesh-generation-roof-3) |
| done | Open Buildings v3 real fetch path | [open-todo-plans.md](open-todo-plans.md#11-open-buildings-v3-real-fetch-path) |
| done | Shadow height inference pipeline | [open-todo-plans.md](open-todo-plans.md#12-shadow-height-actual-inference-pipeline) |
| done | Plate Carree cache refactor (P-PROJ-CACHE) | [open-todo-plans.md](open-todo-plans.md#13-plate-carree-cache-refactor-p-proj-cache) |
| done | Slanted roofs in city heights raster (F-ROOF1) | [building-roof-pipeline-plan.md](building-roof-pipeline-plan.md) |
| done | First-principles height model (Retna_V1) replaces RoofNetV3 | [plans/height-training-status.md](plans/height-training-status.md) |
| done | ML pipeline cleanup (3 active checkpoints; 50+ failed deleted) | [plans/height-training-status.md](plans/height-training-status.md) |
| done | Unified ML CLI driver (`tools/ml/pipeline.py`) | [ml-pipeline.md](ml-pipeline.md) |
| open | Wire Retna provider into height-fetch pool | [height-pipeline-improvement-plan.md](height-pipeline-improvement-plan.md) |
| open | Close tall-building MAE gap (currently 13–16m on skyscrapers) | [plans/height-training-status.md](plans/height-training-status.md) |

## Notes By Item

1. Web Worker Part B (OffscreenCanvas)
   The worker follow-up is tracked in both [proposals.md](proposals.md) and the rendering notes in [layers TODO](../app/client/static/js/modules/layers/TODO.md). [ux-audit.md](ux-audit.md) also records the broader performance context and current worker status.

2. Curve editor bugs and presets versioning
   [ui TODO](../app/client/static/js/modules/ui/TODO.md) is the main detail source. It contains the preset undo idea and the versioning/migration note for old saved presets.

3. Refactor cities_3d._terrain_mesh (B-LIB1)
   [proposals.md](proposals.md) defines the intended refactor target and effort. [libraries.md](libraries.md) explains the library-wrapper goal and the current duplication/delegation boundaries.

4. Refactor _extrude_ring/_ear_clip (B-LIB2)
   [proposals.md](proposals.md) links this to `numpy2stl` prism/triangulation helpers. [libraries.md](libraries.md) is the best supporting reference for the relevant public APIs.

5. Fix legacy projection in dem.py (B-LIB3)
   [projections.md](projections.md) is the best background reference for projection behavior; [proposals.md](proposals.md) and [issues.md](issues.md) record the original debt item.

6. Use geo2stl scale calculation (B-LIB4)
   [proposals.md](proposals.md) holds the task definition. [libraries.md](libraries.md) points to the `geo2stl` scale helper that should be reused.

7. Route terrain.py through core/dem (B-LIB5)
   [proposals.md](proposals.md) records the layering issue. [libraries.md](libraries.md) gives the app-vs-library import map that explains why this belonged in the core layer.

8. Export progress indicator (EXP-1)
   [export TODO](../app/client/static/js/modules/export/TODO.md) contains the UX requirement and suggested transport. [proposals.md](proposals.md) contains the feature tracker entry.

9. Replace inline styles with CSS (CLEAN-1)
   [ui TODO](../app/client/static/js/modules/ui/TODO.md) lists remaining cleanup targets. [proposals.md](proposals.md) and [../TODO.md](../TODO.md) provide the broader cleanup tracker context.

10. Lazy-allocate hidden layer canvases (UX-M)
    The most concrete implementation notes are in [layers TODO](../app/client/static/js/modules/layers/TODO.md), including the context-loss caveat for zero-sized canvases. [ux-audit.md](ux-audit.md) and [layer_system_analysis.md](layer_system_analysis.md) provide the memory/performance rationale.

11. Consolidate region creation entry (UX-1)
    [map TODO](../app/client/static/js/modules/map/TODO.md) contains the specific UI consolidation guidance. [ux-audit.md](ux-audit.md) explains the discoverability problem and [proposals.md](proposals.md) tracks the task.

12. Region list pagination (REG-1)
    [proposals.md](proposals.md) is currently the clearest requirements source for pagination/virtual scrolling. [regions TODO](../app/client/static/js/modules/regions/TODO.md) is the owning module tracker.

13. Region import/export JSON (REG-2)
    [proposals.md](proposals.md) records the original scope and [regions TODO](../app/client/static/js/modules/regions/TODO.md) is the owning module tracker.

14. Event bus consolidation (R-EVENTS-A)
    [events TODO](../app/client/static/js/modules/events/TODO.md) has the best migration outline, including the early event names. [layer_system_analysis.md](layer_system_analysis.md) records the architectural reason for the change and [proposals.md](proposals.md) tracks the larger refactor.

15. Height pipeline Phase 1a (DONE)
    Core height package built: `app/server/core/height/` with HeightResult, HeightProvider protocol, merge_height_rasters(), and 4 providers (nDSM, WSF3D, Copernicus, 3DEP LiDAR). Router at `/api/height/`. 91 tests passing.

15b. Height pipeline Phase 1b (GHSL, Open Buildings, shadow) — DONE
    All 8 providers are now wired into `TerrainSession.fetch_building_heights()`. GHSL is functional.
    Open Buildings `_fetch_buildings_for_bbox` still returns `None` (see note 15d).
    ShadowHeight `fetch_heights` now fully implemented with optional `rgb` parameter (see note 15e).
    Wiring complete as of 2026-04-24. [height-pipeline-plan.md](height-pipeline-plan.md) Phase 1b section updated.

15c. Height pipeline Phase 3 (STL import + IDW infill) — DONE
    `stl_import.py` (trimesh ray-cast → heightmap), `infill.py` (IDW + nearest-neighbour), and `TerrainSession.load_stl()` / `preview_stl()` / `infill_heights()` implemented and tested (32 new tests). Test count: 179. See [height-pipeline-plan.md](height-pipeline-plan.md) Phase 3 section.

15d. Open Buildings v3 real fetch path — open
    Placeholder provider in `app/server/core/height/open_buildings.py`. Plan in [open-todo-plans.md](open-todo-plans.md#11-open-buildings-v3-real-fetch-path).

15e. Shadow height actual inference pipeline — **done**
    `fetch_heights` now accepts optional `rgb` and runs `_infer_from_rgb` (shadow detection,
    sun elevation, connected-component analysis, height estimation). Returns empty result when
    no RGB is supplied — safe default. See [open-todo-plans.md](open-todo-plans.md#12-shadow-height-actual-inference-pipeline).

16. Composite DEM Phase 2 (city rasterization)
    [composite_dem_design.md](composite_dem_design.md) is the main phase/design document. [height-pipeline-plan.md](height-pipeline-plan.md) adds related building-height and composition context.

17. Off-thread DEM pixel rendering (Plan A)
    [dem TODO](../app/client/static/js/modules/dem/TODO.md) contains the concrete worker plan, payload shape, and prerequisites. [proposals.md](proposals.md) tracks the broader performance item.

18. Bbox coord validation bugs (5 issues)
    [bbox_editing_options.md](bbox_editing_options.md) is the canonical analysis doc for the five validation bugs and their UI behavior.

19. Chrome DevTools perf profile (P0)
    [ux-audit.md](ux-audit.md) contains the P0 runtime profiling checklist and what to capture during a browser session.

20. Building Height & Roof Geometry Pipeline (ROOF-1/2/3)
    [building-roof-pipeline-plan.md](building-roof-pipeline-plan.md) is the canonical design document. Three phases:
    - ROOF-1: Preserve OSM roof tags (`roof:shape`, `roof:height`, etc.) through the building pipeline instead of discarding them. Write inferred heights from shadow/GHSL/nDSM providers back into the building GeoJSON table via `apply_height_raster_to_buildings()`.
    - ROOF-2: Classify roof shapes from satellite imagery for buildings without OSM tags. Full ROOF-2 implementation complete:
        - Multi-signal heuristic classifier in `city2stl/roof_classifier.py`
        - RoofNet CNN architecture in `tools/networks.py`
        - 5 eval cities seeded + evaluated (`tools/seed_eval_regions.py`, `tools/eval_roof_classifier.py`, `tools/eval_pseudo_ndsm.py`)
        - Training pipeline scripts created: `tools/harvest_roof_crops.py`, `tools/train_roof_classifier.py`, `tools/train_pseudo_ndsm.py`
        - Eval baseline (heuristic, untrained): Amsterdam 2.5%, Vienna 2.2%, Prague 8.7–9.0%, Berlin 2.4%, Rotterdam 5.6%
        - Coverage gain: 100% of buildings classified after one pass (overwrite=False path)
        - Next step for ROOF-2 training: run `harvest_roof_crops.py` → `train_roof_classifier.py` → re-run eval to measure CNN improvement
    - ROOF-3: Generate non-flat roof meshes in `city2stl/mesh.py` (gabled, hipped, pyramidal, dome, etc.) based on the OSM-4D parametric model.
    Research covers OSM-4D/Roof_table schema, T-SwinUNet height estimation, BRAILS classifier, and RooFormer.

21. Open Buildings v3 real fetch path
    Placeholder provider in `app/server/core/height/providers/open_buildings.py`. Plan in [open-todo-plans.md](open-todo-plans.md#11-open-buildings-v3-real-fetch-path). Requires `pyarrow>=14`, `fsspec`, `s3fs` dependencies.
