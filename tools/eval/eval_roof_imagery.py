"""Measure the roof-from-imagery thresholds on Granada (F-ROOFIMG).

Reads only caches: the OSM buildings (``cache/osm/<key>.json.gz``), the classifier's roofs
(``roof_cache``), the zoom-18 tiles (``roof_tiles.CACHE``) and one survey nDSM
(``cache/survey_cnig_mdsn/<hash>.npz`` with its ``.json`` transform). Nothing is fetched.

    python -m tools.eval.eval_roof_imagery measure --out raw.pkl   # slow: reads every footprint
    python -m tools.eval.eval_roof_imagery report  --raw raw.pkl   # fast: thresholds and scores

``measure`` stores the raw measurements of ``city2stl.roof_imagery.measure`` per footprint plus
the lidar truth. ``report`` prints, against that truth:

* registration: median shift, share of weak matches;
* ridge: agreement of the imagery axis and of the long axis with a two-plane fit to the nDSM,
  on the footprints of 150 m2 or more, overall and on the confident subset, per threshold;
* hip: grouped-fold accuracy of the hip score on the OSM-tagged gabled / hipped roofs;
* courtyards: precision of imagery-only patches (truth: the nDSM reads below 2 m) by score,
  and recall of lidar ground patches and of OSM's own courtyards.
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import pickle
import time
from pathlib import Path

from geo2stl.geo import M_PER_DEG_SPHERE, m_per_deg_lon

ROOT = Path(__file__).resolve().parents[2]
OSM = ROOT / "cache" / "osm" / "403ac47a87f615e9dc9c0e89de8f03c0.json.gz"
NDSM = ROOT / "cache" / "survey_cnig_mdsn" / "89bffb95629c73dae358e6f62bf7d16b"
RIDGE_MIN_AREA = 150.0


def _guard():
    from city2stl.resources import scratch_guard
    scratch_guard(max_workers=2)


def load_buildings():
    from city2stl import roof_cache
    feats = json.load(gzip.open(OSM))["buildings"]["features"]
    model = roof_cache.model_id()
    hits = roof_cache.lookup([roof_cache.feature_key(f, model) for f in feats])
    shapes = []
    for f in feats:
        tag = (f["properties"].get("roof:shape") or "").lower()
        if tag:
            shapes.append((tag, True))
        else:
            h = hits.get(roof_cache.feature_key(f, model))
            shapes.append((h[0] if h else "", False))
    return feats, shapes


def load_ndsm():
    import numpy as np
    from affine import Affine
    arr = np.load(f"{NDSM}.npz")["ndsm"].astype("float32")
    tf = Affine(*json.load(open(f"{NDSM}.json"))["transform"][:6])
    return arr, tf


# --------------------------------------------------------------------------- lidar truth

def _local(ring_ll):
    import numpy as np
    a = np.asarray(ring_ll, float)
    lat0 = a[:, 1].mean()
    return np.column_stack([(a[:, 0] - a[0, 0]) * M_PER_DEG_SPHERE * math.cos(math.radians(lat0)),
                            (a[:, 1] - a[0, 1]) * M_PER_DEG_SPHERE]), lat0


def footprint_cells(nd, geom, erode_m=1.0):
    """(x, y, z) of the nDSM cells whose centres lie inside the footprint, eroded by ``erode_m``;
    x, y in metres east / north of the first vertex."""
    import numpy as np
    import shapely
    from shapely.geometry import Polygon
    arr, tf = nd
    ring = geom["coordinates"][0]
    xy, lat0 = _local(ring)
    poly = Polygon(xy).buffer(-erode_m)
    if poly.is_empty or poly.geom_type != "Polygon":
        return None
    kx = float(m_per_deg_lon(lat0))
    lons = np.array([ring[0][0] + x / kx for x in (poly.bounds[0], poly.bounds[2])])
    lats = np.array([ring[0][1] + y / M_PER_DEG_SPHERE for y in (poly.bounds[1], poly.bounds[3])])
    c0, c1 = sorted(int((lo - tf.c) // tf.a) for lo in lons)
    r0, r1 = sorted(int((la - tf.f) // tf.e) for la in lats)
    c0, c1, r0, r1 = max(c0, 0), min(c1 + 1, arr.shape[1]), max(r0, 0), min(r1 + 1, arr.shape[0])
    if c1 <= c0 or r1 <= r0:
        return None
    cc, rr = np.meshgrid(np.arange(c0, c1), np.arange(r0, r1))
    lon = tf.c + (cc.ravel() + 0.5) * tf.a
    lat = tf.f + (rr.ravel() + 0.5) * tf.e
    x, y = (lon - ring[0][0]) * kx, (lat - ring[0][1]) * M_PER_DEG_SPHERE
    ok = shapely.contains_xy(poly, x, y)
    z = arr[rr.ravel()[ok], cc.ravel()[ok]]
    return x[ok], y[ok], z


def two_plane_ridge(x, y, z):
    """``(angle_deg, gain)`` of the best two-plane (gable) fit to cells, ridge angle mod 180, and
    ``gain`` = 1 - SSE(two planes) / SSE(one plane); also ``ambiguity`` = best SSE of an angle at
    least 30 degrees away over the best SSE. None with too few cells."""
    import numpy as np
    if len(z) < 40:
        return None
    A = np.column_stack([x, y, np.ones_like(x)])
    coef, *_ = np.linalg.lstsq(A, z, rcond=None)
    sse1 = float(((A @ coef - z) ** 2).sum()) + 1e-9
    best = {}
    for deg in range(0, 180, 5):
        th = math.radians(deg)
        u = -math.sin(th) * x + math.cos(th) * y
        b = np.inf
        for u0 in np.percentile(u, np.linspace(25, 75, 11)):
            B = np.column_stack([-np.abs(u - u0), np.ones_like(u)])
            c, *_ = np.linalg.lstsq(B, z, rcond=None)
            if c[0] <= 0:
                continue
            b = min(b, float(((B @ c - z) ** 2).sum()))
        best[deg] = b
    deg = min(best, key=best.get)
    far = [v for d, v in best.items() if min(abs(d - deg), 180 - abs(d - deg)) >= 30]
    return float(deg), 1 - best[deg] / sse1, (min(far) / max(best[deg], 1e-9) if far else 1.0)


def truth_patches(nd, geom, a_min=12.0, width_min=2.5):
    """Ground-level patches (nDSM below 2 m) inside a footprint that the survey sees, as lon/lat
    polygons. Cells are the nDSM grid; patches keep 1 m from the outline. No solidity test:
    truth is whatever reads as ground."""
    import numpy as np
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    got = footprint_cells(nd, geom, 1.0)
    if got is None or len(got[2]) < 30 or np.percentile(got[2], 90) < 3.0:
        return None
    x, y, z = got
    arr, tf = nd
    ring = geom["coordinates"][0]
    kx = float(m_per_deg_lon(_local(ring)[1]))
    cell = abs(tf.a) * kx * 1.02          # a hair over a cell so neighbours fuse
    low = z < 2.0
    if low.sum() == 0:
        return []
    boxes = [Polygon([(a - cell / 2, b - cell / 2), (a + cell / 2, b - cell / 2),
                      (a + cell / 2, b + cell / 2), (a - cell / 2, b + cell / 2)])
             for a, b in zip(x[low], y[low], strict=True)]
    merged = unary_union(boxes)
    out = []
    for g in getattr(merged, "geoms", [merged]):
        if g.area < a_min or g.buffer(-width_min / 2).is_empty:
            continue
        out.append((g, ring[0], kx))
    return out


def to_local(ring_ll, ref, kx):
    return [((x - ref[0]) * kx, (y - ref[1]) * M_PER_DEG_SPHERE) for x, y in ring_ll]


# --------------------------------------------------------------------------- measure

def measure_all(out, limit=0):
    from city2stl import roof_imagery as ri
    feats, shapes = load_buildings()
    nd = load_ndsm()
    idx = [i for i, f in enumerate(feats)
           if f["geometry"]["type"] == "Polygon" and ri.footprint_area_m2(f["geometry"]) >= ri.MIN_AREA_M2]
    if limit:
        idx = idx[::max(1, len(idx) // limit)]
    print(f"{len(idx)} footprints of {len(feats)}", flush=True)
    t0 = time.time()
    sh = {i: shapes[i][0] for i in idx}
    got = ri.measure_missing(feats, sh, progress=lambda n, m: print(f"  {n}/{m} {time.time() - t0:.0f}s", flush=True)
                             if n % 2000 == 0 else None)
    print(f"measured in {time.time() - t0:.0f}s", flush=True)
    rows = []
    for i in idx:
        r = got.get(i)
        g = feats[i]["geometry"]
        rec = {"i": i, "shape": shapes[i][0], "tagged": shapes[i][1], "area": ri.footprint_area_m2(g),
               "m": r, "osm_holes": len(g["coordinates"]) - 1}
        px = ri.footprint_px(g)
        rec["tile"] = (int(px[:, 0].mean() // 256), int(px[:, 1].mean() // 256))
        # lidar truth: ridge angle for pitched, ground patches for all
        if shapes[i][0] in ("gabled", "hipped", "pyramidal") and rec["area"] >= RIDGE_MIN_AREA:
            c = footprint_cells(nd, g, 1.0)
            rec["truth_ridge"] = two_plane_ridge(*c) if c is not None else None
        rec["has_lidar"] = None
        if r and r.get("holes") is not None:
            for h in r["holes"]:
                has, roofed = ri.lidar_check(nd, g, h["ring_ll"])
                h["has_lidar"], h["roofed"] = has, roofed
        tp = truth_patches(nd, {"coordinates": [g["coordinates"][0]]})
        rec["truth_n"] = None if tp is None else len(tp)
        if tp is not None and r is not None:
            ref, kx = (g["coordinates"][0][0], float(m_per_deg_lon(_local(g["coordinates"][0])[1])))
            from shapely.geometry import Polygon
            cands = [Polygon(to_local(h["ring_ll"], ref, kx)) for h in r.get("holes", [])]
            rec["truth_hit"] = [any(c.intersection(t[0]).area >= 0.3 * t[0].area for c in cands) for t in tp]
        osm_h = g["coordinates"][1:]
        if osm_h and r is not None:
            from shapely.geometry import Polygon
            ref = g["coordinates"][0][0]
            kx = float(m_per_deg_lon(_local(g["coordinates"][0])[1]))
            cands = [Polygon(to_local(h["ring_ll"], ref, kx)) for h in r.get("holes", [])]
            hit = []
            for hole in osm_h:
                hp = Polygon(to_local(hole, ref, kx))
                hit.append(hp.is_valid and hp.area > 0 and any(c.intersection(hp).area >= 0.3 * hp.area for c in cands))
            rec["osm_hit"] = hit
            rec["osm_area"] = [Polygon(to_local(h, ref, kx)).area for h in osm_h]
        rows.append(rec)
    pickle.dump(rows, open(out, "wb"))
    print("wrote", out, flush=True)


# --------------------------------------------------------------------------- report

def _ang(a, b):
    d = abs(a - b) % 180
    return min(d, 180 - d)


def report(raw):
    import numpy as np

    from city2stl import roof_imagery as ri
    rows = pickle.load(open(raw, "rb"))
    print(f"{len(rows)} footprints, {sum(1 for r in rows if r['m'] is None)} without tiles")
    # registration
    reg = [r["m"] for r in rows if r["m"]]
    q = np.array([m["reg"] for m in reg])
    sh = np.array([m["shift"] for m in reg])
    print("\n== registration ==")
    print("quality percentiles 5/25/50/75/95:", np.round(np.percentile(q, [5, 25, 50, 75, 95]), 2))
    for gate in (1.1, 1.2, 1.3, 1.5, 1.8):
        ok = q >= gate
        mag = np.hypot(*sh[ok].T) * 0.477
        print(f" gate {gate}: kept {ok.mean():.2f}  median |shift| {np.median(mag):.2f} m  "
              f"median dx,dy px {np.median(sh[ok][:, 0]):.1f},{np.median(sh[ok][:, 1]):.1f}  "
              f"share at search edge {(np.abs(sh[ok]).max(1) >= ri.SHIFT_MAX_PX).mean():.2f}")

    # ridge
    print("\n== ridge (truth: CNIG two-plane fit, pitched footprints >= 150 m2) ==")
    T = []
    for r in rows:
        tr = r.get("truth_ridge")
        m = r["m"]
        if not tr or not m or not m.get("ridge"):
            continue
        ang, gain, amb = tr
        if gain < 0.4 or amb < 1.15:
            continue
        # long axis angle in the metric frame is recomputed from the pixel frame: |d| symmetric
        T.append((r, ang, m["ridge"]))
    print("usable truth footprints:", len(T))
    # long axis angle in metric frame: recompute from OSM
    feats, _ = load_buildings()
    out = []
    for r, ang, f in T:
        g = feats[r["i"]]["geometry"]["coordinates"][0]
        xy, _ = _local(g)
        from shapely.geometry import Polygon
        la, _, _ = ri.long_axis_angle(Polygon(xy))
        d = _ang(math.degrees(la), ang)
        if 37 <= d <= 53:
            continue
        out.append((d > 45, f, r))
    n = len(out)
    along = np.mean([not t for t, _, _ in out])
    print(f"unambiguous: {n};  long axis agreement {along:.3f}  (share truly across: {1 - along:.3f})")
    for tconf in (0.0, 0.05, 0.1, 0.15, 0.2, 0.25, 0.3, 0.4, 0.5):
        sub = [(t, f) for t, f, _ in out if ri.ridge_conf(f) >= tconf and f["s_across"] == f["b_across"]]
        if len(sub) < 10:
            continue
        img = np.mean([t == f["s_across"] for t, f in sub])
        lax = np.mean([not t for t, f in sub])
        # overall: across emitted only on a confident across vote
        fixed = sum(1 for t, f, _ in out if ri.ridge_across(f, tconf) and t)
        broke = sum(1 for t, f, _ in out if ri.ridge_across(f, tconf) and not t)
        print(f" conf>={tconf:.2f}: confident n={len(sub):4d}  imagery {img:.3f}  long-axis {lax:.3f}  "
              f"delta {100 * (img - lax):+.1f} pts | emitted across: fixed {fixed} broke {broke} "
              f"overall {100 * (fixed - broke) / n:+.2f} pts")
    # components alone
    for name, key in (("texture only", "s_across"), ("brightness only", "b_across")):
        print(f" {name}: agreement {np.mean([t == f[key] for t, f, _ in out]):.3f}")

    # hip
    print("\n== hip (tagged OSM roofs) ==")
    lab = [(r["shape"] in ("hipped", "pyramidal"), ri.hip_score(r["m"].get("hip")), r["tile"], r["m"].get("hip"))
           for r in rows if r["tagged"] and r["shape"] in ("hipped", "pyramidal", "gabled") and r["m"]
           and r["m"].get("hip")]
    print("n =", len(lab), " hipped/pyramidal:", sum(1 for a, *_ in lab if a), " gabled:", sum(1 for a, *_ in lab if not a))
    if len(lab) >= 8:
        y = np.array([a for a, *_ in lab])
        s = np.array([b for _, b, *_ in lab])
        if 0 < y.sum() < len(y):
            auc = np.mean([(sp > sn) + 0.5 * (sp == sn) for sp in s[y] for sn in s[~y]])
            print(f"AUC {auc:.3f}")
            for name in ("hl", "ctrl", "tri"):
                v = np.array([f[name] for *_, f in lab])
                a2 = np.mean([(sp > sn) + 0.5 * (sp == sn) for sp in v[y] for sn in v[~y]])
                print(f"  AUC of {name}: {a2:.3f}")
            groups = np.array([hash(t) % 5 for _, _, t, _ in lab])
            correct = 0
            for k in range(5):
                tr, te = groups != k, groups == k
                if not te.any() or y[tr].all() or (~y[tr]).all():
                    continue
                cand = np.unique(s[tr])
                bacc = [0.5 * ((s[tr][y[tr]] >= c).mean() + (s[tr][~y[tr]] < c).mean()) for c in cand]
                c = cand[int(np.argmax(bacc))]
                correct += int(((s[te] >= c) == y[te]).sum())
            print(f"grouped 5-fold accuracy {correct / len(lab):.3f}  (always-hipped baseline {y.mean():.3f})")
    gab = [ri.hip_score(r["m"].get("hip")) for r in rows if r["shape"] == "gabled" and not r["tagged"]
           and r["m"] and r["m"].get("hip")]
    if gab:
        print("classifier-gabled hip score percentiles 50/75/90/95/99:", np.round(np.percentile(gab, [50, 75, 90, 95, 99]), 2))

    # courtyards
    print("\n== courtyards ==")
    cands = [(h, r) for r in rows if r["m"] and r["m"].get("holes") for h in r["m"]["holes"]]
    withl = [(h, r) for h, r in cands if h.get("has_lidar")]
    print(f"candidates {len(cands)}, with lidar {len(withl)}")
    for tmin in (0.0, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8):
        sel = [(h, r) for h, r in withl if h["score"] >= tmin]
        if not sel:
            continue
        prec = np.mean([h["roofed"] < 0.5 for h, _ in sel])
        print(f" score>={tmin:.1f}: n={len(sel):4d} precision {prec:.3f}")
    hits = [x for r in rows if r.get("truth_hit") is not None for x in r["truth_hit"]]
    print(f"lidar ground patches: {len(hits)}, recalled {np.mean(hits) if hits else float('nan'):.3f}")
    oh = [(h, a) for r in rows if r.get("osm_hit") for h, a in zip(r["osm_hit"], r["osm_area"], strict=True)]
    big = [h for h, a in oh if a >= ri.A_MIN_M2]
    print(f"OSM courtyards >= {ri.A_MIN_M2} m2: {len(big)}, recalled {np.mean(big) if big else float('nan'):.3f}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["measure", "report"])
    ap.add_argument("--out", default="roof_imagery_raw.pkl")
    ap.add_argument("--raw", default="roof_imagery_raw.pkl")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()
    _guard()
    if a.cmd == "measure":
        measure_all(a.out, a.limit)
    else:
        report(a.raw)


if __name__ == "__main__":
    main()
