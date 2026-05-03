"""
core/validation.py — Shared request parsing, bbox validation, and async helpers.

Consolidates helpers previously duplicated across routers (terrain.py had
_parse_float/_parse_int/_parse_bool/_b64/_validate_bbox/_validate_dim;
cities.py had inline bbox-diagonal math).
"""

from __future__ import annotations

import asyncio
import base64 as _b64m
import math
from dataclasses import dataclass
from functools import partial
from typing import Any

import numpy as np
from fastapi import Query, Request
from fastapi.responses import JSONResponse

from app.server.config import MAX_DIM, MAX_BBOX_DIAGONAL_KM

# ---------------------------------------------------------------------------
# Metres-per-degree constant (equatorial)
# ---------------------------------------------------------------------------

METRES_PER_DEGREE: float = 111_320.0
EARTH_RADIUS_KM: float = 6371.0


@dataclass(frozen=True, slots=True)
class BboxQueryParams:
    north: float | None
    south: float | None
    east: float | None
    west: float | None


# ---------------------------------------------------------------------------
# Query-parameter parsers
# ---------------------------------------------------------------------------

def parse_float(params: Any, key: str, default: float | None = None) -> float | None:
    """Extract a float from query params, returning *default* on failure."""
    val = params.get(key)
    if val is None or val == '':
        return default
    try:
        return float(val)
    except (ValueError, TypeError):
        return default


def parse_int(params: Any, key: str, default: int | None = None) -> int | None:
    """Extract an int from query params, returning *default* on failure."""
    val = params.get(key)
    if val is None or val == '':
        return default
    try:
        return int(float(val))
    except (ValueError, TypeError):
        return default


def parse_bool(params: Any, key: str, default: bool = False) -> bool:
    """Extract a boolean from query params (true/1/yes/on → True)."""
    val = params.get(key)
    if val is None or val == '':
        return default
    return val.lower() in ('true', '1', 'yes', 'on')


def parse_bbox_query(
    request: Request,
    north: float | None = Query(None, description="Northern latitude bound (degrees)"),
    south: float | None = Query(None, description="Southern latitude bound (degrees)"),
    east: float | None = Query(None, description="Eastern longitude bound (degrees)"),
    west: float | None = Query(None, description="Western longitude bound (degrees)"),
) -> BboxQueryParams:
    """Parse bbox values from request query params with existing fallback semantics.

    Keeps the current terrain-router behavior where malformed values become
    ``None`` and are later translated into the existing 400 error response
    shape by ``validate_bbox()``.
    """
    params = request.query_params
    return BboxQueryParams(
        north=parse_float(params, "north"),
        south=parse_float(params, "south"),
        east=parse_float(params, "east"),
        west=parse_float(params, "west"),
    )


# ---------------------------------------------------------------------------
# Binary encoding
# ---------------------------------------------------------------------------

def b64_encode(arr: np.ndarray) -> str:
    """Encode a numpy array as base64 little-endian float32 for binary transport."""
    return _b64m.b64encode(arr.ravel().astype(np.float32).tobytes()).decode("ascii")


def model_to_dict(model: Any, **kwargs) -> dict:
    """Return a plain dict from a Pydantic model across v1/v2 APIs."""
    if hasattr(model, "model_dump"):
        return model.model_dump(**kwargs)
    return model.dict(**kwargs)


# ---------------------------------------------------------------------------
# Bbox validation
# ---------------------------------------------------------------------------

def validate_bbox(north: float | None, south: float | None,
                  east: float | None, west: float | None) -> JSONResponse | None:
    """Return a JSONResponse error if bbox is missing or incoherent, else None."""
    if any(v is None for v in (north, south, east, west)):
        return JSONResponse(content={"error": "north, south, east, west are all required"},
                            status_code=400)
    if not (-90 <= south <= 90 and -90 <= north <= 90):
        return JSONResponse(content={"error": "latitude must be between -90 and 90"},
                            status_code=400)
    if not (-180 <= west <= 180 and -180 <= east <= 180):
        return JSONResponse(content={"error": "longitude must be between -180 and 180"},
                            status_code=400)
    if north <= south:
        return JSONResponse(content={"error": "north must be greater than south"},
                            status_code=400)
    if east <= west:
        return JSONResponse(content={"error": "east must be greater than west"},
                            status_code=400)
    return None


def validate_dim(dim: int | None, max_dim: int = MAX_DIM) -> JSONResponse | None:
    """Return a JSONResponse error if dim is out of range, else None."""
    if dim is not None and not (1 <= dim <= max_dim):
        return JSONResponse(content={"error": f"dim must be between 1 and {max_dim}"},
                            status_code=400)
    return None


def validate_bbox_diagonal(north: float, south: float,
                           east: float, west: float,
                           max_km: float = MAX_BBOX_DIAGONAL_KM) -> tuple[float, JSONResponse | None]:
    """Return (diagonal_km, error_response_or_None).

    Uses the Haversine-approximation diagonal check from cities.py.
    """
    d_lat = (north - south) * math.pi / 180
    d_lon = ((east - west) * math.pi
             * math.cos(((north + south) / 2) * math.pi / 180) / 180)
    diag_km = math.sqrt((EARTH_RADIUS_KM * d_lat) ** 2
                        + (EARTH_RADIUS_KM * d_lon) ** 2)
    if diag_km > max_km:
        return diag_km, JSONResponse(
            content={"error": f"Bounding box too large ({diag_km:.1f} km diagonal, "
                              f"max {max_km:.0f} km)"},
            status_code=422)
    return diag_km, None


# ---------------------------------------------------------------------------
# Async executor helper
# ---------------------------------------------------------------------------

async def run_sync(fn, *args, **kwargs):
    """Run a sync function in the default executor without boilerplate.

    Replaces the repeated pattern::

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(None, partial(fn, *args, **kwargs))
    """
    loop = asyncio.get_running_loop()
    if kwargs:
        return await loop.run_in_executor(None, partial(fn, *args, **kwargs))
    if args:
        return await loop.run_in_executor(None, partial(fn, *args))
    return await loop.run_in_executor(None, fn)
