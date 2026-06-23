"""skyline._core.segmentation — extracted from pipeline.py (A1 split)."""
from __future__ import annotations
from collections import OrderedDict as _OrderedDict

import logging
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

_ADE20K_SKY: int = 2

_ADE20K_BUILDING_CLASSES: tuple[int, ...] = (1, 25, 48)

_ADE20K_BUILDING: int = _ADE20K_BUILDING_CLASSES[0]

_ADE20K_WATER_CLASSES: tuple[int, ...] = (21, 26, 60)

_ADE20K_VEGETATION_CLASSES: tuple[int, ...] = (4, 9, 17, 29)

def _segformer_model_id() -> str:
    """Resolve the SegFormer model ID at first inference.

    Default is ``b3`` (flipped from ``b0`` 2026-05-16 after measurement —
    on Cartagena b3 doubled the matched-tagged-building count (n=8 → 17)
    and dropped MAE 22.13 → 13.73, with cross-seed bias collapsing from
    +20.30 m to +0.87 m). Set env var ``SKYLINE_CV_SEGFORMER_SIZE`` to
    any of ``b0`` … ``b5`` to override. ``b0`` is still useful for fast
    iteration (~3 min vs b3's ~5–6 min on Cartagena). Larger variants
    improve building/sky mask quality on reflective-glass spires and
    shadowed tower tops that smaller models classify as sky. The first
    call downloads the weights to HuggingFace's local cache; subsequent
    runs reuse them.
    """
    size = os.environ.get("SKYLINE_CV_SEGFORMER_SIZE", "b3").strip().lower()
    if size not in {"b0", "b1", "b2", "b3", "b4", "b5"}:
        size = "b3"
    return f"nvidia/segformer-{size}-finetuned-ade-512-512"

def _segformer_input_size() -> int:
    """Resolve the SegFormer processor input resolution (square).

    Inference cost on a transformer scales roughly with the *pixel count*
    of the input, so dropping from 512² to 384² is ~44% fewer pixels and
    ~1.8× speedup; 320² is ~61% fewer pixels and ~2.5× speedup. The
    accuracy floor is the inter-tower gap width: at 320 px, a 3-pixel-wide
    sky strip between two close towers becomes ~2 px and may merge.
    Default 512 (the size the model was finetuned at). Override with
    ``SKYLINE_CV_SEGFORMER_INPUT_SIZE`` (integer ≥ 128).
    """
    raw = os.environ.get("SKYLINE_CV_SEGFORMER_INPUT_SIZE", "512").strip()
    try:
        n = int(raw)
    except ValueError:
        return 512
    return max(128, n)

_SEGFORMER_MODEL_ID: str = _segformer_model_id()

_SEGFORMER_LOADED: bool = False

_SEGFORMER_OK: bool = False

_segformer_processor = None

_segformer_model = None

_NEURAL_CACHE_CAPACITY = 64

_neural_cache: "_OrderedDict[int, dict]" = _OrderedDict()

_MOBILESAM_LOADED: bool = False

_MOBILESAM_OK: bool = False

_mobilesam_predictor = None

_segformer_device: str = "cpu"

