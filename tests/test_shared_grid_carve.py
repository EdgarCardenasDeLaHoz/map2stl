"""The projection-independent shared grid: one DEM read and one river carve per box.

Switching the Edit map's projection used to re-read the base DEM and re-carve every
river (Europa: 3.6 s and ~10 s), because each projection samples its own source grid.
Both now happen once on ``geo2stl.projections.shared_source_shape`` and each
projection pools from it (``geo2stl.water_layers.pool_river_stack``).
"""
from __future__ import annotations

import threading
import time

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString

# Imported before any test patches config.TEST_MODE: these modules bind it at
# import, and importing them under the patch left later test files online.
import app.server.routers.composite  # noqa: E402,F401  (also registers the water sources)
import app.server.routers.terrain  # noqa: E402,F401
from geo2stl import dem as dem_mod
from geo2stl import water_layers as wl
from geo2stl.projections import (
    get_projection_info,
    shared_covers,
    shared_source_dim,
    shared_source_shape,
    source_dim,
    source_shape,
)

N, S, E, W = 40.05, 40.0, -75.0, -75.1
BBOX = {"north": N, "south": S, "east": E, "west": W}
BOX = (N, S, E, W)
EUROPA = (68.65655498475738, 27.839076094777816, 47.28515625000001, -16.347656250000004)


# ---------------------------------------------------------------------------
# Shared grid size
# ---------------------------------------------------------------------------

def test_shared_grid_covers_every_projection_within_the_cap():
    for box in (BOX, EUROPA):
        sd = shared_source_dim(600, box)
        assert 600 <= sd <= 1200
        shared = shared_source_shape(600, box)
        for p in get_projection_info():
            if source_dim(600, box, p) <= sd:
                assert shared_covers(shared, source_shape(600, box, p)), p
    assert shared_source_dim(600, EUROPA, maintain_dimensions=True) == 600
    assert not shared_covers((10, 20), (11, 20))


# ---------------------------------------------------------------------------
# Pooling keeps thin rivers
# ---------------------------------------------------------------------------

def _lines(shape, row, col, depth=2.0):
    a = np.zeros(shape)
    a[row, :] = depth
    a[:, col] = depth
    return a


@pytest.mark.parametrize("target", [(61, 97), (99, 149), (50, 75), (100, 150)])
def test_pool_river_stack_keeps_one_pixel_lines_without_widening(target):
    src = _lines((100, 150), 37, 71)
    pooled = wl.pool_river_stack(np.stack([src, src * 0.5]), target)
    assert pooled.shape == (2, *target)
    rows = np.flatnonzero(pooled[0].all(axis=1))
    cols = np.flatnonzero(pooled[0].all(axis=0))
    assert len(rows) == 1 and len(cols) == 1                  # survives, one cell wide
    assert pooled[0].max() == 2.0 and pooled[1].max() == 1.0  # depth kept per order
    assert (pooled[0] > 0).sum() == target[0] + target[1] - 1


def test_pool_deepest_keeps_the_deepest_cell():
    src = -_lines((100, 150), 0, 149, depth=3.0)
    src[50, 20] = -7.0
    out = wl.pool_deepest(src, (33, 51))
    assert out.shape == (33, 51) and out.min() == -7.0
    assert (out[0] == -3.0).all() and (out[:, -1] == -3.0).all()
    assert wl.pool_deepest(src, (120, 150)).shape == (120, 150)   # upsampling falls back


# ---------------------------------------------------------------------------
# shared_base_grid: memo, single flight, disk
# ---------------------------------------------------------------------------

@pytest.fixture
def slow_dem(monkeypatch):
    calls = []
    real = dem_mod.fetch_layer_data

    def fake(source, n, s, e, w, dim, options=None, **kw):
        if source != "SRTMGL1":
            return real(source, n, s, e, w, dim, options, **kw)
        calls.append(dim)
        time.sleep(0.2)
        yy, xx = np.mgrid[0:dim, 0:dim]
        return 100.0 + yy + 0.5 * xx

    monkeypatch.setattr(dem_mod, "fetch_layer_data", fake)
    return calls


