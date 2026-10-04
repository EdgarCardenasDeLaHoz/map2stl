#!/usr/bin/env python3
"""F-WEB2: a region's building heights from its Commons skyline photos, end to end.

    python -m city2stl.skyline.scripts.17_photo_pipeline --region miami --report <report dir> [--workers 8]

Needs the outline cache (``13_photo_profiles``), and for links the embeddings and pairs
(``16_embed_photos``, ``14_group_photos``). No downloads, no GPU: everything works on cached
outlines. Each photo gets a camera by the best route it qualifies for, and is kept only when
the route's gate passes:

1. labels (``sites/annotations/<region>_*.json``): ``photo_localize.solve_from_labels``;
   the unlabelled copy in ``same_pose_as`` is measured;
2. recorded location + EXIF FOV: ``skyline_match.refine`` within 500 m; gate misfit <= 0.3
   and skyline coverage >= 25 %;
3. unlocated, linked to a located photo of the same spot (``photo_groups.embed_links``):
   refine from that photo's kept camera; same gate;
4. unlocated with EXIF FOV, not linked: full ``skyline_match.locate``; gate: the best pose
   beats the runner-up by >= 0.1 misfit (EXIF-FOV validation, 2026-10-04: right 0.17-0.19,
   wrong <= 0.035).

Towers in each kept photo: ``photo_heights.measure_towers`` + ``loo_heights``, towers beyond
``--max-dist-m`` dropped (a tower 7.7 km away read +208 m). Per building: median over photos.
Every candidate photo is measured, kept or not, and saved to ``photo_results.json`` with its
towers' truth, so the gates (incl. ``--max-anchor-dev``: towers must agree with their own OSM
heights) can be tuned with ``--rescore`` without placing photos again. Refinement tries camera
heights ``--h-cams`` (street, deck, rooftop). Scored against the benchmark truth and against
Street View on the same buildings (the report's ``heights.json``). Writes ``photos.json``,
``photo_heights.json`` and the report's ``benchmark.html``.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
    os.environ.setdefault(_v, "1")  # one thread per worker: the pool is the parallelism

import argparse  # noqa: E402
import glob  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import math  # noqa: E402
from concurrent.futures import ProcessPoolExecutor, as_completed  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

from city2stl.skyline import benchmark as bm  # noqa: E402
from city2stl.skyline import commons_photos as cp  # noqa: E402
from city2stl.skyline import photo_groups as pg  # noqa: E402
from city2stl.skyline import photo_heights as ph  # noqa: E402
from city2stl.skyline import skyline_match as sm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
log = logging.getLogger("photo_pipeline")
_W: dict = {}


def _init(region: str):
    """Worker state: towers and outlines, loaded once per process."""
    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

    osm, _ = _load_osm_for_region(_load_region_bbox(region))
    _W["towers"] = sm.tower_table(osm["buildings"]["features"])
    d = ROOT / "runs" / "commons_cache" / region
    _W["prof"] = dict(np.load(d / "profiles.npz"))


def _profile(m: dict) -> sm.PhotoProfile:
    return sm.PhotoProfile(_W["prof"][m["key"]].astype(float), m["px"][0], m["px"][1])


def _measure(prof, pose: ph.PhotoPose, max_dist_m: float, h_cam: float = 2.0):
    """Towers measured at ``pose``, and how far the leave-one-out heights sit from the towers'
    own OSM heights (median |est - osm|, m). Each estimate excludes its own tower from the tilt
    and camera-height fit, so the comparison is fair; a wrong pose makes towers contradict
    their tags (the kept-but-wrong Miami photo read 74.8 m off the truth)."""
    towers = _W["towers"]
    ms = [m for m in ph.measure_towers(prof, towers, pose, h_cam=h_cam) if m.dist_m <= max_dist_m]
    est = ph.loo_heights(ms)
    rows = [{"tower": m.index, "name": m.name, "dist_m": round(m.dist_m),
             "photo_m": round(est[m.index], 1), "osm_m": m.osm_height_m}
            for m in ms if m.index in est]
    dev = float(np.median([abs(r["photo_m"] - r["osm_m"]) for r in rows])) if rows else None
    return rows, dev


def _job(job: dict) -> dict:
    """One photo: pose by its route, gate, measure. Runs in a worker."""
    m, route = job["meta"], job["route"]
    prof = _profile(m)
    towers = _W["towers"]
    cover = float(np.isfinite(prof.y_top).mean())
    res = {"key": m["key"], "title": m["title"], "route": route, "coverage": cover}
    if route == "labels":
        p = job["pose"]
        pose = ph.PhotoPose(p["lat"], p["lon"], p["heading_deg"], p["hfov_deg"])
        res.update(camera={**p, "source": "solved from labels"}, kept=True)
    elif route in ("recorded", "linked"):
        pr = job["prior"]
        prior = sm.Hit(pr["lat"], pr["lon"], pr.get("heading_deg") or 0.0, pr["hfov_deg"],
                       0.0, 0.0, 1.0, 0)
        # camera height: street, station/deck, high deck/rooftop (best misfit wins); 19 of
        # 30 recorded Miami photos failed the misfit gate at 2 m, some from ship decks
        h_best, hit = 2.0, None
        for h in job.get("h_cams", (2.0,)):
            cand = sm.refine(prof, towers, prior, radius_m=500.0, step_m=50.0,
                             fov_span=job.get("fov_span", 0.08), h_cam=h)
            if hit is None or cand.misfit < hit.misfit:
                h_best, hit = h, cand
        moved = math.hypot((hit.lat - pr["lat"]) * 111320,
                           (hit.lon - pr["lon"]) * 111320 * math.cos(math.radians(pr["lat"])))
        kept = hit.misfit <= job["max_misfit"] and cover >= job["min_coverage"]
        pose = ph.PhotoPose(hit.lat, hit.lon, hit.heading_deg, hit.hfov_deg)
        res.update(camera={"lat": hit.lat, "lon": hit.lon, "heading_deg": hit.heading_deg,
                           "hfov_deg": hit.hfov_deg, "misfit": hit.misfit, "moved_m": moved,
                           "h_cam": h_best,
                           "source": f"{route}, refined {moved:.0f} m, camera {h_best:.0f} m up "
                                     f"(misfit {hit.misfit:.2f})"},
                   kept=kept, why=None if kept else f"misfit {hit.misfit:.2f}, coverage {cover:.0%}")
    else:  # search
        hits = sm.locate(prof, towers, step_m=300.0, fovs_deg=(m["hfov_deg"],), fov_span=0.08)
        if not hits:
            return {**res, "gate_ok": False, "why": "no fit", "towers": [], "anchor_dev_m": None}
        best = hits[0]
        x0, y0 = towers.to_xy(best.lat, best.lon)
        rival = next((h for h in hits[1:]
                      if math.hypot(*np.subtract(towers.to_xy(h.lat, h.lon), (x0, y0))) > 500), None)
        margin = (rival.misfit - best.misfit) if rival else 1.0
        kept = margin >= job["min_margin"] and cover >= job["min_coverage"]
        pose = ph.PhotoPose(best.lat, best.lon, best.heading_deg, best.hfov_deg)
        res.update(camera={"lat": best.lat, "lon": best.lon, "heading_deg": best.heading_deg,
                           "hfov_deg": best.hfov_deg, "misfit": best.misfit, "margin": margin,
                           "source": f"skyline search (misfit {best.misfit:.2f}, margin {margin:.2f})"},
                   kept=kept, why=None if kept else f"margin {margin:.2f}")
    # every candidate is measured, kept or not, so the gates can be tuned afterwards against
    # the truth (``--rescore``) without placing the photos again
    res["gate_ok"] = res.pop("kept")
    res["towers"], res["anchor_dev_m"] = _measure(prof, pose, job["max_dist_m"],
                                                  (res.get("camera") or {}).get("h_cam", 2.0))
    return res


def _run(ex, jobs: list[dict], label: str) -> list[dict]:
    """Submit ``jobs`` and log each photo as it finishes, so a run can be reviewed while it
    works (user, 2026-10-04), not only between stages."""
    out = []
    futs = [ex.submit(_job, j) for j in jobs]
    for i, f in enumerate(as_completed(futs), 1):
        r = f.result()
        out.append(r)
        n = len(r.get("towers") or [])
        dev = r.get("anchor_dev_m")
        log.info("[%s %d/%d] %-8s %s %3d towers, OSM dev %s | %s | %s", label, i, len(jobs),
                 r["route"], "gate ok" if r.get("gate_ok") else "gate no", n,
                 "-" if dev is None else f"{dev:.0f} m", r.get("why") or "", r["title"][5:55])
    return out


def _thumb(url: str) -> str:
    import re
    return re.sub(r"/(\d+)px-", "/500px-", url)


def _keep(r: dict, args) -> bool:
    """The keep decision: the route's gate, and (when set) agreement with OSM heights.

    Rescue (``--rescue-anchor-dev``): a photo failing its fit/margin gate is kept when at least
    ``--rescue-min-towers`` towers sit within that many m (median) of their OSM heights. Why:
    the margin gate mostly measures line-of-sight ambiguity; on Miami run 2 (2026-10-04),
    "fit/margin OR (OSM dev <= 20 m and >= 12 towers)" measured 193 buildings (142 confirmed)
    against 142 (100) at the same accuracy (MAE 23.2 vs 21.7 m, pair order 0.86 vs 0.85).
    """
    if not r.get("gate_ok"):
        dev = r.get("anchor_dev_m")
        return (args.rescue_anchor_dev is not None and dev is not None
                and dev <= args.rescue_anchor_dev
                and len(r.get("towers") or []) >= args.rescue_min_towers)
    if args.max_anchor_dev is not None and r.get("route") != "labels":
        dev = r.get("anchor_dev_m")
        if dev is None or len(r.get("towers") or []) < args.min_towers or dev > args.max_anchor_dev:
            return False
    return True


def place(args, meta: dict, osm: dict) -> list[dict]:
    """Camera per photo by route, gates, and measured towers for every candidate."""
    d = ROOT / "runs" / "commons_cache" / args.region
    by_title = {m["title"]: m for m in meta.values()}
    common = {"max_misfit": args.max_misfit, "min_margin": args.min_margin,
              "min_coverage": args.min_coverage, "max_dist_m": args.max_dist_m}
    from city2stl.skyline.photo_localize import building_index, load_annotations, solve_from_labels

    index = building_index(osm["buildings"]["features"])
    jobs1, done_titles = [], set()
    for path in sorted(glob.glob(str(ROOT / "sites" / "annotations" / f"{args.region}_*.json"))):
        ann = load_annotations(path)
        w, _h = ann["image_px"]
        src = by_title.get(ann["file"])
        f_px = None
        if src and src.get("hfov_deg"):
            f_px = (w / 2) / math.tan(math.radians(src["hfov_deg"] / 2))
        pose, _ = solve_from_labels(ann, index, **({"f_prior_px": f_px} if f_px else {}))
        p = {"lat": pose.lat, "lon": pose.lon, "heading_deg": pose.heading_deg,
             "hfov_deg": pose.hfov_deg, "sigma_m": pose.sigma_m}
        for t in ann.get("same_pose_as") or [ann["file"]]:
            if t in by_title:
                jobs1.append({"meta": by_title[t], "route": "labels", "pose": p, **common})
                done_titles.add(t)
        done_titles.add(ann["file"])
    jobs2 = [{"meta": m, "route": "recorded", "h_cams": args.h_cams, **common,
              "prior": {"lat": m["lat"], "lon": m["lon"], "heading_deg": m["heading_deg"],
                        "hfov_deg": m["hfov_deg"]}}
             for m in meta.values() if m["lat"] is not None and m.get("hfov_deg")
             and m["title"] not in done_titles and not m.get("why_not_usable")]
    results = []
    with ProcessPoolExecutor(args.workers, initializer=_init, initargs=(args.region,)) as ex:
        results += _run(ex, jobs1 + jobs2, "place")
        ok_cam = {r["key"]: r["camera"] for r in results if _keep(r, args)}
        links = {}
        if (d / "embed.npz").exists() and (d / "pairs.json").exists():
            E = np.load(d / "embed.npz")
            keys = [str(k) for k in E["keys"]]
            located = {k for k in keys if meta[k]["lat"] is not None}
            links = pg.embed_links(keys, E["vecs"], json.loads((d / "pairs.json").read_text()),
                                   located)
        jobs3, jobs4 = [], []
        for k, m in meta.items():
            if m["lat"] is not None or m["title"] in done_titles:
                continue
            if k in links:
                # the linked photo's kept camera, or its recorded GPS + EXIF when its own
                # refinement was not kept (Miami run 1: 0 links used because of that)
                lm = meta[links[k][0]]
                c = ok_cam.get(links[k][0]) or {
                    "lat": lm["lat"], "lon": lm["lon"], "heading_deg": lm.get("heading_deg"),
                    "hfov_deg": lm.get("hfov_deg") or m.get("hfov_deg") or 60.0}
                jobs3.append({"meta": m, "route": "linked", "h_cams": args.h_cams, **common,
                              "fov_span": 0.08 if m.get("hfov_deg") else 0.35,
                              "prior": {"lat": c["lat"], "lon": c["lon"],
                                        "heading_deg": c["heading_deg"],
                                        "hfov_deg": m.get("hfov_deg") or c["hfov_deg"]}})
            elif m.get("hfov_deg") and m["px"][0] / m["px"][1] <= cp.MAX_ASPECT:
                jobs4.append({"meta": m, "route": "search", **common})
        log.info("[pipeline] labels %d, recorded %d (kept %d), linked %d, search %d",
                 len(jobs1), len(jobs2), len(ok_cam), len(jobs3), len(jobs4))
        results += _run(ex, jobs3 + jobs4, "place2")
    return results


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
    all_t = {t["tower"] for r in results for t in r.get("towers") or []}
    rings = {ti: [list(towers.to_ll(x, y))[::-1] for x, y in towers.verts[ti]] for ti in all_t}
    keys = {ti: bm.footprint_key(r) for ti, r in rings.items()}
    truth = bm.footprint_truth(args.region, {keys[ti]: rings[ti] for ti in all_t},
                               bm.REGIONS.get(args.region))

    def tv(ti):
        t = truth.get(keys[ti], {})
        return t.get("truth_m") if t.get("status") == "confirmed" else None

    for r in results:
        r["kept"] = _keep(r, args)
        for t in r.get("towers") or []:
            t["truth_m"] = tv(t["tower"])
        ct = [(t["photo_m"], t["truth_m"]) for t in r.get("towers") or [] if t["truth_m"] is not None]
        r["score"] = _score(*zip(*ct, strict=True)) if len(ct) >= 2 else {}
    per: dict[int, list[float]] = {}
    for r in results:
        if r["kept"]:
            for t in r.get("towers") or []:
                per.setdefault(t["tower"], []).append(t["photo_m"])
    rows = [{"tower": int(ti), "name": towers.names[ti], "key": keys[ti],
             "photo_m": round(float(np.median(hs)), 1), "n_photos": len(hs),
             "spread_m": round(float(np.ptp(hs)), 1) if len(hs) > 1 else 0.0,
             "osm_m": float(towers.height_m[ti]), "truth_m": tv(ti)}
            for ti, hs in per.items()]

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
                dd = np.hypot((cent[:, 0] - c[0]) * 111320, (cent[:, 1] - c[1]) * 100000)
                j = int(np.argmin(dd))
                b = sv_b[j] if dd[j] < 30 else None
            if b is not None:
                r["street_view_m"] = round(float(b["effective_height_m"]), 1)
    conf = [r for r in rows if r["truth_m"] is not None]
    both = [r for r in conf if "street_view_m" in r]
    routes = ("labels", "recorded", "linked", "search")
    summary = {
        "gates": {"max_misfit": args.max_misfit, "min_margin": args.min_margin,
                  "max_anchor_dev_m": args.max_anchor_dev, "min_towers": args.min_towers,
                  "rescue_anchor_dev_m": args.rescue_anchor_dev,
                  "rescue_min_towers": args.rescue_min_towers},
        "photos": {k: sum(1 for r in results if r["route"] == k) for k in routes},
        "kept": {k: sum(1 for r in results if r["route"] == k and r["kept"]) for k in routes},
        "buildings_measured": len(rows), "confirmed": len(conf),
        "photo_vs_truth": _score([r["photo_m"] for r in conf], [r["truth_m"] for r in conf]),
        # the yardstick photos must beat to add anything: every photo-measured tower has an
        # OSM height (the tower table is OSM-tagged towers only)
        "osm_vs_truth": _score([r["osm_m"] for r in conf], [r["truth_m"] for r in conf]),
        "same_buildings": {
            "photo": _score([r["photo_m"] for r in both], [r["truth_m"] for r in both]),
            "osm": _score([r["osm_m"] for r in both], [r["truth_m"] for r in both]),
            "street_view": _score([r["street_view_m"] for r in both], [r["truth_m"] for r in both])},
    }
    (args.report / "photo_heights.json").write_text(
        json.dumps({"summary": summary, "buildings": rows}, indent=1), encoding="utf-8")
    cards = []
    for r in sorted(results, key=lambda r: (not r["kept"], r["route"], r["title"])):
        m = meta[r["key"]]
        why = r.get("why") or (f"OSM disagreement {r.get('anchor_dev_m')} m" if r.get("gate_ok") else "")
        cards.append({"title": m["title"][5:], "page": m.get("page_url", ""), "thumb": _thumb(m["url"]),
                      "attribution": f"{m.get('author', '')}, {m.get('licence', '')}",
                      "camera": r.get("camera") or {},
                      "status": ("measured" if r["kept"] else f"not kept: {why}") + f" ({r['route']})",
                      "towers": r.get("towers") if r["kept"] else [],
                      "score": r["score"] if r["kept"] else {}})
    (args.report / "photos.json").write_text(json.dumps(cards, indent=1), encoding="utf-8")
    if heights.exists():
        from city2stl.skyline.benchmark_report import write_benchmark_page
        _name, sv_b = bm.load_report(heights)
        tr = bm.footprint_truth(args.region, {b["key"]: b["footprint_lonlat"] for b in sv_b},
                                bm.REGIONS.get(args.region))
        sc = {"region": args.region, "report": str(heights), **bm.score_buildings(sv_b, tr)}
        write_benchmark_page(args.report, sc, sv_b, tr, cards, photo_summary=summary)
    return summary


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--report", required=True, type=Path)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--max-misfit", type=float, default=0.3)
    ap.add_argument("--min-margin", type=float, default=0.1)
    ap.add_argument("--min-coverage", type=float, default=0.25)
    ap.add_argument("--max-dist-m", type=float, default=5000.0)
    ap.add_argument("--h-cams", type=float, nargs="+", default=[2.0, 30.0, 60.0],
                    help="camera heights tried when refining (street, station/deck, rooftop)")
    ap.add_argument("--max-anchor-dev", type=float, default=None,
                    help="keep a photo only when its towers sit within this many m (median) of "
                         "their OSM heights; off by default until calibrated")
    ap.add_argument("--min-towers", type=int, default=4)
    ap.add_argument("--rescue-anchor-dev", type=float, default=20.0,
                    help="keep a photo failing its fit/margin gate when its towers sit within "
                         "this many m of their OSM heights (median); see _keep")
    ap.add_argument("--rescue-min-towers", type=int, default=12)
    ap.add_argument("--rescore", action="store_true",
                    help="no placing: apply the gates to the saved photo_results.json")
    args = ap.parse_args()
    d = ROOT / "runs" / "commons_cache" / args.region
    meta = {m["key"]: m for m in json.loads((d / "profiles.json").read_text(encoding="utf-8"))
            if "px" in m}
    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

    osm, _ = _load_osm_for_region(_load_region_bbox(args.region))
    saved = args.report / "photo_results.json"
    if args.rescore:
        results = json.loads(saved.read_text(encoding="utf-8"))
    else:
        results = place(args, meta, osm)
        saved.write_text(json.dumps(results, indent=0), encoding="utf-8")
    summary = finish(args, results, meta, osm)
    saved.write_text(json.dumps(results, indent=0), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
