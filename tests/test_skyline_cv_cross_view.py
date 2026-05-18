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
    score_geometric_width_consistency,
    score_vertical_edge_consistency,
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


class TestGeometricWidthConsistency(unittest.TestCase):
    """Test score_geometric_width_consistency (Signal 2)."""

    def test_moderate_width_height_ratio(self):
        """Segment with balanced width-to-height returns reasonable score."""
        sv_image = np.ones((200, 100, 3), dtype=np.uint8) * 128
        seg = {"x_left": 25, "x_right": 75, "top_y": 50}  # width:height ≈ 50:150
        score = score_geometric_width_consistency(sv_image, seg, [])
        self.assertGreater(score, 0.1)  # Should be better than very skewed ratios
        self.assertLess(score, 0.8)  # But not perfect due to aspect ratio

    def test_very_narrow_segment(self):
        """Very narrow segment (needle-like) returns low score."""
        sv_image = np.ones((200, 200, 3), dtype=np.uint8) * 128
        seg = {"x_left": 99, "x_right": 101, "top_y": 0}  # width:height ≈ 2:200
        score = score_geometric_width_consistency(sv_image, seg, [])
        self.assertLess(score, 0.5)  # Suspicious aspect ratio

    def test_very_wide_segment(self):
        """Very wide segment (flat) returns low score."""
        sv_image = np.ones((50, 200, 3), dtype=np.uint8) * 128
        seg = {"x_left": 0, "x_right": 200, "top_y": 0}  # width:height ≈ 200:50
        score = score_geometric_width_consistency(sv_image, seg, [])
        self.assertLess(score, 0.5)  # Suspicious aspect ratio

    def test_invalid_segment_returns_neutral(self):
        """Invalid segment returns neutral 0.5."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        seg = {}  # Missing keys
        score = score_geometric_width_consistency(sv_image, seg, [])
        self.assertEqual(score, 0.5)

    def test_score_in_valid_range(self):
        """All scores are in [0, 1] range."""
        sv_image = np.random.randint(0, 256, (200, 200, 3), dtype=np.uint8)
        seg = {"x_left": 50, "x_right": 150, "top_y": 20}
        score = score_geometric_width_consistency(sv_image, seg, [])
        self.assertGreaterEqual(score, 0.0)
        self.assertLessEqual(score, 1.0)


class TestVerticalEdgeConsistency(unittest.TestCase):
    """Test score_vertical_edge_consistency (Signal 3)."""

    def test_structured_edges_high_score(self):
        """Image with strong vertical edges returns higher score."""
        # Create image with vertical edge pattern.
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 200
        sv_image[20:80, 45:55] = 50  # Vertical stripe = vertical edge
        seg = {"x_left": 30, "x_right": 70, "top_y": 10}
        score = score_vertical_edge_consistency(sv_image, seg)
        self.assertGreater(score, 0.3)  # Should detect vertical structure

    def test_uniform_region_low_score(self):
        """Uniform image (no edges) returns low score."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        seg = {"x_left": 30, "x_right": 70, "top_y": 10}
        score = score_vertical_edge_consistency(sv_image, seg)
        self.assertLess(score, 0.3)  # No edges in uniform region

    def test_invalid_segment_returns_neutral(self):
        """Invalid segment returns neutral 0.5."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        seg = {}  # Missing keys
        score = score_vertical_edge_consistency(sv_image, seg)
        self.assertEqual(score, 0.5)

    def test_very_small_segment_returns_neutral(self):
        """Segment too small to analyze returns neutral."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        seg = {"x_left": 49, "x_right": 51, "top_y": 99}  # 2×1 pixels
        score = score_vertical_edge_consistency(sv_image, seg)
        self.assertEqual(score, 0.5)

    def test_score_in_valid_range(self):
        """All scores are in [0, 1] range."""
        sv_image = np.random.randint(0, 256, (100, 100, 3), dtype=np.uint8)
        seg = {"x_left": 20, "x_right": 80, "top_y": 10}
        score = score_vertical_edge_consistency(sv_image, seg)
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

    def test_scorer_returns_dict_with_all_signals(self):
        """Scorer returns dict with all signal keys."""
        sv_image = np.ones((100, 100, 3), dtype=np.uint8) * 128
        sat_image = np.ones((200, 200, 3), dtype=np.uint8) * 130

        def sat_project(lon, lat):
            return (100, 100)

        scorer = make_cross_view_scorer(sat_image, sat_project, sv_image)
        seg = {"x_left": 10, "x_right": 90, "top_y": 20}
        building_polygon = [(0, 0), (0.001, 0), (0.001, 0.001), (0, 0.001)]
        result = scorer(seg, building_polygon)

        self.assertIsInstance(result, dict)
        self.assertIn("color", result)
        self.assertIn("width", result)
        self.assertIn("edges", result)
        self.assertIn("combined", result)
        self.assertGreaterEqual(result["color"], 0.0)
        self.assertLessEqual(result["color"], 1.0)
        self.assertGreaterEqual(result["width"], 0.0)
        self.assertLessEqual(result["width"], 1.0)
        self.assertGreaterEqual(result["edges"], 0.0)
        self.assertLessEqual(result["edges"], 1.0)
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

        # All signals should return neutral, so combined should too.
        self.assertEqual(result["color"], 0.5)
        self.assertEqual(result["width"], 0.5)
        self.assertEqual(result["edges"], 0.5)
        self.assertEqual(result["combined"], 0.5)


if __name__ == "__main__":
    unittest.main()
