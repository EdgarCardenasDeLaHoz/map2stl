"""Routes, gates and the keep decision (script 17): which photo gets which camera, and which are kept."""

from __future__ import annotations

import glob
import json
import logging
import math
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

import numpy as np

from city2stl.skyline import commons_photos as cp
from city2stl.skyline import photo_groups as pg
from city2stl.skyline import photo_heights as ph

from .cache import ROOT, cache_dir, load_site
from .measure import _init, _job

log = logging.getLogger("photo_pipeline")


#: ``--reuse``: earlier results by photo key; a job whose route and outcome cannot change
#: under the current code is copied instead of placed again.
_REUSE: dict = {}


def _reusable(j: dict) -> dict | None:
    """The earlier result for job ``j`` when placing it again would give the same answer: same
    route, and not a failed recorded fit (those now get the FOV retry). Linked jobs are always
    re-run (their prior may come from a newly kept photo)."""
    r = _REUSE.get(j["meta"]["key"])
    def bare(t):                                # results store titles without "File:"
        return str(t or "").removeprefix("File:")

    if (r is None or bare(r.get("title")) != bare(j["meta"]["title"])
            or r.get("route") != j["route"] or j["route"] == "linked"):
        return None
    if j["route"] == "recorded" and not r.get("gate_ok"):
        return None
    if j.get("projection", "pinhole") != (r.get("camera") or {}).get("projection", "pinhole"):
        return None
    return r


def _run(ex, jobs: list[dict], label: str) -> list[dict]:
    """Submit ``jobs`` and log each photo as it finishes, so a run can be reviewed while it
    works (user, 2026-10-04), not only between stages."""
    out = [r for j in jobs if (r := _reusable(j)) is not None]
    jobs = [j for j in jobs if _reusable(j) is None]
    if out:
        log.info("[%s] reused %d earlier results, placing %d", label, len(out), len(jobs))
    futs = {ex.submit(_job, j): j for j in jobs}
    for i, f in enumerate(as_completed(futs), 1):
        try:
            r = f.result()
        except Exception as e:  # noqa: BLE001 - one bad photo must not end the run
            m = futs[f]["meta"]
            log.exception("[%s] %s failed", label, m["key"])
            r = {"key": m["key"], "title": m["title"], "route": futs[f]["route"],
                 "gate_ok": False, "why": f"error: {e}", "towers": [], "anchor_dev_m": None}
        out.append(r)
        n = len(r.get("towers") or [])
        dev = r.get("anchor_dev_m")
        log.info("[%s %d/%d] %-8s %s %3d towers, OSM dev %s | %s | %s", label, i, len(jobs),
                 r["route"], "gate ok" if r.get("gate_ok") else "gate no", n,
                 "-" if dev is None else f"{dev:.0f} m", r.get("why") or "", r["title"][5:55])
    return out


def _abs_dev_p75(towers: list[dict]) -> float:
    """75th percentile of |photo - OSM| over a photo's measured towers (inf without any)."""
    devs = [abs(t["photo_m"] - t["osm_m"]) for t in towers
            if t.get("photo_m") is not None and t.get("osm_m") is not None]
    return float(np.percentile(devs, 75)) if devs else float("inf")


def _keep(r: dict, args) -> bool:
    """The keep decision: the route's gate, and (when set) agreement with OSM heights.

    Rescue (``--rescue-anchor-dev``): a photo failing its fit/margin gate is kept when at least
    ``--rescue-min-towers`` towers sit within that many m (median) of their OSM heights. Why:
    the margin gate mostly measures line-of-sight ambiguity; on Miami run 2 (2026-10-04),
    "fit/margin OR (OSM dev <= 20 m and >= 12 towers)" measured 193 buildings (142 confirmed)
    against 142 (100) at the same accuracy (MAE 23.2 vs 21.7 m, pair order 0.86 vs 0.85).

    ``--rescue-max-p75`` (2026-10-23): the rescued photo's 75th-percentile |photo - OSM| must
    also stay under it. The median let Miami p20 through (dev 19.1 m, rejected in review): its
    p75 is 76 m, as is p79's, against 23-33 m on every usable or unreviewed rescued Miami photo.
    On Chicago it drops 11 of 53 rescued (truth MAE 26.9 m vs 18.1 m kept). Two bad photos:
    confirm on the next reviewed screen.
    """
    if not r.get("gate_ok"):
        dev = r.get("anchor_dev_m")
        towers = r.get("towers") or []
        if (args.rescue_anchor_dev is None or dev is None or dev > args.rescue_anchor_dev
                or len(towers) < args.rescue_min_towers):
            return False
        cap = getattr(args, "rescue_max_p75", None)
        return cap is None or _abs_dev_p75(towers) <= cap
    if args.max_anchor_dev is not None and r.get("route") != "labels":
        dev = r.get("anchor_dev_m")
        if dev is None or len(r.get("towers") or []) < args.min_towers or dev > args.max_anchor_dev:
            return False
    return True


