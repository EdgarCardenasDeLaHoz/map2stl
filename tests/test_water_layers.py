"""F-REGION steps 1-3: rivers and lakes as terrain-relative composite layers,
carved after the export's median filter, and the city-layer size guard.

Synthetic GeoDataFrames only: no network, no HydroRIVERS parquet.
"""

from __future__ import annotations

import geopandas as gpd
import numpy as np
import pytest
from shapely.geometry import LineString, Polygon, mapping

from geo2stl import water_layers as wl
from geo2stl.dem import _LAYER_SOURCES, is_terrain_relative_source, register_layer_source

# ~5.6 km x 8.5 km box at 40 N; a 100 x 100 grid is ~85 m x 56 m per pixel.
N, S, E, W = 40.05, 40.0, -75.0, -75.1
BBOX = {"north": N, "south": S, "east": E, "west": W}


def _river(order=None, discharge=None, lat=40.025):
    data = {"geometry": [LineString([(W - 0.01, lat), (E + 0.01, lat)])]}
    if order is not None:
        data["ORD_STRA"] = [order]
    if discharge is not None:
        data["DIS_AV_CMS"] = [discharge]
    return gpd.GeoDataFrame(data, crs="EPSG:4326")


# ---------------------------------------------------------------------------
# Hydraulic geometry
# ---------------------------------------------------------------------------

def test_width_and_depth_follow_discharge():
    w, d = wl.river_size_m(order=[5], discharge=[100.0])
    assert w[0] == pytest.approx(7.2 * 100 ** 0.5)          # 72 m
    assert d[0] == pytest.approx(0.27 * 100 ** 0.39)


def test_missing_discharge_falls_back_to_order_and_clamps():
    w, d = wl.river_size_m(order=[1, 9], discharge=[0.0, np.nan])
    q = wl.order_discharge([1, 9])
    assert w[1] == pytest.approx(7.2 * q[1] ** 0.5)
    assert w[0] == pytest.approx(max(wl.MIN_WIDTH_M, 7.2 * q[0] ** 0.5))
    assert d[0] == wl.MIN_DEPTH_M                            # tiny stream: depth floor
    w_huge, d_huge = wl.river_size_m(discharge=[1e7])
    assert w_huge[0] == wl.MAX_WIDTH_M and d_huge[0] == wl.MAX_DEPTH_M


# ---------------------------------------------------------------------------
# River rasterisation
# ---------------------------------------------------------------------------

def test_narrow_river_is_at_least_one_pixel_and_continuous():
    grid = wl.rasterize_river_depth(_river(order=1), N, S, E, W, (100, 100))
    assert grid.max() == 0.0 and grid.min() < 0.0
    # A 3 m stream on ~56-85 m pixels still burns one pixel in every column.
    assert (grid < 0).any(axis=0).all()
    assert (grid < 0).sum(axis=0).max() <= 2


def test_wide_river_width_is_in_metres():
    # Q = 2500 m^3/s -> W = 7.2 * 50 = 360 m, about 6.4 rows of ~56 m.
    grid = wl.rasterize_river_depth(_river(discharge=2500.0), N, S, E, W, (100, 100))
    rows = (grid[:, 50] < 0).sum()
    px_m = (N - S) * wl.M_PER_DEG_LAT / 100
    assert abs(rows * px_m - 360) <= 2 * px_m
    assert grid.min() == pytest.approx(-0.27 * 2500 ** 0.39)


def test_deeper_reach_wins_where_reaches_overlap():
    gdf = gpd.GeoDataFrame(
        {"geometry": [LineString([(W, 40.025), (E, 40.025)])] * 2,
         "ORD_STRA": [2, 7]}, crs="EPSG:4326")
    grid = wl.rasterize_river_depth(gdf, N, S, E, W, (50, 50))
    _, d = wl.river_size_m(order=[7])
    assert grid.min() == pytest.approx(-d[0])


def test_river_sources_are_registered_and_relative(monkeypatch):
    import app.server.routers.composite  # noqa: F401 - registers the sources
    from geo2stl import hydrology

    for name in ("hydrorivers", "natural_earth_rivers", "lakes"):
        assert is_terrain_relative_source(name)
    monkeypatch.setattr(hydrology, "fetch_hydrorivers",
                        lambda n, s, e, w, min_order=3: _river(order=5))
    base = np.zeros((40, 60))
    grid = wl.hydrorivers_layer(N, S, E, W, 600, {"min_order": 4}, base=base)
    assert grid.shape == base.shape and grid.min() < 0


