"""city2stl.registration.correlate: NCC surface, peak shortlist, edges, height channel."""

from __future__ import annotations

import numpy as np
import pytest

from city2stl.registration.correlate import edges, find_peaks, height_channel, ncc_surface


def _pattern(n=64, seed=0):
    """A blocky random field with a little structure, like a block map."""
    rng = np.random.default_rng(seed)
    small = (rng.random((n // 4, n // 4)) > 0.55).astype(np.float64)
    return np.kron(small, np.ones((4, 4)))


def test_ncc_surface_finds_a_known_shift():
    n = 64
    fixed = _pattern(n)
    # Template = the fixed image cut to a centred window, then moved by (+5, -7).
    support = np.zeros((n, n))
    support[16:48, 16:48] = 1.0
    template = np.roll(fixed, (5, -7), axis=(0, 1)) * support
    surf = ncc_surface(fixed, template, support)
    assert surf.shape == (n, n)
    peaks = find_peaks(surf, separation=4, count=3)
    best = peaks[0]
    # Zero shift sits at the centre (n//2); the template content moved by (5, -7), so the
    # recovered displacement is its negative.
    dy, dx = best["y"] - n // 2, best["x"] - n // 2
    assert (abs(dy), abs(dx)) == (5, 7)
    assert (dy, dx) == (-5, 7)
    assert surf[best["y"], best["x"]] == pytest.approx(1.0, abs=1e-6)
    assert best["z"] > peaks[1]["z"]


def test_ncc_surface_zero_shift_is_unity_at_centre():
    n = 48
    fixed = _pattern(n, seed=3)
    support = np.ones((n, n))
    surf = ncc_surface(fixed, fixed * support, support)
    assert surf[n // 2, n // 2] == pytest.approx(1.0, abs=1e-6)
    assert surf.max() == pytest.approx(1.0, abs=1e-6)
    assert np.all(np.abs(surf) <= 1.0 + 1e-6)


def test_ncc_surface_flat_template_scores_zero():
    n = 32
    fixed = _pattern(n, seed=1)
    support = np.ones((n, n))
    surf = ncc_surface(fixed, np.full((n, n), 3.0), support)
    assert np.all(surf == 0.0)


def test_ncc_surface_accepts_precomputed_ffts():
    n = 32
    fixed = _pattern(n, seed=2)
    support = np.zeros((n, n))
    support[8:24, 8:24] = 1.0
    template = np.roll(fixed, (2, 3), axis=(0, 1)) * support
    ffts = (np.fft.rfft2(fixed), np.fft.rfft2(fixed * fixed))
    np.testing.assert_allclose(ncc_surface(fixed, template, support),
                               ncc_surface(fixed, template, support, fixed_ffts=ffts))


def test_find_peaks_orders_and_separates():
    surf = np.zeros((40, 40))
    surf[10, 10] = 5.0
    surf[10, 12] = 4.5   # inside the 3 px suppression radius of the first
    surf[30, 30] = 4.0
    peaks = find_peaks(surf, separation=3, count=3)
    assert [(p["y"], p["x"]) for p in peaks[:2]] == [(10, 10), (30, 30)]
    assert (10, 12) not in [(p["y"], p["x"]) for p in peaks]
    zs = [p["z"] for p in peaks]
    assert zs == sorted(zs, reverse=True)
    assert len(peaks) == 3
    mean, std = surf.mean(), surf.std()
    assert peaks[0]["z"] == pytest.approx((5.0 - mean) / std)


def test_find_peaks_constant_surface_does_not_divide_by_zero():
    peaks = find_peaks(np.ones((10, 10)), separation=2, count=2)
    assert len(peaks) == 2
    assert all(p["z"] == 0.0 for p in peaks)


def test_find_peaks_stops_suppressing_at_the_borders():
    surf = np.zeros((8, 8))
    surf[0, 0] = 1.0
    peaks = find_peaks(surf, separation=20, count=1)
    assert (peaks[0]["y"], peaks[0]["x"]) == (0, 0)


def test_edges_outlines_a_square():
    mask = np.zeros((10, 10), dtype=np.uint8)
    mask[3:7, 3:7] = 1
    e = edges(mask)
    assert e.dtype == np.float32
    assert e.max() == 1.0 and e.min() == 0.0
    # Cells on both sides of every 0/1 transition are marked; the far interior and the
    # far exterior are not.
    assert e[3, 4] == 1.0 and e[2, 4] == 1.0
    assert e[4, 4] == 0.0
    assert e[0, 0] == 0.0 and e[9, 9] == 0.0
    assert edges(np.zeros((5, 5))).sum() == 0.0
    assert edges(np.ones((5, 5))).sum() == 0.0


def test_height_channel_cleans_and_normalises():
    raw = np.array([[np.nan, 2.0, -3.0], [np.inf, 4.0, -np.inf]])
    out = height_channel(raw)
    assert out.dtype == np.float32
    assert out.max() == 1.0
    np.testing.assert_allclose(out, [[0, 0.5, 0], [0, 1.0, 0]])


def test_height_channel_all_ground_stays_zero():
    out = height_channel(np.full((3, 3), np.nan))
    assert out.shape == (3, 3) and not out.any()
