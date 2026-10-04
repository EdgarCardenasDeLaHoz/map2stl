"""F-WEB2 step 5c: locating a photo from its skyline, on a synthetic city."""

import math

import numpy as np
import pytest

from city2stl.skyline import skyline_match as sm

LAT0, LON0 = 25.77, -80.19


def _city(seed=0, n=40):
    """Towers 20-60 m wide, 40-250 m tall, scattered over a 2 x 2 km downtown."""
    rng = np.random.default_rng(seed)
    verts, hs = [], []
    for _ in range(n):
        cx, cy = rng.uniform(-1000, 1000, 2)
        w, d = rng.uniform(20, 60, 2)
        verts.append(np.array([[cx - w, cy - d], [cx + w, cy - d], [cx + w, cy + d], [cx - w, cy + d],
                               [cx - w, cy - d]]))
        hs.append(rng.uniform(40, 250))
    return sm.Towers(LAT0, LON0, verts, np.array(hs), [f"t{i}" for i in range(n)])


def _photo(towers, cam_xy, heading_deg, hfov_deg, width=1200, height=800, tilt_deg=2.0):
    """Skyline profile a pinhole camera at ``cam_xy`` would see (no segmentation noise)."""
    model = sm.predicted_outline(towers, cam_xy)
    f = (width / 2) / math.tan(math.radians(hfov_deg / 2))
    xs = np.arange(width) + 0.5
    az = np.degrees(np.arctan((xs - width / 2) / f))
    bins = np.round((heading_deg + az) / sm.BIN_DEG).astype(int) % sm.N_BINS
    el = model[bins]
    y = np.where(el > 0, height / 2 - f * np.tan(np.radians(el + tilt_deg)), np.nan)
    return sm.PhotoProfile(y, width, height)


def test_outline_is_topmost_tower():
    t = sm.Towers(LAT0, LON0,
                  [np.array([[-20, 990], [20, 990], [20, 1010], [-20, 1010], [-20, 990]]),
                   np.array([[-20, 1990], [20, 1990], [20, 2010], [-20, 2010], [-20, 1990]])],
                  np.array([50.0, 300.0]), ["near low", "far tall"])
    model, own = sm.predicted_outline(t, (0.0, 0.0), owners=True)
    b = int(round(0 / sm.BIN_DEG))
    assert own[b] == 1                                    # the far tall tower tops the skyline
    # elevation of the roof over the nearest footprint corner (20 m off-axis at 1990 m)
    assert abs(model[b] - math.degrees(math.atan2(298.0, math.hypot(20, 1990)))) < 1e-6


@pytest.mark.slow  # ~1 min: a full grid search over 3600 headings and 10 FOVs
def test_locate_finds_the_camera():
    towers = _city()
    cam, heading, fov = (-2400.0, 300.0), 95.0, 40.0
    prof = _photo(towers, cam, heading, fov)
    hits = sm.locate(prof, towers, radius_m=3500.0, step_m=300.0, top=4)
    best = hits[0]
    x, y = towers.to_xy(best.lat, best.lon)
    assert math.hypot(x - cam[0], y - cam[1]) < 300
    assert abs((best.heading_deg - heading + 180) % 360 - 180) < 2.0
    assert abs(best.hfov_deg - fov) / fov < 0.2
    assert best.misfit < 0.2          # pixel-to-bin resampling alone leaves ~0.13
    assert abs(best.tilt_deg + 2.0) < 1.0 or abs(best.tilt_deg - 2.0) < 1.0


def test_photo_profile_ignores_trees_and_open_sky():
    sky = np.zeros((10, 4), bool)
    sky[:3] = True
    bld = np.zeros((10, 4), bool)
    bld[3:, 0] = True                 # column 0: a tower under the sky
    bld[3:, 2] = False                # column 2: something else (tree)
    sky[:, 3] = True                  # column 3: all sky
    prof = sm.photo_profile(sky, bld)
    assert prof.y_top[0] == 3 and np.isnan(prof.y_top[2]) and np.isnan(prof.y_top[3])


def _outline_loop(t, cam_xy, h_cam=2.0, min_dist_m=60.0):
    """The original per-tower loop, kept here as the reference for the vectorised version."""
    out = np.zeros(sm.N_BINS)
    own = np.full(sm.N_BINS, -1)
    for i, (v, H) in enumerate(zip(t.verts, t.height_m, strict=True)):
        dx, dy = v[:, 0] - cam_xy[0], v[:, 1] - cam_xy[1]
        dmin = float(np.hypot(dx, dy).min())
        if dmin < min_dist_m:
            continue
        b = np.degrees(np.arctan2(dx, dy)) % 360.0
        rel = (b - b[0] + 180.0) % 360.0 - 180.0
        lo, hi = b[0] + rel.min(), b[0] + rel.max()
        elev = math.degrees(math.atan2(H - h_cam, dmin))
        idx = np.arange(math.floor(lo / sm.BIN_DEG), math.ceil(hi / sm.BIN_DEG) + 1) % sm.N_BINS
        win = elev > out[idx]
        out[idx[win]] = elev
        own[idx[win]] = i
    return out, own


def test_vectorised_outline_matches_the_loop():
    towers = _city(seed=3, n=60)
    for cam in [(-2400.0, 300.0), (0.0, -1500.0), (900.0, 50.0)]:   # last one inside downtown
        ref_out, ref_own = _outline_loop(towers, cam)
        out, own = sm.predicted_outline(towers, cam, owners=True)
        assert np.allclose(out, ref_out)
        assert (own == ref_own).all()