def _ensure_segformer() -> bool:
    """Lazily load SegFormer-b0 (ADE20K). Returns True if the model is ready.

    Moves the model to CUDA when available (single forward pass is dominated
    by GPU transfer + transformer math; on Cartagena we measured ~2 s/image
    on CPU vs ~0.2-0.4 s/image on a consumer GPU). Override the device with
    the ``SKYLINE_CV_SEGFORMER_DEVICE`` env var (e.g. ``cpu`` to force CPU
    on a machine with a busy GPU).
    """
    global _SEGFORMER_LOADED, _SEGFORMER_OK, _segformer_processor, _segformer_model, _segformer_device
    if _SEGFORMER_LOADED:
        return _SEGFORMER_OK
    _SEGFORMER_LOADED = True
    try:
        import torch  # noqa: PLC0415
        from transformers import (  # noqa: PLC0415
            SegformerForSemanticSegmentation,
            SegformerImageProcessor,
        )
        _segformer_processor = SegformerImageProcessor.from_pretrained(
            _SEGFORMER_MODEL_ID)
        _segformer_model = SegformerForSemanticSegmentation.from_pretrained(
            _SEGFORMER_MODEL_ID)
        _segformer_model.eval()  # type: ignore[union-attr]
        # Override the processor's internal resize so a smaller input
        # square reduces the per-forward-pass cost. We touch both the
        # ``size`` dict (canonical for new transformers) AND ``do_resize``
        # so the processor actually performs the resize before it hands
        # tensors to the model.
        _input_n = _segformer_input_size()
        try:
            _segformer_processor.size = {"height": _input_n, "width": _input_n}
            _segformer_processor.do_resize = True
        except Exception as _e_size:
            print(f"[segformer] failed to set input size to {_input_n}: {_e_size}")
        # Device selection: respect the override env var, otherwise pick CUDA
        # if it's available, falling back to CPU.
        override = os.environ.get("SKYLINE_CV_SEGFORMER_DEVICE", "").strip().lower()
        if override:
            _segformer_device = override
        elif torch.cuda.is_available():
            _segformer_device = "cuda"
        else:
            _segformer_device = "cpu"
        try:
            _segformer_model.to(_segformer_device)  # type: ignore[union-attr]
            print(f"[segformer] device={_segformer_device}  model={_SEGFORMER_MODEL_ID.split('/')[-1]}  input={_input_n}px")
        except Exception as _e_dev:
            print(f"[segformer] failed to move to {_segformer_device}: {_e_dev} — falling back to CPU")
            _segformer_device = "cpu"
            _segformer_model.to("cpu")  # type: ignore[union-attr]
        _SEGFORMER_OK = True
    except Exception:
        _SEGFORMER_OK = False
    return _SEGFORMER_OK

def _ensure_mobilesam() -> bool:
    """Lazily load MobileSAM vit_t. Returns True if the predictor is ready."""
    global _MOBILESAM_LOADED, _MOBILESAM_OK, _mobilesam_predictor
    if _MOBILESAM_LOADED:
        return _MOBILESAM_OK
    _MOBILESAM_LOADED = True
    ckpt = os.environ.get(
        "MOBILESAM_CHECKPOINT_PATH",
        str(Path.home() / ".cache" / "mobile_sam" / "vit_t.pth"),
    )
    try:
        from mobile_sam import SamPredictor, sam_model_registry  # noqa: PLC0415
        if not Path(ckpt).is_file():
            return False
        model = sam_model_registry["vit_t"](checkpoint=ckpt)
        model.eval()
        _mobilesam_predictor = SamPredictor(model)
        _MOBILESAM_OK = True
    except Exception:
        _MOBILESAM_OK = False
    return _MOBILESAM_OK

def _mobilesam_available() -> bool:
    """Return True without triggering a load (for quick gate checks)."""
    if _MOBILESAM_LOADED:
        return _MOBILESAM_OK
    return _ensure_mobilesam()

def _neural_cache_put(img_id: int, entry: dict, image_rgb: np.ndarray) -> None:
    """LRU put with bounded capacity.

    CRITICAL: stores a strong reference to ``image_rgb`` inside ``entry``
    (key ``_anchor``). This prevents Python's garbage collector from freeing
    the image array while its id() is still in the cache. Without this
    anchor, freed memory addresses get reused for subsequent images and
    produce SILENT false cache hits — returning a different image's masks.
    The pre-LRU 1-slot cache was incidentally safe because it always held
    just one entry; the LRU's stale-id window made the bug observable.
    """
    entry = dict(entry)
    entry["_anchor"] = image_rgb
    if img_id in _neural_cache:
        _neural_cache.move_to_end(img_id)
        _neural_cache[img_id] = entry
        return
    _neural_cache[img_id] = entry
    while len(_neural_cache) > _NEURAL_CACHE_CAPACITY:
        _neural_cache.popitem(last=False)

