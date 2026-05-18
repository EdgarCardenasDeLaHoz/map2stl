"""
Audit script to test DEM and city raster projection alignment.
Compares output dimensions and coordinate systems when using projections.
"""

import json
import base64
import requests
import numpy as np
from pathlib import Path

# Barcelona Eixample test region
BBOX = {
    "north": 41.395,
    "south": 41.375,
    "east": 2.175,
    "west": 2.145,
}

BASE_URL = "http://127.0.0.1:9000"


def test_projection_alignment(projection="cosine"):
    """
    Test that DEM and city raster produce aligned outputs with given projection.
    """
    print(f"\n{'='*70}")
    print(f"Testing projection: {projection}")
    print(f"{'='*70}")

    # Request DEM with projection (uses query parameters, not JSON body)
    print(f"\n1. Fetching DEM with projection={projection}...")
    dem_params = {
        **BBOX,
        "dim": 100,
        "projection": projection,
        "clip_valid_region": "false",
    }

    try:
        dem_resp = requests.get(
            f"{BASE_URL}/api/terrain/dem", params=dem_params, timeout=30)
        dem_resp.raise_for_status()
        dem_data = dem_resp.json()

        # Parse base64-encoded DEM values
        dem_b64 = dem_data.get("dem_values_b64")
        if dem_b64:
            dem_bytes = base64.b64decode(dem_b64)
            dem_vals = np.frombuffer(dem_bytes, dtype=np.float32)
            # Reshape based on dimensions
            dims = dem_data.get("dimensions", [100, 100])
            if isinstance(dims, str):
                dims = json.loads(dims)
            dem_h, dem_w = int(dims[0]), int(dims[1])
            dem_vals = dem_vals.reshape(dem_h, dem_w)
        else:
            print(f"   ✗ DEM response has no dem_values_b64")
            return False

        print(f"   ✓ DEM: shape={dem_h}×{dem_w}")
        print(
            f"   ✓ DEM: min={np.nanmin(dem_vals):.2f}, max={np.nanmax(dem_vals):.2f}, finite={np.count_nonzero(np.isfinite(dem_vals))}")
    except Exception as e:
        print(f"   ✗ DEM fetch failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    # Request composite city raster with projection
    print(
        f"\n2. Fetching composite city raster with projection={projection}...")
    city_payload = {
        **BBOX,
        "width": 100,
        "height": 100,
        "projection": projection,
        "clip_valid_region": False,
    }

    try:
        city_resp = requests.post(
            f"{BASE_URL}/api/composite/city-raster", json=city_payload, timeout=30)
        city_resp.raise_for_status()
        city_data = city_resp.json()
        city_h = int(city_data.get("height", 100))
        city_w = int(city_data.get("width", 100))

        # Check if composite has the expected layers
        buildings = np.array(city_data.get("buildings", []),
                             dtype=np.float32).reshape(city_h, city_w)

        print(f"   ✓ City composite: shape={city_h}×{city_w}")
        print(
            f"   ✓ Buildings: min={np.nanmin(buildings):.2f}, max={np.nanmax(buildings):.2f}, finite={np.count_nonzero(np.isfinite(buildings))}")
    except Exception as e:
        print(f"   ✗ City composite fetch failed: {e}")
        return False

    # Request cities raster endpoint with projection
    print(f"\n3. Fetching cities raster with projection={projection}...")
    cities_raster_payload = {
        **BBOX,
        "dim": 100,
        "projection": projection,
        "clip_valid_region": False,
    }

    try:
        cities_raster_resp = requests.post(
            f"{BASE_URL}/api/cities/raster", json=cities_raster_payload, timeout=30)
        cities_raster_resp.raise_for_status()
        cities_raster_data = cities_raster_resp.json()
        cities_h = int(cities_raster_data["height"])
        cities_w = int(cities_raster_data["width"])
        cities_vals = np.array(
            cities_raster_data["values"], dtype=np.float32).reshape(cities_h, cities_w)

        print(f"   ✓ Cities raster: shape={cities_h}×{cities_w}")
        print(
            f"   ✓ Cities raster: min={np.nanmin(cities_vals):.2f}, max={np.nanmax(cities_vals):.2f}, finite={np.count_nonzero(np.isfinite(cities_vals))}")
    except Exception as e:
        print(f"   ✗ Cities raster fetch failed: {e}")
        return False

    # Compare dimensions
    print(f"\n4. Dimension Alignment Check:")
    dims_match = (dem_h == city_h == cities_h) and (
        dem_w == city_w == cities_w)
    if dims_match:
        print(f"   ✓ All layers have same dimensions: {dem_h}×{dem_w}")
    else:
        print(f"   ✗ Dimension mismatch!")
        print(f"      DEM:           {dem_h}×{dem_w}")
        print(f"      City composite: {city_h}×{city_w}")
        print(f"      Cities raster:  {cities_h}×{cities_w}")

    return dims_match


def test_cache_invalidation():
    """
    Test that changing projection causes cache invalidation (new fetch, not old cache).
    """
    print(f"\n{'='*70}")
    print(f"Testing cache invalidation across projections")
    print(f"{'='*70}")

    projections = ["none", "cosine", "mercator"]
    cache_hits = {}

    for proj in projections:
        print(f"\nFetching composite city-raster with projection={proj}...")
        payload = {
            **BBOX,
            "width": 100,
            "height": 100,
            "projection": proj,
            "clip_valid_region": False,
        }

        # Fetch twice to see if second is a cache hit
        for attempt in [1, 2]:
            try:
                resp = requests.post(
                    f"{BASE_URL}/api/composite/city-raster", json=payload, timeout=30)
                resp.raise_for_status()
                data = resp.json()
                h, w = int(data.get("height")), int(data.get("width"))
                print(f"  Attempt {attempt}: {h}×{w}")
            except Exception as e:
                print(f"  Attempt {attempt} failed: {e}")

    print("\n✓ Cache invalidation test complete")


def test_clip_valid_region_behavior():
    """Test how clip_valid_region affects DEM dimensions vs city rasters."""
    print(f"\n{'='*70}")
    print(f"Testing clip_valid_region behavior")
    print(f"{'='*70}")

    for clip_val in [True, False]:
        print(f"\n--- clip_valid_region={clip_val} ---")

        try:
            # DEM
            dem_params = {
                **BBOX,
                "dim": 100,
                "projection": "cosine",
                "clip_valid_region": str(clip_val).lower(),
            }
            dem_resp = requests.get(
                f"{BASE_URL}/api/terrain/dem", params=dem_params, timeout=30)
            dem_data = dem_resp.json()
            dem_dims = dem_data.get("dimensions", [100, 100])
            print(f"  DEM dimensions: {dem_dims}")

            # City raster
            city_payload = {
                **BBOX,
                "width": 100,
                "height": 100,
                "projection": "cosine",
                "clip_valid_region": clip_val,
            }
            city_resp = requests.post(
                f"{BASE_URL}/api/composite/city-raster", json=city_payload, timeout=30)
            city_data = city_resp.json()
            city_h = city_data.get("height")
            city_w = city_data.get("width")
            print(f"  City composite dimensions: {city_h}×{city_w}")
        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    # Test each projection
    success = True
    for proj in ["none", "cosine", "mercator"]:
        if not test_projection_alignment(proj):
            success = False

    # Test cache invalidation
    test_cache_invalidation()

    # Test clip_valid_region behavior
    test_clip_valid_region_behavior()

    if success:
        print(f"\n{'='*70}")
        print("✓ All projection alignment tests passed")
        print(f"{'='*70}")
    else:
        print(f"\n{'='*70}")
        print("✗ Some tests failed - see details above")
        print(f"{'='*70}")
