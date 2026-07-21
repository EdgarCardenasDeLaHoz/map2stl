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


def _apply_opentopo_key(key: str) -> bool:
    """Rebind the OpenTopography key in already-imported modules.

    config.OPENTOPO_API_KEY is captured at import time, and terrain.py holds
    its own alias (_OPENTOPO_API_KEY). Update both so downloads work without a
    server restart. Returns True if at least one binding was updated.
    """
    applied = False
    try:
        from app.server import config as _config
        _config.OPENTOPO_API_KEY = key
        applied = True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not rebind config.OPENTOPO_API_KEY: %s", exc)
    try:
        from app.server.routers import terrain as _terrain
        _terrain._OPENTOPO_API_KEY = key
        applied = True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not rebind terrain._OPENTOPO_API_KEY: %s", exc)
    # geo2stl.dem holds the key actually passed to the OpenTopography download
    # call, captured at its own import time — rebind it too.
    try:
        from geo2stl import dem as _geo_dem
        _geo_dem._OPENTOPO_API_KEY = key
        applied = True
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not rebind geo2stl.dem._OPENTOPO_API_KEY: %s", exc)
    return applied


def _check_earth_engine() -> dict:
    try:
        # Route through the memoized initializer (geo2stl/sat2stl.py) so
        # polling /api/auth/status doesn't itself pay the ~3-4s EE-init tax
        # on every call when EE isn't authenticated.
        from geo2stl.sat2stl import initialize_earth_engine
        initialize_earth_engine()
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

        # Apply immediately to the running process so the user doesn't have to
        # restart the server. config.OPENTOPO_API_KEY is read at import time;
        # rebind it (and the terrain router's cached copy) here.
        applied = _apply_opentopo_key(key)
        return JSONResponse(content={"ok": True, "applied": applied})
    except Exception as exc:
        logger.error("Failed to save OpenTopography key: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


# ---------------------------------------------------------------------------
# Earth Engine OAuth flow — server-triggered, no CLI required.
#
# Google's EE OAuth ("notebook" mode) is designed for exactly this shape: the
# server has no browser of its own, so it hands the user a Google-hosted URL
# to open themselves; Google shows a one-time code; the user pastes that code
# back into the app, and the server exchanges it for a refresh token which it
# writes to disk (~/.config/earthengine/credentials). No local callback
# server or port is needed for this mode — see ee.oauth.Flow.
# ---------------------------------------------------------------------------

# Holds the in-progress Flow between /start and /complete. Single-user desktop
# app, so a module-level slot (not a session store) is enough — a new /start
# call simply replaces any abandoned prior attempt.
_ee_auth_flow = None


@router.post("/api/auth/earth-engine/start")
async def start_earth_engine_auth():
    """Begin the Earth Engine OAuth flow: return a URL for the user to open.

    The user opens `auth_url` in their own browser, signs in / approves
    access, and is shown a one-time code to paste back into
    /api/auth/earth-engine/complete.
    """
    global _ee_auth_flow
    try:
        import ee.oauth as oauth
        _ee_auth_flow = oauth.Flow(auth_mode="notebook")
        return JSONResponse(content={"auth_url": _ee_auth_flow.auth_url})
    except Exception as exc:
        logger.error("Failed to start Earth Engine auth flow: %s", exc)
        return JSONResponse(status_code=500, content={"error": str(exc)})


@router.post("/api/auth/earth-engine/complete")
async def complete_earth_engine_auth(body: dict = Body(...)):
    """Exchange the pasted authorization code for credentials and save them."""
    global _ee_auth_flow
    code = (body.get("code") or "").strip()
    if not code:
        return JSONResponse(status_code=400, content={"error": "Code must not be empty"})
    if _ee_auth_flow is None:
        return JSONResponse(
            status_code=400,
            content={"error": "No authentication in progress — click Start again."},
        )

    try:
        _ee_auth_flow.save_code(code)
        _ee_auth_flow = None

        from geo2stl.sat2stl import reset_earth_engine_status_cache
        reset_earth_engine_status_cache()

        # Confirm it actually works before telling the client "authenticated".
        status = _check_earth_engine()
        return JSONResponse(content={"ok": bool(status["authenticated"]), "error": status["error"]})
    except Exception as exc:
        logger.error("Failed to complete Earth Engine auth: %s", exc)
        return JSONResponse(status_code=400, content={"error": str(exc)})
