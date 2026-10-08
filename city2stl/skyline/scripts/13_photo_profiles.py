#!/usr/bin/env python3
"""F-WEB2 step G1: cache the skyline outline of every Commons skyline photo of a region.

    python -m city2stl.skyline.scripts.13_photo_profiles --region miami

All landscape JPEGs in the region's skyline categories, located or not, wide or not (portraits
are recorded and skipped). Per photo: the Commons metadata (location, EXIF FOV, compass, date,
licence), the outline (``skyline_match.photo_profile``: top building row per column) at
1600 px wide, and its clarity (``commons_photos.skyline_quality``, sky brightness). Dark photos
are no longer skipped: the photo pipeline keeps a low-light photo whose skyline is clear
(``commons_photos.quality_reject``; user review 2026-10-07).

``--rescreen``: outline the photos an earlier run skipped as dark, and add clarity fields to
cached outlines (from ``img/``, no GPU). Writes ``runs/commons_cache/<region>/profiles.json`` +
``profiles.npz``; reruns skip photos already cached. SegFormer runs on the GPU when there is
one; the CUDA cache is released after each photo so other jobs on the card are not blocked.
"""

from __future__ import annotations

import argparse
import dataclasses
import io
import json
import logging
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from city2stl.skyline import commons_photos as cp
from city2stl.skyline import skyline_match as sm

ROOT = Path(__file__).resolve().parents[1]
WIDTH = 1600


def cache_dir(region: str) -> Path:
    return ROOT / "runs" / "commons_cache" / region


def load_profiles(region: str) -> tuple[list[dict], dict[str, np.ndarray]]:
    d = cache_dir(region)
    meta = json.loads((d / "profiles.json").read_text(encoding="utf-8")) if (d / "profiles.json").exists() else []
    arrs = dict(np.load(d / "profiles.npz")) if (d / "profiles.npz").exists() else {}
    return meta, arrs


def _fetch(S: requests.Session, url: str):
    for a in range(5):
        r = S.get(url, timeout=180)
        if r.status_code != 429:
            break
        time.sleep(4 * (a + 1))
    return r


def _outline(row: dict, img: np.ndarray, arrs: dict, masks) -> None:
    """Outline + clarity of ``img`` into ``row`` / ``arrs`` (or ``row["skip"]``)."""
    sky, bld = masks(img)
    try:
        from city2stl.resources import free_gpu_cache

        free_gpu_cache()                # other jobs share the card
    except Exception:  # noqa: BLE001
        pass
    if sky is None:
        row["skip"] = "segmentation failed"
        return
    prof = sm.photo_profile(sky, bld)
    row.pop("skip", None)
    row["px"] = [prof.width, prof.height]
    row["coverage"] = float(np.isfinite(prof.y_top).mean())
    row["quality"] = cp.skyline_quality(img, prof.y_top, prof.height)
    row["sky_value"] = cp.sky_value(img)
    arrs[row["key"]] = prof.y_top.astype(np.float32)


def rescreen(region: str, masks) -> None:
    """Outline the rows skipped as dark; clarity fields for cached outlines."""
    from PIL import Image as _I

    meta, arrs = load_profiles(region)
    d = cache_dir(region)
    S = requests.Session()
    S.headers["User-Agent"] = cp.UA
    n_dark = n_q = 0
    for i, row in enumerate(meta):
        if row.get("skip") == "dark":
            r = _fetch(S, row["url"])
            if r.status_code != 200:
                row["skip"] = f"http {r.status_code}"
                continue
            img = np.asarray(_I.open(io.BytesIO(r.content)).convert("RGB"))
            _outline(row, img, arrs, masks)
            if "px" in row:
                (d / "img").mkdir(exist_ok=True)
                _I.fromarray(img).save(d / "img" / f"{row['key']}.jpg", quality=88)
            n_dark += 1
            time.sleep(0.4)
        elif "px" in row and "quality" not in row and (d / "img" / f"{row['key']}.jpg").exists():
            img = np.asarray(_I.open(d / "img" / f"{row['key']}.jpg").convert("RGB"))
            row["quality"] = cp.skyline_quality(img, arrs[row["key"]], row["px"][1])
            row["sky_value"] = cp.sky_value(img)
            n_q += 1
        if (i + 1) % 25 == 0:
            (d / "profiles.json").write_text(json.dumps(meta, indent=0), encoding="utf-8")
            np.savez_compressed(d / "profiles.npz", **arrs)
            logging.info("[rescreen] %d/%d", i + 1, len(meta))
    (d / "profiles.json").write_text(json.dumps(meta, indent=0), encoding="utf-8")
    np.savez_compressed(d / "profiles.npz", **arrs)
    print(f"{region}: outlined {n_dark} formerly dark photos, clarity for {n_q} cached outlines")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    ap.add_argument("--rescreen", action="store_true",
                    help="outline photos skipped as dark; add clarity to cached outlines")
    args = ap.parse_args()
    from city2stl.resources import wait_for_ram

    wait_for_ram()                              # CLAUDE.md "Shared machine resources"
    from city2stl.skyline._core.segmentation import _neural_sky_and_building_masks

    if args.rescreen:
        rescreen(args.region, _neural_sky_and_building_masks)
        return 0
    site = json.loads((ROOT / "sites" / f"{args.region}.json").read_text(encoding="utf-8-sig"))
    bbox = (site["north"], site["south"], site["east"], site["west"])
    S = requests.Session()
    S.headers["User-Agent"] = cp.UA
    cp.FETCH_WIDTH = WIDTH
    # The site's ``commons_categories`` add neighbourhoods whose categories don't say "skyline"
    # (Cartagena: Bocagrande, its beaches, Hotel Estelar).
    cats = list(dict.fromkeys(cp.skyline_categories(S, args.region.replace("_", " "))
                              + list(site.get("commons_categories", []))))
    print(f"[profiles] {args.region}: categories {cats}")
    photos = cp.describe_files(S, cp.category_files(S, cats))
    meta, arrs = load_profiles(args.region)
    done = {m["title"] for m in meta}
    d = cache_dir(args.region)
    d.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(photos):
        if p.title in done:
            continue
        row = {**{k: v for k, v in dataclasses.asdict(p).items() if k != "notes"},
               "why_not_usable": cp.screen(p, bbox)[1], "key": f"p{len(meta)}"}
        if p.height > p.width:
            row["skip"] = "portrait"
        else:
            r = _fetch(S, p.url)
            if r.status_code != 200:
                row["skip"] = f"http {r.status_code}"
            else:
                img = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
                _outline(row, img, arrs, _neural_sky_and_building_masks)
            time.sleep(0.4)
        meta.append(row)
        if len(meta) % 10 == 0:
            (d / "profiles.json").write_text(json.dumps(meta, indent=0), encoding="utf-8")
            np.savez_compressed(d / "profiles.npz", **arrs)
            logging.info("[profiles] %d/%d", i + 1, len(photos))
    (d / "profiles.json").write_text(json.dumps(meta, indent=0), encoding="utf-8")
    np.savez_compressed(d / "profiles.npz", **arrs)
    n_ok = sum("px" in m for m in meta)
    print(f"{args.region}: {len(meta)} photos, {n_ok} outlines, located {sum(m['lat'] is not None and 'px' in m for m in meta)}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
