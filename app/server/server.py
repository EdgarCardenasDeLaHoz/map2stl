# app, geo2stl, city2stl and numpy2stl are installed packages (pip install -e,
# see scripts/setup-venv.ps1), so no sys.path bootstrap is needed here.
import logging
import mimetypes as _mimetypes
import os
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse as _FileResponse
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.server.config import OSM_CACHE_PATH
from app.server.core.cache import migrate_osm_plain_json, prune_all_caches
from app.server.core.dem_store import DemGone
from app.server.routers.auth import router as _auth_router
from app.server.routers.cache import router as _cache_router
from app.server.routers.cities import router as _cities_router
from app.server.routers.composite import router as _composite_router
from app.server.routers.diagnostics import router as _diagnostics_router
from app.server.routers.export import router as _export_router
from app.server.routers.geocode import router as _geocode_router
from app.server.routers.guides import guide_path as _guide_path
from app.server.routers.guides import router as _guides_router
from app.server.routers.height import router as _height_router
from app.server.routers.layers import router as _layers_router
from app.server.routers.regions import router as _regions_router
from app.server.routers.registration import router as _registration_router
from app.server.routers.reports import router as _reports_router
from app.server.routers.settings import router as _settings_router
from app.server.routers.terrain import router as _terrain_router
from app.server.routers.usage import router as _usage_router

# Configure logging to write to a file — use an absolute path so the log
# file never lands inside a directory watched by uvicorn's auto-reloader.
# Under MAP2STL_TEST_MODE (set by tests/conftest.py) skip the file handler so
# importing the app in tests neither writes server.log nor holds it open.
log_file = str(Path(__file__).parent.parent.parent / "server.log")
_log_handlers: list[logging.Handler] = [logging.StreamHandler()]  # console
if os.environ.get("MAP2STL_TEST_MODE", "0") != "1":
    _log_handlers.append(RotatingFileHandler(
        log_file, maxBytes=5*1024*1024, backupCount=3))  # file with rotation
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event("startup"))
# ---------------------------------------------------------------------------


@asynccontextmanager
async def _lifespan(app):
    """FastAPI lifespan: run startup tasks, then yield control to the server."""
    import asyncio
    loop = asyncio.get_running_loop()
    # Cache maintenance
    def _startup_cache_maintenance():
        try:
            migrate_osm_plain_json(OSM_CACHE_PATH)
            counts = prune_all_caches()
            if any(v > 0 for v in counts.values()):
                logger.info(f"Startup cache prune: {counts}")
        except Exception as e:
            logger.warning(
                f"Startup cache maintenance failed (non-fatal): {e}")
    loop.run_in_executor(None, _startup_cache_maintenance)
    yield  # server runs here


# ---------------------------------------------------------------------------
# OpenAPI metadata — populates Swagger UI (/docs) and ReDoc (/redoc)
# ---------------------------------------------------------------------------
_API_DESCRIPTION = """\
# 3D Maps — Terrain-to-3D Pipeline API

**map2stl** is a terrain-to-3D pipeline with three main surfaces:

- **FastAPI backend** (`app/server/`) — this API
- **Browser client** (`app/client/`) — interactive web UI
- **Python SDK** (`app/session/terrain_session.py`) — notebook-driven workflows

## Workflow

1. **Select or save** a geographic region (bounding box).
2. **Fetch terrain** (DEM) and optional overlays (water mask, ESA land cover, satellite, city/OSM, hydrology).
3. **Inspect or merge** layers with projection alignment.
4. **Export** STL, OBJ, or 3MF assets for 3D printing.

## Architecture

### Data Flow

```
Region (bbox) → Fetch layers (Plate Carrée) → Rasterize → Project → Client render
```

All raster layers follow a uniform projection pipeline:
1. Fetch raw data in **Plate Carrée** (WGS84 / EPSG:4326)
2. Rasterize to a 2D grid using the layer-specific engine
3. Apply map projection via `project_coordinates()` from `geo2stl`
4. Return the projected grid to the client

### Projections

Supported projections: `none` (Plate Carrée), `cosine`, `mercator`, \
`equidistant`, `lambert`, `sinusoidal`.

- **Continuous data** (DEM, water mask, hydrology): bilinear interpolation, `fill_value=NaN`
- **Categorical data** (ESA land cover class IDs): nearest-neighbour interpolation, `fill_value=0`
- **`clip_valid_region`** strips projection padding (all-NaN border rows/columns)

### Caching

Server-side disk cache stores fetched/projected arrays as `.npz` files:
- Namespaces: `dem/`, `water/`, `satellite/`, `osm/`, `opentopo/`
- Cache keys: MD5 of `namespace:bbox:extra_params`
- Cache can be cleared per-region or globally

### SDK / Notebooks

The Python SDK (`TerrainSession`) wraps every API endpoint for use in Jupyter notebooks.
See `notebooks/API_Terrain.ipynb` for the end-to-end workflow.

---

*For full project documentation, visit `/project-docs/`. \
For Python API reference, visit `/api-reference/`.*
"""

