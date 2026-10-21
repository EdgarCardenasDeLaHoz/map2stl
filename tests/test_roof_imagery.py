"""Roof detail read from imagery (``city2stl.roof_imagery``) on synthetic RGB: ridge direction,
hips, courtyards, the lidar veto, registration, the cache round trip and the precedence rules.
No network and no tiles: ``open_site`` is replaced by a painter of fake crops."""

from __future__ import annotations

import copy

import numpy as np
import pytest
import trimesh
from affine import Affine
from numpy2stl.processing.boolean import from_manifold, union
from shapely import affinity
from shapely.geometry import Polygon

import geo2stl.cache as geo_cache
from city2stl import roof_cache
from city2stl import roof_imagery as ri
from city2stl.roofs import building_solids

M_PER_PX = 0.4777


@pytest.fixture(autouse=True)
def tmp_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(geo_cache, "CACHE_ROOT", str(tmp_path))
    return tmp_path


# ------------------------------------------------------------------ synthetic imagery

def _rect(cx, cy, length, width, theta=0.0):
    r = Polygon([(-length / 2, -width / 2), (length / 2, -width / 2),
                 (length / 2, width / 2), (-length / 2, width / 2)])
    return affinity.translate(affinity.rotate(r, theta, origin=(0, 0), use_radians=True), cx, cy)


def _frame(poly, hw, theta):
    h, w = hw
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    c = np.asarray(poly.centroid.coords[0])
    u = np.array([np.cos(theta), np.sin(theta)])
    n = np.array([-u[1], u[0]])
    return ((xx - c[0]) * u[0] + (yy - c[1]) * u[1]), ((xx - c[0]) * n[0] + (yy - c[1]) * n[1])


def _to_rgb(img, poly, hw):
    out = np.full((*hw, 3), 120, np.uint8)
    m = ri._mask(poly, hw) > 0
    out[m] = np.clip(np.dstack([img, img * 0.62, img * 0.45])[m], 0, 255).astype(np.uint8)
    return out


def _paint_gable(poly, hw, ridge_along=True, theta=0.0, seed=0):
    """Terracotta two-slope roof: one slope bright, one dark, courses parallel to the ridge."""
    along, perp = _frame(poly, hw, theta)
    axis = perp if ridge_along else along                            # the slopes split across the ridge
    rng = np.random.default_rng(seed)
    img = 90.0 + np.where(axis > 0, 40.0, -25.0) + 14 * np.sign(np.sin(axis * 1.6))
    img = img + rng.normal(0, 3, hw)
    return _to_rgb(img.astype(np.float32), poly, hw)


def test_ridge_along_and_across():
    poly = _rect(60, 50, 44, 20)
    fa = ri.ridge_features(ri.to_gray(_paint_gable(poly, (100, 120), ridge_along=True)), poly)
    fx = ri.ridge_features(ri.to_gray(_paint_gable(poly, (100, 120), ridge_along=False)), poly)
    assert not fa["s_across"] and not fa["b_across"] and not ri.ridge_across(fa, 0.0)
    assert fx["s_across"] and fx["b_across"] and ri.ridge_across(fx, 0.0)
    assert ri.ridge_conf(fx) > 0.2
    assert fx["aspect"] == pytest.approx(2.2, abs=0.1)


def test_ridge_follows_a_rotated_footprint():
    th = np.radians(33)
    poly = _rect(70, 70, 44, 20, th)
    img = _paint_gable(poly, (140, 140), ridge_along=False, theta=th)
    assert ri.ridge_across(ri.ridge_features(ri.to_gray(img), poly), 0.0)


def _paint_hipped(poly, hw):
    """Four planar faces (45 degree slopes) shaded by their normals; hip lines are crisp edges."""
    L, W = 44.0, 20.0
    a, p = _frame(poly, hw, 0.0)
    end_face = (np.abs(a) - (L / 2 - W / 2)) > np.abs(p)
    img = np.where(end_face, np.where(a > 0, 60.0, 175.0), np.where(p > 0, 215.0, 105.0))
    return _to_rgb(img.astype(np.float32), poly, hw)


def test_hipped_render_scores_above_gabled():
    poly = _rect(60, 50, 44, 20)
    hip = ri.hip_features(ri.to_gray(_paint_hipped(poly, (100, 120))), poly)
    gab = ri.hip_features(ri.to_gray(_paint_gable(poly, (100, 120), ridge_along=True)), poly)
    assert ri.hip_score(hip) > ri.hip_score(gab) + 0.3
    assert ri.hip_score(None) == 0.0


