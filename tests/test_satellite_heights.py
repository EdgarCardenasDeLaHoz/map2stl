"""F-SKY26 step 7: satellite height package (city2stl.height.satellite) and its skyline wiring."""

import datetime as dt
import json
import math

import numpy as np
import pytest

from city2stl.height.satellite import measure as sm
from city2stl.height.satellite import readings as sr
from city2stl.height.satellite import scene as ss
from city2stl.height.satellite import weights as sw
from city2stl.skyline import footprint_detect as fd
from city2stl.skyline import satellite_fusion as sf
from city2stl.skyline._core.height import withhold_untagged_street_view
from city2stl.skyline._core.types import BuildingRecord


# ------------------------------------------------------------------------------ weights
def test_sigma_table_matches_the_decision_and_footprint_detect_delegates():
    assert sw.sigma_log("lean", 150, 0.8) == 0.07
    assert sw.sigma_log("lean", 30, 0.9) is None                  # lean under 40 m dropped
    assert sw.sigma_log("shadow", 60, 0.2) == 0.13
    assert sw.sigma_log("shadow", 20, 0.9) == 0.67
    assert sw.sigma_log("stereo", 39, 1.0) is None
    assert sw.sigma_log("multiview", 60, 0.3) == 0.08
    assert sw.sigma_log("multiview", 60, 0.29) is None            # conf < 0.3
    assert sw.sigma_log("multiview", 30, 0.9) is None
    assert sw.weight_scale("shadow", 20, 0.9) == pytest.approx((0.15 / 0.67) ** 2)
    assert sw.weight_scale("lean", 150, 0.8) == 1.0
    for k, h, c in (("lean", 150, 0.8), ("shadow", 10, 0.2), ("stereo", 60, 0.5), ("ls", 120, 0.8)):
        assert fd.satellite_sigma_log(k, h, c) == sw.sigma_log(k, h, c)


# ------------------------------------------------------------------------------ scene
def test_sunpos_and_unit_vectors():
    az, el = ss.sunpos(dt.datetime(2026, 3, 20, 12, 7), 0.0, 0.0)   # equinox, noon at Greenwich
    assert el > 88.0
    az, el = ss.sunpos(dt.datetime(2026, 2, 10, 15, 44), 10.405, -75.552)
    assert 135 < az < 145 and 55 < el < 60                          # Cartagena LG01 scene
    assert np.allclose(ss.uv(90.0), [1.0, 0.0]) and np.allclose(ss.uv(180.0), [0.0, 1.0])
    assert ss.scene_name({"SRC_DATE": 20260210, "SRC_DESC": "LG01"}) == "2026-02-10_LG01"


def test_tile_source_crops_global_pixels(tmp_path):
    from PIL import Image

    Image.new("RGB", (256, 256), (90, 90, 90)).save(tmp_path / "18_10_20.jpg")
    src = ss.TileSource(tmp_path)
    g, have = src.crop(10 * 256 - 8, 20 * 256 - 8, 10 * 256 + 8, 20 * 256 + 8)
    assert g.shape == (16, 16) and have[8:, 8:].all() and not have[:8, :8].any()
    assert abs(float(g[12, 12]) - 90) < 3


# ------------------------------------------------------------------------------ measure
def test_shadow_height_reads_a_synthetic_shadow():
    """A 30 m box on bright ground with its shadow drawn at bearing 320, sun 45 deg: the dark
    run from the roof edge to the tip gives the height (no lean: base == roof edge)."""
    from skimage.draw import polygon as skpoly

    M, Hb, el, bearing = 0.5, 30.0, 45.0, 320.0
    gray = np.full((800, 800), 180.0, np.float32)
    ub = ss.uv(bearing)
    sq = np.array([[380, 380], [420, 380], [420, 420], [380, 420]], float)   # 20 m square
    L = Hb / math.tan(math.radians(el)) / M                                   # shadow length, px
    for fr in np.linspace(0, 1, 200):                                         # swept footprint
        rr, cc = skpoly(sq[:, 1] + ub[1] * L * fr, sq[:, 0] + ub[0] * L * fr, gray.shape)
        gray[rr, cc] = 30.0
    rr, cc = skpoly(sq[:, 1], sq[:, 0], gray.shape)
    gray[rr, cc] = 200.0                                                      # the roof
    lab = np.zeros(gray.shape, np.int32)
    lab[rr, cc] = 1
    ctx = sm.ShadowCtx(gray=gray, lab=lab, water=np.zeros(gray.shape, bool),
                       veg=np.zeros(gray.shape, bool), B={0: dict(P=[sq], tag=None)}, ub=ub,
                       COT=1.0, p_ls=0.0, DEN_ROOF=1.0, DARK=80.0, M=M)
    got = sm.shadow_height(ctx, 0)
    assert got["height_m"] == pytest.approx(Hb, abs=2.0) and got["conf"] > 0.3


