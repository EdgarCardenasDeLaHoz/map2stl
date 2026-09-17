"""Elevation-datum regression tests for the panoramic height estimator.

The pinhole height math mixes two elevations: the camera's, fetched as metres
above sea level, and the building's own ground. When per-building terrain is
not fetched the record keeps a 0.0 placeholder — subtracting that from an
absolute rooftop z adds the entire camera elevation to every building.

At sea level (Cartagena, the region this pipeline was developed against) the
error is a couple of metres and invisible. In Denver it is +1600 m, which not
only inflates every height but pushes every estimate outside the plausibility
gates, so the region silently yields nothing at all.

These tests pin the datum behaviour so the placeholder cannot come back.
"""

import math

import numpy as np
import pytest

from city2stl.skyline._core.height import (
    _ground_elev_m,
    augment_estimates_with_depth,
    estimate_heights_from_registration,
)
from city2stl.skyline._core.types import (
    BuildingRecord,
    CapturedView,
    RegisteredBuildingEstimate,
    Viewpoint,
)

IMAGE_W = 960
IMAGE_H = 540
FOV = 90.0
ROOF_Y = 170.0          # 100 px above the principal row
FORWARD_M = 200.0
CAMERA_HEIGHT_M = 1.7

# f = 0.5 * W / tan(fov/2) = 480 px for a 960 px wide 90 deg view.
F_PX = 0.5 * IMAGE_W / math.tan(math.radians(FOV) * 0.5)
# The building's height above its own base, with camera and building on the
# same ground plane: cam_height + forward * tan(atan((cy - roof_y) / f)).
EXPECTED_H = CAMERA_HEIGHT_M + FORWARD_M * math.tan(
    math.atan((IMAGE_H * 0.5 - ROOF_Y) / F_PX))


def _viewpoint(pitch: float = 0.0) -> Viewpoint:
    return Viewpoint(
        name="seed_1_000", query="", lat=0.0, lon=0.0, heading=0.0,
        pitch=pitch, fov=FOV, image_width=IMAGE_W, image_height=IMAGE_H,
    )


def _captured(pitch: float = 0.0) -> CapturedView:
    return CapturedView(
        viewpoint=_viewpoint(pitch),
        image_path=None,
        metadata_path=None,
        image=np.zeros((IMAGE_H, IMAGE_W, 3), dtype=np.uint8),
    )


def _building(terrain_elev_m: float = 0.0, terrain_known: bool = False,
              height_tag_m=None) -> BuildingRecord:
    return BuildingRecord(
        feature_id="b0001", name="tower", geometry=None,
        centroid_lat=0.0, centroid_lon=0.0,
        height_tag_m=height_tag_m, height_source="none", area_m2=900.0,
        terrain_elev_m=terrain_elev_m, terrain_known=terrain_known,
    )


def _registration() -> dict:
    contour = np.full(IMAGE_W, ROOF_Y, dtype=np.float32)
    proj = {
        "feature_id": "b0001",
        "x_px": IMAGE_W * 0.5,
        "forward_m": FORWARD_M,
        "match_residual_px": 0.0,
    }
    return {
        "contour": contour,
        "best_offset": 0.0,
        "best_score": 0.0,
        "projections": [proj],
        "all_projections": [proj],
    }


@pytest.fixture(autouse=True)
def _no_neural_masks(monkeypatch):
    """Force the contour path: no SegFormer in a unit test."""
    monkeypatch.setattr(
        "city2stl.skyline._core.height._neural_sky_and_building_masks",
        lambda image: (None, None),
    )
    monkeypatch.setattr(
        "city2stl.skyline._core.height._building_projected_x_range",
        lambda *a, **k: None,
    )


def _height_for(camera_elev_m: float, building: BuildingRecord) -> float:
    out = estimate_heights_from_registration(
        _captured(), _registration(), [building],
        camera_height_m=CAMERA_HEIGHT_M, camera_elev_m=camera_elev_m,
    )
    assert len(out) == 1, "expected exactly one estimate"
    return out[0].estimated_height_m