def test_natural_earth_rank_maps_to_order(monkeypatch):
    gdf = gpd.GeoDataFrame({"geometry": [LineString([(W, 40.02), (E, 40.02)])],
                            "scalerank": [2]}, crs="EPSG:4326")
    monkeypatch.setattr(wl, "natural_earth_river_features", lambda *a, **k: gdf)
    grid = wl.natural_earth_rivers_layer(N, S, E, W, 80, {}, base=np.zeros((50, 80)))
    _, d = wl.river_size_m(order=[8])
    assert grid.min() == pytest.approx(-d[0])


def test_resize_relative_keeps_a_one_pixel_channel():
    layer = np.zeros((100, 100))
    layer[37, :] = -4.0
    small = wl.resize_relative(layer, (30, 30))
    assert small.shape == (30, 30)
    assert small.min() == -4.0 and (small < 0).any(axis=0).all()


# ---------------------------------------------------------------------------
# Lakes
# ---------------------------------------------------------------------------

def _bowl(shape=(60, 60)):
    yy, xx = np.mgrid[: shape[0], : shape[1]]
    return 100.0 + 0.5 * np.hypot(yy - shape[0] / 2, xx - shape[1] / 2)


def _poly(x0, y0, x1, y1, **props):
    """Feature covering fractions [x0, x1] x [y0, y1] of the bbox (y from south)."""
    lon = lambda f: W + f * (E - W)          # noqa: E731
    lat = lambda f: S + f * (N - S)          # noqa: E731
    ring = [(lon(x0), lat(y0)), (lon(x1), lat(y0)), (lon(x1), lat(y1)), (lon(x0), lat(y1))]
    return {"type": "Feature", "properties": props, "geometry": mapping(Polygon(ring))}


def test_lake_is_flat_at_shore_minimum_minus_depth():
    from scipy import ndimage

    dem = 300.0 - _bowl()            # a dome: every lake cell is above the shore minimum
    rel = wl.lake_depth_grid([_poly(0.3, 0.3, 0.7, 0.7, natural="water")],
                             N, S, E, W, dem, depth_m=3.0, min_area_m2=0, smooth=1)
    lake = rel < 0
    assert lake.sum() > 100
    surface = (dem + rel)[lake]
    assert np.ptp(surface) < 1e-9
    ring = ndimage.binary_dilation(lake, np.ones((3, 3))) & ~lake
    assert surface[0] == pytest.approx(dem[ring].min() - 3.0)
    assert rel.max() == 0.0


def test_lake_never_raises_cells_below_its_surface():
    dem = _bowl()                    # bowl: the centre is below the shore level
    rel = wl.lake_depth_grid([_poly(0.3, 0.3, 0.7, 0.7, natural="water")],
                             N, S, E, W, dem, depth_m=1.0, min_area_m2=0)
    assert rel.max() == 0.0 and rel.min() < 0
    assert rel[30, 30] == 0.0


def test_lakes_skip_rivers_mapped_as_areas_and_small_ponds():
    dem = _bowl()
    feats = [_poly(0.3, 0.3, 0.7, 0.7, natural="water", water="river"),
             _poly(0.1, 0.1, 0.105, 0.105, natural="water")]        # ~0.1 ha
    rel = wl.lake_depth_grid(feats, N, S, E, W, dem, min_area_m2=10_000)
    assert not rel.any()


def test_lakes_source_needs_a_base_and_uses_the_fetcher():
    fetched = []

    def fetch(n, s, e, w):
        fetched.append((n, s, e, w))
        return {"type": "FeatureCollection",
                "features": [_poly(0.3, 0.3, 0.7, 0.7, natural="water")]}

    provider = wl.make_lakes_source(fetch)
    assert not provider(N, S, E, W, 64, {}).any()        # no base: nothing, no fetch
    assert fetched == []
    rel = provider(N, S, E, W, 64, {"depth_m": 1.0, "min_area_m2": 0}, base=_bowl())
    assert rel.min() < 0 and fetched


