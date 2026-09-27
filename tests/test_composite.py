"""
Tests for POST /api/composite/city-raster.

The endpoint rasterizes cached OSM data into per-pixel height-delta arrays.
We test the no-cache, cache-miss, and cache-hit paths without real OSM network calls.
"""
import gzip
import json
from pathlib import Path

SMALL_BBOX = {"north": 39.960, "south": 39.950, "east": -75.140, "west": -75.170}
PAYLOAD = {**SMALL_BBOX, "width": 32, "height": 32}

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


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCityRasterEndpoint:
    def test_returns_empty_arrays_when_no_osm_cache(self, client, tmp_data_dir):
        """No OSM cache entry → endpoint returns zero-filled arrays."""
        resp = client.post("/api/composite/city-raster", json=PAYLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 32
        assert body["height"] == 32
        for layer in ("buildings", "roads", "waterways", "walls"):
            assert layer in body
            assert len(body[layer]) == 32 * 32
            assert all(v == 0.0 for v in body[layer])

    def test_rasterize_with_empty_osm_features(self, client, tmp_data_dir):
        """OSM cache exists but has no features → still all zeros."""
        _write_osm_cache(tmp_data_dir["cache_root"], SMALL_BBOX, EMPTY_OSM)
        resp = client.post("/api/composite/city-raster", json=PAYLOAD)
        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 32
        assert body["height"] == 32
        for layer in ("buildings", "roads", "waterways", "walls"):
            assert all(v == 0.0 for v in body[layer])

    def test_cache_hit_skips_rasterization(self, client, tmp_data_dir, monkeypatch):
        """Second identical request is served from the array cache."""
        import app.server.routers.composite as comp_router

        _write_osm_cache(tmp_data_dir["cache_root"], SMALL_BBOX, EMPTY_OSM)

        call_count = {"n": 0}
        original_rasterize = comp_router._rasterize_city

        def counting_rasterize(req):
            call_count["n"] += 1
            return original_rasterize(req)

        monkeypatch.setattr(comp_router, "_rasterize_city", counting_rasterize)

        client.post("/api/composite/city-raster", json=PAYLOAD)
        assert call_count["n"] == 1

        # Second call should hit the array cache — rasterize not called again
        client.post("/api/composite/city-raster", json=PAYLOAD)
        assert call_count["n"] == 1

    def test_response_has_correct_pixel_count(self, client, tmp_data_dir):
        """Width × height pixels in each layer."""
        payload = {**SMALL_BBOX, "width": 16, "height": 24}
        resp = client.post("/api/composite/city-raster", json=payload)
        assert resp.status_code == 200
        body = resp.json()
        assert body["width"] == 16
        assert body["height"] == 24
        assert len(body["buildings"]) == 16 * 24

    def test_building_pixels_nonzero_with_building_feature(self, client, tmp_data_dir):
        """A building polygon that fills the entire bbox → building array not all zero."""
        N, S, E, W = SMALL_BBOX["north"], SMALL_BBOX["south"], SMALL_BBOX["east"], SMALL_BBOX["west"]
        building_feature = {
            "type": "Feature",
            "geometry": {
                "type": "Polygon",
                "coordinates": [[
                    [W, S], [E, S], [E, N], [W, N], [W, S]
                ]]
            },
            "properties": {"height_m": 15.0}
        }
        osm_data = {
            **EMPTY_OSM,
            "buildings": {"type": "FeatureCollection", "features": [building_feature]},
        }
        _write_osm_cache(tmp_data_dir["cache_root"], SMALL_BBOX, osm_data)

        resp = client.post("/api/composite/city-raster", json=PAYLOAD)
        assert resp.status_code == 200
        buildings = resp.json()["buildings"]
        assert any(v > 0 for v in buildings), "Expected nonzero building pixels"


def test_building_burn_keeps_tallest_and_leaves_holes_empty():
    """Overlapping footprints take the max (not the sum) and courtyards stay 0."""
    from app.server.routers.composite import _rasterize_buildings

    def square(x0, y0, x1, y1):
        return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]

    feats = [
        {"geometry": {"type": "Polygon",
                      "coordinates": [square(0, 0, 10, 10), square(4, 4, 6, 6)]},
         "properties": {"height_m": 20.0}},
        {"geometry": {"type": "Polygon", "coordinates": [square(0, 0, 3, 3)]},
         "properties": {"height_m": 30.0}},
    ]
    arr = _rasterize_buildings(feats, (0.0, 0.0, 10.0, 10.0), 10, 10)
    assert arr.dtype.name == "float32"
    assert arr.max() == 30.0                 # overlap: max, not 50
    assert arr[9, 0] == 30.0                 # row 0 = north: the (0..3, 0..3) square is bottom-left
    assert arr[5, 5] == 0.0                  # courtyard hole
    assert arr[0, 9] == 20.0
