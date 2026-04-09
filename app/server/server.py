# ── sys.path bootstrap ────────────────────────────────────────────────────────
# Ensure strm2stl/ (config, routers, core) and Code/ (numpy2stl peer) are
# importable whether this file is run as a script or imported as a module.
import sys as _sys
from pathlib import Path as _Path
_STRM2STL_ROOT = str(_Path(__file__).parent.parent.parent)      # .../strm2stl/
_CODE_ROOT = str(_Path(__file__).parent.parent.parent.parent)   # .../Code/
for _p in (_CODE_ROOT, _STRM2STL_ROOT):
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
del _sys, _Path, _STRM2STL_ROOT, _CODE_ROOT
# ─────────────────────────────────────────────────────────────────────────────

import webbrowser
import threading
import uvicorn
from fastapi import FastAPI, Request
from app.server.config import OSM_CACHE_PATH
# Disk-cache helpers: prune on startup, migrate legacy OSM cache
try:
    from app.server.core.cache import prune_all_caches, migrate_osm_plain_json
    _CACHE_AVAILABLE = True
except ImportError:
    _CACHE_AVAILABLE = False
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, validator, Field
from fastapi.staticfiles import StaticFiles
from typing import Optional, List, Dict, Any
import asyncio
import os
import time
import sys
import json
import numpy as np
from pathlib import Path
import logging
from logging.handlers import RotatingFileHandler

# Configure logging to write to a file — use an absolute path so the log
# file never lands inside a directory watched by uvicorn's auto-reloader.
log_file = str(Path(__file__).parent.parent.parent / "server.log")
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Keep logging to console
        RotatingFileHandler(log_file, maxBytes=5*1024*1024,
                            backupCount=3)  # Log to file with rotation
    ]
)
logger = logging.getLogger(__name__)

selected_location = {}


# ---------------------------------------------------------------------------
# Lifespan (replaces deprecated @app.on_event("startup"))
# ---------------------------------------------------------------------------
from contextlib import asynccontextmanager


@asynccontextmanager
async def _lifespan(app):
    """FastAPI lifespan: run startup tasks, then yield control to the server."""
    import asyncio
    loop = asyncio.get_running_loop()
    # Generate global DEM overview in background
    loop.run_in_executor(None, _build_global_dem_cache, False)
    # Cache maintenance
    if _CACHE_AVAILABLE:
        def _startup_cache_maintenance():
            try:
                migrate_osm_plain_json(OSM_CACHE_PATH)
                counts = prune_all_caches()
                if any(v > 0 for v in counts.values()):
                    logger.info(f"Startup cache prune: {counts}")
            except Exception as e:
                logger.warning(f"Startup cache maintenance failed (non-fatal): {e}")
        loop.run_in_executor(None, _startup_cache_maintenance)
    yield  # server runs here


# FastAPI initialization
app = FastAPI(lifespan=_lifespan)

# Template path using absolute path
templates_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", "..", "client", "templates")
logger.info(f"Templates path: {templates_path}")
templates = Jinja2Templates(directory=templates_path)

# Mount static files (JS/CSS)
# Serve via a custom route so we can add no-cache headers for .js/.css
from fastapi import Response as _Response
from fastapi.responses import FileResponse as _FileResponse
import mimetypes as _mimetypes

static_path = os.path.join(os.path.dirname(
    os.path.abspath(__file__)), "..", "..", "client", "static")

@app.get("/static/{file_path:path}")
async def serve_static(file_path: str):
    full_path = os.path.join(static_path, file_path)
    if not os.path.isfile(full_path):
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
from app.server.routers.regions import router as _regions_router
from app.server.routers.terrain import router as _terrain_router
from app.server.routers.cities import router as _cities_router
from app.server.routers.export import router as _export_router
from app.server.routers.cache import router as _cache_router
from app.server.routers.settings import router as _settings_router
from app.server.routers.composite import router as _composite_router
app.include_router(_regions_router)
app.include_router(_terrain_router)
app.include_router(_cities_router)
app.include_router(_export_router)
app.include_router(_cache_router)
app.include_router(_settings_router)
app.include_router(_composite_router)
logger.info("Routers loaded: regions, terrain, cities, export, cache, settings, composite")


# ============================================================
# Pydantic Schemas — imported from schemas.py (backend refactor step 2)
# ============================================================
try:
    from app.server.schemas import (
        BoundingBox, BoundingBoxLegacy,
        RegionParameters, RegionCreate, RegionResponse, RegionsListResponse,
        RegionSettings, CityRequest,
        DEMRequest, DEMResponse, RawDEMResponse,
        WaterMaskRequest, WaterMaskResponse,
        SatelliteRequest, SatelliteResponse,
        ExportRequest, ExportResponse,
        CacheDirInfo, CacheStatusResponse, CacheClearResponse,
        ProjectionInfo, ProjectionsResponse,
        ColormapInfo, ColormapsResponse,
        DatasetInfo, DatasetsResponse,
        Region,
        ProcessingSpec, MergeLayerSpec, MergeRequest,
    )
except ImportError:
    # Fallback: inline definitions for environments where schemas.py is not on path
    # --- Shared base ---

    class BoundingBox(BaseModel):
        """Geographic bounding box using cardinal directions."""
        north: float = Field(..., ge=-90, le=90,
                             description="Northern latitude bound")
        south: float = Field(..., ge=-90, le=90,
                             description="Southern latitude bound")
        east: float = Field(..., ge=-180, le=180,
                            description="Eastern longitude bound")
        west: float = Field(..., ge=-180, le=180,
                            description="Western longitude bound")

        @validator("north")
        def north_gt_south(cls, v, values):
            if "south" in values and v <= values["south"]:
                raise ValueError("north must be greater than south")
            return v


    # Legacy alias kept for backward-compatibility with older frontend code
    class BoundingBoxLegacy(BaseModel):
        southWestLat: float
        southWestLng: float
        northEastLat: float
        northEastLng: float


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})



