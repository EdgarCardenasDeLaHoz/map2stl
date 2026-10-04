#!/usr/bin/env python3
"""F-WEB2 step G1: cache the skyline outline of every Commons skyline photo of a region.

    python -m city2stl.skyline.scripts.13_photo_profiles --region miami

All landscape JPEGs in the region's skyline categories, located or not, wide or not (portrait
and dark photos are recorded and skipped). Per photo: the Commons metadata (location, EXIF
FOV, compass, date, licence) and the outline (``skyline_match.photo_profile``: top building
row per column) at 1600 px wide. Writes ``runs/commons_cache/<region>/profiles.json`` +
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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    args = ap.parse_args()
    from city2stl.skyline._core.segmentation import _neural_sky_and_building_masks

    site = json.loads((ROOT / "sites" / f"{args.region}.json").read_text(encoding="utf-8-sig"))
    bbox = (site["north"], site["south"], site["east"], site["west"])
    S = requests.Session()
    S.headers["User-Agent"] = cp.UA
    cp.FETCH_WIDTH = WIDTH
    photos = cp.describe_files(S, cp.category_files(S, cp.skyline_categories(S, args.region.replace("_", " "))))
    meta, arrs = load_profiles(args.region)
    done = {m["title"] for m in meta}
    d = cache_dir(args.region)
    d.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(photos):
        if p.title in done:
            continue
        row = {**{k: v for k, v in dataclasses.asdict(p).items() if k != "notes"},
               "why_not_usable": cp.usable(p, bbox), "key": f"p{len(meta)}"}
        if p.height > p.width:
            row["skip"] = "portrait"
        else:
            for a in range(5):
                r = S.get(p.url, timeout=180)
                if r.status_code != 429:
                    break
                time.sleep(4 * (a + 1))
            if r.status_code != 200:
                row["skip"] = f"http {r.status_code}"
            else:
                img = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
                if cp.is_dark(img):
                    row["skip"] = "dark"
                else:
                    sky, bld = _neural_sky_and_building_masks(img)
                    try:
                        import torch
                        if torch.cuda.is_available():
                            torch.cuda.empty_cache()
                    except Exception:  # noqa: BLE001
                        pass
                    if sky is None:
                        row["skip"] = "segmentation failed"
                    else:
                        prof = sm.photo_profile(sky, bld)
                        row["px"] = [prof.width, prof.height]
                        row["coverage"] = float(np.isfinite(prof.y_top).mean())
                        arrs[row["key"]] = prof.y_top.astype(np.float32)
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
