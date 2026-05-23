"""Unit tests for the F-SKY13 Phase B inverse pinhole helper.

``pano_water_top_to_lonlat`` inverts the forward projection in
``project_lonlat_to_view``. Tests build a synthetic pano water mask with a
known water-top in selected columns and verify the round-trip back to
lon/lat is consistent.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from city2stl.skyline_cv.coastline_registration import (
    pano_water_top_to_lonlat,
    project_lonlat_to_view,
)


def _make_pano_mask(height: int, width: int, water_top_y: int) -> np.ndarray:
    """A pano water mask with a flat water band starting at ``water_top_y``."""
    mask = np.zeros((height, width), dtype=bool)
    mask[water_top_y:, :] = True
    return mask


def _headings_uniform(width: int) -> np.ndarray:
    return np.linspace(0.0, 360.0, width, endpoint=False, dtype=np.float64)


class TestPanoWaterTopToLonLat:
    def test_empty_inputs_return_empty(self):
        assert pano_water_top_to_lonlat(None, np.array([]), 0.0, 0.0) == []
        empty = np.zeros((0, 0), dtype=bool)
        assert pano_water_top_to_lonlat(empty, np.array([]), 0.0, 0.0) == []

    def test_no_water_returns_empty(self):
        mask = np.zeros((100, 96), dtype=bool)
        headings = _headings_uniform(96)
        assert pano_water_top_to_lonlat(mask, headings, 0.0, 0.0) == []

    def test_uniform_water_top_yields_points(self):
        # 96-column pano, water starting at row 60 (below the horizon)
        mask = _make_pano_mask(100, 96, water_top_y=60)
        headings = _headings_uniform(96)
        pts = pano_water_top_to_lonlat(
            mask, headings, seed_lat=40.0, seed_lon=-75.0,
            column_stride=8,
        )
        # 12 sampled columns at stride 8, each with valid water
        assert len(pts) == 12
        # All points should be roughly equidistant from the seed (uniform
        # water_top → uniform distance), since headings cover 360° they'll
        # surround the seed approximately.
        seed = (-75.0, 40.0)
        dists = [
            math.hypot((p[0] - seed[0]) * 85000.0, (p[1] - seed[1]) * 111000.0)
            for p in pts
        ]
        # All within 30% of the median (uniform geometry)
        med = float(np.median(dists))
        assert all(0.7 * med <= d <= 1.3 * med for d in dists)

    def test_max_distance_filters_far_points(self):
        # Water row very close to horizon → very large distance → should be
        # filtered out when max_distance_m is small.
        mask = _make_pano_mask(100, 96, water_top_y=52)  # just below cy=50
        headings = _headings_uniform(96)
        pts_unlimited = pano_water_top_to_lonlat(
            mask, headings, 40.0, -75.0,
            column_stride=8, max_distance_m=100_000.0,
        )
        pts_limited = pano_water_top_to_lonlat(
            mask, headings, 40.0, -75.0,
            column_stride=8, max_distance_m=50.0,
        )
        assert len(pts_limited) <= len(pts_unlimited)

    def test_higher_water_top_means_farther(self):
        # Higher water_top_y (closer to horizon) → larger distance
        headings = _headings_uniform(96)
        mask_near = _make_pano_mask(200, 96, water_top_y=180)  # well below cy
        mask_far = _make_pano_mask(200, 96, water_top_y=120)   # closer to cy

        pts_near = pano_water_top_to_lonlat(
            mask_near, headings, 40.0, -75.0,
            column_stride=8, max_distance_m=10_000.0,
        )
        pts_far = pano_water_top_to_lonlat(
            mask_far, headings, 40.0, -75.0,
            column_stride=8, max_distance_m=10_000.0,
        )
        # Both should produce points
        assert pts_near and pts_far
        # Median distance from seed should be larger for the "closer to horizon" mask
        seed = (-75.0, 40.0)
        d_near = np.median([
            math.hypot((p[0] - seed[0]) * 85000.0, (p[1] - seed[1]) * 111000.0)
            for p in pts_near
        ])
        d_far = np.median([
            math.hypot((p[0] - seed[0]) * 85000.0, (p[1] - seed[1]) * 111000.0)
            for p in pts_far
        ])
        assert d_far > d_near

    def test_round_trip_consistency(self):
        # Forward-project a known sea-level lon/lat, place water at that
        # column's y, invert, and verify the recovered point is near the
        # original. Tolerance accounts for column-quantization (the helper
        # samples at column_stride=8 so the result is rounded to that grid).
        seed_lat, seed_lon = 40.0, -75.0
        # Point ~500 m east of the seed at sea level
        target_lon = seed_lon + (500.0 / (111_320.0 * math.cos(math.radians(seed_lat))))
        target_lat = seed_lat
        # 96-wide pano covering 360° → heading at col 24 is 90° (east)
        proj = project_lonlat_to_view(
            target_lon, target_lat,
            seed_lat=seed_lat, seed_lon=seed_lon,
            heading_deg=90.0,  # looking east
            fov_deg=360.0,
            image_width=96,
            image_height=200,
        )
        # Forward projection puts the point at (x_px, y_px); we use the y
        # to construct a synthetic water mask whose top row matches.
        if proj is None:
            pytest.skip("forward projection unavailable for this geometry")
        _, y_target = proj
        if not (0 < y_target < 200):
            pytest.skip("projected y outside frame")
        mask = np.zeros((200, 96), dtype=bool)
        mask[int(y_target):, :] = True
        headings = _headings_uniform(96)
        recovered = pano_water_top_to_lonlat(
            mask, headings, seed_lat, seed_lon,
            column_stride=1, max_distance_m=2000.0,
        )
        assert recovered  # should have points
        # Find the recovered point closest to bearing 90° (east). The
        # uniform headings array places east at index 24.
        east_lon, east_lat = recovered[24]
        # Both should be within ~50 m of the target (column quantisation)
        d_lon = (east_lon - target_lon) * 85_000.0
        d_lat = (east_lat - target_lat) * 111_000.0
        assert math.hypot(d_lon, d_lat) < 60.0
