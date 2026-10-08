"""F-DET6 in the region report: the adapter from footprint measurements to pipeline records."""

import numpy as np
import pytest
from shapely.geometry import Polygon

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline._core.height import aggregate_building_heights
from city2stl.skyline._core.types import BuildingRecord
from city2stl.skyline._pano import elevated as el
from city2stl.skyline.region_types import SkylinePoint

H, W = 100, 360


def _pano(offset_frame=0.0):
    frame = (np.arange(W) * 1.0 + offset_frame) % 360.0         # 1 deg per column
    labels = np.full((H, W), fd.SKY_CLASS, np.int16)
    labels[60:, :] = 21                                            # water
    return fd.Pano("seed_9", 10.4, -75.55, np.zeros((H, W, 3), np.uint8), labels, frame, 57.3, 0.0)


def _m(i, x0, x1, h, dist, base=True, frac=1.0, name=""):
    return fd.Measured(i, name, x0, x1, 40.0, 60.0, 61.0, dist, h, x1 - x0 + 1, base, None, frac, "sky")


def test_pano_result_centres_north_and_carries_one_box_per_footprint():
    pano = _pano()
    pose = fd.PanoPose(30.0, 80.0, 0.0, 0.3, W)                    # bearing = frame + 30
    ms = [_m(0, 10, 20, 120.0, 400.0, name="tower"), _m(1, 200, 210, 40.0, 900.0)]
    res = el.pano_result(SkylinePoint("seed_9", 10.4, -75.55, 0.0, "seed", 1.0), pano, pose, ms,
                         ["way/1", "way/2"], depth=np.zeros((H, W)))
    assert res.headings_per_col[W // 2] == 0.0                    # north mid-strip
    seg = next(s for s in res.matched_segments if s["matched_projection"]["feature_id"] == "way/1")
    assert seg["true_bearing_deg"] == 45.0                         # column 15: frame 15 + 30
    assert seg["height_m"] == 120.0 and seg["height_src"] == "footprint"
    assert res.headings_per_col[seg["mid_x"]] == seg["true_bearing_deg"]
    assert res.n_matched == 2 and res.geom_K == 80.0 * 57.3


def test_estimates_settle_lopsided_disputes_and_drop_even_ones():
    pano = _pano()
    pose = fd.PanoPose(0.0, 80.0, 0.0, 0.3, W)
    pf = fd.PositionFit(0.0, 0.0, pose, 0.3, 0.3, 0.4, 0.4, "recorded")
    seed = SkylinePoint("seed_9", 10.4, -75.55, 0.0, "seed", 1.0)
    near = el.ElevatedSeed("seed_1", pf, [_m(0, 10, 20, 26.0, 600.0)], ["way/7"],
                           el.pano_result(seed, pano, pose, [_m(0, 10, 20, 26.0, 600.0)], ["way/7"], None))
    far_m = _m(0, 10, 20, 111.0, 1116.0)                           # read the tower behind
    far = el.ElevatedSeed("seed_5", pf, [far_m], ["way/7"],
                          el.pano_result(seed, pano, pose, [far_m], ["way/7"], None))
    est = el.elevated_estimates([near, far])                       # the near seed outweighs 3x+
    assert [e.view_name for e in est] == ["seed_1_015"]
    rival_m = _m(0, 10, 20, 60.0, 650.0)                           # as near, twice the height
    rival = el.ElevatedSeed("seed_4", pf, [rival_m], ["way/7"],
                            el.pano_result(seed, pano, pose, [rival_m], ["way/7"], None))
    assert el.elevated_estimates([near, rival]) == []              # disputed: no drone height
    est = el.elevated_estimates([near])                            # one seed: kept
    assert est[0].view_name == "seed_1_015" and est[0].estimated_height_m == 26.0
    agg = aggregate_building_heights(est)
    assert agg[0]["feature_id"] == "way/7" and agg[0]["effective_height_m"] == 26.0


def test_footprints_from_records_keep_ids_and_tags():
    poly = Polygon([(-75.55, 10.40), (-75.549, 10.40), (-75.549, 10.401), (-75.55, 10.401)])
    rec = BuildingRecord("way/42", "Ravello", poly, 10.4005, -75.5495, 160.0, "osm_tag", 1200.0)
    fps, fids = el.footprints_from_records([rec, BuildingRecord("node/1", "", None, 0, 0, None, "", 0)])
    assert fids == ["way/42"] and fps[0].osm_height_m == 160.0 and fps[0].ring.shape == (5, 2)


def test_site_lists_the_cartagena_drone_seeds():
    from city2stl.skyline.region_data import _load_site_elevated_seeds

    assert _load_site_elevated_seeds("cartagena") == {"seed_1", "seed_4", "seed_5", "seed_6"}
    assert _load_site_elevated_seeds("miami") == set()


def test_chunked_upsample_gives_the_full_argmax_labels():
    """The full-resolution upsample of all 150 class scores needed 6.6 GB for one Commons
    photo; the chunked running maximum must give exactly the same labels, ties included."""
    torch = pytest.importorskip("torch")              # the cloud venv has no torch
    import torch.nn.functional as F

    from city2stl.skyline._core.segmentation import _upsampled_labels

    g = torch.Generator().manual_seed(0)
    logits = torch.randn(1, 150, 17, 23, generator=g)
    logits[0, 37] = logits[0, 5]                       # exact ties: the first class must win
    full = F.interpolate(logits, size=(70, 90), mode="bilinear", align_corners=False)[0].argmax(0)
    got = _upsampled_labels(logits, 70, 90)
    assert got.dtype == np.uint8 and got.shape == (70, 90)
    assert np.array_equal(got, full.numpy())


def test_trusted_drone_readings():
    from types import SimpleNamespace as N

    from city2stl.skyline._pano.elevated import trusted

    assert trusted(N(top_edge="sky", base_visible=True, visible_frac=1.0))
    assert not trusted(N(top_edge="depth", base_visible=True, visible_frac=1.0))
    assert trusted(N(top_edge="sky", base_visible=False, visible_frac=0.6))       # half shows
    assert not trusted(N(top_edge="sky", base_visible=False, visible_frac=0.3))
    assert trusted(N(top_edge="roof", base_visible=False, visible_frac=1.0, confidence=0.7,
                     dist_m=600.0))
    assert not trusted(N(top_edge="roof", base_visible=False, visible_frac=1.0, confidence=0.2,
                         dist_m=600.0))
    assert not trusted(N(top_edge="roof", base_visible=False, visible_frac=1.0, confidence=0.7,
                         dist_m=1500.0))                   # far: low roofs read the towers behind


def test_seed_page_lists_the_numbered_buildings():
    from types import SimpleNamespace

    from city2stl.skyline.html_report import _segments_table_html

    pr = SimpleNamespace(seed_lat=10.40693, seed_lon=-75.55608, matched_segments=[
        {"seed_index": 91, "true_bearing_deg": 102.0, "height_m": 95.0, "base_visible": True,
         "matched_projection": {"feature_id": "b0812", "name": "b0812", "distance_m": 289.0}}])
    page = _segments_table_html(SimpleNamespace(seed_lat=0, seed_lon=0), pr)
    assert 'id="seg-91"' in page and "b0812" in page and ">95<" in page and "10.40639" in page


def _sphere_colour(az, el):
    """A synthetic sphere: R and G encode azimuth (two harmonics, so the wrap is seamless) and
    B elevation; a mis-sampled pixel shows."""
    a = np.radians(az)
    return np.stack([127 + 120 * np.sin(a), 127 + 120 * np.cos(a), (el + 90) * 1.4], -1).clip(0, 255)


def _render_view(heading, pitch, f, w, h):
    from city2stl.skyline._pano.elevated import _camera_axes

    r, d, fw = _camera_axes(heading, pitch)
    yy, xx = np.mgrid[0:h, 0:w].astype(float)
    ray = (fw[None, None] + ((xx - w / 2) / f)[..., None] * r[None, None]
           + ((yy - h / 2) / f)[..., None] * d[None, None])
    ray /= np.linalg.norm(ray, axis=-1, keepdims=True)
    az = np.degrees(np.arctan2(ray[..., 0], ray[..., 1])) % 360
    el = np.degrees(np.arcsin(ray[..., 2]))
    return _sphere_colour(az, el).astype(np.uint8)


def test_sphere_pano_puts_every_pixel_at_its_direction_even_pointed_down():
    """Pinhole crops stitched side by side tore apart at the seams 36 deg down (seed_6); the
    reprojected pano samples each heading/elevation from the view that sees it best."""
    from city2stl.skyline._pano.elevated import sphere_pano

    fov, w = 30.0, 96
    f = 0.5 * w / np.tan(np.radians(fov / 2))
    views = {(float(hd), float(p)): _render_view(hd, p, f, w, w)
             for hd in range(0, 360, 30) for p in (-10, -36, -62)}
    pano = sphere_pano("t", 0.0, 0.0, views, fov)
    rows = np.arange(pano.height)
    el = pano.elevation_deg(rows + 0.5)
    az2, el2 = np.broadcast_arrays(pano.frame_heading[None, :], el[:, None])
    want = _sphere_colour(az2, el2)
    covered = pano.rgb.any(-1)
    assert covered.mean() > 0.999                       # slivers no view covers are filled
    err = np.abs(pano.rgb.astype(float) - want)[covered]
    assert np.median(err) <= 1.5 and np.percentile(err, 98) <= 6
    assert pano.elevation_deg(0) > 4 and pano.elevation_deg(pano.height - 1) < -76


def test_a_tilted_sphere_is_rebuilt_level_and_its_tilt_read_from_the_horizon():
    """seed_6's Photo Sphere was tilted ~1 deg (its sea horizon swung 1.06 deg around the
    circle); rebuilt with the tilt, every row is a true elevation again."""
    from city2stl.skyline import footprint_detect as fd
    from city2stl.skyline._pano.elevated import (
        _camera_axes,
        _tilt_rotation,
        horizon_tilt,
        sphere_pano,
    )

    tilt = (1.5, 75.0)
    Rt = _tilt_rotation(tilt)
    fov, w = 30.0, 96
    f = 0.5 * w / np.tan(np.radians(fov / 2))

    def view(hd, p):
        r, d, fw = _camera_axes(hd, p)
        yy, xx = np.mgrid[0:w, 0:w].astype(float)
        u = fw[None, None] + ((xx - w / 2) / f)[..., None] * r + ((yy - w / 2) / f)[..., None] * d
        u /= np.linalg.norm(u, axis=-1, keepdims=True)
        wdir = u @ Rt                                   # world direction seen by the tilted camera
        az = np.degrees(np.arctan2(wdir[..., 0], wdir[..., 1])) % 360
        el = np.degrees(np.arcsin(np.clip(wdir[..., 2], -1, 1)))
        lab = np.where(el > -0.5, fd.SKY_CLASS, 21).astype(np.int16)   # sky above, water below
        return _sphere_colour(az, el).astype(np.uint8), lab

    keys = [(float(hd), float(p)) for hd in range(0, 360, 30) for p in (8, -18)]
    vv = {k: view(*k) for k in keys}
    views = {k: v[0] for k, v in vv.items()}
    labels = {k: v[1] for k, v in vv.items()}
    raw = sphere_pano("t", 0.0, 0.0, views, fov, labels)
    t = horizon_tilt(raw, min_cols=100)
    assert t is not None and abs(t[1] - tilt[0]) < 0.3
    assert abs(((t[2] - tilt[1]) + 180) % 360 - 180) < 10
    level = sphere_pano("t", 0.0, 0.0, views, fov, labels, tilt_deg=(t[1], t[2]))
    el = level.elevation_deg(np.arange(level.height) + 0.5)
    az2, el2 = np.broadcast_arrays(level.frame_heading[None, :], el[:, None])
    err = np.abs(level.rgb.astype(float) - _sphere_colour(az2, el2))
    assert np.median(err) <= 1.5
    t2 = horizon_tilt(level, min_cols=100)
    assert t2 is not None and t2[1] < 0.3


def test_waterline_seed_fills_untrusted_footprints_with_trusted_roof_fits(monkeypatch):
    """A low drone's run that never reached the sky (Cartagena seed_5, Nautica/Ravello) gets the
    roof fit instead when that is trusted; a trusted run is kept; the fill weighs less."""
    from city2stl.skyline._pano import roof_fit as rf

    def roof(i, h, conf, dist=500.0):
        return rf.RoofMeasured(i, f"b{i}", 0, 9, 40.0, 60.0, 61.0, dist, h, 10, True, None, 1.0,
                               "roof", h, 2.0, 10, 0, conf)

    runs = [_m(0, 0, 9, 50.0, 400.0),                                       # trusted run: kept
            fd.Measured(1, "b1", 0, 9, 40.0, 60.0, 61.0, 450.0, 30.0, 10, True, None, 1.0, "depth"),
            fd.Measured(2, "b2", 0, 9, 40.0, 60.0, 61.0, 470.0, 20.0, 10, True, None, 1.0, "depth")]
    fits = [roof(0, 70.0, 0.9), roof(1, 150.0, 0.8), roof(2, 90.0, 0.2), roof(3, 60.0, 0.7),
            roof(4, 60.0, 0.9, dist=1500.0)]
    monkeypatch.setattr(fd, "measure_footprints", lambda *a, **k: list(runs))
    monkeypatch.setattr(rf, "fit_roof_heights", lambda *a, **k: list(fits))
    got = {m.footprint: m for m in el.measure_waterline(_pano(), fd.PanoPose(0, 80, 0, 0, W), [])}
    assert got[0].top_edge == "sky" and got[0].height_m == 50.0           # the run stays
    assert got[1].top_edge == "roof" and got[1].height_m == 150.0         # filled
    assert got[1].weight_scale == el.ROOF_FILL_WEIGHT
    assert got[2].top_edge == "depth"                                     # roof fit not trusted
    assert got[3].top_edge == "roof"                                      # no run at all
    assert 4 not in got                                                   # past 1 km: not trusted
    assert fd.measurement_weight(got[1]) == pytest.approx(
        el.ROOF_FILL_WEIGHT * 0.8 / 500.0 ** 2)
    monkeypatch.setattr(el, "ROOF_FILL_WEIGHT", None)                     # off
    assert [m.top_edge for m in el.measure_waterline(_pano(), fd.PanoPose(0, 80, 0, 0, W), [])] == \
        ["sky", "depth", "depth"]


def test_waterline_fill_only_within_fill_distance(monkeypatch):
    """2026-10-07, Miami LiDAR: fills 840-930 m out read the tower behind (The Loft 1 177/82 m,
    Brickell Key I 157/68 m); a fill past FILL_MAX_DIST_M keeps the untrusted run instead."""
    from city2stl.skyline._pano import roof_fit as rf

    near, far = el.FILL_MAX_DIST_M - 50.0, el.FILL_MAX_DIST_M + 100.0
    runs = [fd.Measured(i, f"b{i}", 0, 9, 40.0, 60.0, 61.0, d, 30.0, 10, False, None, 0.1, "depth")
            for i, d in ((0, near), (1, far))]
    fits = [rf.RoofMeasured(i, f"b{i}", 0, 9, 40.0, 60.0, 61.0, d, 170.0, 30, False, None, 0.05,
                            "roof", 170.0, 2.0, 30, 0, 0.95) for i, d in ((0, near), (1, far))]
    assert all(el.trusted(f) for f in fits)                                # both confident
    monkeypatch.setattr(fd, "measure_footprints", lambda *a, **k: list(runs))
    monkeypatch.setattr(rf, "fit_roof_heights", lambda *a, **k: list(fits))
    got = {m.footprint: m for m in el.measure_waterline(_pano(), fd.PanoPose(0, 80, 0, 0, W), [])}
    assert got[0].top_edge == "roof"
    assert got[1].top_edge == "depth" and not el.trusted(got[1])


def test_outline_gate_rejects_sea_level_panos_and_keeps_drones():
    """2026-10-07: drone seeds fit the tower outline within 1.3 deg and move under 110 m; a
    Miami boat deck and a Chicago street pano fitted 3.96-4.63 deg after moving 184-228 m."""
    pose = fd.PanoPose(0.0, 60.0, 0.0, 0.3, W)

    def of(dx, dy, mis, n=300):
        return fd.OutlineFit(dx, dy, pose, 1.0, mis + 1.0, mis, n)

    assert el.outline_gate(None) is None                                  # no towers: no verdict
    assert el.outline_gate(of(0.0, -40.0, 0.61)) is None                  # Miami seed_4
    assert el.outline_gate(of(110.0, 10.0, 1.24)) is None                 # Cartagena seed_4 hi-res
    assert "deg" in el.outline_gate(of(200.0, -110.0, 4.28))              # Miami seed_2 boat deck
    assert "deg" in el.outline_gate(of(10.0, 0.0, 3.0))
    assert " m " in el.outline_gate(of(170.0, -150.0, 1.0))               # ran off the search
    assert el.outline_gate(of(170.0, -150.0, 9.0, n=20)) is None          # too few tower columns


def _fp_at(name, b0, b1, d, depth_m=30.0, tag=None, lat=10.4, lon=-75.55):
    """A footprint spanning bearings ``b0..b1`` (deg) from ``d`` to ``d + depth_m`` metres."""
    kx = fd.M_PER_DEG_LAT * np.cos(np.radians(lat))
    pts = [(r * np.sin(np.radians(b)), r * np.cos(np.radians(b)))
           for r, b in ((d, b0), (d, b1), (d + depth_m, b1), (d + depth_m, b0))]
    ring = np.array([(lon + x / kx, lat + y / fd.M_PER_DEG_LAT) for x, y in pts + pts[:1]])
    return fd.Footprint(name, ring, tag)


def test_a_run_onto_a_tagged_tower_behind_is_not_trusted(monkeypatch):
    """Miami seed_3 (2026-10-07): Kaseya Center (43 m) read 138 m, its run climbing from the
    arena's MobileSAM instance onto the towers behind. A sky-topped run whose top instance is not
    its base's, with its top where a farther tagged footprint puts its tag, becomes ``behind``."""
    from city2stl.skyline._pano import roof_fit as rf

    pano, pose = _pano(), fd.PanoPose(0.0, 80.0, 0.0, 0.3, W)
    fps = [_fp_at("arena", 10.0, 20.0, 400.0), _fp_at("tower", 12.0, 18.0, 1000.0, tag=180.0)]
    top = float(fd._row_of(pano, pose, np.degrees(np.arctan2(180.0 - 80.0, 1000.0))))
    run = fd.Measured(0, "arena", 10, 20, top, 60.0, 61.0, 400.0, 150.0, 11, True, None, 1.0, "sky")
    monkeypatch.setattr(fd, "measure_footprints", lambda *a, **k: [run])
    monkeypatch.setattr(rf, "fit_roof_heights", lambda *a, **k: [])
    inst = np.zeros((H, W), np.int32)
    inst[:50, :] = 2                                                 # the tower behind
    inst[50:, :] = 1                                                 # the arena
    got = el.measure_waterline(pano, pose, fps, instances=inst)
    assert got[0].top_edge == "behind" and not el.trusted(got[0])
    same = np.ones((H, W), np.int32)                                 # one instance: kept
    assert el.measure_waterline(pano, pose, fps, instances=same)[0].top_edge == "sky"
    fps[1] = _fp_at("tower", 12.0, 18.0, 1000.0, tag=260.0)          # tag puts its top elsewhere
    assert el.measure_waterline(pano, pose, fps, instances=inst)[0].top_edge == "sky"
    fps[1] = _fp_at("tower", 12.0, 18.0, 420.0, tag=180.0)           # not farther: kept
    assert el.measure_waterline(pano, pose, fps, instances=inst)[0].top_edge == "sky"
    monkeypatch.setattr(el, "BEHIND_TOL_PX", None)                    # off
    fps[1] = _fp_at("tower", 12.0, 18.0, 1000.0, tag=180.0)
    assert el.measure_waterline(pano, pose, fps, instances=inst)[0].top_edge == "sky"


def _seed_with(name, ms, fids, behind=None):
    pano, pose = _pano(), fd.PanoPose(0.0, 80.0, 0.0, 0.3, W)
    pf = fd.PositionFit(0.0, 0.0, pose, 0.3, 0.3, 0.4, 0.4, "recorded")
    sp = SkylinePoint(name, 10.4, -75.55, 0.0, "seed", 1.0)
    return el.ElevatedSeed(name, pf, ms, fids, el.pano_result(sp, pano, pose, ms, fids, None),
                           behind=behind or {})


def test_behind_map_lists_farther_footprints_with_the_height_the_top_row_gives_them():
    pano, pose = _pano(), fd.PanoPose(0.0, 80.0, 0.0, 0.3, W)
    fps = [_fp_at("low", 10.0, 20.0, 400.0), _fp_at("tower", 12.0, 18.0, 1000.0, tag=180.0),
           _fp_at("beside", 60.0, 70.0, 1000.0), _fp_at("close", 12.0, 18.0, 420.0)]
    top = float(fd._row_of(pano, pose, np.degrees(np.arctan2(180.0 - 80.0, 1000.0))))
    run = fd.Measured(0, "low", 10, 20, top, 60.0, 61.0, 400.0, 120.0, 11, True, None, 1.0, "sky")
    got = el.behind_map(pano, pose, fps, ["w/0", "w/1", "w/2", "w/3"], [run])
    (g, hg, tag), = got[0]                         # not beside it, not barely farther
    assert g == "w/1" and tag == 180.0 and hg == pytest.approx(180.0, rel=0.03)
    untrusted = fd.Measured(0, "low", 10, 20, top, 60.0, 61.0, 400.0, 120.0, 11, False, None,
                            0.2, "sky")
    assert el.behind_map(pano, pose, fps, ["w/0", "w/1", "w/2", "w/3"], [untrusted]) == {}


def test_tower_behind_needs_a_low_satellite_and_a_farther_footprint_that_explains_the_top():
    """F-SKY26 step 6 (2026-10-07, Cartagena v8: 212 drone singles over 2x the prior, median
    102 m, on 160 every satellite reading under 40 m)."""
    low = _m(0, 10, 20, 110.0, 600.0)
    behind = {0: (("w/9", 150.0, None),)}
    sat = {"w/1": [{"method": "stereo", "height_m": 9.0, "conf": 1.0}],
           "w/9": [{"method": "lean", "height_m": 160.0, "conf": 0.9}]}
    s = _seed_with("seed_1", [low], ["w/1"], behind)
    assert el.tower_behind([s], sat) == {("seed_1", "w/1"): ("w/9", 150.0, 160.0)}
    assert el.tower_behind([s], None) == {}                               # no satellite: off
    hi = dict(sat, **{"w/1": [{"method": "stereo", "height_m": 95.0, "conf": 1.0}]})
    assert el.tower_behind([s], hi) == {}                                  # satellite says tall
    weak = dict(sat, **{"w/1": [{"method": "stereo", "height_m": 9.0, "conf": 0.3}]})
    assert el.tower_behind([s], weak) == {}                                # not confident
    shadow = dict(sat, **{"w/1": [{"method": "shadow", "height_m": 9.0, "conf": 0.9}]})
    assert el.tower_behind([s], shadow) == {}                              # a lower bound only
    near = dict(sat, **{"w/1": [{"method": "stereo", "height_m": 60.0 / 2.2, "conf": 1.0}]})
    assert el.tower_behind([_seed_with("seed_1", [_m(0, 10, 20, 50.0, 600.0)], ["w/1"], behind)],
                           near) == {}                                     # not 2x the satellite
    off = dict(sat, **{"w/9": [{"method": "lean", "height_m": 240.0, "conf": 0.9}]})
    assert el.tower_behind([s], off) == {}                                 # G's evidence disagrees
    # G's evidence from a tag, or from another seed's trusted reading of G
    tagged = _seed_with("seed_1", [low], ["w/1"], {0: (("w/9", 150.0, 140.0),)})
    assert el.tower_behind([tagged], {"w/1": sat["w/1"]})[("seed_1", "w/1")][2] == 140.0
    other = _seed_with("seed_4", [_m(0, 30, 40, 155.0, 900.0)], ["w/9"])
    assert el.tower_behind([s, other], {"w/1": sat["w/1"]})[("seed_1", "w/1")][2] == 155.0


def test_estimates_leave_out_tower_behind_readings_given_raw_satellite():
    low = _m(0, 10, 20, 110.0, 600.0)
    s = _seed_with("seed_1", [low, _m(1, 30, 40, 40.0, 500.0)], ["w/1", "w/2"],
                   {0: (("w/9", 150.0, 150.0),)})
    raw = {"w/1": [{"method": "stereo", "height_m": 9.0, "conf": 1.0}]}
    assert {e.feature_id for e in el.elevated_estimates([s])} == {"w/1", "w/2"}
    assert {e.feature_id for e in el.elevated_estimates([s], satellite=raw)} == {"w/2"}
    # fusion-form readings (no low ones) leave the reading in, as before
    fused = {"w/1": [fd.satellite_reading("w/1", "stereo", 105.0, 1.0)]}
    assert {e.feature_id for e in el.elevated_estimates([s], satellite=fused)} == {"w/1", "w/2"}


def test_segments_carry_trust_and_the_reason_a_reading_is_left_out():
    """The seed pages' reason column: top_edge, trusted, untrusted_reason on every segment."""
    from dataclasses import asdict, replace

    from city2stl.skyline._pano.roof_fit import RoofMeasured

    def roof(m, conf):
        return RoofMeasured(**{**asdict(m), "top_edge": "roof"}, confidence=conf)
    ms = [_m(0, 10, 20, 110.0, 600.0),                               # trusted, then tower behind
          _m(1, 30, 40, 40.0, 500.0),                                # trusted
          _m(2, 50, 60, 40.0, 500.0, base=False, frac=0.2),          # base hidden
          replace(_m(3, 70, 80, 40.0, 500.0), top_edge="depth"),
          replace(_m(4, 90, 100, 40.0, 500.0), top_edge="behind"),
          roof(_m(5, 110, 120, 40.0, 500.0), 0.3),
          roof(_m(6, 130, 140, 40.0, 1500.0), 0.9)]
    fids = [f"w/{i}" for i in range(1, 8)]
    s = _seed_with("seed_1", ms, fids, {0: (("w/9", 150.0, 150.0),)})
    seg = {g["matched_projection"]["feature_id"]: g for g in s.pano_result.matched_segments}
    assert [(seg[f]["top_edge"], seg[f]["trusted"], seg[f]["untrusted_reason"]) for f in fids] == [
        ("sky", True, None), ("sky", True, None), ("sky", False, "base_hidden"),
        ("depth", False, "depth_edge"), ("behind", False, "tag_behind"),
        ("roof", False, "roof_conf_low"), ("roof", False, "roof_beyond_1000m")]
    raw = {"w/1": [{"method": "stereo", "height_m": 9.0, "conf": 1.0}]}
    el.elevated_estimates([s], satellite=raw)
    assert (seg["w/1"]["trusted"], seg["w/1"]["untrusted_reason"]) == (False, "tower_behind")
    assert seg["w/2"]["trusted"] is True


def test_floors_flag_high_rises_join_fusion_and_never_publish_alone():
    """F-SKY26 steps 4/5: the storey from tagged plots counted with their base in view, the
    high-rise flag on an untagged plot (not where satellite says low), floors readings from
    another seed verify a drone reading, and a floors-only plot emits nothing."""
    from city2stl.height.satellite.readings import SatReading
    from city2stl.skyline import floor_bands as fl

    fids = ["w/t1", "w/t2", "w/t3", "w/u", "w/v", "w/low"]
    tags = [{"fid": f, "floors": n, "base_seen": True, "spread": 0.02, "n_strips": 4,
             "instance": i, "name": f, "tag_m": n * 4.0 + 3.0}
            for i, (f, n) in enumerate([("w/t1", 20.0), ("w/t2", 30.0), ("w/t3", 40.0)])]
    other = [{"fid": "w/u", "floors": 25.0, "base_seen": False, "spread": 0.03, "n_strips": 3,
              "instance": 9, "name": "u", "tag_m": None},                 # untagged, no drone
             {"fid": "w/v", "floors": 15.0, "base_seen": True, "spread": 0.0, "n_strips": 3,
              "instance": 10, "name": "v", "tag_m": None},
             {"fid": "w/low", "floors": 14.0, "base_seen": False, "spread": 0.0, "n_strips": 3,
              "instance": 11, "name": "low", "tag_m": None}]
    a = _seed_with("seed_1", [_m(4, 10, 20, 63.0, 500.0)], fids)            # drone on w/v
    a.floors = tags
    b = _seed_with("seed_4", [], fids)
    b.floors = other
    sat = {"w/low": [SatReading("stereo", 8.0, 0.9)]}
    est = el.elevated_estimates([a, b], satellite=sat)
    assert isinstance(est, el.ElevatedEstimates)
    assert est.storey.storey_m == pytest.approx(4.0) and est.storey.reliable
    u = est.floors["w/u"]
    assert u["high_rise_seen"] and u["lower_bound"] and u["floors"] == 25.0
    assert fl.high_rise_height(12.0, u) == pytest.approx(25 * 4.0 + 3)
    assert not est.floors["w/low"]["high_rise_seen"]                       # satellite: low
    # w/v: drone 63 m (seed_1) and floors 15 x 4 + 3 = 63 m (seed_4): one estimate, the drone's
    assert [(e.feature_id, e.view_name.split("_0")[0]) for e in est] == [("w/v", "seed_1")]
    # floors-only footprints (w/u, the tagged ones) emit nothing
    assert {e.feature_id for e in est} == {"w/v"}
