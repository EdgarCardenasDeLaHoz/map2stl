"""RoofNet height provider â€” wraps a trained RoofNetV3 / HeightUNet checkpoint.

Fetches an ESRI World Imagery RGB tile for the bbox, runs the model from
`city2stl.skyline.height.predict` to produce a per-pixel height raster, and exposes
it through the standard `HeightProvider` protocol so it joins the parallel
fetch pool in `app.server.routers.height`.

Coverage: global (anywhere ESRI satellite tiles exist).
Confidence: 0.65 â€” between Copernicus (0.70) and OpenBuildings (0.60), since
it is satellite-only with no external ground-truth pairing at runtime.

The provider is gated by checkpoint availability: if no `*.pt` is found at
the configured path, ``covers()`` returns False so the merge skips this
provider entirely (no spurious zeros).
"""

from __future__ import annotations

import base64
import io
import logging
import os
from pathlib import Path
from typing import Tuple

import numpy as np

from city2stl.skyline.height import BBox, HeightResult

logger = logging.getLogger(__name__)

_DEFAULT_CKPT_CANDIDATES = [
    # RoofNetV3 / HeightUNet checkpoints â€” all deprecated (marginal-mean collapse).
    # Provider auto-disables when no candidate exists. The current generation of
    # height models (Retna_V1) needs a separate provider â€” this one stays here
    # as a stub for backward compat.
    "models/roofnet_unet_iou_v2.pt",
    "models/roofnet_unet.pt",
]
_RESOLUTION_M = 5.0  # nominal â€” actual depends on bbox size & sat tile zoom
_CONFIDENCE = 0.65


def _resolve_checkpoint() -> Path | None:
    """Return the first existing checkpoint or None.

    Override via env var ROOFNET_CKPT="path/to/checkpoint.pt".
    """
    env = os.environ.get("ROOFNET_CKPT")
    candidates = [env] if env else []
    candidates.extend(_DEFAULT_CKPT_CANDIDATES)

    # roofnet.py is at city2stl/height/providers/roofnet.py -- parents[3] = strm2stl
    strm2stl = Path(__file__).resolve().parents[3]
    for c in candidates:
        if not c:
            continue
        p = Path(c)
        if not p.is_absolute():
            p = strm2stl / c
        if p.exists():
            return p
    return None


def _fetch_rgb(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
    """Fetch satellite RGB for *bbox* at *dim* using ESRI WMTS.

    Returns (H, W, 3) uint8 or None on failure.
    """
    from geo2stl.sat2stl import fetch_satellite_tiles
    from PIL import Image

    h, w = dim
    try:
        b64 = fetch_satellite_tiles(bbox[0], bbox[1], bbox[2], bbox[3], dim=max(h, w))
    except Exception as e:
        logger.warning(f"roofnet: satellite fetch failed: {e}")
        return None
    try:
        raw = base64.b64decode(b64)
        img = Image.open(io.BytesIO(raw)).convert("RGB").resize((w, h))
        return np.array(img, dtype=np.uint8)
    except Exception as e:
        logger.warning(f"roofnet: decode/resize failed: {e}")
        return None


def _empty(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="roofnet",
        resolution_m=_RESOLUTION_M,
    )


class RoofNetProvider:
    """Trained-model height provider (RoofNetV3 / HeightUNet checkpoint).

    Activates only when a checkpoint is available on disk.
    """

    name = "roofnet"

    def __init__(self):
        self._ckpt = _resolve_checkpoint()
        if self._ckpt is None:
            logger.info(
                "RoofNet provider: no checkpoint at any of "
                f"{_DEFAULT_CKPT_CANDIDATES}. Provider disabled."
            )

    def covers(self, bbox: BBox) -> bool:
        # ESRI satellite tiles cover the globe; trained model is global.
        # Disable if no checkpoint is present.
        return self._ckpt is not None

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        if self._ckpt is None:
            return _empty(dim)

        h, w = dim
        rgb = _fetch_rgb(bbox, dim)
        if rgb is None:
            logger.warning("roofnet: no satellite RGB; returning NaN raster")
            return _empty(dim)

        # Determine model variant from checkpoint name.
        ckpt_name = self._ckpt.stem.lower()
        if "unet_iou" in ckpt_name or "height_unet" in ckpt_name:
            model_kind = "height_unet"
        else:
            model_kind = "unet"

        try:
            from city2stl.skyline.height.predict import predict
            res = predict(
                sat_rgb=rgb,
                known_heights=None,
                bbox=bbox,
                model=model_kind,
                checkpoint=self._ckpt,
                device="cpu",
                tile_size=256,
                mask_threshold=0.3,
            )
        except Exception as e:
            logger.warning(f"roofnet: inference failed: {e}", exc_info=True)
            return _empty(dim)

        # Normalise resolution_m to bbox-derived value
        north, south = bbox[0], bbox[1]
        bbox_h_m = abs(north - south) * 111_320.0
        res_m = bbox_h_m / h if h > 0 else _RESOLUTION_M

        return HeightResult(
            raster=res.raster.astype(np.float32),
            confidence=np.full_like(res.raster, _CONFIDENCE, dtype=np.float32),
            source_name="roofnet",
            resolution_m=float(res_m),
        )
