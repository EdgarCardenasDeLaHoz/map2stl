"""Tests for F-SKY10 cross-view non-ML registration (street view ↔ satellite).

Tests Signal 1 (roof colour consistency) implementation and integration.
Signals 2 & 3 (geometric width, vertical edges) deferred to future work.
"""

import unittest
import numpy as np

from city2stl.skyline_cv.cross_view import (
    _median_rgb,
    _street_view_roof_strip,
    score_roof_color_consistency,
    make_cross_view_scorer,
)


class TestMedianRGB(unittest.TestCase):
    """Test _median_rgb helper."""

    def test_median_rgb_valid_patch(self):
        """Median RGB of valid patch returns float tuple."""
        patch = np.array([
            [[100, 150, 200], [110, 160, 210]],
            [[120, 170, 220], [130, 180, 230]],
        ], dtype=np.uint8)
        rgb = _median_rgb(patch)
        self.assertIsNotNone(rgb)
        self.assertEqual(len(rgb), 3)
        # Median of [100, 110, 120, 130] = 115
        self.assertAlmostEqual(rgb[0], 115.0, delta=1.0)

    def test_median_rgb_empty_patch(self):
        """Empty patch returns None."""
        patch = np.array([], dtype=np.uint8).reshape(0, 0, 3)
        rgb = _median_rgb(patch)
        self.assertIsNone(rgb)

    def test_median_rgb_none_input(self):
        """None input returns None."""
        rgb = _median_rgb(None)
        self.assertIsNone(rgb)

    def test_median_rgb_wrong_dims(self):
        """2-D array returns None."""
        patch = np.array([[1, 2], [3, 4]], dtype=np.uint8)
        rgb = _median_rgb(patch)
        self.assertIsNone(rgb)

    def test_median_rgb_single_pixel(self):
        """Single pixel returns median of that pixel's RGB."""
        patch = np.array([[[42, 84, 126]]], dtype=np.uint8)
        rgb = _median_rgb(patch)
        self.assertIsNotNone(rgb)
        self.assertAlmostEqual(rgb[0], 42.0)
        self.assertAlmostEqual(rgb[1], 84.0)
        self.assertAlmostEqual(rgb[2], 126.0)


class TestStreetViewRoofStrip(unittest.TestCase):
    """Test _street_view_roof_strip helper."""

    def test_roof_strip_valid_segment(self):
        """Roof strip extraction returns correct region."""
        sv_image = np.zeros((720, 1280, 3), dtype=np.uint8)
        sv_image[100:115, 200:400] = [200, 150, 100]  # Roof area
        seg = {
            "x_left": 200,
            "x_right": 400,
            "top_y": 100,
        }
        strip = _street_view_roof_strip(sv_image, seg, strip_height_px=14)
        self.assertIsNotNone(strip)
        self.assertEqual(strip.shape[0], 14)  # height = strip_height_px
        self.assertEqual(strip.shape[1], 200)  # width = x_right - x_left

    def test_roof_strip_segment_out_of_bounds(self):
        """Bounds clipping works correctly."""
        sv_image = np.zeros((100, 200, 3), dtype=np.uint8)
        seg = {
            "x_left": 0,
            "x_right": 500,  # Out of bounds
            "top_y": 0,
        }
        strip = _street_view_roof_strip(sv_image, seg)
        self.assertIsNotNone(strip)
        # Should be clipped to image bounds
        self.assertEqual(strip.shape[1], 200)

    def test_roof_strip_none_image(self):
        """None image returns None."""
        seg = {"x_left": 0, "x_right": 100, "top_y": 0}
        strip = _street_view_roof_strip(None, seg)
        self.assertIsNone(strip)

    def test_roof_strip_missing_keys(self):
        """Missing segment keys gracefully returns None."""
        sv_image = np.zeros((100, 200, 3), dtype=np.uint8)
        seg = {}  # Missing keys
        strip = _street_view_roof_strip(sv_image, seg)
        self.assertIsNone(strip)


