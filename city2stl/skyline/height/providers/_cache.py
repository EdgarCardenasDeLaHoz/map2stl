"""Shared height-provider cache plumbing.

Every height provider persists a single ``HeightResult`` (raster + confidence +
``resolution_m``) per ``(bbox, dim)`` key in the app-server array cache, and
registers a namespace TTL at import time. That contract was copy-pasted into
each provider; this module centralises it so the providers only express the
parts that actually differ (namespace, default resolution, the fetch body).

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

from app.server.core.cache import (  # noqa: F401 – make_cache_key re-exported for callers
    make_cache_key,
    read_array_cache,
    write_array_cache,
    NAMESPACE_TTL,
)
from city2stl.skyline.height import HeightResult

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

    Reconstructs the standard ``{raster, confidence}`` arrays + ``resolution_m``
    metadata layout written by :func:`write_height_result`.
    """
    cached = read_array_cache(namespace, key)
    if cached is None:
        return None
    arrays, meta = cached
    return HeightResult(
        raster=arrays["raster"],
        confidence=arrays["confidence"],
        source_name=source_name,
        resolution_m=meta.get("resolution_m", default_resolution_m),
    )


def write_height_result(namespace: str, key: str, result: HeightResult) -> None:
    """Persist *result* under *key* using the standard array/metadata layout."""
    write_array_cache(
        namespace,
        key,
        {"raster": result.raster, "confidence": result.confidence},
        {"resolution_m": result.resolution_m},
    )
