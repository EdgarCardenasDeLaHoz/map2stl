"""Tests for core/height/predict.py — CNN building height prediction.

Tests mock all heavy ML dependencies (torch, transformers, timm) so they run
without GPU or model downloads.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.server.core.height import HeightResult


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

BARCELONA_BBOX = (41.42, 41.35, 2.19, 2.12)  # (N, S, E, W)


def _make_rgb(h=64, w=64):
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (h, w, 3), dtype=np.uint8)


def _make_known_heights(h=64, w=64, nan_frac=0.3):
    rng = np.random.default_rng(1)
    arr = rng.uniform(5.0, 40.0, (h, w)).astype(np.float32)
    mask = rng.random((h, w)) < nan_frac
    arr[mask] = np.nan
    return arr


# ──────────────────────────────────────────────────────────────────────────────
# _calibrate_depth
# ──────────────────────────────────────────────────────────────────────────────

class TestCalibrateDepth:
    """Linear calibration of relative depth → metres."""

    def _run(self, depth_rel, known_heights):
        from app.server.core.height.predict import _calibrate_depth
        return _calibrate_depth(depth_rel, known_heights, BARCELONA_BBOX)

    def test_returns_float32_array(self):
        depth_rel = np.linspace(0, 1, 64 * 64, dtype=np.float32).reshape(64, 64)
        known = _make_known_heights()
        result, slope, intercept = self._run(depth_rel, known)
        assert result.dtype == np.float32

    def test_output_shape_matches_input(self):
        depth_rel = np.ones((32, 48), dtype=np.float32) * 0.5
        known = np.full((32, 48), 15.0, dtype=np.float32)
        result, _, _ = self._run(depth_rel, known)
        assert result.shape == (32, 48)

    def test_all_nan_known_uses_heuristic(self):
        """With no calibration data, falls back to depth_rel * 30."""
        depth_rel = np.full((16, 16), 0.5, dtype=np.float32)
        known = np.full((16, 16), np.nan, dtype=np.float32)
        result, slope, intercept = self._run(depth_rel, known)
        # Heuristic: 0.5 * 30 = 15
        assert abs(float(result.mean()) - 15.0) < 1.0

    def test_perfect_known_heights_gives_near_zero_residual(self):
        """When every pixel is known, the calibration should be very tight."""
        depth_rel = np.linspace(0, 1, 64 * 64, dtype=np.float32).reshape(64, 64)
        # True relationship: height = 20 * depth + 5
        known = (depth_rel * 20.0 + 5.0).astype(np.float32)
        result, slope, intercept = self._run(depth_rel, known)
        assert abs(slope - 20.0) < 0.1
        assert abs(intercept - 5.0) < 0.1

    def test_output_clipped_to_physical_range(self):
        """Output must be in [0, 500] m."""
        depth_rel = np.ones((16, 16), dtype=np.float32)  # all 1.0
        # Extreme slope would push values above 500 without clamping
        known = np.full((16, 16), 600.0, dtype=np.float32)
        result, _, _ = self._run(depth_rel, known)
        assert float(result.max()) <= 500.0
        assert float(result.min()) >= 0.0

    def test_partial_nan_uses_valid_pixels_only(self):
        known = _make_known_heights(nan_frac=0.7)  # 70% NaN
        depth_rel = np.random.default_rng(2).random((64, 64)).astype(np.float32)
        result, slope, intercept = self._run(depth_rel, known)
        # Result should be numeric everywhere (no NaN from calibration)
        assert not np.any(np.isnan(result))

    def test_slope_sign_positive_for_positive_correlation(self):
        depth_rel = np.linspace(0, 1, 100, dtype=np.float32).reshape(10, 10)
        known = np.linspace(3.0, 50.0, 100, dtype=np.float32).reshape(10, 10)
        _, slope, _ = self._run(depth_rel, known)
        assert slope > 0


# ──────────────────────────────────────────────────────────────────────────────
# _depth_anything_inference (mocked)
# ──────────────────────────────────────────────────────────────────────────────

class TestDepthAnythingInference:
    """Mock HuggingFace pipeline; only test shape and dtype contracts."""

    def _mock_pipeline(self, h=64, w=64):
        """Return a mock pipeline that outputs a constant depth map."""
        mock_result = {"predicted_depth": np.full((h, w), 0.4, dtype=np.float32)}
        pipe = MagicMock(return_value=mock_result)
        return pipe

    def test_output_shape_matches_input(self):
        from app.server.core.height import predict as _pm

        rgb = _make_rgb(64, 64)
        with patch.object(_pm, "_load_da2", return_value=self._mock_pipeline(64, 64)):
            result = _pm._depth_anything_inference(rgb, device="cpu")
        assert result.shape == (64, 64)

    def test_output_float32(self):
        from app.server.core.height import predict as _pm

        rgb = _make_rgb(32, 32)
        with patch.object(_pm, "_load_da2", return_value=self._mock_pipeline(32, 32)):
            result = _pm._depth_anything_inference(rgb, device="cpu")
        assert result.dtype == np.float32

    def test_output_normalised_0_1(self):
        from app.server.core.height import predict as _pm

        rgb = _make_rgb(32, 32)
        with patch.object(_pm, "_load_da2", return_value=self._mock_pipeline(32, 32)):
            result = _pm._depth_anything_inference(rgb, device="cpu")
        assert float(result.min()) >= 0.0
        assert float(result.max()) <= 1.0

    def test_resize_applied_when_model_output_differs(self):
        """Pipeline returns (8, 8) depth; should be resized to (32, 32) input."""
        from app.server.core.height import predict as _pm

        rgb = _make_rgb(32, 32)
        small_depth = {"predicted_depth": np.full((8, 8), 0.5, dtype=np.float32)}
        pipe = MagicMock(return_value=small_depth)
        with patch.object(_pm, "_load_da2", return_value=pipe):
            result = _pm._depth_anything_inference(rgb, device="cpu")
        assert result.shape == (32, 32)


# ──────────────────────────────────────────────────────────────────────────────
# predict() — pretrained path (end-to-end with mocks)
# ──────────────────────────────────────────────────────────────────────────────

class TestPredictPretrained:
    """Full predict() call with DA2 mocked out."""

    def _mock_da2(self, h, w):
        mock_result = {"predicted_depth": np.linspace(0, 1, h * w, dtype=np.float32).reshape(h, w)}
        return MagicMock(return_value=mock_result)

    def test_returns_height_result(self):
        from app.server.core.height import predict as _pm

        h, w = 32, 32
        rgb = _make_rgb(h, w)
        known = _make_known_heights(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, known, BARCELONA_BBOX, model="pretrained")
        assert isinstance(result, HeightResult)

    def test_raster_shape_matches_input(self):
        from app.server.core.height import predict as _pm

        h, w = 48, 64
        rgb = _make_rgb(h, w)
        known = _make_known_heights(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, known, BARCELONA_BBOX, model="pretrained")
        assert result.raster.shape == (h, w)
        assert result.confidence.shape == (h, w)

    def test_source_name_is_depth_anything_v2(self):
        from app.server.core.height import predict as _pm

        h, w = 16, 16
        rgb = _make_rgb(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, None, BARCELONA_BBOX, model="pretrained")
        assert result.source_name == "depth_anything_v2"

    def test_no_known_heights_still_produces_result(self):
        from app.server.core.height import predict as _pm

        h, w = 16, 16
        rgb = _make_rgb(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, None, BARCELONA_BBOX, model="pretrained")
        assert not np.any(np.isnan(result.raster))

    def test_confidence_non_negative(self):
        from app.server.core.height import predict as _pm

        h, w = 16, 16
        rgb = _make_rgb(h, w)
        known = _make_known_heights(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, known, BARCELONA_BBOX, model="pretrained")
        assert float(result.confidence.min()) >= 0.0
        assert float(result.confidence.max()) <= 1.0

    def test_resolution_m_positive(self):
        from app.server.core.height import predict as _pm

        h, w = 16, 16
        rgb = _make_rgb(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, None, BARCELONA_BBOX, model="pretrained")
        assert result.resolution_m > 0.0

    def test_raster_no_nan_after_calibration(self):
        from app.server.core.height import predict as _pm

        h, w = 32, 32
        rgb = _make_rgb(h, w)
        known = _make_known_heights(h, w)
        with patch.object(_pm, "_load_da2", return_value=self._mock_da2(h, w)):
            result = _pm.predict(rgb, known, BARCELONA_BBOX, model="pretrained")
        assert not np.any(np.isnan(result.raster))


# ──────────────────────────────────────────────────────────────────────────────
# predict() — unet path (mocked checkpoint)
# ──────────────────────────────────────────────────────────────────────────────

class TestPredictUNet:
    """U-Net predict path — checkpoint is a minimal fake .pt file."""

    def _make_fake_checkpoint(self, tmp_path: Path) -> Path:
        """Write a real minimal U-Net checkpoint using a tiny mock model."""
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            pytest.skip("torch not installed")

        class _TinyModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv = nn.Conv2d(3, 1, 1)
                nn.init.constant_(self.conv.weight, 0.01)
                nn.init.constant_(self.conv.bias, 5.0)

            def forward(self, x):
                return torch.relu(self.conv(x))

        model = _TinyModel()
        ckpt = tmp_path / "test_unet.pt"
        torch.save({"model_state_dict": model.state_dict()}, ckpt)
        return ckpt

    def test_unet_returns_height_result(self, tmp_path):
        from app.server.core.height import predict as _pm

        ckpt = self._make_fake_checkpoint(tmp_path)
        h, w = 32, 32
        rgb = _make_rgb(h, w)

        # Patch _build_unet to return the tiny model, not EfficientNet
        try:
            import torch
            import torch.nn as nn
        except ImportError:
            pytest.skip("torch not installed")

        class _TinyModel(nn.Module):
            def __init__(self):
                super().__init__()
                self.conv = nn.Conv2d(3, 1, 1)

            def forward(self, x):
                return torch.relu(self.conv(x))

        with patch.object(_pm, "_build_unet", return_value=_TinyModel()):
            result = _pm.predict(rgb, None, BARCELONA_BBOX, model="unet", checkpoint=ckpt)

        assert isinstance(result, HeightResult)
        assert result.raster.shape == (h, w)
        assert result.source_name == "unet"

    def test_missing_checkpoint_raises(self, tmp_path):
        from app.server.core.height.predict import predict

        rgb = _make_rgb(16, 16)
        missing = tmp_path / "nonexistent.pt"
        with pytest.raises(FileNotFoundError, match="checkpoint not found"):
            predict(rgb, None, BARCELONA_BBOX, model="unet", checkpoint=missing)

    def test_unknown_model_raises(self):
        from app.server.core.height.predict import predict

        rgb = _make_rgb(16, 16)
        with pytest.raises(ValueError, match="Unknown model"):
            predict(rgb, None, BARCELONA_BBOX, model="magic")


# ──────────────────────────────────────────────────────────────────────────────
# Missing-dependency error messages
# ──────────────────────────────────────────────────────────────────────────────

class TestMissingDependencies:
    def test_load_da2_raises_helpful_error_without_transformers(self):
        import sys
        from importlib import import_module
        from app.server.core.height import predict as _pm

        # Remove _da2_model singleton so the function tries to import
        _pm._da2_model = None

        with patch.dict(sys.modules, {"transformers": None}):
            with pytest.raises(ImportError, match="transformers"):
                _pm._load_da2()

    def test_build_unet_raises_without_torch(self):
        import sys
        from app.server.core.height import predict as _pm

        with patch.dict(sys.modules, {"torch": None, "timm": None,
                                       "segmentation_models_pytorch": None}):
            with pytest.raises(ImportError, match="torch"):
                _pm._build_unet()
