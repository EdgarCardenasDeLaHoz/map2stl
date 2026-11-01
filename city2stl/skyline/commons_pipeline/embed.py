"""Stage 3 (script 16): image embeddings of a region's photos (DINOv2-small), for same-spot search.

Why: skyline-outline similarity alone matched only 3-4 of 18 Miami links correctly (simple or
partial skylines fit almost anything). An image embedding sees the whole scene (water, bridges,
foreground), so it proposes same-spot candidates; the outline fit then confirms them
(``photo_groups.embed_links``). User choice 2026-10-04.

``cache_thumbnails`` keeps 500 px thumbnails in ``<commons_cache>/<region>/img/`` (one download
per photo, ever); ``embed_images`` writes ``embed.npz`` (L2-normalised CLS + mean-patch
embeddings, keyed like the profiles). GPU when available; the CUDA cache is released at the end.
"""

from __future__ import annotations

import io
import logging
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from city2stl.skyline import commons_photos as cp

from .cache import cache_dir, get_with_retry, new_session, outlined, thumb_url

MODEL = "facebook/dinov2-small"


def cache_thumbnails(S: requests.Session, meta: list[dict], img_dir: Path) -> int:
    """Download each photo's 500 px thumbnail not yet in ``img_dir``; returns how many were fetched."""
    img_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for m in meta:
        f = img_dir / f"{m['key']}.jpg"
        if f.exists():
            continue
        for url in (thumb_url(m["url"]), m["url"]):  # a small original refuses 500 px
            r = get_with_retry(S, url, timeout=120, tries=6)
            if r.status_code == 200:
                break
        r.raise_for_status()
        f.write_bytes(r.content)
        n += 1
        time.sleep(0.3)
    return n


def embed_images(meta: list[dict], img_dir: Path, batch: int = 16) -> tuple[list[str], np.ndarray, str]:
    """DINOv2 embeddings of the cached thumbnails: ``(keys, vectors, device)``."""
    import torch
    from transformers import AutoImageProcessor, AutoModel

    from city2stl.resources import free_gpu_cache

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    if dev == "cuda":
        from city2stl.resources import wait_for_gpu

        wait_for_gpu(1.0)                       # DINOv2-small and a batch of thumbnails
    proc = AutoImageProcessor.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL).to(dev).eval()
    keys, vecs = [], []
    with torch.inference_mode():
        for i in range(0, len(meta), batch):
            chunk = meta[i:i + batch]
            ims = [Image.open(io.BytesIO((img_dir / f"{m['key']}.jpg").read_bytes())).convert("RGB")
                   for m in chunk]
            out = model(**proc(images=ims, return_tensors="pt").to(dev))
            cls = out.last_hidden_state[:, 0]
            patches = out.last_hidden_state[:, 1:].mean(dim=1)
            v = torch.nn.functional.normalize(torch.cat([cls, patches], dim=1), dim=1)
            vecs.append(v.float().cpu().numpy())
            keys += [m["key"] for m in chunk]
    del model
    if dev == "cuda":
        free_gpu_cache()
    return keys, np.concatenate(vecs), dev


def embed_region(region: str) -> int:
    """Script 16: thumbnails and ``embed.npz`` for the region's outlined photos."""
    d = cache_dir(region)
    meta = outlined(region)
    img_dir = cp.img_dir(d)
    cache_thumbnails(new_session(), meta, img_dir)
    logging.info("[embed] %d thumbnails cached", len(meta))
    keys, vecs, dev = embed_images(meta, img_dir)
    np.savez_compressed(d / "embed.npz", keys=np.array(keys), vecs=vecs)
    print(f"{region}: {len(keys)} embeddings ({MODEL}, {dev})")
    return len(keys)
