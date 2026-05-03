# Task Routing — strm2stl

_Last updated: 2026-05-03_

Use this document to choose the right files before editing.

## First Decision

| If the task is about... | Start here |
|---|---|
| Route shape, request payloads, or server behavior | `api.md` and `app/server/routers/` |
| Terrain data fetching, projection, or scale math | `geo2stl/` (dem.py, projections.py, raster.py) |
| OSM / city rasterization, cache staleness, building heights | `city2stl/` (rasterize.py, cache_policy.py, height/) |
| Disk cache, export generation, height provider orchestration | `app/server/core/` |
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

### Three-Layer Model

```
routers/   → HTTP adapters (request parsing, delegation, response formatting)
core/      → server-side coordination (cache I/O, SQLite, export generation, height orchestration)
geo2stl/   → terrain domain (DEM fetch, projection, tile stitch, raster scale, satellite, hydrology)
city2stl/  → city domain (OSM rasterize, cache policy, building heights)
numpy2stl/ → mesh generation (array → STL triangles)
```

**Decision rule:**
- If the change is about HTTP shape → `routers/`
- If the change is about server-side state (cache, DB, export file) → `core/`
- If the change is about terrain math or projection → `geo2stl/`
- If the change is about OSM, buildings, or city height → `city2stl/`
- If the change needs to work without the server → **put it in the library, not in core**

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

- `../numpy2stl/`: array-to-mesh (STL triangulation). Only via `core/export.py`
- `geo2stl/`: terrain domain library — DEM fetch, projection, tile stitch, raster scale, hydrology, satellite
- `city2stl/`: city domain library — OSM fetch, rasterize, cache staleness, building heights, roof classification

Start here when the change is usable from notebooks or scripts without the server running (e.g. projection math, raster scale, cache staleness predicates).

## Quick Examples

| I want to... | Start here |
|---|---|
| Add a new DEM source or change fetch logic | `geo2stl/dem.py` and `app/server/routers/terrain.py` |
| Change projection behavior | `geo2stl/projections.py` |
| Change satellite scale calculation | `geo2stl/raster.py` |
| Change OSM rasterization | `city2stl/rasterize.py` |
| Change OSM cache staleness rules | `city2stl/cache_policy.py` |
| Add a new building height provider | `city2stl/height/providers/` and `app/server/core/height/service.py` |
| Change how water mask is shown in the UI | `app/client/static/js/modules/layers/` |
| Add a notebook helper around export | `app/session/terrain_session.py` |
| Add a new export format | `app/server/core/export.py`, `app/server/routers/export.py`, then `app/client/static/js/modules/export/` if the UI exposes it |
| Fix region save/load behavior | `app/server/routers/regions.py` and `app/client/static/js/modules/regions/` |
| Add or change a cache route | `app/server/routers/cache.py` + `app/server/core/cache_inspector.py` |
| Understand why a view does not rerender | `state.md`, `arch.md`, and the owning module directory |

## Before Editing

1. Read `../CLAUDE.md` for project rules.
2. Read only the docs for the subsystem you are touching.
3. Check `issues.md` and `todos/README.md` if the task looks related to existing debt.