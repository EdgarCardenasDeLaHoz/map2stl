"""Unit tests for city2stl.skyline.depth_estimation (F-SKY12 Phase A).

These tests cover the three pure functions and the disagreement helper.
Tests do NOT load Depth Anything V2 — that's an integration concern. We
mock the relative-depth output and exercise the calibration + height
geometry directly.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from city2stl.skyline.depth_estimation import (
    DEPTH_DISAGREEMENT_THRESHOLD,
    calibrate_pano_depth,
    compare_heights,
    depth_height_from_segment,
)


class TestCalibratePanoDepth:
    """Linear fit from relative DA2 depth to metric distance."""

    def test_single_anchor_scale_only(self):
        # Synthetic depth includes a relative-depth value of 1.0 (closest)
        depth_rel = np.linspace(1.0, 0.1, 100, dtype=np.float32).reshape(10, 10)
        # Anchor: pixel at (5, 5), known to be 50 m away
        anchors = [(5, 5, 50.0)]
        depth_m = calibrate_pano_depth(depth_rel, anchors)

        # The calibrated metric depth at the anchor pixel should match the known distance
        assert depth_m[5, 5] == pytest.approx(50.0, rel=1e-3)
        # Scale-only fit (a = 0): pixel with relative depth == 1.0 maps to 0 m
        assert depth_m[0, 0] == pytest.approx(0.0, abs=1e-3)

    def test_two_anchors_linear_fit(self):
        depth_rel = np.linspace(0.9, 0.1, 100, dtype=np.float32).reshape(10, 10)
        # Place anchors at two known points with known distances
        anchors = [(0, 0, 10.0), (9, 9, 90.0)]
        depth_m = calibrate_pano_depth(depth_rel, anchors)

        assert depth_m[0, 0] == pytest.approx(10.0, abs=0.5)
        assert depth_m[9, 9] == pytest.approx(90.0, abs=0.5)

    def test_no_anchors_raises(self):
        depth_rel = np.zeros((10, 10), dtype=np.float32)
        with pytest.raises(ValueError, match="at least one anchor"):
            calibrate_pano_depth(depth_rel, [])

    def test_out_of_bounds_anchors_skipped(self):
        depth_rel = np.full((10, 10), 0.5, dtype=np.float32)
        # First anchor is out of bounds; second is valid
        anchors = [(99, 99, 50.0), (5, 5, 20.0)]
        depth_m = calibrate_pano_depth(depth_rel, anchors)
        # Only the valid anchor was used → scale-only fit → 20 m at the anchor
        assert depth_m[5, 5] == pytest.approx(20.0, rel=1e-3)

    def test_all_anchors_oob_raises(self):
        depth_rel = np.zeros((10, 10), dtype=np.float32)
        with pytest.raises(ValueError, match="fell outside"):
            calibrate_pano_depth(depth_rel, [(99, 99, 50.0)])

    def test_clips_negative_depths(self):
        # Construct a case where extrapolation would yield negative depth
        depth_rel = np.array([[0.5, 1.5]], dtype=np.float32)  # 1.5 is out-of-range but allowed
        anchors = [(0, 0, 10.0)]
        depth_m = calibrate_pano_depth(depth_rel, anchors)
        assert (depth_m >= 0.0).all()


class TestDepthHeightFromSegment:
    """Pinhole geometry: depth + segment-top pixel -> absolute height."""

    def test_horizon_pixel_returns_zero(self):
        depth_m = np.full((100, 100), 50.0, dtype=np.float32)
        # Pixel exactly on the principal point (cy = 50) -> no pitch -> 0 height
        h = depth_height_from_segment(depth_m, (50, 50), fy=100.0, cy=50.0)
        assert h == 0.0

    def test_pixel_above_horizon_gives_positive_height(self):
        depth_m = np.full((100, 100), 50.0, dtype=np.float32)
        # Pixel above the horizon (y < cy) -> positive pitch -> positive height
        h = depth_height_from_segment(depth_m, (50, 30), fy=100.0, cy=50.0)
        # Expected: 2.5 (camera height) + 50 * tan(atan(20/100)) = 2.5 + 50*0.2 = 12.5
        assert h == pytest.approx(12.5, rel=1e-3)

    def test_pixel_below_horizon_returns_zero(self):
        depth_m = np.full((100, 100), 50.0, dtype=np.float32)
        # Pixel below horizon (y > cy) is the ground, not a building top
        h = depth_height_from_segment(depth_m, (50, 70), fy=100.0, cy=50.0)
        assert h == 0.0

    def test_oob_pixel_returns_zero(self):
        depth_m = np.full((100, 100), 50.0, dtype=np.float32)
        assert depth_height_from_segment(depth_m, (999, 30), fy=100.0, cy=50.0) == 0.0
        assert depth_height_from_segment(depth_m, (50, -5), fy=100.0, cy=50.0) == 0.0

    def test_nan_or_zero_depth_returns_zero(self):
        depth_m = np.full((100, 100), np.nan, dtype=np.float32)
        assert depth_height_from_segment(depth_m, (50, 30), fy=100.0, cy=50.0) == 0.0
        depth_m = np.zeros((100, 100), dtype=np.float32)
        assert depth_height_from_segment(depth_m, (50, 30), fy=100.0, cy=50.0) == 0.0

    def test_taller_pitch_taller_building(self):
        depth_m = np.full((100, 100), 100.0, dtype=np.float32)
        h_short = depth_height_from_segment(depth_m, (50, 45), fy=100.0, cy=50.0)
        h_tall = depth_height_from_segment(depth_m, (50, 10), fy=100.0, cy=50.0)
        assert h_tall > h_short > 2.5  # both above camera height

    def test_custom_camera_height(self):
        depth_m = np.full((100, 100), 50.0, dtype=np.float32)
        h = depth_height_from_segment(
            depth_m, (50, 30), fy=100.0, cy=50.0, camera_height_m=0.0
        )
        # No camera offset -> pure trig
        assert h == pytest.approx(50.0 * math.tan(math.atan(20.0 / 100.0)), rel=1e-3)


class TestCompareHeights:
    """Disagreement flag for cross-verification."""

    def test_close_estimates_agree(self):
        # 20 m vs 22 m -> 9% diff -> no disagreement at default 40% threshold
        assert compare_heights(20.0, 22.0) is False

    def test_far_estimates_disagree(self):
        # 10 m vs 100 m -> 90% diff -> disagreement
        assert compare_heights(10.0, 100.0) is True

    def test_symmetric(self):
        # Order shouldn't matter
        assert compare_heights(20.0, 100.0) == compare_heights(100.0, 20.0)

    def test_zero_or_negative_returns_false(self):
        # No valid second signal -> can't disagree
        assert compare_heights(0.0, 50.0) is False
        assert compare_heights(50.0, 0.0) is False
        assert compare_heights(-5.0, 50.0) is False

    def test_threshold_boundary(self):
        # h_geom=10, h_depth=15 -> rel_diff = 5/15 ≈ 0.333 -> below default 0.40
        assert compare_heights(10.0, 15.0) is False
        # Tighter threshold flips the result
        assert compare_heights(10.0, 15.0, threshold=0.20) is True

    def test_default_threshold_is_documented(self):
        # The constant is part of the public API (PDF report quotes it).
        assert DEPTH_DISAGREEMENT_THRESHOLD == pytest.approx(0.40)
