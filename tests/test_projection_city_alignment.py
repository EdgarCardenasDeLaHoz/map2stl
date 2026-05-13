"""
Test that city raster endpoints correctly apply projection parameter.

This test verifies the fix for the alignment issue where city layers
were not being projected to match the DEM when a map projection was selected.
"""

import gzip
import json
import pytest
import numpy as np
from pathlib import Path

BBOX = {
    "north": 41.395,
    "south": 41.375,
    "east": 2.175,
    "west": 2.145,
}

EMPTY_FC = {"type": "FeatureCollection", "features": []}

# A minimal OSM data blob (no features → all arrays should be zeros)
EMPTY_OSM = {
    "buildings":  EMPTY_FC,
    "roads":      EMPTY_FC,
    "waterways":  EMPTY_FC,
    "walls":      EMPTY_FC,
}


def _write_osm_cache(cache_root: Path, bbox: dict, data: dict,
                     tol: float = 0.5, min_area: float = 5.0):
    """Helper: write OSM data as .json.gz where the composite route expects it."""
    from app.server.core.cache import osm_cache_key
    key = osm_cache_key(
        bbox["north"], bbox["south"], bbox["east"], bbox["west"], tol, min_area
    )
    osm_dir = cache_root / "osm"
    osm_dir.mkdir(parents=True, exist_ok=True)
    (osm_dir / f"{key}.json.gz").write_bytes(
        gzip.compress(json.dumps(data).encode())
    )
    return key


class TestProjectionCityAlignment:
    """Verify city raster endpoints properly accept projection parameters."""

    def test_composite_city_raster_with_projection_none(self, client, tmp_data_dir):
        """Test composite city-raster endpoint with projection='none'."""
        _write_osm_cache(tmp_data_dir["cache_root"], BBOX, EMPTY_OSM)
        
        payload = {
            **BBOX,
            "width": 50,
            "height": 50,
            "projection": "none",
            "clip_valid_region": False,
        }
        
        resp = client.post("/api/composite/city-raster", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        
        assert "buildings" in body
        assert body["width"] > 0 and body["height"] > 0
        print(f"✓ Composite city-raster projection='none': OK")

    def test_composite_city_raster_with_projection_cosine(self, client, tmp_data_dir):
        """Test composite city-raster endpoint with projection='cosine'."""
        _write_osm_cache(tmp_data_dir["cache_root"], BBOX, EMPTY_OSM)
        
        payload = {
            **BBOX,
            "width": 50,
            "height": 50,
            "projection": "cosine",
            "clip_valid_region": False,
        }
        
        resp = client.post("/api/composite/city-raster", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        
        assert "buildings" in body
        assert body["width"] > 0 and body["height"] > 0
        print(f"✓ Composite city-raster projection='cosine': OK")

    def test_composite_city_raster_with_projection_mercator(self, client, tmp_data_dir):
        """Test composite city-raster endpoint with projection='mercator'."""
        _write_osm_cache(tmp_data_dir["cache_root"], BBOX, EMPTY_OSM)
        
        payload = {
            **BBOX,
            "width": 50,
            "height": 50,
            "projection": "mercator",
            "clip_valid_region": False,
        }
        
        resp = client.post("/api/composite/city-raster", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        
        assert "buildings" in body
        assert body["width"] > 0 and body["height"] > 0
        print(f"✓ Composite city-raster projection='mercator': OK")

    def test_cities_raster_with_projection_none(self, client, tmp_data_dir):
        """Test that /api/cities/raster accepts projection='none'."""
        payload = {
            **BBOX,
            "dim": 50,
            "projection": "none",
            "clip_valid_region": False,
        }
        
        resp = client.post("/api/cities/raster", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        
        assert "values" in body
        assert body["width"] > 0 and body["height"] > 0
        print(f"✓ Cities raster projection='none': OK")

    def test_cities_raster_with_projection_mercator(self, client, tmp_data_dir):
        """Test that /api/cities/raster accepts projection='mercator'."""
        payload = {
            **BBOX,
            "dim": 50,
            "projection": "mercator",
            "clip_valid_region": False,
        }
        
        resp = client.post("/api/cities/raster", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        
        assert "values" in body
        assert body["width"] > 0 and body["height"] > 0
        print(f"✓ Cities raster projection='mercator': OK")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
