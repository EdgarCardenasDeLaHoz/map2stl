"""
routers/regions.py — /api/regions/* CRUD endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Step 12: reads/writes SQLite via core/db.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from typing import List, Optional

from fastapi import APIRouter
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["regions"])

from app.core.db import get_db, init_db

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
# Routes
# ---------------------------------------------------------------------------

@router.get("/api/regions")
async def list_regions():
    """Return all saved geographic regions."""
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
