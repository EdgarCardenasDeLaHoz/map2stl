"""Open sea in the water mask, and failed river fetches never cached as "no rivers".

Europa (2026-10-14): the 2D Rivers & lakes layer showed only a coastal band of
water and no river. ESA WorldCover class 80 covers the coastal strip only (the
open sea is its no-data, 0), and a broken pyarrow made every HydroRIVERS read
return None, which the river carve and the preview caches kept for good.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

from geo2stl import hydrology, sat2stl
from geo2stl import water_layers as wl

N, S, E, W = 40.05, 40.0, -75.0, -75.1


def _river(order=5, lat=40.025):
    return gpd.GeoDataFrame({"geometry": [LineString([(W - 0.01, lat), (E + 0.01, lat)])],
                             "ORD_STRA": [order]}, crs="EPSG:4326")


def _esa_coast():
    """ESA grid: land (10) on the right, a coastal water strip (80), open sea (0) left."""
    esa = np.full((40, 60), 10, dtype=np.uint8)
    esa[:, :20] = 0        # outside every WorldCover tile: the open sea
    esa[:, 20:25] = 80     # sea inside a coastal tile
    return esa


def test_esa_water_counts_no_data_as_sea():
    water = sat2stl.esa_water(_esa_coast())
    assert water[:, :25].all()          # open sea and coastal strip
    assert not water[:, 25:].any()      # land
    assert not sat2stl.esa_water(np.array([10, 20, 30, 40, 50, 60, 90, 95, 100])).any()


def test_water_mask_fills_the_open_sea(monkeypatch):
    # Large box (> 30 km) with no local elevation store: ESA alone must fill the sea.
    monkeypatch.setattr(sat2stl, "fetch_water_mask_images",
                        lambda *a, **k: (_esa_coast(), None, None))
    mask, esa, _ = sat2stl.fetch_water_mask(60.0, 40.0, 20.0, -10.0, 5000, "esa")
    assert mask.dtype == np.float32
    assert mask[:, :25].min() == 1.0    # the interior, not only the coastal band
    assert mask[:, 25:].max() == 0.0


def test_failed_river_fetch_is_not_cached(monkeypatch):
    calls = []
    answers = [None, _river(), _river()]

    def fetch(n, s, e, w, options):
        calls.append(options["min_order"])
        return answers[len(calls) - 1]

    base = np.zeros((40, 60))
    orders, _ = wl.river_carve_layers("t_src", fetch, N, S, E, W, 60, {"min_order": 5},
                                      base)
    assert len(orders) == 0
    # The empty carve was not cached: the next call fetches again and gets the river.
    orders, stack = wl.river_carve_layers("t_src", fetch, N, S, E, W, 60,
                                          {"min_order": 5}, base)
    assert len(orders) and stack.max() > 0 and len(calls) == 2
    # A carve with rivers is cached: no third fetch.
    wl.river_carve_layers("t_src", fetch, N, S, E, W, 60, {"min_order": 5}, base)
    assert len(calls) == 2


def test_unreadable_hydrorivers_region_raises(monkeypatch, tmp_path):
    import time

    monkeypatch.setattr(hydrology, "_cache_dir", lambda: tmp_path)   # no parquet at all
    monkeypatch.setattr(hydrology, "_ensure_region_parquet", lambda region: None)
    with pytest.raises(hydrology.HydroRiversUnavailable):
        hydrology._read_region_parquets(["eu"], W, S, E, N, True, time)


def test_order3_read_does_not_rebuild_the_full_parquet(monkeypatch, tmp_path):
    """A valid order 3+ parquet answers an order 3+ read even when the full one is
    missing: rebuilding that from the shapefile cost Colombia's first open 21 s
    (geo2stl/hydrology.py::_read_region_parquets)."""
    import time

    monkeypatch.setattr(hydrology, "_cache_dir", lambda: tmp_path)
    gpd.GeoDataFrame({"ORD_STRA": [4], "geometry": [LineString([(W, S), (E, N)])]},
                     crs="EPSG:4326").to_parquet(
        hydrology._region_parquet_path("eu", order3plus=True), write_covering_bbox=True)
    built = []
    monkeypatch.setattr(hydrology, "_ensure_region_parquet",
                        lambda region: built.append(region))
    gdf = hydrology._read_region_parquets(["eu"], W, S, E, N, True, time)
    assert built == [] and len(gdf) == 1
    with pytest.raises(hydrology.HydroRiversUnavailable):   # the full read still needs a build
        hydrology._read_region_parquets(["eu"], W, S, E, N, False, time)
    assert built == ["eu"]


def test_incomplete_rivers_preview_is_not_cacheable():
    from app.server.routers.terrain import _print_payload_complete

    assert _print_payload_complete({"water_surface": True, "feature_count": 12})
    assert not _print_payload_complete({"water_surface": False, "feature_count": 12})
    assert not _print_payload_complete({"water_surface": True, "feature_count": 0})