def test_pair_consensus_takes_the_height_most_pairs_support():
    st = dict(per_pair_h=[100.0, 102.0, 99.0, 40.0], pairs=["a|b", "a|c", "b|c", "c|d"])
    c = sm.consensus(st)
    assert c["n_agree"] == 3 and c["height_m"] == 100.0 and c["agreeing_pairs"] == ["a|b", "a|c", "b|c"]
    assert sm.consensus(dict(per_pair_h=[10.0], pairs=["a|b"])) is None


# ------------------------------------------------------------------------------ readings
def test_shadow_scenes_merge_to_one_lower_bound_reading_and_lean_shadow_make_ls():
    rs = sr.footprint_readings(
        lean={"height_m": 120.0, "conf": 0.8},
        shadows={"s1": {"height_m": 110.0, "conf": 0.6}, "s2": {"height_m": 112.0, "conf": 0.4},
                 "s3": {"height_m": 40.0, "conf": 0.3}, "s4": {"height_m": 90.0, "conf": 0.1}},
        stereo={"height_m": 118.0, "conf": 0.5, "n_pairs": 9, "n_agree": 4},
        multiview={"height_m": 119.0, "conf": 0.2}, ref_scene="s1")
    by = {r.method: r for r in rs}
    assert [r.method for r in rs] == ["ls", "lean", "shadow", "stereo"]     # multiview conf < 0.3
    sh = by["shadow"]
    assert sh.lower_bound and sh.n_scenes == 2 and sh.scene == "s1+s2"      # s3 outside 25 %, s4 < 0.15
    assert sh.height_m == pytest.approx((110 * 0.6 + 112 * 0.4) / 1.0)
    assert by["ls"].height_m == pytest.approx((120 * 0.8 + sh.height_m * 0.6) / 1.4)
    assert by["ls"].conf == 0.8


def test_readings_round_trip(tmp_path):
    rs = {"b0001": [sr.SatReading("shadow", 12.0, 0.5, "s1", True, 2)]}
    sr.save(tmp_path / "r.json", rs, {"scenes": {"s1": {"date": "20260210"}}},
            {"b0001": {"lat": 10.4, "lon": -75.55}})
    meta, got = sr.load(tmp_path / "r.json")
    assert meta["scenes"]["s1"]["date"] == "20260210" and meta["version"] == sr.READINGS_VERSION
    assert got["b0001"]["lat"] == 10.4 and got["b0001"]["readings"][0] == rs["b0001"][0]
    assert sr.load(tmp_path / "missing.json") == ({}, {})


# ------------------------------------------------------------------------------ skyline wiring
def test_fusion_readings_use_ls_in_place_of_lean_and_shadow():
    rs = {"b1": sr.footprint_readings(lean={"height_m": 150, "conf": 0.8},
                                      shadows={"s": {"height_m": 140, "conf": 0.7}})}
    ds = sf.fusion_readings(rs)["b1"]
    assert [d["kind"] for d in ds] == ["sat_ls"]
    assert ds[0]["dist_m"] == pytest.approx(3300 * 0.07)


def test_publishable_only_when_sigma_is_small():
    low = sr.footprint_readings(shadows={"s": {"height_m": 20.0, "conf": 0.9}})   # sigma 0.67
    assert sf.publishable(low) is None
    tall = sr.footprint_readings(lean={"height_m": 130.0, "conf": 0.8})          # sigma 0.07
    p = sf.publishable(tall)
    assert p["height_m"] == 130.0 and p["methods"] == ["sat_lean"] and not p["lower_bound"]


def _rec(fid, source, tag=None):
    return BuildingRecord(fid, fid, None, 10.4, -75.55, tag, source, 400.0)


