"""Targeted tests for the skyline_cv research scaffold."""

from __future__ import annotations

import numpy as np
import pytest

from city2stl.skyline_cv.pipeline import (
    BuildingRecord,
    Viewpoint,
    _cull_occluded_projections,
    _match_projections_to_peaks,
    _project_building,
    _seed_from_view_name,
    aggregate_building_heights,
    match_segments_to_buildings,
    detect_building_silhouettes,
    RegisteredBuildingEstimate,
)
from city2stl.skyline_cv.region_pdf import _parse_streetview_url


def _vp(heading: float = 0.0, fov: float = 80.0, w: int = 960, h: int = 540) -> Viewpoint:
    return Viewpoint(
        name="t",
        query="t",
        lat=10.4,
        lon=-75.55,
        heading=heading,
        pitch=0.0,
        fov=fov,
        image_width=w,
        image_height=h,
    )


def _bld(fid: str, lat: float, lon: float, height: float | None = None, area: float = 100.0) -> BuildingRecord:
    return BuildingRecord(
        feature_id=fid,
        name=fid,
        geometry=None,
        centroid_lat=lat,
        centroid_lon=lon,
        height_tag_m=height,
        height_source="osm_tag" if height is not None else "default",
        area_m2=area,
    )


class TestParseStreetviewUrl:
    def test_real_cartagena_url_returns_h_token_not_y(self):
        url = (
            "https://www.google.com/maps/place/X/@10.4069333,-75.5559,3a,75y,321.21h,95.81t/"
            "data=!3m8!1e1!3m6!1sCIHM0ogKEICAgIDagoD5Mg!2e10!3e11!6shttps:..."
        )
        result = _parse_streetview_url(url)
        assert result is not None
        lat, lon, heading, fov, pitch, pano_id = result
        assert lat == pytest.approx(10.4069333)
        assert lon == pytest.approx(-75.5559)
        assert heading == pytest.approx(321.21)  # the bug returned 75.0 here
        assert fov == pytest.approx(75.0)
        assert pitch == pytest.approx(90.0 - 95.81)
        assert pano_id == "CIHM0ogKEICAgIDagoD5Mg"

    def test_heading_normalized_to_0_360(self):
        url = "https://maps.google/@10,-75,3a,75y,720.5h,90t/"
        result = _parse_streetview_url(url)
        assert result is not None
        assert result[2] == pytest.approx(0.5)
        assert result[5] is None  # no pano_id in this URL

    def test_query_string_fallback(self):
        url = "https://example.com/?lat=10.0&lon=-75.0&heading=42&fov=70&pitch=-3"
        result = _parse_streetview_url(url)
        assert result == (10.0, -75.0, 42.0, 70.0, -3.0, None)

    def test_empty_returns_none(self):
        assert _parse_streetview_url("") is None
        assert _parse_streetview_url("   ") is None


class TestProjectBuildingFrustumCull:
    def test_drops_building_outside_fov(self):
        vp = _vp(heading=0.0, fov=60.0)  # ±30°
        # Building 1km due east of camera. Camera looking north → bearing 90°, outside fov.
        b = _bld("east", lat=10.4, lon=-75.55 + 0.009, height=20.0)
        assert _project_building(b, vp, heading_offset_deg=0.0) is None

    def test_keeps_building_in_fov(self):
        vp = _vp(heading=0.0, fov=80.0)  # ±40°
        # Building 500m due north of camera (bearing 0°)
        b = _bld("north", lat=10.4 + 0.0045, lon=-75.55, height=30.0)
        result = _project_building(b, vp, heading_offset_deg=0.0)
        assert result is not None
        assert abs(result["x_px"] - vp.image_width * 0.5) < 5  # near image center

    def test_drops_too_far_building(self):
        vp = _vp(heading=0.0)
        # ~5km north — beyond default 4km cap
        b = _bld("far", lat=10.4 + 0.045, lon=-75.55, height=100.0)
        assert _project_building(b, vp, heading_offset_deg=0.0) is None

    def test_drops_too_close_building(self):
        vp = _vp(heading=0.0)
        # ~1m north
        b = _bld("close", lat=10.4 + 0.000005, lon=-75.55, height=20.0)
        assert _project_building(b, vp, heading_offset_deg=0.0) is None


