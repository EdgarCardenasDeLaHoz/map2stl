"""Tests for the optional height-estimation trace recorder.

Covers two surfaces:
  - `HeightTraceRecorder` itself (filtering, JSON-ready coercion, stage list).
  - `estimate_heights_from_registration` integration: trace=None preserves
    existing behaviour, and trace=recorder emits one event per gate.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from city2stl.skyline import pipeline
from city2stl.skyline.height_trace import (
    STAGES,
    HeightTraceRecorder,
)
from city2stl.skyline.pipeline import (
    BuildingRecord,
    CapturedView,
    Viewpoint,
    estimate_heights_from_registration,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _vp() -> Viewpoint:
    return Viewpoint(
        name="seed_test_000",
        query="t",
        lat=10.4,
        lon=-75.55,
        heading=0.0,
        pitch=0.0,
        fov=80.0,
        image_width=960,
        image_height=540,
    )


def _captured(vp: Viewpoint | None = None) -> CapturedView:
    vp = vp or _vp()
    image = np.zeros((vp.image_height, vp.image_width, 3), dtype=np.uint8)
    return CapturedView(
        viewpoint=vp,
        image_path=Path("ignored.png"),
        metadata_path=Path("ignored.json"),
        image=image,
    )


def _building(fid: str, *, lat_off: float = 0.0045,
              lon_off: float = 0.0, height: float | None = 50.0,
              area: float = 200.0) -> BuildingRecord:
    return BuildingRecord(
        feature_id=fid,
        name=fid,
        geometry=None,
        centroid_lat=10.4 + lat_off,
        centroid_lon=-75.55 + lon_off,
        height_tag_m=height,
        height_source="osm_tag" if height is not None else "default",
        area_m2=area,
        terrain_elev_m=0.0,
    )


def _registration_one(building_x_px: float, forward_m: float,
                      *, image_width: int = 960,
                      contour_top_y: float = 200.0,
                      contour_size: int | None = None) -> dict:
    """Hand-rolled registration dict for one in-FOV building."""
    contour = np.full(contour_size or image_width, contour_top_y, dtype=np.float32)
    return {
        "contour": contour,
        "best_offset": 0.0,
        "best_score": 5.0,
        "projections": [
            {"feature_id": "b1", "x_px": building_x_px, "forward_m": forward_m,
             "lateral_m": 0.0, "match_residual_px": 1.0},
        ],
        "all_projections": [
            {"feature_id": "b1", "x_px": building_x_px, "forward_m": forward_m,
             "lateral_m": 0.0},
        ],
    }


@pytest.fixture(autouse=True)
def _no_segformer(monkeypatch):
    """Skip SegFormer in tests — the contour path is exercised instead."""
    monkeypatch.setattr(
        pipeline,
        "_neural_sky_and_building_masks",
        lambda image: (None, None),
    )


# ---------------------------------------------------------------------------
# Recorder unit tests
# ---------------------------------------------------------------------------

class TestRecorder:
    def test_records_event_with_required_fields(self):
        r = HeightTraceRecorder()
        r("emit", view_name="v1", feature_id="b1", height_m=42.0)
        assert r.events == [
            {"stage": "emit", "view_name": "v1", "feature_id": "b1", "height_m": 42.0}
        ]

    def test_only_feature_id_filters(self):
        r = HeightTraceRecorder(only_feature_id="b1")
        r("emit", view_name="v1", feature_id="b1", height_m=42.0)
        r("emit", view_name="v1", feature_id="b2", height_m=99.0)
        assert len(r.events) == 1
        assert r.events[0]["feature_id"] == "b1"

    def test_unknown_stage_raises(self):
        r = HeightTraceRecorder()
        with pytest.raises(ValueError, match="unknown trace stage"):
            r("not_a_stage", view_name="v1", feature_id="b1")

    def test_to_json_ready_coerces_nan_and_inf(self):
        r = HeightTraceRecorder()
        r("pinhole_math", view_name="v1", feature_id="b1",
          height_m=float("nan"), forward_m=float("inf"))
        clean = r.to_json_ready()
        assert clean[0]["height_m"] is None
        assert clean[0]["forward_m"] is None

    def test_save_view_is_idempotent_and_copies(self):
        r = HeightTraceRecorder()
        img = np.ones((10, 10, 3), dtype=np.uint8)
        contour = np.zeros(10, dtype=np.float32)
        r.save_view("v1", img, contour, None)
        r.save_view("v1", img * 99, contour, None)  # second call ignored
        stored = r.view_artifacts["v1"]
        assert stored["image"][0, 0, 0] == 1   # first call stuck
        # And the stored copy is independent of the source array.
        img[0, 0, 0] = 7
        assert stored["image"][0, 0, 0] == 1

    def test_stages_complete(self):
        # Sanity guard: pipeline-emitted stages must all appear in STAGES.
        # If a new gate is added in pipeline.py, this catches the
        # mismatch and forces the STAGES list to be updated.
        expected_min = {
            "building_start", "drop_no_projection", "closest_in_bin",
            "drop_closest_in_bin", "roof_y_from_mask", "contour_override",
            "pinhole_math", "geometric_y_gate", "drop_geometric_gate",
            "drop_plausibility_tag", "emit",
        }
        assert expected_min <= set(STAGES)


# ---------------------------------------------------------------------------
# Integration: trace=None must preserve current behaviour
# ---------------------------------------------------------------------------

class TestIntegrationBehaviourNeutral:
    def test_trace_none_unchanged(self):
        """Output identical with and without a trace recorder attached."""
        cap = _captured()
        reg = _registration_one(building_x_px=480.0, forward_m=500.0)
        bld = _building("b1", height=80.0)

        a = estimate_heights_from_registration(
            cap, reg, [bld], camera_height_m=1.7)
        b = estimate_heights_from_registration(
            cap, reg, [bld], camera_height_m=1.7,
            trace=HeightTraceRecorder())

        assert len(a) == len(b)
        if a:
            assert a[0].feature_id == b[0].feature_id
            assert a[0].estimated_height_m == pytest.approx(
                b[0].estimated_height_m)


# ---------------------------------------------------------------------------
# Integration: each gate emits its event in the right order
# ---------------------------------------------------------------------------

class TestIntegrationGatesEmitEvents:
    def test_happy_path_emits_full_chain(self):
        cap = _captured()
        # Building 500m away, projected at image center, contour at y=200.
        # contour at y=200 with cy=270, f_px≈573, pitch=0 →
        # angle ≈ atan((270-200)/573) ≈ 0.121 rad ≈ 6.96°
        # height_m ≈ 1.7 + 500*tan(0.121) ≈ 62.7 m
        reg = _registration_one(building_x_px=480.0, forward_m=500.0,
                                contour_top_y=200.0)
        bld = _building("b1", height=80.0)
        rec = HeightTraceRecorder()
        out = estimate_heights_from_registration(
            cap, reg, [bld], camera_height_m=1.7, trace=rec)

        stages_seen = [e["stage"] for e in rec.events]
        assert "building_start" in stages_seen
        # No mask, so roof_y_from_mask still fires (mask_available=False).
        assert "roof_y_from_mask" in stages_seen
        # No mask roof_y, so contour fallback path: pinhole_math fires.
        assert "pinhole_math" in stages_seen
        assert "geometric_y_gate" in stages_seen
        assert stages_seen[-1] == "emit"
        assert len(out) == 1
        assert out[0].estimated_height_m > 30.0

    def test_drop_no_projection_records(self):
        cap = _captured()
        # Registration with NO projection for this building → drop_no_projection.
        reg = {
            "contour": np.full(960, 200.0, dtype=np.float32),
            "best_offset": 0.0,
            "best_score": 5.0,
            "projections": [],
            "all_projections": [],
        }
        bld = _building("b1", height=80.0)
        rec = HeightTraceRecorder()
        out = estimate_heights_from_registration(
            cap, reg, [bld], camera_height_m=1.7, trace=rec)
        assert out == []
        stages = [e["stage"] for e in rec.events]
        assert "drop_no_projection" in stages

    def test_drop_closest_in_bin_records(self):
        cap = _captured()
        # Two projections in the same column bin (within ±15 px), the second
        # much farther: it should be dropped by the closest-in-bin gate.
        reg = {
            "contour": np.full(960, 200.0, dtype=np.float32),
            "best_offset": 0.0,
            "best_score": 5.0,
            "projections": [
                {"feature_id": "near", "x_px": 480.0, "forward_m": 100.0,
                 "lateral_m": 0.0, "match_residual_px": 1.0},
                {"feature_id": "far", "x_px": 485.0, "forward_m": 500.0,
                 "lateral_m": 0.0, "match_residual_px": 1.0},
            ],
            "all_projections": [
                {"feature_id": "near", "x_px": 480.0, "forward_m": 100.0},
                {"feature_id": "far", "x_px": 485.0, "forward_m": 500.0},
            ],
        }
        bld_near = _building("near", height=80.0)
        bld_far = _building("far", height=80.0)
        rec = HeightTraceRecorder(only_feature_id="far")
        out = estimate_heights_from_registration(
            cap, reg, [bld_near, bld_far], camera_height_m=1.7, trace=rec)

        far_emitted = [e for e in out if e.feature_id == "far"]
        assert far_emitted == []
        stages = [e["stage"] for e in rec.events]
        assert "drop_closest_in_bin" in stages
        # Margin recorded should be ~400 m (500 - 100).
        drop_event = next(e for e in rec.events
                          if e["stage"] == "drop_closest_in_bin")
        assert drop_event["margin_m"] == pytest.approx(400.0, abs=1.0)
