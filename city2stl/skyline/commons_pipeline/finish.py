"""Truth, scores, per-building heights, Street View comparison and the report page (script 17)."""

from __future__ import annotations

import json

import numpy as np

from city2stl.skyline import benchmark as bm
from city2stl.skyline import benchmark_geometry as bm_geo
from city2stl.skyline import commons_photos as cp
from city2stl.skyline import photo_heights as ph
from city2stl.skyline import skyline_match as sm
from geo2stl.geo import M_PER_DEG_SPHERE, m_per_deg_lon

from .cache import cache_dir, thumb_url
from .measure import _flag, _tower_key, _untagged_table
from .placement import _keep
from .readings import _building_row, _cross_check, _photo_groups, _shared_buildings


def _remap(results: list[dict], towers) -> None:
    """Point saved readings at the current tower table by footprint key (readings without a key
    keep their index and are checked against their OSM height in ``finish``); a tower gone from OSM is dropped."""
    if not any("fp" in t for r in results for t in r.get("towers") or []):
        return
    index = {_tower_key(towers, i): i for i in range(len(towers.verts))}
    for r in results:
        kept = []
        for t in r.get("towers") or []:
            if "fp" in t:
                if t["fp"] not in index:
                    continue
                t["tower"] = index[t["fp"]]
            kept.append(t)
        r["towers"] = kept


def _truth(args, footprints: dict) -> dict:
    """Benchmark truth for ``footprints``; with ``--no-truth-fetch`` only what the cache holds
    (no survey or paid 3D Tiles reads)."""
    if getattr(args, "no_truth_fetch", False):
        cache = bm.load_truth_cache(args.region)
        return {k: cache[k] for k in footprints if k in cache}
    return bm.footprint_truth(args.region, footprints, bm_geo.REGIONS.get(args.region))


def _score(pred, tru):
    pred, tru = np.array(pred), np.array(tru)
    if len(pred) < 2:
        return {"n": int(len(pred))}
    rel = bm.relative_metrics(pred, tru)
    e = pred - tru
    return {"n": int(len(pred)), "mae_m": round(float(np.abs(e).mean()), 1),
            "bias_m": round(float(e.mean()), 1), "pair_order": rel["pair_order"],
            "spearman": rel["spearman"]}


