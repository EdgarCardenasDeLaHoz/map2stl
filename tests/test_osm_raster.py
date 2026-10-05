# city2stl.osm_raster — OSM building rasters for registration.
# No-network unit tests run always; integration tests require osmnx + a real STL.
import importlib.util

import numpy as np
import pytest

HAS_CV2 = importlib.util.find_spec("cv2") is not None

try:
    import osmnx  # noqa: F401
    HAS_OSMNX = True
except ImportError:
    HAS_OSMNX = False

try:
    import geopandas  # noqa: F401
    HAS_GEOPANDAS = True
except ImportError:
    HAS_GEOPANDAS = False

# ---------------------------------------------------------------------------
# Synthetic fixtures (no files, no network)
# ---------------------------------------------------------------------------


class TestResolveBuildingHeight:

    def test_height_tag_parsed(self):
        from city2stl.osm_raster import _resolve_building_height
        row = {"height": "15.5", "building:levels": None}
        assert _resolve_building_height(row, 10.0, 3.5) == pytest.approx(15.5)

    def test_height_tag_with_unit(self):
        from city2stl.osm_raster import _resolve_building_height
        row = {"height": "15 m", "building:levels": None}
        assert _resolve_building_height(row, 10.0, 3.5) == pytest.approx(15.0)

    @pytest.mark.parametrize("tag, metres", [("40 ft", 12.192), ("12;15", 12.0)])
    def test_height_tag_units_and_lists(self, tag, metres):
        # Stripping non-digits read these as 40 m and 1215 m (audit 2026-10-05).
        from city2stl.osm_raster import _resolve_building_height
        row = {"height": tag, "building:levels": None}
        assert _resolve_building_height(row, 10.0, 3.5) == pytest.approx(metres)

    def test_levels_fallback(self):
        from city2stl.osm_raster import _resolve_building_height
        row = {"height": None, "building:levels": "4"}
        assert _resolve_building_height(row, 10.0, 3.5) == pytest.approx(14.0)

    def test_default_fallback(self):
        from city2stl.osm_raster import _resolve_building_height
        row = {"height": None, "building:levels": None}
        assert _resolve_building_height(row, 10.0, 3.5) == pytest.approx(10.0)

    def test_nan_height_falls_through(self):
        from city2stl.osm_raster import _resolve_building_height
        row = {"height": float("nan"), "building:levels": "3"}
        # NaN height → falls through to building:levels
        assert _resolve_building_height(row, 10.0, 3.5) == pytest.approx(10.5)


class TestMakeResult:

    def test_return_format_matches_mesh_to_heightmap(self):
        from city2stl.osm_raster import _make_result
        hm = np.full((32, 32), np.nan)
        hm[5:10, 5:10] = 15.0
        result = _make_result(hm, N=40.06, S=39.86, E=-74.95, W=-75.28, resolution=32)
        assert "heightmap" in result
        assert "bounds" in result
        assert "resolution" in result
        assert "cell_size" in result
        assert "projection" in result
        assert result["heightmap"].dtype == np.float64
        assert result["resolution"] == (32, 32)
        assert result["projection"] == "max"

    def test_bounds_x_is_west_east(self):
        from city2stl.osm_raster import _make_result
        hm = np.zeros((16, 16))
        result = _make_result(hm, N=40.0, S=39.0, E=-75.0, W=-76.0, resolution=16)
        W_out, E_out = result["bounds"]["x"]
        assert W_out == pytest.approx(-76.0)
        assert E_out == pytest.approx(-75.0)

    def test_bounds_y_is_south_north(self):
        from city2stl.osm_raster import _make_result
        hm = np.zeros((16, 16))
        result = _make_result(hm, N=40.0, S=39.0, E=-75.0, W=-76.0, resolution=16)
        S_out, N_out = result["bounds"]["y"]
        assert S_out == pytest.approx(39.0)
        assert N_out == pytest.approx(40.0)


