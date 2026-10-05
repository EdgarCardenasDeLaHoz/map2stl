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


def test_photo_heights_recover_towers_leave_one_out():
    """Measured from a synthetic photo with unknown tilt, each tower comes back from the others."""
    from city2stl.skyline import photo_heights as ph

    towers = _city(seed=5, n=40)
    cam, heading, fov, tilt = (-2400.0, 300.0), 95.0, 40.0, 2.5
    prof = _photo(towers, cam, heading, fov, width=2400, height=1600, tilt_deg=tilt)
    lat, lon = towers.to_ll(*cam)
    ms = ph.measure_towers(prof, towers, ph.PhotoPose(lat, lon, heading, fov))
    assert len(ms) >= 6
    est = ph.loo_heights(ms)
    err = [abs(est[m.index] - towers.height_m[m.index]) for m in ms if m.index in est]
    assert np.median(err) < 5.0          # 0.1-deg bins and pixel rows are the only noise


# ── C2: heights for untagged buildings from 2+ located photos (T16) ─────────

def _box(cx, cy, w=25.0):
    return np.array([[cx - w, cy - w], [cx + w, cy - w], [cx + w, cy + w], [cx - w, cy + w],
                     [cx - w, cy - w]])


def _c2_scene():
    """Tagged anchors, an untagged tower forming the skyline (U) and a low untagged
    building in front of it (D), photographed from three spots south of the city."""
    tagged = [(-700, 400, 120), (-350, 650, 160), (350, 500, 140), (700, 300, 100),
              (-150, 900, 200), (500, 900, 180)]
    untag = [(0, 400, 230.0), (60, -600, 25.0)]
    verts = [_box(x, y) for x, y, _ in tagged] + [_box(x, y) for x, y, _ in untag]
    hs = np.array([h for *_, h in tagged] + [h for *_, h in untag], float)
    n = len(tagged)
    world = sm.Towers(LAT0, LON0, verts, hs, [f"t{i}" for i in range(n)] + ["U", "D"])
    anchors = sm.Towers(LAT0, LON0, verts[:n], hs[:n], world.names[:n])
    table = sm.Towers(LAT0, LON0, verts[n:], np.full(2, np.nan), ["U", "D"])
    return world, anchors, table


def _c2_implied(world, anchors, table, cams):
    from city2stl.skyline import photo_heights as ph

    per = []
    for cx in cams:
        cam = (cx, -1800.0)
        hd = math.degrees(math.atan2(-cx, 2200.0)) % 360
        prof = _photo(world, cam, hd, 50.0)
        pose = ph.PhotoPose(*world.to_ll(*cam), hd, 50.0)
        ms = ph.measure_towers(prof, anchors, pose)
        tilt, h = ph.fit_tilt_height(ms, np.array([m.osm_height_m for m in ms]))
        per.append(ph.implied_heights(prof, pose, table, tilt, h))
    return per


def test_untagged_height_from_agreeing_photos():
    from city2stl.skyline import photo_heights as ph

    world, anchors, table = _c2_scene()
    per = _c2_implied(world, anchors, table, (-500, 0, 450))
    est = ph.agreed_heights(per)
    assert 0 in est                                         # U: the skyline-former
    h, n, spread = est[0]
    assert h == pytest.approx(230.0, rel=0.02) and n == 3
    assert 1 not in est                                     # D: implied heights disagree


def test_untagged_needs_two_photos():
    from city2stl.skyline import photo_heights as ph

    world, anchors, table = _c2_scene()
    assert ph.agreed_heights(_c2_implied(world, anchors, table, (0,))) == {}


def test_agreed_heights_rule():
    from city2stl.skyline import photo_heights as ph

    # within max(3 m, 10 %) of the median: kept; one photo far off: dropped
    assert ph.agreed_heights([{7: 100.0}, {7: 108.0}])[7][0] == pytest.approx(104.0)
    assert 7 not in ph.agreed_heights([{7: 100.0}, {7: 130.0}])
    assert ph.agreed_heights([{7: 20.0}, {7: 22.5}])[7][1] == 2       # 3 m floor
    assert ph.agreed_heights([{7: -5.0}, {7: 50.0}]) == {}             # <= 0 ignored


def test_untagged_table_skips_tagged_and_small():
    from city2stl.skyline import photo_heights as ph

    def feat(src, size_deg):
        x0, y0 = -80.19, 25.77
        ring = [[x0, y0], [x0 + size_deg, y0], [x0 + size_deg, y0 + size_deg],
                [x0, y0 + size_deg], [x0, y0]]
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"height_source": src}}

    feats = [feat("osm_tag", 3e-4), feat("default", 3e-4), feat("default", 5e-5)]
    t = ph.untagged_table(feats)
    assert len(t.verts) == 1 and np.isnan(t.height_m).all()          # tagged and tiny left out


def test_pipeline_untagged_rows_and_worker_step(monkeypatch):
    """17_photo_pipeline --untagged: workers store implied heights per photo; finish keeps
    the agreeing ones from kept photos and scores them (truth mocked: no network)."""
    import importlib
    from types import SimpleNamespace

    from city2stl.skyline import photo_heights as ph

    pl = importlib.import_module("city2stl.skyline.scripts.17_photo_pipeline")
    world, anchors, table = _c2_scene()
    monkeypatch.setitem(pl._W, "towers", anchors)
    monkeypatch.setitem(pl._W, "untagged", table)
    cx = 0.0
    hd = math.degrees(math.atan2(-cx, 2200.0)) % 360
    prof = _photo(world, (cx, -1800.0), hd, 50.0)
    imp = pl._implied_untagged(prof, ph.PhotoPose(*world.to_ll(cx, -1800.0), hd, 50.0),
                               5000.0, 2.0)
    assert float(imp["0"]) == pytest.approx(230.0, rel=0.02)

    # finish side: features whose untagged table equals ``table`` (same frame, same order)
    def feat(v):
        ring = [list(table.to_ll(x, y))[::-1] for x, y in v]
        return {"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
                "properties": {"height_source": "default"}}

    osm = {"buildings": {"features": [feat(v) for v in table.verts]}}
    monkeypatch.setattr(pl.bm, "footprint_truth",
                        lambda region, fps, prov: {k: {"status": "confirmed", "truth_m": 228.0}
                                                   for k in fps})
    results = [{"kept": True, "untagged": {"0": 231.0, "1": 25.0}},
               {"kept": True, "untagged": {"0": 229.0, "1": 80.0}},
               {"kept": False, "untagged": {"1": 25.0}}]       # not kept: ignored
    rows, summary = pl._untagged_rows(SimpleNamespace(region="miami"), results, anchors, osm)
    assert [r["index"] for r in rows] == [0]
    assert rows[0]["photo_m"] == pytest.approx(230.0) and rows[0]["truth_m"] == 228.0
    assert summary["buildings"] == 1 and summary["confirmed"] == 1
