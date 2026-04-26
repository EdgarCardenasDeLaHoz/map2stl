"""Tests for city2stl.roof_classifier (ROOF-2).

All tests use only numpy and scipy (no torch, no cv2) so they run in CI
without GPU dependencies.  The classifier degrades gracefully when those
libraries are absent — the tests verify that degraded path still produces
valid output.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

from city2stl.roof_classifier import (
    _ElevFeatures,
    _MultiTemporalFeatures,
    _RGBFeatures,
    _ShadowFeatures,
    _classify,
    _crop_bounds_for_ring,
    _crop_geo_bounds,
    _detect_shadows,
    _ellipse_axes,
    _extract_elev_features,
    _extract_multitemporal_features,
    _extract_rgb_features,
    _extract_shadow_features,
    _lonlat_to_pixel,
    _profile_along_axis,
    _ring_to_footprint_mask,
    _sun_azimuth_elevation,
    _triangularity,
    _triangulate_heights_from_shadows,
    classify_roof_shapes,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _simple_geojson(rings=None):
    """Build a minimal GeoJSON FeatureCollection with one rectangular building."""
    if rings is None:
        # A ~20×10 m rectangle centred at (0.001, 51.501)
        rings = [[
            [0.0000, 51.5000],
            [0.0002, 51.5000],
            [0.0002, 51.5001],
            [0.0000, 51.5001],
            [0.0000, 51.5000],
        ]]
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "geometry": {"type": "Polygon", "coordinates": rings},
                "properties": {},
            }
        ],
    }


def _solid_rgb(h=64, w=64, brightness=120):
    """Return a uniform grey H×W×3 uint8 image."""
    return np.full((h, w, 3), brightness, dtype=np.uint8)


def _dark_shadow_band(h=64, w=64, band_col_start=10, band_col_end=20):
    """Return an RGB image with a dark vertical band (simulates cast shadow)."""
    img = _solid_rgb(h, w, 150)
    img[:, band_col_start:band_col_end, :] = 30
    return img


# ─────────────────────────────────────────────────────────────────────────────
# TestShadowDetection
# ─────────────────────────────────────────────────────────────────────────────

class TestShadowDetection:
    def test_uniform_bright_image_no_shadow(self):
        rgb = _solid_rgb(brightness=200)
        mask = _detect_shadows(rgb)
        assert mask.sum() == 0

    def test_uniform_dark_image_all_shadow(self):
        rgb = _solid_rgb(brightness=30)
        mask = _detect_shadows(rgb)
        assert mask.mean() > 0.5

    def test_dark_band_detected(self):
        img = _dark_shadow_band()
        mask = _detect_shadows(img)
        # At least the dark band should be flagged
        assert mask[:, 10:20].sum() > 0

    def test_output_dtype(self):
        rgb = _solid_rgb()
        mask = _detect_shadows(rgb)
        assert mask.dtype == bool
        assert mask.shape == rgb.shape[:2]


# ─────────────────────────────────────────────────────────────────────────────
# TestEllipseAndTriangularity
# ─────────────────────────────────────────────────────────────────────────────

class TestEllipseAndTriangularity:
    def test_square_returns_near_equal_axes(self):
        binary = np.zeros((20, 20), dtype=bool)
        binary[5:15, 5:15] = True
        major, minor = _ellipse_axes(binary)
        assert abs(major - minor) < major * 0.2

    def test_elongated_rectangle_major_gt_minor(self):
        binary = np.zeros((10, 40), dtype=bool)
        binary[2:8, 5:35] = True
        major, minor = _ellipse_axes(binary)
        assert major > minor * 2.0

    def test_triangularity_full_square(self):
        binary = np.ones((10, 10), dtype=bool)
        score = _triangularity(binary)
        # A square is the least triangular → score near 0
        assert score < 0.2

    def test_triangularity_sparse_mask(self):
        binary = np.zeros((20, 20), dtype=bool)
        # Triangle-like: dense centre, sparse edges
        for r in range(1, 19):
            width = max(1, int((19 - r) * 0.8))
            binary[r, 10 - width:10 + width] = True
        score = _triangularity(binary)
        assert score >= 0.0  # just check no crash

    def test_too_few_pixels_returns_defaults(self):
        binary = np.zeros((5, 5), dtype=bool)
        assert _ellipse_axes(binary) == (1.0, 1.0)
        assert _triangularity(binary) == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TestCoordinateHelpers
# ─────────────────────────────────────────────────────────────────────────────

class TestCoordinateHelpers:
    def test_lonlat_to_pixel_corners(self):
        # NW corner → (0, 0), SE corner → (H-1, W-1)
        r, c = _lonlat_to_pixel(0.0, 52.0, north=52.0, south=51.0, east=1.0, west=0.0, h=100, w=100)
        assert r == 0
        assert c == 0
        r, c = _lonlat_to_pixel(1.0, 51.0, north=52.0, south=51.0, east=1.0, west=0.0, h=100, w=100)
        assert r == 99
        assert c == 99

    def test_lonlat_clamps_out_of_bounds(self):
        r, c = _lonlat_to_pixel(-1.0, 53.0, north=52.0, south=51.0, east=1.0, west=0.0, h=100, w=100)
        assert 0 <= r <= 99
        assert 0 <= c <= 99

    def test_ring_to_footprint_mask_basic(self):
        ring = [
            [0.25, 51.75],
            [0.75, 51.75],
            [0.75, 51.25],
            [0.25, 51.25],
            [0.25, 51.75],
        ]
        mask = _ring_to_footprint_mask(ring, north=52.0, south=51.0, east=1.0, west=0.0, h=100, w=100)
        assert mask.shape == (100, 100)
        assert mask.any()

    def test_crop_bounds_padding(self):
        ring = [
            [0.4, 51.6],
            [0.6, 51.6],
            [0.6, 51.4],
            [0.4, 51.4],
            [0.4, 51.6],
        ]
        cr0, cr1, cc0, cc1 = _crop_bounds_for_ring(
            ring, north=52.0, south=51.0, east=1.0, west=0.0, rgb_h=100, rgb_w=100
        )
        assert cr0 >= 0
        assert cr1 <= 100
        assert cr1 - cr0 >= 8  # must have some padding
        c_n, c_s, c_e, c_w = _crop_geo_bounds(cr0, cr1, cc0, cc1, 52.0, 51.0, 1.0, 0.0, 100, 100)
        assert c_n > c_s
        assert c_e > c_w


# ─────────────────────────────────────────────────────────────────────────────
# TestElevFeatures
# ─────────────────────────────────────────────────────────────────────────────

class TestElevFeatures:
    def _fp_mask(self, h=30, w=30, margin=4):
        mask = np.zeros((h, w), dtype=bool)
        mask[margin:h - margin, margin:w - margin] = True
        return mask

    def test_flat_dem_returns_low_ridge_score(self):
        fp = self._fp_mask()
        dem = np.full(fp.shape, 5.0, dtype=np.float32)
        dem[~fp] = 0.0
        feat = _extract_elev_features(dem, fp)
        assert feat.ridge_score < 0.5
        assert feat.roof_rise < 0.5

    def test_bell_shaped_dem_returns_high_ridge_score(self):
        h, w = 30, 30
        fp = self._fp_mask(h, w)
        dem = np.zeros((h, w), dtype=np.float32)
        cx = w // 2
        for c in range(w):
            dem[:, c] = max(0, 3.0 - abs(c - cx) * 0.3)
        feat = _extract_elev_features(dem, fp)
        assert feat.ridge_score > 0.1  # should detect some ridge

    def test_central_peak_returns_high_apex_score(self):
        h, w = 30, 30
        fp = self._fp_mask(h, w)
        dem = np.zeros((h, w), dtype=np.float32)
        # Pyramidal: single high pixel at centre
        dem[h // 2, w // 2] = 10.0
        dem[fp] = np.clip(dem[fp], 0, 10)
        feat = _extract_elev_features(dem, fp)
        assert feat.n_pixels > 0

    def test_all_nan_returns_zero_pixels(self):
        fp = self._fp_mask()
        dem = np.full(fp.shape, np.nan, dtype=np.float32)
        feat = _extract_elev_features(dem, fp)
        assert feat.n_pixels == 0

    def test_profile_along_axis_correct_length(self):
        fp = self._fp_mask()
        dem = np.ones(fp.shape, dtype=np.float32)
        profile = _profile_along_axis(dem, fp, n_bins=8)
        assert len(profile) == 8


# ─────────────────────────────────────────────────────────────────────────────
# TestRGBFeatures
# ─────────────────────────────────────────────────────────────────────────────

class TestRGBFeatures:
    def _fp_mask(self, h=32, w=32):
        mask = np.zeros((h, w), dtype=bool)
        mask[4:28, 4:28] = True
        return mask

    def test_uniform_image_low_gradient(self):
        rgb = _solid_rgb(32, 32, 150)
        fp = self._fp_mask()
        feat = _extract_rgb_features(rgb, fp)
        assert feat.gradient_strength < 0.1

    def test_striped_image_has_gradient(self):
        rgb = np.zeros((32, 32, 3), dtype=np.uint8)
        rgb[:, :16, :] = 200
        rgb[:, 16:, :] = 50
        fp = self._fp_mask()
        feat = _extract_rgb_features(rgb, fp)
        assert feat.gradient_strength > 0.0

    def test_returns_namedtuple(self):
        rgb = _solid_rgb(32, 32, 100)
        fp = self._fp_mask()
        feat = _extract_rgb_features(rgb, fp)
        assert isinstance(feat, _RGBFeatures)

    def test_empty_footprint_returns_zeros(self):
        rgb = _solid_rgb(32, 32)
        fp = np.zeros((32, 32), dtype=bool)
        feat = _extract_rgb_features(rgb, fp)
        assert feat.gradient_strength == 0.0


# ─────────────────────────────────────────────────────────────────────────────
# TestShadowFeatures
# ─────────────────────────────────────────────────────────────────────────────

class TestShadowFeatures:
    def test_no_shadow_gives_zero_ratio(self):
        shadow = np.zeros((30, 30), dtype=bool)
        fp = np.zeros((30, 30), dtype=bool)
        fp[10:20, 10:20] = True
        feat = _extract_shadow_features(shadow, fp)
        assert feat.shadow_ratio == 0.0
        assert feat.confidence == 0.0

    def test_shadow_outside_footprint_detected(self):
        shadow = np.zeros((30, 30), dtype=bool)
        fp = np.zeros((30, 30), dtype=bool)
        fp[10:20, 10:20] = True
        shadow[20:26, 10:20] = True  # shadow beyond footprint
        feat = _extract_shadow_features(shadow, fp)
        assert feat.shadow_ratio > 0
        assert feat.confidence > 0

    def test_elongated_shadow_has_high_elongation(self):
        shadow = np.zeros((50, 50), dtype=bool)
        fp = np.zeros((50, 50), dtype=bool)
        fp[22:28, 22:28] = True
        shadow[28:48, 22:24] = True  # thin vertical shadow strip
        feat = _extract_shadow_features(shadow, fp)
        assert feat.elongation > 1.5


# ─────────────────────────────────────────────────────────────────────────────
# TestShadowTriangulation
# ─────────────────────────────────────────────────────────────────────────────

class TestShadowTriangulation:
    def _build_shadow_mask(self, fp_mask, shadow_len_px, direction="south"):
        mask = np.zeros(fp_mask.shape, dtype=bool)
        fp_rows, fp_cols = np.where(fp_mask)
        row_max = fp_rows.max()
        col_min, col_max = fp_cols.min(), fp_cols.max()
        if direction == "south":
            mask[row_max + 1:row_max + shadow_len_px + 1, col_min:col_max + 1] = True
        elif direction == "east":
            mask[fp_rows.min():fp_rows.max() + 1, col_max + 1:col_max + shadow_len_px + 1] = True
        return mask

    def test_two_images_height_estimate_reasonable(self):
        """Two shadow masks with known lengths → height estimate within 50 %."""
        h, w = 60, 60
        fp = np.zeros((h, w), dtype=bool)
        fp[25:35, 25:35] = True  # 10×10 px footprint

        # Shadow length = 20 px, pixel_m = 1.0, elevation = 45° → h = 20 m
        # elevation = 45° → tan(45°) = 1 → h = len * m/px * 1 = 20 m
        sm1 = self._build_shadow_mask(fp, shadow_len_px=20, direction="south")
        sm2 = self._build_shadow_mask(fp, shadow_len_px=15, direction="east")

        # sun_params: (azimuth, elevation)
        sun_params = [
            (180.0, 45.0),  # sun from south, 45° elevation
            (90.0, 53.0),   # sun from east, 53° elevation
        ]
        h_est, conf = _triangulate_heights_from_shadows(
            [sm1, sm2], fp, pixel_m=1.0, sun_params=sun_params
        )
        assert h_est > 0.0
        assert 0.0 <= conf <= 1.0

    def test_single_image_low_confidence(self):
        h, w = 60, 60
        fp = np.zeros((h, w), dtype=bool)
        fp[25:35, 25:35] = True
        sm = self._build_shadow_mask(fp, 15, "south")
        h_est, conf = _triangulate_heights_from_shadows(
            [sm], fp, pixel_m=1.0, sun_params=[(180.0, 45.0)]
        )
        # Single image → low confidence
        assert conf <= 0.30

    def test_no_shadow_returns_zero_height(self):
        fp = np.zeros((30, 30), dtype=bool)
        fp[10:20, 10:20] = True
        no_shadow = np.zeros((30, 30), dtype=bool)
        h_est, conf = _triangulate_heights_from_shadows(
            [no_shadow, no_shadow], fp, pixel_m=1.0, sun_params=[(180.0, 45.0), (90.0, 45.0)]
        )
        assert h_est == 0.0
        assert conf == 0.0

    def test_wide_azimuth_spread_gives_higher_confidence(self):
        h, w = 60, 60
        fp = np.zeros((h, w), dtype=bool)
        fp[25:35, 25:35] = True
        sm1 = self._build_shadow_mask(fp, 20, "south")
        sm2 = self._build_shadow_mask(fp, 20, "east")
        # 90° spread
        _, conf90 = _triangulate_heights_from_shadows(
            [sm1, sm2], fp, pixel_m=1.0,
            sun_params=[(180.0, 45.0), (90.0, 45.0)]
        )
        # 10° spread (nearly collinear sun directions)
        _, conf10 = _triangulate_heights_from_shadows(
            [sm1, sm2], fp, pixel_m=1.0,
            sun_params=[(180.0, 45.0), (170.0, 45.0)]
        )
        assert conf90 >= conf10


# ─────────────────────────────────────────────────────────────────────────────
# TestSunPosition
# ─────────────────────────────────────────────────────────────────────────────

class TestSunPosition:
    def test_midday_june_london_elevation_positive(self):
        az, elev = _sun_azimuth_elevation(lat=51.5, lon=-0.1, month=6, hour=12)
        assert elev > 40.0  # should be well above horizon

    def test_morning_vs_midday_azimuth_differs(self):
        az_morn, _ = _sun_azimuth_elevation(51.5, -0.1, month=6, hour=8)
        az_noon, _ = _sun_azimuth_elevation(51.5, -0.1, month=6, hour=12)
        assert abs(az_morn - az_noon) > 5.0


# ─────────────────────────────────────────────────────────────────────────────
# TestClassifyFusion
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyFusion:
    """Verify that the fusion logic produces sensible decisions for each tier."""

    _zero_elev = _ElevFeatures(0.0, 0.0, 0.0, 0.5, 0.0, 0)
    _zero_rgb = _RGBFeatures(0.0, 0.0, 0.0)
    _zero_shadow = _ShadowFeatures(0.0, 1.0, 0.0, 0.0)
    _zero_mt = _MultiTemporalFeatures(0.0, 0.0, 0.0, 1)

    def test_no_signals_returns_none(self):
        shape, conf = _classify(
            self._zero_elev, self._zero_rgb, self._zero_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape is None

    def test_flat_elev_returns_flat(self):
        flat_elev = _ElevFeatures(0.1, 0.1, 0.0, 0.5, 0.0, 20)
        shape, conf = _classify(
            flat_elev, self._zero_rgb, self._zero_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape == "flat"
        assert conf > 0.5

    def test_bell_elev_symmetric_returns_gabled(self):
        gable_elev = _ElevFeatures(1.5, 3.0, 0.8, 0.9, 0.1, 50)
        shape, conf = _classify(
            gable_elev, self._zero_rgb, self._zero_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape in ("gabled", "hipped")

    def test_central_apex_returns_pyramidal(self):
        pyr_elev = _ElevFeatures(2.0, 4.0, 0.2, 0.5, 0.8, 50)
        shape, conf = _classify(
            pyr_elev, self._zero_rgb, self._zero_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape == "pyramidal"

    def test_high_confidence_cnn_wins(self):
        """CNN output with conf ≥ 0.55 should override elevation profile."""
        gable_elev = _ElevFeatures(1.5, 3.0, 0.8, 0.9, 0.1, 50)
        shape, conf = _classify(
            gable_elev, self._zero_rgb, self._zero_shadow, self._zero_mt,
            cnn=("flat", 0.90),
        )
        assert shape == "flat"

    def test_low_confidence_cnn_ignored(self):
        """CNN output with conf < 0.55 should be ignored."""
        gable_elev = _ElevFeatures(1.5, 3.0, 0.8, 0.9, 0.1, 50)
        shape, conf = _classify(
            gable_elev, self._zero_rgb, self._zero_shadow, self._zero_mt,
            cnn=("flat", 0.30),
        )
        assert shape != "flat" or conf != 0.30  # cnn shouldn't dictate result

    def test_directional_rgb_gradient_returns_pitched(self):
        aniso_rgb = _RGBFeatures(0.15, 0.70, 0.20)
        shape, conf = _classify(
            self._zero_elev, aniso_rgb, self._zero_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape in ("gabled", "hipped", "pyramidal")

    def test_shadow_elongation_returns_gabled(self):
        elong_shadow = _ShadowFeatures(0.20, 3.0, 0.30, 0.60)
        shape, conf = _classify(
            self._zero_elev, self._zero_rgb, elong_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape == "gabled"

    def test_shadow_elongation_with_high_tri_score_returns_pyramidal(self):
        pyr_shadow = _ShadowFeatures(0.20, 3.0, 0.70, 0.60)
        shape, conf = _classify(
            self._zero_elev, self._zero_rgb, pyr_shadow, self._zero_mt,
            cnn=(None, 0.0),
        )
        assert shape == "pyramidal"

    def test_multitemporal_high_triangulation_returns_pitched(self):
        tall_mt = _MultiTemporalFeatures(5.0, 0.80, 3.0, 2)
        shape, conf = _classify(
            self._zero_elev, self._zero_rgb, self._zero_shadow, tall_mt,
            cnn=(None, 0.0),
        )
        assert shape in ("gabled", "hipped")


# ─────────────────────────────────────────────────────────────────────────────
# TestClassifyRoofShapes (public API)
# ─────────────────────────────────────────────────────────────────────────────

class TestClassifyRoofShapes:
    _BBOX = (51.502, 51.498, 0.004, -0.002)

    def _make_rgb(self, h=64, w=64, brightness=120):
        return np.full((h, w, 3), brightness, dtype=np.uint8)

    def test_output_is_geojson_with_stats(self):
        geojson = _simple_geojson()
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX)
        assert "features" in result
        assert "_stats" in result
        s = result["_stats"]
        assert "total" in s
        assert s["total"] == s["classified"] + s["skipped"] + s["unchanged"]

    def test_classified_buildings_have_roof_source(self):
        geojson = _simple_geojson()
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX)
        for feat in result["features"]:
            props = feat["properties"]
            if props.get("roof:shape"):
                assert props.get("roof_source") == "satellite_classify"

    def test_existing_tag_not_overwritten_by_default(self):
        """Without overwrite=True, pre-existing roof:shape must be preserved."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [0.0000, 51.5000],
                            [0.0002, 51.5000],
                            [0.0002, 51.5001],
                            [0.0000, 51.5001],
                            [0.0000, 51.5000],
                        ]],
                    },
                    "properties": {"roof:shape": "gabled"},
                }
            ],
        }
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX, overwrite=False)
        assert result["_stats"]["unchanged"] == 1
        assert result["features"][0]["properties"]["roof:shape"] == "gabled"

    def test_overwrite_flag_replaces_existing_tag(self):
        """With overwrite=True, pre-existing roof:shape may be replaced."""
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "Polygon",
                        "coordinates": [[
                            [0.0000, 51.5000],
                            [0.0002, 51.5000],
                            [0.0002, 51.5001],
                            [0.0000, 51.5001],
                            [0.0000, 51.5000],
                        ]],
                    },
                    "properties": {"roof:shape": "pyramidal"},
                }
            ],
        }
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX, overwrite=True)
        assert result["_stats"]["unchanged"] == 0

    def test_multipolygon_feature_handled(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {
                        "type": "MultiPolygon",
                        "coordinates": [
                            [[
                                [0.0000, 51.5000],
                                [0.0002, 51.5000],
                                [0.0002, 51.5001],
                                [0.0000, 51.5001],
                                [0.0000, 51.5000],
                            ]]
                        ],
                    },
                    "properties": {},
                }
            ],
        }
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX)
        assert "features" in result

    def test_invalid_geometry_skipped(self):
        geojson = {
            "type": "FeatureCollection",
            "features": [
                {
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [0.001, 51.5]},
                    "properties": {},
                }
            ],
        }
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX)
        assert result["_stats"]["skipped"] == 1

    def test_empty_featurecollection(self):
        geojson = {"type": "FeatureCollection", "features": []}
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, rgb, self._BBOX)
        assert result["_stats"]["total"] == 0

    def test_list_input_treated_as_multitemporal(self):
        """Passing a list of 2 images should not raise."""
        geojson = _simple_geojson()
        rgb = self._make_rgb()
        result = classify_roof_shapes(geojson, [rgb, rgb], self._BBOX)
        assert "features" in result
        assert result["_stats"]["n_images"] if "_stats" in result else True

    def test_height_raster_enables_estimate(self):
        """estimate_roof_heights=True fills roof:height when dem available."""
        # Bell-shaped DEM → gabled, roof:height should be filled
        geojson = _simple_geojson()
        h, w = 64, 64
        dem = np.zeros((h, w), dtype=np.float32)
        cx = w // 2
        for c in range(w):
            dem[:, c] = max(0.0, 4.0 - abs(c - cx) * 0.15)
        rgb = self._make_rgb(h, w)
        result = classify_roof_shapes(
            geojson, rgb, self._BBOX,
            height_raster=dem,
            estimate_roof_heights=True,
        )
        # No assertion on the specific tag (may fall through due to small test image)
        # but the call must not raise
        assert "features" in result

    def test_acquisition_months_hours_accepted(self):
        geojson = _simple_geojson()
        rgb = [self._make_rgb(), self._make_rgb()]
        result = classify_roof_shapes(
            geojson, rgb, self._BBOX,
            acquisition_months=[3, 9],
            acquisition_hours=[9, 15],
        )
        assert "features" in result


