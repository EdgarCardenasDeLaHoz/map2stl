"""
routers/diagnostics.py — /api/diagnostics endpoint.

Returns a snapshot of server-side status so the browser client can show the
user *why* something isn't working (auth keys, DEM source availability, cache
size, and an optional coverage probe for a given bbox). Powers the header's
"Diagnostics" button.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["diagnostics"])


def _dir_size_mb(path: Path) -> float:
    """Total size of a directory tree in MB (0.0 if missing)."""
    if not path or not path.exists():
        return 0.0
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return round(total / (1024 * 1024), 1)


def _auth_summary() -> dict:
    """Key/auth status without importing the auth router's heavy EE check."""
    out = {"opentopo_key": False, "earth_engine": None}
    try:
        from app.server.config import OPENTOPO_API_KEY
        out["opentopo_key"] = bool(OPENTOPO_API_KEY)
    except Exception as exc:  # pragma: no cover - defensive
        out["opentopo_error"] = str(exc)
    return out


def _dem_sources() -> dict:
    """Which DEM sources are usable right now."""
    has_key = _auth_summary()["opentopo_key"]
    try:
        from app.server.config import H5_SRTM_FILE
        h5_ok = bool(H5_SRTM_FILE and Path(H5_SRTM_FILE).exists())
    except Exception:
        h5_ok = False
    return {
        "local": {"label": "Local SRTM Tiles", "available": True,
                  "note": "Coverage is limited; large/continent-scale boxes may be empty."},
        "h5_local": {"label": "Local SRTM H5 (~90m)", "available": h5_ok},
        "opentopo": {"label": "OpenTopography (SRTM/COP/ALOS)", "available": has_key,
                     "note": None if has_key else "Add an API key in the Keys panel to enable."},
    }


def _coverage_probe(request: Request) -> Optional[dict]:
    """If bbox query params are present, report its size + a coverage guess."""
    q = request.query_params
    try:
        north = float(q["north"]); south = float(q["south"])
        east = float(q["east"]);   west = float(q["west"])
    except (KeyError, ValueError, TypeError):
        return None
    span_ns = abs(north - south)
    span_ew = abs(east - west)
    # Local SRTM tiles realistically cover roughly city-to-small-country scale.
    too_large = span_ns > 12 or span_ew > 12
    return {
        "bbox": {"north": north, "south": south, "east": east, "west": west},
        "span_deg": {"ns": round(span_ns, 3), "ew": round(span_ew, 3)},
        "likely_local_coverage": not too_large,
        "recommendation": (
            "This region is large; the local DEM likely has no data for it. "
            "Use an OpenTopography source (needs an API key)."
            if too_large else
            "Region size is within the range the local DEM usually covers."
        ),
    }


@router.get("/api/diagnostics")
async def diagnostics(request: Request):
    """Return a server status snapshot for the Diagnostics panel.

    Optional query params ``north/south/east/west`` add a coverage probe for
    the currently-selected region.
    """
    try:
        from app.server.core.cache import CACHE_ROOT
        cache_path = Path(CACHE_ROOT)
    except Exception:
        cache_path = Path(__file__).parent.parent.parent / "cache"

    payload = {
        "ok": True,
        "auth": _auth_summary(),
        "dem_sources": _dem_sources(),
        "cache": {
            "path": str(cache_path),
            "size_mb": _dir_size_mb(cache_path),
        },
    }
    probe = _coverage_probe(request)
    if probe is not None:
        payload["region_probe"] = probe
    return JSONResponse(content=payload)