def test_shared_base_is_read_once_by_concurrent_callers(slow_dem):
    out, gate = [], threading.Barrier(6)

    def worker():
        gate.wait()
        out.append(dem_mod.shared_base_grid("SRTMGL1", N, S, E, W, 64, {}))

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(slow_dem) == 1 and len(out) == 6
    assert all(o is out[0] for o in out)
    assert out[0].shape == shared_source_shape(64, BOX)
    assert not dem_mod._SHARED_BASE_LOCKS                    # lock entries released

    dem_mod._SHARED_BASE_MEMO.clear()                        # disk cache for OpenTopo sources
    again = dem_mod.shared_base_grid("SRTMGL1", N, S, E, W, 64, {})
    assert len(slow_dem) == 1 and np.array_equal(again, out[0])


def test_shared_base_source_excludes_registered_layers():
    assert dem_mod.is_shared_base_source("SRTMGL1")
    assert not dem_mod.is_shared_base_source("hydrorivers")
    assert not dem_mod.is_shared_base_source("water_esa")
    assert dem_mod.is_shared_grid_source("hydrorivers")
    assert not dem_mod.is_shared_grid_source("lakes")


# ---------------------------------------------------------------------------
# One carve for every projection
# ---------------------------------------------------------------------------

@pytest.fixture
def carve_env(monkeypatch, slow_dem):
    import app.server.config as config_mod
    from geo2stl import hydrology

    monkeypatch.setattr(config_mod, "TEST_MODE", False, raising=False)
    river = gpd.GeoDataFrame({"geometry": [LineString([(W - 0.01, 40.021), (E + 0.01, 40.031)])],
                              "ORD_STRA": [4]}, crs="EPSG:4326")
    monkeypatch.setattr(hydrology, "fetch_hydrorivers", lambda *a, **k: river)
    carves = []
    real = wl.river_depth_by_order

    def counted(gdf, n, s, e, w, shape, **kw):
        carves.append(tuple(shape))
        return real(gdf, n, s, e, w, shape, **kw)

    monkeypatch.setattr(wl, "river_depth_by_order", counted)
    return carves


PROJECTIONS = ("none", "sinusoidal", "mercator", "cosine")
LAYERS = [{"source": "SRTMGL1", "dim": 64, "blend_mode": "base"},
          {"source": "hydrorivers", "dim": 64, "blend_mode": "add",
           "options": {"min_order": 3, "width_scale": 1}}]


def test_composite_carves_once_across_projections(carve_env, slow_dem):
    from app.server.routers.composite import compute_composite_dem

    for p in PROJECTIONS:
        base, carve = compute_composite_dem(BBOX, 64, LAYERS, p, split_carve=True)
        assert carve.min() < 0, p                             # the river is there
        assert np.isfinite(base).any()
    assert carve_env == [shared_source_shape(64, BOX)]
    assert len(slow_dem) == 1


def test_rivers_preview_shares_the_composite_carve(carve_env, slow_dem):
    from app.server.routers.composite import compute_composite_dem
    from app.server.routers.terrain import _river_print_payload

    compute_composite_dem(BBOX, 64, LAYERS, "none", split_carve=True)
    for p in PROJECTIONS:
        _river_print_payload(N, S, E, W, 64, "SRTMGL1", "hydrorivers", 3, 1.0, 1.0, p, True, False)
    assert carve_env == [shared_source_shape(64, BOX)]
    assert len(slow_dem) == 1


def test_carve_memo_answers_without_the_disk(carve_env, monkeypatch):
    from geo2stl.water_layers import RIVER_FETCH

    base = dem_mod.shared_base_grid("SRTMGL1", N, S, E, W, 64, {})
    opts = {"min_order": 3, "width_scale": 1}
    sd = shared_source_dim(64, BOX)
    first = wl.river_carve_layers("hydrorivers", RIVER_FETCH["hydrorivers"], N, S, E, W, sd, opts, base)

    def no_disk(*a, **k):
        raise AssertionError("the memo should answer")

    monkeypatch.setattr("geo2stl.cache.read_array_cache", no_disk)
    second = wl.river_carve_layers("hydrorivers", RIVER_FETCH["hydrorivers"], N, S, E, W, sd, opts, base)
    assert np.array_equal(first[1], second[1]) and len(carve_env) == 1
