# Open Todo — Implementation Plans

_Last updated: 2026-04-30_

> **All 13 implementation items in this document are now complete.** This file is kept as an index of what was planned and where it shipped, in case future work needs to revisit any of them.

For new work, write the plan in [`../proposals.md`](../proposals.md) and link from there.

## Completed items (one-line index)

| # | Item | Where it shipped |
|---|---|---|
| 1 | Web Worker / OffscreenCanvas (PERF6B) | `app/client/static/js/modules/layers/city-render.js` + `city-worker.js` |
| 2a | Curve editor refactor — `CurveEditorState` class | `tests/js/helpers/curveEditorState.js` + `ui/curve-editor.js` |
| 2b | Preset versioning + revert | `app/client/static/js/modules/ui/presets.js` (`PRESET_VERSION`, `_migratePreset`, `revertPreset`) |
| 3 | Replace inline styles with CSS (CLEAN-1) | `app/client/static/css/app.css` |
| 4 | Lazy-allocate hidden layer canvases (UX-M) | `app/client/static/js/modules/layers/stacked-layers.js` |
| 5 | Consolidate region creation entry point (UX-1) | `app/client/static/js/modules/regions/region-creation.js` |
| 6 | Region list pagination (REG-1) | `app/server/routers/regions.py` + frontend region list |
| 7 | Event bus consolidation (R-EVENTS-A) | `app/client/static/js/modules/core/events.js` |
| 8 | Composite DEM Phase 2 — city rasterization | `composite-dem.js` + `/api/composite/city-raster` |
| 9 | Off-thread DEM pixel rendering (Plan A) | `app/client/static/js/modules/dem/dem-loader.js` + `dem-worker.js` |
| 10 | Chrome DevTools perf profile (P0) | `docs/issues.md` (one-time profiling, no code change) |
| 11 | Open Buildings v3 real fetch path | `app/server/core/height/providers/open_buildings.py` (Overture Maps via pyarrow + S3) |
| 12 | Shadow height inference pipeline | `app/server/core/height/providers/shadow_height.py` |
| 13 | Plate Carree cache refactor (P-PROJ-CACHE) | `app/server/core/projection.py` |

## Where to find current open work

| Topic | File |
|---|---|
| Building-height ML model | [`../plans/height-training-status.md`](../plans/height-training-status.md) |
| Height pipeline improvements still open | [`../todos/height-pipeline-improvement-plan.md`](../todos/height-pipeline-improvement-plan.md) |
| New AI-generated proposals | [`../proposals.md`](../proposals.md) |
| Active issues / tech debt | [`../issues.md`](../issues.md) |