_OPENAPI_TAGS = [
    {
        "name": "terrain",
        "description": (
            "Digital Elevation Model (DEM) fetching, water masks, ESA land cover, "
            "satellite imagery, hydrology, layer merging, and export previews. "
            "All raster endpoints accept `projection` and `clip_valid_region` parameters "
            "for server-side map projection."
        ),
    },
    {
        "name": "regions",
        "description": (
            "CRUD operations for saved geographic regions (bounding boxes). "
            "Each region can store rendering/export settings as a JSON blob."
        ),
    },
    {
        "name": "cities",
        "description": (
            "OpenStreetMap building, road, and waterway data. Includes raw GeoJSON "
            "fetching, rasterization to height-map grids, and 3MF export with "
            "extruded building prisms."
        ),
    },
    {
        "name": "export",
        "description": (
            "Generate downloadable 3D model files (STL, OBJ, 3MF) from DEM data. "
            "Includes cross-section exports and Three.js preview meshes."
        ),
    },
    {
        "name": "cache",
        "description": (
            "Server-side disk cache management. Check status, clear by region "
            "or globally. Namespaces: dem, water, satellite, osm, opentopo."
        ),
    },
    {
        "name": "settings",
        "description": (
            "Application configuration: available map projections, colormaps "
            "for DEM rendering, and elevation/land-cover dataset metadata."
        ),
    },
    {
        "name": "composite",
        "description": (
            "Composite DEM operations: rasterize OSM features to height-delta "
            "grids using PIL for blending with the primary DEM."
        ),
    },
    {
        "name": "layers",
        "description": (
            "STL/OBJ mesh import as a registered layer: upload, convert to a "
            "geo-referenced heightmap, and fit a manual point-pair affine "
            "registration against the current DEM/city view."
        ),
    },
]

# FastAPI initialization
app = FastAPI(
    title="3D Maps — map2stl API",
    version="1.0.0",
    description=_API_DESCRIPTION,
    openapi_tags=_OPENAPI_TAGS,
    lifespan=_lifespan,
)

# Compress JSON responses. The DEM payload is base64-encoded float32 elevation
# (measured: 1.9 MB for a 600x600 grid) and compresses well; STL/3MF downloads
# are already binary and are streamed as FileResponse, which GZipMiddleware
# leaves alone below the size threshold it applies per response.
app.add_middleware(GZipMiddleware, minimum_size=1024)


@app.exception_handler(DemGone)
async def _dem_gone(_request: Request, exc: DemGone):
    # An expired or pre-restart DEM handle: the client should reload, not retry.
    return JSONResponse(status_code=410, content={"error": str(exc)})

# Template path using absolute path
templates_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", "client", "templates")
logger.info(f"Templates path: {templates_path}")
templates = Jinja2Templates(directory=templates_path)

# ---------------------------------------------------------------------------
# Mount built documentation sites (MkDocs + Sphinx) if they exist
# ---------------------------------------------------------------------------
_map2stl_root = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", "..")
_docs_site_path = os.path.join(_map2stl_root, "docs_site")
_sphinx_site_path = os.path.join(_map2stl_root, "sphinx_site")

if os.path.isdir(_docs_site_path):
    app.mount("/project-docs", StaticFiles(directory=_docs_site_path,
              html=True), name="project-docs")
    logger.info(f"Mounted /project-docs -> {_docs_site_path}")