# ---------------------------------------------------------------------------
# Composite + terrain stage
# ---------------------------------------------------------------------------

@pytest.fixture
def composite_env(monkeypatch):
    import app.server.config as config_mod
    from geo2stl import hydrology

    monkeypatch.setattr(config_mod, "TEST_MODE", False, raising=False)
    monkeypatch.setattr("app.server.core.cache.read_array_cache", lambda *a, **k: None)
    monkeypatch.setattr("app.server.core.cache.write_array_cache", lambda *a, **k: None)
    monkeypatch.setattr(hydrology, "fetch_hydrorivers",
                        # Centred on row 31 of 64, so the channel is exactly one pixel wide.
                        lambda n, s, e, w, min_order=3: _river(order=4, lat=N - (N - S) * 31.5 / 64))
    register_layer_source("unit_valley", lambda n, s, e, w, dim, o: np.full((dim, dim), 200.0))
    yield
    _LAYER_SOURCES.pop("unit_valley", None)


LAYERS = [{"source": "unit_valley", "dim": 64, "blend_mode": "base"},
          {"source": "hydrorivers", "dim": 64, "blend_mode": "add", "weight": 2.0}]


def test_composite_splits_the_carve(composite_env):
    from app.server.routers.composite import compute_composite_dem

    merged = compute_composite_dem(BBOX, 64, LAYERS)
    base, carve = compute_composite_dem(BBOX, 64, LAYERS, split_carve=True)
    assert np.allclose(base, 200.0)
    _, d = wl.river_size_m(order=[4])
    assert carve.min() == pytest.approx(-2.0 * d[0])       # weight scales the depth
    assert np.allclose(merged, base + carve)


def test_river_survives_the_median_filter(composite_env):
    """Carving after prepare_dem keeps a 1-px river; baking it into dem_values
    first (what a plain composite would do) lets the 3x3 median erase it."""
    from app.server.core.export import _prepare_dem_array
    from app.server.core.export_params import ExportContext

    data = {"bbox": BBOX, "composite_layers": LAYERS, "composite_dim": 64,
            "z_mode": "true", "median_size": 3}
    p = ExportContext.from_request(data)
    assert p.composite_error is None and p.carve_m is not None
    carved, lo, hi, _ = _prepare_dem_array(p)
    assert lo < 200.0 and hi == 200.0

    baked = ExportContext.from_request({**data, "composite_layers": None,
                                        "dem_values": (np.array(p.dem_values).reshape(
                                            p.height, p.width) + p.carve_m).ravel().tolist(),
                                        "height": p.height, "width": p.width})
    _, lo_baked, _, _ = _prepare_dem_array(baked)
    assert lo_baked == 200.0


# ---------------------------------------------------------------------------
# City-layer size guard
# ---------------------------------------------------------------------------

def test_city_layers_refused_beyond_25_km(monkeypatch):
    from app.server.core import city_data

    def boom(*a, **k):
        raise AssertionError("must not fetch")

    monkeypatch.setattr(city_data, "fetch_osm_data", boom)
    monkeypatch.setattr(city_data, "read_osm_cache", lambda *a, **k: None)
    big = (41.0, 40.7, -74.7, -75.1)                          # ~46 km diagonal
    with pytest.raises(city_data.CityAreaTooLarge, match="25 km"):
        city_data.get_city_layers(*big, ["buildings"])
    city_data.check_city_area(*big, [])                      # terrain only: fine
    city_data.check_city_area(*big, ["roads"], allow_large=True)
    city_data.check_city_area(N, S, E, W, ["buildings"])     # ~10 km: fine


def test_city_task_fails_with_the_guard_message(monkeypatch):
    from app.server.core import city_model_task as task_mod

    class Task:
        failed = None

        def update(self, *a):
            pass

        def fail(self, msg):
            self.failed = msg

    monkeypatch.setattr(task_mod, "get_city_layers",
                        lambda *a, **k: pytest.fail("must not fetch"))
    big = {"north": 41.0, "south": 40.7, "east": -74.7, "west": -75.1}
    task = Task()
    task_mod.run_city_model({"bbox": big, "dem_values": [0.0, 1.0, 2.0, 3.0],
                             "height": 2, "width": 2,
                             "layers": {"buildings": {"enabled": True}}}, task)
    assert task.failed and "allow_large_city" in task.failed


