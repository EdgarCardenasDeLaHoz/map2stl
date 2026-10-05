"""F-DET1 early-out counts buildings, not mask fragments (T26)."""

import cv2
import numpy as np

from city2stl.skyline._pano.detect import _count_skyline_buildings


def _city(heights, width=40, h=400, base=300):
    """One continuous building band: towers side by side, tops at ``base - height``."""
    m = np.zeros((h, width * len(heights)), bool)
    for k, t in enumerate(heights):
        m[base - t:base, k * width:(k + 1) * width] = True
    return m


def test_continuous_city_counts_its_towers():
    m = _city([60, 120, 40, 150, 90, 30, 110, 70])
    assert cv2.connectedComponents(m.astype(np.uint8))[0] - 1 == 1   # the old count: 1
    assert _count_skyline_buildings(m) == 8                          # one per tower


def test_empty_and_wall():
    assert _count_skyline_buildings(np.zeros((100, 200), bool)) == 0
    wall = np.zeros((100, 200), bool)
    wall[:, :] = True                       # a wall filling the frame: one building
    assert _count_skyline_buildings(wall) == 1


def test_separate_flat_blobs_still_count_once_each():
    """Low flat buildings (no peak above the prominence) count as before: one per blob."""
    m = np.zeros((400, 600), bool)
    for x0 in (20, 220, 420):
        m[296:300, x0:x0 + 100] = True     # 4 px tall: below the 2 % prominence
    assert _count_skyline_buildings(m) == 3


def test_never_below_the_component_count():
    rng = np.random.default_rng(3)
    for _ in range(20):
        m = rng.random((60, 120)) > 0.9
        n_old = cv2.connectedComponents(m.astype(np.uint8))[0] - 1
        assert _count_skyline_buildings(m) >= n_old


def test_jagged_mask_edge_is_not_a_city():
    """1-2 px noise on a flat roofline stays below the prominence: still one building."""
    m = np.zeros((400, 800), bool)
    rng = np.random.default_rng(0)
    for x in range(800):
        m[200 + int(rng.integers(0, 3)):, x] = True
    assert _count_skyline_buildings(m) == 1
