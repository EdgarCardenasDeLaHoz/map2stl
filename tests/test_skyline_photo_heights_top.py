"""T17 stepped tops and T30 photo weighting in ``photo_heights``, on synthetic photos and poses."""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest

from city2stl.skyline import photo_heights as ph
from city2stl.skyline import skyline_match as sm

LAT0, LON0 = 25.77, -80.19


def _city(seed=5, n=40):
    rng = np.random.default_rng(seed)
    verts, hs = [], []
    for _ in range(n):
        cx, cy = rng.uniform(-1000, 1000, 2)
        w, d = rng.uniform(20, 60, 2)
        verts.append(np.array([[cx - w, cy - d], [cx + w, cy - d], [cx + w, cy + d],
                               [cx - w, cy + d], [cx - w, cy - d]]))
        hs.append(rng.uniform(40, 250))
    return sm.Towers(LAT0, LON0, verts, np.array(hs), [f"t{i}" for i in range(n)])


def _photo(towers, cam_xy, heading_deg, hfov_deg, width=2400, height=1600, tilt_deg=2.5):
    model = sm.predicted_outline(towers, cam_xy)
    f = (width / 2) / math.tan(math.radians(hfov_deg / 2))
    xs = np.arange(width) + 0.5
    az = np.degrees(np.arctan((xs - width / 2) / f))
    bins = np.round((heading_deg + az) / sm.BIN_DEG).astype(int) % sm.N_BINS
    el = model[bins]
    y = np.where(el > 0, height / 2 - f * np.tan(np.radians(el + tilt_deg)), np.nan)
    return sm.PhotoProfile(y, width, height)


CAMS = [((-2400.0, 300.0), 95.0), ((-2300.0, 100.0), 98.0), ((-2500.0, 500.0), 92.0)]
FOV = 40.0


def _measure(towers, prof, cam, heading):
    lat, lon = towers.to_ll(*cam)
    return ph.measure_towers(prof, towers, ph.PhotoPose(lat, lon, heading, FOV))


def _step(prof, run, start, share, drop_px):
    """Lower the tower's top by ``drop_px`` rows over every column of its run except a
    ``share`` stretch starting at fraction ``start`` (the tall step)."""
    y = prof.y_top.copy()
    cols = np.arange(run[0], run[1] + 1)
    a = run[0] + int(start * len(cols))
    keep = (cols >= a) & (cols < a + int(share * len(cols)))
    y[cols[~keep]] += drop_px
    return sm.PhotoProfile(y, prof.width, prof.height)


def _photos(towers, drops=(None, None, None)):
    """Per photo: measures, optionally with a stepped tower. Returns (per-photo ms, target index)."""
    base = [_photo(towers, c, h, FOV) for c, h in CAMS]
    ms0 = [_measure(towers, p, c, h) for p, (c, h) in zip(base, CAMS, strict=True)]
    common = set.intersection(*({m.index for m in ms if m.dist_m > 800 and m.x1 - m.x0 > 60}
                                for ms in ms0))
    assert common, "synthetic scene must show a wide tower in all photos"
    target = sorted(common)[0]
    out = []
    for (cam, h), prof, ms, drop, start in zip(CAMS, base, ms0, drops, (0.1, 0.4, 0.65), strict=True):
        if drop:
            m = next(m for m in ms if m.index == target)
            prof = _step(prof, (m.x0, m.x1), start, 0.25, drop)
        out.append(_measure(towers, prof, cam, h))
    return out, target


def _readings(per_photo, target):
    med, peak = [], []
    for ms in per_photo:
        med.append(ph.loo_heights(ms)[target])
        peak.append(ph.loo_heights(ms, top="peak")[target])
    return med, peak


def test_stepped_tower_picks_peak():
    towers = _city()
    per_photo, target = _photos(towers, drops=(40, 70, 25))
    med, peak = _readings(per_photo, target)
    truth = float(towers.height_m[target])
    assert max(med) < truth - 5                       # the median landed on a lower step
    assert all(abs(p - truth) < 6 for p in peak)      # the peak is the real roof
    assert ph.choose_top(med, peak) == "peak"
    for ms in per_photo:
        m = next(m for m in ms if m.index == target)
        assert m.peak_row < m.roof_row                # median kept as a diagnostic


def test_flat_top_is_unchanged():
    towers = _city()
    per_photo, target = _photos(towers)
    med, peak = _readings(per_photo, target)
    truth = float(towers.height_m[target])
    assert all(abs(p - truth) < 6 for p in med)
    assert abs(np.median(peak) - np.median(med)) < ph.PEAK_MIN_GAIN_M
    assert ph.choose_top(med, peak) == "median"


def test_single_photo_keeps_the_median():
    towers = _city()
    per_photo, target = _photos(towers, drops=(40, None, None))
    med, peak = _readings(per_photo[:1], target)
    assert peak[0] > med[0] + ph.PEAK_MIN_GAIN_M      # a step is visible...
    assert ph.choose_top(med, peak) == "median"       # ...but one photo cannot confirm it


def test_photos_that_disagree_on_the_peak_keep_the_median():
    assert ph.choose_top([100.0, 101.0], [140.0, 190.0]) == "median"


# ---- T30 -----------------------------------------------------------------

def test_low_deviation_photo_dominates():
    assert ph.combine_photo_heights([100.0, 150.0], [5.0, 50.0]) == 100.0
    assert ph.combine_photo_heights([100.0, 150.0, 160.0], [30.0, 6.0, 40.0]) == 150.0
    # three photos: one trusted photo outweighs two poor ones that agree with each other
    assert ph.combine_photo_heights([100.0, 150.0, 152.0], [5.0, 60.0, 60.0]) == 100.0


@pytest.mark.parametrize("hs", [[120.0], [100.0, 150.0], [90.0, 100.0, 170.0], [80.0, 90.0, 140.0, 200.0]])
def test_equal_deviations_reduce_to_the_median(hs):
    for dev in (8.0, 30.0):
        assert ph.combine_photo_heights(hs, [dev] * len(hs)) == pytest.approx(float(np.median(hs)))
    assert ph.combine_photo_heights(hs) == pytest.approx(float(np.median(hs)))


def test_deviation_floor_and_missing():
    assert ph.photo_weight(1.0) == ph.photo_weight(5.0) == pytest.approx(0.2)
    assert ph.photo_weight(None) == pytest.approx(1 / ph.DEFAULT_DEV_M)


def _pipeline():
    path = Path(__file__).resolve().parents[1] / "city2stl" / "skyline" / "scripts" / "17_photo_pipeline.py"
    spec = importlib.util.spec_from_file_location("photo_pipeline_17", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_building_row_weights_and_keeps_median_result():
    pp = _pipeline()
    towers = _city(n=3)
    keys = {0: "k"}
    reads = [(155.0, 235.0, 6.0), (190.0, 238.0, 9.0), (160.0, 240.0, 40.0)]
    row = pp._building_row(towers, keys, 0, reads, True, True, 0, None)
    assert row["top"] == "peak" and 234 <= row["photo_m"] <= 240
    assert row["photo_median_m"] == 160.0
    old = pp._building_row(towers, keys, 0, reads, False, False, 0, None)
    assert old["top"] == "median" and old["photo_m"] == 160.0
    one = pp._building_row(towers, keys, 0, reads[:1], True, True, 0, None)
    assert one["photo_m"] == 155.0 and one["top"] == "median"