def finish(args, results: list[dict], meta: dict, osm: dict) -> dict:
    """Keep decision, per-building medians, truth, Street View comparison, report page."""
    towers = sm.tower_table(osm["buildings"]["features"])
    # truth for every candidate tower (kept or not), so the gates can be judged per photo
    _remap(results, towers)
    all_t = {t["tower"] for r in results for t in r.get("towers") or []}
    # saved results index the tower table; a changed region box or OSM data makes another table
    # (names can't tell: measured readings carry display names such as "tower65")
    stale = [t for r in results for t in r.get("towers") or []
             if t["tower"] >= len(towers.verts) or ("fp" not in t and t["osm_m"] != float(towers.height_m[t["tower"]]))]
    if stale:
        raise SystemExit(f"photo_results.json was written with another tower table ({len(stale)} "
                         "readings don't match; the region box or OSM changed): run without --rescore")
    rings = {ti: [list(towers.to_ll(x, y))[::-1] for x, y in towers.verts[ti]] for ti in all_t}
    keys = {ti: bm.footprint_key(r) for ti, r in rings.items()}
    truth = _truth(args, {keys[ti]: rings[ti] for ti in all_t})

    def tv(ti):
        t = truth.get(keys[ti], {})
        return t.get("truth_m") if t.get("status") == "confirmed" else None

    for r in results:
        r["kept"] = _keep(r, args)
        h_cam = (r.get("camera") or {}).get("h_cam", 2.0)
        for t in r.get("towers") or []:
            t["truth_m"] = tv(t["tower"])
            f = _flag(t, h_cam)
            if f:
                t["flag"] = f                    # shown on the card, left out of everything below
        ct = [(t["photo_m"], t["truth_m"]) for t in r.get("towers") or []
              if t["truth_m"] is not None and not t.get("flag")]
        r["score"] = _score(*zip(*ct, strict=True)) if len(ct) >= 2 else {}
    _cross_check(results)
    per: dict[int, list[tuple[float, float, float | None]]] = {}   # (median m, peak m, photo dev)
    confirmed: dict[int, int] = {}
    for r in results:
        if r["kept"]:
            for t in r.get("towers") or []:
                if not t.get("flag"):
                    per.setdefault(t["tower"], []).append(
                        (t["photo_m"], t.get("peak_m", t["photo_m"]), r.get("anchor_dev_m")))
                    confirmed[t["tower"]] = confirmed.get(t["tower"], 0) + bool(t.get("confirmed_by"))
    weighted = getattr(args, "photo_weighting", "anchor") == "anchor"
    use_peak = getattr(args, "top", "auto") == "auto"
    rows = [_building_row(towers, keys, ti, rd, weighted, use_peak, confirmed.get(ti, 0), tv(ti))
            for ti, rd in per.items()]

    # Street View on the same buildings (footprint key, else nearest centroid within 30 m)
    heights = args.report / "heights.json"
    if heights.exists():
        _name, sv_b = bm.load_report(heights)
        sv_key = {b["key"]: b for b in sv_b}
        cent = np.array([[b["centroid_lat"], b["centroid_lon"]] for b in sv_b])
        for r in rows:
            b = sv_key.get(r["key"])
            if b is None and len(cent):
                c = np.mean(np.array(rings[r["tower"]])[:, ::-1], axis=0)
                dd = np.hypot((cent[:, 0] - c[0]) * M_PER_DEG_SPHERE,
                            (cent[:, 1] - c[1]) * m_per_deg_lon(c[0]))
                j = int(np.argmin(dd))
                b = sv_b[j] if dd[j] < 30 else None
            if b is not None:
                # the Street View reading itself, also where T28 withheld it
                sv_m = b.get("street_view_m", b["effective_height_m"])
                if sv_m is not None:
                    r["street_view_m"] = round(float(sv_m), 1)
    conf = [r for r in rows if r["truth_m"] is not None]
    both = [r for r in conf if "street_view_m" in r]
    routes = ("labels", "recorded", "linked", "search")
    summary = {
        "gates": {"max_misfit": args.max_misfit, "min_margin": args.min_margin,
                  "max_anchor_dev_m": args.max_anchor_dev, "min_towers": args.min_towers,
                  "rescue_anchor_dev_m": args.rescue_anchor_dev,
                  "rescue_min_towers": args.rescue_min_towers,
                  "rescue_max_p75_m": args.rescue_max_p75},
        "photos": {k: sum(1 for r in results if r["route"] == k) for k in routes},
        "kept": {k: sum(1 for r in results if r["route"] == k and r["kept"]) for k in routes},
        "buildings_measured": len(rows), "confirmed": len(conf),
        "photo_vs_truth": _score([r["photo_m"] for r in conf], [r["truth_m"] for r in conf]),
        # buildings two photos agree on, against those only one photo read (_cross_check)
        "photo_vs_truth_confirmed": _score([r["photo_m"] for r in conf if r["n_confirmed"]],
                                           [r["truth_m"] for r in conf if r["n_confirmed"]]),
        "photo_vs_truth_single": _score([r["photo_m"] for r in conf if r["n_photos"] == 1],
                                        [r["truth_m"] for r in conf if r["n_photos"] == 1]),
        "readings": {"used": sum(1 for r in results if r["kept"] for t in r.get("towers") or []
                                 if not t.get("flag")),
                     "not_used": {f: sum(1 for r in results if r["kept"] for t in r.get("towers") or []
                                         if t.get("flag") == f)
                                  for f in sorted({t["flag"] for r in results if r["kept"]
                                                   for t in r.get("towers") or [] if t.get("flag")})}},
        # the yardstick photos must beat to add anything: every photo-measured tower has an
        # OSM height (the tower table is OSM-tagged towers only)
        "osm_vs_truth": _score([r["osm_m"] for r in conf], [r["truth_m"] for r in conf]),
        "same_buildings": {
            "photo": _score([r["photo_m"] for r in both], [r["truth_m"] for r in both]),
            "osm": _score([r["osm_m"] for r in both], [r["truth_m"] for r in both]),
            "street_view": _score([r["street_view_m"] for r in both], [r["truth_m"] for r in both])},
    }
    out = {"summary": summary, "buildings": rows}
    if args.untagged:
        out["untagged"], summary["untagged"] = _untagged_rows(args, results, towers, osm)
    (args.report / "photo_heights.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    cards = []
    for r in sorted(results, key=lambda r: (not r["kept"], r["route"], r["title"])):
        m = meta[r["key"]]
        why = r.get("why") or (f"OSM disagreement {r.get('anchor_dev_m')} m" if r.get("gate_ok") else "")
        cards.append({"key": r["key"], "title": m["title"][5:], "page": m.get("page_url", ""),
                      "thumb": thumb_url(m["url"]),
                      "attribution": f"{m.get('author', '')}, {m.get('licence', '')}",
                      "camera": r.get("camera") or {},
                      "status": ("measured" if r["kept"] else f"not kept: {why}") + f" ({r['route']})",
                      "towers": r.get("towers") if r["kept"] else [],
                      "score": r["score"] if r["kept"] else {}})
        if r["kept"]:
            cards[-1].update(_card_images(args, r, m, towers, osm))
    (args.report / "photos.json").write_text(json.dumps(cards, indent=1), encoding="utf-8")
    # photos grouped by the buildings they show, and every building 2+ photos read (after the
    # cards, so the readings carry their card numbers)
    titles = {r["key"]: meta[r["key"]]["title"][5:] for r in results}
    for r in results:
        r["title"] = titles[r["key"]]
    summary["groups"] = _photo_groups(results)
    summary["shared_buildings"] = _shared_buildings(results, rows)
    (args.report / "photo_heights.json").write_text(json.dumps(out, indent=1), encoding="utf-8")
    if heights.exists():
        from city2stl.skyline.benchmark_report import write_benchmark_page
        _name, sv_b = bm.load_report(heights)
        tr = _truth(args, {b["key"]: b["footprint_lonlat"] for b in sv_b})
        sc = {"region": args.region, "report": str(heights), **bm.score_buildings(sv_b, tr)}
        write_benchmark_page(args.report, sc, sv_b, tr, cards, photo_summary=summary)
    return summary


def _card_images(args, r: dict, m: dict, towers, osm: dict) -> dict:
    """The kept photo with its towers numbered, and its location map (``photo_cards``), so the
    page shows which building is which, as the seed pages do (user, 2026-10-05). Labels go
    onto the card's tower rows. Nothing when the cached image or outline is missing."""
    from city2stl.skyline import photo_cards as pc

    cache = cache_dir(args.region)
    img_path = cp.img_dir(cache) / f"{r['key']}.jpg"
    if "prof" not in _CARDS:
        _CARDS["prof"] = dict(np.load(cache / "profiles.npz"))
        _CARDS["bg"] = pc.background_polygons(osm["buildings"]["features"], towers)
    if not img_path.exists() or r["key"] not in _CARDS["prof"]:
        return {}
    import cv2

    c = r["camera"]
    out = pc.render_photo_card(
        cv2.cvtColor(cv2.imread(str(img_path)), cv2.COLOR_BGR2RGB),
        sm.PhotoProfile(_CARDS["prof"][r["key"]].astype(float), m["px"][0], m["px"][1]),
        ph.PhotoPose(c["lat"], c["lon"], c["heading_deg"], c["hfov_deg"], projection=c.get("projection", "pinhole")), c.get("h_cam", 2.0),
        towers, r.get("towers") or [], _CARDS["bg"], args.report / "assets" / "photos", r["key"])
    for t in r.get("towers") or []:
        t["label"] = out["labels"].get(t["tower"])
    return {"overlay": out["overlay"], "map": out["map"]}


_CARDS: dict = {}


def _untagged_rows(args, results: list[dict], towers, osm: dict) -> tuple[list[dict], dict]:
    """Untagged buildings two or more kept photos agree on, with their truth and a score.

    Truth comes from the region's benchmark truth cache; ``--untagged-truth fetch`` measures
    the missing footprints (3D Tiles reads: hours and money for a city's worth of them)."""
    table = _untagged_table(args.region, osm, towers,
                            getattr(args, "untagged_candidates", "all"))
    kept = [r for r in results if r.get("kept")]
    per = [{int(k): v for k, v in (r.get("untagged") or {}).items()} for r in kept]
    spans = [{int(k): tuple(v) for k, v in (r.get("untagged_spans") or {}).items()}
             for r in kept]
    # viewpoints, not files: the kept cameras in the table's frame, and each candidate's
    # centroid for the view-direction spread
    cams = [table.to_xy(r["camera"]["lat"], r["camera"]["lon"]) for r in kept]
    seen = {i for p in per for i in p}
    centroids = {i: tuple(table.verts[i][:-1].mean(axis=0)) for i in seen}
    rules: dict = {}
    est = ph.agreed_with_occlusion(per, spans, cams_xy=cams, centroids=centroids, stats=rules,
                                   far_first=getattr(args, "untagged_far_first", False))
    rules["capped_readings"] = sum(len(r.get("untagged_capped") or {}) for r in kept)
    vp = ph.viewpoints(cams)
    keys = {i: table.keys[i] for i in est}
    if getattr(args, "untagged_truth", "cached") == "fetch" and est:
        truth = bm.footprint_truth(args.region, {keys[i]: table.rings[i] for i in est},
                                   bm_geo.REGIONS.get(args.region))
    else:
        truth = bm.load_truth_cache(args.region)
    rows = []
    for i, (h, n, spread) in sorted(est.items()):
        t = truth.get(keys[i]) or {}
        photos = [k for k, p in enumerate(per) if i in p]
        rows.append({"index": i, "name": table.names[i], "key": keys[i],
                     "footprint_lonlat": table.rings[i], "photo_m": round(h, 1), "n_photos": n,
                     "n_viewpoints": len({vp[k] for k in photos}),
                     "view_spread_deg": round(ph.view_spread_deg(
                         centroids[i], [cams[k] for k in photos]), 1),
                     "spread_m": round(spread, 1),
                     "truth_m": t.get("truth_m") if t.get("status") == "confirmed" else None})
    conf = [r for r in rows if r["truth_m"] is not None]
    return rows, {"buildings": len(rows), "confirmed": len(conf), "rules": rules,
                  "with_truth": sum(1 for i in est if keys[i] in truth),
                  "photo_vs_truth": _score([r["photo_m"] for r in conf],
                                           [r["truth_m"] for r in conf])}
