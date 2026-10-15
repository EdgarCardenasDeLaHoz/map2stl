"""Water subtraction runs on every DEM source (user report 2026-10-13: "I don't
see that the water is subtracted in the extrusion", Europa on SRTMGL1).

Covers geo2stl.dem::apply_water_and_depth / water_fraction_for_dem and that
geo2stl.dem::fetch_dem applies them on the OpenTopography and h5_local paths,
not only on ``local``; and that the DEM route shows a note when only the sea
could be found. No network: the DEM and the ESA image are synthetic.
"""

import inspect

import numpy as np
import pytest

from geo2stl import dem

BBOX = (46.0, 45.0, 8.0, 7.0)


#: apply_water_and_depth works in float32 (F-SPEED #13): results match a float64
#: computation to float32 precision.
F32_RTOL = 1e-6


def _relief():
    """Land from 100 m to 1100 m, no sea: float32 values (as every DEM file holds)
    in a float64 array, so untouched cells compare exactly after the float32 pass."""
    return np.tile(np.linspace(100.0, 1100.0, 40, dtype=np.float32), (30, 1)).astype(np.float64)


def _esa_with_lake(shape=(30, 40)):
    """An ESA WorldCover image: tree cover (10) with a class-80 lake block."""
    img = np.full(shape, 10, np.uint8)
    img[10:20, 10:25] = 80
    return img


def test_apply_lowers_water_and_leaves_land(monkeypatch):
    water = np.zeros((30, 40))
    water[5:10, 5:10] = 1.0
    monkeypatch.setattr(dem, "water_fraction_for_dem",
                        lambda *a, **k: (water, None))
    im = _relief()
    out, note = dem.apply_water_and_depth(im, BBOX, water_scale=0.05)
    assert note is None
    relief = im.max() - im.min()
    np.testing.assert_allclose(out[5:10, 5:10], im[5:10, 5:10] - 0.05 * relief,
                               rtol=F32_RTOL)
    land = water == 0
    np.testing.assert_array_equal(out[land], im[land])


def test_depth_scale_and_nan_no_data(monkeypatch):
    monkeypatch.setattr(dem, "water_fraction_for_dem", lambda *a, **k: (None, "x"))
    im = _relief()
    im[0, 0] = -200.0
    im[1, 1] = np.nan                       # OpenTopography no-data
    out, note = dem.apply_water_and_depth(im, BBOX, depth_scale=0.5)
    assert note == "x"
    assert out[0, 0] == -100.0 and np.isnan(out[1, 1])
    assert np.isfinite(out[2:, 2:]).all()


@pytest.mark.parametrize("source", ["SRTMGL1", "h5_local"])
def test_fetch_dem_subtracts_water_on_every_source(monkeypatch, source):
    """The SRTMGL1 and h5_local paths used to skip water subtraction entirely."""
    raw = _relief()
    monkeypatch.setattr(dem, "fetch_layer_data", lambda *a, **k: raw.copy())
    monkeypatch.setattr(dem, "fetch_bbox_image", lambda *a, **k: _esa_with_lake())
    notes: list[str] = []
    out = dem.fetch_dem(BBOX, 40, source, subtract_water=True, water_scale=0.05,
                        notes=notes)
    assert notes == []
    lowered = out < raw - 1e-6
    # The lake's interior is lowered by the full step; the land far from it is not.
    relief = raw.max() - raw.min()
    np.testing.assert_allclose(out[12:18, 12:23], raw[12:18, 12:23] - 0.05 * relief,
                               rtol=F32_RTOL)
    assert not lowered[:5].any() and not lowered[:, 30:].any()
    assert lowered.sum() >= 6 * 11


def test_fetch_dem_without_subtraction_does_not_read_water(monkeypatch):
    raw = _relief()
    monkeypatch.setattr(dem, "fetch_layer_data", lambda *a, **k: raw.copy())

    def boom(*a, **k):
        raise AssertionError("no water source should be read")
    monkeypatch.setattr(dem, "fetch_bbox_image", boom)
    out = dem.fetch_dem(BBOX, 40, "SRTMGL1", subtract_water=False, water_scale=0.05)
    # The legacy "no subtraction" step: land raised uniformly, relief unchanged.
    # float32: the 50 m step is exact to about one ulp of 1100 m (1.2e-4 m).
    np.testing.assert_allclose(out - raw, 0.05 * (raw.max() - raw.min()), atol=2e-4)