class TestRoofColorConsistency(unittest.TestCase):
    """Test score_roof_color_consistency (Signal 1)."""

    def test_identical_colors_high_score(self):
        """Identical roof colours → score close to 1.0."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * [100, 150, 200]
        sat_image = np.ones((200, 200, 3), dtype=np.uint8) * [100, 150, 200]
        seg = {"x_left": 10, "x_right": 90, "top_y": 20}
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]

        def sat_project(lon, lat):
            """Mock projection: lat/lon → pixel coords."""
            return (int(lat * 100000), int(lon * 100000))

        score = score_roof_color_consistency(
            sv_image, seg, building_polygon, sat_image, sat_project)
        self.assertGreater(score, 0.95)  # Very close to 1.0

    def test_opposite_colors_low_score(self):
        """Black vs white roof colours → score close to 0.0."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * [0, 0, 0]
        sat_image = np.ones((200, 200, 3), dtype=np.uint8) * [255, 255, 255]
        seg = {"x_left": 10, "x_right": 90, "top_y": 20}
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]

        def sat_project(lon, lat):
            return (int(lat * 100000), int(lon * 100000))

        score = score_roof_color_consistency(
            sv_image, seg, building_polygon, sat_image, sat_project)
        self.assertLess(score, 0.1)  # Very close to 0.0

    def test_neutral_score_when_no_satellite_data(self):
        """Missing satellite data → neutral 0.5."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        sat_image = np.zeros((200, 200, 3), dtype=np.uint8)  # Empty
        seg = {"x_left": 10, "x_right": 90, "top_y": 20}
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]

        def sat_project(lon, lat):
            # Return coordinates outside valid range to trigger missing data
            return (-1000, -1000)

        score = score_roof_color_consistency(
            sv_image, seg, building_polygon, sat_image, sat_project)
        self.assertEqual(score, 0.5)  # Neutral when data missing

    def test_score_in_valid_range(self):
        """All scores are in [0, 1] range."""
        sv_image = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        sat_image = np.random.randint(0, 256, (200, 200, 3), dtype=np.uint8)
        seg = {"x_left": 10, "x_right": 90, "top_y": 20}
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]

        def sat_project(lon, lat):
            return (100, 100)

        score = score_roof_color_consistency(
            sv_image, seg, building_polygon, sat_image, sat_project)
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


class TestMakeCrossViewScorer(unittest.TestCase):
    """Test make_cross_view_scorer factory."""

    def test_scorer_builds_without_error(self):
        """Cross-view scorer closure builds successfully."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        sat_image = np.ones((200, 200, 3), dtype=np.uint8) * 150

        def sat_project(lon, lat):
            return (100, 100)

        scorer = make_cross_view_scorer(sat_image, sat_project, sv_image)
        self.assertIsNotNone(scorer)
        self.assertTrue(callable(scorer))

    def test_scorer_returns_dict_with_combined(self):
        """Scorer returns dict with 'combined' key."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        sat_image = np.ones((200, 200, 3), dtype=np.uint8) * 130

        def sat_project(lon, lat):
            return (100, 100)

        scorer = make_cross_view_scorer(sat_image, sat_project, sv_image)
        seg = {"x_left": 10, "x_right": 90, "top_y": 20}
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]
        result = scorer(seg, building_polygon)

        self.assertIsInstance(result, dict)
        self.assertIn("combined", result)
        self.assertIn("color", result)
        self.assertGreaterEqual(result["combined"], 0.0)
        self.assertLessEqual(result["combined"], 1.0)

    def test_scorer_with_invalid_segment(self):
        """Scorer handles invalid segment gracefully."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        sat_image = np.ones((200, 200, 3), dtype=np.uint8) * 130

        def sat_project(lon, lat):
            return (100, 100)

        scorer = make_cross_view_scorer(sat_image, sat_project, sv_image)
        seg = {}  # Missing keys
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]
        result = scorer(seg, building_polygon)

        # Should return neutral score, not crash
        self.assertEqual(result["combined"], 0.5)


if __name__ == "__main__":
    unittest.main()
