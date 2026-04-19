# Task Routing — strm2stl

Use this document to choose the right files before editing.

## First Decision

| If the task is about... | Start here |
|---|---|
| Route shape, request payloads, or server behavior | `api.md` and `app/server/routers/` |
| Terrain processing, mesh generation, caching, or data fetching | `app/server/core/` |
| Notebook-driven Python workflow | `sdk-workflow.md` and `app/session/terrain_session.py` |
| Browser UI, views, layers, or rendering | `modules.md`, `state.md`, and `app/client/static/js/modules/` |
| Export UX or 3D preview | `app/client/static/js/modules/export/` and `app/server/routers/export.py` |
| Region selection, saved regions, or region settings | `app/server/routers/regions.py` and `app/client/static/js/modules/regions/` |

## Frontend Routing

### Explore View

Start in:

- `app/client/static/js/modules/map/`
- `app/client/static/js/modules/regions/`

Typical tasks:

- map controls
- globe behavior
- bbox tools
- region list and selection

### Edit View

Start in:

- `app/client/static/js/modules/dem/`
- `app/client/static/js/modules/layers/`
- `app/client/static/js/modules/ui/`

Typical tasks:

- DEM rendering
- water, land-cover, satellite, or combined layers
- curve editor and panel controls
- stacked canvas behavior

### Extrude View

Start in:

- `app/client/static/js/modules/export/`

Typical tasks:

- model preview
- STL, OBJ, or 3MF downloads
- export progress and options

## Backend Routing

### Router Or Core?

- Edit `app/server/routers/` when the task changes HTTP shape, validation boundary, or endpoint orchestration.
- Edit `app/server/core/` when the task changes processing, caching, export generation, or data fetching internals.

### Route Families

| Concern | Router file |
|---|---|
| Terrain and merge | `app/server/routers/terrain.py` |
| Regions and saved settings | `app/server/routers/regions.py` |
| Exports | `app/server/routers/export.py` |
| Cities and OSM fetch | `app/server/routers/cities.py` |
| Composite city raster | `app/server/routers/composite.py` |
| Cache | `app/server/routers/cache.py` |
| UI settings and metadata | `app/server/routers/settings.py` |

## Session Client Routing

Start in `app/session/terrain_session.py` when:

- a notebook or Python script drives the behavior
- settings mutation changes the request payload
- a server call should become easier to script or visualize

Use `sdk-workflow.md` to map the method to its route family.

## Supporting Libraries

- `../numpy2stl/`: low-level array-to-mesh support
- `geo2stl/`: projections and tile stitching
- `city2stl/`: OSM/building mesh helpers

Only start in these when the change is below the application layer.

## Quick Examples

| I want to... | Start here |
|---|---|
| Add a new DEM processing option | `app/server/core/dem.py` and `app/server/routers/terrain.py` |
| Change how water mask is shown in the UI | `app/client/static/js/modules/layers/` |
| Add a notebook helper around export | `app/session/terrain_session.py` |
| Add a new export format | `app/server/core/export.py`, `app/server/routers/export.py`, then `app/client/static/js/modules/export/` if the UI exposes it |
| Fix region save/load behavior | `app/server/routers/regions.py` and `app/client/static/js/modules/regions/` |
| Understand why a view does not rerender | `state.md`, `arch.md`, and the owning module directory |

## Before Editing

1. Read `../CLAUDE.md` for project rules.
2. Read only the docs for the subsystem you are touching.
3. Check `issues.md` and `../TODO.md` if the task looks related to existing debt.