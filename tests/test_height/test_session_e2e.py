"""End-to-end tests for the building-height pipeline via TerrainSession.

All network calls are mocked.  Tests exercise the full path from
session.fetch_building_heights() through provider selection, fetch,
merge, and storage on self.building_heights.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pytest

_STRM2STL_ROOT = Path(__file__).parent.parent.parent
for _p in (str(_STRM2STL_ROOT.parent), str(_STRM2STL_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from city2stl.skyline.height import HeightResult


# ── helper to build a fake session ──────────────────────────────

def _make_session(bbox=None, dem=None, monkeypatch=None, tmp_path=None):
    """Build a TerrainSession with just enough state to call
    fetch_building_heights(), without starting a server.
    """
    # Redirect cache directory to a temp dir to avoid polluting real cache
    if monkeypatch is not None and tmp_path is not None:
        import app.server.core.cache as cache_mod
        monkeypatch.setattr(cache_mod, "CACHE_ROOT", tmp_path / "cache")

    from app.session.terrain_session import TerrainSession
    s = TerrainSession(port=9999)

    if bbox is None:
        bbox = {"north": 41.40, "south": 41.38,
                "east": 2.18, "west": 2.16}
    s.bbox = bbox

    if dem is not None:
        s.dem = dem

    return s


def _synthetic_height_result(dim, value, confidence, name, res_m=90.0):
    """Build a HeightResult with a uniform raster."""
    h, w = dim
    raster = np.full((h, w), value, dtype=np.float32)
    conf = np.full((h, w), confidence, dtype=np.float32)
    return HeightResult(raster=raster, confidence=conf,
                        source_name=name, resolution_m=res_m)


# ── basic session plumbing ──────────────────────────────────────

class TestSessionPlumbing:
    """Test that fetch_building_heights validates state correctly."""

    def test_requires_bbox(self, monkeypatch, tmp_path):
        """Raises when bbox is not set."""
        s = _make_session(bbox={}, monkeypatch=monkeypatch, tmp_path=tmp_path)
        s.bbox = {}
        with pytest.raises(RuntimeError, match="bbox"):
            s.fetch_building_heights(providers=["wsf3d"])

    def test_unknown_provider_skipped(self, monkeypatch, tmp_path, capsys):
        """Unknown provider name prints a warning, doesn't crash."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        # Mock wsf3d to avoid network
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=_synthetic_height_result((10, 10), 15.0, 0.5, "wsf3d"),
        ):
            s.fetch_building_heights(providers=["bogus_provider", "wsf3d"])

        captured = capsys.readouterr()
        assert "Unknown height provider" in captured.out
        assert s.building_heights is not None

    def test_returns_self_for_chaining(self, monkeypatch, tmp_path):
        """fetch_building_heights returns self."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=_synthetic_height_result((800, 800), 10.0, 0.5, "wsf3d"),
        ):
            result = s.fetch_building_heights(providers=["wsf3d"])
        assert result is s

    def test_no_results_sets_none(self, monkeypatch, tmp_path, capsys):
        """When no provider returns data, building_heights is None."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        # google3d with no API key → skipped; no other provider
        with patch(
            "city2stl.skyline.height.providers.google_3d._get_api_key",
            return_value=None,
        ):
            s.fetch_building_heights(providers=["google3d"])
        assert s.building_heights is None
        captured = capsys.readouterr()
        assert "no coverage" in captured.out or "No building height" in captured.out


# ── single-provider pipelines ───────────────────────────────────

