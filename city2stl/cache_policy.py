"""Predicates for OSM cache staleness and enrichment checks."""

from __future__ import annotations

from typing import Any


CITY_PIPELINE_VERSION = 2


def building_features(payload: dict[str, Any]) -> list[dict[str, Any]]:
    buildings = payload.get("buildings") or {}
    features = buildings.get("features") or []
    return features if isinstance(features, list) else []


def city_cache_missing_height_source(payload: dict[str, Any]) -> bool:
    """Detect older cached city payloads created before height_source existed."""
    features = building_features(payload)
    if not features:
        return False
    return any("height_source" not in ((feat.get("properties") or {})) for feat in features)


def city_cache_missing_building_parts(payload: dict[str, Any]) -> bool:
    """Detect cached payloads written before building-part reconstruction was enabled."""
    version = int(payload.get("city_pipeline_version") or 0)
    if version < CITY_PIPELINE_VERSION:
        return True
    # Versioned payloads are authoritative.
    return False


def city_cache_needs_enrichment(payload: dict[str, Any]) -> bool:
    """Return True when default-height buildings still need raster enhancement."""
    if city_cache_missing_height_source(payload):
        return True

    features = building_features(payload)
    if not features:
        return False

    enhancement = payload.get("height_enhancement") or {}
    if enhancement.get("source_name") == "merged":
        return False

    return any((feat.get("properties") or {}).get("height_source") == "default" for feat in features)