def test_failing_optional_layer_is_skipped_not_fatal(composite_env, monkeypatch):
    """The Region preset leaves the ESA water channel on; without Earth Engine
    it raised and the whole composite (rivers included) was lost. It is now
    skipped with a warning, and the partial composite is cached with the skip
    recorded, so a cache hit repeats the warning."""
    from app.server.routers import composite as composite_mod

    def no_ee(*a, **k):
        raise RuntimeError("No module named 'ee'")

    written = []
    monkeypatch.setattr("app.server.core.cache.write_array_cache",
                        lambda *a, **k: written.append(a))
    register_layer_source("unit_needs_ee", no_ee)
    try:
        warnings = []
        base, carve = composite_mod.compute_composite_dem(
            BBOX, 64, [LAYERS[0],
                       {"source": "unit_needs_ee", "dim": 64, "blend_mode": "rivers",
                        "weight": 5.0},
                       LAYERS[1]],
            split_carve=True, warnings=warnings)
        assert np.allclose(base, 200.0) and carve.min() < 0      # rivers still there
        assert len(warnings) == 1 and "unit_needs_ee" in warnings[0]
        assert len(written) == 1 and written[0][3] == {"skipped": warnings}

        import time as _time
        arrays = {k: v for k, v in written[0][2].items()}
        hit = (arrays, {"skipped": warnings, "_cached_at": _time.time()})
        monkeypatch.setattr("app.server.core.cache.read_array_cache", lambda *a, **k: hit)
        again = []
        composite_mod.compute_composite_dem(BBOX, 64, LAYERS, warnings=again)
        assert again == warnings                       # the hit repeats the skip
        stale = (arrays, {"skipped": warnings, "_cached_at": 0.0})
        monkeypatch.setattr("app.server.core.cache.read_array_cache", lambda *a, **k: stale)
        fresh = []
        composite_mod.compute_composite_dem(BBOX, 64, LAYERS, warnings=fresh)
        assert fresh == []                              # old skip: rebuilt, retried

        # The base layer failing is still an error.
        with pytest.raises(RuntimeError):
            composite_mod.compute_composite_dem(
                BBOX, 64, [{"source": "unit_needs_ee", "dim": 64, "blend_mode": "base"}])
    finally:
        _LAYER_SOURCES.pop("unit_needs_ee", None)


