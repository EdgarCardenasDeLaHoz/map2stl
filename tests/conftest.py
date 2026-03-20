"""
Shared pytest fixtures for strm2stl API tests.

Sets STRM2STL_TEST_MODE=1 so the DEM endpoint returns a fast deterministic
response without any Earth Engine or network calls.
"""
import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# Point to strm2stl root so location_picker.py imports work
_STRM2STL_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_STRM2STL_ROOT))
sys.path.insert(0, str(_STRM2STL_ROOT / "ui"))

# Enable test mode before importing the app
os.environ["STRM2STL_TEST_MODE"] = "1"


@pytest.fixture()
def tmp_data_dir(tmp_path, monkeypatch):
    """
    Redirect COORDINATES_PATH, REGION_SETTINGS_PATH, and OSM_CACHE_PATH to
    a temporary directory so tests never touch real data files.
    """
    import strm2stl.ui.location_picker as lp

    coords_file = tmp_path / "coordinates.json"
    coords_file.write_text(json.dumps({"regions": [
        {
            "name": "TestRegion",
            "north": 40.0, "south": 39.9, "east": -75.1, "west": -75.2,
            "description": "Test region",
            "label": None,
            "parameters": {
                "dim": 100, "depth_scale": 0.5, "water_scale": 0.05,
                "height": 10, "base": 2, "subtract_water": True, "sat_scale": 500
            }
        }
    ]}))

    settings_file = tmp_path / "region_settings.json"
    osm_cache = tmp_path / "osm_raw_cache"
    osm_cache.mkdir()

    monkeypatch.setattr(lp, "COORDINATES_PATH", coords_file)
    monkeypatch.setattr(lp, "REGION_SETTINGS_PATH", settings_file)
    monkeypatch.setattr(lp, "OSM_CACHE_PATH", osm_cache)

    return {
        "coords_file": coords_file,
        "settings_file": settings_file,
        "osm_cache": osm_cache,
        "tmp_path": tmp_path,
    }


@pytest.fixture()
def client(tmp_data_dir):
    """FastAPI TestClient with patched data paths."""
    from fastapi.testclient import TestClient
    import strm2stl.ui.location_picker as lp
    return TestClient(lp.app)
