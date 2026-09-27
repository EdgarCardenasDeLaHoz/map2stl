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
    city_cache_stale_buildings_only,
)
from city2stl.fetch import FetchCancelled, fetch_osm_data
from geo2stl.geo import bbox_diagonal_km

logger = logging.getLogger(__name__)

#: Largest bbox diagonal (km) served with city layers unless the caller overrides
#: (``allow_large``). Beyond it an OSM fetch takes minutes and most features print
#: below nozzle width; large regions are terrain + rivers (F-REGION, Region preset).
CITY_LAYERS_MAX_DIAGONAL_KM = 25.0


class CityAreaTooLarge(ValueError):
    """City layers were requested for a bbox beyond CITY_LAYERS_MAX_DIAGONAL_KM."""


def check_city_area(north: float, south: float, east: float, west: float,
                    layers: list[str], allow_large: bool = False) -> None:
    """Raise :class:`CityAreaTooLarge` for city layers on a box > 25 km diagonal."""
    if not layers or allow_large:
        return
    diag = bbox_diagonal_km({"north": north, "south": south, "east": east, "west": west})
    if diag > CITY_LAYERS_MAX_DIAGONAL_KM:
        raise CityAreaTooLarge(
            f"City layers are limited to regions of {CITY_LAYERS_MAX_DIAGONAL_KM:.0f} km "
            f"diagonal; this one is {diag:.1f} km. Turn the city layers off (the Region "
            f"preset builds terrain with rivers and lakes), choose a smaller box, or set "
            f"allow_large_city to fetch anyway.")


def get_city_layers(north: float, south: float, east: float, west: float,
                    layers: list[str], simplify_tolerance: float = 0.5,
                    min_area: float = 5.0, *, progress=None, on_mirror=None,
                    should_cancel=None, allow_large: bool = False) -> dict:
    """Return {layer: FeatureCollection, ...} for ``layers`` (plus whatever else is cached).

    Optional hooks, for the background fetch (``core/city_fetch_tasks.py``):
    ``progress(layer, state)`` gets "cached" for layers served from disk, then
    "fetching" / "done" / "failed" from ``city2stl.fetch.fetch_osm_data``, and
    ``("heights", "fetching" | "done")`` around building-height enhancement;
    ``on_mirror(endpoint)`` names the Overpass mirror of each pass;
    ``should_cancel()`` is checked before each layer and before the height step
    (raises ``city2stl.fetch.FetchCancelled``). A cancelled fetch writes no cache.

    Raises :class:`CityAreaTooLarge` when layers are asked for on a bbox over
    ``CITY_LAYERS_MAX_DIAGONAL_KM`` diagonal, unless ``allow_large``.
    """
    check_city_area(north, south, east, west, layers, allow_large)
    key = osm_cache_key(north, south, east, west, simplify_tolerance, min_area)
    cached = read_osm_cache(key) or {}
    if cached and not city_cache_missing_height_source(cached) \
            and city_cache_stale_buildings_only(cached):
        # Only the buildings' columns changed since this payload was written:
        # keep the other layers and re-fetch buildings.
        logger.info("Re-fetching buildings of an older OSM cache payload: %s", key)
        cached = {k: v for k, v in cached.items() if k not in ("buildings", "city_pipeline_version")}
    elif cached and (city_cache_missing_height_source(cached)
                     or city_cache_missing_building_parts(cached)):
        logger.info("Ignoring stale OSM cache payload: %s", key)
        cached = {}

    missing = [name for name in layers if name not in cached]
    if progress:
        for name in layers:
            if name in cached:
                progress(name, "cached")
    if not missing:
        return cached

    logger.info("OSM layers %s not cached for %s; fetching", missing, key[:8])
    hooks = {k: v for k, v in (("progress", progress), ("on_mirror", on_mirror),
                               ("should_cancel", should_cancel)) if v}
    fetched = fetch_osm_data(north, south, east, west, missing, simplify_tolerance, min_area,
                             **hooks)
    if "buildings" in fetched:
        if should_cancel and should_cancel():
            raise FetchCancelled("City fetch cancelled")
        if progress:
            progress("heights", "fetching")
        try:
            fetched = enhance_city_data(fetched, north, south, east, west)
        except Exception as exc:
            logger.warning("City height auto-enhancement skipped: %s", exc)
        if progress:
            progress("heights", "done")

    result = {**cached, **fetched, "cache_key": key}
    result.setdefault("city_pipeline_version", CITY_PIPELINE_VERSION)
    if not any("error" in v for v in fetched.values() if isinstance(v, dict)):
        write_osm_cache(key, result)
    return result