class TestCullOccluded:
    def test_dedupes_within_bin_keeps_closest_and_tallest(self):
        projections = [
            {"feature_id": "a", "x_px": 100.0, "forward_m": 50.0, "lateral_m": 0.0},
            {"feature_id": "b", "x_px": 105.0, "forward_m": 200.0, "lateral_m": 0.0},
            {"feature_id": "c", "x_px": 110.0, "forward_m": 1000.0, "lateral_m": 0.0},
        ]
        # a closest, c untagged but largest area, b shortest
        bld = {
            "a": _bld("a", 10.4, -75.55, height=10.0, area=50.0),
            "b": _bld("b", 10.4, -75.55, height=15.0, area=80.0),
            "c": _bld("c", 10.4, -75.55, height=80.0, area=600.0),
        }
        kept = _cull_occluded_projections(projections, bld, image_width=960, bin_px=24)
        ids = {p["feature_id"] for p in kept}
        assert "a" in ids  # closest
        assert "c" in ids  # tallest


class TestHungarianMatching:
    def test_one_to_one_assignment(self):
        projs = [
            {"feature_id": "a", "x_px": 100.0},
            {"feature_id": "b", "x_px": 200.0},
            {"feature_id": "c", "x_px": 300.0},
        ]
        peaks = np.array([102, 198, 305], dtype=np.int32)
        matches, mean_resid = _match_projections_to_peaks(projs, peaks, max_match_px=30.0)
        assert len(matches) == 3
        assert mean_resid < 5.0

    def test_drops_pairs_above_max(self):
        projs = [{"feature_id": "a", "x_px": 100.0}, {"feature_id": "b", "x_px": 200.0}]
        peaks = np.array([800, 850], dtype=np.int32)
        matches, mean_resid = _match_projections_to_peaks(projs, peaks, max_match_px=30.0)
        assert matches == []
        assert mean_resid == float("inf")

    def test_returns_inf_when_empty(self):
        matches, resid = _match_projections_to_peaks([], np.array([], dtype=np.int32))
        assert matches == []
        assert resid == float("inf")


class TestSeedFromViewName:
    def test_strips_heading_suffix(self):
        assert _seed_from_view_name("seed_1_321") == "seed_1"
        assert _seed_from_view_name("seed_default_2_088") == "seed_default_2"

    def test_returns_input_if_no_heading_suffix(self):
        assert _seed_from_view_name("just_a_name") == "just_a_name"


