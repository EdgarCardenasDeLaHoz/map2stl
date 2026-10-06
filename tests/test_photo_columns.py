"""Per-column photo placement against a trusted reference, on a synthetic city."""

import math

import numpy as np

from city2stl.skyline import photo_columns as pc
from city2stl.skyline import skyline_match as sm

LAT0, LON0 = 10.40, -75.55
KX = sm.M_PER_DEG_LAT * math.cos(math.radians(LAT0))


def _feature(fid, cx, cy, w=25.0, d=25.0, h=None, src="default"):
    ring = [[cx - w, cy - d], [cx + w, cy - d], [cx + w, cy + d], [cx - w, cy + d], [cx - w, cy - d]]
    ll = [[LON0 + x / KX, LAT0 + y / sm.M_PER_DEG_LAT] for x, y in ring]
    return {"geometry": {"type": "Polygon", "coordinates": [ll]},
            "properties": {"feature_id": fid, "name": fid, "height_m": h, "height_source": src}}


def _city(seed=0, n=40):
    """Towers 50-200 m tagged, plus untagged blocks whose readings disagree."""
    rng = np.random.default_rng(seed)
    feats, rows = [], []
    for i in range(n):
        cx, cy = rng.uniform(-1000, 1000, 2)
        if i % 4:
            feats.append(_feature(f"t{i}", cx, cy, *rng.uniform(15, 40, 2), rng.uniform(50, 200), "osm_tag"))
        else:
            feats.append(_feature(f"u{i}", cx, cy, *rng.uniform(15, 40, 2)))
            rows.append({"feature_id": f"u{i}", "per_seed_median_m": {"street": rng.uniform(30, 90)}})
    return feats, rows


def _photo(ref, cam_xy, heading, hfov, h_cam=2.0, width=1200, height=800, tilt=1.5):
    """Skyline a pinhole camera sees of the trusted towers (untrusted ones at their mid height)."""
    mid = sm.Towers(ref.towers.lat0, ref.towers.lon0, ref.towers.verts,
                    np.where(ref.trusted, ref.lo_hi[:, 0], ref.lo_hi.mean(1)), ref.towers.names)
    model = sm.predicted_outline(mid, cam_xy, h_cam)
    f = (width / 2) / math.tan(math.radians(hfov / 2))
    az = np.degrees(np.arctan((np.arange(width) + 0.5 - width / 2) / f))
    el = model[np.round((heading + az) / sm.BIN_DEG).astype(int) % sm.N_BINS]
    y = np.where(el > 0, height / 2 - f * np.tan(np.radians(el + tilt)), np.nan)
    return pc.photo_columns(y, width, height)


def test_reference_splits_by_trust():
    feats, rows = _city()
    site = {"elevated_seeds": ["drone"],
            "known_heights_m": {"Pub": {"lat": LAT0, "lon": LON0, "height_m": 222.0}}}
    feats.append(_feature("pub", 0.0, 0.0, h=150.0, src="osm_tag"))
    feats.append(_feature("d1", 1500.0, 0.0))
    rows.append({"feature_id": "d1", "per_seed_median_m": {"drone": 100.0, "street": 95.0}})
    feats.append(_feature("d2", -1500.0, 0.0))
    rows.append({"feature_id": "d2", "per_seed_median_m": {"drone": 47.0, "street": 118.0}})
    ref = pc.trusted_reference(feats, rows, site, min_earn_height_m=0.0)
    towers, mask, lo_hi = ref
    by = dict(zip(towers.names, range(len(mask)), strict=True))
    assert mask[by["Pub"]] and lo_hi[by["Pub"], 0] == 222.0          # published beats the tag
    assert mask[by["d1"]] and ref.source[by["d1"]] == "drone"         # confirmed drone reading
    assert not mask[by["d2"]] and lo_hi[by["d2"], 0] < 47 and lo_hi[by["d2"], 1] > 118
    assert mask[by["t1"]] and not mask[by["u0"]]
    low = pc.trusted_reference(feats, rows, site, min_earn_height_m=120.0)
    assert not low.trusted[by["d1"]] and np.allclose(low.lo_hi[by["d1"]], (80.0, 120.0))


def test_synthetic_skyline_recovers_heading_and_tilt():
    feats, rows = _city()
    ref = pc.trusted_reference(feats, rows)
    cam = (300.0, -1800.0)
    for heading in (5.0, 20.0, 350.0):
        cols = _photo(ref, cam, heading, 40.0)
        s = pc.score_poses(cols, pc.model_bins(ref, cam), (30.0, 40.0, 50.0))
        i, hd, _ = s.best()
        assert i == 1
        assert abs((hd - heading + 180) % 360 - 180) <= 0.2
        k = int(round(hd / sm.BIN_DEG))
        assert abs(s.tilt_deg[i, k] - 1.5) < 0.1


def test_true_camera_ranks_first():
    feats, rows = _city(seed=3)
    ref = pc.trusted_reference(feats, rows)
    cam = (-200.0, -1700.0)
    cols = _photo(ref, cam, 10.0, 45.0)
    rng = np.random.default_rng(1)
    cams = [cam] + [tuple(p) for p in rng.uniform(-2500, 2500, (25, 2))]
    s = pc.rank_cameras(cols, ref, cams, 45.0, h_cams=(2.0,))
    assert np.argmax(s) == 0
    assert s[0] > np.sort(s)[-2] + 10
