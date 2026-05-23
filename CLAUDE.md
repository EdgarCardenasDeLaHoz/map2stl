# CLAUDE.md — strm2stl

> **Index file only.** Full details are in `docs/`. Read this first, then load only what you need.

## Context Management (read first)

- `/compact` is **user-triggered** — Claude cannot run it. After each commit Claude will output a signal. When you see: `--- Task complete. Run /compact before continuing. ---` at the end of a response, run `/compact` to compress context.
- Run `/compact` when you see that signal, or when context exceeds ~60%
- Load at most 1–2 `docs/` files per session. See "When to Read What" table below for guidance.
- Keep sessions focused on one module at a time. If scope creeps, ask the user before editing additional modules.

## Quick Start

```bash
cd strm2stl && source ../.venv/bin/activate
python -m uvicorn app.server.server:app --port 9000 --reload   # starts FastAPI
python -m pytest tests/ -v                                     # run all tests (651 pass; tests/e2e/ requires playwright — excluded via pytest.ini)
```

## Recommended Read Order

1. `docs/ai-agent-onboarding.md` for the shortest correct project map
2. `docs/README.md` for the preferred docs index inside `docs/`
3. `docs/sdk-workflow.md` for notebook and `TerrainSession` tracing
4. `docs/task-routing.md` to choose the right files before editing
5. Subsystem docs such as `docs/api.md`, `docs/modules.md`, `docs/state.md`, or `docs/arch.md`

## When to Read What

| Working on | Files to read |
|---|---|
| First-pass project orientation | `docs/ai-agent-onboarding.md` + `docs/task-routing.md` |
| Backend endpoint | `docs/api.md` + relevant router file |
| Terrain API call stacks | `docs/terrain-api-audit.md` |
| Session client (Python API) | `docs/sdk-workflow.md` + `app/session/terrain_session.py` + `notebooks/Session_API_Reference.ipynb` |
| Notebook-driven terrain workflow | `docs/sdk-workflow.md` + `notebooks/API_Terrain.ipynb` |
| Cache / storage | `app/server/core/cache.py` header + `docs/api.md` |
| DEM rendering / colormaps | `docs/modules.md` + `app/client/static/js/modules/dem/dem-loader.js:1-30` |
| City / OSM features | `docs/modules.md` + `app/client/static/js/modules/layers/city-overlay.js:1-40` |
| Stacked layers / composite | `docs/modules.md` + `app/client/static/js/modules/layers/stacked-layers.js:1-30` |
| Region CRUD | `docs/api.md` + `app/server/routers/regions.py:1-50` |
| Frontend state variables | `docs/state.md` |
| View tabs / navigation | `docs/arch.md` |
| Data flow debugging | `docs/arch.md` (Data Flow section) |
| Function lookup | `docs/modules.md` (Function Index section) |
| Known bugs / tech debt | `docs/issues.md` |
| Library integration / geo2stl / numpy2stl | `docs/arch.md` (Library Delegation section) |
| JS module map | `docs/modules.md` |
| Writing tests | `tests/conftest.py` + relevant test file |
| Approving / denying AI proposals | `docs/proposals.md` |

## Project Structure (key paths)

```
strm2stl/
│
│  ── application ────────────────────────────────────────────────────────
├── app/
│   ├── server/            ← HTTP server (Python/FastAPI)
│   │   ├── server.py      ← FastAPI app + lifespan  (entry: uvicorn app.server.server:app)
│   │   ├── config.py      ← constants, OPENTOPO_DATASETS, API keys
│   │   ├── schemas.py     ← all Pydantic models
│   │   ├── core/          ← cache.py, cache_inspector.py, db.py, export.py,
│   │   │                    export_params.py, export_tasks.py, osm_cache_policy.py,
│   │   │                    responses.py, terrain_raster.py, validation.py,
│   │   │                    height/ subpackage (service.py, train.py)
│   │   └── routers/       ← terrain.py, regions.py, export.py, cities.py, cache.py,
│   │                        settings.py, composite.py, height.py
│   ├── client/            ← browser client (HTML/CSS/JS)
│   │   ├── static/js/     ← main.js, modules/ (35 ES modules in 8 subdirs)
│   │   ├── static/css/    ← app.css
│   │   └── templates/     ← index.html
│   └── session/           ← Python SDK client (talks to server over HTTP)
│       ├── terrain_session.py  ← ~3100 lines (Python SDK client)
│       └── viz.py
│
│  ── geo/mesh libraries ─────────────────────────────────────────────────
├── geo2stl/               ← map projections + tile stitching
├── city2stl/              ← OSM/building to 3D mesh helpers
│
│  ── project tooling ────────────────────────────────────────────────────
├── tests/                 ← pytest suite (conftest.py + 32 test files; e2e/ requires playwright)
├── notebooks/             ← Jupyter notebooks + helpers (API_Terrain, Session_API_Reference, …)
├── tools/                 ← utility scripts + slicer_configs/
├── docs/                  ← all reference docs (api, arch, state, modules, proposals, …)
│
│  ── build / config ─────────────────────────────────────────────────────
├── Makefile, ruff.toml, requirements*.txt, package.json, vite.config.js
│
│  ── runtime (gitignored) ───────────────────────────────────────────────
└── cache/, output/, data.db, server.log
```

## Proposal + Plan + Edit Workflow

The point of this workflow is **persistence**. Long sessions, compactions, and
multi-turn scope creep all destroy in-conversation context; the proposal +
plan files survive that. They are written so a future session can pick the
work up cold.

