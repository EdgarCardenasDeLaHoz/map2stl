"""Building instances on a panorama with MobileSAM (one label per building).

SegFormer's ADE20K labels say *building* but not *which* building: from a drone, a city's towers
merge into one mask band, and footprint runs (``footprint_detect.measure_footprints``) can only
stop at a depth step, which Depth Anything blurs between towers 10-30 % apart (2026-10-06,
Cartagena seed_1: readings within 25 % of their OSM tag 50 %). MobileSAM separates the towers
by their outline and facade texture.

Prompts: one point every ``grid_px`` over the SegFormer building pixels, in windows of
``window_px`` columns (the encoder's input is 1024 px; a 2,688 px pano at once shrinks a far
tower to a few pixels). For each prompt, the best-scoring of SAM's three masks that is mostly
building and narrower than ``max_width_frac`` of the window (the widest mask is the whole
skyline). Masks are deduplicated by IoU, then painted largest first so a smaller mask wins: a
tower in front of a wider block keeps its own label.

Without MobileSAM (package or checkpoint missing) :func:`building_instances` returns None and
callers measure as before. Model loading: ``_core.segmentation._ensure_mobilesam``
(``MOBILESAM_CHECKPOINT_PATH``, default ``~/.cache/mobile_sam/vit_t.pth``).
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)


def _dedupe(masks: list[tuple[float, np.ndarray]], iou: float) -> list[tuple[float, np.ndarray]]:
    """Highest score first; drop a mask whose IoU with a kept one exceeds ``iou`` (on 1/4-res
    copies: a few thousand full-res pairwise IoUs took minutes)."""
    if not masks:
        return []
    small = np.stack([m[::4, ::4].ravel() for _, m in masks]).astype(np.float32)
    inter = small @ small.T
    area = small.sum(1)
    keep: list[int] = []
    for i in np.argsort([-s for s, _ in masks]):
        if all(inter[i, j] / (area[i] + area[j] - inter[i, j] + 1e-6) <= iou for j in keep):
            keep.append(int(i))
    return [masks[i] for i in keep]


def paint(masks: list[tuple[float, np.ndarray]], shape: tuple[int, int],
          min_px: int = 40) -> np.ndarray:
    """Label map (0 = none, 1.. = instance) with larger masks painted first, so smaller ones
    win; labels left with fewer than ``min_px`` pixels are dropped."""
    lab = np.zeros(shape, np.int32)
    for i, (_s, m) in enumerate(sorted(masks, key=lambda t: -int(t[1].sum())), 1):
        lab[m] = i
    ids, cnt = np.unique(lab[lab > 0], return_counts=True)
    for i, c in zip(ids, cnt, strict=True):
        if c < min_px:
            lab[lab == i] = 0
    return lab


def building_instances(rgb: np.ndarray, building: np.ndarray, device: str | None = None,
                       window_px: int = 448, grid_px: int = 12, batch: int = 64,
                       min_score: float = 0.8, min_building_frac: float = 0.8,
                       max_width_frac: float = 0.45, min_mask_px: int = 60,
                       iou: float = 0.6) -> np.ndarray | None:
    """Instance label map (H x W int32, 0 = no instance) of the ``building`` pixels of ``rgb``,
    or None without MobileSAM. See the module docstring."""
    from ._core import segmentation as sg

    if not sg._ensure_mobilesam():
        return None
    import torch

    pred = sg._mobilesam_predictor
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    pred.model.to(device)
    H, W = building.shape
    window_px = min(window_px, W)
    half = window_px // 2
    cols = np.flatnonzero(building.sum(0) >= 4)
    starts = sorted({min(max(0, int(c) - half), W - window_px) // half * half for c in cols})
    masks: list[tuple[float, np.ndarray]] = []
    for x0 in starts:
        x1 = min(W, x0 + window_px)
        sub = building[:, x0:x1]
        if sub.sum() < 50:
            continue
        gy, gx = np.mgrid[grid_px // 2:H:grid_px, grid_px // 2:x1 - x0:grid_px]
        ok = sub[gy, gx]
        pts = np.column_stack([gx[ok], gy[ok]]).astype(np.float32)
        if not len(pts):
            continue
        with torch.no_grad():
            pred.set_image(np.ascontiguousarray(rgb[:, x0:x1]))
        found = []
        for i in range(0, len(pts), batch):
            p = torch.as_tensor(pred.transform.apply_coords(pts[i:i + batch], (H, x1 - x0)),
                                device=pred.device)[:, None, :]
            with torch.no_grad():
                m, sc, _ = pred.predict_torch(p, torch.ones(p.shape[:2], device=pred.device),
                                              multimask_output=True)
            m, sc = m.cpu().numpy(), sc.cpu().numpy()
            for j in range(len(m)):
                for k in np.argsort(-sc[j]):
                    mm = m[j, k]
                    a = int(mm.sum())
                    if sc[j, k] < min_score or a < min_mask_px:
                        continue
                    if (mm & sub).sum() / a < min_building_frac:
                        continue
                    xs = np.flatnonzero(mm.any(0))
                    if xs[-1] - xs[0] > max_width_frac * (x1 - x0):
                        continue
                    found.append((float(sc[j, k]), mm & sub))
                    break
        for s, mm in _dedupe(found, iou):
            full = np.zeros((H, W), bool)
            full[:, x0:x1] = mm
            masks.append((s, full))
    pred.reset_image()
    lab = paint(_dedupe(masks, iou), (H, W))
    logger.info("[instances] %d building instances from %d windows", len(np.unique(lab)) - 1,
                len(starts))
    return lab