def test_composite_keeps_the_projected_dem_grid(composite_env):
    """The composite's grid is the projected base grid, as /api/terrain/dem
    returns it: a cosine projection narrows the longitude side below dim, and
    the composite used to stretch it back to dim (Apply to DEM widened the
    model by 1/cos(lat) and interpolated every cell)."""
    from app.server.routers.composite import compute_composite_dem
    from geo2stl.projections import project_grid

    bbox = {"north": 60.5, "south": 60.0, "east": 11.0, "west": 10.0}   # 2:1 in degrees
    register_layer_source("unit_tilted", lambda n, s, e, w, dim, o: np.tile(
        np.linspace(0, 100, dim), (dim // 2, 1)))
    try:
        base, carve = compute_composite_dem(
            bbox, 64, [{"source": "unit_tilted", "dim": 64, "blend_mode": "base"}],
            projection="cosine", split_carve=True)
    finally:
        _LAYER_SOURCES.pop("unit_tilted", None)
    expected = project_grid(np.tile(np.linspace(0, 100, 64), (32, 1)), 60.5, 60.0, 11.0, 10.0,
                            "cosine", True, categorical=False)
    assert base.shape == expected.shape and max(base.shape) < 64
    assert carve.shape == base.shape


def test_river_snaps_to_the_valley_floor():
    """HydroRIVERS lines can sit hundreds of metres off the SRTM valley (15"
    source grid): with the DEM given, the channel is re-routed onto the floor."""
    h, w = 100, 100
    rows = np.arange(h)[:, None]
    # A V-shaped valley along row 60 (bottom at 100 m, walls rising 20 m per row).
    dem = 100.0 + 20.0 * np.abs(rows - 60) + np.zeros((1, w))
    lat = N - (N - S) * 55.5 / h                      # drawn 5 rows (~280 m) north of it
    gdf = _river(order=6, lat=lat)
    drawn = wl.rasterize_river_depth(gdf, N, S, E, W, (h, w))
    snapped = wl.rasterize_river_depth(gdf, N, S, E, W, (h, w), dem=dem)
    assert set(np.nonzero(drawn < 0)[0]) <= {54, 55, 56}
    carved_rows = np.nonzero(snapped < 0)[0]
    assert np.median(carved_rows) == 60
    assert (snapped < 0).any(axis=0).all()             # still continuous
    assert snapped.min() == pytest.approx(drawn.min())  # same depth, new place
    off = wl.rasterize_river_depth(gdf, N, S, E, W, (h, w), dem=dem, snap=False)
    assert np.array_equal(off, drawn)


def test_snap_radius_grows_with_order():
    r = wl.snap_radius_m([3, 5, 7, np.nan])
    assert r[0] < r[1] < r[2] == wl.SNAP_RADIUS_MAX_M and r[3] == r[0]


def test_lake_is_flat_after_the_export_median():
    """Levelled against the 3x3 median the export applies before adding the
    carve, so a noisy water surface still prints flat."""
    from scipy import ndimage

    rng = np.random.default_rng(0)
    dem = 300.0 - _bowl() + rng.normal(0, 2.0, (60, 60))
    rel = wl.lake_depth_grid([_poly(0.3, 0.3, 0.7, 0.7, natural="water")],
                             N, S, E, W, dem, depth_m=2.0, min_area_m2=0)
    lake = rel < 0
    printed = ndimage.median_filter(dem, size=3) + rel
    assert lake.sum() > 100 and np.ptp(printed[lake]) < 1e-9
    raw = wl.lake_depth_grid([_poly(0.3, 0.3, 0.7, 0.7, natural="water")],
                             N, S, E, W, dem, depth_m=2.0, min_area_m2=0, smooth=1)
    assert np.ptp((ndimage.median_filter(dem, size=3) + raw)[raw < 0]) > 0.5


def test_ocean_mask_is_edge_connected_sea_only():
    import numpy as np

    from geo2stl.water_layers import ocean_mask

    z = np.full((20, 30), 50.0)
    z[:, :6] = -200.0            # open sea on the west edge
    z[8:12, 14:18] = -30.0       # enclosed basin below sea level (Dead Sea-like)
    z[0, 10] = 0.0               # a sea-level cell on the north edge
    m = ocean_mask(z)
    assert m[:, :6].all()
    assert not m[8:12, 14:18].any()
    assert m[0, 10]
    assert not m[:, 6:].any() or m[0, 10]


def test_composite_carve_is_zero_on_open_sea(monkeypatch):
    """compute_composite_dem drops river carve on the open sea, keeps it on land."""
    import numpy as np

    import app.server.config as config
    from app.server.routers.composite import compute_composite_dem
    from app.server.schemas import MergeLayerSpec

    def base(n, s, e, w, dim, options):
        z = np.full((dim, dim), 100.0)
        z[:, : dim // 4] = -50.0                       # sea along the west edge
        return z

    def river(n, s, e, w, dim, options, base=None):
        g = np.zeros_like(base)
        g[base.shape[0] // 2, :] = -5.0                # crosses the coast into the sea
        return g

    river.terrain_relative = True
    register_layer_source("unit_sea_base", base)
    register_layer_source("unit_sea_river", river)
    monkeypatch.setattr(config, "TEST_MODE", False)
    try:
        specs = [MergeLayerSpec(source="unit_sea_base", dim=60, blend_mode="base", weight=1.0),
                 MergeLayerSpec(source="unit_sea_river", dim=60, blend_mode="add", weight=1.0)]
        comp, carve = compute_composite_dem({"north": 1, "south": 0, "east": 1, "west": 0}, 60,
                                            specs, projection="none", split_carve=True)
    finally:
        _LAYER_SOURCES.pop("unit_sea_base", None)
        _LAYER_SOURCES.pop("unit_sea_river", None)
    row = carve[carve.shape[0] // 2]
    sea_cols = comp[carve.shape[0] // 2] <= 0
    assert sea_cols.any() and (row[sea_cols] == 0).all()
    assert (row[~sea_cols] == -5).all()
