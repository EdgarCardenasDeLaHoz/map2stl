# Completed Todo History

_Last updated: 2026-05-02_

This file tracks work that has already moved out of `docs/todos/`. For active items, start in [../todos/README.md](../todos/README.md).

## Shipped work

| Area | Item | Primary doc |
|---|---|---|
| Frontend performance | Web Worker / OffscreenCanvas | [open-todo-plans.md](open-todo-plans.md) |
| Frontend UX | Curve editor refactor and presets versioning | [open-todo-plans.md](open-todo-plans.md) |
| Frontend UX | Replace inline styles with CSS | [frontend-ui-ux-plan.md](frontend-ui-ux-plan.md) |
| Frontend UX | Lazy-allocate hidden layer canvases | [frontend-ui-ux-plan.md](frontend-ui-ux-plan.md) |
| Frontend UX | Consolidate region creation entry point | [frontend-ui-ux-plan.md](frontend-ui-ux-plan.md) |
| Frontend UX | Region list pagination and import/export | [frontend-ui-ux-plan.md](frontend-ui-ux-plan.md) |
| Frontend architecture | Event bus consolidation | [open-todo-plans.md](open-todo-plans.md) |
| Frontend rendering | Off-thread DEM pixel rendering | [open-todo-plans.md](open-todo-plans.md) |
| Backend cleanup | Library integration debt B-LIB1..5 | [../reference/libraries.md](../reference/libraries.md) |
| Backend validation | Bbox coordinate validation fixes | [../design/bbox-editing-options.md](../design/bbox-editing-options.md) |
| Height pipeline | Phase 1a, 1b, and Phase 3 shipped | [height-pipeline-plan.md](height-pipeline-plan.md) |
| Composite DEM | City rasterization phase shipped | [../design/composite-dem-design.md](../design/composite-dem-design.md) |
| Roof pipeline | ROOF-1, ROOF-2, ROOF-3, and F-ROOF1 shipped | [building-roof-pipeline-plan.md](building-roof-pipeline-plan.md) |
| ML platform | Unified ML CLI and Retna migration cleanup | [../reference/ml-pipeline.md](../reference/ml-pipeline.md) |
| Project history | Older completed feature log | [functionality-history.md](functionality-history.md) |

## Completion rule

When a task leaves `docs/todos/`:

1. Move any finished plan file into `docs/completed/`.
2. Add a short row here with the owning doc.
3. Remove the item from `docs/todos/README.md`.