class TestGroundElevHelper:
    def test_measured_terrain_is_used(self):
        assert _ground_elev_m(_building(120.0, True), 1600.0) == 120.0

    def test_unknown_terrain_falls_back_to_the_camera_ground(self):
        assert _ground_elev_m(_building(0.0, False), 1600.0) == 1600.0

    def test_placeholder_zero_is_not_mistaken_for_sea_level(self):
        # terrain_elev_m == 0.0 but never measured: must NOT be subtracted.
        assert _ground_elev_m(_building(0.0, False), 250.0) == 250.0


class TestElevationDatum:
    def test_height_is_independent_of_camera_elevation_when_terrain_unknown(self):
        """A Denver seed must not report 1600 m taller than a Cartagena one."""
        sea_level = _height_for(0.0, _building())
        denver = _height_for(1600.0, _building())
        assert sea_level == pytest.approx(EXPECTED_H, abs=0.01)
        assert denver == pytest.approx(sea_level, abs=1e-6)

    def test_measured_terrain_below_the_camera_makes_the_building_taller(self):
        """Camera on a hill 30 m above the footprint: 30 m more building."""
        level = _height_for(100.0, _building(100.0, terrain_known=True))
        downhill = _height_for(100.0, _building(70.0, terrain_known=True))
        assert level == pytest.approx(EXPECTED_H, abs=0.01)
        assert downhill == pytest.approx(level + 30.0, abs=0.01)

    def test_elevated_region_still_yields_estimates(self):
        """The plausibility gate must not swallow every building at altitude.

        With the datum bug the geometric y-gate computed bounds around
        (300 - cam_z), which is negative at any real city elevation, so every
        candidate fell outside the window and the region produced no heights.
        """
        for elev in (0.0, 250.0, 1600.0, 2640.0):
            out = estimate_heights_from_registration(
                _captured(), _registration(), [_building()],
                camera_height_m=CAMERA_HEIGHT_M, camera_elev_m=elev,
            )
            assert len(out) == 1, f"no estimate survived at {elev} m elevation"

    def test_tagged_building_survives_at_altitude(self):
        """The tag-disagreement filter also used to drop everything up high."""
        out = estimate_heights_from_registration(
            _captured(), _registration(),
            [_building(height_tag_m=round(EXPECTED_H, 1))],
            camera_height_m=CAMERA_HEIGHT_M, camera_elev_m=1600.0,
        )
        assert len(out) == 1
        assert out[0].estimated_height_m == pytest.approx(EXPECTED_H, abs=0.01)


