"""
routers/regions.py — /api/regions/* CRUD endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Step 12: reads/writes SQLite via core/db.py instead of JSON files.
Falls back to JSON-file storage if core.db cannot be imported.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from functools import partial
from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["regions"])

# ---------------------------------------------------------------------------
# DB import — try core.db, degrade to JSON if unavailable
# ---------------------------------------------------------------------------
try:
    from core.db import get_db, init_db, DB_PATH
    _DB_AVAILABLE = True
except ImportError:
    _DB_AVAILABLE = False
    logger.warning("core.db unavailable — region routes will use JSON files")

# ---------------------------------------------------------------------------
# Path imports — for JSON fallback
# ---------------------------------------------------------------------------
try:
    from config import COORDINATES_PATH, REGION_SETTINGS_PATH
except ImportError:
    from pathlib import Path
    _UI_DIR = Path(__file__).parent.parent
    _STRM2STL_DIR = _UI_DIR.parent
    COORDINATES_PATH = _STRM2STL_DIR / "coordinates.json"
    REGION_SETTINGS_PATH = _STRM2STL_DIR / "region_settings.json"

# ---------------------------------------------------------------------------
# Schema imports — try schemas.py first, inline fallback
# ---------------------------------------------------------------------------
try:
    from schemas import RegionCreate, RegionParameters, RegionSettings
except ImportError:
    from pydantic import BaseModel, Field

    class RegionParameters(BaseModel):
        dim: int = Field(200)
        depth_scale: float = Field(0.5)
        water_scale: float = Field(0.05)
        height: float = Field(10.0)
        base: float = Field(2.0)
        subtract_water: bool = Field(True)
        sat_scale: int = Field(500)

    class RegionCreate(BaseModel):
        name: str
        north: float
        south: float
        east: float
        west: float
        description: Optional[str] = None
        label: Optional[str] = None
        parameters: Optional[RegionParameters] = None

    class RegionSettings(BaseModel):
        dim: Optional[int] = None
        depth_scale: Optional[float] = None
        water_scale: Optional[float] = None
        height: Optional[float] = None
        base: Optional[float] = None
        subtract_water: Optional[bool] = None
        sat_scale: Optional[int] = None
        colormap: Optional[str] = None
        projection: Optional[str] = None
        rescale_min: Optional[float] = None
        rescale_max: Optional[float] = None
        gridlines_show: Optional[bool] = None
        gridlines_count: Optional[int] = None
        elevation_curve: Optional[str] = None
        elevation_curve_points: Optional[List] = None
        dem_source: Optional[str] = None

        def model_dump(self, **kw):
            return self.dict(**kw)


# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

_PARAM_FIELDS = ("dim", "depth_scale", "water_scale", "height", "base", "subtract_water", "sat_scale")


def _row_to_region(row) -> dict:
    """Convert a sqlite3.Row (from regions table) to the API region dict."""
    r = dict(row)
    params = {k: r.pop(k) for k in _PARAM_FIELDS if k in r}
    # subtract_water is stored as INTEGER 0/1
    if "subtract_water" in params:
        params["subtract_water"] = bool(params["subtract_water"])
    r["parameters"] = params
    return r


def _ensure_db() -> None:
    """Create the database schema on first use."""
    init_db()


# ---------------------------------------------------------------------------
# Routes — SQLite path
# ---------------------------------------------------------------------------

@router.get("/api/regions")
async def list_regions():
    """Return all saved geographic regions."""
    if not _DB_AVAILABLE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _list_regions_json)

    try:
        _ensure_db()
        with get_db() as conn:
            rows = conn.execute(
                "SELECT name, label, description, north, south, east, west, "
                "dim, depth_scale, water_scale, height, base, subtract_water, sat_scale "
                "FROM regions ORDER BY name"
            ).fetchall()
        regions = [_row_to_region(r) for r in rows]
        return JSONResponse(content={"regions": regions})
    except Exception as e:
        logger.error(f"Error reading regions: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.post("/api/regions", status_code=201)
async def create_region(region: RegionCreate):
    """Save a new geographic region."""
    if not _DB_AVAILABLE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_create_region_json, region))

    try:
        _ensure_db()
        params = region.parameters or RegionParameters()
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO regions "
                "(name, label, description, north, south, east, west, "
                " dim, depth_scale, water_scale, height, base, subtract_water, sat_scale) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    region.name, region.label, region.description,
                    region.north, region.south, region.east, region.west,
                    params.dim, params.depth_scale, params.water_scale,
                    params.height, params.base,
                    int(params.subtract_water), params.sat_scale,
                ),
            )
            conn.commit()
        payload = region.model_dump()
        if payload.get("parameters") is None:
            payload["parameters"] = RegionParameters().model_dump()
        return JSONResponse(content=payload, status_code=201)
    except sqlite3.IntegrityError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Error creating region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.put("/api/regions/{name}")
async def update_region(name: str, region: RegionCreate):
    """Update an existing saved region by name."""
    if not _DB_AVAILABLE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_update_region_json, name, region))

    try:
        _ensure_db()
        params = region.parameters or RegionParameters()
        with get_db() as conn:
            cur = conn.execute(
                "UPDATE regions SET "
                "label=?, description=?, north=?, south=?, east=?, west=?, "
                "dim=?, depth_scale=?, water_scale=?, height=?, base=?, subtract_water=?, sat_scale=? "
                "WHERE name=?",
                (
                    region.label, region.description,
                    region.north, region.south, region.east, region.west,
                    params.dim, params.depth_scale, params.water_scale,
                    params.height, params.base,
                    int(params.subtract_water), params.sat_scale,
                    name,
                ),
            )
            conn.commit()
            if cur.rowcount == 0:
                return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
        return JSONResponse(content=region.model_dump())
    except Exception as e:
        logger.error(f"Error updating region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.delete("/api/regions/{name}")
async def delete_region(name: str):
    """Delete a saved region by name. ON DELETE CASCADE removes its settings."""
    if not _DB_AVAILABLE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_delete_region_json, name))

    try:
        _ensure_db()
        with get_db() as conn:
            cur = conn.execute("DELETE FROM regions WHERE name=?", (name,))
            conn.commit()
            if cur.rowcount == 0:
                return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
        return JSONResponse(content={"status": "deleted", "name": name})
    except Exception as e:
        logger.error(f"Error deleting region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.get("/api/regions/{name}/settings")
async def get_region_settings(name: str):
    """Fetch saved panel settings for a region. Returns empty settings if none saved yet."""
    if not _DB_AVAILABLE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_get_region_settings_json, name))

    try:
        _ensure_db()
        with get_db() as conn:
            row = conn.execute(
                "SELECT settings_json FROM region_settings WHERE region_name=?", (name,)
            ).fetchone()
        if row is None:
            return JSONResponse(content={"name": name, "settings": {}})
        settings = json.loads(row["settings_json"] or "{}")
        return JSONResponse(content={"name": name, "settings": settings})
    except Exception as e:
        logger.error(f"Error fetching region settings: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.put("/api/regions/{name}/settings")
async def save_region_settings_route(name: str, settings: RegionSettings):
    """Save or update all panel settings for a region."""
    if not _DB_AVAILABLE:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, partial(_save_region_settings_json, name, settings))

    try:
        _ensure_db()
        payload = {k: v for k, v in settings.model_dump().items() if v is not None}
        settings_json = json.dumps(payload)
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO region_settings (region_name, settings_json) VALUES (?,?)",
                (name, settings_json),
            )
            conn.commit()
        return JSONResponse(content={"status": "saved", "name": name, "settings": payload})
    except Exception as e:
        logger.error(f"Error saving region settings: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# JSON fallback implementations (used when _DB_AVAILABLE is False)
# ---------------------------------------------------------------------------

def _list_regions_json():
    try:
        with open(COORDINATES_PATH, "r") as f:
            data = json.load(f)
        return JSONResponse(content=data)
    except FileNotFoundError:
        return JSONResponse(content={"regions": []})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _create_region_json(region: RegionCreate):
    try:
        data = json.loads(COORDINATES_PATH.read_text()) if COORDINATES_PATH.exists() else {"regions": []}
        payload = region.model_dump()
        if payload.get("parameters") is None:
            payload["parameters"] = RegionParameters().model_dump()
        data["regions"].append(payload)
        COORDINATES_PATH.write_text(json.dumps(data, indent=2))
        return JSONResponse(content=payload, status_code=201)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _update_region_json(name: str, region: RegionCreate):
    try:
        data = json.loads(COORDINATES_PATH.read_text()) if COORDINATES_PATH.exists() else {"regions": []}
        regions = data.get("regions", [])
        for i, r in enumerate(regions):
            if r.get("name") == name:
                payload = region.model_dump()
                if payload.get("parameters") is None:
                    payload["parameters"] = r.get("parameters", RegionParameters().model_dump())
                regions[i] = payload
                COORDINATES_PATH.write_text(json.dumps(data, indent=2))
                return JSONResponse(content=payload)
        return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _delete_region_json(name: str):
    try:
        if not COORDINATES_PATH.exists():
            return JSONResponse(content={"error": "Region not found"}, status_code=404)
        data = json.loads(COORDINATES_PATH.read_text())
        original_count = len(data.get("regions", []))
        data["regions"] = [r for r in data["regions"] if r.get("name") != name]
        if len(data["regions"]) == original_count:
            return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
        COORDINATES_PATH.write_text(json.dumps(data, indent=2))
        if REGION_SETTINGS_PATH.exists():
            try:
                sd = json.loads(REGION_SETTINGS_PATH.read_text())
                if name in sd:
                    del sd[name]
                    REGION_SETTINGS_PATH.write_text(json.dumps(sd, indent=2))
            except Exception:
                pass
        return JSONResponse(content={"status": "deleted", "name": name})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _get_region_settings_json(name: str):
    try:
        if not REGION_SETTINGS_PATH.exists():
            return JSONResponse(content={"name": name, "settings": {}})
        data = json.loads(REGION_SETTINGS_PATH.read_text())
        if name not in data:
            return JSONResponse(content={"name": name, "settings": {}})
        return JSONResponse(content={"name": name, "settings": data[name]})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _save_region_settings_json(name: str, settings: RegionSettings):
    try:
        data = json.loads(REGION_SETTINGS_PATH.read_text()) if REGION_SETTINGS_PATH.exists() else {}
        payload = {k: v for k, v in settings.model_dump().items() if v is not None}
        data[name] = payload
        REGION_SETTINGS_PATH.write_text(json.dumps(data, indent=2))
        return JSONResponse(content={"status": "saved", "name": name, "settings": payload})
    except Exception as e:
        return JSONResponse(content={"error": str(e)}, status_code=500)
