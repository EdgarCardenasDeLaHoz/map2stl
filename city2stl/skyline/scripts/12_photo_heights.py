#!/usr/bin/env python3
"""F-WEB2: tower heights from a region's Commons skyline photos -> ``photos.json`` for a report.

    python -m city2stl.skyline.scripts.12_photo_heights --region miami --report <report dir>

Per photo: a camera pose, then identify-then-measure (``photo_heights``), scored against the
benchmark truth. Poses come from
- labelled photos (``sites/annotations/<region>_*.json``; ``same_pose_as`` lists unlabelled
  copies to measure instead, since the drawn labels sit in the sky and disturb the sky mask);
- located Commons photos: recorded location + EXIF FOV + compass as the prior, refined against
  the skyline in a +-500 m window (``skyline_match.refine``), kept when the misfit is below
  ``--max-misfit`` and the skyline covers enough columns.
Writes ``<report>/photos.json`` and (re)writes ``benchmark.html`` when ``heights.json`` is there.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import logging
import math
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from city2stl.skyline import benchmark as bm
from city2stl.skyline import commons_photos as cp
from city2stl.skyline import photo_heights as ph
from city2stl.skyline import skyline_match as sm
from city2stl.skyline.benchmark_report import write_benchmark_page
from city2stl.skyline.photo_localize import building_index, load_annotations, solve_from_labels
from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1600
log = logging.getLogger("photo_heights")


def _session():
    s = requests.Session()
    s.headers["User-Agent"] = cp.UA
    return s


def _info(S, title, width=WIDTH):
    r = S.get(cp.API, params=dict(action="query", titles=title, prop="imageinfo",
                                  iiprop="url|size|metadata|extmetadata", iiurlwidth=width,
                                  iiextmetadatafilter="Artist|LicenseShortName",
                                  format="json", formatversion=2), timeout=60).json()
    return r["query"]["pages"][0]["imageinfo"][0]


def _image(S, url):
    for a in range(5):
        r = S.get(url, timeout=180)
        if r.status_code != 429:
            r.raise_for_status()
            return np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
        time.sleep(4 * (a + 1))
    raise RuntimeError(f"rate-limited: {url}")


def _profile(img):
    from city2stl.skyline._core.segmentation import _neural_sky_and_building_masks

    sky, bld = _neural_sky_and_building_masks(img)
    _release_gpu_cache()
    return None if sky is None else sm.photo_profile(sky, bld)


def _release_gpu_cache():
    """Hand freed CUDA blocks back after a SegFormer pass: the CPU search that follows takes
    minutes, and the caching allocator otherwise keeps ~3.9 GB of the 4 GB card reserved,
    blocking other GPU jobs on the machine (2026-10-04)."""
    try:
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001 - CPU-only torch or no torch: nothing to release
        pass


def _measure(prof, towers, pose, region, provider):
    ms = ph.measure_towers(prof, towers, pose)
    est = ph.loo_heights(ms)
    fps = {}
    for m in ms:
        ring = [list(towers.to_ll(x, y))[::-1] for x, y in towers.verts[m.index]]
        fps[bm.footprint_key(ring)] = (m, ring)
    truth = bm.footprint_truth(region, {k: r for k, (_m, r) in fps.items()}, provider)
    rows, P, T = [], [], []
    for k, (m, _r) in fps.items():
        t = truth.get(k, {})
        tm = t.get("truth_m") if t.get("status") == "confirmed" else None
        e = est.get(m.index)
        rows.append({"name": m.name, "dist_m": m.dist_m, "photo_m": e, "osm_m": m.osm_height_m,
                     "truth_m": tm, "truth_status": t.get("status")})
        if e is not None and tm is not None:
            P.append(e)
            T.append(tm)
    score = {}
    if len(P) >= 2:
        P, T = np.array(P), np.array(T)
        rel = bm.relative_metrics(P, T)
        score = {"n": int(len(P)), "pair_order": rel["pair_order"], "spearman": rel["spearman"],
                 "mae_m": round(float(np.mean(np.abs(P - T))), 1),
                 "bias_m": round(float(np.mean(P - T)), 1)}
    rows.sort(key=lambda r: -(r["truth_m"] or r["osm_m"] or 0))
    return rows, score


def _photo_meta(info, title):
    ext = info.get("extmetadata") or {}
    return {"title": title[5:], "page": info.get("descriptionurl", ""),
            "thumb": info.get("thumburl", ""),
            "attribution": f"{cp._strip_html((ext.get('Artist') or {}).get('value', ''))}, "
                           f"{(ext.get('LicenseShortName') or {}).get('value', '')}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--report", required=True, type=Path, help="report dir (holds heights.json)")
    ap.add_argument("--max-photos", type=int, default=8)
    ap.add_argument("--max-misfit", type=float, default=0.3)
    ap.add_argument("--min-coverage", type=float, default=0.25,
                    help="share of photo columns with a building skyline")
    args = ap.parse_args()

    S = _session()
    site = json.loads((ROOT / "sites" / f"{args.region}.json").read_text(encoding="utf-8-sig"))
    bbox = (site["north"], site["south"], site["east"], site["west"])
    osm, _ = _load_osm_for_region(_load_region_bbox(args.region))
    towers = sm.tower_table(osm["buildings"]["features"])
    index = building_index(osm["buildings"]["features"])
    provider = bm.REGIONS.get(args.region)
    photos = []

    # 1. labelled photos
    for path in sorted(glob.glob(str(ROOT / "sites" / "annotations" / f"{args.region}_*.json"))):
        ann = load_annotations(path)
        info = _info(S, ann["file"])
        md = {m["name"]: m["value"] for m in info.get("metadata") or [] if isinstance(m, dict)}
        f35 = cp._num(md.get("FocalLengthIn35mmFilm"))
        w, h = ann["image_px"]
        kw = {}
        if f35:
            kw["f_prior_px"] = (w / 2) / math.tan(math.radians(cp.hfov_from_35mm(f35, w, h) / 2))
        pose, _chosen = solve_from_labels(ann, index, **kw)
        for title in ann.get("same_pose_as", []) or [ann["file"]]:
            inf = _info(S, title)
            prof = _profile(_image(S, inf["thumburl"]))
            pp = ph.PhotoPose(pose.lat, pose.lon, pose.heading_deg, pose.hfov_deg, pose.projection)
            rows, score = _measure(prof, towers, pp, args.region, provider)
            photos.append({**_photo_meta(inf, title),
                           "camera": {"source": "solved from labels", "lat": pose.lat,
                                      "lon": pose.lon, "heading_deg": pose.heading_deg,
                                      "hfov_deg": pose.hfov_deg, "sigma_m": pose.sigma_m},
                           "status": f"measured ({len(rows)} towers identified)",
                           "towers": rows, "score": score})
            log.info("[labels] %s: %s", title, score)

    # 2. located Commons photos, prior refined against the skyline
    for p in cp.find_skyline_photos(args.region.replace("_", " "), bbox, max_photos=args.max_photos * 2):
        if len([x for x in photos if x["camera"]["source"] != "solved from labels"]) >= args.max_photos:
            break
        if p.hfov_deg is None:
            continue
        inf = _info(S, p.title)
        img = _image(S, inf["thumburl"])
        meta = _photo_meta(inf, p.title)
        cam = {"source": "recorded (EXIF)", "lat": p.lat, "lon": p.lon,
               "heading_deg": p.heading_deg, "hfov_deg": p.hfov_deg}
        if cp.is_dark(img):
            photos.append({**meta, "camera": cam, "status": "skipped: night"})
            continue
        prof = _profile(img)
        cover = 0.0 if prof is None else float(np.isfinite(prof.y_top).mean())
        if prof is None or cover < args.min_coverage:
            photos.append({**meta, "camera": cam,
                           "status": f"skipped: skyline covers {cover:.0%} of columns"})
            continue
        prior = sm.Hit(p.lat, p.lon, p.heading_deg if p.heading_deg is not None else 0.0,
                       p.hfov_deg, 0.0, 0.0, 1.0, 0)
        # refine searches every heading, so a missing compass only widens nothing here
        hit = sm.refine(prof, towers, prior, radius_m=500.0, step_m=50.0, fov_span=0.08)
        moved = math.hypot((hit.lat - p.lat) * 111320,
                           (hit.lon - p.lon) * 111320 * math.cos(math.radians(p.lat)))
        cam = {"source": f"recorded, refined {moved:.0f} m (misfit {hit.misfit:.2f})",
               "lat": hit.lat, "lon": hit.lon, "heading_deg": hit.heading_deg,
               "hfov_deg": hit.hfov_deg}
        if hit.misfit > args.max_misfit:
            photos.append({**meta, "camera": cam,
                           "status": f"skipped: skyline fit too poor (misfit {hit.misfit:.2f})"})
            continue
        pp = ph.PhotoPose(hit.lat, hit.lon, hit.heading_deg, hit.hfov_deg)
        rows, score = _measure(prof, towers, pp, args.region, provider)
        photos.append({**meta, "camera": cam, "status": f"measured ({len(rows)} towers identified)",
                       "towers": rows, "score": score})
        log.info("[commons] %s: %s", p.title, score)

    args.report.mkdir(parents=True, exist_ok=True)
    (args.report / "photos.json").write_text(json.dumps(photos, indent=1), encoding="utf-8")
    heights = args.report / "heights.json"
    if heights.exists():
        name, buildings = bm.load_report(heights)
        truth = bm.footprint_truth(args.region, {b["key"]: b["footprint_lonlat"] for b in buildings},
                                   provider)
        score = {"region": args.region, "report": str(heights), **bm.score_buildings(buildings, truth)}
        out = write_benchmark_page(args.report, score, buildings, truth, photos)
        print("wrote", out)
    for x in photos:
        print(f"{x['status'][:40]:40s} {x.get('score')}  {x['title'][:60]}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
