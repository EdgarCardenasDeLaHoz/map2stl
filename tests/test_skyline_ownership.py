"""Geometric ownership of drone readings (review 2026-10-09 item 3): isovist, claims, re-credit."""

import math
from types import SimpleNamespace

import numpy as np
from shapely.geometry import Polygon

from city2stl.skyline import footprint_detect as fd
from city2stl.skyline._pano import elevated as el
from city2stl.skyline._pano import ownership as ow

LAT0, LON0, CAM_H = 25.77, -80.19, 60.0
H, W, F_PX = 700, 2620, 417.0                       # ~0.137 deg per column, as the spin panos


def _pano():
    frame = np.arange(W) * 360.0 / W
    labels = np.full((H, W), fd.SKY_CLASS, np.int16)
    return fd.Pano("seed_9", LAT0, LON0, np.zeros((H, W, 3), np.uint8), labels, frame, F_PX, 0.0)


POSE = fd.PanoPose(0.0, CAM_H, 0.0, 0.2, W)


def _square(bearing_deg, dist_m, side_m):
    """A square footprint (lon/lat ring) centred ``dist_m`` from the camera along ``bearing_deg``."""
    b = math.radians(bearing_deg)
    cx, cy = dist_m * math.sin(b), dist_m * math.cos(b)
    h = side_m / 2
    xy = [(cx - h, cy - h), (cx + h, cy - h), (cx + h, cy + h), (cx - h, cy + h), (cx - h, cy - h)]
    kx = fd.M_PER_DEG_LAT * math.cos(math.radians(LAT0))
    return np.array([(LON0 + x / kx, LAT0 + y / fd.M_PER_DEG_LAT) for x, y in xy])


def _tag(t):
    return ow.Upper(t, "tag", True, t * (1 + ow.TAG_MARGIN))


def _scene(specs):
    """specs: [(bearing, dist, side, Upper)] -> (Scene, footprints)."""
    fps = [fd.Footprint(f"fp{i}", _square(b, d, s), u.height_m if u.source == "tag" else None)
           for i, (b, d, s, u) in enumerate(specs)]
    return ow.Scene(_pano(), POSE, fps, [u for *_x, u in specs]), fps


def _reading(sc, i, elev_deg, dist_m):
    p = sc.polys[sc.by_fp[i]]
    x0, x1 = int(math.ceil(p.c0)), int(math.floor(p.c1))
    top = float(sc.row(elev_deg))
    base = float(sc.row(-math.degrees(math.atan2(CAM_H, dist_m))))
    h = CAM_H + dist_m * math.tan(math.radians(elev_deg))
    return fd.Measured(i, f"fp{i}", x0, x1, top, base, base, dist_m, h, x1 - x0 + 1, True, None,
                       1.0, "sky")


def _elev(height, d):
    return math.degrees(math.atan2(height - CAM_H, d))


def test_a_low_building_reading_the_tower_behind_is_the_towers_and_is_re_credited():
    # the tower is untagged: its prior p90 (170 m) is all that says it can reach the top
    sc, _ = _scene([(45.0, 500.0, 30.0, _tag(20.0)),
                    (45.0, 700.0, 40.0, ow.Upper(170.0, "prior_p90", False))])
    d_tower = 680.0                                    # the tower's near wall on the axis
    m = _reading(sc, 0, _elev(150.0, d_tower), 485.0)  # the run climbed onto the tower
    v = sc.assess(m)
    # no tagged footprint explains the top: above the tag, re-credited to the untagged tower
    assert v.owner == "none" and v.owner_all == "farther" and v.far_fp == 1
    assert v.shares["farther"] > 0.9 and ow.not_owned(v) == "owner_above_tag"
    rc = sc.recredit(m, v)
    assert rc.footprint == 1 and abs(rc.height_m - 150.0) < 3.0 and not rc.base_visible
    assert rc.visible_frac > 0.8                       # the low building claims only its foot
    assert el.trusted(rc) and rc.name == "fp1"


def test_an_untagged_footprint_keeps_a_top_only_untagged_claims_dispute():
    # Cartagena's untagged mid-rises: F's prior p90 (30 m) under the reading, an untagged tower
    # behind whose p90 holds the top; only a tag or a cadastre count takes a top away
    sc, _ = _scene([(45.0, 500.0, 30.0, ow.Upper(30.0, "prior_p90", False)),
                    (45.0, 700.0, 40.0, ow.Upper(170.0, "prior_p90", False))])
    v = sc.assess(_reading(sc, 0, _elev(70.0, 485.0), 485.0))
    assert v.owner_all == "farther" and v.owner == "none" and ow.not_owned(v) is None