# Function to run FastAPI server


def run_server():
    port = int(os.environ.get('UI_PORT', '9000'))
    uvicorn.run(app, host="127.0.0.1", port=port)

# Function to detect Jupyter notebook environment


def _build_global_dem_cache(force: bool = False) -> bool:
    """
    Stitch all elevation tiles for the full globe, downsample to ≤1200 px wide,
    apply a terrain colormap, and save to ui/static/global_dem.png + meta JSON.
    Returns True on success.  Safe to call from a background thread.
    """
    import json as _json
    import cv2 as _cv2
    from PIL import Image as _PILImage

    static_dir = Path(__file__).parent.parent.parent / "client" / "static"
    static_dir.mkdir(exist_ok=True)
    png_path = static_dir / "global_dem.png"
    meta_path = static_dir / "global_dem_meta.json"

    if png_path.exists() and meta_path.exists() and not force:
        logger.info("Global DEM cache already exists — skipping generation.")
        return True

    logger.info("Building global DEM cache (full globe, 90/-90/180/-180) …")
    from geo2stl.geo2stl import stitch_tiles_no_rasterio, tile_files as _tile_files
    if not _tile_files:
        logger.warning("Global DEM cache: no elevation tiles found.")
        return False

    img_arr = stitch_tiles_no_rasterio((90.0, -90.0, 180.0, -180.0))

    if img_arr is None or img_arr.size == 0:
        logger.error("Global DEM cache: stitching returned empty array.")
        return False

    # Downsample to max 1200 px wide
    h_orig, w_orig = img_arr.shape[:2]
    max_w = 1200
    if w_orig > max_w:
        new_h = max(1, int(h_orig * max_w / w_orig))
        img_arr = _cv2.resize(img_arr.astype(np.float32), (max_w, new_h),
                              interpolation=_cv2.INTER_AREA)

    vmin = float(np.nanmin(img_arr))
    vmax = float(np.nanmax(img_arr))
    norm = ((img_arr - vmin) / ((vmax - vmin) or 1.0)).clip(0, 1)

    # 5-stop terrain colormap
    _stops = [
        (0.00, (0.10, 0.22, 0.50)),
        (0.30, (0.20, 0.50, 0.85)),
        (0.45, (0.20, 0.70, 0.30)),
        (0.60, (0.75, 0.70, 0.35)),
        (0.80, (0.55, 0.35, 0.20)),
        (1.00, (1.00, 1.00, 1.00)),
    ]

    def _tc(t):
        for i in range(len(_stops) - 1):
            t0, c0 = _stops[i]
            t1, c1 = _stops[i + 1]
            if t0 <= t <= t1:
                f = (t - t0) / (t1 - t0)
                return tuple(c0[j] + f * (c1[j] - c0[j]) for j in range(3))
        return _stops[-1][1]

    h2, w2 = norm.shape
    rgba = np.zeros((h2, w2, 4), dtype=np.uint8)
    flat = norm.ravel()
    for idx, t in enumerate(flat):
        r, g, b = _tc(float(t))
        row, col = divmod(idx, w2)
        rgba[row, col] = (int(r * 255), int(g * 255), int(b * 255), 220)

    _PILImage.fromarray(rgba, 'RGBA').save(str(png_path), 'PNG')
    meta = {"north": 90.0, "south": -90.0, "east": 180.0, "west": -180.0,
            "vmin": vmin, "vmax": vmax}
    meta_path.write_text(_json.dumps(meta))
    logger.info(f"Global DEM cache saved: {w2}×{h2} px → {png_path}")
    return True



@app.get("/api/global_dem_overview")
async def get_global_dem_overview(regen: bool = False):
    """Serve the cached global DEM PNG (regenerate if regen=true or file missing)."""
    from fastapi.responses import FileResponse as _FR
    import json as _json

    static_dir = Path(__file__).parent.parent.parent / "client" / "static"
    png_path = static_dir / "global_dem.png"
    meta_path = static_dir / "global_dem_meta.json"

    if regen or not png_path.exists() or not meta_path.exists():
        ok = _build_global_dem_cache(force=regen)
        if not ok:
            return JSONResponse(content={"error": "Could not build global DEM cache"}, status_code=500)

    return _FR(str(png_path), media_type="image/png",
               headers={"X-DEM-Meta": meta_path.read_text()})


def in_notebook():
    try:
        from IPython import get_ipython
        if 'IPython' in sys.modules:
            return True
        return False
    except ImportError:
        return False

# Function to open browser and select location


def get_location():
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
    webbrowser.open("http://127.0.0.1:9000")
    logger.info("Waiting for user to select a bounding box...")

    logger.debug(f"Templates path: {templates_path}")

    if not in_notebook():
        # If in Jupyter, use asyncio.sleep for non-blocking operation
        while 'lat' not in selected_location:
            asyncio.run(asyncio.sleep(0.2))
    else:
        # Otherwise, use time.sleep for standard blocking operation
        while 'lat' not in selected_location:
            time.sleep(0.2)

    return selected_location['lat'], selected_location['lng']


if __name__ == "__main__":
    # Run the server when script is executed directly
    logger.info("Starting 3D Maps Globe Selector server...")
    port = int(os.environ.get('UI_PORT', '9000'))
    logger.info(f"Open http://127.0.0.1:{port} in your browser")
    uvicorn.run(app, host="127.0.0.1", port=port)
