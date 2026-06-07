"""
routers/auth.py — /api/auth/* endpoints for service authentication status and key management.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Body
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])

_CONFIG_PATH = Path(__file__).parent.parent.parent / "config.json"


def _check_earth_engine() -> dict:
    try:
        import ee
        ee.Initialize()
        return {"authenticated": True, "error": None}
    except Exception as exc:
        msg = str(exc)
        if "Please authorize" in msg or "credentials" in msg.lower() or "authenticate" in msg.lower():
            return {"authenticated": False, "error": "Not authenticated"}
        return {"authenticated": False, "error": msg}


def _check_opentopo() -> dict:
    from app.server.config import OPENTOPO_API_KEY
    has_key = bool(OPENTOPO_API_KEY)
    return {"authenticated": has_key, "error": None if has_key else "No API key configured"}


@router.get("/api/auth/status")
async def auth_status():
    """Return authentication status for all external services."""
    return JSONResponse(content={
        "earth_engine": _check_earth_engine(),
        "opentopo": _check_opentopo(),
    })


@router.post("/api/auth/opentopo-key")
async def save_opentopo_key(body: dict = Body(...)):
    """Save an OpenTopography API key to config.json."""
    key = (body.get("key") or "").strip()
    if not key:
        return JSONResponse(status_code=400, content={"error": "Key must not be empty"})

    try:
        cfg = {}
        if _CONFIG_PATH.exists():
            cfg = json.loads(_CONFIG_PATH.read_text())
        cfg["opentopo_api_key"] = key
        _CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
        logger.info("OpenTopography API key saved to config.json")
        return JSONResponse(content={"ok": True})
    except Exception as exc:
        logger.error("Failed to save OpenTopography key: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})
