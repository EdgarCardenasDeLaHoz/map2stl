# AI Agent Onboarding — strm2stl

_Last updated: 2026-05-03_

Use this document when you need a fast, correct mental model of the repository before making changes.

## Start Here

1. Read `../CLAUDE.md` first. It is the index for the project.
2. Treat `strm2stl/` as the main application.
3. Treat `../numpy2stl/` as a supporting mesh-generation library and `geo2stl/`, `city2stl/` as supporting geospatial libraries inside `strm2stl/`.
4. Choose the narrowest next document based on your task instead of scanning the whole repo.

## Fast Mental Model

`strm2stl` is a terrain-to-3D pipeline with three main surfaces:

- FastAPI backend in `app/server/`
- Browser client in `app/client/`
- Python SDK in `app/session/terrain_session.py`

**Backend is three layers:**

```
routers/   → HTTP adapters (parse, delegate, format)
core/      → server-side state (cache, SQLite, export, height service)
geo2stl/   → terrain domain library (DEM, projection, tiles, raster, hydrology, satellite)
city2stl/  → city domain library (OSM rasterize, cache policy, building heights)
```

Routers import from both `core/` and `geo2stl/`/`city2stl/` directly — not everything goes through `core/`. Logic lives in the library layer when it is useful without the server (e.g. projection math, cache staleness predicates).

The usual workflow is:

1. Select or save a region.
2. Fetch terrain and optional overlays.
3. Inspect or merge layers.
4. Export STL, OBJ, or 3MF assets.

The clearest end-to-end example is `../notebooks/API_Terrain.ipynb`.

## Read Path By Task

| If you need to... | Read this first | Then read |
|---|---|---|
| Understand the whole app quickly | `../CLAUDE.md` | `arch.md`, `task-routing.md` |
| Follow the notebook-driven terrain flow | `sdk-workflow.md` | `../notebooks/API_Terrain.ipynb`, `../app/session/terrain_session.py` |
| Change a backend endpoint | `api.md` | relevant file in `../app/server/routers/` and `../app/server/core/` |
| Change frontend behavior | `task-routing.md` | `modules.md`, `state.md` |
| Change session client behavior | `sdk-workflow.md` | `../app/session/terrain_session.py` |
| Understand data flow bugs | `arch.md` | `reference/functions.md`, relevant module or router |
| Check current debt before editing | `issues.md` | `todos/README.md` and module TODO files |

## Main Boundaries

### Backend

- Request handlers live in `app/server/routers/`. These are thin HTTP adapters.
- Server-side coordination (cache I/O, SQLite, export, height orchestration) lives in `app/server/core/`.
- Terrain domain logic (DEM fetch, projection, tile stitch, raster scale) lives in `geo2stl/`.
- City domain logic (OSM rasterize, cache staleness, building heights) lives in `city2stl/`.
- `core/terrain_raster.py` and `core/osm_cache_policy.py` are **deprecated compatibility wrappers** — do not add new code there.
- `api.md` is the route index.

### Frontend

- `app.js` is not an ES module.
- `main.js` loads the ES modules.
- Modules coordinate through `window.appState`, `window.events`, and `window.api`.
- `modules.md` and `state.md` are the main references.

### Session Client

- `app/session/terrain_session.py` is the Python API wrapper around the HTTP server.
- `../notebooks/API_Terrain.ipynb` shows the main happy path.
- `../notebooks/Session_API_Reference.ipynb` shows endpoint coverage and method examples.

## Common Agent Mistakes To Avoid

- Do not treat `numpy2stl/` as the primary application surface.
- Do not start broad repo searches before checking `../CLAUDE.md`.
- Do not assume frontend modules import each other directly.
- Do not duplicate authoritative reference material from `api.md`, `arch.md`, or `modules.md`; link to it.
- Do not add significant features without checking `proposals.md` unless the user requested the work directly.
- Do not add new logic to `core/terrain_raster.py` or `core/osm_cache_policy.py` — these are deprecated wrappers.
- Do not assume "business logic belongs in `core/`" — terrain math and OSM logic belong in `geo2stl/` and `city2stl/` respectively.
- Do not look for `_fetch_dem_array` in terrain.py — it was removed; use `geo2stl.dem.fetch_dem_from_source` instead.
- Do not look for `_CACHE_AVAILABLE` in cities.py — it was removed; cache is always available and imported unconditionally.

## Next Document

- For notebook, session, and endpoint tracing: `sdk-workflow.md`
- For file ownership and where to edit: `task-routing.md`
- For runtime structure and request/data flow: `arch.md`