class TestDepthCalibrationAnchors:
    """F-SKY12 anchors must sit on the facade, not on the silhouette top.

    ``forward_m`` is the horizontal ground distance to the footprint. Only a
    pixel whose sight line is horizontal has a z-depth equal to that. The
    silhouette top is the last building pixel before sky, so a monocular
    depth model routinely reports the far background there — regressing that
    against near footprint distances mis-scales the entire depth map.
    """

    @staticmethod
    def _estimate(y_px: float, *, feature_id: str = "b0001",
                  x_px: float = IMAGE_W * 0.5,
                  forward_m: float = FORWARD_M) -> RegisteredBuildingEstimate:
        return RegisteredBuildingEstimate(
            feature_id=feature_id, name="tower", view_name="seed_1_000",
            heading_offset_deg=0.0, x_px=x_px, y_px=y_px,
            forward_m=forward_m, estimated_height_m=EXPECTED_H, confidence=0.9,
        )

    @staticmethod
    def _depth_map() -> np.ndarray:
        """Relative depth, DA2 convention: higher value = closer.

        Everything above the roof line is distant sky; the facade below it
        is close. Anchoring on the roof row samples the sky value.
        """
        depth = np.full((IMAGE_H, IMAGE_W), 0.9, dtype=np.float32)
        # Sky through the roof row inclusive: the silhouette top is the
        # boundary pixel, and that is exactly where the background bleeds in.
        depth[: int(ROOF_Y) + 1] = 0.02
        return depth

    def _patch_predict(self, monkeypatch):
        monkeypatch.setattr(
            "city2stl.skyline.depth_estimation.predict_pano_depth",
            lambda image, device="cpu": self._depth_map(),
        )

    def _augment(self, y_px: float):
        return augment_estimates_with_depth(
            np.zeros((IMAGE_H, IMAGE_W, 3), dtype=np.uint8),
            [self._estimate(y_px)], _viewpoint(),
            camera_height_m=CAMERA_HEIGHT_M,
        )

    def test_anchor_row_is_the_horizon_not_the_roof(self, monkeypatch):
        self._patch_predict(monkeypatch)
        import city2stl.skyline.depth_estimation as de
        real_calibrate = de.calibrate_pano_depth
        seen: dict = {}

        def _spy(depth_rel, anchors):
            seen["anchors"] = list(anchors)
            return real_calibrate(depth_rel, anchors)

        monkeypatch.setattr(de, "calibrate_pano_depth", _spy)
        self._augment(ROOF_Y)
        rows = [a[0] for a in seen["anchors"]]
        assert rows == [IMAGE_H // 2], "anchor should be the horizontal row"
        assert all(r > ROOF_Y for r in rows), "anchor must be below the roof"

    def test_facade_anchor_recovers_a_sane_height(self, monkeypatch):
        self._patch_predict(monkeypatch)
        out = self._augment(ROOF_Y)
        assert out[0].depth_height_m == pytest.approx(EXPECTED_H, rel=0.05)

    def test_distance_comes_from_the_facade_not_the_roof_pixel(self, monkeypatch):
        """Two buildings, same roof row, very different distances.

        The angle to the roof is identical for both, so the whole height
        difference has to come from the sampled distance. Reading depth at
        the silhouette top gives both of them the sky's value and therefore
        the same (hugely inflated) height; reading it on the facade
        recovers the two distances the calibration was anchored on.
        """
        near_x, far_x = 200.0, 700.0
        near_d, far_d = 100.0, 400.0

        def _depth_map() -> np.ndarray:
            # Facade: left half close (0.9 rel), right half far (0.6 rel).
            # Above the roof line: sky, which DA2 reports as very distant.
            # The two anchors then fit depth_m = 1000 * (1 - rel), so the
            # sky reads 980 m — nothing like either building.
            depth = np.empty((IMAGE_H, IMAGE_W), dtype=np.float32)
            depth[:, : IMAGE_W // 2] = 0.9
            depth[:, IMAGE_W // 2:] = 0.6
            depth[: int(ROOF_Y) + 1] = 0.02
            return depth

        monkeypatch.setattr(
            "city2stl.skyline.depth_estimation.predict_pano_depth",
            lambda image, device="cpu": _depth_map(),
        )
        out = augment_estimates_with_depth(
            np.zeros((IMAGE_H, IMAGE_W, 3), dtype=np.uint8),
            [
                self._estimate(ROOF_Y, feature_id="near", x_px=near_x,
                               forward_m=near_d),
                self._estimate(ROOF_Y, feature_id="far", x_px=far_x,
                               forward_m=far_d),
            ],
            _viewpoint(), camera_height_m=CAMERA_HEIGHT_M,
        )

        tan_roof = (IMAGE_H * 0.5 - ROOF_Y) / F_PX
        for est, dist in ((out[0], near_d), (out[1], far_d)):
            expected = CAMERA_HEIGHT_M + dist * tan_roof
            assert est.depth_height_m == pytest.approx(expected, rel=0.02)
        assert out[1].depth_height_m > out[0].depth_height_m * 3.0

    def test_building_below_the_horizon_is_not_anchored(self, monkeypatch):
        """No facade row available: skip rather than fit a wrong distance."""
        self._patch_predict(monkeypatch)
        out = self._augment(IMAGE_H * 0.5 + 40.0)
        assert out[0].depth_height_m is None
