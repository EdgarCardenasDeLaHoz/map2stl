"""Faster MobileSAM building instances (T42 prototype; same output as ``building_instances``).

``building_instances.building_instances`` prompts MobileSAM on a fixed ``grid_px`` grid over the
SegFormer building pixels of each ``window_px`` window, moves every prompt's three full-window
masks to the CPU and filters them in a Python loop. This module keeps its windows, filters,
dedupe and painting (it imports them) and changes how the prompts are chosen and decoded:

- **Seed prompts** (``seeds``): ``"grid"`` (the original grid), ``"coarse"`` (every
  ``coarse_factor``-th grid point), ``"peaks"`` (a few points under each local top of the
  building outline), or ``"points"`` (caller's points, e.g. :func:`footprint_points` from the
  projected OSM footprints).
- **Refinement** (``refine``): after the seed prompts of a window, prompt the fine-grid points
  the kept masks leave uncovered (``"gaps"``), or those plus the points within ``edge_px`` of a
  boundary between two kept masks (``"gaps_edges"``). ``None``: seeds only.
- **Decoding on the GPU**: the score / area / building-fraction / width / window-edge filters
  run batched on the device, and only the chosen mask of each prompt (cropped to its box) is
  copied back. Masks are upsampled in chunks of ``upsample_chunk`` prompts (SAM's own
  ``postprocess_masks`` upsamples a whole batch to 1024 x 1024 floats first: 0.8 GB at 64).
- **Batches sized to free GPU memory** (``batch=None``, the default on CUDA): the decoder's
  per-prompt memory is measured once on a few prompts, the upsampling's is computed from the
  window size, and each gets half of what is left under ``gpu_cap_gb`` (whole card, other
  processes and the CUDA context included) less a margin: PyTorch caches the decoder's and the
  upsampling's blocks separately, so the reserved memory is their sum, not their maximum. A fixed 64 (the original) peaked at 2.1 GB allocated and
  4.6 GB reserved on the 4 GB card (spilling into shared RAM; seed_1, 2026-10-06). If
  ``torch.cuda.memory_reserved()`` still passes ``abort_reserved_gb``,
  :class:`GpuBudgetExceeded` is raised.
- ``fp16``: autocast the encoder and decoder to float16.
- ``full_cover``: add a last window flush with the right edge. The original snaps window
  starts to multiples of half a window, so the last ``W % (window_px // 2)`` columns (up to
  half a window) are never prompted.

``stats`` (a dict, optional) receives encoder passes, decoded prompts, batch sizes, peak GPU
memory and the time spent.

Benchmark (T42, 2026-10-07, GTX 1650 4 GB, Cartagena seeds 1/4/5/6): the original's fixed
batch of 64 reserved 4.6 GB and spilled into shared RAM (seed_1: 85.6 s); memory-sized batches
give the same instances (seed_1 identical; seed_6 99.7 % of instances at IoU >= 0.7, batch-size
float noise) in 12.3 s at <= 1.5 GB reserved (seed_6, 2,076 x 7,504 px: 137 s, 2.1 GB). The
decoder runs once per prompt over the full 64 x 64 embedding (~8 ms a prompt), so only fewer
prompts cut time further: ``coarse`` + ``gaps_edges`` 0.79x the time, 95 % of instances;
``peaks``, ``osm`` points and ``gaps`` alone lose more (83-94 %). ``fp16`` autocast is 1.7-2x
slower on this card (no tensor cores) and drifts (96 %). torch.compile (no triton) and ONNX
Runtime are not installed. Default: the grid, memory-sized batches.
"""

from __future__ import annotations

import logging
import math
import time

import numpy as np

from .building_instances import Mask, _dedupe, paint

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------------- prompts

def grid_points(sub: np.ndarray, grid_px: int, phase: int | None = None) -> np.ndarray:
    """(N, 2) float32 (x, y) grid points on ``sub``'s True pixels; the original prompt grid."""
    H, W = sub.shape
    p = grid_px // 2 if phase is None else phase
    gy, gx = np.mgrid[p:H:grid_px, p:W:grid_px]
    ok = sub[gy, gx]
    return np.column_stack([gx[ok], gy[ok]]).astype(np.float32)