def test_without_esa_the_sea_is_lowered_and_noted(monkeypatch):
    """Earth Engine not set up: the sea in the DEM itself is the mask, and the
    response says lakes and rivers were not lowered."""
    def no_ee(*a, **k):
        raise RuntimeError("Earth Engine is not authenticated")
    monkeypatch.setattr(dem, "fetch_bbox_image", no_ee)
    raw = _relief()
    raw[:, :8] = 0.0                       # sea along the west edge
    raw[12:15, 20:23] = 0.0                # an inland basin: not the sea
    monkeypatch.setattr(dem, "fetch_layer_data", lambda *a, **k: raw.copy())
    notes: list[str] = []
    out = dem.fetch_dem(BBOX, 40, "SRTMGL1", subtract_water=True, notes=notes)
    assert notes == [dem.WATER_NOTE_SEA_ONLY]
    assert (out[:, :8] < 0).all()
    assert (out[12:15, 20:23] == 0).all() and (out[:, 8:] == raw[:, 8:]).all()


def test_without_esa_and_no_sea_the_note_says_nothing_was_lowered(monkeypatch):
    def no_ee(*a, **k):
        raise RuntimeError("Earth Engine is not authenticated")
    monkeypatch.setattr(dem, "fetch_bbox_image", no_ee)
    raw = _relief()
    monkeypatch.setattr(dem, "fetch_layer_data", lambda *a, **k: raw.copy())
    notes: list[str] = []
    out = dem.fetch_dem(BBOX, 40, "h5_local", subtract_water=True, notes=notes)
    assert notes == [dem.WATER_NOTE_NONE]
    np.testing.assert_array_equal(out, raw)


def test_esa_scale_keeps_the_earth_engine_floor(monkeypatch):
    """A continental box asks Earth Engine for no finer than it will serve."""
    seen = {}

    def fake(n, s, e, w, scale=None, dataset=None, **k):
        seen["scale"] = scale
        return _esa_with_lake()
    monkeypatch.setattr(dem, "fetch_bbox_image", fake)
    europa = (68.66, 27.84, 47.29, -16.35)
    dem.water_fraction_for_dem(europa, (600, 600), elevation=None)
    assert seen["scale"] >= dem._ee_scale_floor(*europa) > 400


# ---------------------------------------------------------------------------
# float32 throughout (F-SPEED #13), with the river carve's hash unchanged
# ---------------------------------------------------------------------------

def _rough_dem(shape=(50, 70)):
    """float64 values that float32 cannot hold exactly, with sea and no-data."""
    rng = np.random.default_rng(13)
    im = rng.uniform(-300.0, 2500.0, shape)
    im[3, 4] = np.nan
    return im


@pytest.mark.parametrize("subtract", [True, False])
def test_float32_pass_matches_float64_within_float32_tolerance(monkeypatch, subtract):
    water = np.zeros((50, 70))
    water[10:30, 20:40] = np.linspace(0.0, 1.0, 20)
    monkeypatch.setattr(dem, "water_fraction_for_dem", lambda *a, **k: (water, None))
    im = _rough_dem()
    kw = dict(depth_scale=0.37, water_scale=0.05, subtract_water=subtract)
    out32, _ = dem.apply_water_and_depth(im, BBOX, **kw)
    out64, _ = dem.apply_water_and_depth(im, BBOX, dtype=np.float64, **kw)
    assert out32.dtype == np.float32 and out64.dtype == np.float64
    np.testing.assert_allclose(out32, out64, rtol=F32_RTOL, atol=1e-3, equal_nan=True)
    assert np.isnan(out32[3, 4])

    # The float64 pass is the pre-#13 arithmetic, bit for bit.
    ref = np.array(im, dtype=np.float64, copy=True)
    finite = np.isfinite(ref)
    ref[finite & (ref < 0)] = ref[finite & (ref < 0)] * 0.37
    relief = float(np.ptp(ref[finite]))
    if subtract:
        ref = ref - water * relief * 0.05
    else:
        ref[finite & (ref > 0)] = ref[finite & (ref > 0)] + relief * 0.05
    assert out64.tobytes() == ref.tobytes()