@pytest.mark.integration
@pytest.mark.skipif(not HAS_OSMNX or not HAS_GEOPANDAS, reason="osmnx/geopandas not installed")
class TestGetPhiladelphiaHeightmap:

    def test_returns_correct_format(self):
        from city2stl.osm_raster import get_philadelphia_heightmap
        result = get_philadelphia_heightmap(resolution=64)
        assert "heightmap" in result
        assert result["heightmap"].dtype == np.float64
        assert result["heightmap"].shape == (64, 64)
        assert result["projection"] == "max"

    def test_bounds_are_latitude_longitude(self):
        from city2stl.osm_raster import get_philadelphia_heightmap
        result = get_philadelphia_heightmap(resolution=64)
        W, E = result["bounds"]["x"]
        S, N = result["bounds"]["y"]
        # Philadelphia is in the western hemisphere, ~40°N
        assert -76 < W < -74
        assert -76 < E < -74
        assert 39 < S < 41
        assert 39 < N < 41
        assert W < E
        assert S < N

    def test_has_some_buildings(self):
        from city2stl.osm_raster import get_philadelphia_heightmap
        result = get_philadelphia_heightmap(resolution=64)
        valid = result["heightmap"][~np.isnan(result["heightmap"])]
        assert len(valid) > 0
        assert valid.max() > 5.0  # at least some buildings > 5 m



def test_make_result_carries_cell_size_m():
    from city2stl.osm_raster import _make_result
    hm = np.zeros((100, 100))
    # 0.01 deg x 0.01 deg at the equator: 1113.2 m / 100 px on both axes
    result = _make_result(hm, N=0.005, S=-0.005, E=0.01, W=0.0, resolution=100)
    assert result["cell_size_m"] == pytest.approx(11.132, rel=1e-4)


class TestOSMReference:

    def test_bbox_is_fetched_as_given_and_unanchored(self):
        from city2stl.registration import OSMReference
        ref = OSMReference((40.0, 39.9, -75.1, -75.2))
        assert ref.resolve_target(60.0, 100.0, 1.5) == ((40.0, 39.9, -75.1, -75.2), False)
        assert ref.candidate_targets(60.0, 100.0, 1.5) == []
        assert ref.name.startswith("bbox ")

    def test_named_city_with_anchor_gets_tight_bbox(self):
        from city2stl.osm_raster import tight_bbox_from_extent
        from city2stl.registration import OSMReference
        ref = OSMReference("Somewhere", center=(40.0, -75.0), scale_m_per_unit=10.0)
        target, anchored = ref.resolve_target(60.0, 100.0, 1.5)
        assert anchored
        assert target == pytest.approx(tight_bbox_from_extent(40.0, -75.0, 1000.0, margin=1.5))
        assert ref.m_per_unit(60.0) == 10.0

    def test_candidate_ring(self):
        from city2stl.registration import OSMReference
        ref = OSMReference("Somewhere", center=(40.0, -75.0), scale_m_per_unit=10.0)
        cands = ref.candidate_targets(60.0, 100.0, 1.5)
        assert len(cands) == 33
        assert cands[0][0] == (40.0, -75.0)


def _square_heightmap(resolution):
    hm = np.full((resolution, resolution), np.nan)
    q = resolution // 4
    hm[q:3 * q, q:3 * q] = 50.0
    return hm


def test_register_city_stl_wrapper_offline(monkeypatch):
    """The city-name entry point builds an OSMReference and runs numpy2stl offline."""
    from city2stl import osm_raster
    from city2stl.registration import register_city_stl

    def fake_mesh(stl_file, resolution=512, **kw):
        return {"heightmap": _square_heightmap(resolution).astype(np.float32),
                "bounds": {"x": (0.0, 1.0), "y": (0.0, 1.0), "z": (0.0, 60.0)}}

    def fake_osm(target, resolution=512, **kw):
        N, S, E, W = target
        return osm_raster._make_result(_square_heightmap(resolution), N, S, E, W, resolution)

    monkeypatch.setattr("numpy2stl.stl2numpy.heightmap.mesh_to_heightmap", fake_mesh)
    monkeypatch.setattr(osm_raster, "get_osm_building_heightmap", fake_osm)
    monkeypatch.setattr(osm_raster, "get_osm_semantic_masks", lambda t, resolution=512: {})

    report = register_city_stl("synthetic.stl", (40.0, 39.9, -75.1, -75.2), resolution=512,
                               out_dir=False, detect_resolution_factor=1)
    assert report.region_name == "bbox (40.0, 39.9, -75.1, -75.2)"
    assert report.osm_bbox == (40.0, 39.9, -75.1, -75.2)
    assert report.cell_size_m == pytest.approx(
        osm_raster.grid_cell_size_m(40.0, 39.9, -75.1, -75.2, (512, 512)))
    assert report.registration.transform.shape == (2, 3)
