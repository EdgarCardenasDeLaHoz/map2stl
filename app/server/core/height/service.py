from __future__ import annotations

import asyncio
import base64
import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

import numpy as np

from geo2stl.projections import project_grid as _project_grid

logger = logging.getLogger(__name__)

try:
    from app.server.core.cache import (
        CACHE_ROOT as _CACHE_ROOT,
        make_cache_key,
        read_array_cache,
        write_array_cache,
    )
    _CACHE_AVAILABLE = _CACHE_ROOT is not None
except Exception:
    _CACHE_ROOT = None
    _CACHE_AVAILABLE = False
    make_cache_key = read_array_cache = write_array_cache = None  # type: ignore

from city2stl.skyline_cv.height import HeightResult, _filter_outliers, merge_height_rasters, provider_stats
from city2stl.skyline_cv.height.providers.copernicus import CopernicusProvider
from city2stl.skyline_cv.height.providers.ghsl import GHSLProvider
from city2stl.skyline_cv.height.providers.lidar_3dep import LiDAR3DEPProvider
from city2stl.skyline_cv.height.providers.ndsm import NDSMProvider
from city2stl.skyline_cv.height.providers.open_buildings import OpenBuildingsProvider
from city2stl.skyline_cv.height.providers.roofnet import RoofNetProvider
from city2stl.skyline_cv.height.providers.wsf3d import WSF3DProvider
from city2stl.heights import enhance_buildings_with_raster


_HEIGHT_CACHE_VERSION = "v2"  # bumped: cache format changed to shared npz+json


@dataclass
class RegisteredProvider:
    """Single source of truth for a height provider and its metadata."""
    instance: Any
    confidence: float
    resolution_m: float

    @property
    def name(self) -> str:
        return self.instance.name


_REGISTRY: list[RegisteredProvider] = [
    RegisteredProvider(LiDAR3DEPProvider(),    confidence=0.95, resolution_m=1.0),
    RegisteredProvider(NDSMProvider(),          confidence=0.80, resolution_m=30.0),
    RegisteredProvider(CopernicusProvider(),    confidence=0.70, resolution_m=10.0),
    RegisteredProvider(RoofNetProvider(),       confidence=0.65, resolution_m=5.0),
    RegisteredProvider(OpenBuildingsProvider(), confidence=0.60, resolution_m=5.0),
    RegisteredProvider(WSF3DProvider(),         confidence=0.50, resolution_m=90.0),
    RegisteredProvider(GHSLProvider(),          confidence=0.40, resolution_m=100.0),
]

_ALL_PROVIDERS = [r.instance for r in _REGISTRY]
_PROVIDER_MAP: dict[str, RegisteredProvider] = {r.name: r for r in _REGISTRY}


def provider_infos(bbox):
    return [
        {
            "name": r.name,
            "covers": r.instance.covers(bbox),
            "confidence": r.confidence,
            "resolution_m": r.resolution_m,
        }
        for r in _REGISTRY
    ]


def _select_providers(bbox, requested: Optional[list[str]] = None):
    if requested:
        providers = [_PROVIDER_MAP[name].instance for name in requested if name in _PROVIDER_MAP]
        unknown = [name for name in requested if name not in _PROVIDER_MAP]
    else:
        providers = [r.instance for r in _REGISTRY if r.instance.covers(bbox)]
        unknown = []
    return providers, unknown


def _provider_cache_key(name: str, bbox: tuple, dim: tuple) -> str:
    north, south, east, west = bbox
    height, width = dim
    return make_cache_key(
        f"height_{name}", north, south, east, west,
        {"h": height, "w": width, "v": _HEIGHT_CACHE_VERSION},
    )


def _read_provider_cache(name: str, bbox: tuple, dim: tuple) -> HeightResult | None:
    if not _CACHE_AVAILABLE:
        return None
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
    if not _CACHE_AVAILABLE:
        return
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


def enhance_city_data(payload: Dict[str, Any], north: float, south: float, east: float, west: float,
                      dim: int = 512) -> Dict[str, Any]:
    features = ((payload.get("buildings") or {}).get("features") or [])
    if not features:
        return payload

    center_lat = (north + south) * 0.5
    center_lon = (east + west) * 0.5
    in_conus = 24.0 <= center_lat <= 50.0 and -125.0 <= center_lon <= -66.0
    in_alaska = 51.0 <= center_lat <= 72.0 and -170.0 <= center_lon <= -129.0
    in_hawaii = 18.0 <= center_lat <= 23.5 and -161.0 <= center_lon <= -154.0
    if in_conus or in_alaska or in_hawaii:
        payload["height_enhancement"] = {
            "source_name": "osm_only_us",
            "providers_used": [],
            "resolution_m": 0.0,
            "stats": {"skipped": True, "reason": "US bbox uses OSM heights only"},
        }
        return payload

    default_count = sum(
        1 for feat in features
        if (feat.get("properties") or {}).get("height_source") == "default"
    )
    if default_count == 0:
        return payload

    bbox = (north, south, east, west)
    providers = [
        provider for provider in (
            NDSMProvider(),
            CopernicusProvider(),
            OpenBuildingsProvider(),
            WSF3DProvider(),
            GHSLProvider(),
        )
        if provider.covers(bbox)
    ]

    results = []
    for provider in providers:
        try:
            result = provider.fetch_heights(bbox, (dim, dim))
        except Exception as exc:
            logger.warning("City height enhancement provider '%s' failed: %s", provider.name, exc)
            continue
        if result.raster.size == 0:
            continue
        valid_pixels = int(np.count_nonzero(~np.isnan(result.raster)))
        if valid_pixels <= 0:
            continue
        results.append(result)

    if not results:
        return payload

    merged = merge_height_rasters(results, target_shape=(dim, dim))
    enhanced = enhance_buildings_with_raster(
        payload["buildings"],
        merged.raster,
        bbox,
        confidence_raster=merged.confidence,
        source_name=merged.source_name,
    )
    payload["buildings"] = enhanced["buildings"]
    payload["height_enhancement"] = {
        "source_name": merged.source_name,
        "providers_used": [item.source_name for item in results],
        "resolution_m": float(merged.resolution_m),
        "stats": enhanced.get("stats") or {},
    }
    return payload