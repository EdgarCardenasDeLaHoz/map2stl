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