def test_tiers_with_satellite(monkeypatch):
    monkeypatch.delenv("SKYLINE_WITHHOLD_UNTAGGED", raising=False)
    # the satellite singles here are over 2x the 12 m prior, which the single-over-2x rule
    # would withhold (test_skyline_tiers); this test is about the satellite wiring
    monkeypatch.setenv("SKYLINE_WITHHOLD_SINGLE", "0")
    sat = {
        "d": sr.footprint_readings(lean={"height_m": 110.0, "conf": 0.8}),       # drone + lean
        "s": sr.footprint_readings(lean={"height_m": 130.0, "conf": 0.8}),       # satellite only
        "ss": sr.footprint_readings(shadows={"a": {"height_m": 60, "conf": 0.9},  # shadows only
                                            "b": {"height_m": 61, "conf": 0.9}}),
        "lo": sr.footprint_readings(shadows={"a": {"height_m": 20, "conf": 0.9}}),
    }
    rows = [{"feature_id": f, "effective_height_m": 50.0, "effective_height_source": "geometric",
             "per_seed_median_m": ({"seed_1": 100.0, "seed_9": 50.0} if f == "d" else {"seed_9": 50.0})}
            for f in ("d", "s", "ss", "lo")]
    recs = [_rec(f, "default") for f in ("d", "s", "ss", "lo")]
    withhold_untagged_street_view(rows, recs, fallback=lambda r: (12.0, "prior"),
                                  measured_seeds={"seed_1"}, satellite=sat)
    d, s, ssr, lo = rows
    assert d["effective_height_source"] == "withheld:elevated" and d["tier"] == "corroborated"
    assert d["tier_methods"] == ["drone:seed_1", "lean"]
    assert s["effective_height_source"] == "withheld:satellite" and s["effective_height_m"] == 130.0
    assert s["tier"] == "single"
    assert ssr["effective_height_source"] == "withheld:satellite" and ssr["tier"] == "single"
    assert ssr["satellite_lower_bound"]                       # shadows only: a lower bound
    assert lo["effective_height_source"] == "withheld:prior" and lo["tier"] == "prior"
    assert lo["satellite"] == {"shadow": [20.0, 0.9]}
    # rule on (refined by the user, 2026-10-09): the lean-only single (130 m at conf 0.8 vs a
    # 12 m prior) is a validated reading, so it stays; the shadows-only one publishes the prior
    monkeypatch.setenv("SKYLINE_WITHHOLD_SINGLE", "1")
    rows = [{"feature_id": f, "effective_height_m": 50.0, "effective_height_source": "geometric",
             "per_seed_median_m": {"seed_9": 50.0}} for f in ("s", "ss")]
    withhold_untagged_street_view(rows, [_rec("s", "default"), _rec("ss", "default")],
                                  fallback=lambda r: (12.0, "prior"),
                                  measured_seeds={"seed_1"}, satellite=sat)
    s, ssr = rows
    assert s["effective_height_m"] == 130.0 and s["tier"] == "single"
    assert s["single_support"] == ["lean"] and "withheld_reason" not in s
    assert ssr["effective_height_m"] == 12.0 and ssr["tier"] == "prior"
    assert ssr["single_source"] == "withheld:satellite" and ssr["single_reading_m"] > 24.0


def test_elevated_estimates_skip_satellite_keys_and_satellite_can_dispute():
    from city2stl.skyline._pano import elevated as el
    from city2stl.skyline.region_types import SkylinePoint

    H, W = 100, 360
    frame = np.arange(W) * 1.0
    labels = np.full((H, W), fd.SKY_CLASS, np.int16)
    labels[60:, :] = 21
    pano = fd.Pano("seed_9", 10.4, -75.55, np.zeros((H, W, 3), np.uint8), labels, frame, 57.3, 0.0)
    pose = fd.PanoPose(0.0, 80.0, 0.0, 0.3, W)
    pf = fd.PositionFit(0.0, 0.0, pose, 0.3, 0.3, 0.4, 0.4, "recorded")
    seed = SkylinePoint("seed_9", 10.4, -75.55, 0.0, "seed", 1.0)
    m = fd.Measured(0, "", 10, 20, 40.0, 60.0, 61.0, 900.0, 150.0, 11, True, None, 1.0, "sky")
    s1 = el.ElevatedSeed("seed_1", pf, [m], ["b7"], el.pano_result(seed, pano, pose, [m], ["b7"], None))
    agree = sf.fusion_readings({"b7": sr.footprint_readings(lean={"height_m": 155.0, "conf": 0.8})})
    est = el.elevated_estimates([s1], satellite=agree)
    assert [e.view_name for e in est] == ["seed_1_015"]           # no KeyError on sat_lean
    against = sf.fusion_readings({"b7": sr.footprint_readings(lean={"height_m": 60.0, "conf": 0.8})})
    assert el.elevated_estimates([s1], satellite=against) == []    # lean at 528 m-eq outweighs 900 m


