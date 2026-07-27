"""
Simple audit script to test DEM and city raster projection alignment.
"""

import json
import base64
import requests
import numpy as np

BBOX = {
    "north": 41.395,
    "south": 41.375,
    "east": 2.175,
    "west": 2.145,
}

BASE_URL = "http://127.0.0.1:9000"


def test_clip_valid_region_behavior():
    """Test how clip_valid_region affects DEM dimensions vs city rasters."""
    print("\n" + "="*70)
    print("Testing clip_valid_region behavior")
    print("="*70)

    for clip_val in [True, False]:
        print(f"\n--- clip_valid_region={clip_val} ---")

        try:
            # DEM with cosine projection
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
            print(f"  DEM (cosine) dimensions: {dem_dims}")

            # City raster with cosine projection
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
            print(f"  City composite (cosine) dimensions: {city_h}x{city_w}")

        except Exception as e:
            print(f"  Error: {e}")


def test_projection_consistency():
    """Test all projections to see dimension patterns."""
    print("\n" + "="*70)
    print("Testing projection consistency (clip_valid_region=false)")
    print("="*70)

    for proj in ["none", "cosine", "mercator"]:
        print(f"\n--- Projection: {proj} ---")

        try:
            # DEM
            dem_params = {
                **BBOX,
                "dim": 100,
                "projection": proj,
                "clip_valid_region": "false",
            }
            dem_resp = requests.get(
                f"{BASE_URL}/api/terrain/dem", params=dem_params, timeout=30)
            dem_data = dem_resp.json()
            dem_dims = dem_data.get("dimensions", [100, 100])
            print(f"  DEM dimensions: {dem_dims}")

            # City composite
            city_payload = {
                **BBOX,
                "width": 100,
                "height": 100,
                "projection": proj,
                "clip_valid_region": False,
            }
            city_resp = requests.post(
                f"{BASE_URL}/api/composite/city-raster", json=city_payload, timeout=30)
            city_data = city_resp.json()
            city_h = city_data.get("height")
            city_w = city_data.get("width")
            print(f"  City composite dimensions: {city_h}x{city_w}")

            # Cities raster
            cities_payload = {
                **BBOX,
                "dim": 100,
                "projection": proj,
                "clip_valid_region": False,
            }
            cities_resp = requests.post(
                f"{BASE_URL}/api/cities/raster", json=cities_payload, timeout=30)
            cities_data = cities_resp.json()
            cities_h = cities_data.get("height")
            cities_w = cities_data.get("width")
            print(f"  Cities raster dimensions: {cities_h}x{cities_w}")

            # Check alignment
            if dem_dims[0] != city_h or dem_dims[1] != city_w:
                print(
                    f"  MISMATCH: DEM is {dem_dims[0]}x{dem_dims[1]}, city is {city_h}x{city_w}")
            else:
                print(f"  OK: All dimensions match")

        except Exception as e:
            print(f"  Error: {e}")


if __name__ == "__main__":
    print("\n" + "="*70)
    print("PROJECTION ALIGNMENT DIAGNOSTIC")
    print("="*70)

    test_clip_valid_region_behavior()
    test_projection_consistency()

    print("\n" + "="*70)
    print("Test complete")
    print("="*70)