**For new features or significant refactors not already requested in the
current conversation**, the AI must follow steps 1–3 *before* writing
implementation code. The user does **not** need to pre-approve — capturing
the proposal and plan is the gate, not awaiting approval. The AI then
proceeds to implement immediately.

1. **Add a proposal entry** to `docs/proposals.md` (status `pending`) with a
   short description, target file(s), and an effort estimate. The entry's
   ID becomes the durable handle.
2. **Write a plan** in `docs/plans/<ID>-<slug>.md` before touching code. The
   plan should cover: goal, approach in 3–6 bullets, target files, success
   criteria, and any known risks. The proposal entry **must link to this
   plan file** (so the plan is not orphaned by a future renumbering).
3. **Implement** — the proposal and plan exist for posterity; the AI continues
   directly into code.

For items that the user explicitly requests in conversation ("fix X", "add
Y"), skip steps 1–2 unless the scope grows large enough to warrant
durable tracking.

When multiple proposals are approved and ready, ask the user which to work on first.

### Documentation Update Checklist (apply after every code edit)

After you edit code, before committing:
- [ ] Updated module docstrings (if the file has one)?
- [ ] Updated `docs/modules.md` function index (if adding/removing/renaming functions)?
- [ ] Updated `docs/issues.md` status (if fixing a known issue)?
- [ ] Updated `docs/proposals.md` if this implements a proposal?
- [ ] Audited surrounding code for dead branches, unused parameters, or stale comments?

"Done" means the next session's reader can find the change referenced in the docs.

### Edit hygiene (apply after every code edit, while the region is in context)

- **Update the code's docs in the same turn.** Module docstrings, README
  references, line-number citations in `docs/modules.md`, and the issues
  list in `docs/issues.md` decay fast if updates are deferred. They are
  cheapest to fix while the file is open and the change is fresh.
- **Audit the surrounding region for bloat.** Look for dead branches,
  unused parameters, comments that no longer reflect the code, redundant
  guards, or two functions that have converged to the same shape. Edit
  context is the cheapest review context.
- **Audit for opportunities while the area is loaded.** Note (or fix) any
  obvious follow-ups that became visible while editing — they are far
  cheaper to address now than after the file leaves context.

---

## Editing Rules

1. **Never use `os.chdir()`** — breaks relative paths for all other requests.
2. **Never call `asyncio.get_event_loop()`** — use `asyncio.get_running_loop()` inside async functions.
3. **Modules expose `window.*`** — they do not import each other. Coordination is via `window.appState` and `window.events`.
4. **`app.js` is a plain `<script>`** — not an ES module. Keep public functions on `window.*`.
5. **New closure vars go on `window.appState`** — so modules can access them. See `docs/state.md`.
6. **Patch at the correct module path in tests** — e.g. `app.server.routers.cities._fetch_osm_data`, not `app.routers.cities._fetch_osm_data`.
7. **Backend**: blocking ops go in `run_in_executor`; `asyncio.get_running_loop()` not `get_event_loop()`.

## Training & ML Status

**Current production model:** `retna_pruned.pt` (0.2691 loss, 3.82m MAE) — used for building height estimation.

### Phase G (May 5-6, 2026) — Baseline established
- MAE 7.55m on 573 high-res tiles (512×512 @ 1.0 m/pixel)
- Architecture [6,7,6,8,7,7,7,7,9] (22,184 params)
- Deliverable: `models/retna_phase_g_global.pt` (Phase H warmstart)
- See: `PHASE-G-README.md`

### Phase H (May 7-8, 2026) — Validation complete
- RMSE 17.75m (3-cycle test + 10-epoch retraining)
- Promotion eligible; metric switched MAE → RMSE
- Deliverable: `models/retna_phase_h_final.pt`
- See: `PHASE-H-LAUNCH-SUMMARY.md`, `PHASE-H-BENCHMARK-TIERS.md`

### F-SKY (Current focus, May 2026) — Skyline CV pipeline
Computer-vision improvements to `city2stl/skyline_cv/` for cross-view building height estimation.
- **Active:** F-SKY2, 4, 6, 7, 8, 10 (opt-in), 11.1
- **Removed:** F-SKY3 (regression), F-SKY11.2 (dead-end)
- **Pending:** F-SKY5 (MobileSAM instance head)
- See: `docs/F-SKY-INTEGRATION.md`

## Full Details

| Topic | File |
|---|---|
| Preferred docs index | `docs/README.md` |
| AI agent onboarding | `docs/ai-agent-onboarding.md` |
| Notebook and SDK route map | `docs/sdk-workflow.md` |
| Task-to-file routing | `docs/task-routing.md` |
| Architecture + data flows | `docs/arch.md` |
| Global state variables | `docs/state.md` |
| Function index | `docs/modules.md` (Function Index section) |
| API routes + Pydantic models | `docs/api.md` |
| JS module map | `docs/modules.md` |
| Known issues + feature status | `docs/issues.md` |
| Completed feature history | `docs/functionality_doc.md` |
| AI-proposed features (approve/deny) | `docs/proposals.md` |
| Model training strategy | `docs/MODEL-STRATEGY.md` |
| Model reference guide | `docs/MODELS-REFERENCE.md` |
| Tile analysis guide | `docs/TILE-ANALYSIS-GUIDE.md` |
| F-SKY integration & status | `docs/F-SKY-INTEGRATION.md` |
| Building height model summary | `docs/BUILDING-HEIGHT-MODEL-SUMMARY.md` |
