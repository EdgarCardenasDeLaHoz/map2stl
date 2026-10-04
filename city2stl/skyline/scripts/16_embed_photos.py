#!/usr/bin/env python3
"""F-WEB2 step G4: image embeddings of a region's photos (DINOv2-small), for same-spot search.

    python -m city2stl.skyline.scripts.16_embed_photos --region miami

Why: skyline-outline similarity alone matched only 3-4 of 18 Miami links correctly (simple or
partial skylines fit almost anything). An image embedding sees the whole scene (water, bridges,
foreground), so it proposes same-spot candidates; the outline fit then confirms them
(``photo_groups.embed_links``). User choice 2026-10-04.

Reads the outline cache (``13_photo_profiles``) for the photo list, caches 500 px thumbnails in
``runs/commons_cache/<region>/img/`` (one download per photo, ever), writes ``embed.npz``
(L2-normalised CLS embeddings, keyed like the profiles). GPU when available; the CUDA cache is
released at the end.
"""

from __future__ import annotations

import argparse
import io
import json
import logging
import re
import time
from pathlib import Path

import numpy as np
import requests
from PIL import Image

from city2stl.skyline import commons_photos as cp

ROOT = Path(__file__).resolve().parents[1]
MODEL = "facebook/dinov2-small"


def _thumb_url(url: str, width: int = 500) -> str:
    """Commons serves only standard thumbnail widths (https://w.wiki/GHai): 512 is refused."""
    return re.sub(r"/(\d+)px-", f"/{width}px-", url)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--region", required=True)
    args = ap.parse_args()
    import torch
    from transformers import AutoImageProcessor, AutoModel

    d = ROOT / "runs" / "commons_cache" / args.region
    meta = [m for m in json.loads((d / "profiles.json").read_text(encoding="utf-8")) if "px" in m]
    img_dir = d / "img"
    img_dir.mkdir(exist_ok=True)
    S = requests.Session()
    S.headers["User-Agent"] = cp.UA
    for m in meta:
        f = img_dir / f"{m['key']}.jpg"
        if f.exists():
            continue
        for url in (_thumb_url(m["url"]), m["url"]):  # a small original refuses 500 px
            for a in range(6):
                r = S.get(url, timeout=120)
                if r.status_code != 429:
                    break
                time.sleep(4 * (a + 1))
            if r.status_code == 200:
                break
        r.raise_for_status()
        f.write_bytes(r.content)
        time.sleep(0.3)
    logging.info("[embed] %d thumbnails cached", len(meta))

    dev = "cuda" if torch.cuda.is_available() else "cpu"
    proc = AutoImageProcessor.from_pretrained(MODEL)
    model = AutoModel.from_pretrained(MODEL).to(dev).eval()
    keys, vecs = [], []
    with torch.inference_mode():
        for i in range(0, len(meta), 16):
            batch = meta[i:i + 16]
            ims = [Image.open(io.BytesIO((img_dir / f"{m['key']}.jpg").read_bytes())).convert("RGB")
                   for m in batch]
            out = model(**proc(images=ims, return_tensors="pt").to(dev))
            cls = out.last_hidden_state[:, 0]
            patches = out.last_hidden_state[:, 1:].mean(dim=1)
            v = torch.nn.functional.normalize(torch.cat([cls, patches], dim=1), dim=1)
            vecs.append(v.float().cpu().numpy())
            keys += [m["key"] for m in batch]
    del model
    if dev == "cuda":
        torch.cuda.empty_cache()
    np.savez_compressed(d / "embed.npz", keys=np.array(keys), vecs=np.concatenate(vecs))
    print(f"{args.region}: {len(keys)} embeddings ({MODEL}, {dev})")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    raise SystemExit(main())
