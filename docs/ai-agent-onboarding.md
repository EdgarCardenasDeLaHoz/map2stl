# AI Agent Onboarding — strm2stl

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
| Understand data flow bugs | `arch.md` | `functions.md`, relevant module or router |
| Check current debt before editing | `issues.md` | `../TODO.md` and module TODO files |

## Main Boundaries

### Backend

- Request handlers live in `app/server/routers/`.
- Business logic lives in `app/server/core/`.
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

## Next Document

- For notebook, session, and endpoint tracing: `sdk-workflow.md`
- For file ownership and where to edit: `task-routing.md`
- For runtime structure and request/data flow: `arch.md`