#!/usr/bin/env python3
"""F-WEB2: a region's building heights from its Commons skyline photos, end to end.

    python -m city2stl.skyline.scripts.17_photo_pipeline --region miami --report <report dir> [--workers 8]

Needs the outline cache (``13_photo_profiles``), and for links the embeddings and pairs
(``16_embed_photos``, ``14_group_photos``). No downloads, no GPU: everything works on cached
outlines. Each photo gets a camera by the best route it qualifies for, and is kept only when
the route's gate passes:

1. labels (``sites/annotations/<region>_*.json``): ``photo_localize.solve_from_labels``;
   the unlabelled copy in ``same_pose_as`` is measured;
2. recorded location (``commons_photos.pipeline_status`` FIT): ``skyline_match.refine`` within
   500 m from ``commons_photos.fit_prior`` (EXIF FOV +-8 %, retried at +-40 % when the gate
   fails; a free FOV without EXIF; a cylindrical projection for panoramas wider than 3:1);
   gate misfit <= 0.3 and skyline coverage >= 25 %;
3. unlocated, linked to a located photo of the same spot (``photo_groups.embed_links``):
   refine from that photo's kept camera; same gate;
4. unlocated with EXIF FOV, not linked: full ``skyline_match.locate``; gate: the best pose
   beats the runner-up by >= 0.1 misfit (EXIF-FOV validation, 2026-10-04: right 0.17-0.19,
   wrong <= 0.035).
   Unlocated photos (and wrong geotags) not placed by 3 or 4 go to the manual-label list in
   ``placement_queue.json`` (labels place a photo to +-5 m). Screen and routes: user review of
   38 rejected photos, 2026-10-07 (``docs/decisions/building-heights.md``).

Towers in each kept photo: ``photo_heights.measure_towers`` + ``loo_heights``, towers beyond
``--max-dist-m`` dropped (a tower 7.7 km away read +208 m). Per building: median over photos.
Every candidate photo is measured, kept or not, and saved to ``photo_results.json`` with its
towers' truth, so the gates (incl. ``--max-anchor-dev``: towers must agree with their own OSM
heights) can be tuned with ``--rescore`` without placing photos again. Refinement tries camera
heights ``--h-cams`` (street, deck, rooftop). Scored against the benchmark truth and against
Street View on the same buildings (the report's ``heights.json``). Writes ``photos.json``,
``photo_heights.json`` and the report's ``benchmark.html``.

``--untagged`` (F-WEB2 C2): also heights for untagged buildings. Each candidate photo fits its
tilt and camera height on its tagged towers and implies a height for every untagged footprint
in view within ``--untagged-max-dist-m`` (``photo_heights.implied_heights``), dropping heights
above min(the site's ``max_plausible_height_m``, 1.2x the tallest tagged tower in view) and
columns a nearer tagged tower explains; where a tagged tower behind the footprint explains
its columns the height is only an upper bound (``untagged_capped``, not counted). A building is
kept when kept photos from 2+ viewpoints (cameras within 100 m are one) with 15 deg of view
directions at the footprint agree within max(3 m, min(10 %, 10 m)), with footprints behind an
agreed building dropped from the photos where it covers them
(``photo_heights.agreed_with_occlusion``); ``summary.untagged.rules`` counts what each rule
removed; ``--untagged-far-first`` (experiment) also lets a farther agreed building cap the
nearer footprints over its columns. Written to
``photo_heights.json`` (``untagged``) and scored against the benchmark truth cache
(``--untagged-truth fetch`` measures missing footprints: paid 3D Tiles reads).
``--untagged-candidates truth`` limits candidates to footprints the truth cache holds, so a
run can be scored without fetching (the default ``all`` keeps competing owners in play).
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


def _init(region: str, untagged: bool = False, candidates: str = "all"):
    """Worker state: towers and outlines, loaded once per process."""
    from city2stl.skyline.region_data import (
        _load_osm_for_region,
        _load_region_bbox,
        _load_site_max_plausible_height_m,
    )

    osm, _ = _load_osm_for_region(_load_region_bbox(region))
    _W["towers"] = sm.tower_table(osm["buildings"]["features"])
    if untagged:
        _W["untagged"] = _untagged_table(region, osm, _W["towers"], candidates)
        _W["max_height_m"] = _load_site_max_plausible_height_m(region)
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
    # readings the photo can't be trusted on (``ph.reading_flag``: hidden behind something in
    # front, far from the OSM height, low on the horizon) measured another building; they would
    # bias the others' tilt and camera-height fit too, so refit without them and flag them
    flags = {m.index: f for m in ms if m.index in est
             and (f := ph.reading_flag(est[m.index], m.osm_height_m, m.dist_m, h_cam))}
    if flags:
        refit = ph.loo_heights([m for m in ms if m.index not in flags])
        est = {**refit, **{i: est[i] for i in flags}}
        flags.update({m.index: f for m in ms if m.index in refit
                      and (f := ph.reading_flag(refit[m.index], m.osm_height_m, m.dist_m, h_cam))})
    rows = [{"tower": m.index, "name": m.name, "dist_m": round(m.dist_m),
             "photo_m": round(est[m.index], 1), "osm_m": m.osm_height_m, "fp": _tower_key(towers, m.index),
             **({"flag": flags[m.index]} if m.index in flags else {})}
            for m in ms if m.index in est]
    # every reading counts here: a misplaced photo shows up as many towers far from their tags
    dev = float(np.median([abs(r["photo_m"] - r["osm_m"]) for r in rows])) if rows else None
    return rows, dev


#: Two photos confirm each other on a tower when their readings lie within max(CROSS_ABS_M,
#: CROSS_REL x the other photos' median).
CROSS_ABS_M = 5.0
CROSS_REL = 0.15


def _cross_check(results: list[dict]) -> None:
    """Photos that claim the same tower must agree on it (user, 2026-10-05).

    Per used reading of a kept photo: ``others`` = how many other kept photos read that tower,
    ``confirmed_by`` = how many of them agree with it. A reading that two or more other photos
    read and none agrees with is flagged "other photos disagree" and not used. On 2,472
    readings with confirmed truth (Miami, Chicago): confirmed readings were more than 25 % off
    in 11 % of cases, contradicted ones in 62 %, readings no other photo had in 32 %.
    """
    by: dict[int, list[tuple[int, dict]]] = {}
    for k, r in enumerate(results):
        if r.get("kept"):
            for t in r.get("towers") or []:
                if not t.get("flag"):
                    by.setdefault(t["tower"], []).append((k, t))
    for lst in by.values():
        for k, t in lst:
            others = [o["photo_m"] for k2, o in lst if k2 != k]
            t["others"] = len(others)
            if others:
                tol = max(CROSS_ABS_M, CROSS_REL * float(np.median(others)))
                t["confirmed_by"] = int(sum(abs(h - t["photo_m"]) <= tol for h in others))
    for lst in by.values():
        for _k, t in lst:
            if t["others"] >= 2 and not t.get("confirmed_by"):
                t["flag"] = "other photos disagree"


def _photo_groups(results: list[dict], min_shared: int = 4) -> list[dict]:
    """Kept photos grouped by the towers they show: linked when two share ``min_shared`` used
    tower readings, groups = connected sets. Per group: its photos, the towers read by 2+ of
    them, and the share of those readings another photo confirms."""
    kept = [r for r in results if r.get("kept")]
    sets = [{t["tower"] for t in r.get("towers") or [] if not t.get("flag")} for r in kept]
    parent = list(range(len(kept)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(kept)):
        for j in range(i + 1, len(kept)):
            if len(sets[i] & sets[j]) >= min_shared:
                parent[find(i)] = find(j)
    members: dict[int, list[int]] = {}
    for i in range(len(kept)):
        members.setdefault(find(i), []).append(i)
    out = []
    for ms in members.values():
        shared = {ti for ti in set().union(*(sets[i] for i in ms)) if sum(ti in sets[i] for i in ms) >= 2}
        reads = [t for i in ms for t in kept[i]["towers"]
                 if t["tower"] in shared and t.get("others") and not t.get("flag")]
        flagged = [t for i in ms for t in kept[i]["towers"]
                   if t["tower"] in shared and t.get("flag") == "other photos disagree"]
        out.append({"photos": [kept[i]["key"] for i in ms], "titles": [kept[i]["title"] for i in ms],
                    "towers": len(shared), "readings": len(reads) + len(flagged),
                    "confirmed": sum(1 for t in reads if t.get("confirmed_by")),
                    "contradicted": len(flagged)})
    return sorted(out, key=lambda g: (-len(g["photos"]), -g["towers"]))


def _shared_buildings(results: list[dict], rows: list[dict]) -> list[dict]:
    """Every tower two or more kept photos read: each photo's reading (and card label), the
    consensus and truth, most-seen first."""
    by: dict[int, list[dict]] = {}
    for r in results:
        if r.get("kept"):
            for t in r.get("towers") or []:
                by.setdefault(t["tower"], []).append(
                    {"photo": r["key"], "title": r["title"], "label": t.get("label"),
                     "photo_m": t["photo_m"], "flag": t.get("flag"),
                     "confirmed_by": t.get("confirmed_by", 0)})
    row = {r["tower"]: r for r in rows}
    out = []
    for ti, reads in by.items():
        if len(reads) < 2:
            continue
        b = row.get(ti, {})
        out.append({"tower": ti, "name": b.get("name") or "", "reads": reads,
                    "photo_m": b.get("photo_m"), "osm_m": b.get("osm_m"), "truth_m": b.get("truth_m"),
                    "confirmed": sum(1 for x in reads if x["confirmed_by"] and not x["flag"])})
    return sorted(out, key=lambda b: (-len(b["reads"]), b["name"]))


def _tower_key(towers, ti: int) -> str:
    """Stable id of a tower (its footprint key): the table index changes when OSM is re-fetched."""
    return bm.footprint_key([list(towers.to_ll(x, y))[::-1] for x, y in towers.verts[ti]])


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
    return bm.footprint_truth(args.region, footprints, bm.REGIONS.get(args.region))


def _flag(t: dict, h_cam: float = 2.0) -> str | None:
    """Why a stored tower reading isn't trusted (``ph.reading_flag``), or None; works on photo
    results written before the flags existed."""
    return t.get("flag") or ph.reading_flag(t["photo_m"], t["osm_m"], t["dist_m"], h_cam)


def _job(job: dict) -> dict:
    """One photo: pose by its route, gate, measure. Runs in a worker."""
    m, route = job["meta"], job["route"]
    prof = _profile(m)
    towers = _W["towers"]
    cover = float(np.isfinite(prof.y_top).mean())
    res = {"key": m["key"], "title": m["title"], "route": route, "coverage": cover}
    projection = job.get("projection", "pinhole")
    if route != "labels" and cover > 0 and float(np.nanstd(prof.y_top)) < 1.0:
        # a flat outline is a segmentation failure, not a skyline (Miami p234: a black-and-
        # white photo, no sky found, every column's top at row 0; the fit returned misfit 1.0)
        return {**res, "gate_ok": False, "why": "flat outline (no sky found)", "towers": [],
                "anchor_dev_m": None}
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
        spans = [(job.get("fov_span", 0.08), job.get("fov_steps", 9))]
        if job.get("fov_retry_span"):
            spans.append((job["fov_retry_span"], 13))
        for span, steps in spans:
            # retry with the FOV freer only when the EXIF FOV fails the gate: Commons crops
            # keep the uncropped frame's EXIF (Miami p276 fits 50 deg, EXIF 74; p78 25 vs 17,
            # passing at 0.28). +-40 % passed none of the 4 photos the user agreed were bad
            # (2026-10-07); a roll search did (p121 0.93 -> 0.24 at 3 deg), so no roll.
            if hit is not None and hit.misfit <= job["max_misfit"]:
                break
            for h in job.get("h_cams", (2.0,)):
                cand = sm.refine(prof, towers, prior, radius_m=500.0, step_m=50.0,
                                 fov_span=span, h_cam=h, fov_steps=steps, projection=projection)
                if hit is None or cand.misfit < hit.misfit:
                    h_best, hit = h, cand
        moved = math.hypot((hit.lat - pr["lat"]) * 111320,
                           (hit.lon - pr["lon"]) * 111320 * math.cos(math.radians(pr["lat"])))
        kept = hit.misfit <= job["max_misfit"] and cover >= job["min_coverage"]
        pose = ph.PhotoPose(hit.lat, hit.lon, hit.heading_deg, hit.hfov_deg,
                            projection=projection)
        res.update(camera={"lat": hit.lat, "lon": hit.lon, "heading_deg": hit.heading_deg,
                           "hfov_deg": hit.hfov_deg, "misfit": hit.misfit, "moved_m": moved,
                           "h_cam": h_best, "projection": projection,
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
    h_cam = (res.get("camera") or {}).get("h_cam", 2.0)
    res["towers"], res["anchor_dev_m"] = _measure(prof, pose, job["max_dist_m"], h_cam)
    if "untagged" in _W:
        res["untagged"], res["untagged_spans"], res["untagged_capped"] = _implied_untagged(
            prof, pose, job["max_dist_m"], h_cam,
            job.get("untagged_max_dist_m", ph.UNTAGGED_MAX_DIST_M))
    return res


def _untagged_table(region: str, osm: dict, frame, candidates: str = "all"):
    """The untagged footprints C2 measures; ``candidates="truth"``: only those the region's
    benchmark truth cache holds (Miami: 942), so the run is scored without fetching."""
    only = set(bm.load_truth_cache(region)) if candidates == "truth" else None
    return ph.untagged_table(osm["buildings"]["features"], frame=frame, only_keys=only)


def _implied_untagged(prof, pose: ph.PhotoPose, max_dist_m: float, h_cam: float,
                      untagged_max_dist_m: float = ph.UNTAGGED_MAX_DIST_M
                      ) -> tuple[dict, dict, dict]:
    """``({index: implied height}, {index: [first col, last col, distance]},
    {index: upper bound})`` for one photo, with tilt and camera height fitted on all its
    tagged towers (no tower's estimate is involved, so no leave-one-out). Heights above
    min(site maximum, 1.2x the tallest tagged tower in view) are dropped; the tagged towers
    occlude footprints behind them, and a footprint in front of a tagged tower that explains
    its columns gets only an upper bound (not counted toward agreement)."""
    ms = [m for m in ph.measure_towers(prof, _W["towers"], pose, h_cam=h_cam)
          if m.dist_m <= max_dist_m]
    if len(ms) < 2:
        return {}, {}, {}
    tilt, h = ph.fit_tilt_height(ms, np.array([m.osm_height_m for m in ms]))
    cap = min(_W.get("max_height_m", math.inf), 1.2 * max(m.osm_height_m for m in ms))
    spans: dict = {}
    caps: dict = {}
    imp = ph.implied_heights(prof, pose, _W["untagged"], tilt, h,
                             max_dist_m=min(max_dist_m, untagged_max_dist_m),
                             max_height_m=cap, occluders=ms, spans=spans, caps=caps)
    return ({str(i): round(v, 2) for i, v in imp.items()},
            {str(i): [c0, c1, round(d, 1)] for i, (c0, c1, d) in spans.items()},
            {str(i): round(v, 2) for i, v in caps.items()})


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
    site = json.loads((ROOT / "sites" / f"{args.region}.json").read_text(encoding="utf-8-sig"))
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
    per: dict[int, list[float]] = {}
    confirmed: dict[int, int] = {}
    for r in results:
        if r["kept"]:
            for t in r.get("towers") or []:
                if not t.get("flag"):
                    per.setdefault(t["tower"], []).append(t["photo_m"])
                    confirmed[t["tower"]] = confirmed.get(t["tower"], 0) + bool(t.get("confirmed_by"))
    rows = [{"tower": int(ti), "name": towers.names[ti], "key": keys[ti],
             "photo_m": round(float(np.median(hs)), 1), "n_photos": len(hs),
             "n_confirmed": confirmed.get(ti, 0),
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
                  "rescue_min_towers": args.rescue_min_towers},
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
                      "thumb": _thumb(m["url"]),
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

    cache = ROOT / "runs" / "commons_cache" / args.region
    img_path = cache / "img" / f"{r['key']}.jpg"
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
                                   bm.REGIONS.get(args.region))
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
    ap.add_argument("--untagged", action="store_true",
                    help="also measure untagged buildings that 2+ kept photos agree on (C2)")
    ap.add_argument("--untagged-max-dist-m", type=float, default=ph.UNTAGGED_MAX_DIST_M,
                    help="C2 candidates farther than this from the camera are skipped")
    ap.add_argument("--untagged-truth", choices=("cached", "fetch"), default="cached",
                    help="score C2 on the benchmark truth cache, or fetch missing footprints "
                         "(paid 3D Tiles reads)")
    ap.add_argument("--untagged-far-first", action="store_true",
                    help="C2 experiment: a farther agreed building caps the nearer footprints "
                         "over its columns (counted in summary.untagged.rules)")
    ap.add_argument("--untagged-candidates", choices=("all", "truth"), default="all",
                    help="truth: only footprints the truth cache holds (scoring without fetches)")
    ap.add_argument("--no-truth-fetch", action="store_true",
                    help="score on the cached truth only (no survey or paid 3D Tiles reads)")
    ap.add_argument("--reuse", type=Path, default=None,
                    help="an earlier photo_results.json: photos whose route is unchanged (and "
                         "not a failed recorded fit) are copied, not placed again")
    ap.add_argument("--rescore", action="store_true",
                    help="no placing: apply the gates to the saved photo_results.json")
    args = ap.parse_args()
    if not args.rescore:                        # placing photos is a heavy job (CLAUDE.md)
        from city2stl.resources import wait_for_ram

        wait_for_ram()
    d = ROOT / "runs" / "commons_cache" / args.region
    meta = {m["key"]: m for m in json.loads((d / "profiles.json").read_text(encoding="utf-8"))
            if "px" in m}
    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

    osm, _ = _load_osm_for_region(_load_region_bbox(args.region))
    saved = args.report / "photo_results.json"
    if args.rescore:
        results = json.loads(saved.read_text(encoding="utf-8"))
    else:
        if args.reuse:
            _REUSE.update({r["key"]: r for r in json.loads(args.reuse.read_text(encoding="utf-8"))})
        results = place(args, meta, osm)
        saved.write_text(json.dumps(results, indent=0), encoding="utf-8")
    summary = finish(args, results, meta, osm)
    saved.write_text(json.dumps(results, indent=0), encoding="utf-8")
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
