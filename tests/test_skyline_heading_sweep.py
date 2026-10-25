"""Unit tests for the pano heading-offset sweep (``coastline_registration``).

``sweep_pano_heading_offset`` scores every candidate offset with
``score_pano_offset_keypoints``. The tests build a pano whose water band sits
where a sea-level keypoint at a known distance projects, only in the columns the
keypoints reach at the true offset, and check the sweep finds that offset.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from city2stl.skyline.coastline_registration import (
    score_pano_offset_keypoints,
    sweep_pano_heading_offset,
)

H, W = 240, 360
DIST_M = 100.0
TRUE_OFFSET = 30.0
SEED = (25.77, -80.19)


def _expected_top_y(dist_m: float) -> int:
    focal = H / (2.0 * math.tan(math.radians(37.5)))
    return int(round(H / 2.0 + 1.7 / dist_m * focal))


def _scene():
    headings = np.arange(W, dtype=np.float64)          # column i is labelled i degrees
    mask = np.zeros((H, W), dtype=bool)
    cols = np.arange(95, 146, 5)
    mask[_expected_top_y(DIST_M):, cols] = True        # water only in the keypoints' columns
    # the keypoints' true bearings are the labels plus the spin's error
    kps = [{"bearing_deg": float(b) + TRUE_OFFSET, "distance_m": DIST_M} for b in cols]
    return kps, mask, headings


def test_sweep_recovers_the_offset():
    kps, mask, headings = _scene()
    best, cand, scores = sweep_pano_heading_offset(kps, mask, headings, *SEED)
    assert best == pytest.approx(TRUE_OFFSET)
    assert len(cand) == len(scores) == 360
    assert scores.max() == pytest.approx(1.0, abs=0.05)
    assert scores[2] == 0.0                            # 2 degrees off: every keypoint lands on a dry column


def test_sweep_step_sets_the_candidate_grid():
    kps, mask, headings = _scene()
    best, cand, _ = sweep_pano_heading_offset(kps, mask, headings, *SEED, step_deg=10.0)
    assert len(cand) == 36
    assert best == pytest.approx(TRUE_OFFSET)


def test_score_is_zero_on_degenerate_input():
    kps, mask, headings = _scene()
    assert score_pano_offset_keypoints([], mask, headings, *SEED, TRUE_OFFSET) == 0.0
    assert score_pano_offset_keypoints(kps, mask, headings[:-1], *SEED, TRUE_OFFSET) == 0.0
    near = [{"bearing_deg": 120.0 + TRUE_OFFSET, "distance_m": 0.5}]   # under 1 m is skipped
    assert score_pano_offset_keypoints(near, mask, headings, *SEED, TRUE_OFFSET) == 0.0


def test_far_keypoints_weigh_less_than_near_ones():
    _, mask, headings = _scene()
    good = {"bearing_deg": 120.0 + TRUE_OFFSET, "distance_m": DIST_M}
    # a far keypoint over a dry column scores 0 but counts with weight 200 / 2000
    far_dry = {"bearing_deg": 200.0 + TRUE_OFFSET, "distance_m": 2000.0}
    s = score_pano_offset_keypoints([good, far_dry], mask, headings, *SEED, TRUE_OFFSET)
    assert s == pytest.approx(1.0 / 1.1, abs=0.02)
