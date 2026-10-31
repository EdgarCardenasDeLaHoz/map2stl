"""Overhead (Geopose) heights for footprints: a learned monocular height-above-ground model run over
the same Esri z18 scene the lean/shadow code reads (F-SATTALL, 2026-10-31).

It gives an *independent* per-footprint height. Two uses, both in this package:

- a satellite reading of method ``geopose`` (:func:`to_reading`, ``readings.footprint_readings``;
  ``weights.sigma_log`` gives it a conservative sigma; it is not a ``SUPPORT_MIN_CONF`` method in
  ``city2stl/skyline``, so it cannot publish a height on its own);
- the tall gate of ``measure.search_cap`` (a footprint whose ``geopose`` height is over
  ``HINT_TALL_M`` is searched up to ``HINT_TALL_CAP_M``). The gate is **off** until scored
  (``measure.GEOPOSE_GATE``).

Backends (:data:`BACKENDS`, first one whose weights are present wins; weights live outside
OneDrive in ``$GEOPOSE_MODELS`` or ``~/data/models/geopose/<name>/``, see ``models/README.md``):

=============  ==============================================  =========  ==========
``first``      Geopose 1st place (EfficientNetV2-L), from      lean+AGL   slot only
               ``$GEOPOSE_1ST_DIR``; no architecture code is
               bundled, so it raises :class:`BackendUnavailable`
``pubgeo``     pubgeo baseline (ResNet-34 U-Net, Apache-2.0)   lean+AGL   works
``rs3dada``    RS3DAda ViT-L DPT (MIT), AGL only: under-reads  AGL        works
               towers (AltiMap's own test: 104 m vs 151 m)
``altimap``    AltiMap v1 fine-tune of RS3DAda (MIT)           AGL        works
=============  ==============================================  =========  ==========

Resolution: the Esri z18 scene is ``scene.mpp(lat)`` per pixel (0.60 m at Cartagena). The pubgeo
baseline was trained and tested on the Geopose data downsampled by 2 (``--downsample=2`` in its
README; the originals are 0.3-0.5 m), so its working GSD is taken as 0.7 m; RS3DAda/AltiMap were
trained and tested on ~0.33 m imagery. Each backend resamples the scene to its ``gsd_m``
(``Backend.gsd_m``, override with ``gsd_m=``); the result is not re-validated at other GSDs.

The model's AGL map is registered to the *image*, so a tall roof sits displaced from its OSM
footprint along the lean. :func:`heights_for_footprints` therefore reads each footprint's median
AGL over the footprint shifted by ``h x lean`` and takes the height ``h`` where the median agrees
with ``h`` (roof area only, not the facade). The lean comes from the pubgeo model's own angle and
scale (image level, tile nearest the footprint) or, for AGL-only backends, from ``lean_m=`` (e.g.
``scene.fit_lean``); with neither the footprint is read unshifted, which under-reads towers.

GPU only (``wait_for_gpu``, no CPU fallback); ``device="cpu"`` is an explicit opt-in for a handful
of tiles. ``torch`` is imported only when a backend is loaded.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

Z = 18
#: Backends in the order they are tried.
BACKENDS = ("first", "pubgeo", "rs3dada", "altimap")
#: The ``geopose`` reading's confidence (the model has none): below ``LEAN_TRUST_CONF`` so nothing
#: treats it as a confident reading.
GEOPOSE_CONF = 0.5
#: Sign applied to the pubgeo lean ``(sin a, cos a)`` to get (east, south): see
#: :func:`lean_from_model`. Fixed by the Cartagena check in the F-SATTALL plan.
LEAN_SIGN = (1.0, 1.0)


class BackendUnavailable(RuntimeError):
    """A backend's weights or architecture are not available."""


class GpuUnavailable(RuntimeError):
    """No CUDA GPU (the module never falls back to the CPU unless ``device="cpu"``)."""


def model_root() -> Path:
    """Folder holding ``<backend dir>/<weights>``: ``$GEOPOSE_MODELS`` or ``~/data/models/geopose``."""
    return Path(os.environ.get("GEOPOSE_MODELS") or Path.home() / "data" / "models" / "geopose")


@dataclass(frozen=True)
class Backend:
    name: str
    weights: str                  # path under :func:`model_root`
    tile: int                     # tile edge in fed pixels (multiple of 32 / 14)
    gsd_m: float                  # metres per pixel the model wants
    has_lean: bool
    mean: tuple
    std: tuple
    vram_gb: float