class TestDetectBuildingSilhouettes:
    def _facade_image(self, w: int, h: int, contour: np.ndarray) -> np.ndarray:
        """Build a synthetic image with brown 'building' below the contour and
        bright 'sky' above, so _segment_has_structure doesn't reject the
        synthetic segments for lack of structure."""
        img = np.full((h, w, 3), [220, 230, 240], dtype=np.uint8)  # sky
        for x in range(w):
            y = int(contour[x])
            # Vertical stripes to give Sobel some structure
            facade_color = [120, 100, 80] if (x // 8) % 2 == 0 else [140, 115, 95]
            img[y:, x] = facade_color
        return img

    def test_finds_two_buildings_split_by_valley(self):
        h = 540
        w = 600
        contour = np.full(w, h * 0.6, dtype=np.float32)
        contour[50:200] = h * 0.3   # building A roof
        contour[260:380] = h * 0.4  # building B roof
        img = self._facade_image(w, h, contour)
        segs = detect_building_silhouettes(contour, img, min_width_px=15)
        # Each building should produce at least one silhouette
        assert len(segs) >= 2
        mids = sorted(s["mid_x"] for s in segs)
        # Roughly one centered in [50..200], another in [260..380]
        assert any(75 <= m <= 200 for m in mids)
        assert any(260 <= m <= 380 for m in mids)

    def test_no_segments_for_flat_skyline(self):
        h = 540
        w = 400
        contour = np.full(w, 300.0, dtype=np.float32)
        img = self._facade_image(w, h, contour)
        assert detect_building_silhouettes(contour, img) == []

    def test_drops_too_narrow_segments(self):
        h = 400
        w = 200
        contour = np.full(w, h * 0.6, dtype=np.float32)
        contour[80:85] = h * 0.2  # only 5 px wide spike
        img = self._facade_image(w, h, contour)
        segs = detect_building_silhouettes(contour, img, min_width_px=20)
        # Spike narrower than min_width_px → should drop or coalesce; expect empty
        assert all(s["x_right"] - s["x_left"] >= 20 for s in segs)


class TestMatchSegmentsToBuildings:
    def test_nearest_in_segment_wins(self):
        """With interval-IoU matching: the candidate whose projected footprint
        overlaps the segment most, breaking ties by nearest distance."""
        seg = {"x_left": 100, "x_right": 200, "top_y": 50, "base_y": 540, "mid_x": 150}
        projs = [
            # tall is closer AND its projected footprint covers the segment
            {"feature_id": "tall", "x_px": 150.0, "x_left_px": 110.0,
             "x_right_px": 190.0, "forward_m": 150.0},
            # short is much further and only grazes the segment edge
            {"feature_id": "short", "x_px": 105.0, "x_left_px": 95.0,
             "x_right_px": 115.0, "forward_m": 800.0},
            {"feature_id": "outside", "x_px": 300.0, "x_left_px": 290.0,
             "x_right_px": 310.0, "forward_m": 200.0},
        ]
        bld = {
            "short": _bld("short", 10.4, -75.55, height=10.0),
            "tall": _bld("tall", 10.4, -75.55, height=80.0),
            "outside": _bld("outside", 10.4, -75.55, height=200.0),
        }
        out = match_segments_to_buildings([seg], projs, bld)
        assert len(out) == 1
        assert out[0]["matched_projection"]["feature_id"] == "tall"

    def test_kiosk_in_front_of_tower(self):
        """Kiosk-in-front-of-tower case: when the nearest candidate is < 5 m
        proxy height and a much taller one sits within 1.5× the nearest
        distance, prefer the taller one (it defines the skyline peak)."""
        seg = {"x_left": 100, "x_right": 200, "top_y": 50, "base_y": 540, "mid_x": 150}
        projs = [
            {"feature_id": "kiosk", "x_px": 150.0, "x_left_px": 120.0,
             "x_right_px": 180.0, "forward_m": 100.0},
            {"feature_id": "tower", "x_px": 150.0, "x_left_px": 110.0,
             "x_right_px": 190.0, "forward_m": 140.0},
        ]
        bld = {
            # kiosk: tiny area → height proxy ~3 m, below the 5 m threshold
            "kiosk": _bld("kiosk", 10.4, -75.55, area=25.0),
            "tower": _bld("tower", 10.4, -75.55, height=80.0),
        }
        out = match_segments_to_buildings([seg], projs, bld)
        assert out[0]["matched_projection"]["feature_id"] == "tower"

    def test_no_match_when_no_projection_in_segment(self):
        seg = {"x_left": 0, "x_right": 50, "top_y": 50, "base_y": 540, "mid_x": 25}
        projs = [{"feature_id": "far", "x_px": 500.0,
                  "x_left_px": 490.0, "x_right_px": 510.0, "forward_m": 200.0}]
        bld = {"far": _bld("far", 10.4, -75.55, height=20.0)}
        out = match_segments_to_buildings([seg], projs, bld)
        assert out[0]["matched_projection"] is None


class TestAggregateBuildingHeights:
    def test_n_seeds_counts_distinct(self):
        ests = [
            RegisteredBuildingEstimate("b1", "B1", "seed_1_010", 0.0, 100, 50, 200.0, 30.0, 0.7),
            RegisteredBuildingEstimate("b1", "B1", "seed_1_020", 0.0, 100, 50, 200.0, 32.0, 0.7),
            RegisteredBuildingEstimate("b1", "B1", "seed_2_010", 0.0, 100, 50, 200.0, 31.0, 0.7),
        ]
        agg = aggregate_building_heights(ests)
        assert len(agg) == 1
        assert agg[0]["n_seeds"] == 2
        assert agg[0]["n_views"] == 3