def peak_points(building: np.ndarray, grid_px: int, radius_px: int | None = None,
                depths=(0.5, 2.0, 4.0)) -> np.ndarray:
    """Points under each local top of the building outline (a skyline peak: a tower).

    The outline is the first building row per column; a peak is a column whose top is the
    highest within ``radius_px`` (default ``2 * grid_px``). Points go ``depths`` x ``grid_px``
    rows below the top, where the pixel is building."""
    H, W = building.shape
    has = building.any(0)
    top = np.where(has, building.argmax(0), H).astype(float)
    r = radius_px or 2 * grid_px
    from scipy.ndimage import minimum_filter1d

    lo = minimum_filter1d(top, 2 * r + 1, mode="nearest")
    cols = np.flatnonzero(has & (top <= lo) & (top < H))
    # a flat top gives a run of equal columns: one peak at its middle
    out = []
    if cols.size:
        runs = np.split(cols, np.flatnonzero(np.diff(cols) > 1) + 1)
        for run in runs:
            x = int(run[len(run) // 2])
            for d in depths:
                y = int(top[x] + d * grid_px)
                if y < H and building[y, x]:
                    out.append((x, y))
    return np.asarray(out, np.float32).reshape(-1, 2)


def footprint_points(pano, pose, footprints, grid_px: int, max_dist_m: float = 3000.0,
                     min_cols: float = 1.0, n_rows: int = 3,
                     snap_px: int | None = None) -> np.ndarray:
    """Prompt points from the OSM footprints projected into the pano: at each footprint's
    centre column, the topmost building pixel + ``grid_px / 2`` and ``n_rows - 1`` points
    spaced down to the footprint's base row (building pixels only). Pano-wide (x, y).

    One point per ``snap_px`` cell (default ``2 * grid_px``): Cartagena's ~2,500 small
    footprints within 3 km gave 7,700 prompts on seed_1 unsnapped, 5x the dense grid."""
    from . import footprint_detect as fd

    building = np.isin(pano.labels, fd.BUILDING_CLASSES)
    H, W = building.shape
    bu, b0 = fd.bearing_columns(pano, pose)
    h = pose.camera_h_m
    out = []
    for fp in footprints:
        xy = fd._local(pano.lat, pano.lon, np.asarray(fp.ring, float))
        d = np.hypot(xy[:, 0], xy[:, 1])
        dn = float(d.min())
        if dn < 20.0 or dn > max_dist_m:
            continue
        bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1])) % 360.0
        rel = (bear - bear[0] + 180.0) % 360.0 - 180.0
        c0 = fd.column_of((bear[0] + rel.min()) % 360.0, bu, b0)
        c1 = fd.column_of((bear[0] + rel.max()) % 360.0, bu, b0)
        if not (np.isfinite(c0) and np.isfinite(c1)) or c1 - c0 < min_cols:
            continue
        x = int(round(0.5 * (c0 + c1)))
        if not 0 <= x < W or not building[:, x].any():
            continue
        base = float(fd._row_of(pano, pose, -math.degrees(math.atan2(h, dn))))
        col = building[:, x]
        y_top = int(col.argmax()) + grid_px // 2
        y_bot = int(min(H - 1, base - grid_px // 2))
        ys = [y_top] if y_bot <= y_top else np.linspace(y_top, y_bot, n_rows).round().astype(int)
        out.extend((x, int(y)) for y in ys if 0 <= y < H and col[y])
    if not out:
        return np.zeros((0, 2), np.float32)
    pts = np.asarray(out, np.int64)
    snap = 2 * grid_px if snap_px is None else snap_px
    _, first = np.unique(pts // max(1, snap), axis=0, return_index=True)
    return pts[np.sort(first)].astype(np.float32)


def refine_points(sub: np.ndarray, found: list[Mask], x_off: int, grid_px: int,
                  mode: str, edge_px: int, used: np.ndarray) -> np.ndarray:
    """Fine-grid points of the window ``sub`` to prompt after the seed pass: uncovered by any
    kept mask (``"gaps"``), or also within ``edge_px`` of a boundary between two masks as
    painted (``"gaps_edges"``). ``used``: seed points already prompted (window coords)."""
    pts = grid_points(sub, grid_px)
    if not len(pts):
        return pts
    H, W = sub.shape
    lab = np.zeros((H, W), np.int32)
    for i, (_s, y, x, m) in enumerate(sorted(found, key=lambda t: -int(t[3].sum())), 1):
        x = x - x_off
        lab[y:y + m.shape[0], x:x + m.shape[1]][m] = i
    xi, yi = pts[:, 0].astype(int), pts[:, 1].astype(int)
    want = lab[yi, xi] == 0
    if mode == "gaps_edges":
        e = edge_px
        edge = np.zeros((H, W), bool)
        # a pixel near a label change (between two masks, or a mask and the gap beside it)
        for dy, dx in ((e, 0), (0, e)):
            a, b = lab[:H - dy, :W - dx], lab[dy:, dx:]
            diff = (a != b) & (a > 0) & (b > 0)
            edge[:H - dy, :W - dx] |= diff
            edge[dy:, dx:] |= diff
        want |= edge[yi, xi]
    elif mode != "gaps":
        raise ValueError(f"refine mode {mode!r}")
    if len(used):
        u = {(int(a), int(b)) for a, b in used}
        want &= np.array([(int(a), int(b)) not in u for a, b in pts])
    return pts[want]


# --------------------------------------------------------------------------------- decoding

class GpuBudgetExceeded(RuntimeError):
    """The GPU memory reserved by this process passed ``abort_reserved_gb``."""


#: Decoder memory per prompt (bytes), measured once per (model, fp16).
_DEC_PER_PROMPT: dict = {}

#: Masks found in a window before they are deduped (the original's 2 x 64).
DEDUPE_EVERY = 128


def _headroom(cap_bytes: float) -> float:
    """Bytes this process may still take so the whole card stays under ``cap_bytes`` (other
    processes and the CUDA context, ~0.8 GB on the GTX 1650, included). Memory cached by
    PyTorch but free does not count: its blocks are sized for other tensors."""
    import torch

    free, total = torch.cuda.mem_get_info()
    return cap_bytes - (total - free)


def _upsample_bytes(S: int, h: int, w: int) -> float:
    """Peak bytes per prompt of the chunked upsampling in :meth:`_Decoder.__call__`: three
    masks at S x S floats (and a cropped copy), then at h x w floats, bools and temps."""
    return 3 * (2 * S * S * 4 + h * w * (4 + 3))

class _Decoder:
    """Decodes point prompts on the current image of a ``SamPredictor`` and keeps, per prompt,
    the best-scoring mask that passes the filters of ``building_instances`` (on the device)."""

    def __init__(self, pred, sub_t, x0: int, x1: int, W: int, min_score: float,
                 min_building_frac: float, max_width_frac: float, min_mask_px: int,
                 upsample_chunk: int, fp16: bool):
        self.pred, self.sub, self.x0, self.x1, self.W = pred, sub_t, x0, x1, W
        self.min_score, self.min_bf, self.min_px = min_score, min_building_frac, min_mask_px
        self.max_w = max_width_frac * (x1 - x0)
        self.chunk, self.fp16 = upsample_chunk, fp16
        self.calls = 0

    def _decode(self, c, lbl):
        import torch

        model = self.pred.model
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16, enabled=self.fp16
                                             and self.pred.device.type == "cuda"):
            sp, dn = model.prompt_encoder(points=(c, lbl), boxes=None, masks=None)
            return model.mask_decoder(image_embeddings=self.pred.features,
                                      image_pe=model.prompt_encoder.get_dense_pe(),
                                      sparse_prompt_embeddings=sp,
                                      dense_prompt_embeddings=dn, multimask_output=True)

    def probe(self, n: int = 8) -> float:
        """Decoder bytes per prompt, from the peak of one batch of ``n`` dummy prompts. Resets
        the CUDA peak counter (:func:`building_instances` keeps its own maximum)."""
        import torch

        key = (id(self.pred.model), self.fp16)
        if key not in _DEC_PER_PROMPT:
            dev = self.pred.device
            torch.cuda.synchronize()
            torch.cuda.reset_peak_memory_stats()
            base = torch.cuda.memory_allocated()
            c = torch.full((n, 1, 2), 512.0, device=dev)
            out = self._decode(c, torch.ones((n, 1), device=dev))
            torch.cuda.synchronize()
            peak = torch.cuda.max_memory_allocated() - base
            del out
            _DEC_PER_PROMPT[key] = peak / n
        return _DEC_PER_PROMPT[key]

    def __call__(self, pts: np.ndarray) -> list[Mask]:
        import torch
        import torch.nn.functional as F

        pred = self.pred
        model = pred.model
        h, w = self.sub.shape
        dev = pred.device
        if not len(pts):
            return []
        self.calls += len(pts)
        c = torch.as_tensor(pred.transform.apply_coords(pts, (h, w)), device=dev)[:, None, :]
        low, sc = self._decode(c, torch.ones(c.shape[:2], device=dev))
        sc = sc.float()
        # a prompt none of whose three masks reaches min_score gives nothing: skip its upsampling
        # (the upsampling and filters, not the decoder, took most of the time)
        live = torch.nonzero((sc >= self.min_score).any(1))[:, 0]
        if len(live) < len(sc):
            low, sc = low[live], sc[live]
        out: list[Mask] = []
        S = model.image_encoder.img_size
        ih, iw = pred.input_size
        cols = torch.arange(w, device=dev)
        with torch.no_grad():
            for i in range(0, len(sc), self.chunk):
                lo = low[i:i + self.chunk].float()
                # SAM's postprocess_masks, a chunk at a time
                m = F.interpolate(lo, (S, S), mode="bilinear", align_corners=False)
                m = m[..., :ih, :iw]
                m = F.interpolate(m, (h, w), mode="bilinear", align_corners=False)
                m = m > model.mask_threshold                      # B x 3 x h x w
                area = m.sum((2, 3))
                inb = (m & self.sub).sum((2, 3))
                anyc = m.any(2)                                   # B x 3 x w
                first = torch.where(anyc, cols, w).amin(2)
                last = torch.where(anyc, cols, -1).amax(2)
                s = sc[i:i + self.chunk]
                ok = ((s >= self.min_score) & (area >= self.min_px)
                      & (inb >= self.min_bf * area.clamp(min=1)) & (area > 0)
                      & ((last - first) <= self.max_w))
                if self.x0 > 0:
                    ok &= first != 0
                if self.x1 < self.W:
                    ok &= last != w - 1
                best = torch.where(ok, s, -1.0).argmax(1)
                good = ok.gather(1, best[:, None])[:, 0]
                idx = torch.nonzero(good)[:, 0]
                if not len(idx):
                    continue
                sel = m[idx, best[idx]] & self.sub              # K x h x w
                rows = sel.any(2)
                cs = sel.any(1)
                ry0 = rows.float().argmax(1)
                ry1 = h - 1 - rows.flip(1).float().argmax(1)
                rx0 = cs.float().argmax(1)
                rx1 = w - 1 - cs.flip(1).float().argmax(1)
                bb = torch.stack([ry0, ry1, rx0, rx1], 1).cpu().numpy()
                scs = s[idx, best[idx]].cpu().numpy()
                # one copy of the K masks' union box, then crops on the CPU
                Y0, Y1, X0, X1 = bb[:, 0].min(), bb[:, 1].max(), bb[:, 2].min(), bb[:, 3].max()
                block = sel[:, Y0:Y1 + 1, X0:X1 + 1].cpu().numpy()
                for k in range(len(bb)):
                    y0, y1, a0, a1 = bb[k]
                    out.append((float(scs[k]), int(y0), int(a0) + self.x0,
                                block[k, y0 - Y0:y1 - Y0 + 1, a0 - X0:a1 - X0 + 1].copy()))
        return out


def building_instances(rgb: np.ndarray, building: np.ndarray, device: str | None = None,
                       window_px: int = 448, grid_px: int = 12, batch: int | None = None,
                       min_score: float = 0.8, min_building_frac: float = 0.8,
                       max_width_frac: float = 0.45, min_mask_px: int = 60,
                       iou: float = 0.6, *, seeds: str = "grid", coarse_factor: int = 2,
                       points: np.ndarray | None = None, refine: str | None = None,
                       edge_px: int | None = None, fp16: bool = False,
                       full_cover: bool = True, upsample_chunk: int | None = None,
                       gpu_cap_gb: float = 3.0, abort_reserved_gb: float = 3.5,
                       max_batch: int = 128, stats: dict | None = None
                       ) -> np.ndarray | None:
    """Instance label map (H x W int32, 0 = none) of the ``building`` pixels of ``rgb``, or None
    without MobileSAM. Defaults reproduce ``building_instances.building_instances``; see the
    module docstring for ``seeds`` / ``points`` / ``refine`` / ``fp16``. ``batch`` /
    ``upsample_chunk``: None sizes them to free GPU memory (64 / 16 on the CPU). The caller
    holds the GPU (``resources.wait_for_gpu``) and frees it after."""
    from ._core import segmentation as sg

    if not sg._ensure_mobilesam():
        return None
    import torch

    t_start = time.perf_counter()
    pred = sg._mobilesam_predictor
    if device is None:
        device = "cuda" if torch.cuda.is_available() else "cpu"
    pred.model.to(device)
    H, W = building.shape
    window_px = min(window_px, W)
    half = window_px // 2
    edge_px = grid_px if edge_px is None else edge_px
    if seeds == "peaks":
        all_seed = peak_points(building, grid_px)
    elif seeds == "points":
        all_seed = np.zeros((0, 2), np.float32) if points is None else np.asarray(points, np.float32)
    elif seeds not in ("grid", "coarse"):
        raise ValueError(f"seeds {seeds!r}")
    cols = np.flatnonzero(building.sum(0) >= 4)
    starts = {min(max(0, int(c) - half), W - window_px) // half * half for c in cols}
    if full_cover and len(cols) and cols[-1] >= max(starts, default=0) + window_px:
        # the half-window snap never reaches the last W % half columns: buildings there got
        # no instance (seed_1: columns 2464-2619 of 2620)
        starts.add(W - window_px)
    starts = sorted(starts)
    masks: list[Mask] = []
    n_enc = n_dec = 0
    t_enc = 0.0
    cuda = str(device).startswith("cuda")
    cap, abort = gpu_cap_gb * 2**30, abort_reserved_gb * 2**30
    peak = peak_res = 0
    sizes = None

    def check():
        nonlocal peak, peak_res
        if cuda:
            peak = max(peak, torch.cuda.max_memory_allocated())
            peak_res = max(peak_res, torch.cuda.max_memory_reserved())
            # refine passes decode odd-sized batches, and the cache keeps a block per size:
            # hand the cache back before the card passes the cap (peaks_ge reached 3.07 GB
            # used on seed_5 without this, 2026-10-07)
            free, total = torch.cuda.mem_get_info()
            if total - free > 0.9 * cap:
                torch.cuda.empty_cache()
            if torch.cuda.memory_reserved() > abort:
                raise GpuBudgetExceeded(f"{torch.cuda.memory_reserved() / 2**30:.2f} GB reserved"
                                        f" > {abort_reserved_gb} GB")

    for x0 in starts:
        x1 = min(W, x0 + window_px)
        sub = building[:, x0:x1]
        if sub.sum() < 50:
            continue
        if seeds == "grid":
            pts = grid_points(sub, grid_px)
        elif seeds == "coarse":
            g = grid_px * coarse_factor
            pts = grid_points(sub, g, phase=grid_px // 2 + (coarse_factor // 2) * grid_px)
        else:
            inw = (all_seed[:, 0] >= x0) & (all_seed[:, 0] < x1)
            pts = all_seed[inw] - np.array([x0, 0], np.float32)
        if not len(pts) and refine is None:
            continue
        t = time.perf_counter()
        with torch.no_grad(), torch.autocast("cuda", dtype=torch.float16,
                                             enabled=fp16 and cuda):
            pred.set_image(np.ascontiguousarray(rgb[:, x0:x1]))
        if cuda:
            torch.cuda.synchronize()
        t_enc += time.perf_counter() - t
        n_enc += 1
        check()
        dec = _Decoder(pred, torch.as_tensor(sub, device=pred.device), x0, x1, W, min_score,
                       min_building_frac, max_width_frac, min_mask_px, upsample_chunk or 16,
                       fp16)
        if sizes is None:
            # once, on the first window (all windows are the same size): later windows would
            # see the first one's cached blocks as used and shrink their batches
            sizes = (batch or 64, upsample_chunk or 16)
            if cuda and (batch is None or upsample_chunk is None):
                room = 0.35 * _headroom(cap)         # 70 % of it, half each
                bs = sizes[0] if batch else int(max(1, min(max_batch, room // dec.probe())))
                up = (upsample_chunk if upsample_chunk else int(max(1, min(
                    bs, room // _upsample_bytes(pred.model.image_encoder.img_size, *sub.shape)))))
                sizes = (bs, up)
            if stats is not None:
                stats["batch"], stats["upsample_chunk"] = sizes
        bs, dec.chunk = sizes

        def run(p, found, dec=dec, bs=bs):
            for i in range(0, len(p), bs):
                found.extend(dec(p[i:i + bs]))
                check()
                if len(found) > DEDUPE_EVERY:
                    found[:] = _dedupe(found, iou)
            return found

        found = run(pts, [])
        if refine is not None:
            found = _dedupe(found, iou)
            more = refine_points(sub, found, x0, grid_px, refine, edge_px, pts)
            found = run(more, found)
        n_dec += dec.calls
        masks.extend(_dedupe(found, iou))
    pred.reset_image()
    lab = paint(_dedupe(masks, iou), (H, W))
    if stats is not None:
        stats.update(encoder_passes=n_enc, decoder_prompts=n_dec, encoder_s=t_enc,
                     gpu_peak_mb=round(peak / 2**20, 1),
                     gpu_reserved_peak_mb=round(peak_res / 2**20, 1),
                     total_s=time.perf_counter() - t_start, windows=len(starts),
                     instances=int(len(np.unique(lab)) - 1))
    logger.info("[instances-fast] %d instances, %d windows, %d prompts",
                len(np.unique(lab)) - 1, n_enc, n_dec)
    return lab