class TestSingleProvider:
    """Each provider individually via fetch_building_heights."""

    def test_wsf3d_only(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 12.0, 0.5, "wsf3d")
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["wsf3d"])
        assert s.building_heights is not None
        assert s.building_heights.raster.shape == (800, 800)
        assert np.nanmean(s.building_heights.raster) == pytest.approx(12.0, abs=0.1)

    def test_google3d_only(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 25.0, 0.9, "google3d", 1.0)
        with patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.covers",
            return_value=True,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["google3d"])
        assert s.building_heights is not None
        assert np.nanmean(s.building_heights.raster) == pytest.approx(25.0, abs=0.1)

    def test_copernicus_only(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 18.0, 0.7, "copernicus", 10.0)
        with patch(
            "city2stl.skyline.height.providers.copernicus.CopernicusProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["copernicus"])
        assert s.building_heights is not None
        assert np.nanmean(s.building_heights.raster) == pytest.approx(18.0, abs=0.1)

    def test_ndsm_only(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 20.0, 0.8, "ndsm", 30.0)
        with patch(
            "city2stl.skyline.height.providers.ndsm.NDSMProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["ndsm"])
        assert s.building_heights is not None
        assert np.nanmean(s.building_heights.raster) == pytest.approx(20.0, abs=0.1)

    def test_lidar_3dep_only(self, monkeypatch, tmp_path):
        """US bbox → lidar_3dep covers → returns result."""
        us_bbox = {"north": 40.0, "south": 39.9, "east": -75.0, "west": -75.2}
        s = _make_session(bbox=us_bbox, monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 30.0, 0.95, "lidar_3dep", 1.0)
        with patch(
            "city2stl.skyline.height.providers.lidar_3dep.LiDAR3DEPProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["lidar_3dep"])
        assert s.building_heights is not None
        assert np.nanmean(s.building_heights.raster) == pytest.approx(30.0, abs=0.1)

    def test_lidar_3dep_skipped_for_europe(self, monkeypatch, tmp_path, capsys):
        """European bbox → lidar_3dep doesn't cover → skipped."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        s.fetch_building_heights(providers=["lidar_3dep"])
        assert s.building_heights is None
        captured = capsys.readouterr()
        assert "no coverage" in captured.out


# ── multi-provider merge ────────────────────────────────────────

class TestMultiProviderMerge:
    """Test that fetch_building_heights correctly merges multiple sources."""

    def test_wsf3d_and_google3d_merge(self, monkeypatch, tmp_path):
        """Google3D (conf=0.9) overrides WSF3D (conf=0.5) where both have data."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)

        wsf_raster = np.full((10, 10), 5.0, dtype=np.float32)
        wsf = HeightResult(wsf_raster, np.full((10, 10), 0.5, dtype=np.float32),
                           "wsf3d", 90.0)

        g3d_raster = np.full((10, 10), 25.0, dtype=np.float32)
        g3d = HeightResult(g3d_raster, np.full((10, 10), 0.9, dtype=np.float32),
                           "google3d", 1.0)

        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=wsf,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.covers",
            return_value=True,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.fetch_heights",
            return_value=g3d,
        ):
            s.fetch_building_heights(providers=["wsf3d", "google3d"])

        assert s.building_heights is not None
        # Google3D should win — higher confidence
        assert np.nanmean(s.building_heights.raster) == pytest.approx(25.0, abs=0.5)
        assert s.building_heights.source_name == "merged"

    def test_gap_filling(self, monkeypatch, tmp_path):
        """Lower-confidence provider fills NaN gaps from higher-confidence provider."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        s.settings["dem"]["dim"] = 10

        # WSF3D: full coverage at 5m
        wsf_raster = np.full((10, 10), 5.0, dtype=np.float32)
        wsf = HeightResult(wsf_raster, np.full((10, 10), 0.5, dtype=np.float32),
                           "wsf3d", 90.0)

        # Google3D: only top-left quadrant has data, rest is NaN
        g3d_raster = np.full((10, 10), np.nan, dtype=np.float32)
        g3d_raster[:5, :5] = 30.0
        g3d_conf = np.where(np.isnan(g3d_raster), 0.0, 0.9).astype(np.float32)
        g3d = HeightResult(g3d_raster, g3d_conf, "google3d", 1.0)

        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=wsf,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.covers",
            return_value=True,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.fetch_heights",
            return_value=g3d,
        ):
            s.fetch_building_heights(providers=["wsf3d", "google3d"])

        merged = s.building_heights.raster
        # Top-left should be Google3D value (30.0)
        assert np.nanmean(merged[:5, :5]) == pytest.approx(30.0, abs=0.5)
        # Bottom-right should be WSF3D value (5.0) — gap filled
        assert np.nanmean(merged[5:, 5:]) == pytest.approx(5.0, abs=0.5)

    def test_three_provider_priority_cascade(self, monkeypatch, tmp_path):
        """WSF3D (0.5) < nDSM (0.8) < Google3D (0.9) — highest confidence wins."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        s.settings["dem"]["dim"] = 10

        wsf = _synthetic_height_result((10, 10), 5.0, 0.5, "wsf3d", 90.0)
        ndsm = _synthetic_height_result((10, 10), 15.0, 0.8, "ndsm", 30.0)

        g3d_raster = np.full((10, 10), np.nan, dtype=np.float32)
        g3d_raster[:3, :3] = 35.0  # partial coverage
        g3d_conf = np.where(np.isnan(g3d_raster), 0.0, 0.9).astype(np.float32)
        g3d = HeightResult(g3d_raster, g3d_conf, "google3d", 1.0)

        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=wsf,
        ), patch(
            "city2stl.skyline.height.providers.ndsm.NDSMProvider.fetch_heights",
            return_value=ndsm,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.covers",
            return_value=True,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.fetch_heights",
            return_value=g3d,
        ):
            s.fetch_building_heights(providers=["wsf3d", "ndsm", "google3d"])

        merged = s.building_heights.raster
        # Top-left: Google3D wins (35.0)
        assert np.nanmean(merged[:3, :3]) == pytest.approx(35.0, abs=0.5)
        # Rest: nDSM wins over WSF3D (15.0)
        assert np.nanmean(merged[5:, 5:]) == pytest.approx(15.0, abs=0.5)
        assert s.building_heights.resolution_m == 1.0  # best of the three

    def test_all_five_providers(self, monkeypatch, tmp_path):
        """All 5 providers queried; only those that cover the bbox contribute."""
        us_bbox = {"north": 40.0, "south": 39.9, "east": -75.0, "west": -75.2}
        s = _make_session(bbox=us_bbox, monkeypatch=monkeypatch, tmp_path=tmp_path)

        wsf = _synthetic_height_result((10, 10), 5.0, 0.5, "wsf3d", 90.0)
        ndsm = _synthetic_height_result((10, 10), 10.0, 0.8, "ndsm", 30.0)
        lidar = _synthetic_height_result((10, 10), 20.0, 0.95, "lidar_3dep", 1.0)

        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=wsf,
        ), patch(
            "city2stl.skyline.height.providers.ndsm.NDSMProvider.fetch_heights",
            return_value=ndsm,
        ), patch(
            "city2stl.skyline.height.providers.copernicus.CopernicusProvider.covers",
            return_value=False,  # US bbox → not in Europe
        ), patch(
            "city2stl.skyline.height.providers.lidar_3dep.LiDAR3DEPProvider.fetch_heights",
            return_value=lidar,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.covers",
            return_value=False,  # no API key
        ):
            s.fetch_building_heights(
                providers=["wsf3d", "ndsm", "copernicus", "lidar_3dep", "google3d"])

        # LiDAR (0.95) wins over nDSM (0.8) wins over WSF3D (0.5)
        merged = s.building_heights.raster
        assert np.nanmean(merged) == pytest.approx(20.0, abs=0.5)
        assert s.building_heights.resolution_m == 1.0


