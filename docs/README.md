# Docs Index — strm2stl

_Last updated: 2026-05-17_

Start here if you opened the `docs/` folder directly and need the preferred reading path.

## Preferred Order

1. `../CLAUDE.md` for project rules and the high-level index
2. `ai-agent-onboarding.md` for the fastest correct project map
3. `sdk-workflow.md` if the task starts from a notebook or `TerrainSession`
4. `task-routing.md` to decide where to edit
5. Subsystem references such as `api.md`, `arch.md`, `modules.md`, and `state.md`

## Use These First

| Goal | Read first |
|---|---|
| Understand the repository quickly | `ai-agent-onboarding.md` |
| Trace notebook actions to code | `sdk-workflow.md` |
| Decide which files own a task | `task-routing.md` |
| Inspect backend routes | `api.md` |
| Inspect runtime structure | `arch.md` |
| Inspect frontend module ownership | `modules.md` and `state.md` |
| Understand layer canvases / GPU memory | `layer-system.md` |
| Understand library delegation patterns | `arch.md` (Library Delegation section) |

## Detailed References

These are useful after orientation, but they are not the best first stop:

- `terrain-api-audit.md` — full call-stack audit of all `/api/terrain/` endpoints with quality analysis
- `layer-system.md` — complete reference for the 7-layer canvas pipeline, GPU memory management, and per-layer data flows
- `issues.md`
- `ux-audit.md`
- design and proposal documents tied to a specific subsystem

## Proposals & Tracking

- `proposals.md` — AI-proposed features and tasks. Approved items are queued for implementation.

## Short Rule

Prefer the newest routing documents over older broad reference documents when you are orienting to the project.