"""
core/osm_cache_policy.py — Predicates for OSM cache staleness detection.

Extracted from routers/cities.py so cache freshness logic can be tested
and reused without importing FastAPI router machinery.
"""

from __future__ import annotations

from typing import Any, Dict, List


def building_features(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    buildings = payload.get("buildings") or {}
    features = buildings.get("features") or []
    return features if isinstance(features, list) else []


def city_cache_missing_height_source(payload: Dict[str, Any]) -> bool:
    """Detect older cached city payloads created before height_source existed."""
    features = building_features(payload)
    if not features:
        return False
    return any("height_source" not in ((feat.get("properties") or {})) for feat in features)


def city_cache_missing_building_parts(payload: Dict[str, Any]) -> bool:
    """Detect cached payloads written before building:parts and extended layers were added.

    When towers/churches/fortifications keys are absent the data was cached before
    the _fetch_building_parts merge was introduced — a fresh fetch is needed.
    """
    return "towers" not in payload and "churches" not in payload


def city_cache_needs_enrichment(payload: Dict[str, Any]) -> bool:
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
