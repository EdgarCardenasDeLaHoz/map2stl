"""Unit tests for city2stl.skyline_cv.osm_water (F-SKY13 Phase A).

Covers feature extraction, 1 km radius clipping, and coastline keypoint
sampling. No network — all tests use synthetic GeoJSON fixtures.
"""

from __future__ import annotations

import pytest

from city2stl.skyline_cv.osm_water import (
    _haversine_m,
    clip_to_radius,
    extract_coastline_features,
    extract_water_features,
    sample_coastline_points,
)


def _make_coastline_feat(coords: list[list[float]]) -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "LineString", "coordinates": coords},
        "properties": {"natural": "coastline"},
    }


def _make_water_feat(coords: list[list[list[float]]], tag: str = "water") -> dict:
    return {
        "type": "Feature",
        "geometry": {"type": "Polygon", "coordinates": coords},
        "properties": {"natural": tag},
    }


def _wrap(features: list[dict]) -> dict:
    """Wrap features as the osm_data dict the pipeline produces."""
    return {"waterways": {"type": "FeatureCollection", "features": features}}


class TestExtractCoastlineFeatures:
    def test_returns_only_coastline(self):
        osm = _wrap([
            _make_coastline_feat([[0.0, 0.0], [0.01, 0.0]]),
            _make_water_feat([[[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.0]]]),
        ])
        result = extract_coastline_features(osm)
        assert len(result) == 1
        assert result[0]["properties"]["natural"] == "coastline"

    def test_empty_input(self):
        assert extract_coastline_features({}) == []
        assert extract_coastline_features(_wrap([])) == []

    def test_missing_waterways_key(self):
        assert extract_coastline_features({"buildings": {"features": []}}) == []


class TestExtractWaterFeatures:
    def test_returns_water_polygons(self):
        ring = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.0]]
        osm = _wrap([
            _make_water_feat([ring], tag="water"),
            _make_water_feat([ring], tag="bay"),
            _make_coastline_feat([[0.0, 0.0], [0.01, 0.0]]),  # excluded
        ])
        result = extract_water_features(osm)
        assert len(result) == 2

    def test_excludes_linestrings(self):
        # Even if natural=water, a LineString is not a water area
        line_water = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0, 0], [1, 0]]},
            "properties": {"natural": "water"},
        }
        assert extract_water_features(_wrap([line_water])) == []

    def test_includes_place_ocean(self):
        ring = [[0.0, 0.0], [0.01, 0.0], [0.01, 0.01], [0.0, 0.0]]
        ocean_feat = {
            "type": "Feature",
            "geometry": {"type": "Polygon", "coordinates": [ring]},
            "properties": {"place": "ocean"},
        }
        result = extract_water_features(_wrap([ocean_feat]))
        assert len(result) == 1


class TestClipToRadius:
    def test_keeps_features_inside_radius(self):
        # Coastline near (0, 0); seed at (0, 0); 1 km radius
        feat = _make_coastline_feat([[0.0, 0.0], [0.001, 0.0]])  # ~110 m
        result = clip_to_radius([feat], seed_lonlat=(0.0, 0.0), radius_m=1000.0)
        assert len(result) == 1

    def test_drops_features_outside_radius(self):
        # ~1.1 km away from origin
        feat = _make_coastline_feat([[0.01, 0.0], [0.011, 0.0]])
        result = clip_to_radius([feat], seed_lonlat=(0.0, 0.0), radius_m=500.0)
        assert result == []

    def test_keeps_partial_overlap(self):
        # Long linestring with one vertex inside the radius
        feat = _make_coastline_feat([[0.0, 0.0], [0.1, 0.0]])  # 11 km long
        result = clip_to_radius([feat], seed_lonlat=(0.0, 0.0), radius_m=500.0)
        assert len(result) == 1  # First vertex is at the seed

    def test_empty_input(self):
        assert clip_to_radius([], seed_lonlat=(0.0, 0.0), radius_m=1000.0) == []

    def test_handles_polygon_nested_rings(self):
        ring = [[0.0, 0.0], [0.001, 0.0], [0.001, 0.001], [0.0, 0.0]]
        feat = _make_water_feat([ring])
        result = clip_to_radius([feat], seed_lonlat=(0.0, 0.0), radius_m=1000.0)
        assert len(result) == 1

    def test_returns_new_list(self):
        feat = _make_coastline_feat([[0.0, 0.0], [0.001, 0.0]])
        original = [feat]
        result = clip_to_radius(original, seed_lonlat=(0.0, 0.0), radius_m=1000.0)
        assert result is not original


class TestSampleCoastlinePoints:
    def test_returns_endpoints_at_minimum(self):
        feat = _make_coastline_feat([[0.0, 0.0], [0.001, 0.0]])  # ~110 m
        pts = sample_coastline_points([feat], spacing_m=200.0)
        # At 200 m spacing on a 110 m line, only the start vertex is emitted
        assert len(pts) >= 1
        assert pts[0] == pytest.approx((0.0, 0.0), abs=1e-9)

    def test_denser_spacing_more_points(self):
        feat = _make_coastline_feat([[0.0, 0.0], [0.01, 0.0]])  # ~1.1 km
        sparse = sample_coastline_points([feat], spacing_m=500.0)
        dense = sample_coastline_points([feat], spacing_m=50.0)
        assert len(dense) > len(sparse)

    def test_handles_multilinestring(self):
        feat = {
            "type": "Feature",
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [[0.0, 0.0], [0.001, 0.0]],
                    [[0.002, 0.0], [0.003, 0.0]],
                ],
            },
            "properties": {"natural": "coastline"},
        }
        pts = sample_coastline_points([feat], spacing_m=200.0)
        # Both linestrings contribute their starting vertex
        assert len(pts) >= 2

    def test_empty_input(self):
        assert sample_coastline_points([], spacing_m=20.0) == []

    def test_degenerate_linestring(self):
        # Single-point LineString — ignored, not a crash
        bad = {
            "type": "Feature",
            "geometry": {"type": "LineString", "coordinates": [[0.0, 0.0]]},
            "properties": {"natural": "coastline"},
        }
        assert sample_coastline_points([bad]) == []


