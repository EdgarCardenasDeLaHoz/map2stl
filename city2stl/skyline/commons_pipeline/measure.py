"""One photo's pose and towers (the worker side of script 17).

``_init`` loads the towers and outlines once per worker process into ``_W``; ``_job`` places a
photo by its route (labels, recorded, linked, search), applies the route's gate and measures the
towers at the pose (``_measure``), plus the implied untagged heights with ``--untagged``.
"""

from __future__ import annotations

import math

import numpy as np

from city2stl.skyline import benchmark as bm
from city2stl.skyline import photo_heights as ph
from city2stl.skyline import skyline_match as sm
from geo2stl.geo import M_PER_DEG_SPHERE

from .cache import cache_dir

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
    d = cache_dir(region)
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
    est_peak = ph.loo_heights(ms, top="peak")
    if flags:
        good = [m for m in ms if m.index not in flags]
        refit = ph.loo_heights(good)
        est = {**refit, **{i: est[i] for i in flags}}
        est_peak = {**ph.loo_heights(good, top="peak"), **{i: est_peak[i] for i in flags if i in est_peak}}
        flags.update({m.index: f for m in ms if m.index in refit
                      and (f := ph.reading_flag(refit[m.index], m.osm_height_m, m.dist_m, h_cam))})
    rows = [{"tower": m.index, "name": m.name, "dist_m": round(m.dist_m),
             "photo_m": round(est[m.index], 1), "osm_m": m.osm_height_m, "fp": _tower_key(towers, m.index),
             **({"peak_m": round(est_peak[m.index], 1)} if m.index in est_peak else {}),
             **({"flag": flags[m.index]} if m.index in flags else {})}
            for m in ms if m.index in est]
    # every reading counts here: a misplaced photo shows up as many towers far from their tags
    dev = float(np.median([abs(r["photo_m"] - r["osm_m"]) for r in rows])) if rows else None
    return rows, dev

def _tower_key(towers, ti: int) -> str:
    """Stable id of a tower (its footprint key): the table index changes when OSM is re-fetched."""
    return bm.footprint_key([list(towers.to_ll(x, y))[::-1] for x, y in towers.verts[ti]])


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
        moved = math.hypot((hit.lat - pr["lat"]) * M_PER_DEG_SPHERE,
                           (hit.lon - pr["lon"]) * M_PER_DEG_SPHERE * math.cos(math.radians(pr["lat"])))
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
        # T18: only a rival that identifies a different set of towers counts.
        margin, _rival = sm.gate_margin(prof, towers, hits)
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
