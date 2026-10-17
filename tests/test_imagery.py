"""geo2stl.imagery — slippy tile math and the stitched bbox fetch (no network)."""
from __future__ import annotations

import io
import math
import threading

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
        self._lock = threading.Lock()

    def get(self, url, timeout=None):
        with self._lock:
            self.calls += 1
        y, x = (int(v) for v in url.rsplit("/", 2)[1:])
        buf = io.BytesIO()
        Image.new("RGB", (256, 256), (x % 256, y % 256, 0)).save(buf, "PNG")
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


class _NoNetwork:
    def get(self, url, timeout=None):
        raise AssertionError(f"network call on a cache hit: {url}")


def test_tile_cache_hit_needs_no_network(tmp_path, monkeypatch):
    bbox = {"north": 40.76, "south": 40.74, "east": -73.97, "west": -73.99}
    first, meta = imagery.fetch_rgb(bbox, zoom=16, session=_FakeSession(),
                                    cache_dir=tmp_path)
    monkeypatch.setattr(imagery, "new_session", lambda *a, **k: _NoNetwork())
    again, meta2 = imagery.fetch_rgb(bbox, zoom=16, cache_dir=tmp_path)
    assert np.array_equal(first, again) and meta2 == meta
    # layout: <cache_dir>/<z>/<x>/<y>.jpg, holding the server's own bytes
    x0, _, y0, _ = imagery.tile_range(bbox, 16)
    path = imagery.tile_cache_path(tmp_path, 16, x0, y0)
    assert path == tmp_path / "16" / str(x0) / f"{y0}.jpg" and path.exists()
    assert Image.open(path).format == "PNG"
    assert not list(tmp_path.rglob("*.tmp"))


def test_pooled_stitch_matches_serial(tmp_path):
    x0, x1, y0, y1 = 19296, 19301, 24633, 24637
    serial, n_serial = imagery.stitch_tiles(x0, x1, y0, y1, 16,
                                            session=_FakeSession(), workers=1)
    sess = _FakeSession()
    pooled, n_pooled = imagery.stitch_tiles(x0, x1, y0, y1, 16, session=sess, workers=8)
    assert n_serial == n_pooled == sess.calls == 30
    assert serial.tobytes() == pooled.tobytes()
    # a half-warm cache gives the same image again
    for tx in range(x0, x1 + 1, 2):
        imagery.fetch_tile(16, tx, y0, session=_FakeSession(), cache_dir=tmp_path)
    warm = _FakeSession()
    mixed, n_mixed = imagery.stitch_tiles(x0, x1, y0, y1, 16, session=warm,
                                          cache_dir=tmp_path)
    assert n_mixed == 30 and warm.calls == 27
    assert mixed.tobytes() == serial.tobytes()


def test_pooled_stitch_threads_own_sessions(monkeypatch):
    made = []

    def _new(*_a, **_k):
        s = _FakeSession()
        made.append(s)
        return s

    monkeypatch.setattr(imagery, "new_session", _new)
    img, n = imagery.stitch_tiles(0, 3, 0, 3, 2, workers=4)
    assert n == 16 and sum(s.calls for s in made) == 16
    assert 1 <= len(made) <= 4


def test_default_tile_cache_dir_follows_cache_root(tmp_path, monkeypatch):
    from geo2stl import cache as geo_cache
    monkeypatch.setattr(geo_cache, "CACHE_ROOT", tmp_path)
    assert imagery.default_tile_cache_dir() == tmp_path / "tiles" / "esri"
    assert imagery.default_tile_cache_dir("other") == tmp_path / "tiles" / "other"