def place(args, meta: dict, osm: dict) -> list[dict]:
    """Camera per photo by route, gates, and measured towers for every candidate."""
    d = cache_dir(args.region)
    by_title = {m["title"]: m for m in meta.values()}
    common = {"max_misfit": args.max_misfit, "min_margin": args.min_margin,
              "min_coverage": args.min_coverage, "max_dist_m": args.max_dist_m,
              "untagged_max_dist_m": getattr(args, "untagged_max_dist_m", ph.UNTAGGED_MAX_DIST_M)}
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
    # route by ``commons_photos.pipeline_status`` (user review 2026-10-07): located photos
    # without EXIF FOV and wide panoramas (cylindrical) now reach the fit too; unlocated ones
    # and wrong geotags go to the placement queue; low-light photos stay when their skyline
    # is clear
    site = load_site(args.region)
    bbox = (site["north"], site["south"], site["east"], site["west"])
    status = {k: cp.pipeline_status(m, bbox) for k, m in meta.items()}
    jobs2 = []
    for k, m in meta.items():
        if status[k][0] != cp.FIT or m["title"] in done_titles:
            continue
        fov, span, proj = cp.fit_prior(m)
        jobs2.append({"meta": m, "route": "recorded", "h_cams": args.h_cams, **common,
                      "fov_span": span, "fov_steps": 9 if span <= 0.1 else 13,
                      "fov_retry_span": cp.FOV_RETRY_SPAN if span <= 0.1 else None,
                      "projection": proj,
                      "prior": {"lat": m["lat"], "lon": m["lon"],
                                "heading_deg": m["heading_deg"], "hfov_deg": fov}})
    results = []
    with ProcessPoolExecutor(args.workers, initializer=_init,
                             initargs=(args.region, args.untagged,
                                       getattr(args, "untagged_candidates", "all"))) as ex:
        results += _run(ex, jobs1 + jobs2, "place")
        ok_cam = {r["key"]: r["camera"] for r in results if _keep(r, args)}
        links = {}
        if (d / "embed.npz").exists() and (d / "pairs.json").exists():
            E = np.load(d / "embed.npz")
            keys = [str(k) for k in E["keys"]]
            located = {k for k in keys if k in status and status[k][0] == cp.FIT}
            links = pg.embed_links(keys, E["vecs"], json.loads((d / "pairs.json").read_text()),
                                   located)
        jobs3, jobs4, manual = [], [], []
        for k, m in meta.items():
            if status[k][0] != cp.PLACEMENT or m["title"] in done_titles:
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
                # the EXIF + lead-gate placer (decisions/building-heights.md, 2026-10-07:
                # 4 of 5 right at lead >= 0.1)
                jobs4.append({"meta": m, "route": "search", **common})
            else:
                manual.append({"key": k, "title": m["title"],
                               "why": "no EXIF focal length" if not m.get("hfov_deg")
                               else "wide panorama without location"})
        log.info("[pipeline] labels %d, recorded %d (kept %d), linked %d, search %d, manual %d",
                 len(jobs1), len(jobs2), len(ok_cam), len(jobs3), len(jobs4), len(manual))
        results += _run(ex, jobs3 + jobs4, "place2")
    write_queue(args.report, meta, status, results, manual, args)
    return results


def write_queue(report: Path, meta: dict, status: dict, results: list[dict],
                manual: list[dict], args) -> dict:
    """``placement_queue.json``: the unlocated photos (and wrong geotags), placed when the
    linked refinement or the EXIF + lead-gate search passed, otherwise on the manual-label
    list (``sites/annotations/<region>_*.json`` places those to +-5 m); and every photo's
    screen status, so a filter change can be compared without placing again."""
    by_key = {r["key"]: r for r in results}
    placed, to_label = [], list(manual)
    # located photos whose outline failed (no sky found) can still be placed by hand
    to_label += [{"key": r["key"], "title": r["title"], "route": r["route"], "why": r["why"]}
                 for r in results if str(r.get("why", "")).startswith("flat outline")]
    for k, (st, _why) in status.items():
        if st != cp.PLACEMENT or k not in by_key:
            continue
        r = by_key[k]
        if _keep(r, args):
            placed.append({"key": k, "title": r["title"], "route": r["route"],
                           "camera": r.get("camera")})
        else:
            to_label.append({"key": k, "title": r["title"], "route": r["route"],
                             "why": r.get("why") or "gate"})
    out = {"placed": placed, "manual_label": sorted(to_label, key=lambda x: x["key"]),
           "status": {k: {"status": st, "why": why, "title": meta[k]["title"]}
                      for k, (st, why) in status.items()}}
    report.mkdir(parents=True, exist_ok=True)
    (report / "placement_queue.json").write_text(json.dumps(out, indent=0), encoding="utf-8")
    log.info("[queue] placed %d, manual labels %d", len(placed), len(to_label))
    return out
