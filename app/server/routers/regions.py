"""
routers/regions.py — /api/regions/* CRUD endpoints.

Extracted from location_picker.py (backend refactor, step 6).
Step 12: reads/writes SQLite via core/db.py.
"""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.server.core.db import get_db, init_db
from app.server.core.validation import model_to_dict

logger = logging.getLogger(__name__)
router = APIRouter(tags=["regions"])


# ---------------------------------------------------------------------------
# Schema imports
# ---------------------------------------------------------------------------
from app.server.schemas import RegionCreate, RegionParameters  # noqa: E402

# ---------------------------------------------------------------------------
# SQLite helpers
# ---------------------------------------------------------------------------

_PARAM_FIELDS = ("dim", "depth_scale", "water_scale",
                 "height", "base", "subtract_water", "sat_scale")
# Every regions column except the `name` key (copied as-is by a rename).
_REGION_COLUMNS = ("label", "description", "north", "south", "east", "west", *_PARAM_FIELDS)


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


def _bbox_iou(a: dict, b: dict) -> float:
    """Intersection-over-union of two {north,south,east,west} bboxes.

    Plain planar IoU on lat/lon degrees — fine for the small, roughly
    similar-latitude bboxes this is used to compare (candidate cities),
    not a precise geodesic area calculation.
    """
    ix0, iy0 = max(a["west"], b["west"]), max(a["south"], b["south"])
    ix1, iy1 = min(a["east"], b["east"]), min(a["north"], b["north"])
    iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
    inter = iw * ih
    area_a = max(0.0, a["east"] - a["west"]) * max(0.0, a["north"] - a["south"])
    area_b = max(0.0, b["east"] - b["west"]) * max(0.0, b["north"] - b["south"])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def find_overlapping_region(bbox: dict, min_iou: float = 0.5) -> dict | None:
    """Return the saved region with the highest bbox IoU against `bbox`, if
    it clears `min_iou`; otherwise None. Used by F-MESHIMPORT auto-register
    to reuse an existing region rather than creating a near-duplicate.
    """
    _ensure_db()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT name, label, description, north, south, east, west FROM regions"
        ).fetchall()
    best_name, best_iou = None, 0.0
    for row in rows:
        r = dict(row)
        iou = _bbox_iou(bbox, r)
        if iou > best_iou:
            best_name, best_iou = r["name"], iou
    if best_name is not None and best_iou >= min_iou:
        return {"name": best_name, "iou": best_iou}
    return None


def _unique_region_name(base_name: str) -> str:
    """Return base_name, or base_name suffixed with " (2)", " (3)", ... if
    base_name already exists (regions.name is a primary key; POST /api/regions
    silently overwrites on collision, which auto-register should never do to
    an existing, possibly-customised region)."""
    _ensure_db()
    with get_db() as conn:
        existing = {r["name"] for r in conn.execute("SELECT name FROM regions").fetchall()}
    if base_name not in existing:
        return base_name
    i = 2
    while f"{base_name} ({i})" in existing:
        i += 1
    return f"{base_name} ({i})"