def test_a_tagged_tower_behind_takes_the_reading_away_but_gets_no_re_credit():
    sc, _ = _scene([(45.0, 500.0, 30.0, _tag(20.0)), (45.0, 700.0, 40.0, _tag(150.0))])
    m = _reading(sc, 0, _elev(150.0, 680.0), 485.0)
    v = sc.assess(m)
    assert ow.not_owned(v) == "owner_farther" and v.far_fp == 1
    assert sc.recredit(m, v) is None                   # its tag publishes; no circular witness


def test_a_reading_of_its_own_top_stays_its_own():
    sc, _ = _scene([(225.0, 600.0, 40.0, _tag(100.0)), (225.0, 900.0, 40.0, _tag(160.0))])
    v = sc.assess(_reading(sc, 0, _elev(100.0, 580.0), 580.0))
    assert v.owner == "self" and ow.not_owned(v) is None


def test_a_top_inside_a_nearer_footprints_claim_is_the_nearer_ones():
    sc, _ = _scene([(135.0, 500.0, 30.0, _tag(30.0)), (135.0, 300.0, 60.0, _tag(120.0))])
    # F behind N read N's roofline (1 deg under N's claimed top): the isovist fails
    m = _reading(sc, 0, _elev(120.0, 270.0) - 1.0, 485.0)
    v = sc.assess(m)
    assert v.owner == "nearer" and v.near_fp == 1 and ow.not_owned(v) == "owner_nearer"
    assert sc.recredit(m, v) is None                   # never re-credited to a nearer footprint


def test_a_nearer_claim_owns_a_column_only_inside_its_edges_by_the_tolerance():
    # heading and footprint misplacement shift a claim sideways: a column one pixel inside N's
    # left edge stays F's, six pixels inside (past the 4 px tolerance) is N's
    sc, _ = _scene([(315.0, 600.0, 60.0, _tag(100.0)), (317.4, 300.0, 20.0, _tag(150.0))])
    c = int(math.ceil(sc.polys[sc.by_fp[1]].c0))
    xs = np.array([c + 1, c + 6])
    e = _elev(100.0, 570.0)
    k = sc.by_fp[0]
    assert list(sc.owners(k, xs, e)[0]) == ["self", "nearer"]
    assert list(sc.owners(k, xs, e, erode=False)[0]) == ["nearer", "nearer"]
    # F keeps a top it shows in a third of its columns
    v = sc.assess(_reading(sc, 0, e, 570.0))
    assert v.owner == "self" and v.shares["nearer"] > 0.3


def test_a_top_above_an_untagged_prior_with_nothing_behind_stays_but_not_above_a_tag():
    soft = ow.Upper(30.0, "prior_p90", False)
    sc, _ = _scene([(10.0, 500.0, 40.0, soft)])
    v = sc.assess(_reading(sc, 0, _elev(150.0, 480.0), 480.0))
    assert v.owner == "none" and ow.not_owned(v) is None   # a tower the prior misses
    sc, _ = _scene([(10.0, 500.0, 40.0, _tag(30.0))])
    v = sc.assess(_reading(sc, 0, _elev(150.0, 480.0), 480.0))
    assert v.owner == "none" and ow.not_owned(v) == "owner_above_tag"


def test_a_cadastre_occluder_hides_a_reading_but_never_owns_one():
    fps = [fd.Footprint("fp0", _square(60.0, 500.0, 30.0), 30.0)]
    occl = [(_square(60.0, 300.0, 60.0), 120.0)]          # not in OSM: geometry only
    sc = ow.Scene(_pano(), POSE, fps, [_tag(30.0)], occluders=occl)
    v = sc.assess(_reading(sc, 0, _elev(120.0, 270.0) - 1.0, 485.0))
    assert v.owner == "nearer" and v.near_fp == -1
    # a cadastre polygon on an OSM footprint is that building: dropped
    sc = ow.Scene(_pano(), POSE, fps, [_tag(30.0)], occluders=[(_square(60.0, 501.0, 30.0), 6.0)])
    assert len(sc.polys) == 1


