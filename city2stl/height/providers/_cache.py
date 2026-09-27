"""Shared height-provider cache plumbing.

Every height provider persists a single ``HeightResult`` (raster + confidence +
``resolution_m``) per ``(bbox, dim)`` key in the shared array cache
(``geo2stl.cache``), and registers a namespace TTL at import time. That
contract was copy-pasted into each provider; this module centralises it so the
providers only express the parts that actually differ (namespace, default
resolution, the fetch body).

Usage::

    from ._cache import register_ttl, make_cache_key, read_height_result, write_height_result

    register_ttl("ndsm", 90)
    ...
    key = make_cache_key("ndsm", north, south, east, west, {"dim": list(dim)})
    hit = read_height_result("ndsm", key, self.name, default_resolution_m=30.0)
    if hit is not None:
        return hit
    ...
    write_height_result("ndsm", key, result)
"""

from __future__ import annotations

from city2stl.height import HeightResult
from geo2stl.cache import (  # noqa: F401 – make_cache_key re-exported for callers
    NAMESPACE_TTL,
    make_cache_key,
    read_array_cache,
    write_array_cache,
)

__all__ = [
    "register_ttl",
    "make_cache_key",
    "read_height_result",
    "write_height_result",
]


def register_ttl(namespace: str, days: int) -> None:
    """Register a cache TTL (in days) for *namespace* without clobbering an
    existing value."""
    NAMESPACE_TTL.setdefault(namespace, days * 86400)


def read_height_result(
    namespace: str,
    key: str,
    source_name: str,
    default_resolution_m: float,
) -> HeightResult | None:
    """Return a cached ``HeightResult`` for *key*, or ``None`` on a miss.

    Reconstructs the standard ``{raster, confidence}`` arrays written by
    :func:`write_height_result`.

    ``default_resolution_m`` is authoritative and the cached sidecar's
    ``resolution_m`` is ignored. Resolution is a property of the source
    product, not of the bytes on disk, and every caller passes its module's own
    constant. Sidecars written before 2026-08-30 hold the *output grid spacing*
    for the nDSM and 3DEP providers -- around 5.9 m for a 3 km box at 512 cells
    -- which claims a detail level those 30 m products do not have. Trusting
    that number would keep the old merge ordering alive on every cache hit.
    """
    cached = read_array_cache(namespace, key)
    if cached is None:
        return None
    arrays, _meta = cached
    return HeightResult(
        raster=arrays["raster"],
        confidence=arrays["confidence"],
        source_name=source_name,
        resolution_m=default_resolution_m,
    )


def write_height_result(namespace: str, key: str, result: HeightResult) -> None:
    """Persist *result* under *key* using the standard array/metadata layout."""
    write_array_cache(
        namespace,
        key,
        {"raster": result.raster, "confidence": result.confidence},
        {"resolution_m": result.resolution_m},
    )
