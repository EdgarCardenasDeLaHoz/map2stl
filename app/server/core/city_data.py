"""OSM city layers for a bbox: cache first, fetch only what is missing.

The cache key is the bbox plus simplification settings, not the layer list, so a
payload cached for three layers used to be served as-is to a request for nine and
the extra layers silently never arrived. Here the missing layers are fetched and
merged into the cached payload instead.
"""

from __future__ import annotations

import logging

from app.server.core.cache import osm_cache_key, read_osm_cache, write_osm_cache
from app.server.core.height.service import enhance_city_data
from city2stl.cache_policy import (
    CITY_PIPELINE_VERSION,
    city_cache_missing_building_parts,
    city_cache_missing_height_source,
)
from city2stl.fetch import fetch_osm_data

logger = logging.getLogger(__name__)


def get_city_layers(north: float, south: float, east: float, west: float,
                    layers: list[str], simplify_tolerance: float = 0.5,
                    min_area: float = 5.0) -> dict:
    """Return {layer: FeatureCollection, ...} for ``layers`` (plus whatever else is cached)."""
    key = osm_cache_key(north, south, east, west, simplify_tolerance, min_area)
    cached = read_osm_cache(key) or {}
    if cached and (city_cache_missing_height_source(cached)
                   or city_cache_missing_building_parts(cached)):
        logger.info("Ignoring stale OSM cache payload: %s", key)
        cached = {}

    missing = [name for name in layers if name not in cached]
    if not missing:
        return cached

    logger.info("OSM layers %s not cached for %s; fetching", missing, key[:8])
    fetched = fetch_osm_data(north, south, east, west, missing, simplify_tolerance, min_area)
    if "buildings" in fetched:
        try:
            fetched = enhance_city_data(fetched, north, south, east, west)
        except Exception as exc:
            logger.warning("City height auto-enhancement skipped: %s", exc)

    result = {**cached, **fetched, "cache_key": key}
    result.setdefault("city_pipeline_version", CITY_PIPELINE_VERSION)
    if not any("error" in v for v in fetched.values() if isinstance(v, dict)):
        write_osm_cache(key, result)
    return result