# ── DEM interaction ─────────────────────────────────────────────

class TestDEMInteraction:
    """Test that DEM data is correctly passed to google3d for subtraction."""

    def test_dim_from_dem(self, monkeypatch, tmp_path):
        """When DEM is available, target dim matches DEM dimensions."""
        dem = {
            "values": [100.0] * (50 * 50),
            "dimensions": [50, 50],
            "min_elevation": 80.0,
            "max_elevation": 120.0,
            "mean_elevation": 100.0,
        }
        s = _make_session(dem=dem, monkeypatch=monkeypatch, tmp_path=tmp_path)

        wsf = _synthetic_height_result((50, 50), 8.0, 0.5, "wsf3d")
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=wsf,
        ) as mock_fetch:
            s.fetch_building_heights(providers=["wsf3d"])

        # Provider was called with dim = (50, 50)
        call_args = mock_fetch.call_args
        assert call_args[0][1] == (50, 50)

    def test_dim_default_without_dem(self, monkeypatch, tmp_path):
        """Without DEM, target dim defaults to settings['dem']['dim']."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        s.settings["dem"]["dim"] = 300

        wsf = _synthetic_height_result((300, 300), 8.0, 0.5, "wsf3d")
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=wsf,
        ) as mock_fetch:
            s.fetch_building_heights(providers=["wsf3d"])

        call_args = mock_fetch.call_args
        assert call_args[0][1] == (300, 300)

    def test_google3d_receives_dem_array(self, monkeypatch, tmp_path):
        """Google3D provider receives the DEM array for DSM subtraction."""
        dem = {
            "values": [100.0] * (10 * 10),
            "dimensions": [10, 10],
            "min_elevation": 100.0,
            "max_elevation": 100.0,
            "mean_elevation": 100.0,
        }
        s = _make_session(dem=dem, monkeypatch=monkeypatch, tmp_path=tmp_path)

        fake = _synthetic_height_result((10, 10), 50.0, 0.9, "google3d", 1.0)

        with patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.covers",
            return_value=True,
        ), patch(
            "city2stl.skyline.height.providers.google_3d.Google3DProvider.fetch_heights",
            return_value=fake,
        ) as mock_fetch:
            s.fetch_building_heights(providers=["google3d"])

        # Check that dem= kwarg was passed
        call_kwargs = mock_fetch.call_args
        assert "dem" in call_kwargs.kwargs
        dem_arr = call_kwargs.kwargs["dem"]
        assert dem_arr.shape == (10, 10)
        assert np.allclose(dem_arr, 100.0)


# ── provider error resilience ───────────────────────────────────

class TestProviderErrors:
    """Test that one provider failing doesn't break the pipeline."""

    def test_provider_exception_caught(self, monkeypatch, tmp_path, capsys):
        """If one provider throws, others still run."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)

        wsf = _synthetic_height_result((800, 800), 10.0, 0.5, "wsf3d")

        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            side_effect=RuntimeError("network down"),
        ), patch(
            "city2stl.skyline.height.providers.ndsm.NDSMProvider.fetch_heights",
            return_value=_synthetic_height_result((800, 800), 15.0, 0.8, "ndsm", 30.0),
        ):
            s.fetch_building_heights(providers=["wsf3d", "ndsm"])

        # ndsm should still succeed
        assert s.building_heights is not None
        assert np.nanmean(s.building_heights.raster) == pytest.approx(15.0, abs=0.5)

        captured = capsys.readouterr()
        assert "network down" in captured.out

    def test_all_providers_fail(self, monkeypatch, tmp_path, capsys):
        """If all providers fail, building_heights is None."""
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)

        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            side_effect=RuntimeError("timeout"),
        ):
            s.fetch_building_heights(providers=["wsf3d"])

        assert s.building_heights is None


# ── HeightResult integrity ──────────────────────────────────────

class TestResultIntegrity:
    """Verify the HeightResult stored on the session is well-formed."""

    def test_result_dtype(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 10.0, 0.5, "wsf3d")
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["wsf3d"])

        bh = s.building_heights
        assert bh.raster.dtype == np.float32
        assert bh.confidence.dtype == np.float32

    def test_confidence_range(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((10, 10), 10.0, 0.5, "wsf3d")
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["wsf3d"])

        bh = s.building_heights
        assert np.all(bh.confidence >= 0.0)
        assert np.all(bh.confidence <= 1.0)

    def test_shapes_match(self, monkeypatch, tmp_path):
        s = _make_session(monkeypatch=monkeypatch, tmp_path=tmp_path)
        fake = _synthetic_height_result((800, 800), 10.0, 0.5, "wsf3d")
        with patch(
            "city2stl.skyline.height.providers.wsf3d.WSF3DProvider.fetch_heights",
            return_value=fake,
        ):
            s.fetch_building_heights(providers=["wsf3d"])

        bh = s.building_heights
        assert bh.raster.shape == bh.confidence.shape
