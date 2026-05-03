"""
Compatibility wrapper: re-exports cache-policy predicates from city2stl.cache_policy.

Deprecated. Use city2stl.cache_policy directly.
"""

from city2stl.cache_policy import (
    building_features,
    city_cache_missing_building_parts,
    city_cache_missing_height_source,
    city_cache_needs_enrichment,
)

__all__ = [
    "building_features",
    "city_cache_missing_height_source",
    "city_cache_missing_building_parts",
    "city_cache_needs_enrichment",
]
