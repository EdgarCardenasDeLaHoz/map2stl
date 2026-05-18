"""
Test what happens to city raster dimensions when projection is applied.
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


def test_city_raster_projection_dims():
    """Test if city raster dimensions change when projection is applied."""
    print("\n" + "="*70)
    print("Testing city raster dimension behavior with projections")
    print("="*70)

    for proj in ["none", "cosine", "mercator"]:
        print(f"\n--- Projection: {proj} ---")

        for width, height in [(100, 100), (62, 100), (100, 62)]:
            try:
                city_payload = {
                    **BBOX,
                    "width": width,
                    "height": height,
                    "projection": proj,
                    "clip_valid_region": False,
                }
                city_resp = requests.post(
                    f"{BASE_URL}/api/composite/city-raster", json=city_payload, timeout=30)
                city_data = city_resp.json()
                city_h = city_data.get("height")
                city_w = city_data.get("width")

                match = "MATCH" if (
                    city_h == height and city_w == width) else "MISMATCH"
                print(
                    f"  Request {width}x{height} -> Response {city_w}x{city_h}  [{match}]")

            except Exception as e:
                print(f"  Request {width}x{height} -> Error: {e}")


if __name__ == "__main__":
    test_city_raster_projection_dims()
    print("\n" + "="*70)
    print("Test complete")
    print("="*70)
