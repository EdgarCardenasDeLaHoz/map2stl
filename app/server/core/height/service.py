"""
core/height/service.py — Async height endpoints over the ``city2stl.height`` registry.

The registry, provider selection and ``enhance_city_data`` live in
``city2stl.height.service``; this module adds what needs the event loop or the
server config: ``fetch_height_payload`` / ``fetch_height_diagnostics`` (fetch
providers in the executor, cache each result, merge, project, encode) and the
configured OpenTopography key.
"""

from __future__ import annotations

import asyncio
import base64
import logging

import numpy as np

from app.server import config as _config
from app.server.core.cache import (
    make_cache_key,
    read_array_cache,
    write_array_cache,
)
from city2stl.height import (
    HeightResult,
    _filter_outliers,
    merge_height_rasters,
    provider_stats,
)
from city2stl.height.service import (  # noqa: F401 – re-exported for app callers
    _PROVIDER_MAP,
    _REGISTRY,
    RegisteredProvider,
    _select_providers,
    enhance_city_data,
    provider_infos,
    set_opentopo_api_key,
)
from geo2stl.projections import project_grid as _project_grid

logger = logging.getLogger(__name__)

_HEIGHT_CACHE_VERSION = "v2"  # bumped: cache format changed to shared npz+json

set_opentopo_api_key(_config.OPENTOPO_API_KEY)


def _provider_cache_key(name: str, bbox: tuple, dim: tuple) -> str:
    north, south, east, west = bbox
    height, width = dim
    return make_cache_key(
        f"height_{name}", north, south, east, west,
        {"h": height, "w": width, "v": _HEIGHT_CACHE_VERSION},
    )


def _read_provider_cache(name: str, bbox: tuple, dim: tuple) -> HeightResult | None:
    try:
        key = _provider_cache_key(name, bbox, dim)
        result = read_array_cache(f"height_{name}", key)
        if result is None:
            return None
        arrays, meta = result
        return HeightResult(
            raster=arrays["raster"],
            confidence=arrays["confidence"],
            source_name=str(meta["source_name"]),
            resolution_m=float(meta["resolution_m"]),
        )
    except Exception as exc:
        logger.debug("height cache read failed for %s: %s", name, exc)
        return None


def _write_provider_cache(name: str, bbox: tuple, dim: tuple, hr: HeightResult) -> None:
    try:
        key = _provider_cache_key(name, bbox, dim)
        write_array_cache(
            f"height_{name}", key,
            {"raster": hr.raster.astype(np.float32),
             "confidence": hr.confidence.astype(np.float32)},
            {"source_name": hr.source_name, "resolution_m": hr.resolution_m},
        )
    except Exception as exc:
        logger.debug("height cache write failed for %s: %s", name, exc)


