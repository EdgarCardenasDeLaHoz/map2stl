"""photo-label tool: `tools/photo_label/mask_heights.py` on a synthetic scene (flat DEM, one tower,
a pinhole camera, a hand-drawn class mask)."""
import math
import sys
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "photo_label"))
import mask_heights as mh  # noqa: E402

from city2stl.skyline.photo_view import PinholeCamera  # noqa: E402

LAT, LON = 10.40, -75.55
KX = mh.M_PER_DEG_LAT * math.cos(math.radians(LAT))
W, H, F = 640, 540, 417.0


def flat(lat, lon):
    return np.zeros(np.shape(lat))


def cam(elev=30.0):
    return PinholeCamera(LAT, LON, elev, 0.0, 0.0, 0.0, F, W, H)


def tower_fc(h_osm, north=300.0, half=20.0, east=0.0):
    lat0 = LAT + north / mh.M_PER_DEG_LAT
    dlat, dlon = half / mh.M_PER_DEG_LAT, half / KX
    lon0 = LON + east / KX
    ring = [[lon0 - dlon, lat0 - dlat], [lon0 + dlon, lat0 - dlat], [lon0 + dlon, lat0 + dlat],
            [lon0 - dlon, lat0 + dlat], [lon0 - dlon, lat0 - dlat]]
    return {"type": "FeatureCollection", "features": [{
        "type": "Feature", "geometry": {"type": "Polygon", "coordinates": [ring]},
        "properties": {"id": 0, "name": "t", "height": h_osm, "levels": None, "lat": lat0, "lon": lon0}}]}


def synth_mask(cam_, true_h, north=300.0, half=20.0):
    """Sky above the horizon, ground (other) below; a building column from the front wall base to the
    true top, rendered by projecting the front face."""
    m = np.full((H, W), mh.OTHER, np.uint8)
    m[: int(cam_.height / 2)] = mh.SKY
    d = north - half
    z0, z1 = 0.0, true_h
    xs = [tuple(float(v) for v in cam_.project_ll(LAT + d / mh.M_PER_DEG_LAT, LON + s * half / KX, z))
          for s in (-1, 1) for z in (z0, z1)]
    x0, x1 = int(min(p[0] for p in xs)), int(max(p[0] for p in xs))
    y_top, y_base = int(round(min(p[1] for p in xs))), int(round(max(p[1] for p in xs)))
    m[y_top:y_base + 1, x0:x1 + 1] = mh.BUILDING
    return m


def run(true_h, osm_h, mask=None, c=None):
    c = c or cam()
    fc = tower_fc(osm_h)
    m = synth_mask(c, true_h) if mask is None else mask
    return mh.analyse_frame(m, c, fc, flat, ground_of=flat), m, c


def test_height_from_mask_top():
    res, _, _ = run(true_h=120.0, osm_h=120.0)
    assert not res["gate"]["bad_pose"]
    r = res["rows"][0]
    assert r["class"] == "ok"
    assert r["height_m"] == pytest.approx(120.0, rel=0.04)


def test_too_short_when_mask_rises_above_outline():
    res, _, _ = run(true_h=150.0, osm_h=60.0)
    r = res["rows"][0]
    assert r["class"] == "too_short"
    assert r["margin_deg"] > mh.TOL_DEG
    assert r["height_m"] == pytest.approx(150.0, rel=0.05)


def test_too_tall_when_outline_rises_into_sky():
    res, _, _ = run(true_h=60.0, osm_h=150.0)
    r = res["rows"][0]
    assert r["class"] == "too_tall"
    assert r["margin_deg"] < -mh.TOL_DEG


def test_vegetation_at_top_drops_columns():
    c = cam()
    m = synth_mask(c, 120.0)
    top = int(np.argmax((m == mh.BUILDING).any(1)))
    m[top: top + 4][m[top: top + 4] == mh.BUILDING] = mh.VEG
    res, _, _ = run(120.0, 120.0, mask=m)
    assert res["rows"][0]["height_m"] is None
    assert res["rows"][0]["dropped"].get("veg", 0) > 0