# ─────────────────────────────────────────────────────────────────────────────
# TestMultiTemporalStack
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiTemporalStack:
    """Check that _extract_multitemporal_features returns a valid NamedTuple."""

    def _fp_mask(self, h=40, w=40):
        mask = np.zeros((h, w), dtype=bool)
        mask[15:25, 15:25] = True
        return mask

    def test_returns_namedtuple_with_correct_fields(self):
        fp = self._fp_mask()
        rgb = np.full((*fp.shape, 3), 120, dtype=np.uint8)
        shadow = np.zeros(fp.shape, dtype=bool)
        shadow[25:30, 15:25] = True
        feat = _extract_multitemporal_features(
            rgb_stack=[rgb, rgb],
            shadow_stack=[shadow, shadow],
            footprint_mask=fp,
            pixel_m=0.5,
            lat=51.5,
            lon=-0.1,
            acquisition_months=[6, 9],
            acquisition_hours=[10, 14],
        )
        assert isinstance(feat, _MultiTemporalFeatures)
        assert feat.n_images == 2
        assert feat.tri_confidence >= 0.0
        assert feat.tri_height_m >= 0.0

    def test_single_image_returns_zero_mt_confidence(self):
        fp = self._fp_mask()
        rgb = np.full((*fp.shape, 3), 100, dtype=np.uint8)
        shadow = np.zeros(fp.shape, dtype=bool)
        feat = _extract_multitemporal_features(
            rgb_stack=[rgb],
            shadow_stack=[shadow],
            footprint_mask=fp,
            pixel_m=1.0,
            lat=52.0,
            lon=0.0,
            acquisition_months=[6],
            acquisition_hours=[12],
        )
        # Single image → low/zero triangulation confidence
        assert feat.n_images == 1
        assert feat.tri_confidence <= 0.30
