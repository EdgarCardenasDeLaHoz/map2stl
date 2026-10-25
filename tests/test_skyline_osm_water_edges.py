"""Edge cases for city2stl.skyline.osm_water (F-SKY13 OSM coastline extraction).

Covers malformed or empty features, duplicate vertices, spacing carried across
short segments, and the green-area (F-SKY18) helpers. Synthetic GeoJSON only.
"""

from __future__ import annotations

import pytest

from city2stl.skyline.osm_water import (
    _haversine_m,
    _resample_line,
    clip_to_radius,
    extract_coastline_features,
    extract_green_features,
    green_keypoints_for_scoring,
    sample_coastline_points,
    sample_green_points,
)

SEED = (0.0, 0.0)
STEP = 0.0001          # degrees of latitude, about 11.1 m


def _feat(gtype, coords, **props):
    return {"type": "Feature", "geometry": {"type": gtype, "coordinates": coords}, "properties": props}


def _square(lon, lat, d):
    return [[lon, lat], [lon + d, lat], [lon + d, lat + d], [lon, lat + d], [lon, lat]]


class TestResampleLine:
    def test_fewer_than_two_points_gives_nothing(self):
        assert _resample_line([], 20.0) == []
        assert _resample_line([[0.0, 0.0]], 20.0) == []

    def test_duplicate_vertices_are_skipped(self):
        clean = _resample_line([[0.0, 0.0], [0.0, 0.001]], 20.0)
        dup = _resample_line([[0.0, 0.0], [0.0, 0.0], [0.0, 0.001], [0.0, 0.001]], 20.0)
        assert dup == pytest.approx(clean)

    def test_spacing_carries_across_short_segments(self):
        # ten 11 m segments, 20 m spacing: points every 20 m along the whole line
        line = [[0.0, i * STEP] for i in range(11)]
        pts = _resample_line(line, 20.0)
        gaps = [_haversine_m(*a, *b) for a, b in zip(pts, pts[1:], strict=False)]
        assert len(pts) == 6
        assert gaps == pytest.approx([20.0] * 5, abs=0.01)


class TestCoastlineEdges:
    def test_properties_none_and_missing_layer(self):
        osm = {"waterways": {"features": [
            {"type": "Feature", "geometry": None, "properties": None},
            _feat("LineString", [[0, 0], [0, 0.001]], natural="coastline"),
        ]}}
        assert len(extract_coastline_features(osm)) == 1
        assert extract_coastline_features({"waterways": None}) == []

    def test_only_line_geometries_are_sampled(self):
        feats = [
            _feat("MultiLineString", [[[0, 0], [0, 0.001]], [[0.01, 0], [0.01, 0.001]]]),
            _feat("Polygon", [_square(0, 0, 0.001)]),
            {"type": "Feature", "geometry": None},
            {"type": "Feature"},
        ]
        pts = sample_coastline_points(feats, spacing_m=20.0)
        one = sample_coastline_points([_feat("LineString", [[0, 0], [0, 0.001]])], spacing_m=20.0)
        assert len(pts) == 2 * len(one)
        assert all(lon in (0.0, 0.01) for lon, _ in pts)

    def test_clip_drops_features_without_coordinates(self):
        near = _feat("LineString", [[0, 0], [0, 0.001]])
        feats = [near, {"type": "Feature", "geometry": None}, {"type": "Feature"},
                 {"type": "Feature", "geometry": {"type": "LineString"}}]
        assert clip_to_radius(feats, SEED, 1000.0) == [near]


class TestGreen:
    def test_extract_keeps_only_areas(self):
        poly = _feat("Polygon", [_square(0, 0, 0.001)], leisure="park")
        multi = _feat("MultiPolygon", [[_square(0, 0, 0.001)]], landuse="grass")
        osm = {"green": {"features": [poly, multi, _feat("LineString", [[0, 0], [0, 1]]),
                                      {"type": "Feature", "geometry": None}]}}
        assert extract_green_features(osm) == [poly, multi]
        assert extract_green_features({}) == []
        assert extract_green_features({"green": None}) == []

    def test_sample_walks_exterior_rings_only(self):
        outer = _square(0, 0, 0.001)
        hole = _square(0.0004, 0.0004, 0.0002)
        with_hole = sample_green_points([_feat("Polygon", [outer, hole])], spacing_m=20.0)
        plain = sample_green_points([_feat("Polygon", [outer])], spacing_m=20.0)
        assert with_hole == plain and plain

    def test_sample_skips_empty_geometries(self):
        feats = [_feat("Polygon", []), _feat("MultiPolygon", [[], [_square(0, 0, 0.001)]]),
                 {"type": "Feature", "geometry": None}]
        assert sample_green_points(feats, spacing_m=20.0) == sample_green_points(
            [_feat("Polygon", [_square(0, 0, 0.001)])], spacing_m=20.0)

    def test_keypoints_bearing_and_seed_point_dropped(self):
        # the square's first vertex is the seed itself (under 1 m: dropped)
        kps = green_keypoints_for_scoring([_feat("Polygon", [_square(0, 0, 0.001)])], SEED, spacing_m=50.0)
        assert kps and all(k["distance_m"] >= 1.0 for k in kps)
        east = [k for k in kps if k["lat"] == pytest.approx(0.0) and k["lon"] > 0]
        assert east and all(k["bearing_deg"] == pytest.approx(90.0, abs=0.01) for k in east)
        assert green_keypoints_for_scoring([], SEED) == []