# ------------------------------------------------------------------------------ calibration
def test_calibrate_caps_a_low_lean_and_drops_stereo_under_a_confident_tall_lean():
    """2026-10-08, Chicago LiDAR: lean under 40 m is not a roof reading whatever its peak shape;
    stereo 1.25x or more under a confident tall lean matched a lower level (podium, setback)."""
    # Ravello (published 144 m): lean 38 m at conf 1.0, stereo 161 m
    rav = sr.footprint_readings(lean={"height_m": 38.0, "conf": 1.0},
                                stereo={"height_m": 161.0, "conf": 1.0, "n_pairs": 18})
    by = {r.method: r for r in rav}
    assert by["lean"].conf == sr.LOW_LEAN_MAX_CONF and by["lean"].extra["conf_peak"] == 1.0
    assert by["stereo"].conf == 1.0
    # Allure (180 m): lean 167.5 m at conf 1.0, stereo 77 m at 0.83 -> stereo dropped, kept as evidence
    al = sr.footprint_readings(lean={"height_m": 167.5, "conf": 1.0},
                               stereo={"height_m": 77.0, "conf": 0.83, "n_pairs": 18},
                               multiview={"height_m": 150.0, "conf": 0.5})
    assert [r.method for r in al] == ["lean", "multiview"]
    assert al[0].extra == {"stereo_under_lean": 77.0}
    # an unsure lean (conf < 0.7) never drops stereo; within 1.25x both stay
    assert [r.method for r in sr.footprint_readings(
        lean={"height_m": 167.5, "conf": 0.6}, stereo={"height_m": 77.0, "conf": 0.83})] == ["lean", "stereo"]
    assert [r.method for r in sr.footprint_readings(
        lean={"height_m": 120.0, "conf": 0.9}, stereo={"height_m": 100.0, "conf": 0.83})] == ["lean", "stereo"]
    # idempotent, and calibrate=False keeps the 2026-10-07 readings
    assert sr.calibrate(al) == al and sr.calibrate(rav) == rav
    raw = sr.footprint_readings(lean={"height_m": 38.0, "conf": 1.0}, calibrate=False)
    assert raw[0].conf == 1.0 and sr.calibrate(raw)[0].conf == sr.LOW_LEAN_MAX_CONF and raw[0].conf == 1.0


def test_calibrated_low_lean_no_longer_says_low_for_the_tower_behind_test():
    from city2stl.skyline._pano.elevated import _sat_max

    rs = sr.footprint_readings(lean={"height_m": 22.0, "conf": 1.0})
    assert _sat_max(sr.footprint_readings(lean={"height_m": 22.0, "conf": 1.0}, calibrate=False)) == 22.0
    assert _sat_max(rs) is None


# ------------------------------------------------------------------------------ region coverage
def _script():
    import importlib

    return importlib.import_module("city2stl.skyline.scripts.20_satellite_heights")


def test_scene_outline_rings_are_even_odd_and_select_footprints():
    outer = [[0, 0], [10, 0], [10, 10], [0, 10], [0, 0]]
    hole = [[4, 4], [6, 4], [6, 6], [4, 6], [4, 4]]
    part = [[20, 20], [22, 20], [22, 22], [20, 22], [20, 20]]
    got = ss.in_rings([outer, hole, part], [1, 5, 21, 15], [1, 5, 21, 15])
    assert got.tolist() == [True, False, True, False]
    fps = [dict(fid="a", lon=1.0, lat=1.0), dict(fid="b", lon=5.0, lat=5.0), dict(fid="c", lon=15.0, lat=1.0)]
    s = _script()
    assert s.covered_fids([outer, hole], fps) == {"a"}
    assert s.covered_fids(None, fps) == {"a", "b", "c"}          # unknown outline: all