def find_or_create_region_for_bbox(
    bbox: dict, label_hint: str, min_iou: float = 0.5,
) -> dict:
    """Reuse a saved region whose bbox overlaps `bbox` by >= min_iou, else
    create a new one named after `label_hint` (de-duplicated if needed).

    Returns {"name": str, "created": bool, "iou": float|None}.
    """
    match = find_overlapping_region(bbox, min_iou=min_iou)
    if match is not None:
        return {"name": match["name"], "created": False, "iou": match["iou"]}

    name = _unique_region_name(label_hint.strip() or "Untitled region")
    _ensure_db()
    with get_db() as conn:
        conn.execute(
            "INSERT INTO regions (name, label, description, north, south, east, west) "
            "VALUES (?,?,?,?,?,?,?)",
            (name, label_hint, "Created by F-MESHIMPORT auto-register",
             bbox["north"], bbox["south"], bbox["east"], bbox["west"]),
        )
        conn.commit()
    logger.info(f"auto-register: created new region {name!r} for bbox {bbox}")
    return {"name": name, "created": True, "iou": None}


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
            # An upsert, not INSERT OR REPLACE: REPLACE deletes the old row
            # first, and with foreign keys on that cascade-deleted the
            # region's saved settings every time a region was re-saved.
            conn.execute(
                "INSERT INTO regions "
                "(name, label, description, north, south, east, west, "
                " dim, depth_scale, water_scale, height, base, subtract_water, sat_scale) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET "
                "label=excluded.label, description=excluded.description, "
                "north=excluded.north, south=excluded.south, "
                "east=excluded.east, west=excluded.west, "
                "dim=excluded.dim, depth_scale=excluded.depth_scale, "
                "water_scale=excluded.water_scale, height=excluded.height, "
                "base=excluded.base, subtract_water=excluded.subtract_water, "
                "sat_scale=excluded.sat_scale",
                (
                    region.name, region.label, region.description,
                    region.north, region.south, region.east, region.west,
                    params.dim, params.depth_scale, params.water_scale,
                    params.height, params.base,
                    int(params.subtract_water), params.sat_scale,
                ),
            )
            conn.commit()
        payload = model_to_dict(region)
        if payload.get("parameters") is None:
            payload["parameters"] = model_to_dict(RegionParameters())
        return JSONResponse(content=payload, status_code=201)
    except sqlite3.IntegrityError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    except Exception as e:
        logger.error(f"Error creating region: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


def _rename_region_rows(conn: sqlite3.Connection, old: str, new: str) -> None:
    """Rename region ``old`` to ``new`` together with its settings and landmark rows.

    The child tables reference ``regions(name)`` with no ON UPDATE action, so an
    in-place ``UPDATE regions SET name`` fails the foreign-key check while children
    exist. Instead: copy the row under the new name, repoint the children, then
    delete the old row (its ON DELETE CASCADE then has nothing left to remove).
    Runs inside the caller's transaction.
    """
    cols = ", ".join(_REGION_COLUMNS)
    conn.execute(
        f"INSERT INTO regions (name, {cols}) SELECT ?, {cols} FROM regions WHERE name=?",
        (new, old))
    conn.execute("UPDATE region_settings SET region_name=? WHERE region_name=?", (new, old))
    conn.execute("UPDATE region_landmarks SET region_name=? WHERE region_name=?", (new, old))
    conn.execute("DELETE FROM regions WHERE name=?", (old,))


@router.put("/api/regions/{name}")
async def update_region(name: str, region: RegionCreate):
    """Update an existing saved region by name.

    A body ``name`` different from the path renames the region; its saved
    settings and landmark overrides move with it. 404 if ``name`` is unknown,
    409 if the new name is already taken.
    """
    try:
        _ensure_db()
        params = region.parameters
        new_name = region.name.strip()
        if not new_name:
            return JSONResponse(content={"error": "Region name must not be blank"}, status_code=400)
        with get_db() as conn:
            if not conn.execute("SELECT 1 FROM regions WHERE name=?", (name,)).fetchone():
                return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
            if new_name != name:
                if conn.execute("SELECT 1 FROM regions WHERE name=?", (new_name,)).fetchone():
                    return JSONResponse(
                        content={"error": f"A region named '{new_name}' already exists"},
                        status_code=409)
                _rename_region_rows(conn, name, new_name)
                name = new_name
            if params is None:
                # A rename or bbox edit sends no parameters. Writing the
                # RegionParameters defaults here reset the region's stored
                # values on every such edit; leave them alone instead.
                cur = conn.execute(
                    "UPDATE regions SET "
                    "label=?, description=?, north=?, south=?, east=?, west=? "
                    "WHERE name=?",
                    (region.label, region.description,
                     region.north, region.south, region.east, region.west,
                     name),
                )
            else:
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
            if cur.rowcount == 0:
                return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
            conn.commit()
        payload = model_to_dict(region)
        payload["name"] = name
        return JSONResponse(content=payload)
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


def _rename_legacy_clip_nans(settings: dict) -> dict:
    """Rename the retired ``clip_nans`` key to ``clip_valid_region`` in saved settings.

    The only place the old spelling is still read. Every route dropped the ``clip_nans``
    alias on 2026-09-30; region settings saved before then (by the SDK in its
    ``projection`` group, or flat) may still carry it, so it is renamed as they load.
    An explicit ``clip_valid_region`` wins.
    """
    groups = [settings, *(v for v in settings.values() if isinstance(v, dict))]
    for group in groups:
        if "clip_nans" in group:
            legacy = group.pop("clip_nans")
            group.setdefault("clip_valid_region", legacy)
    return settings


@router.get("/api/regions/{name}/settings")
async def get_region_settings(name: str):
    """Fetch saved panel settings for a region. Returns empty settings if none saved yet."""
    try:
        _ensure_db()
        with get_db() as conn:
            row = conn.execute(
                "SELECT settings_json FROM region_settings WHERE region_name=?", (
                    name,)
            ).fetchone()
        if row is None:
            return JSONResponse(content={"name": name, "settings": {}})
        settings = _rename_legacy_clip_nans(json.loads(row["settings_json"] or "{}"))
        return JSONResponse(content={"name": name, "settings": settings})
    except Exception as e:
        logger.error(f"Error fetching region settings: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.put("/api/regions/{name}/settings")
async def save_region_settings_route(name: str, request: Request):
    """Save or update all panel settings for a region.

    Accepts a free-form JSON body — either the grouped structure
    (dem, projection, view, water, satellite, city, export, split, hydrology)
    or the legacy flat structure.  The blob is stored verbatim.
    """
    try:
        _ensure_db()
        payload = await request.json()
        if not isinstance(payload, dict):
            return JSONResponse(content={"error": "Body must be a JSON object"}, status_code=400)
        settings_json = json.dumps(payload)
        with get_db() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO region_settings (region_name, settings_json) VALUES (?,?)",
                (name, settings_json),
            )
            conn.commit()
        logger.info(
            f"Settings saved for region '{name}' ({len(payload)} top-level keys)")
        return JSONResponse(content={"status": "saved", "name": name, "settings": payload})
    except Exception as e:
        logger.error(f"Error saving region settings: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


# ---------------------------------------------------------------------------
# Landmark overrides (F-LANDMARK §3), one row per (region, OSM id)
# ---------------------------------------------------------------------------

@router.get("/api/regions/{name}/landmarks")
async def get_region_landmarks(name: str):
    """``{"name", "overrides": {osm_id: spec}}`` stored for the region."""
    from app.server.core.landmarks import load_region_overrides
    try:
        return JSONResponse(content={"name": name, "overrides": load_region_overrides(name)})
    except sqlite3.Error as e:
        logger.error(f"Error reading landmark overrides: {e}")
        return JSONResponse(content={"error": str(e)}, status_code=500)


@router.put("/api/regions/{name}/landmarks/{osm_id:path}")
async def save_region_landmark(name: str, osm_id: str, request: Request):
    """Store one override: body ``{"kind": "osm"|"mesh"|"ndsm", ...}`` (city2stl.landmarks)."""
    from app.server.core.landmarks import save_region_override
    from city2stl.landmarks import LandmarkError
    try:
        spec = await request.json()
        spec = save_region_override(name, osm_id, spec)
    except LandmarkError as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    except KeyError:
        return JSONResponse(content={"error": f"Region '{name}' not found"}, status_code=404)
    except (ValueError, sqlite3.Error) as e:
        return JSONResponse(content={"error": str(e)}, status_code=400)
    return JSONResponse(content={"status": "saved", "name": name, "osm_id": osm_id, "override": spec})


@router.delete("/api/regions/{name}/landmarks/{osm_id:path}")
async def delete_region_landmark(name: str, osm_id: str):
    """Remove one stored override (the building goes back to its OSM tags)."""
    from app.server.core.landmarks import delete_region_override
    if not delete_region_override(name, osm_id):
        return JSONResponse(content={"error": f"No override for {osm_id} in '{name}'"}, status_code=404)
    return JSONResponse(content={"status": "deleted", "name": name, "osm_id": osm_id})
