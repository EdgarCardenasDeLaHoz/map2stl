"""
routers/geocode.py — /api/geocode/* endpoints (F-UX batch 2: landmark search).

- ``GET /api/geocode?q=`` — place search (Nominatim, via ``geo2stl.geocode``), for
  the search box on the Explore map.
- ``GET /api/geocode/edge-landmarks`` — named notable OSM features close to the
  region box edge, for the "Alhambra is 120 m outside the east edge" warning
  (one Overpass query, via ``geo2stl.landmarks``).

Both results are cached on disk by the library; the network calls are blocking
and run in the executor.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse

from app.server.core.responses import error_response
from app.server.core.validation import run_sync, validate_bbox, validate_bbox_diagonal
from geo2stl import geocode as _geocode
from geo2stl import landmarks as _landmarks

logger = logging.getLogger(__name__)
router = APIRouter(tags=["geocode"])

#: Edge-landmark checks are skipped above this box diagonal: the edge band of a
#: country-sized box is thousands of km of Overpass query for a warning that is
#: about a city print.
EDGE_LANDMARKS_MAX_DIAG_KM = 60.0


@router.get("/api/geocode")
async def geocode_search(
    q: str = Query(..., min_length=1, max_length=200, description="Place or landmark name"),
    limit: int = Query(5, ge=1, le=20, description="Maximum results"),
):
    """Search for a place by name. Returns ``{query, results: [{name, display_name,
    lat, lon, bbox: {north, south, east, west} | null, class, type, osm_type, osm_id}]}``."""
    try:
        results = await run_sync(_geocode.search_places, q, limit)
    except Exception as e:
        logger.warning("Geocode search failed for %r: %s", q, e)
        return error_response(f"Place search failed: {e}", 502)
    return JSONResponse(content={"query": q, "results": results})


@router.get("/api/geocode/edge-landmarks")
async def edge_landmarks(
    north: float, south: float, east: float, west: float,
    warn_m: float = Query(_landmarks.DEFAULT_WARN_M, gt=0, le=2000,
                          description="Report features within this distance of the edge"),
    band_m: float = Query(_landmarks.DEFAULT_BAND_M, gt=0, le=4000,
                          description="Width of the queried band, centred on the edge"),
):
    """Named notable features within ``warn_m`` of the box edge, inside or outside.

    Returns ``{landmarks: [{name, class, type, lat, lon, position: inside|outside|crosses,
    edge, distance_m, message}], warn_m, band_m}``, nearest first; ``skipped`` is set
    (and the list empty) when the box is too large to check.
    """
    err = validate_bbox(north, south, east, west)
    if err:
        return err
    bbox = {"north": north, "south": south, "east": east, "west": west}
    body = {"landmarks": [], "warn_m": warn_m, "band_m": max(band_m, 2 * warn_m)}
    _, too_big = validate_bbox_diagonal(north, south, east, west,
                                        max_km=EDGE_LANDMARKS_MAX_DIAG_KM)
    if too_big:
        body["skipped"] = f"region larger than {EDGE_LANDMARKS_MAX_DIAG_KM:.0f} km diagonal"
        return JSONResponse(content=body)
    try:
        body["landmarks"] = await run_sync(
            _landmarks.edge_landmarks, bbox, warn_m, body["band_m"])
    except Exception as e:
        logger.warning("Edge landmark query failed: %s", e)
        return error_response(f"Landmark query failed: {e}", 502)
    return JSONResponse(content=body)
