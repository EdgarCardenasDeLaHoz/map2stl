# SDK Workflow Map — strm2stl

Use this document when you need to connect the notebooks, the Python session client, and the backend routes without tracing everything manually.

## Primary Notebook Path

`../notebooks/API_Terrain.ipynb` is the shortest end-to-end workflow example.

It follows this sequence:

1. Start the server via `TerrainSession.start()`.
2. Inspect or choose regions via `TerrainSession.regions()` and `TerrainSession.select()`.
3. Update settings on `s.settings[...]`.
4. Fetch DEM, water, satellite, city, or merge data.
5. Export assets and optionally verify or slice them.
6. Stop the server.

`../notebooks/Session_API_Reference.ipynb` is the detailed reference notebook. Use it when you need method examples or route coverage rather than the happy path.

## Main Files

- Session client: `../app/session/terrain_session.py`
- API route index: `api.md`
- Runtime architecture: `arch.md`
- Example workflow notebook: `../notebooks/API_Terrain.ipynb`
- Exhaustive method notebook: `../notebooks/Session_API_Reference.ipynb`

## Method To Route Map

| TerrainSession concern | Typical methods | Backend routes | Router modules |
|---|---|---|---|
| Server startup and settings | `start()`, `server_settings()` | settings and source discovery endpoints | `app/server/server.py`, `app/server/routers/settings.py`, `app/server/routers/terrain.py` |
| Region management | `regions()`, `select()`, `save_region()`, settings persistence helpers | `/api/regions*` | `app/server/routers/regions.py` |
| DEM and overlays | `fetch_dem()`, `fetch_water_mask()`, `fetch_satellite()` | `/api/terrain/dem`, `/api/terrain/water-mask`, `/api/terrain/satellite` | `app/server/routers/terrain.py` |
| DEM blending and hydrology | merge-related helpers | `/api/dem/merge` | `app/server/routers/terrain.py` |
| City and OSM features | city fetch and raster helpers | `/api/cities*`, `/api/composite/city-raster` | `app/server/routers/cities.py`, `app/server/routers/composite.py` |
| Export and print pipeline | `export_stl()`, `export_obj()`, split and slicer helpers, verify/slice helpers | `/api/export*` | `app/server/routers/export.py` |
| Cache inspection | cache helpers | `/api/cache*` | `app/server/routers/cache.py` |

## Trace A Notebook Step Quickly

When a notebook cell changes pipeline behavior, use this order:

1. Find the `TerrainSession` method or `s.settings[...]` key in `../app/session/terrain_session.py`.
2. Check which route family it belongs to in `api.md`.
3. Open the relevant router in `../app/server/routers/`.
4. If the work is not in the router, continue into `../app/server/core/`.

## Settings Ownership

The session notebook mutates `s.settings`, but ownership is split by group:

- `projection` applies to all projected layers
- `dem` drives terrain fetch and export inputs
- `water` drives water-mask and land-cover requests
- `satellite` drives satellite imagery resolution
- `city` drives OSM feature fetch and 3D city export behavior
- `export`, `split`, and `slicer` drive model generation and downstream print preparation
- `view` is local visualization only
- `hydrology` affects river extraction and depressions

## Which Notebook To Use

| Notebook | Use it for |
|---|---|
| `../notebooks/API_Terrain.ipynb` | End-to-end terrain session workflow |
| `../notebooks/Session_API_Reference.ipynb` | Method and endpoint coverage |
| `../notebooks/Cities.ipynb` | City/building-specific workflows |
| `../notebooks/Oceans.ipynb` | Ocean and bathymetry workflows |
| `../notebooks/Rivers.ipynb` | River and hydrology workflows |

## Fastest Path For Common Questions

- “Which endpoint does this session action hit?”: `api.md` and `../notebooks/Session_API_Reference.ipynb`
- “Which file owns this notebook behavior?”: `../app/session/terrain_session.py`
- “Where does the real processing happen?”: matching router file, then `../app/server/core/`
- “What is the intended full workflow?”: `../notebooks/API_Terrain.ipynb`