else:
    logger.info(
        "MkDocs site not built yet -- run 'mkdocs build' in map2stl/ to enable /project-docs")

if os.path.isdir(_sphinx_site_path):
    app.mount("/api-reference", StaticFiles(directory=_sphinx_site_path,
              html=True), name="api-reference")
    logger.info(f"Mounted /api-reference -> {_sphinx_site_path}")
else:
    logger.info(
        "Sphinx site not built yet -- run 'sphinx-build sphinx/ sphinx_site/' in map2stl/ to enable /api-reference")

# Mount static files (JS/CSS)
# Serve via a custom route so we can add no-cache headers for .js/.css

static_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", "client", "static")

# Vite build output — checked as fallback when file not found in static_path.
# Maps /static/js/vue-main.js → dist/js/vue-main.js etc.
dist_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", "..", "dist")

# dist/ is gitignored, so a fresh clone has no vue-main.js and the whole Vue
# half of the UI 404s — the page renders blank with nothing but a console
# error to explain it. Say so at startup instead.
if not os.path.isfile(os.path.join(dist_path, "js", "vue-main.js")):
    logger.error(
        "Vue bundle missing (%s). The UI will render blank. "
        "Run 'npm install && npm run build' in map2stl/ to build it.",
        os.path.normpath(os.path.join(dist_path, "js", "vue-main.js")),
    )


@app.get("/static/{file_path:path}")
async def serve_static(file_path: str):
    full_path = os.path.join(static_path, file_path)
    if not os.path.isfile(full_path):
        # Fallback: check Vite dist/ for built bundles (e.g. vue-main.js)
        dist_full = os.path.join(dist_path, file_path)
        if os.path.isfile(dist_full):
            full_path = dist_full
        else:
            from fastapi import HTTPException
            raise HTTPException(status_code=404)
    mime, _ = _mimetypes.guess_type(full_path)
    headers = {}
    if full_path.endswith((".js", ".css")):
        headers["Cache-Control"] = "no-store"
    return _FileResponse(full_path, media_type=mime or "application/octet-stream", headers=headers)

if not os.path.isdir(static_path):
    logger.warning(f"Static path not found: {static_path}")

# ---------------------------------------------------------------------------
# Routers (backend refactor step 6)
# ---------------------------------------------------------------------------
app.include_router(_auth_router)
app.include_router(_regions_router)
app.include_router(_terrain_router)
app.include_router(_cities_router)
app.include_router(_export_router)
app.include_router(_cache_router)
app.include_router(_settings_router)
app.include_router(_composite_router)
app.include_router(_height_router)
app.include_router(_diagnostics_router)
app.include_router(_layers_router)
app.include_router(_reports_router)
app.include_router(_guides_router)
app.include_router(_registration_router)
app.include_router(_geocode_router)
app.include_router(_usage_router)
logger.info(
    "Routers loaded: regions, terrain, cities, export, cache, settings, composite, height, "
    "diagnostics, layers, reports, guides, registration")


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    """Browser for the skyline pipeline's rendered reports; data comes from
    /api/reports/index in app/server/routers/reports.py."""
    return templates.TemplateResponse("reports.html", {"request": request})


@app.get("/guides", response_class=HTMLResponse)
async def guides_page(request: Request):
    """Step-by-step SOPs (docs/guides/*.md) rendered in the app; data comes from
    /api/guides in app/server/routers/guides.py. Deep link: /guides#<slug>/<anchor>."""
    return templates.TemplateResponse(request, "guides.html")


@app.get("/guides/{slug}", response_class=HTMLResponse)
async def guides_page_for(request: Request, slug: str):
    """The Guides page opening ``slug`` first (404 for an unknown guide)."""
    _guide_path(slug)
    return templates.TemplateResponse(request, "guides.html")


# Function to run FastAPI server


def run_server():
    port = int(os.environ.get('UI_PORT', '9000'))
    uvicorn.run(app, host="127.0.0.1", port=port)


if __name__ == "__main__":
    # Run the server when script is executed directly
    logger.info("Starting 3D Maps Globe Selector server...")
    port = int(os.environ.get('UI_PORT', '9000'))
    logger.info(f"Open http://127.0.0.1:{port} in your browser")
    uvicorn.run(app, host="127.0.0.1", port=port)