def test_bad_pose_gate_on_floating_outlines():
    # the mask is all sky above the horizon: outlines of a nearer-than-real pose float on sky
    c = cam()
    m = np.full((H, W), mh.OTHER, np.uint8)
    m[:420] = mh.SKY
    fc = {"type": "FeatureCollection", "features": []}
    for k, e in enumerate((-80.0, 0.0, 80.0)):
        f = tower_fc(40.0, north=300.0, east=e)["features"][0]
        f["properties"]["id"] = k
        fc["features"].append(f)
    res = mh.analyse_frame(m, c, fc, flat, ground_of=flat)
    assert res["gate"]["checked"] and res["gate"]["bad_pose"]
    assert all(r["height_m"] is None and r["class"] == "bad_pose" for r in res["rows"])


def test_unmatched_building_blob():
    c = cam()
    m = synth_mask(c, 120.0)
    m[200:330, 20:90] = mh.BUILDING
    res, _, _ = run(120.0, 120.0, mask=m)
    assert len(res["unmatched"]) == 1
    assert res["unmatched"][0]["cols"][0] <= 25


def test_agree_group_needs_two_within_25pct():
    assert sorted(mh.agree_group([100.0, 120.0, 300.0])) == [0, 1]
    assert len(mh.agree_group([100.0, 130.0])) == 1


def _frame(seed, frame, rows, weak=False):
    return {"seed": seed, "frame": frame, "weak": weak, "gate": {}, "rows": rows, "reassign": []}


def _row(i, h, cls="too_short"):
    return {"id": i, "class": cls, "height_m": h, "margin_deg": 5.0, "n_cols": 9, "lat": 1.0, "lon": 2.0}


def test_aggregate_keeps_only_agreeing_non_weak_views():
    fr = [_frame("s1", "a", [_row(0, 100.0), _row(1, 100.0)]),
          _frame("s4", "b", [_row(0, 110.0), _row(1, 200.0)]),
          _frame("s6", "c", [_row(0, 105.0), _row(1, 200.0)], weak=True)]
    s = mh.aggregate(fr)
    assert s[0]["agreed_m"] == pytest.approx(104.9, abs=0.2) and s[0]["n_agree"] == 2
    assert s[1]["agreed_m"] is None            # the weak view does not count


def test_reassign_to_neighbour_with_matching_height():
    a = {**_row(0, None, "too_tall"), "lat": 10.0, "lon": -75.0}
    b = {**_row(1, 150.0), "lat": 10.0003, "lon": -75.0}
    out = mh.find_reassignments([a, b], lambda i, r: 156.0)
    assert out and out[0]["from"] == 0 and out[0]["to"] == 1


def test_readings_and_merge(tmp_path):
    from city2stl.height.satellite import readings as sr
    s = {0: {"agreed_m": 150.0, "lat": 10.0, "lon": -75.0, "n_agree": 2, "seeds_agree": 1}}
    rs, where = mh.to_readings(s, lambda la, lo: "b0001")
    assert rs["b0001"][0].method == "photo_mask" and rs["b0001"][0].conf == 0.5
    p = tmp_path / "readings.json"
    sr.save(p, {"b0001": [sr.SatReading("lean", 90.0, 0.9)]}, {"region": "x"}, {"b0001": {"lat": 10.0, "lon": -75.0}})
    out = mh.merge_production(p, rs, where)
    assert out["written"] and (tmp_path / "readings.json.bak-2026-10-31").exists()
    _, data = sr.load(p, calibrate=False)
    assert sorted(r.method for r in data["b0001"]["readings"]) == ["lean", "photo_mask"]
    assert mh.merge_production(tmp_path / "missing.json", rs, where) == {
        "written": False, "expected": str(tmp_path / "missing.json")}