def test_a_square_is_too_round_to_read_a_hip():
    poly = _rect(40, 40, 20, 20)
    assert ri.hip_features(ri.to_gray(_paint_gable(poly, (80, 80))), poly) is None


# ------------------------------------------------------------------ courtyards

def _paint_patio(poly, hw, hole, colour=(55, 60, 58)):
    """A plain terracotta roof (one tone, light grain) with a dark patch cut into it."""
    rng = np.random.default_rng(1)
    img = _to_rgb((165.0 + rng.normal(0, 3, hw)).astype(np.float32), poly, hw)
    img[ri._mask(hole, hw) > 0] = colour
    return img


def test_inner_patio_is_found_and_one_on_the_edge_is_not():
    poly = _rect(60, 60, 40, 40)
    inner = _rect(60, 60, 12, 10)
    got = ri.courtyard_candidates(_paint_patio(poly, (120, 120), inner), poly, M_PER_PX)
    assert len(got) == 1
    c = got[0]
    assert Polygon(c["ring"]).intersection(inner).area / inner.area > 0.85
    assert c["area_m2"] == pytest.approx(inner.area * M_PER_PX ** 2, rel=0.25)
    assert 0 < c["score"] <= 1
    edge = _rect(60 - 20 + 6, 60, 12, 10)                        # runs into the outline
    assert ri.courtyard_candidates(_paint_patio(poly, (120, 120), edge), poly, M_PER_PX) == []


def test_a_small_or_thin_dark_patch_is_not_a_courtyard():
    poly = _rect(60, 60, 40, 40)
    assert ri.courtyard_candidates(_paint_patio(poly, (120, 120), _rect(60, 60, 4, 4)), poly, M_PER_PX) == []
    assert ri.courtyard_candidates(_paint_patio(poly, (120, 120), _rect(60, 60, 30, 2)), poly, M_PER_PX) == []


# ------------------------------------------------------------------ registration

def test_a_three_pixel_shift_is_recovered():
    poly = _rect(60, 50, 44, 26)
    img = _paint_gable(poly, (110, 130))
    moved = np.roll(np.roll(img, 3, axis=1), -2, axis=0)             # content displaced by (+3, -2)
    sc, q = ri.register_map(ri.to_gray(moved), poly)
    dx, dy = ri.pick_shift(sc)
    assert abs(dx - 3) <= 1 and dy == -2
    assert q > 1.2
    assert ri.pick_shift(sc, (0.0, 0.0)) == (dx, dy)                 # a prior does not outvote a clear peak
    flat = np.full((110, 130), 100, np.float32)                      # no structure
    reg = ri.register_map(flat, poly)
    assert reg is None or reg[1] < ri.REG_GATE                       # nothing to lock onto: gated out


def test_footprint_too_near_the_crop_edge_has_no_registration():
    assert ri.register_map(np.zeros((40, 40), np.float32), _rect(5, 5, 8, 8)) is None


# ------------------------------------------------------------------ lidar veto

def _ndsm(lon0, lat0, n=400, step=2e-6, roof_box=None, height=8.0):
    """(array, affine); ``height`` inside ``roof_box`` = (lon0, lat0, lon1, lat1), else 0."""
    arr = np.zeros((n, n), np.float32)
    tf = Affine(step, 0, lon0, 0, -step, lat0 + n * step)
    if roof_box:
        c0, c1 = int((roof_box[0] - lon0) / step), int((roof_box[2] - lon0) / step)
        r0, r1 = int((lat0 + n * step - roof_box[3]) / step), int((lat0 + n * step - roof_box[1]) / step)
        arr[r0:r1, c0:c1] = height
    return arr, tf


def _clear(nd, box):
    """Zero the nDSM inside ``box`` = (lon0, lat0, lon1, lat1)."""
    arr, tf = nd
    c0, c1 = int((box[0] - tf.c) / tf.a), int((box[2] - tf.c) / tf.a)
    r0, r1 = int((box[3] - tf.f) / tf.e), int((box[1] - tf.f) / tf.e)
    arr[r0:r1, c0:c1] = 0.0
    return nd


def _geom(w=-3.6030, s=37.1810, dx=6e-4, dy=2.5e-4):
    return {"type": "Polygon", "coordinates": [[[w, s], [w + dx, s], [w + dx, s + dy], [w, s + dy], [w, s]]]}


def _box(g, d=1e-4):
    w, s = g["coordinates"][0][0]
    return (w - d, s - d, w + 6e-4 + d, s + 2.5e-4 + d)


