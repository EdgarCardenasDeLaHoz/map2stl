# strm2stl — 3D Map Generator

Turn geographic and elevation data (SRTM/DEM terrain, GEBCO bathymetry,
OpenStreetMap buildings/roads) into 3D-printable models (STL / OBJ / 3MF).
strm2stl is a **FastAPI web app** with an interactive map UI, plus a **Python
SDK** for driving the same pipeline from notebooks or scripts.

> **New here?** Read [`CLAUDE.md`](CLAUDE.md) first — it's the project index —
> then [`docs/ai-agent-onboarding.md`](docs/ai-agent-onboarding.md) for the
> shortest correct reading path. The workspace-level overview is in the parent
> [`../README.md`](../README.md).

## Features

- **Terrain** — DEM/SRTM elevation → 3D surfaces, with colormaps, projections, and adjustable resolution.
- **Cities** — OSM buildings and roads extracted and extruded (`city2stl/`).
- **Oceans** — GEBCO bathymetry with region-specific processing.
- **Building heights** — CNN + skyline-CV estimation for realistic city models.
- **Web UI** — draw/select a bounding box, preview stacked layers, export a mesh.
- **Python SDK** — `TerrainSession` drives the server over HTTP from notebooks.

## Install

Requires **Python 3.11+**. The venv lives *outside* OneDrive at
`~\.venvs\strm2stl` (OneDrive sync locks binary packages, and a synced venv breaks
on the other PC — never put the venv inside the synced tree).

```powershell
# From this directory (strm2stl/). Run once per PC; safe to re-run.
powershell -ExecutionPolicy Bypass -File scripts\setup-venv.ps1
```

The script installs `requirements.txt` + `requirements-dev.txt`, sends bytecode to
`~\.cache\pycache`, and points the `nbstripout` git filter at the venv (git refuses
to stage notebooks until it does).

The sibling `../numpy2stl/` package (mesh generation) is used as a library and
is expected on the path alongside this repo — see [`../numpy2stl/README.md`](../numpy2stl/README.md).

## Run the web app

```powershell
& "$HOME\.venvs\strm2stl\Scripts\python.exe" -m uvicorn app.server.server:app --host 127.0.0.1 --port 9000 --reload
```

Then open <http://127.0.0.1:9000>. Equivalent shortcuts:

- `make serve` (uses the Makefile's venv detection), or
- **`..\Start 3D Maps.bat`** on Windows (starts the server and opens the browser).

The UI has three views:

- **Explore** — 2D Leaflet map + 3D globe; draw or select a bounding box, manage saved regions.
- **Edit** — DEM preview with a stacked-layer canvas (elevation, water mask, satellite, gridlines); adjust colormap, projection, resolution, and bbox; export STL/OBJ/3MF.
- **Extrude** — Three.js 3D preview with orbit controls; download the final model.

Saved regions and their settings persist in a **SQLite database** (`data.db`,
gitignored) via the regions API (`app/server/routers/regions.py`,
`app/server/core/db.py`) — this replaced the older `coordinates.json` /
`region_settings.json` JSON files.

## Use the Python SDK

`app/session/terrain_session.py` is a Python client that manages and talks to
the FastAPI server over HTTP:

```python
from app.session.terrain_session import TerrainSession

s = TerrainSession(port=9000)
s.start()                                                            # launch/attach the server
s.create_region("Barcelona", north=41.395, south=41.375, east=2.175, west=2.145)
s.select("Barcelona")
s.fetch_dem()                                                        # fetch terrain for the region
s.export_obj()                                                       # write an OBJ of the current model
```

The clearest end-to-end example is
[`notebooks/API_Terrain.ipynb`](notebooks/API_Terrain.ipynb); method-to-endpoint
coverage is in [`notebooks/Session_API_Reference.ipynb`](notebooks/Session_API_Reference.ipynb).
See [`docs/sdk-workflow.md`](docs/sdk-workflow.md) for notebook → SDK → route tracing.

> There is no standalone command-line entry point; the web UI, the SDK, and the
> notebooks are the supported ways to drive the pipeline.

## Tests

```powershell
& "$HOME\.venvs\strm2stl\Scripts\python.exe" -m pytest tests/ -v   # or: make test
```

`pytest.ini` collects `tests/` and `../numpy2stl/tests`. The `tests/e2e/`
Playwright suite needs a live server + `pytest-playwright` and is excluded from
the default run (invoke explicitly with `--browser chromium`).

## Dependencies

Key libraries: `fastapi` + `uvicorn` (server), `numpy`/`scipy`/`shapely`
(geo/numeric), `trimesh` + `triangle` + the local `numpy2stl` (mesh),
`opencv-python`/`scikit-image`/`pillow` (imagery), `rasterio` (geo raster I/O),
`jinja2` (templates). Optional extras (OSM via `osmnx`, CNN height prediction
via `torch`/`timm`, MobileSAM) are commented in `requirements.txt`. See that
file for the full pinned list.

## Project structure

```
strm2stl/
├── app/
│   ├── server/        # FastAPI backend — routers/ + core/ + schemas.py
│   ├── client/        # Browser UI — static/js modules, templates/index.html
│   └── session/       # Python SDK (TerrainSession)
├── geo2stl/           # Map projections + DEM tile stitching (library)
├── city2stl/          # OSM/building → mesh + skyline CV pipeline (library)
├── tests/             # pytest suite (e2e/ needs Playwright)
├── notebooks/         # Jupyter examples (API_Terrain, Session_API_Reference, …)
├── tools/             # Utility + ad-hoc diagnostic scripts, slicer configs
├── docs/              # Architecture, API, module, and state reference
├── Makefile           # serve / test / lint / fmt / install / clean-runs
├── requirements*.txt  # Pinned dependencies
└── CLAUDE.md          # Project index (read first)
```

## Documentation

| Topic | File |
|---|---|
| Project index (read first) | [`CLAUDE.md`](CLAUDE.md) |
| Onboarding / reading path | [`docs/ai-agent-onboarding.md`](docs/ai-agent-onboarding.md) |
| Docs index | [`docs/README.md`](docs/README.md) |
| Architecture + data flow | [`docs/arch.md`](docs/arch.md) |
| API routes + Pydantic models | [`docs/api.md`](docs/api.md) |
| Frontend modules / state | [`docs/modules.md`](docs/modules.md), [`docs/state.md`](docs/state.md) |
| Known issues / status | [`docs/issues.md`](docs/issues.md) |
| Phase G/H model history (archived) | [`docs/phase-history/`](docs/phase-history/) |

## License

MIT — see [`../LICENSE`](../LICENSE).