SPECS = {
    "pubgeo": Backend("pubgeo", "pubgeo_baseline/geopose_baseline.pth", 512, 0.7, True,
                      (0.485 * 255, 0.456 * 255, 0.406 * 255), (0.229 * 255, 0.224 * 255, 0.225 * 255), 1.5),
    "rs3dada": Backend("rs3dada", "rs3dada/RS3DAda_vitl_DPT_height.pth", 518, 0.33, False,
                       (123.675, 116.28, 103.53), (58.395, 57.12, 57.375), 2.5),
    "altimap": Backend("altimap", "altimap_v1/best.pth", 518, 0.33, False,
                       (123.675, 116.28, 103.53), (58.395, 57.12, 57.375), 2.5),
}


def available_backends() -> list[str]:
    """Backends whose weights are on disk, in :data:`BACKENDS` order (``first``: only when
    ``$GEOPOSE_1ST_DIR`` exists, though it cannot be loaded yet)."""
    out = []
    for n in BACKENDS:
        if n == "first":
            d = os.environ.get("GEOPOSE_1ST_DIR")
            if d and Path(d).is_dir():
                out.append(n)
        elif (model_root() / SPECS[n].weights).is_file():
            out.append(n)
    return out


# ------------------------------------------------------------------ predictors

class TorchPredictor:
    """One loaded network: ``predict(rgb uint8 (h, w, 3)) -> (agl (h, w) float32, lean)`` where
    ``lean`` is ``(angle_rad, scale_px_per_m)`` in the tile's own pixels, or None."""

    def __init__(self, spec: Backend, net, device: str):
        self.spec, self.net, self.device = spec, net, device
        self.name, self.tile, self.gsd_m, self.has_lean = spec.name, spec.tile, spec.gsd_m, spec.has_lean

    def predict(self, rgb: np.ndarray):
        import torch  # noqa: PLC0415

        s = self.spec
        x = (rgb.astype(np.float32) - np.array(s.mean, np.float32)) / np.array(s.std, np.float32)
        t = torch.from_numpy(x.transpose(2, 0, 1)[None]).to(self.device)
        with torch.no_grad(), torch.autocast(self.device, dtype=torch.float16, enabled=self.device == "cuda"):
            out = self.net(t)
        if self.has_lean:
            xydir, agl, scale = out
            xy = xydir[0].float().cpu().numpy()
            lean = (float(math.atan2(xy[0], xy[1])), float(scale[0].float().cpu()))
        else:
            agl, lean = out, None
        return np.clip(agl[0, 0].float().cpu().numpy(), 0, None), lean


def _resolve_device(device: str | None, need_gb: float) -> str:
    if device == "cpu":
        return "cpu"
    from city2stl.resources import wait_for_gpu  # noqa: PLC0415

    if not wait_for_gpu(need_gb):
        raise GpuUnavailable("no CUDA GPU; Geopose does not run on the CPU (device='cpu' to "
                             "force a handful of tiles)")
    return "cuda"


def load_predictor(name: str, device: str | None = None) -> TorchPredictor:
    """Load backend ``name`` (``torch.load(weights_only=True)``; nothing in a checkpoint is
    executed). Raises :class:`BackendUnavailable` when its weights are missing."""
    if name == "first":
        d = os.environ.get("GEOPOSE_1ST_DIR")
        raise BackendUnavailable(
            f"Geopose 1st place: GEOPOSE_1ST_DIR={d!r}; its EfficientNetV2-L architecture is not "
            "bundled yet, add a loader here once the weights are on disk")
    spec = SPECS[name]
    path = model_root() / spec.weights
    if not path.is_file():
        raise BackendUnavailable(f"{name}: no weights at {path}")
    dev = _resolve_device(device, spec.vram_gb)
    import torch  # noqa: PLC0415

    from . import geopose_nets as gn  # noqa: PLC0415

    sd = torch.load(path, map_location="cpu", weights_only=True)
    if isinstance(sd, dict) and "model" in sd and isinstance(sd["model"], dict):
        sd = sd["model"]
    if name == "pubgeo":
        net = gn.UnetVFLOW()
        net.load_state_dict(sd)
    else:
        net = gn.DPTDino()
        net.load_checkpoint(sd)
    net.eval().to(dev)
    if dev == "cuda":
        net.half()
    return TorchPredictor(spec, net, dev)


def first_predictor(device: str | None = None, order=BACKENDS) -> TorchPredictor:
    """The first backend in ``order`` that loads; raises ``BackendUnavailable`` listing why not."""
    why = []
    for n in order:
        try:
            return load_predictor(n, device)
        except BackendUnavailable as e:
            why.append(str(e))
    raise BackendUnavailable("; ".join(why))


# ------------------------------------------------------------------ tiling

def _starts(n: int, tile: int, stride: int) -> list[int]:
    s = list(range(0, max(n - tile, 0) + 1, stride))
    if s[-1] + tile < n:
        s.append(n - tile)
    return s