def test_lidar_veto_reads_the_cells_under_the_hole():
    g = _geom()
    w, s = g["coordinates"][0][0]
    hole = [[w + 2e-4, s + 0.8e-4], [w + 3e-4, s + 0.8e-4], [w + 3e-4, s + 1.7e-4],
            [w + 2e-4, s + 1.7e-4], [w + 2e-4, s + 0.8e-4]]
    has, share = ri.lidar_check(_ndsm(w - 2e-4, s - 2e-4, roof_box=_box(g)), g, hole)
    assert has and share > 0.9                                       # the hole reads as roof
    patio = _clear(_ndsm(w - 2e-4, s - 2e-4, roof_box=_box(g)), (w + 2e-4, s + 0.8e-4, w + 3e-4, s + 1.7e-4))
    has, share = ri.lidar_check(patio, g, hole)
    assert has and share < 0.1                                       # a real patio
    assert ri.lidar_check(_ndsm(w - 2e-4, s - 2e-4), g, hole) == (False, None)    # lidar sees no building
    assert ri.lidar_check(None, g, hole) == (False, None)


# ------------------------------------------------------------------ apply: precedence, cache, veto

def _fake(monkeypatch, paint, calls=None):
    def open_site(geometry):
        if calls is not None:
            calls.append(1)
        px = ri.footprint_px(geometry)
        ox, oy = np.floor(px.min(0)) - 20
        w, h = (np.ceil(px.max(0)) + 20 - [ox, oy]).astype(int)
        poly = Polygon(px - [ox, oy])
        return ri.Site(paint(poly, (h, w)), poly, ox, oy, M_PER_PX, (0, 0))
    monkeypatch.setattr(ri, "open_site", open_site)


def _feat(i=0, **props):
    g = _geom(w=-3.6030 + i * 8e-4)
    return {"type": "Feature", "properties": {"building": "yes", "roof:shape": "gabled",
                                             "roof_source": "roof_model_gbm", **props}, "geometry": g}


def _across(poly, hw):
    return _paint_gable(poly, hw, ridge_along=False)


def _flags(monkeypatch, ridge=True, hip=False, courtyards=False):
    monkeypatch.setattr(ri, "EMIT_RIDGE", ridge)
    monkeypatch.setattr(ri, "EMIT_HIP", hip)
    monkeypatch.setattr(ri, "EMIT_COURTYARDS", courtyards)
    monkeypatch.setattr(ri, "T_RIDGE", 0.0)


def test_apply_sets_across_only_on_untagged_classifier_roofs(monkeypatch):
    _flags(monkeypatch)
    _fake(monkeypatch, _across)
    feats = [_feat(0),
             _feat(1, **{"roof:orientation": "along"}),                      # tagged: untouched
             _feat(2, roof_source="osm"),                                    # higher source: untouched
             _feat(3, **{"roof:shape": "flat"})]                             # not pitched
    fc = {"type": "FeatureCollection", "features": copy.deepcopy(feats)}
    out, stats = ri.apply(fc, (1, 0, 1, 0))
    p = [f["properties"] for f in out["features"]]
    assert p[0]["roof:orientation"] == "across" and p[0]["roof_orientation_source"] == "imagery"
    assert p[1]["roof:orientation"] == "along" and "roof_orientation_source" not in p[1]
    assert "roof:orientation" not in p[2] and "roof:orientation" not in p[3]
    assert stats["ridge_across"] == 1 and fc["features"] == feats           # input not mutated


def test_apply_with_every_switch_off_changes_nothing(monkeypatch):
    _flags(monkeypatch, ridge=False)
    calls: list = []
    _fake(monkeypatch, _across, calls)
    out, stats = ri.apply({"features": [_feat(0)]}, (1, 0, 1, 0))
    assert out is None and not calls and not stats["enabled"]["ridge"]


def test_raw_measurements_round_trip_through_the_cache(monkeypatch):
    _flags(monkeypatch)
    calls: list = []
    _fake(monkeypatch, _across, calls)
    fc = {"features": [_feat(0), _feat(1)]}
    first, s1 = ri.apply(fc, (1, 0, 1, 0))
    n = len(calls)
    assert n >= 2 and s1["cached"] == 0
    second, s2 = ri.apply(fc, (1, 0, 1, 0))
    assert len(calls) == n and s2["cached"] == 2                       # no imagery read the second time
    assert second == first
    monkeypatch.setattr(ri, "T_RIDGE", 5.0)                            # thresholds act after the lookup
    third, _ = ri.apply(fc, (1, 0, 1, 0))
    assert third is None and len(calls) == n
    f = _feat(0)
    assert ri.feature_key(f, "gabled") != ri.feature_key(f, "hipped")
    assert ri.feature_key(f, "gabled") != ri.feature_key(_feat(1), "gabled")
    old = ri.feature_key(f, "gabled")
    assert list(roof_cache.lookup_imagery([old])) == [old]
    monkeypatch.setattr(ri, "IMAGERY_VERSION", ri.IMAGERY_VERSION + 1)
    assert roof_cache.lookup_imagery([ri.feature_key(f, "gabled")]) == {}