def test_add_keeps_earlier_footprints_and_recalibrate_rewrites_stored_readings(tmp_path):
    s = _script()
    p = tmp_path / "readings.json"
    rs = {"b1": sr.footprint_readings(lean={"height_m": 167.5, "conf": 1.0},
                                      stereo={"height_m": 77.0, "conf": 0.83}, calibrate=False),
          "b2": sr.footprint_readings(lean={"height_m": 30.0, "conf": 0.9}, calibrate=False)}
    sr.save(p, rs, {"scenes": {"s": {}}}, {"b1": {"lat": 10.4, "lon": -75.5}, "b2": {"lat": 10.5, "lon": -75.5}})
    meta, data = sr.load(p)
    assert s.measured_before(meta, data) == {"b1", "b2"}
    assert s.measured_before({"measured": ["b1", "b9"]}, data) == {"b1", "b9"}
    assert s.recalibrate(p) == 0
    meta, data = sr.load(p)
    assert [r.method for r in data["b1"]["readings"]] == ["lean"] and data["b1"]["lat"] == 10.4
    assert data["b2"]["readings"][0].conf == sr.LOW_LEAN_MAX_CONF and meta["calibrated"]


def test_block_groups_keep_measure_multi_blocks_whole_and_balanced():
    s = _script()

    def f(fid, x, y):
        return dict(fid=fid, bb=(x, y, x + 10, y + 10))
    todo = [f("a", 10, 10), f("b", 20, 20), f("c", 30, 30), f("d", 2000, 10), f("e", 5000, 5000)]
    g = s.block_groups(todo, 2)
    assert sorted(map(sorted, g)) == [["a", "b", "c"], ["d", "e"]]       # a/b/c share block (0, 0)
    assert s.block_groups(todo, 1) == [["a", "b", "c", "d", "e"]] and s.block_groups([], 3) == []


# ------------------------------------------------------------------------------ search windows
def test_search_cap_widens_an_untagged_window_only_with_a_tall_hint():
    """Review 2026-10-09 item 2: untagged, the shadow / lean windows stopped at 60 m and the sweep
    at 80 m; a drone or floors reading (``hint``) widens them: 1.6x, and at least 220 m when a seed
    reads over 80 m. A tag sizes the window itself; the hint never narrows it."""
    cap = sm.search_cap
    assert cap(dict(tag=None), 60.0, 320.0) == 60.0 and cap(dict(tag=None), 80.0, 330.0) == 80.0
    assert cap(dict(tag=150.0), 60.0, 320.0) == 240.0 and cap(dict(tag=250.0), 60.0, 320.0) == 320.0
    assert cap(dict(tag=150.0, hint=300.0), 60.0, 320.0) == 240.0            # tagged: hint ignored
    assert cap(dict(tag=None, hint=30.0), 60.0, 320.0) == 60.0                 # 1.6 x 30 < 60
    assert cap(dict(tag=None, hint=70.0), 60.0, 320.0) == pytest.approx(112.0)
    assert cap(dict(tag=None, hint=81.0), 60.0, 320.0) == sm.HINT_TALL_CAP_M  # a seed over 80 m
    assert cap(dict(tag=None, hint=81.0), 80.0, 330.0) == sm.HINT_TALL_CAP_M
    assert cap(dict(tag=None, hint=150.0), 80.0, 330.0) == pytest.approx(240.0)
    assert cap(dict(tag=None, hint=400.0), 80.0, 330.0) == 330.0


def _lean_scene(H=120.0, k=0.4, M=0.5):
    """A 20 m square tower of height ``H`` leaning ``k`` m/m to the east (bearing 90): dark
    ground, mid-grey facade swept from the base to the roof, bright roof at ``H * k``."""
    from skimage.draw import polygon as skpoly

    gray = np.full((900, 900), 60.0, np.float32)
    sq = np.array([[300, 400], [340, 400], [340, 440], [300, 440]], float)
    ul = ss.uv(90.0)
    s_px = H * k / M
    for fr in np.linspace(0, 1, 300):
        rr, cc = skpoly(sq[:, 1] + ul[1] * s_px * fr, sq[:, 0] + ul[0] * s_px * fr, gray.shape)
        gray[rr, cc] = 120.0
    rr, cc = skpoly(sq[:, 1] + ul[1] * s_px, sq[:, 0] + ul[0] * s_px, gray.shape)
    gray[rr, cc] = 200.0
    from scipy import ndimage as ndi

    lab = np.zeros(gray.shape, np.int32)
    rr, cc = skpoly(sq[:, 1], sq[:, 0], gray.shape)
    lab[rr, cc] = 1
    z = np.zeros(gray.shape, bool)
    return sm.ShadowCtx(gray=gray, lab=lab, water=z, veg=z, B={0: dict(P=[sq], tag=None)},
                        ub=ss.uv(320.0), COT=1.0, p_ls=0.0, DEN_ROOF=1.0, DARK=40.0, M=M,
                        gxx=ndi.gaussian_filter(gray, 1.0, order=(0, 1)),
                        gyy=ndi.gaussian_filter(gray, 1.0, order=(1, 0)), k_lean=k, ul=ul)


