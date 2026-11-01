"""Stage 1 (script 13): search Commons, fetch, screen and outline a region's skyline photos.

All landscape JPEGs in the region's skyline categories, located or not, wide or not (portraits
are recorded and skipped). Per photo: the Commons metadata (location, EXIF FOV, compass, date,
licence), the outline (``skyline_match.photo_profile``: top building row per column) at
1600 px wide, and its clarity (``commons_photos.skyline_quality``, sky brightness). Dark photos
are not skipped: the photo pipeline keeps a low-light photo whose skyline is clear
(``commons_photos.quality_reject``; user review 2026-10-07).

Writes ``<commons_cache>/<region>/profiles.json`` + ``profiles.npz``; reruns skip photos
already cached.
"""

from __future__ import annotations

import dataclasses
import io
import logging
import time
from collections.abc import Callable

import numpy as np
import requests
from PIL import Image

from city2stl.skyline import commons_photos as cp
from city2stl.skyline import skyline_match as sm

from .cache import cache_dir, get_with_retry, load_profiles, load_site, new_session, save_profiles

WIDTH = 1600

#: ``img -> (sky mask, building mask)``; ``sky`` is None when segmentation failed
MaskFn = Callable[[np.ndarray], "tuple[np.ndarray | None, np.ndarray | None]"]


def outline_into(row: dict, img: np.ndarray, arrs: dict, masks: MaskFn) -> None:
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


def region_photos(S: requests.Session, region: str, extra_categories=()) -> list[cp.CommonsPhoto]:
    """Search: the region's skyline categories (plus the site's ``commons_categories``, which add
    neighbourhoods whose categories don't say "skyline": Cartagena's Bocagrande, its beaches,
    Hotel Estelar) and the described files in them."""
    cats = list(dict.fromkeys(cp.skyline_categories(S, region.replace("_", " ")) + list(extra_categories)))
    print(f"[profiles] {region}: categories {cats}")
    return cp.describe_files(S, cp.category_files(S, cats))


def screen_row(p: cp.CommonsPhoto, bbox, key: str) -> dict:
    """The cache row for a photo before any download: its metadata, why it would not be usable
    (``cp.screen``) and its key."""
    return {**{k: v for k, v in dataclasses.asdict(p).items() if k != "notes"},
            "why_not_usable": cp.screen(p, bbox)[1], "key": key}


def fetch_and_outline(S: requests.Session, row: dict, url: str, arrs: dict, masks: MaskFn) -> None:
    """Download ``url`` and outline it into ``row`` (``row["skip"] = "http N"`` on failure)."""
    r = get_with_retry(S, url)
    if r.status_code != 200:
        row["skip"] = f"http {r.status_code}"
        return
    outline_into(row, np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB")), arrs, masks)


def profile_region(region: str, masks: MaskFn) -> tuple[list[dict], dict[str, np.ndarray]]:
    """Run the stage for ``region``; returns the whole cache (old rows plus new ones)."""
    site = load_site(region)
    bbox = (site["north"], site["south"], site["east"], site["west"])
    S = new_session()
    cp.FETCH_WIDTH = WIDTH
    photos = region_photos(S, region, site.get("commons_categories", []))
    meta, arrs = load_profiles(region)
    done = {m["title"] for m in meta}
    d = cache_dir(region)
    d.mkdir(parents=True, exist_ok=True)
    for i, p in enumerate(photos):
        if p.title in done:
            continue
        row = screen_row(p, bbox, f"p{len(meta)}")
        if p.height > p.width:
            row["skip"] = "portrait"
        else:
            fetch_and_outline(S, row, p.url, arrs, masks)
            time.sleep(0.4)
        meta.append(row)
        if len(meta) % 10 == 0:
            save_profiles(d, meta, arrs)
            logging.info("[profiles] %d/%d", i + 1, len(photos))
    save_profiles(d, meta, arrs)
    n_ok = sum("px" in m for m in meta)
    print(f"{region}: {len(meta)} photos, {n_ok} outlines, located {sum(m['lat'] is not None and 'px' in m for m in meta)}")
    return meta, arrs


def rescreen(region: str, masks: MaskFn) -> tuple[int, int]:
    """Outline the rows skipped as dark; clarity fields for cached outlines (from ``img/``, no
    GPU). Returns ``(formerly dark outlined, outlines given clarity)``."""
    meta, arrs = load_profiles(region)
    d = cache_dir(region)
    S = new_session()
    n_dark = n_q = 0
    for i, row in enumerate(meta):
        cached = cp.img_dir(d) / f"{row['key']}.jpg"
        if row.get("skip") == "dark":
            r = get_with_retry(S, row["url"])
            if r.status_code != 200:
                row["skip"] = f"http {r.status_code}"
                continue
            img = np.asarray(Image.open(io.BytesIO(r.content)).convert("RGB"))
            outline_into(row, img, arrs, masks)
            if "px" in row:
                cp.img_dir(d).mkdir(parents=True, exist_ok=True)
                Image.fromarray(img).save(cached, quality=88)
            n_dark += 1
            time.sleep(0.4)
        elif "px" in row and "quality" not in row and cached.exists():
            img = np.asarray(Image.open(cached).convert("RGB"))
            row["quality"] = cp.skyline_quality(img, arrs[row["key"]], row["px"][1])
            row["sky_value"] = cp.sky_value(img)
            n_q += 1
        if (i + 1) % 25 == 0:
            save_profiles(d, meta, arrs)
            logging.info("[rescreen] %d/%d", i + 1, len(meta))
    save_profiles(d, meta, arrs)
    print(f"{region}: outlined {n_dark} formerly dark photos, clarity for {n_q} cached outlines")
    return n_dark, n_q
