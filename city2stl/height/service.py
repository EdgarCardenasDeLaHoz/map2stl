"""
city2stl/height/service.py — Height-provider registry, selection and per-building merge.

  - ``_REGISTRY`` / ``_PROVIDER_MAP``   the providers and the confidence /
                                        resolution the API reports for each
  - ``provider_infos(bbox)``           registry rows plus coverage for *bbox*
  - ``_select_providers(bbox, names)``  requested providers, or all that cover *bbox*
  - ``set_opentopo_api_key(key)``      rebind the OpenTopography-backed providers
  - ``enhance_city_data(payload, ...)`` fill ``default`` building heights from
                                        3DEP lidar, then the fine raster providers

Synchronous and app-free; the app imports it directly (``app/server/core/city_data.py``,
``app/server/routers/height.py``, ``app/server/routers/auth.py``). The OpenTopography
providers start without a key and fall back to ``geo2stl.opentopo.get_api_key()``
($OPENTOPO_API_KEY / config.json); the key route rebinds them through
``set_opentopo_api_key``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np

from city2stl.height import BUILDING_RESOLUTION_LIMIT_M, merge_height_rasters
from city2stl.height.providers.copernicus import CopernicusProvider
from city2stl.height.providers.gba import GBAProvider
from city2stl.height.providers.ghsl import GHSLProvider
from city2stl.height.providers.lidar_3dep import LiDAR3DEPProvider
from city2stl.height.providers.ndsm import NDSMProvider
from city2stl.height.providers.open_buildings import OpenBuildingsProvider
from city2stl.height.providers.wsf3d import WSF3DProvider
from city2stl.heights import enhance_buildings_with_raster

logger = logging.getLogger(__name__)


@dataclass
class RegisteredProvider:
    """Single source of truth for a height provider and its metadata."""
    instance: Any
    confidence: float
    resolution_m: float

    @property
    def name(self) -> str:
        return self.instance.name


# Confidence and resolution here must match the constants inside each provider
# module, because `merge_height_rasters` ranks on what the provider reports and
# this table is what the API shows. `lidar_3dep` used to be listed at 0.95 / 1 m
# on the strength of its name; it actually computes COP30 minus SRTM and its own
# module has always said 0.82 / 30 m.
#
# Ordering this table by confidence alone no longer decides who wins a pixel:
# the merge scales confidence by `resolution_priority`, so a coarse source is
# demoted for the per-building question however trustworthy it is in general.
_REGISTRY: list[RegisteredProvider] = [
    RegisteredProvider(LiDAR3DEPProvider(),
                       confidence=0.82, resolution_m=30.0),
    RegisteredProvider(NDSMProvider(),
                       confidence=0.80, resolution_m=30.0),
    RegisteredProvider(CopernicusProvider(),    confidence=0.70, resolution_m=10.0),
    RegisteredProvider(OpenBuildingsProvider(), confidence=0.60, resolution_m=5.0),
    # GBA sits just under Overture on measurement, not on principle. Against
    # Miami's OSM-tagged heights Overture scores MAE 5.86 m / corr +0.912 and
    # GBA 25.90 m / +0.489, and above 50 m GBA loses 67 m of height. It earns
    # its place by being the only globally complete per-building source: it is
    # what fires in Cartagena, where Overture covers 9 % of footprints, and its
    # tall-building deficit is nearly unreachable there (0.31 % of pixels above
    # 50 m, against Miami's 18.06 %). See the module docstring before raising.
    RegisteredProvider(GBAProvider(),           confidence=0.55, resolution_m=3.0),
    RegisteredProvider(WSF3DProvider(),         confidence=0.50, resolution_m=90.0),
    RegisteredProvider(GHSLProvider(),          confidence=0.40, resolution_m=100.0),
]

_ALL_PROVIDERS = [r.instance for r in _REGISTRY]
_PROVIDER_MAP: dict[str, RegisteredProvider] = {r.name: r for r in _REGISTRY}


def set_opentopo_api_key(key: str | None) -> None:
    """Give the OpenTopography-backed providers a new key without a restart."""
    for r in _REGISTRY:
        if isinstance(r.instance, (LiDAR3DEPProvider, NDSMProvider)):
            r.instance.api_key = key


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


def _select_providers(bbox, requested: list[str] | None = None):
    if requested:
        providers = [_PROVIDER_MAP[name].instance for name in requested if name in _PROVIDER_MAP]
        unknown = [name for name in requested if name not in _PROVIDER_MAP]
    else:
        providers = [r.instance for r in _REGISTRY if r.instance.covers(bbox)]
        unknown = []
    return providers, unknown


def enhance_city_data(payload: dict[str, Any], north: float, south: float, east: float, west: float,
                      dim: int = 512) -> dict[str, Any]:
    """Fill in heights for buildings that have no OSM tag, from raster sources.

    Only features whose ``height_source`` is ``default`` are touched, which makes
    this idempotent: re-running it over an already-enhanced payload changes
    nothing.

    Providers coarser than ``BUILDING_RESOLUTION_LIMIT_M`` are excluded here even
    though ``merge_height_rasters`` will happily rank them, because a cell wider
    than a building measures the block, not the roof.  A city covered only by
    coarse sources therefore gets no enhancement at all and its buildings keep
    the 10 m fallback; measured over the eight plate cities that is the better
    answer.  ``payload["height_enhancement"]["providers_too_coarse"]`` records
    what was dropped so this reads as a decision rather than a silent no-op.
    """
    features = ((payload.get("buildings") or {}).get("features") or [])
    if not features:
        return payload

    if not _count_default(features):
        return payload

    bbox = (north, south, east, west)
    # Measured lidar first: it answers per footprint, so whatever it cannot
    # measure is left on "default" for the raster sources below.
    lidar = _enhance_from_lidar(features, bbox)
    if not _count_default(features):
        payload["height_enhancement"] = _with_lidar({
            "source_name": LIDAR_SOURCE_NAME, "providers_used": [],
            "providers_too_coarse": [], "resolution_m": 1.0, "stats": {},
        }, lidar)
        return payload
    # The provider list comes from `_REGISTRY` -- the same one `/api/height/*`
    # answers from -- and not from a tuple written out here. This function used
    # to name five providers explicitly, which meant every provider added after
    # it was written was invisible to the export path however prominently it
    # was registered. GlobalBuildingAtlas was the casualty: the registry entry
    # above says it "is what fires in Cartagena", and it never did, because
    # this list did not mention it.
    #
    # US bboxes go through the same path. They used to return early with
    # "osm_only_us", a guard from before the resolution limit below existed,
    # when the only sources were 30 m nDSMs that flattened houses to 3 m. It
    # also shut out the fine per-building sources, so Breckenridge, CO kept
    # 3,788 of 3,973 buildings at the 10 m fallback. GBA and Open Buildings
    # alone bring that to 456; 3DEP lidar first, then those two, to 39.
    providers, _unknown = _select_providers(bbox)

    results = []
    too_coarse = []
    for provider in providers:
        # Known-coarse sources are dropped before fetching, not after: the
        # nDSM and COP30-minus-SRTM downloads were ~2 min of a Breckenridge
        # enhancement whose result was then thrown away.
        registered = _PROVIDER_MAP.get(provider.name)
        if registered is not None and registered.resolution_m > BUILDING_RESOLUTION_LIMIT_M:
            too_coarse.append(provider.name)
            continue
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
        # A cell coarser than a building cannot answer a per-building question:
        # it averages the roof with the streets and courtyards around it. Over
        # the eight plate cities the 30 m nDSM covers 100 % of every grid and
        # reads a median of 0.0 to 2.6 m, which the `max(3.0, ...)` clamp in
        # `enhance_buildings_with_raster` then turns into a field of 3 m boxes.
        # Leaving those buildings on the 10 m fallback is measurably better.
        if result.resolution_m > BUILDING_RESOLUTION_LIMIT_M:
            too_coarse.append(result.source_name)
            continue
        results.append(result)

    if not results:
        if too_coarse:
            logger.info(
                "City height enhancement skipped: only sources coarser than %.0f m "
                "cover this bbox (%s); buildings keep the OSM fallback height",
                BUILDING_RESOLUTION_LIMIT_M, ", ".join(too_coarse),
            )
            payload["height_enhancement"] = _with_lidar({
                "source_name": "osm_only_coarse",
                "providers_used": [],
                "providers_too_coarse": too_coarse,
                "resolution_m": 0.0,
                "stats": {"skipped": True,
                          "reason": "no provider finer than "
                                    f"{BUILDING_RESOLUTION_LIMIT_M:.0f} m"},
            }, lidar)
        elif lidar:
            payload["height_enhancement"] = _with_lidar({
                "source_name": LIDAR_SOURCE_NAME, "providers_used": [],
                "providers_too_coarse": [], "resolution_m": 1.0, "stats": {},
            }, lidar)
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
    payload["height_enhancement"] = _with_lidar({
        "source_name": merged.source_name,
        "providers_used": [item.source_name for item in results],
        "providers_too_coarse": too_coarse,
        "resolution_m": float(merged.resolution_m),
        "stats": enhanced.get("stats") or {},
    }, lidar)
    return payload


LIDAR_SOURCE_NAME = "lidar_3dep_copc"


def _count_default(features: list[dict]) -> int:
    return sum(1 for feat in features
               if (feat.get("properties") or {}).get("height_source") == "default")


def _enhance_from_lidar(features: list[dict], bbox: tuple) -> dict | None:
    """Set measured 3DEP lidar heights on the ``default`` buildings of a US bbox.

    Returns the measurement stats, or None when lidar was not tried (outside the
    US, reader not installed) or failed outright; either way the raster
    providers still run for every building left on ``default``.
    """
    from city2stl.height.providers import lidar_3dep_copc

    if not (lidar_3dep_copc.covers(bbox) and lidar_3dep_copc.available()):
        return None
    from shapely.geometry import shape

    polygons = {}
    for i, feat in enumerate(features):
        if (feat.get("properties") or {}).get("height_source") != "default":
            continue
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            polygons[i] = shape(geom)
        except Exception:
            continue
    try:
        heights, stats = lidar_3dep_copc.footprint_heights(polygons, bbox)
    except Exception as exc:
        logger.warning("3DEP lidar height measurement failed: %s", exc)
        return None
    for i, h in heights.items():
        props = features[i]["properties"]
        props["height_m"] = max(3.0, h)
        props["height_source"] = LIDAR_SOURCE_NAME
    return stats if stats.get("tiles") else None


def _with_lidar(info: dict, lidar: dict | None) -> dict:
    """Record the lidar pass in a ``height_enhancement`` block."""
    if lidar:
        info["providers_used"] = [LIDAR_SOURCE_NAME, *info["providers_used"]]
        info["lidar"] = lidar
    return info
