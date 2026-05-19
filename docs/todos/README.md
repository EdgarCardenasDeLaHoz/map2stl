# Todos — strm2stl

_Last updated: 2026-05-02_

This folder is for active work only. When a task is completed, move its plan doc to `../completed/` and add a short note to `../completed/todo-history.md`.

Audit docs do not own tasks directly. If an audit still has live findings, they must be restated here or in another current tracker.

## Current open work

| ID | Area | Status | Primary doc |
|---|---|---|---|
| ML-1 | Height provider wiring | Open | [height-pipeline-improvement-plan.md](height-pipeline-improvement-plan.md) |
| ML-2 | Tall-building accuracy gap | Open | [../plans/height_training/height-training-status.md](../plans/height_training/height-training-status.md) |
| ML-3 | Remove legacy RoofNet-era training code after notebook migration | Open | [../audits/dead-code-analysis.md](../audits/dead-code-analysis.md) |
| TEST-1 | Add coverage for remaining export and cities endpoints | Open | [../audits/test-coverage-audit.md](../audits/test-coverage-audit.md) |
| A11Y-1 | Normalize sidebar and settings contrast tokens before revisiting accessibility audit | Open | [../audits/accessibility-audit.md](../audits/accessibility-audit.md) |

## Recently closed or removed from the active queue

- `MAP-2`, `UX-1`, and `EXP-1` shipped and now live under [../completed/frontend-ui-ux-plan.md](../completed/frontend-ui-ux-plan.md).
- Earlier UX-audit carry-over such as region-creation consolidation and lazy canvas allocation is already closed and lives under [../completed/todo-history.md](../completed/todo-history.md).
- `P6` multi-material export is not active work; it was denied in [../proposals.md](../proposals.md).
- Historical shipped backlog lives in [../completed/todo-history.md](../completed/todo-history.md).

## Working rules

- Put new AI-proposed work in [../proposals.md](../proposals.md); only `approved` items belong in the active queue.
- Keep this folder small and current. If a file becomes historical, move it out.
- When you extract an item from an audit, keep only the current action here and leave the audit as evidence.
