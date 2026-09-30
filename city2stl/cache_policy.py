"""Predicates for OSM cache staleness and enrichment checks."""

from __future__ import annotations

from typing import Any

# 3: building parts and their outlines are no longer dissolved together.
# 4: buildings carry osm_id, name, building, amenity, historic, tourism (landmarks).
CITY_PIPELINE_VERSION = 4
#: Payload versions whose only defect is the buildings layer: re-fetching that
#: layer brings them up to date, the other cached layers are still good.
BUILDINGS_ONLY_STALE_VERSIONS = frozenset({3})


def building_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    buildings = payload.get("buildings") or {}
    features = buildings.get("features") or []
    return features if isinstance(features, list) else []


def city_cache_missing_height_source(payload: dict[str, Any]) -> bool:
    """Detect older cached city payloads created before height_source existed."""
    features = building_features(payload)
    if not features:
        return False
    return any("height_source" not in (feat.get("properties") or {}) for feat in features)


def city_cache_missing_building_parts(payload: dict[str, Any]) -> bool:
    """Detect cached payloads written before building-part reconstruction was enabled."""
    version = int(payload.get("city_pipeline_version") or 0)
    if version < CITY_PIPELINE_VERSION:
        return True
    # Versioned payloads are authoritative.
    return False


def city_cache_stale_buildings_only(payload: dict[str, Any]) -> bool:
    """True for a payload that is current except for its buildings layer's columns."""
    return int(payload.get("city_pipeline_version") or 0) in BUILDINGS_ONLY_STALE_VERSIONS