class TestOSMKeypointsForScoring:
    """OSM coastline → keypoint-dict adapter for the existing scoring path."""

    def test_shape_matches_satellite_keypoints(self):
        # The dict must carry at least bearing_deg and distance_m to be
        # consumable by score_pano_offset_keypoints.
        from city2stl.skyline_cv.osm_water import osm_keypoints_for_scoring

        feat = _make_coastline_feat([[0.0, 0.0], [0.001, 0.0]])
        kps = osm_keypoints_for_scoring(
            [feat], seed_lonlat=(0.0001, 0.0), spacing_m=20.0
        )
        assert len(kps) >= 1
        kp = kps[0]
        assert "bearing_deg" in kp
        assert "distance_m" in kp
        assert 0.0 <= kp["bearing_deg"] < 360.0
        assert kp["distance_m"] > 0.0

    def test_bearing_east(self):
        # Coastline lying due east of the seed — bearing should be ~90°.
        from city2stl.skyline_cv.osm_water import osm_keypoints_for_scoring

        feat = _make_coastline_feat([[0.001, 0.0], [0.002, 0.0]])
        kps = osm_keypoints_for_scoring(
            [feat], seed_lonlat=(0.0, 0.0), spacing_m=50.0
        )
        assert kps
        assert kps[0]["bearing_deg"] == pytest.approx(90.0, abs=1.0)

    def test_bearing_north(self):
        # Coastline lying due north of the seed — bearing should be ~0°/360°.
        from city2stl.skyline_cv.osm_water import osm_keypoints_for_scoring

        feat = _make_coastline_feat([[0.0, 0.001], [0.0, 0.002]])
        kps = osm_keypoints_for_scoring(
            [feat], seed_lonlat=(0.0, 0.0), spacing_m=50.0
        )
        assert kps
        bearing = kps[0]["bearing_deg"]
        assert bearing == pytest.approx(0.0, abs=1.0) or bearing == pytest.approx(360.0, abs=1.0)

    def test_skips_zero_distance(self):
        # A coastline that starts exactly at the seed point must not produce
        # a degenerate (distance=0) keypoint.
        from city2stl.skyline_cv.osm_water import osm_keypoints_for_scoring

        feat = _make_coastline_feat([[0.0, 0.0], [0.001, 0.0]])
        kps = osm_keypoints_for_scoring(
            [feat], seed_lonlat=(0.0, 0.0), spacing_m=20.0
        )
        # Every returned keypoint must be > 0 m from the seed
        for kp in kps:
            assert kp["distance_m"] >= 1.0

    def test_empty_input(self):
        from city2stl.skyline_cv.osm_water import osm_keypoints_for_scoring

        assert osm_keypoints_for_scoring([], seed_lonlat=(0.0, 0.0)) == []


class TestMinimapOverlay:
    """Smoke tests for the F-SKY13 overlay in region_pdf._draw_osm_coastline_overlay."""

    def test_renders_without_crash_on_empty_data(self):
        # Importing region_pdf is slow (matplotlib etc.) — keep it lazy.
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from city2stl.skyline_cv.region_pdf import _draw_osm_coastline_overlay

        fig, ax = plt.subplots()
        # Empty osm_data → no-op, no exception
        _draw_osm_coastline_overlay(
            ax,
            seed_lon=-75.0,
            seed_lat=40.0,
            mlon=85_000.0,
            mlat=110_540.0,
            osm_data={},
            radius_m=1000.0,
        )
        plt.close(fig)

    def test_draws_coastline_and_circle(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        from city2stl.skyline_cv.region_pdf import _draw_osm_coastline_overlay

        coastline = _make_coastline_feat([[-75.0, 40.0], [-75.001, 40.0]])
        osm = _wrap([coastline])

        fig, ax = plt.subplots()
        # Track how many line artists exist before/after
        n_lines_before = len(ax.lines)
        _draw_osm_coastline_overlay(
            ax,
            seed_lon=-75.0,
            seed_lat=40.0,
            mlon=85_000.0,
            mlat=110_540.0,
            osm_data=osm,
            radius_m=1000.0,
        )
        # At minimum, one coastline polyline + one 1 km circle
        assert len(ax.lines) - n_lines_before >= 2
        plt.close(fig)


class TestHaversine:
    def test_zero_distance(self):
        assert _haversine_m(0.0, 0.0, 0.0, 0.0) == pytest.approx(0.0)

    def test_one_degree_lat(self):
        # 1 degree of latitude ≈ 111 km
        assert _haversine_m(0.0, 0.0, 0.0, 1.0) == pytest.approx(111_195, rel=0.01)

    def test_symmetric(self):
        d1 = _haversine_m(-75.0, 40.0, -76.0, 40.0)
        d2 = _haversine_m(-76.0, 40.0, -75.0, 40.0)
        assert d1 == pytest.approx(d2)
