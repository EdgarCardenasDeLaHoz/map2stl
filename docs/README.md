# Docs Index — strm2stl

_Last updated: 2026-05-02_

The docs tree is organized to keep each folder shallow and AI-friendly. The root keeps only the 10 highest-signal entry docs; supporting material lives in topic folders.

## Start Here

1. `../CLAUDE.md` for repo rules and the high-level map
2. `ai-agent-onboarding.md` for the fastest correct project model
3. `task-routing.md` to pick the owning code surface
4. `sdk-workflow.md` if the task starts from notebooks or `TerrainSession`
5. Subsystem references such as `api.md`, `arch.md`, `modules.md`, and `state.md`

## Site Sections

### Guides

| Area | Best entry points |
|---|---|
| Project orientation | `ai-agent-onboarding.md`, `task-routing.md` |
| Runtime structure | `arch.md` |
| Backend routes | `api.md` |
| Notebook and SDK workflow | `sdk-workflow.md` |
| Frontend ownership | `modules.md`, `state.md` |

### Reference

| Goal | Read first |
|---|---|
| Browse reference docs | `reference/README.md` |
| Inspect support-library integration | `reference/libraries.md` |
| Look up frontend functions quickly | `reference/functions.md` |
| Understand layer canvases / GPU memory | `reference/layer-system.md` |
| Inspect terrain endpoint call stacks | `reference/terrain-api-audit.md` |
| Inspect ML tooling and model lineage | `reference/ml-pipeline.md` |

### In Progress

| Topic | Start with |
|---|---|
| Active backlog | `todos/README.md` |
| Height pipeline open work | `todos/height-pipeline-improvement-plan.md` |
| Current ML status | `plans/height-training-status.md` |
| Supporting ML plan docs | `plans/height-data-sources.md` (active investigation) — others moved to `completed/` |
| Proposed future work | `proposals.md` |
| Known bugs and debt | `issues.md` |

### Design

| Topic | Start with |
|---|---|
| Design doc index | `design/README.md` |
| Composite DEM | `design/composite-dem-design.md` |
| BBox editing | `design/bbox-editing-options.md` |
| Print settings | `design/prusaslicer-terrain-settings.md` |
| Historical roof ML design | `design/roof-ml-architecture.md` |

### Reviews

| Topic | Start with |
|---|---|
| Audit index | `audits/README.md` |
| Layer-system analysis | `audits/layer-system-analysis.md` |
| UX and accessibility | `audits/ux-audit.md`, `audits/accessibility-audit.md` |
| Code and test audits | `audits/dead-code-analysis.md`, `audits/test-coverage-audit.md` |

### Past Work

| Topic | Start with |
|---|---|
| Completed index | `completed/README.md` |
| Completion history | `completed/todo-history.md` |
| Shipped plans | `completed/height-pipeline-plan.md`, `completed/building-roof-pipeline-plan.md`, `completed/frontend-ui-ux-plan.md`, `completed/open-todo-plans.md` |
| Broader feature history | `completed/functionality-history.md` |

### Older Notes

These pages stay available for historical context, but they are intentionally outside the main reading path.

| Topic | Start with |
|---|---|
| Legacy broad app analysis | `archive/web_app_analysis.md` |
| Archived planning notes | `archive/frontend_refactoring.md`, `archive/per-layer-resolution-plan.md`, `archive/rotation-plan.md`, `archive/todo_advanced.md` |

## Topic Folders

| Folder | Purpose | Start with |
|---|---|---|
| `reference/` | Stable technical references and indexes | `reference/README.md` |
| `audits/` | Evidence snapshots and analysis only; extract anything still open into `todos/` or `proposals.md` | `audits/README.md` |
| `design/` | Stable design rationale after temporary audit findings are resolved | `design/README.md` |
| `todos/` | Verified active work only, including extracted audit follow-through | `todos/README.md` |
| `completed/` | Shipped plans and verified closed audit follow-through | `completed/README.md` |
| `plans/` | Ongoing ML status snapshots and working notes | `plans/height-training-status.md` |
| `archive/` | Legacy material kept for reference only | browse only when a current doc points there |

## Current Tracking Rules

- Active backlog items live under `todos/`.
- Audits do not own open work. If an audit still matters, restate the live item in `todos/README.md`, `issues.md`, or `proposals.md`.
- When a todo is completed, move its plan doc to `completed/` and add a note to `completed/todo-history.md`.
- When an audit-driven change ships, fold the stable rationale back into the relevant design or reference doc and trim the audit back to evidence.
- `proposals.md` remains the gate for new AI-proposed work.

## Short Rule

Prefer the routing docs and folder READMEs over older broad narrative documents when you are orienting to the project.