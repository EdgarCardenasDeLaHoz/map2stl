# History

Finished, abandoned or superseded work, kept so its reasoning isn't lost. Nothing here
describes how the code works *now*: for that, start at [../INDEX.md](../INDEX.md). Files
are moved here unchanged, apart from fixed links and, on misleading snapshots, a one-line
"stale snapshot, kept for history" banner. The reasons behind past choices that still
hold are in [../decisions/](../decisions/).

## Folders

- [audits/](audits/): full-project and subsystem audits (2026-10-05 full line-by-line, 2026-05-09, 05-17, 06-07, F-SKY ×2,
  pipeline 2026-08-26, terrain API, accessibility, dead code, test coverage, UX).
  Items still open are tracked in [../issues.md](../issues.md).
- [issues-resolved.md](issues-resolved.md): resolved entries moved out of `issues.md`.
- [refactors/](refactors/): the 2026-05-08 module split and the `export.py` refactor.
- [design/](design/): design options that were decided or dropped (bbox editing).
- [archive/](archive/): old frontend/web-app analyses, rotation and per-layer plans, the
  functionality history and the old open-todo plans.
- [ml-height/](ml-height/): the building-height CNN work (RoofNet, Retna, Phases A–H).
  **Not used at runtime.** Start at [ml-height/README.md](ml-height/README.md).

## ML and height-training docs

From the old docs index (docs/README.md) list, now under [ml-height/](ml-height/):

- [BUILDING-HEIGHT-MODEL-SUMMARY.md](ml-height/BUILDING-HEIGHT-MODEL-SUMMARY.md): Retna training summary
- [MODEL-STRATEGY.md](ml-height/MODEL-STRATEGY.md): checkpoint backup/training strategy across Phase G/H
- [MODELS-REFERENCE.md](ml-height/MODELS-REFERENCE.md): old checkpoint lookup (stale; see [../../models/README.md](../../models/README.md))
- [TILE-ANALYSIS-GUIDE.md](ml-height/TILE-ANALYSIS-GUIDE.md): per-tile model evaluation
- [GROWTH_EXTENDED_TEST_REPORT.md](ml-height/GROWTH_EXTENDED_TEST_REPORT.md): architecture-growth experiments
- [growth_degradation_analysis.md](ml-height/growth_degradation_analysis.md) / [growth_degradation_mitigation.md](ml-height/growth_degradation_mitigation.md): gradient-freezing investigations
- [ml_pipeline_audit_v1.md](ml-height/ml_pipeline_audit_v1.md): ML pipeline audit
- [roof-ml-architecture.md](ml-height/roof-ml-architecture.md): ROOF-2 roof-prediction architecture
- [PHASE-G-ARCHIVE-SUMMARY.md](ml-height/PHASE-G-ARCHIVE-SUMMARY.md): Phase G summary

## Changelog of completed todos (to 2026-05-02)

Folded in from the old completed/todo-history.md. Later completed work is tracked in
[../plans/README.md](../plans/README.md) (`plans/done/`).

| Area | Item | Primary doc |
|---|---|---|
| Frontend performance | Web Worker / OffscreenCanvas | [open-todo-plans.md](archive/open-todo-plans.md) |
| Frontend UX | Curve editor refactor and presets versioning | [open-todo-plans.md](archive/open-todo-plans.md) |
| Frontend UX | Replace inline styles with CSS | [frontend-ui-ux-plan.md](../plans/done/frontend-ui-ux-plan.md) |
| Frontend UX | Lazy-allocate hidden layer canvases | [frontend-ui-ux-plan.md](../plans/done/frontend-ui-ux-plan.md) |
| Frontend UX | Consolidate region creation entry point | [frontend-ui-ux-plan.md](../plans/done/frontend-ui-ux-plan.md) |
| Frontend UX | Region list pagination and import/export | [frontend-ui-ux-plan.md](../plans/done/frontend-ui-ux-plan.md) |
| Frontend architecture | Event bus consolidation | [open-todo-plans.md](archive/open-todo-plans.md) |
| Frontend rendering | Off-thread DEM pixel rendering | [open-todo-plans.md](archive/open-todo-plans.md) |
| Backend cleanup | Library integration debt B-LIB1..5 | [packages.md](../reference/packages.md) |
| Backend validation | Bbox coordinate validation fixes | [bbox-editing-options.md](design/bbox-editing-options.md) |
| Height pipeline | Phase 1a, 1b, and Phase 3 shipped | [height-pipeline-plan.md](../plans/done/height-pipeline-plan.md) |
| Composite DEM | City rasterization phase shipped | [composite-dem-design.md](../reference/composite-dem-design.md) |
| Roof pipeline | ROOF-1, ROOF-2, ROOF-3, and F-ROOF1 shipped | [building-roof-pipeline-plan.md](../plans/done/building-roof-pipeline-plan.md) |
| ML platform | Unified ML CLI and Retna migration cleanup | [ml-pipeline.md](ml-height/ml-pipeline.md) |
| Project history | Older completed feature log | [functionality-history.md](archive/functionality-history.md) |
