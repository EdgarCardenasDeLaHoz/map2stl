# CLAUDE.md — strm2stl

> **Index file only.** Full details are in `docs/`. Read this first, then load only what you need.

## Quick Start

```bash
cd strm2stl && source ../.venv/bin/activate
python ui/server.py          # starts FastAPI on port 9000
python -m pytest tests/ -v   # run all 54 tests
```

## When to Read What

| Working on | Files to read |
|---|---|
| Backend endpoint | `docs/api.md` + relevant router file |
| Cache / storage | `ui/core/cache.py` header + `docs/api.md` |
| DEM rendering / colormaps | `docs/modules.md` + `dem/dem-loader.js:1-30` |
| City / OSM features | `docs/modules.md` + `layers/city-overlay.js:1-40` |
| Stacked layers / composite | `docs/modules.md` + `layers/stacked-layers.js:1-30` |
| Region CRUD | `docs/api.md` + `ui/routers/regions.py:1-50` |
| Frontend state variables | `docs/state.md` |
| View tabs / navigation | `docs/arch.md` |
| Data flow debugging | `docs/arch.md` (Data Flow section) |
| Function lookup | `docs/functions.md` |
| Known bugs / tech debt | `docs/issues.md` |
| JS module map | `docs/modules.md` |
| Writing tests | `tests/conftest.py` + relevant test file |

## Context Management

- Run `/compact` after each completed task
- Run `/compact` when context exceeds ~60%
- Load at most 1–2 `docs/` files per session
- After a large function-lookup session: `/compact` before coding

## Project Structure (key paths)

```
strm2stl/
├── CLAUDE.md              ← this file (index only)
├── docs/                  ← on-demand reference docs
│   ├── arch.md            ← frontend/backend architecture + data flows
│   ├── state.md           ← all global state variables
│   ├── functions.md       ← function one-liner index
│   ├── api.md             ← all backend API routes
│   ├── modules.md         ← JS module map (subdirs + exports)
│   └── issues.md          ← known issues + feature status
├── TODO.md                ← open tasks only (ARCH4, ARCH5, PERF6B)
├── data.db                ← SQLite: regions + region_settings (WAL)
├── ui/
│   ├── server.py          ← FastAPI app + startup lifespan
│   ├── schemas.py         ← all Pydantic models
│   ├── config.py          ← constants, OPENTOPO_DATASETS, API keys
│   ├── core/              ← dem.py, export.py, cache.py, db.py, osm.py, cities_3d.py
│   ├── routers/           ← terrain.py, regions.py, export.py, cities.py, cache.py, settings.py
│   └── static/js/
│       ├── app.js         ← ~8300-line plain script (state + DOMContentLoaded)
│       ├── main.js        ← ES module entry (imports all modules in order)
│       └── modules/       ← 30 ES modules in 8 subdirs (see docs/modules.md)
└── tests/                 ← pytest suite (conftest.py + 6 test files)
```

## Editing Rules

1. **Never use `os.chdir()`** — breaks relative paths for all other requests.
2. **Never call `asyncio.get_event_loop()`** — use `asyncio.get_running_loop()` inside async functions.
3. **Modules expose `window.*`** — they do not import each other. Coordination is via `window.appState` and `window.events`.
4. **`app.js` is a plain `<script>`** — not an ES module. Keep public functions on `window.*`.
5. **New closure vars go on `window.appState`** — so modules can access them. See `docs/state.md`.
6. **Patch at the short-path module boundary in tests** — e.g. `routers.regions`, not `strm2stl.ui.routers.regions`.
7. **Backend**: blocking ops go in `run_in_executor`; `asyncio.get_running_loop()` not `get_event_loop()`.

## Full Details

| Topic | File |
|---|---|
| Architecture + data flows | `docs/arch.md` |
| Global state variables | `docs/state.md` |
| Function index | `docs/functions.md` |
| API routes + Pydantic models | `docs/api.md` |
| JS module map | `docs/modules.md` |
| Known issues + feature status | `docs/issues.md` |
| Completed feature history | `ui/static/FUNCTIONALITY_DOC.md` |
