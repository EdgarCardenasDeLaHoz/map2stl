"""
DEM source metadata (F-UX batch 2): native resolution per source, real samples
across the box vs the returned grid, and the default source.
"""
import pytest

from geo2stl import dem


class TestDemSampling:
    def test_srtm30_on_a_small_box_is_upsampled(self):
        # 0.05 deg square: 180 one-arc-second samples each way.
        info = dem.dem_sampling("SRTMGL1", (40.05, 40.0, -75.0, -75.05), (600, 600))
        assert info["native_resolution_m"] == 30
        assert info["native_samples"] == [180, 180]
        assert info["grid"] == [600, 600]
        assert info["upsample"] == pytest.approx(3.33, abs=0.01)

    def test_h5_is_three_arcseconds(self):
        info = dem.dem_sampling("h5_local", {"north": 1.0, "south": 0.0, "east": 1.0,
                                             "west": 0.0}, (600, 600))
        assert info["native_resolution_m"] == 90
        assert info["native_samples"] == [1200, 1200]
        assert info["upsample"] == 0.5

    def test_every_listed_source_has_a_resolution(self):
        for source in ("local", "h5_local", *dem.OPENTOPO_DATASETS):
            assert dem.native_resolution_m(source) > 0

    def test_unknown_source(self):
        assert dem.dem_sampling("nope", (1, 0, 1, 0), (10, 10)) is None
        assert dem.native_resolution_m("nope") is None


class TestDefaultSource:
    def test_srtm30_with_a_key(self):
        assert dem.default_dem_source(True, h5_available=False) == "SRTMGL1"

    def test_local_stores_without_a_key(self):
        assert dem.default_dem_source(False, h5_available=True) == "h5_local"
        assert dem.default_dem_source(False, h5_available=False) == "local"


class TestEndpoints:
    BBOX = "north=40.05&south=40.0&east=-75.0&west=-75.05"

    def test_dem_response_reports_source_resolution(self, client):
        body = client.get(f"/api/terrain/dem?{self.BBOX}&dim=300&dem_source=SRTMGL1").json()
        res = body["source_resolution"]
        assert res["source"] == "SRTMGL1"
        assert res["native_samples"] == [180, 180]
        assert res["grid"] == body["dimensions"]

    def test_sources_carry_native_resolution_and_default(self, client, monkeypatch):
        from geo2stl import opentopo
        monkeypatch.setattr(opentopo, "get_api_key", lambda: "k")
        body = client.get("/api/terrain/sources").json()
        assert body["default_source"] == "SRTMGL1"
        by_id = {s["id"]: s for s in body["sources"]}
        assert by_id["SRTMGL1"]["native_resolution_m"] == 30
        assert by_id["h5_local"]["native_resolution_m"] == 90

    @pytest.mark.parametrize("key,expected", [("k", "SRTMGL1"), (None, None)])
    def test_settings_default_follows_the_key(self, client, monkeypatch, key, expected):
        from geo2stl import opentopo
        monkeypatch.setattr(opentopo, "get_api_key", lambda: key)
        source = client.get("/api/settings/default").json()["settings"]["dem"]["dem_source"]
        if expected:
            assert source == expected
        else:
            assert source in ("h5_local", "local")


def test_h5_dem_block_mean_downsampling(tmp_path, monkeypatch):
    """fetch_h5_dem(max_px) averages k x k blocks read band by band, matching the
    mean of the native crop; without max_px it returns the native crop."""
    import h5py
    import numpy as np

    from geo2stl import dem

    monkeypatch.setattr(dem, "_H5_TILE_PX", 600)     # small synthetic 5-degree tiles
    monkeypatch.setattr(dem, "_H5_BAND_ROWS", 37)     # force several bands per tile
    rng = np.random.default_rng(0)
    h5 = tmp_path / "strm_data.h5"
    tiles = {}
    with h5py.File(h5, "w") as fh:
        for key in ("srtm_37_12", "srtm_38_12"):      # lon 0-5 and 5-10, lat 0-5
            tiles[key] = rng.integers(0, 3000, (600, 600)).astype(np.int16)
            fh[key] = tiles[key]
    bb = (4.0, 1.0, 7.5, 2.5)                          # crosses the tile seam at lon 5
    native = dem.fetch_h5_dem(*bb, h5_file=h5)
    mosaic = np.hstack([tiles["srtm_37_12"], tiles["srtm_38_12"]]).astype(float)
    assert np.array_equal(native, mosaic[120:480, 300:900])
    k = max(native.shape) // 50
    small = dem.fetch_h5_dem(*bb, h5_file=h5, max_px=50)
    h, w = native.shape
    ref = np.array([[native[i:i + k, j:j + k].mean() for j in range(0, w, k)]
                    for i in range(0, h, k)])
    assert small.shape == ref.shape and np.allclose(small, ref)


