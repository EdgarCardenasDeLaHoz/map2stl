# CLAUDE.md — strm2stl

> **Index file only.** Full details are in `docs/`. Read this first, then load only what you need.

## Context Management (read first)

- `/compact` is **user-triggered** — Claude cannot run it. After each commit Claude will output: `--- Task complete. Run /compact before continuing. ---`
- Run `/compact` when you see that signal, or when context exceeds ~60%
- Load at most 1–2 `docs/` files per session
- Keep sessions focused on one module at a time

## Quick Start

```bash
cd strm2stl && source ../.venv/bin/activate
python -m uvicorn app.server.server:app --port 9000 --reload   # starts FastAPI
python -m pytest tests/ -v                                     # run all tests (108, 7 files)
```

## When to Read What

| Working on | Files to read |
|---|---|
| Backend endpoint | `docs/api.md` + relevant router file |
| Session client (Python API) | `terrain_session.py` + `notebooks/Session_API_Reference.ipynb` |
| Cache / storage | `app/core/cache.py` header + `docs/api.md` |
| DEM rendering / colormaps | `docs/modules.md` + `static/js/modules/dem/dem-loader.js:1-30` |
| City / OSM features | `docs/modules.md` + `static/js/modules/layers/city-overlay.js:1-40` |
| Stacked layers / composite | `docs/modules.md` + `static/js/modules/layers/stacked-layers.js:1-30` |
| Region CRUD | `docs/api.md` + `app/routers/regions.py:1-50` |
| Frontend state variables | `docs/state.md` |
| View tabs / navigation | `docs/arch.md` |
| Data flow debugging | `docs/arch.md` (Data Flow section) |
| Function lookup | `docs/functions.md` |
| Known bugs / tech debt | `docs/issues.md` |
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
│   │   ├── core/          ← dem.py, export.py, cache.py, db.py, osm.py, cities_3d.py
│   │   └── routers/       ← terrain.py, regions.py, export.py, cities.py, cache.py, settings.py
│   ├── client/            ← browser client (HTML/CSS/JS)
│   │   ├── static/js/     ← main.js, modules/ (30 ES modules in 8 subdirs)
│   │   ├── static/css/    ← app.css
│   │   └── templates/     ← index.html
│   └── session/           ← Python SDK client (talks to server over HTTP)
│       ├── terrain_session.py
│       └── viz.py
│
│  ── geo/mesh libraries ─────────────────────────────────────────────────
├── geo2stl/               ← map projections + tile stitching
├── city2stl/              ← OSM/building to 3D mesh helpers
│
│  ── project tooling ────────────────────────────────────────────────────
├── tests/                 ← pytest suite (conftest.py + 7 test files)
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

## Proposals Rule

**Before implementing any new feature or significant refactor not explicitly requested in the current conversation:**
1. Add it to `docs/proposals.md` with status `pending`.
2. Do **not** implement it until the user sets its status to `approved`.
3. On session start, read `docs/proposals.md` and only work on `approved` items (plus any direct user requests).

---

## Editing Rules

1. **Never use `os.chdir()`** — breaks relative paths for all other requests.
2. **Never call `asyncio.get_event_loop()`** — use `asyncio.get_running_loop()` inside async functions.
3. **Modules expose `window.*`** — they do not import each other. Coordination is via `window.appState` and `window.events`.
4. **`app.js` is a plain `<script>`** — not an ES module. Keep public functions on `window.*`.
5. **New closure vars go on `window.appState`** — so modules can access them. See `docs/state.md`.
6. **Patch at the correct module path in tests** — e.g. `app.server.routers.cities._fetch_osm_data`, not `app.routers.cities._fetch_osm_data`.
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
| Completed feature history | `docs/functionality_doc.md` |
| AI-proposed features (approve/deny) | `docs/proposals.md` |