def test_lean_reads_an_untagged_tower_once_a_seed_reading_widens_the_window():
    c = _lean_scene(H=120.0)
    narrow = sm.lean_height(c, 0)                         # untagged: window up to 60 m
    assert narrow["height_m"] <= 70.5 and narrow["conf"] <= 0.3  # stuck at the window's end
    assert narrow["reason"] == "peak at search-window edge"
    c.B[0]["hint"] = 100.0                                # a drone read it at 100 m
    got = sm.lean_height(c, 0)
    assert got["height_m"] == pytest.approx(120.0, abs=4.0) and got["conf"] > 0.5
    c.B[0]["tag"] = 120.0                                 # tagged: the same reading
    assert sm.lean_height(c, 0)["height_m"] == pytest.approx(120.0, abs=4.0)


def test_hints_from_a_region_run_and_the_windows_they_change(tmp_path):
    s = _script()
    fps = [dict(fid="b1", tag=None, lat=10.40, lon=-75.55), dict(fid="b2", tag=None, lat=10.41, lon=-75.55),
           dict(fid="b3", tag=150.0, lat=10.42, lon=-75.55), dict(fid="b4", tag=None, lat=10.43, lon=-75.55),
           dict(fid="b5", tag=None, lat=10.44, lon=-75.55)]
    rows = [
        # drone single withheld (seed_1 view) -> its reading; a street seed's 300 m is not a drone
        {"feature_id": "b1", "height_tag_m": None, "centroid_lat": 10.40, "centroid_lon": -75.55,
         "per_seed_median_m": {"seed_1": 214.1, "auto_090_1400m": 300.0},
         "views": [{"view_name": "seed_1_161", "height_m": 214.1}, {"view_name": "auto_090_1400m_270", "height_m": 300.0}],
         "single_source": "withheld:elevated", "single_reading_m": 214.1, "tier_methods": ["drone:seed_1"]},
        # floors only: 20 x 4.128 m
        {"feature_id": "b2", "height_tag_m": None, "centroid_lat": 10.41, "centroid_lon": -75.55,
         "per_seed_median_m": {}, "floors": 20.0, "storey_m": 4.128},
        # tagged: no hint (the tag sizes the window)
        {"feature_id": "b3", "height_tag_m": 150.0, "centroid_lat": 10.42, "centroid_lon": -75.55,
         "per_seed_median_m": {"seed_1": 140.0}},
        # renumbered run: id b9 at b4's centroid -> b4
        {"feature_id": "b9", "height_tag_m": None, "centroid_lat": 10.43, "centroid_lon": -75.55,
         "per_seed_median_m": {"seed_1": 50.0}},
        # only a street seed: no hint
        {"feature_id": "b5", "height_tag_m": None, "centroid_lat": 10.44, "centroid_lon": -75.55,
         "per_seed_median_m": {"auto_090_1400m": 90.0}},
    ]
    p = tmp_path / "heights.json"
    p.write_text(json.dumps({"region": "nowhere", "buildings": rows}), encoding="utf-8")
    h = s.load_hints(p, fps, "nowhere")
    assert h == {"b1": 214.1, "b2": pytest.approx(82.6, abs=0.05), "b4": 50.0}
    q = tmp_path / "hints.json"
    q.write_text(json.dumps({"b1": 100.0}), encoding="utf-8")
    assert s.load_hints(q) == {"b1": 100.0}
    s.set_hints(fps, h)
    assert [f.get("hint") for f in fps] == [214.1, pytest.approx(82.6, abs=0.05), None, 50.0, None]
    # b4's 50 m hint: 1.6 x 50 = 80 m = the sweep floor, but the lean / shadow window grows (60 -> 80)
    assert s.changed_windows(fps, {}, h) == {"b1", "b2", "b4"}
    assert s.changed_windows(fps, h, h) == set()
    assert s.changed_windows(fps, h, dict(h, b1=230.0)) == set()           # both at the 320 / 330 top
    assert s.changed_windows(fps, {"b1": 100.0}, {"b1": 120.0}) == set()   # both 220 m
    assert s.changed_windows(fps, {"b1": 100.0}, {"b1": 150.0}) == {"b1"}  # 220 -> 240 m


