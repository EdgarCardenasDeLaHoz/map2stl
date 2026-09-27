"""Place search (Nominatim) for the region box editor.

``search_places(q)`` asks the public Nominatim instance for up to *limit* matches
and returns them as plain dicts (name, lat/lon, bbox, class/type). It follows the
Nominatim usage policy (https://operations.osmfoundation.org/policies/nominatim/):

- an identifying ``User-Agent`` (:data:`USER_AGENT`), never the library default;
- at most one request per second across the whole process (:func:`_wait_turn`);
- results cached on disk (``geocode`` namespace of :mod:`geo2stl.cache`), so a
  repeated search never reaches the server;
- no autocomplete: callers search on submit, not on every keystroke.
"""

from __future__ import annotations

import logging
import threading
import time

from geo2stl.cache import json_cache_key, read_json_cache, write_json_cache

logger = logging.getLogger(__name__)

NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"

#: Identifies the application to Nominatim, as its usage policy requires.
USER_AGENT = "map2stl/0.1 (+https://github.com/EdgarCardenasDeLaHoz/map2stl)"

#: The policy's absolute maximum is one request per second.
MIN_INTERVAL_S = 1.0

_REQUEST_TIMEOUT_S = 15

_lock = threading.Lock()
_last_request = [0.0]


def _wait_turn() -> None:
    """Block until a second has passed since the previous Nominatim request."""
    with _lock:
        gap = MIN_INTERVAL_S - (time.monotonic() - _last_request[0])
        if gap > 0:
            time.sleep(gap)
        _last_request[0] = time.monotonic()


def _parse_result(item: dict) -> dict | None:
    """One Nominatim ``jsonv2`` hit -> {name, display_name, lat, lon, bbox, class, type}."""
    try:
        lat = float(item["lat"])
        lon = float(item["lon"])
    except (KeyError, TypeError, ValueError):
        return None
    bbox = None
    bb = item.get("boundingbox")  # [south, north, west, east] as strings
    if isinstance(bb, (list, tuple)) and len(bb) == 4:
        try:
            s, n, w, e = (float(v) for v in bb)
            bbox = {"north": n, "south": s, "east": e, "west": w}
        except (TypeError, ValueError):
            bbox = None
    display = item.get("display_name") or ""
    return {
        "name": item.get("name") or display.split(",")[0].strip(),
        "display_name": display,
        "lat": lat,
        "lon": lon,
        "bbox": bbox,
        "class": item.get("category") or item.get("class"),
        "type": item.get("type"),
        "osm_type": item.get("osm_type"),
        "osm_id": item.get("osm_id"),
    }


def search_places(q: str, limit: int = 5, *, use_cache: bool = True) -> list[dict]:
    """Search Nominatim for *q*; returns up to *limit* parsed results (cached).

    Raises ``requests`` errors on a network or HTTP failure (nothing is cached then).
    """
    query = " ".join((q or "").split())
    if not query:
        return []
    limit = max(1, min(int(limit), 20))
    key = json_cache_key("geocode", query.lower(), limit)
    if use_cache:
        cached = read_json_cache("geocode", key)
        if cached is not None:
            return cached

    import requests

    _wait_turn()
    resp = requests.get(
        NOMINATIM_SEARCH_URL,
        params={"q": query, "format": "jsonv2", "limit": limit},
        headers={"User-Agent": USER_AGENT, "Accept-Language": "en"},
        timeout=_REQUEST_TIMEOUT_S,
    )
    resp.raise_for_status()
    results = [r for r in (_parse_result(i) for i in resp.json() or []) if r]
    write_json_cache("geocode", key, results)
    return results