def test_upper_heights_tag_then_cadastre_then_prior(monkeypatch):
    monkeypatch.setattr(ow, "prior_upper", lambda recs, city=None, q=0.9: np.full(len(recs), 25.0))
    poly = Polygon([(LON0, LAT0), (LON0 + 1e-4, LAT0), (LON0 + 1e-4, LAT0 + 1e-4), (LON0, LAT0 + 1e-4)])
    recs = [SimpleNamespace(feature_id=f, height_tag_m=t, geometry=poly)
            for f, t in (("a", 40.0), ("b", None), ("c", None))]
    up = ow.upper_heights(recs, cadastre={"b": ow.cadastre_bound(2)})
    assert up["a"].hard and up["a"].height_m == 40.0 and up["a"].own == 50.0
    assert up["b"].source == "cadastre" and abs(up["b"].height_m - 2 * 1.5 * 4.13) < 1e-9
    assert up["c"].source == "prior_p90" and not up["c"].hard and up["c"].own == 25.0


def test_estimates_drop_unowned_readings_and_take_the_re_credit():
    from city2stl.skyline.region_types import SkylinePoint

    pano = el.fd.Pano("s", 10.4, -75.55, np.zeros((100, 360, 3), np.uint8),
                      np.full((100, 360), fd.SKY_CLASS, np.int16), np.arange(360) * 1.0, 57.3, 0.0)
    pose = fd.PanoPose(0.0, 80.0, 0.0, 0.3, 360)
    pf = fd.PositionFit(0.0, 0.0, pose, 0.3, 0.3, 0.4, 0.4, "recorded")
    seed = SkylinePoint("s", 10.4, -75.55, 0.0, "seed", 1.0)

    def m(i, h, d, frac=1.0, base=True):
        return fd.Measured(i, f"b{i}", 10, 20, 40.0, 60.0, 61.0, d, h, 11, base, None, frac, "sky")

    low = m(0, 150.0, 500.0)                         # seed_1 read the tower behind over b0
    rc = m(1, 152.0, 700.0, frac=0.9, base=False)    # ... re-credited to b1
    own = ow.SeedOwnership(reasons={0: "owner_farther"}, recredits=[(0, rc)])
    s1 = el.ElevatedSeed("seed_1", pf, [low], ["b0", "b1"],
                         el.pano_result(seed, pano, pose, [low], ["b0", "b1"], None,
                                        recredits=own.recredits), ownership=own)
    tower = m(1, 148.0, 650.0)                       # seed_4 reads b1 itself
    s4 = el.ElevatedSeed("seed_4", pf, [tower], ["b0", "b1"],
                         el.pano_result(seed, pano, pose, [tower], ["b0", "b1"], None))
    est = el.elevated_estimates([s1, s4])
    assert {e.feature_id for e in est} == {"b1"}       # b0's reading left
    assert sorted(e.view_name[:6] for e in est) == ["seed_1", "seed_4"]   # two seeds on b1
    segs = {(s["matched_projection"]["feature_id"], s["height_src"]): s
            for s in s1.pano_result.matched_segments}
    assert segs[("b0", "footprint")]["untrusted_reason"] == "owner_farther"
    assert segs[("b1", "recredit")]["recredit_from"] == "b0"
    # the seed's own kept reading of a footprint wins over a re-credit to it
    s1b = el.ElevatedSeed("seed_1", pf, [low, m(1, 149.0, 690.0)], ["b0", "b1"],
                          el.pano_result(seed, pano, pose, [low], ["b0", "b1"], None),
                          ownership=own)
    assert el.recredited([s1b]) == {}


def test_ownership_switches(monkeypatch):
    monkeypatch.setenv("SKYLINE_OWNERSHIP", "0")
    assert not ow.enabled()
    assert el.seed_ownership(None, None, [], []) is None
    monkeypatch.setenv("SKYLINE_OWNERSHIP", "1")
    assert ow.enabled()
    monkeypatch.delenv("SKYLINE_CADASTRE_OCCLUDERS", raising=False)
    assert ow.cadastre_enabled() is ow.CADASTRE_OCCLUDERS


def test_degenerate_and_nan_footprints_are_skipped_and_no_full_pano_arrays_are_built():
    ring = _square(80.0, 400.0, 30.0)
    bad = [fd.Footprint("nan", np.where(np.arange(10).reshape(5, 2) == 3, np.nan, ring), None),
           fd.Footprint("line", ring[[0, 1, 0, 1]], None),           # zero area
           fd.Footprint("short", ring[:2], None)]
    ok = fd.Footprint("ok", ring, 50.0)
    ups = [ow.Upper(20.0, "prior_p90", False)] * 3 + [_tag(50.0)]
    sc = ow.Scene(_pano(), POSE, bad + [ok], ups, depth=np.ones((H, W), np.float32))
    assert list(sc.by_fp) == [3] and sc.isb is None and sc.dz is None
    v = sc.assess(_reading(sc, 3, _elev(50.0, 385.0), 385.0))
    assert v.owner == "self" and v.corners == (None, None)