def test_a_missing_tile_skips_the_building_and_never_raises(monkeypatch):
    _flags(monkeypatch)
    monkeypatch.setattr(ri, "open_site", lambda geometry: None)
    out, stats = ri.apply({"features": [_feat(0)]}, (1, 0, 1, 0))
    assert out is None and stats["no_tiles"] == 1


def test_a_broken_imagery_pass_returns_nothing_not_an_error(monkeypatch):
    _flags(monkeypatch)

    def boom(geometry):
        raise RuntimeError("tile decoder fell over")

    monkeypatch.setattr(ri, "open_site", boom)
    out, stats = ri.apply({"features": [_feat(0)]}, (1, 0, 1, 0))
    assert out is None and "tile decoder" in stats["error"]


def _patio_paint(poly, hw):
    minx, miny, maxx, maxy = poly.bounds
    hole = _rect(poly.centroid.x, poly.centroid.y, 0.35 * (maxx - minx), 0.35 * (maxy - miny))
    return _paint_patio(poly, hw, hole)


def test_apply_cuts_a_courtyard_unless_lidar_says_roof(monkeypatch):
    _flags(monkeypatch, ridge=False, courtyards=True)
    _fake(monkeypatch, _patio_paint)
    f = _feat(0)
    w, s = f["geometry"]["coordinates"][0][0]
    # lidar sees a roof over the patch: vetoed
    out, st = ri.apply({"features": [f]}, (1, 0, 1, 0), ndsm=_ndsm(w - 2e-4, s - 2e-4, roof_box=_box(f["geometry"])))
    assert out is None and st["vetoed"] == 1 and st["courtyards"] == 0
    # lidar sees the building with a hole in it: cut, and the ring lies inside the exterior
    nd = _clear(_ndsm(w - 2e-4, s - 2e-4, roof_box=_box(f["geometry"])),
                (w + 1.5e-4, s + 0.5e-4, w + 4.5e-4, s + 2.0e-4))
    out, st = ri.apply({"features": [f]}, (1, 0, 1, 0), ndsm=nd)
    cut = out["features"][0]
    assert st["courtyards"] == 1 and cut["properties"]["courtyards"] == 1
    assert len(cut["geometry"]["coordinates"]) == 2
    assert Polygon(cut["geometry"]["coordinates"][0]).contains(Polygon(cut["geometry"]["coordinates"][1]))
    # no lidar at all: only a patch above the stricter threshold is cut
    monkeypatch.setattr(ri, "T_HOLE_NOLIDAR", 1.01)
    out, _ = ri.apply({"features": [_feat(5)]}, (1, 0, 1, 0), ndsm=None)
    assert out is None


def test_a_footprint_with_a_hole_already_is_left_alone(monkeypatch):
    _flags(monkeypatch, ridge=False, courtyards=True)
    _fake(monkeypatch, _patio_paint)
    f = _feat(0)
    w, s = f["geometry"]["coordinates"][0][0]
    f["geometry"]["coordinates"].append([[w + 2e-4, s + 0.8e-4], [w + 3e-4, s + 0.8e-4], [w + 3e-4, s + 1.7e-4],
                                         [w + 2e-4, s + 1.7e-4], [w + 2e-4, s + 0.8e-4]])
    out, _ = ri.apply({"features": [f]}, (1, 0, 1, 0), ndsm=None)
    assert out is None


# ------------------------------------------------------------------ the solid and the seam

def test_a_holed_gabled_footprint_is_a_manifold_solid():
    ring = Polygon([(0, 0), (12, 0), (12, 10), (0, 10)], [[(4, 3), (8, 3), (8, 7), (4, 7)]])
    u, rejected = union(building_solids(ring, 0.0, 5.0, 3.0, "gabled", {"roof:orientation": "across"}))
    assert rejected == 0
    v, f = from_manifold(u)
    m = trimesh.Trimesh(v, f, process=False)
    assert m.is_watertight and m.volume > 0 and m.bounds[1, 2] > 5.5
    assert m.volume < 12 * 10 * 8 - 4 * 4 * 3                        # the patio is open


def test_roof_shapes_off_returns_the_payload_itself():
    from app.server.core import city_data
    payload = {"buildings": {"type": "FeatureCollection", "features": [_feat(0)]}}
    assert city_data.with_roof_shapes(payload, 1, 0, 1, 0, mode="off") is payload