def _ensure_label_map(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Run SegFormer-b0 (ADE20K) and return the per-pixel argmax label map.

    Cached by id(image_rgb). All higher-level mask getters
    (``_neural_sky_and_building_masks``, ``_neural_water_mask``,
    ``_neural_vegetation_mask``) share this single forward pass — once the
    label map is cached, deriving any class mask is a ~1 ms boolean op.

    Returns None if the model is unavailable or inference fails. Cache
    entries store None for the failure case so we don't retry forever.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if entry is not None and entry.get("_anchor") is image_rgb:
        _neural_cache.move_to_end(img_id)
        return entry.get("label_map")

    if not _ensure_segformer():
        _neural_cache_put(img_id, {"label_map": None}, image_rgb)
        return None

    try:
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415

        h, w = image_rgb.shape[:2]
        pil = PILImage.fromarray(image_rgb)
        inputs = _segformer_processor(
            images=pil, return_tensors="pt")  # type: ignore[misc]
        # Move inputs to the model's device (CUDA when available).
        if _segformer_device != "cpu":
            inputs = {k: v.to(_segformer_device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = _segformer_model(**inputs)  # type: ignore[misc]
        # logits: (1, num_classes, H/4, W/4) → upsample to original resolution
        upsampled = F.interpolate(
            outputs.logits, size=(h, w), mode="bilinear", align_corners=False
        )
        # argmax on-device, then transfer the small int64 (H, W) result to CPU
        # for the downstream numpy work — cheaper than moving the full logits.
        label_map = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
        _neural_cache_put(img_id, {"label_map": label_map}, image_rgb)
        return label_map
    except Exception:
        _neural_cache_put(img_id, {"label_map": None}, image_rgb)
        return None

def _segformer_batch_size() -> int:
    """Resolve the prefetch forward-pass batch size (images per call).

    Batching the spin views into a single forward pass amortizes Python
    dispatch and (on GPU) kernel-launch overhead — the dominant per-image
    cost on the 12-view spin once the model itself is warm. Bounded so peak
    activation memory stays modest on CPU-only machines. Override with
    ``SKYLINE_CV_SEGFORMER_BATCH`` (integer ≥ 1; 1 disables batching).
    """
    raw = os.environ.get("SKYLINE_CV_SEGFORMER_BATCH", "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12

def prefetch_label_maps(images: "list[np.ndarray]") -> int:
    """Run SegFormer once on a *batch* of images, populating the neural cache.

    Every higher-level mask getter (``_ensure_label_map`` and the sky /
    building / water / vegetation masks built on it) is keyed by
    ``id(image_rgb)`` in ``_neural_cache``. Pre-running the whole spin as one
    (or a few) batched forward pass(es) means each later per-view call is a
    cache hit instead of its own forward pass — identical numerics, but the
    transformer's fixed per-call overhead is paid once for the batch rather
    than 12×.

    Images already cached (by id + anchor identity) are skipped, so this is
    idempotent and safe to call from multiple phases on overlapping image
    sets. Returns the number of images actually pushed through the model.
    A no-op returning 0 when the model is unavailable or the batch is empty;
    callers then fall back transparently to lazy per-image inference.
    """
    if not images:
        return 0
    # Filter to images not already cached (same id AND same array object, to
    # respect the anti-GC-reuse anchor invariant in _neural_cache_put).
    pending: list[np.ndarray] = []
    seen_ids: set[int] = set()
    for img in images:
        if img is None:
            continue
        img_id = id(img)
        if img_id in seen_ids:
            continue
        seen_ids.add(img_id)
        entry = _neural_cache.get(img_id)
        if (entry is not None and entry.get("_anchor") is img
                and "label_map" in entry):
            continue
        pending.append(img)
    if not pending:
        return 0
    if not _ensure_segformer():
        return 0

    try:
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415

        batch_n = _segformer_batch_size()
        ran = 0
        for start in range(0, len(pending), batch_n):
            chunk = pending[start:start + batch_n]
            pil_batch = [PILImage.fromarray(img) for img in chunk]
            inputs = _segformer_processor(  # type: ignore[misc]
                images=pil_batch, return_tensors="pt")
            if _segformer_device != "cpu":
                inputs = {k: v.to(_segformer_device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = _segformer_model(**inputs)  # type: ignore[misc]
            # logits: (N, num_classes, H/4, W/4). Upsample + argmax per image
            # at its own native resolution (the spin views are uniform size,
            # but this stays correct if a caller mixes sizes) — and avoids
            # one giant (N, C, H, W) full-res tensor.
            logits = outputs.logits
            for i, img in enumerate(chunk):
                h, w = img.shape[:2]
                upsampled = F.interpolate(
                    logits[i:i + 1], size=(h, w),
                    mode="bilinear", align_corners=False)
                label_map = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
                _neural_cache_put(id(img), {"label_map": label_map}, img)
                ran += 1
        return ran
    except Exception:
        # Leave the cache untouched; lazy per-image inference still works.
        return 0

def _neural_sky_and_building_masks(
    image_rgb: np.ndarray,
) -> tuple["np.ndarray | None", "np.ndarray | None"]:
    """Return cleaned-up boolean (sky_mask, building_mask) — morphology applied.

    The SegFormer forward pass is shared with the water/vegetation getters
    via ``_ensure_label_map``. This function adds the morphological cleanup
    (~50 ms/image) that the silhouette/registration code depends on:
    speckle removal, glass-tower top repair, and sky/building mutual
    exclusion. Callers who only need water or vegetation masks should use
    their respective helpers — those skip the morphology entirely.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if (entry is not None and entry.get("_anchor") is image_rgb
            and "sky" in entry and "building" in entry):
        _neural_cache.move_to_end(img_id)
        return entry["sky"], entry["building"]

    label_map = _ensure_label_map(image_rgb)
    if label_map is None:
        entry = _neural_cache.get(img_id)
        if entry is not None:
            entry["sky"] = None
            entry["building"] = None
        return None, None

    sky_mask = label_map == _ADE20K_SKY
    # Union all building-family classes (building, house, skyscraper) so a
    # dark-glass tower SegFormer prefers to label "skyscraper" still
    # contributes to the building mask the silhouette splitter sees.
    building_mask = np.isin(label_map, _ADE20K_BUILDING_CLASSES)
    # Mask cleanup: SegFormer's per-pixel argmax produces speckle —
    # small isolated sky pixels INSIDE a building (window reflections
    # of the sky on glass), and small isolated building pixels
    # OUTSIDE buildings (cloud edges, antenna tips). A 5×5 closing on
    # the building mask fills sub-window holes; a 3×3 opening on the
    # sky mask removes isolated-sky speckle. Then we reassert
    # mutual-exclusion (a pixel can't be both sky AND building post-
    # cleanup) by giving sky precedence near the top of the image
    # and building precedence below — that mirrors the physical
    # likelihood at each y. Net effect on Cartagena: the central
    # tower cluster's mask blob acquires sharper inter-building
    # gaps that F-SKY7's local-max peak detector can exploit.
    b_u8 = (building_mask.astype(np.uint8)) * 255
    s_u8 = (sky_mask.astype(np.uint8)) * 255
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    b_u8 = cv2.morphologyEx(b_u8, cv2.MORPH_CLOSE, close_k)
    s_u8 = cv2.morphologyEx(s_u8, cv2.MORPH_OPEN, open_k)
    # Glass-tower top repair: a tall narrow vertical closing (1 col ×
    # 11 rows) bridges short sky strips that mirrored-sky reflections
    # carve into a glass facade — the canonical Cartagena Bocagrande
    # failure where the mask top has a wavy edge a row of grey-blue
    # pixels below the actual roofline. Vertical-only kernel preserves
    # the HORIZONTAL inter-tower gaps that F-SKY2 splitting depends
    # on (a 5×5 isotropic closing would erase those too). Capped at
    # 11 px so windows-and-cornice gaps two storeys tall don't get
    # mistakenly filled in.
    vert_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
    b_u8 = cv2.morphologyEx(b_u8, cv2.MORPH_CLOSE, vert_k)
    # Glass-tower hole-fill: any non-building region NOT reachable from
    # the image border (i.e. fully enclosed by building) is almost
    # certainly a mis-classified dark glass facade. Flood-fill the
    # complement from (0,0); cells still untouched after the fill are
    # interior holes worth promoting to building.
    inv = (b_u8 == 0).astype(np.uint8)
    h_full, w_full = inv.shape[:2]
    ff_mask = np.zeros((h_full + 2, w_full + 2), dtype=np.uint8)
    flood = inv.copy()
    cv2.floodFill(flood, ff_mask, (0, 0), 2)
    interior_holes = (flood == 1)
    if interior_holes.any():
        b_u8[interior_holes] = 255
    # Beach/sand/earth-as-building cap (Cartagena coast): SegFormer-b1
    # routinely labels coastal sand strips as "building" (class 1), which
    # the hole-fill above then fuses with real tower bases — producing
    # per-view bboxes whose base lands at the waterline instead of the
    # actual building base. Two complementary clips run here:
    #
    #   A. Hard ground cap: in any column where SegFormer DID correctly
    #      label water/earth/sand at row Y, building above row Y is
    #      kept but anything at or below Y is zeroed.
    #
    #   B. Beach-band heuristic: in columns where the model is fooling
    #      itself (no ground class anywhere — just one tall "building"
    #      strip running into the waterline), use the column's water
    #      neighbours to estimate a "ground row" and clip the building
    #      mask at ~0.08 * H above the waterline. This catches the
    #      common case where the model labels the entire beach as
    #      building class 1 with no internal sand pixels.
    # Class-membership ground cap: in any column where SegFormer
    # explicitly labelled a non-building "ground" class (water, sea,
    # river, earth, sand) at row Y, clip the building mask at/below Y.
    # Catches the failure mode where the hole-fill fuses real towers
    # to a correctly-labelled patch of sand below them. Does NOT clip
    # at columns where the model itself mis-labels the beach as
    # building (that overcorrected on run #22 and cost legitimate
    # low-tower matches); the unconditional waterline band was
    # reverted in favour of trusting the model's own ground labels
    # where it provides them.
    # Ground = water + earth + sand (ADE20K 21/26/60 + 13/46). All three
    # are surfaces a building cannot occupy, so the foreground ground
    # strip below a building base (beach/street/sand) must be clipped or
    # SegFormer's beach-as-building mislabels leak the mask down into the
    # ground (over-segmentation). The KEY is finding the groundline
    # BOTTOM-UP, not via a topmost-pixel argmax:
    #   * Bottom-up: the far bay water / distant sand visible at the
    #     horizon ABOVE the foreground building bases is NOT the bottom-
    #     most ground run, so it can't chop a building standing in front
    #     of open bay (the seed_5 peninsula failure that the old argmax
    #     cap caused).
    #   * Including sand/earth: the real foreground beach/street below a
    #     building base joins the bottom water run, so the clip lands at
    #     the building base instead of leaving sand-as-building blobs
    #     hanging down to the waterline (the over-segmentation a water-
    #     only cap produced).
    # A building whose own lower facade fades into mislabelled sand loses
    # at most a few base rows — the lesser evil vs. leaking into ground.
    _GROUND_CLASSES = _ADE20K_WATER_CLASSES + (13, 46)  # + earth, sand
    ground_mask_local = np.isin(label_map, _GROUND_CLASSES)
    if ground_mask_local.any():
        # Groundline = top of the FOREGROUND ground block, found bottom-up
        # per column. We locate the bottom-most contiguous ground run in
        # each column and clip only at/below its top. Ground above the
        # buildings (horizon water/sand) does not participate.
        gm = ground_mask_local
        rev = gm[::-1, :]  # row 0 = image bottom
        has_ground = rev.any(axis=0)
        # Bottom-most ground pixel (index in reversed coords).
        first_ground_rev = rev.argmax(axis=0)
        # First NON-ground row at/above that pixel (still reversed): the
        # top of the foreground ground run.
        row_idx_rev = np.arange(h_full)[:, None]
        non_ground_rev = (~rev) & (row_idx_rev >= first_ground_rev[None, :])
        has_break = non_ground_rev.any(axis=0)
        first_break_rev = np.where(
            has_break, non_ground_rev.argmax(axis=0), h_full)
        # Convert the run-top from reversed to original row coords.
        waterline_per_col = (h_full - first_break_rev).astype(np.int32)
        # Columns with no ground at all: cap below the image (no-op).
        waterline_per_col = np.where(
            has_ground, waterline_per_col, h_full).astype(np.int32)
        row_idx = np.arange(h_full)[:, None]
        margin_px = 4
        below_ground = row_idx >= (waterline_per_col[None, :] - margin_px)
        b_u8[below_ground] = 0
    building_mask = b_u8.astype(bool)
    # Full-height-column filter: a column where (nearly) EVERY row is
    # building is physically impossible — a real building never spans
    # sky-to-ground across the whole frame. It's a SegFormer/stitch
    # artifact (e.g. a dark seam or a mislabelled vertical strip, seen on
    # Cartagena auto_180_2000m). Zero any column whose building coverage
    # exceeds ``full_col_frac`` of the frame height.
    if building_mask.any():
        h_bm = building_mask.shape[0]
        col_cov = building_mask.sum(axis=0)
        full_cols = col_cov >= int(0.97 * h_bm)
        if full_cols.any():
            building_mask[:, full_cols] = False
    # Vegetation subtraction: SegFormer's per-pixel argmax already keeps
    # tree/building pixels disjoint, BUT the building closing above dilates
    # the mask outward — sometimes INTO an adjacent tree canopy. Re-remove
    # any vegetation-class pixel the closing absorbed so tower silhouettes
    # don't grow phantom green-canopy shoulders. Cheap (one isin + and).
    veg = np.isin(label_map, _ADE20K_VEGETATION_CLASSES)
    if veg.any():
        building_mask &= ~veg
    sky_mask = s_u8.astype(bool)
    # Pixels claimed by both: building wins (sky-on-glass-reflection
    # is the dominant failure case; cyan-tower-edge cloud is rare).
    sky_mask &= ~building_mask
    entry = _neural_cache.get(img_id)
    if entry is not None:
        entry["sky"] = sky_mask
        entry["building"] = building_mask
    return sky_mask, building_mask

def _neural_water_mask(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Return the cached water-class boolean mask for this image.

    Shares the SegFormer forward pass with ``_neural_sky_and_building_masks``
    via the cached label_map but skips the (~50 ms/image) morphology pass —
    water consumers don't need it. Subsequent calls within the cache window
    are O(1).
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if (entry is not None and entry.get("_anchor") is image_rgb
            and "water" in entry):
        _neural_cache.move_to_end(img_id)
        return entry["water"]

    label_map = _ensure_label_map(image_rgb)
    entry = _neural_cache.get(img_id)
    if label_map is None or entry is None:
        if entry is not None:
            entry["water"] = None
        return None
    water_mask = np.isin(label_map, _ADE20K_WATER_CLASSES)
    entry["water"] = water_mask
    return water_mask

def _neural_vegetation_mask(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Return the cached vegetation-class boolean mask (F-SKY18).

    Shares the SegFormer forward pass via the cached label_map; skips
    morphology like ``_neural_water_mask``. Consumers only use per-column
    extents, where speckle is averaged out.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if (entry is not None and entry.get("_anchor") is image_rgb
            and "vegetation" in entry):
        _neural_cache.move_to_end(img_id)
        return entry["vegetation"]

    label_map = _ensure_label_map(image_rgb)
    entry = _neural_cache.get(img_id)
    if label_map is None or entry is None:
        if entry is not None:
            entry["vegetation"] = None
        return None
    vegetation_mask = np.isin(label_map, _ADE20K_VEGETATION_CLASSES)
    entry["vegetation"] = vegetation_mask
    return vegetation_mask


def _get_mobilesam_predictor():
    """Live accessor for the lazily-loaded MobileSAM predictor (cross-module state)."""
    return _mobilesam_predictor



__all__ = [
    '_segformer_model_id',
    '_segformer_input_size',
    '_ensure_segformer',
    '_ensure_mobilesam',
    '_mobilesam_available',
    '_neural_cache_put',
    '_ensure_label_map',
    '_segformer_batch_size',
    'prefetch_label_maps',
    '_neural_sky_and_building_masks',
    '_neural_water_mask',
    '_neural_vegetation_mask',
    '_get_mobilesam_predictor',
]