def test_h5_dem_voids_excluded_and_negatives_kept(tmp_path, monkeypatch):
    """PA-15: SRTM voids (-32768) used to be clamped to 0 and averaged in, and real
    below-sea-level ground (Dead Sea, polders) was floored at 0 with them."""
    import h5py
    import numpy as np

    from geo2stl import dem

    monkeypatch.setattr(dem, "_H5_TILE_PX", 600)
    tile = np.full((600, 600), 100, dtype=np.int16)
    tile[:, :300] = -400                                    # west half below sea level
    tile[0:300:2, 300:] = dem._SRTM_VOID                    # every other row void, east half
    tile[300:310, 300:310] = dem._SRTM_VOID                 # an all-void patch
    h5 = tmp_path / "strm_data.h5"
    with h5py.File(h5, "w") as fh:
        fh["srtm_37_12"] = tile                             # lon 0-5, lat 0-5
    bb = (5.0, 0.0, 5.0, 0.0)
    native = dem.fetch_h5_dem(*bb, h5_file=h5)
    assert native.min() == -400                             # not floored at 0
    assert native[0, 400] == 0 and native[1, 400] == 100    # a void cell has no value
    small = dem.fetch_h5_dem(*bb, h5_file=h5, max_px=60)    # 10 x 10 blocks
    assert np.allclose(small[:15, 30:], 100)                # voids left out of the mean
    assert np.allclose(small[:, :30], -400)
    assert small[30, 30] == 0                               # an all-void block stays 0


def _two_tile_store(tmp_path, monkeypatch):
    """A 600 px-per-tile store holding only srtm_37_12 (lon 0-5, lat 0-5); the
    box lon 2.5-7.5 also needs srtm_38_12, which is missing."""
    import h5py
    import numpy as np

    from geo2stl import dem

    monkeypatch.setattr(dem, "_H5_TILE_PX", 600)
    h5 = tmp_path / "strm_data.h5"
    with h5py.File(h5, "w") as fh:
        fh["srtm_37_12"] = np.full((600, 600), 500, dtype=np.int16)
    monkeypatch.setattr(dem, "_H5_SRTM_FILE", h5)
    monkeypatch.setattr(dem, "_FALLBACK_FAILED", {})
    return dem, (4.0, 1.0, 7.5, 2.5)


def test_h5_dem_reports_missing_tiles(tmp_path, monkeypatch):
    """Amazon (2026-10-14): 51 of 63 tiles were not in the store and came back as
    a flat 0 with nothing saying so. fetch_h5_dem now reports them."""
    dem, bb = _two_tile_store(tmp_path, monkeypatch)
    cov = {}
    grid = dem.fetch_h5_dem(*bb, coverage=cov)
    assert cov["tiles_needed"] == ["srtm_37_12", "srtm_38_12"]
    assert cov["tiles_missing"] == ["srtm_38_12"]
    mask = cov["missing_mask"]
    assert mask.shape == grid.shape
    assert not mask[:, :300].any() and mask[:, 300:].all()   # east of lon 5
    assert (grid[mask] == 0).all() and (grid[~mask] == 500).all()


def test_h5_missing_tiles_filled_from_fallback(tmp_path, monkeypatch):
    """Missing tiles are filled from the fallback; present ones keep h5 values,
    and a bathymetric fill is raised to SRTM's sea level of 0."""
    import numpy as np

    from geo2stl import tiles

    dem, bb = _two_tile_store(tmp_path, monkeypatch)
    monkeypatch.setattr(tiles, "get_tile_files", lambda: [])    # no GEBCO folder
    calls = []

    def fake_opentopo(n, s, e, w, demtype, api_key, dim):
        calls.append(demtype)
        out = np.full((dim, dim), 1200.0)
        out[0, :] = -3000.0                                      # sea floor
        return out

    monkeypatch.setattr(dem, "fetch_opentopo_dem", fake_opentopo)
    notes = []
    grid = dem.fetch_layer_data("h5_local", *bb, 50, notes=notes)
    assert calls == ["SRTMGL3"] and notes == []
    w = grid.shape[1]
    assert np.allclose(grid[:, : w // 2 - 1], 500)               # h5 tile kept
    assert np.allclose(grid[1:, w // 2 + 1:], 1200)               # gap filled
    assert grid.min() >= 0


def test_h5_missing_tiles_fill_prefers_local_store(tmp_path, monkeypatch):
    """The local GeoTIFF folder (GEBCO) is used before any OpenTopography call."""
    import numpy as np

    from geo2stl import tiles

    dem, bb = _two_tile_store(tmp_path, monkeypatch)
    monkeypatch.setattr(tiles, "get_tile_files", lambda: ["gebco.tif"])
    monkeypatch.setattr(dem, "fetch_local_dem",
                        lambda n, s, e, w, dim, **kw: np.full((dim, dim), 800.0))

    def no_network(*a, **k):
        raise AssertionError("OpenTopography must not be called")

    monkeypatch.setattr(dem, "fetch_opentopo_dem", no_network)
    grid = dem.fetch_layer_data("h5_local", *bb, 50)
    assert np.isclose(grid.max(), 800)


def test_h5_missing_tiles_without_fallback_say_so(tmp_path, monkeypatch):
    """No fallback answering: the gap stays 0 but a coverage note is returned,
    and a failed OpenTopography fill is not retried on the next request."""
    from geo2stl import tiles

    dem, bb = _two_tile_store(tmp_path, monkeypatch)
    monkeypatch.setattr(tiles, "get_tile_files", lambda: [])
    calls = []

    def rate_limited(*a, **k):
        calls.append(1)
        raise RuntimeError("OpenTopography API error (401): limit")

    monkeypatch.setattr(dem, "fetch_opentopo_dem", rate_limited)
    for _ in range(3):
        notes = []
        dem.fetch_dem(bb, 50, "h5_local", depth_scale=1.0, subtract_water=False,
                      notes=notes)
        assert len(notes) == 1 and "Incomplete coverage" in notes[0]
        assert "1 of 2 tiles" in notes[0]
    assert len(calls) == 1