async def fetch_height_payload(north, south, east, west, width, height,
                               providers=None, projection="none", clip_nans=True):
    bbox = (north, south, east, west)
    dim = (height, width)
    selected, unknown = _select_providers(bbox, providers)
    if unknown:
        logger.warning("Unknown providers ignored: %s", unknown)
    if not selected:
        return None, "No height providers available for this bbox", 404

    loop = asyncio.get_running_loop()
    errors: list[str] = []

    async def _fetch_one(provider) -> HeightResult | None:
        cached = _read_provider_cache(provider.name, bbox, dim)
        if cached is not None:
            logger.info("Height provider '%s': cache hit", provider.name)
            return cached
        try:
            hr = await loop.run_in_executor(None, provider.fetch_heights, bbox, dim)
            logger.info(
                "Height provider '%s': fetched %d valid pixels",
                provider.name,
                int(np.count_nonzero(~np.isnan(hr.raster))),
            )
            _write_provider_cache(provider.name, bbox, dim, hr)
            return hr
        except Exception as exc:
            logger.warning("Height provider '%s' failed: %s", provider.name, exc)
            errors.append(f"{provider.name}: {str(exc)}")
            return None

    gathered = await asyncio.gather(*[_fetch_one(provider) for provider in selected])
    results = [result for result in gathered if result is not None]
    if not results:
        return None, f"All providers failed: {'; '.join(errors)}", 502

    merged = merge_height_rasters(results, target_shape=dim)
    raster = merged.raster
    if projection != "none":
        raster = _project_grid(
            raster, north, south, east, west, projection, clip_nans, categorical=False
        )

    valid = ~np.isnan(raster)
    total = raster.size
    n_valid = int(np.count_nonzero(valid))
    out_h, out_w = raster.shape
    coverage = n_valid / total * 100 if total > 0 else 0
    vmin = float(np.nanmin(raster)) if n_valid > 0 else None
    vmax = float(np.nanmax(raster)) if n_valid > 0 else None
    provider_details = [
        {
            "name": result.source_name,
            "resolution_m": result.resolution_m,
            "confidence": _PROVIDER_MAP[result.source_name].confidence
            if result.source_name in _PROVIDER_MAP else 0.5,
        }
        for result in results
    ]
    payload = {
        "width": out_w,
        "height": out_h,
        "source_name": merged.source_name,
        "resolution_m": merged.resolution_m,
        "coverage_pct": round(coverage, 1),
        "stats": {
            "providers_used": [result.source_name for result in results],
            "providers_failed": errors,
            "providers": provider_details,
            "min_m": vmin,
            "max_m": vmax,
            "mean_m": float(np.nanmean(raster)) if n_valid > 0 else None,
            "valid_pixels": n_valid,
            "total_pixels": total,
        },
        "raster_b64": base64.b64encode(raster.tobytes()).decode("ascii"),
        "dtype": "float32",
        "units": "metres",
        "projection": projection,
        "bbox": {"north": north, "south": south, "east": east, "west": west},
        "vmin": vmin,
        "vmax": vmax,
    }
    return payload, None, None


async def fetch_height_diagnostics(north, south, east, west, width, height, providers=None):
    bbox = (north, south, east, west)
    dim = (height, width)
    selected, unknown = _select_providers(bbox, providers)
    if unknown:
        logger.warning("Unknown providers ignored: %s", unknown)
    if not selected:
        return {"providers": [], "errors": ["No height providers available for this bbox"]}

    loop = asyncio.get_running_loop()
    errors: list[str] = []

    async def _fetch_one(provider):
        cached = _read_provider_cache(provider.name, bbox, dim)
        try:
            if cached is not None:
                hr = cached
                logger.info("Diagnostics: cache hit for '%s'", provider.name)
            else:
                hr = await loop.run_in_executor(None, provider.fetch_heights, bbox, dim)
                _write_provider_cache(provider.name, bbox, dim, hr)

            raw_valid = int(np.count_nonzero(~np.isnan(hr.raster)))
            filtered = _filter_outliers(hr.raster)
            filtered_valid = int(np.count_nonzero(~np.isnan(filtered)))
            outliers_removed = raw_valid - filtered_valid
            stats = provider_stats(hr.__class__(
                raster=filtered,
                confidence=hr.confidence,
                source_name=hr.source_name,
                resolution_m=hr.resolution_m,
            ))
            return {
                "source": hr.source_name,
                "coverage_pct": stats["coverage_pct"],
                "valid_pixels": stats["valid_pixels"],
                "total_pixels": stats["total_pixels"],
                "min_m": stats["min_m"],
                "max_m": stats["max_m"],
                "mean_m": stats["mean_m"],
                "p95_m": stats["p95_m"],
                "resolution_m": hr.resolution_m,
                "confidence": _PROVIDER_MAP[provider.name].confidence
                if provider.name in _PROVIDER_MAP else 0.5,
                "outliers_removed": outliers_removed,
            }
        except Exception as exc:
            logger.warning("Height diagnostics provider '%s' failed: %s", provider.name, exc)
            errors.append(f"{provider.name}: {str(exc)}")
            return None

    gathered = await asyncio.gather(*[_fetch_one(provider) for provider in selected])
    return {"providers": [item for item in gathered if item is not None], "errors": errors}
