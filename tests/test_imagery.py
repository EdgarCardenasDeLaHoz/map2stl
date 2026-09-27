"""geo2stl.imagery — slippy tile math and the stitched bbox fetch (no network)."""
from __future__ import annotations

import io
import math

import numpy as np
import pytest
from PIL import Image

from geo2stl import imagery


def test_tile_math_round_trip():
    lon, lat, z = -73.9857, 40.7484, 15
    x, y = imagery.lonlat_to_tile(lon, lat, z)
    b = imagery.tile_bounds(x, y, z)
    assert b["west"] <= lon <= b["east"] and b["south"] <= lat <= b["north"]
    back = imagery.global_px_to_lonlat(imagery.lon_to_global_px(lon, z),
                                       imagery.lat_to_global_px(lat, z), z)
    assert back == pytest.approx((lon, lat), abs=1e-9)
    assert imagery.lonlat_to_tile(0.0, 0.0, 1) == (1, 1)
    assert imagery.m_per_px(0.0, 0) == pytest.approx(156_543.03, rel=1e-6)


def test_choose_zoom_caps_tiles():
    small = {"north": 40.76, "south": 40.74, "east": -73.97, "west": -73.99}
    assert imagery.choose_zoom(small, target_m_per_px=0.5) == imagery.MAX_ZOOM
    big = {"north": 45.0, "south": 35.0, "east": -70.0, "west": -80.0}
    z = imagery.choose_zoom(big, target_m_per_px=1.0, max_tiles_total=400)
    x0, x1, y0, y1 = imagery.tile_range(big, z)
    assert (x1 - x0 + 1) * (y1 - y0 + 1) <= 400
    with pytest.raises(ValueError):
        imagery.choose_zoom(small)


class _FakeSession:
    """Serves solid tiles whose red channel is the tile x index."""

    def __init__(self):
        self.calls = 0

    def get(self, url, timeout=None):
        self.calls += 1
        x = int(url.rsplit("/", 1)[1])
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (x % 256, 0, 0)).save(buf, "PNG")
        resp = type("R", (), {})()
        resp.content = buf.getvalue()
        resp.raise_for_status = lambda: None
        return resp


def test_fetch_rgb_crops_to_bbox(tmp_path):
    bbox = {"north": 40.76, "south": 40.74, "east": -73.97, "west": -73.99}
    sess = _FakeSession()
    rgb, meta = imagery.fetch_rgb(bbox, zoom=16, session=sess, cache_dir=tmp_path)
    z = meta["zoom"]
    w = math.ceil(imagery.lon_to_global_px(bbox["east"], z)) - math.floor(
        imagery.lon_to_global_px(bbox["west"], z))
    assert rgb.shape[1] == w and rgb.dtype == np.uint8
    assert meta["tiles_loaded"] == meta["tiles_total"] == sess.calls
    # second fetch comes from the tile cache
    imagery.fetch_rgb(bbox, zoom=16, session=sess, cache_dir=tmp_path)
    assert sess.calls == meta["tiles_total"]