# ------------------------------------------------------------------------------ calibrated on load
def test_load_calibrates_a_stored_file_and_load_region_never_uses_it_raw(tmp_path):
    """Review 2026-10-09: Cartagena's region file had 178 leans under 40 m above conf 0.3
    (written before calibration, or kept by --add); readings.load did not calibrate."""
    p = tmp_path / "readings.json"
    rs = {"b1": sr.footprint_readings(lean={"height_m": 22.0, "conf": 1.0}, calibrate=False),
          "b2": sr.footprint_readings(lean={"height_m": 130.0, "conf": 0.8})}
    sr.save(p, rs, {"scenes": {}}, {"b1": {"lat": 10.4, "lon": -75.55}, "b2": {"lat": 10.41, "lon": -75.55}})
    meta, raw = sr.load(p, calibrate=False)
    assert raw["b1"]["readings"][0].conf == 1.0 and "n_recalibrated" not in meta
    meta, got = sr.load(p)
    assert got["b1"]["readings"][0].conf == sr.LOW_LEAN_MAX_CONF and meta["n_recalibrated"] == 1
    assert got["b2"]["readings"] == rs["b2"]
    recs = [_rec("b1", "default"), _rec("b2", "default")]
    recs = [BuildingRecord(r.feature_id, r.name, None, lat, -75.55, None, "default", 400.0)
            for r, lat in zip(recs, (10.4, 10.41), strict=True)]
    sat = sf.load_region("nowhere", recs, path=p)
    assert sat["b1"][0].conf == sr.LOW_LEAN_MAX_CONF and sat["b1"][0].extra["conf_peak"] == 1.0
    from city2stl.skyline._pano.elevated import _sat_max
    assert _sat_max(sat["b1"]) is None                    # no longer "satellite low"
    # --recalibrate rewrites the file; it then loads unchanged
    assert _script().recalibrate(p) == 0
    assert sr.load(p)[0]["n_recalibrated"] == 0


def test_scene_outline_takes_every_polygon_of_the_scene_in_the_bbox(monkeypatch):
    """2026-10-09: a scene is often several polygons in one release (Honolulu 2025 WV02 / WV03:
    two each); identify at one point gave one of them (WV03: 218 of 3,114 footprints)."""
    sq = lambda x0, y0, x1, y1: [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]   # noqa: E731
    feats = [{"attributes": {"SRC_DATE": 20250511, "SRC_DESC": "WV03"}, "geometry": {"rings": [sq(0, 0, 1, 1)]}},
             {"attributes": {"SRC_DATE": 20250201, "SRC_DESC": "WV02"}, "geometry": {"rings": [sq(1, 0, 2, 1)]}},
             {"attributes": {"SRC_DATE": 20250511, "SRC_DESC": "WV03"},
              "geometry": {"rings": [sq(2, 0, 3, 1), sq(2.4, 0.4, 2.6, 0.6)]}}]        # with a hole
    calls = []

    def fake(url, timeout=40):
        calls.append(url)
        return {"features": feats}
    monkeypatch.setattr(ss, "_get_json", fake)
    cfg = {"r": {"metadataLayerUrl": "https://example.invalid/MapServer"}}
    o = ss.scene_outlines(cfg, "r", (0, 0, 3, 1))
    assert set(o) == {"2025-05-11_WV03", "2025-02-01_WV02"} and "esriGeometryEnvelope" in calls[0]
    got = ss.in_rings(o["2025-05-11_WV03"], [0.5, 1.5, 2.2, 2.5], [0.5, 0.5, 0.5, 0.5])
    assert got.tolist() == [True, False, True, False]
    assert ss.scene_polygon(cfg, "r", "2025-05-11_WV03", [], bbox=(0, 0, 3, 1)) == o["2025-05-11_WV03"]