def predict_tiled(pred, rgb: np.ndarray, valid: np.ndarray | None = None):
    """Run ``pred`` over ``rgb`` in overlapping ``pred.tile`` tiles (stride tile/2, blended with a
    triangular window). Returns ``(agl (H, W) float32, leans)``; ``leans``: ``[(cx, cy, angle,
    scale)]`` per tile centre in the image's pixels. Tiles with no valid pixel are skipped."""
    h, w = rgb.shape[:2]
    T = pred.tile
    ph, pw = max(h, T), max(w, T)
    img = np.zeros((ph, pw, 3), np.uint8)
    img[:h, :w] = rgb
    i = np.arange(T)
    ramp = np.minimum(1.0, np.minimum(i + 1, T - i) / (T / 4))
    win = np.outer(ramp, ramp).astype(np.float32)
    acc = np.zeros((ph, pw), np.float32)
    wsum = np.zeros((ph, pw), np.float32)
    leans = []
    for y0 in _starts(ph, T, T // 2):
        for x0 in _starts(pw, T, T // 2):
            if valid is not None and not valid[y0:y0 + T, x0:x0 + T].any():
                continue
            agl, lean = pred.predict(img[y0:y0 + T, x0:x0 + T])
            acc[y0:y0 + T, x0:x0 + T] += agl * win
            wsum[y0:y0 + T, x0:x0 + T] += win
            if lean is not None:
                leans.append((x0 + T / 2, y0 + T / 2, *lean))
    out = np.full((ph, pw), np.nan, np.float32)
    np.divide(acc, wsum, out=out, where=wsum > 0)
    return out[:h, :w], leans


# ------------------------------------------------------------------ footprints

def _lat_of_global_px(y: float, z: int = Z) -> float:
    return math.degrees(math.atan(math.sinh(math.pi - 2 * math.pi * y / (256 * 2 ** z))))


def lean_from_model(angle: float, scale: float, pixel_m: float) -> tuple[float, float]:
    """Lean (metres east, metres south per metre of height) from the pubgeo model's image-level
    angle (rad) and scale (its pixels per metre of height), ``pixel_m`` metres per fed pixel.
    The model's vector is ``scale x (sin a, cos a)`` in image pixels (x right, y down), times
    :data:`LEAN_SIGN`."""
    return (LEAN_SIGN[0] * scale * math.sin(angle) * pixel_m,
            LEAN_SIGN[1] * scale * math.cos(angle) * pixel_m)


def _read_footprint(agl, rings, lean_px, max_h, step):
    """``(h, n_px, score)``: the height where the median AGL under the footprint shifted by
    ``h x lean_px`` equals ``h`` (smallest ``|f - h| / (h + 5)``, ties to the larger h)."""
    import cv2  # noqa: PLC0415

    H, W = agl.shape
    allp = np.vstack(rings)
    x0, y0 = np.floor(allp.min(0)).astype(int) - 1
    x1, y1 = np.ceil(allp.max(0)).astype(int) + 2
    m = np.zeros((y1 - y0, x1 - x0), np.uint8)
    for r in rings:
        cv2.fillPoly(m, [np.round(r - (x0, y0)).astype(np.int32)], 1)
    ys, xs = np.nonzero(m)
    ys, xs = ys + y0, xs + x0
    if ys.size == 0:
        return None
    best = None
    for h in np.arange(0.0, max_h + 1e-6, step):
        yy = np.round(ys + h * lean_px[1]).astype(int)
        xx = np.round(xs + h * lean_px[0]).astype(int)
        ok = (yy >= 0) & (yy < H) & (xx >= 0) & (xx < W)
        if ok.sum() < max(4, 0.3 * ys.size):
            continue
        v = agl[yy[ok], xx[ok]]
        v = v[np.isfinite(v)]
        if v.size < max(4, 0.3 * ys.size):
            continue
        f = float(np.median(v))
        score = abs(f - h) / (h + 5.0)
        if best is None or score <= best[2] + 1e-9:
            best = (f, int(v.size), score)
    return best


def heights_for_footprints(src, footprints, *, predictor=None, backend: str | None = None,
                           device: str | None = None, lean_m=None, reg_m=(0.0, 0.0),
                           gsd_m: float | None = None, margin_m: float = 100.0,
                           max_h: float = 260.0, step: float = 4.0) -> dict:
    """Geopose height of each footprint on one scene.

    ``src``: ``scene.TileSource`` (z18 tiles). ``footprints``: dicts with ``fid``, ``P`` (list of
    global-pixel rings, OSM position) and ``bb`` (``footprints_from_records`` gives them).
    ``predictor``: a loaded one (else :func:`load_predictor` of ``backend``, else the first
    available). ``lean_m``: ``(east, south)`` metres of roof shift per metre of height to use
    instead of the model's (needed for AGL-only backends to read towers; e.g. ``Scene.lean``);
    ``reg_m``: OSM -> scene registration, metres (east, south). ``gsd_m``: metres per pixel to
    feed the model (default the backend's).

    Returns ``{fid: {"h_m", "lean_dx", "lean_dy", "backend", "n_px", "consistent"}}``; ``lean_*``
    metres (east, south) of roof shift per metre of height (None for an AGL-only backend without
    ``lean_m``); ``consistent``: the footprint's median under the shifted outline agreed with the
    height within 35 % (False: the roof was not found at the lean; treat the height as weak).
    Footprints with no usable pixels are absent. Frees the GPU cache when it loaded the model.
    """
    from .scene import mpp  # noqa: PLC0415

    fps = [f for f in footprints if f.get("P") and f.get("bb")]
    if not fps:
        return {}
    own = predictor is None
    if own:
        predictor = load_predictor(backend, device) if backend else first_predictor(device)
    try:
        bb = np.array([f["bb"] for f in fps], float)
        M = mpp(_lat_of_global_px(0.5 * (bb[:, 1].min() + bb[:, 3].max())))
        pad = margin_m / M
        gx0, gy0 = int(bb[:, 0].min() - pad), int(bb[:, 1].min() - pad)
        gx1, gy1 = int(bb[:, 2].max() + pad) + 1, int(bb[:, 3].max() + pad) + 1
        rgb, have = src.crop(gx0, gy0, gx1, gy1, rgb=True)
        r = M / (gsd_m or predictor.gsd_m)
        if abs(r - 1) > 0.02:
            import cv2  # noqa: PLC0415

            sz = (max(1, round(rgb.shape[1] * r)), max(1, round(rgb.shape[0] * r)))
            rgb = cv2.resize(rgb, sz, interpolation=cv2.INTER_CUBIC if r > 1 else cv2.INTER_AREA)
            have = cv2.resize(have.astype(np.uint8), sz, interpolation=cv2.INTER_NEAREST) > 0
        pixel_m = M / r
        agl, leans = predict_tiled(predictor, rgb, have)
        agl[~have] = np.nan
    finally:
        if own and getattr(predictor, "device", "") == "cuda":
            from city2stl.resources import free_gpu_cache  # noqa: PLC0415

            free_gpu_cache()

    out = {}
    reg_px = np.array(reg_m, float) / pixel_m
    for f in fps:
        c = np.array([(f["bb"][0] + f["bb"][2]) / 2 - gx0, (f["bb"][1] + f["bb"][3]) / 2 - gy0]) * r
        if lean_m is not None:
            lm = (float(lean_m[0]), float(lean_m[1]))
        elif leans:
            t = min(leans, key=lambda L: (L[0] - c[0]) ** 2 + (L[1] - c[1]) ** 2)
            lm = lean_from_model(t[2], t[3], pixel_m)
        else:
            lm = None
        lean_px = np.array(lm) / pixel_m if lm else np.zeros(2)
        rings = [(np.asarray(p, float) - (gx0, gy0)) * r + reg_px for p in f["P"]]
        got = _read_footprint(agl, rings, lean_px, max_h if lm else 0.0, step)
        if got is None:
            continue
        h, n, score = got
        out[f["fid"]] = dict(h_m=round(h, 1), lean_dx=None if lm is None else round(lm[0], 4),
                             lean_dy=None if lm is None else round(lm[1], 4),
                             backend=predictor.name, n_px=n,
                             consistent=bool(score <= 0.35 or not lm))
    return out


# ------------------------------------------------------------------ readings

def to_reading(res: dict, scene: str | None = None):
    """The ``geopose`` ``SatReading`` of one :func:`heights_for_footprints` result (conf
    :data:`GEOPOSE_CONF`; backend and lean in ``extra``)."""
    from .readings import SatReading  # noqa: PLC0415

    extra = {"backend": res["backend"], "consistent": bool(res.get("consistent", True))}
    if res.get("lean_dx") is not None:
        extra.update(lean_dx=res["lean_dx"], lean_dy=res["lean_dy"])
    return SatReading("geopose", float(res["h_m"]), GEOPOSE_CONF, scene=scene, extra=extra)


def tall_hints(results: dict) -> dict:
    """``{fid: h_m}`` for ``measure.search_cap``'s ``f["geopose"]`` field."""
    return {fid: r["h_m"] for fid, r in results.items()}


__all__ = ["BACKENDS", "SPECS", "Backend", "BackendUnavailable", "GpuUnavailable", "TorchPredictor",
           "available_backends", "load_predictor", "first_predictor", "predict_tiled",
           "heights_for_footprints", "lean_from_model", "to_reading", "tall_hints", "model_root",
           "GEOPOSE_CONF", "LEAN_SIGN"]
