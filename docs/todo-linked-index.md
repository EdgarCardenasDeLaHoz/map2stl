# Linked Todo Index

_Last updated: 2026-04-19_

This file captures the current 19-item working todo list and links each item to any existing docs, module TODO files, or analysis notes that already contain implementation detail.

| Status | Todo | Detail Links |
|---|---|---|
| open | Web Worker Part B (OffscreenCanvas) | [proposals.md](proposals.md), [layers TODO](../app/client/static/js/modules/layers/TODO.md), [ux-audit.md](ux-audit.md) |
| open | Curve editor bugs and presets versioning | [ui TODO](../app/client/static/js/modules/ui/TODO.md) |
| done | Refactor cities_3d._terrain_mesh (B-LIB1) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Refactor _extrude_ring/_ear_clip (B-LIB2) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Fix legacy projection in dem.py (B-LIB3) | [proposals.md](proposals.md), [projections.md](projections.md), [issues.md](issues.md) |
| done | Use geo2stl scale calculation (B-LIB4) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Route terrain.py through core/dem (B-LIB5) | [proposals.md](proposals.md), [libraries.md](libraries.md) |
| done | Export progress indicator (EXP-1) | [export TODO](../app/client/static/js/modules/export/TODO.md), [proposals.md](proposals.md) |
| open | Replace inline styles with CSS (CLEAN-1) | [ui TODO](../app/client/static/js/modules/ui/TODO.md), [proposals.md](proposals.md), [../TODO.md](../TODO.md) |
| open | Lazy-allocate hidden layer canvases (UX-M) | [layers TODO](../app/client/static/js/modules/layers/TODO.md), [proposals.md](proposals.md), [ux-audit.md](ux-audit.md), [layer_system_analysis.md](layer_system_analysis.md) |
| open | Consolidate region creation entry (UX-1) | [map TODO](../app/client/static/js/modules/map/TODO.md), [proposals.md](proposals.md), [ux-audit.md](ux-audit.md) |
| open | Region list pagination (REG-1) | [regions TODO](../app/client/static/js/modules/regions/TODO.md), [proposals.md](proposals.md) |
| done | Region import/export JSON (REG-2) | [regions TODO](../app/client/static/js/modules/regions/TODO.md), [proposals.md](proposals.md) |
| open | Event bus consolidation (R-EVENTS-A) | [events TODO](../app/client/static/js/modules/events/TODO.md), [proposals.md](proposals.md), [layer_system_analysis.md](layer_system_analysis.md) |
| done | Height pipeline Phase 1a (4 providers + router + merge) | [height-pipeline-plan.md](height-pipeline-plan.md), [3D_plan1.md](../app/server/core/3D_plan1.md) |
| done | Height pipeline Phase 1b (GHSL, Open Buildings, shadow) | [height-pipeline-plan.md](height-pipeline-plan.md) |
| open | Composite DEM Phase 2 (city rasterization) | [composite_dem_design.md](composite_dem_design.md), [height-pipeline-plan.md](height-pipeline-plan.md) |
| open | Off-thread DEM pixel rendering (Plan A) | [dem TODO](../app/client/static/js/modules/dem/TODO.md), [proposals.md](proposals.md) |
| done | Bbox coord validation bugs (5 issues) | [bbox_editing_options.md](bbox_editing_options.md) |
| done | Print-bed multi-piece export (B-MULTI) | [proposals.md](proposals.md) |
| done | OpenAPI schema validation in dev (B-OPENAPI) | [proposals.md](proposals.md) |
| done | Google 3D height raster → city buildings integration | [proposals.md](proposals.md), [height-pipeline-plan.md](height-pipeline-plan.md) |
| open | Chrome DevTools perf profile (P0) | [ux-audit.md](ux-audit.md) |

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

15b. Height pipeline Phase 1b (GHSL, Open Buildings, shadow)
    [height-pipeline-plan.md](height-pipeline-plan.md) describes supplementary sources. These are lower-priority gap-fillers for cities not covered by the 4 core providers.

16. Composite DEM Phase 2 (city rasterization)
    [composite_dem_design.md](composite_dem_design.md) is the main phase/design document. [height-pipeline-plan.md](height-pipeline-plan.md) adds related building-height and composition context.

17. Off-thread DEM pixel rendering (Plan A)
    [dem TODO](../app/client/static/js/modules/dem/TODO.md) contains the concrete worker plan, payload shape, and prerequisites. [proposals.md](proposals.md) tracks the broader performance item.

18. Bbox coord validation bugs (5 issues)
    [bbox_editing_options.md](bbox_editing_options.md) is the canonical analysis doc for the five validation bugs and their UI behavior.

19. Chrome DevTools perf profile (P0)
    [ux-audit.md](ux-audit.md) contains the P0 runtime profiling checklist and what to capture during a browser session.