def test_make_dem_image_keeps_float64_for_the_local_carve_hash(monkeypatch):
    """The ``local`` layer's raw grid keys the river carve cache by its bytes."""
    im = _rough_dem()
    monkeypatch.setattr(dem, "stitch_tiles_no_rasterio", lambda bbox: im.copy())
    out = dem.make_dem_image(BBOX, dim=None, subtract_water=False, projection="none")
    ref, _ = dem.apply_water_and_depth(im, BBOX, depth_scale=0.5, water_scale=0.1,
                                       subtract_water=False, dtype=np.float64)
    assert out.dtype == np.float64 and out.tobytes() == ref.tobytes()


def test_shared_base_grid_is_float32_with_the_same_carve_hash(monkeypatch):
    """The memo holds float32; the carve hashes it as float64 bytes, which must
    equal the pre-#13 float64 grid's bytes, from memory and from disk alike."""
    import hashlib

    from geo2stl.projections import fit_grid, shared_source_shape

    n, s, e, w = 40.05, 40.0, -75.0, -75.1
    rng = np.random.default_rng(5)
    raw = {}

    def fake(source, *a, **k):
        dim = a[4]
        raw.setdefault(dim, rng.uniform(0.0, 3000.0, (dim, dim)))
        return raw[dim].copy()
    monkeypatch.setattr(dem, "fetch_layer_data", fake)

    def carve_hash(grid):     # as geo2stl.water_layers.river_carve_layers takes it
        return hashlib.sha1(np.ascontiguousarray(grid, dtype=np.float64).tobytes()).hexdigest()

    grid = dem.shared_base_grid("SRTMGL1", n, s, e, w, 64, {})
    (src,) = raw.values()
    shape = shared_source_shape(64, (n, s, e, w))
    old = fit_grid(np.asarray(src, dtype=np.float32), shape).astype(np.float64)
    assert grid.dtype == np.float32 and grid.shape == shape
    assert carve_hash(grid) == hashlib.sha1(old.tobytes()).hexdigest()

    dem._SHARED_BASE_MEMO.clear()                      # read back from the disk cache
    disk = dem.shared_base_grid("SRTMGL1", n, s, e, w, 64, {})
    assert disk is not grid and disk.dtype == np.float32
    assert carve_hash(disk) == carve_hash(grid)


def test_defaults_subtract_water():
    sig = inspect.signature(dem.fetch_local_dem)
    assert sig.parameters["subtract_water"].default is True
    assert inspect.signature(dem.fetch_dem).parameters["subtract_water"].default is True


def test_dem_route_shows_the_water_note_and_does_not_cache(monkeypatch):
    from fastapi.testclient import TestClient

    import app.server.routers.terrain as terrain
    from app.server.server import app

    monkeypatch.setattr(terrain, "TEST_MODE", False)
    monkeypatch.setattr(terrain, "read_array_cache", lambda *a, **k: None)
    writes = []
    monkeypatch.setattr(terrain, "write_array_cache", lambda *a, **k: writes.append(a[0]))

    def fake_fetch(bbox, dim, source, *a, notes=None, **k):
        if notes is not None:
            notes.append(dem.WATER_NOTE_SEA_ONLY)
        return _relief()
    monkeypatch.setattr(terrain, "_fetch_dem", fake_fetch)
    n, s, e, w = BBOX
    body = TestClient(app).get("/api/terrain/dem", params={
        "north": n, "south": s, "east": e, "west": w, "dim": 40,
        "dem_source": "SRTMGL1", "subtract_water": "true"}).json()
    assert dem.WATER_NOTE_SEA_ONLY in body["source_note"]
    assert "dem" not in writes
