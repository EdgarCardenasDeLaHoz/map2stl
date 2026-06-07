"""
city2stl/height/predict -- CNN-based building height prediction.

Phase 2 of 3D_plan1.md.

Two modes:
  "pretrained"  -- Depth Anything V2 (small, ~300 MB).  Zero-shot monocular depth
                   is calibrated to absolute metres using known OSM heights in the
                   same tile via a simple linear regression.

  "unet"        -- Custom U-Net with EfficientNet-B4 encoder trained on paired
                   (satellite-RGB, height-raster) tiles collected from Phase 1
                   ground truth.  Requires a saved checkpoint produced by
                   city2stl/height/train.py.

Both paths share the same public interface:
  predict(sat_rgb, known_heights, bbox, ...)  ->  HeightResult

Heavy dependencies (torch, timm, transformers) are lazy-imported so the server
starts without them.  Only the inference call triggers the import.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from city2stl.skyline_cv.height import BBox, HeightResult

logger = logging.getLogger(__name__)

# Default checkpoint paths -- override by passing checkpoint= to predict()
_MODELS_DIR = Path(__file__).resolve().parents[3] / "models"
_UNET_DEFAULT_CKPT = _MODELS_DIR / "height_unet.pt"
_DA2_CACHE_DIR = _MODELS_DIR / "depth_anything_v2"

# ------------------------------------------------------------------------------
# Depth Anything V2 helpers
# ------------------------------------------------------------------------------

_da2_model = None  # module-level singleton after first load


def _load_da2(device: str = "cpu"):
    """Load Depth Anything V2 Small.  Cached after first call."""
    global _da2_model
    if _da2_model is not None:
        return _da2_model

    try:
        from transformers import pipeline as hf_pipeline
    except ImportError as exc:
        raise ImportError(
            "transformers is required for model='pretrained'.  "
            "Install with: pip install transformers"
        ) from exc

    # F-SKY24 depth experiment: env var ``SKYLINE_CV_DA2_VARIANT``
    # selects Small / Base / Large. Default Small — empirical test on
    # Miami showed Base produces visually identical output at ~3x CPU
    # cost (Method 2 finding). Larger variants kept as opt-in for users
    # with GPU budget who want to retest the comparison.
    import os as _os  # noqa: PLC0415
    variant = _os.environ.get(
        "SKYLINE_CV_DA2_VARIANT", "Small").strip()
    if variant not in ("Small", "Base", "Large"):
        variant = "Small"
    model_id = f"depth-anything/Depth-Anything-V2-{variant}-hf"
    logger.info("Loading Depth Anything V2 %s from HuggingFace...", variant)
    _da2_model = hf_pipeline(
        task="depth-estimation",
        model=model_id,
        device=device,
        cache_dir=str(_DA2_CACHE_DIR),
    )
    logger.info("Depth Anything V2 %s loaded", variant)
    return _da2_model


def _depth_anything_inference(
    sat_rgb: np.ndarray,
    device: str = "cpu",
) -> np.ndarray:
    """Run Depth Anything V2 on *sat_rgb* and return a (H, W) relative-depth map.

    Parameters
    ----------
    sat_rgb : (H, W, 3) uint8 RGB array.

    Returns
    -------
    depth_rel : (H, W) float32.  Values are relative -- not yet in metres.
                Higher values = further from the camera (farther below).
    """
    from PIL import Image as PILImage

    pipe = _load_da2(device)
    pil_img = PILImage.fromarray(sat_rgb)
    result = pipe(pil_img)
    # HuggingFace depth-estimation pipeline returns {"predicted_depth": tensor, "depth": PIL}
    # The "predicted_depth" tensor has shape (H, W) in the native coordinate of the model.
    depth_tensor = result["predicted_depth"]
    try:
        depth_np = depth_tensor.squeeze().numpy()
    except AttributeError:
        # already numpy
        depth_np = np.array(depth_tensor).squeeze()

    # Resize back to input resolution if needed
    if depth_np.shape != sat_rgb.shape[:2]:
        try:
            import cv2
            depth_np = cv2.resize(
                depth_np.astype(np.float32),
                (sat_rgb.shape[1], sat_rgb.shape[0]),
                interpolation=cv2.INTER_LINEAR,
            )
        except ImportError:
            from PIL import Image as _P
            depth_np = np.array(
                _P.fromarray(depth_np.astype(np.float32)).resize(
                    (sat_rgb.shape[1], sat_rgb.shape[0]), _P.BILINEAR
                )
            )

    # Invert: Depth Anything outputs inverse depth (closer = higher).
    # We want building heights = closer to camera = *higher*, so invert again.
    # Normalise to [0, 1] before calibration.
    d = depth_np.astype(np.float32)
    d_min, d_max = float(d.min()), float(d.max())
    if d_max > d_min:
        d = (d - d_min) / (d_max - d_min)
    return d


def _calibrate_depth(
    depth_rel: np.ndarray,
    known_heights: np.ndarray,
    bbox: BBox,
) -> Tuple[np.ndarray, float, float]:
    """Fit a linear mapping depth_rel -> metres using *known_heights* samples.

    Parameters
    ----------
    depth_rel    : (H, W) float32 normalised relative depth [0, 1].
    known_heights: (H, W) float32 building heights in metres.  NaN = unknown.
    bbox         : geographic extent (north, south, east, west) -- unused here but
                   available for future spatially-aware calibration.

    Returns
    -------
    height_abs   : (H, W) float32 absolute height in metres.
    slope, intercept : fitted linear parameters.
    """
    valid = ~np.isnan(known_heights)
    n_valid = int(valid.sum())
    if n_valid < 10:
        logger.warning(
            "Only %d known-height pixels for calibration - falling back to "
            "heuristic scale (depth_rel * 30 m)",
            n_valid,
        )
        return depth_rel * 30.0, 30.0, 0.0

    x = depth_rel[valid].ravel()
    y = known_heights[valid].ravel()

    # Ordinary least-squares: y = slope * x + intercept
    A = np.column_stack([x, np.ones_like(x)])
    result, _, _, _ = np.linalg.lstsq(A, y, rcond=None)
    slope, intercept = float(result[0]), float(result[1])

    height_abs = (depth_rel * slope + intercept).astype(np.float32)
    # Clamp to physical range
    height_abs = np.clip(height_abs, 0.0, 500.0)

    logger.info(
        "Calibration: slope=%.3f  intercept=%.3f  n=%d  "
        "predicted range=[%.1f, %.1f] m",
        slope, intercept, n_valid,
        float(height_abs.min()), float(height_abs.max()),
    )
    return height_abs, slope, intercept


# ------------------------------------------------------------------------------
# U-Net helpers
# ------------------------------------------------------------------------------

def _build_unet(pretrained_encoder: bool = False) -> "torch.nn.Module":
    """Build a U-Net with EfficientNet-B4 encoder.

    Returns an ``nn.Module`` whose forward() accepts an (N, 3, H, W) float32
    tensor in [0, 1] and returns an (N, 1, H, W) height prediction in metres.
    """
    try:
        import segmentation_models_pytorch as smp
    except ImportError:
        pass
    else:
        model = smp.Unet(
            encoder_name="efficientnet-b4",
            encoder_weights="imagenet" if pretrained_encoder else None,
            in_channels=3,
            classes=1,
            activation=None,
        )
        return model

    # Fallback: plain timm + minimal U-Net decoder when smp is not available
    try:
        import timm
        import torch.nn as nn
    except ImportError as exc:
        raise ImportError(
            "timm and torch are required for model='unet'.  "
            "Install with: pip install torch torchvision timm"
        ) from exc

    class _SimpleUNet(nn.Module):
        """Lightweight 4-level U-Net with EfficientNet-B4 encoder via timm."""

        def __init__(self):
            super().__init__()
            import torch

            self.encoder = timm.create_model(
                "efficientnet_b4",
                pretrained=pretrained_encoder,
                features_only=True,
                out_indices=(0, 1, 2, 3, 4),
            )
            channels = self.encoder.feature_info.channels()  # [24, 32, 56, 160, 448]

            def _up(in_c, skip_c, out_c):
                return nn.Sequential(
                    nn.Conv2d(in_c + skip_c, out_c, 3, padding=1),
                    nn.BatchNorm2d(out_c),
                    nn.ReLU(inplace=True),
                    nn.Conv2d(out_c, out_c, 3, padding=1),
                    nn.BatchNorm2d(out_c),
                    nn.ReLU(inplace=True),
                )

            self.up4 = _up(channels[4], channels[3], 256)
            self.up3 = _up(256, channels[2], 128)
            self.up2 = _up(128, channels[1], 64)
            self.up1 = _up(64, channels[0], 32)
            self.head = nn.Sequential(
                nn.Conv2d(32, 1, 1),
                nn.ReLU(),       # heights must be non-negative
            )

        def forward(self, x):
            import torch.nn.functional as F

            feats = self.encoder(x)   # list of 5 feature maps
            f0, f1, f2, f3, f4 = feats

            d = F.interpolate(f4, scale_factor=2, mode="bilinear", align_corners=False)
            d = self.up4(torch.cat([d, f3], dim=1))
            d = F.interpolate(d, scale_factor=2, mode="bilinear", align_corners=False)
            d = self.up3(torch.cat([d, f2], dim=1))
            d = F.interpolate(d, scale_factor=2, mode="bilinear", align_corners=False)
            d = self.up2(torch.cat([d, f1], dim=1))
            d = F.interpolate(d, scale_factor=2, mode="bilinear", align_corners=False)
            d = self.up1(torch.cat([d, f0], dim=1))
            d = F.interpolate(d, size=x.shape[-2:], mode="bilinear", align_corners=False)
            return self.head(d)

    import torch  # noqa: F401 -- needed for torch.cat in forward
    return _SimpleUNet()


def _unet_inference(
    sat_rgb: np.ndarray,
    checkpoint: Path,
    device: str = "cpu",
    tile_size: int = 256,
    overlap: int = 32,
) -> np.ndarray:
    """Run the trained U-Net on *sat_rgb* with sliding-window tiling.

    Parameters
    ----------
    sat_rgb    : (H, W, 3) uint8 or float32 RGB.
    checkpoint : Path to a .pt checkpoint saved by ``train.py``.
    tile_size  : Tile size in pixels (model training resolution, default 256).
    overlap    : Overlap between adjacent tiles for seamless blending.

    Returns
    -------
    (H, W) float32 height prediction in metres.
    """
    # Check checkpoint existence first so the error is always FileNotFoundError,
    # regardless of whether torch is installed.
    if not checkpoint.exists():
        raise FileNotFoundError(
            f"U-Net checkpoint not found: {checkpoint}  "
            "Train a model first with TerrainSession.train_height_model() "
            "or train.py."
        )

    try:
        import torch
    except ImportError as exc:
        raise ImportError(
            "torch is required for model='unet'.  "
            "Install with: pip install torch torchvision timm"
        ) from exc

    model = _build_unet(pretrained_encoder=False)
    state = torch.load(checkpoint, map_location=device, weights_only=False)
    model.load_state_dict(state["model_state_dict"])
    model.eval()
    model.to(device)

    H, W = sat_rgb.shape[:2]
    rgb = sat_rgb.astype(np.float32) / 255.0  # (H, W, 3) in [0, 1]

    output = np.zeros((H, W), dtype=np.float32)
    weight = np.zeros((H, W), dtype=np.float32)
    step = tile_size - overlap

    ys = list(range(0, max(1, H - tile_size + 1), step))
    xs = list(range(0, max(1, W - tile_size + 1), step))
    # Ensure we cover the bottom-right corner
    if not ys or ys[-1] + tile_size < H:
        ys.append(max(0, H - tile_size))
    if not xs or xs[-1] + tile_size < W:
        xs.append(max(0, W - tile_size))

    with torch.no_grad():
        for y in ys:
            for x in xs:
                y1, x1 = y, x
                y2, x2 = min(y + tile_size, H), min(x + tile_size, W)
                tile = rgb[y1:y2, x1:x2]

                # Pad if smaller than tile_size
                pad_h = tile_size - tile.shape[0]
                pad_w = tile_size - tile.shape[1]
                if pad_h > 0 or pad_w > 0:
                    tile = np.pad(
                        tile,
                        ((0, pad_h), (0, pad_w), (0, 0)),
                        mode="reflect",
                    )

                t = torch.from_numpy(tile.transpose(2, 0, 1)[np.newaxis]).to(device)
                pred = model(t).squeeze().cpu().numpy()  # (tile_size, tile_size)
                pred = pred[: y2 - y1, : x2 - x1]

                output[y1:y2, x1:x2] += pred
                weight[y1:y2, x1:x2] += 1.0

    weight = np.maximum(weight, 1.0)
    return (output / weight).astype(np.float32)


# ------------------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------------------

def predict(
    sat_rgb: np.ndarray,
    known_heights: Optional[np.ndarray],
    bbox: BBox,
    model: str = "pretrained",
    checkpoint: Optional[Path] = None,
    device: str = "cpu",
    tile_size: int = 256,
) -> HeightResult:
    """Predict building heights from a satellite RGB image.

    Parameters
    ----------
    sat_rgb        : (H, W, 3) uint8 RGB satellite image.
    known_heights  : (H, W) float32 height raster with NaN where unknown.
                     Used for calibration (``model="pretrained"``) or as an
                     auxiliary input.  Pass ``None`` to skip calibration.
    bbox           : (north, south, east, west).
    model          : ``"pretrained"`` (Depth Anything V2) or ``"unet"``
                     (trained U-Net checkpoint).
    checkpoint     : Path to a .pt file for ``model="unet"``.  Defaults to
                     ``models/height_unet.pt``.
    device         : ``"cpu"`` or ``"cuda"``.
    tile_size      : Tile size for U-Net sliding-window inference (default 256).

    Returns
    -------
    HeightResult with:
        raster       -- (H, W) float32 absolute heights in metres.
        confidence   -- (H, W) float32 [0, 1].
        source_name  -- ``"depth_anything_v2"`` or ``"unet"``.
        resolution_m -- estimated metres per pixel (from bbox).
    """
    H, W = sat_rgb.shape[:2]
    north, south, east, west = bbox
    bbox_h_m = abs(north - south) * 111_320.0
    resolution_m = bbox_h_m / H if H > 0 else 1.0

    if model == "pretrained":
        logger.info("Running Depth Anything V2 inference (%d x %d px)", W, H)
        depth_rel = _depth_anything_inference(sat_rgb, device=device)
        kh = known_heights if known_heights is not None else np.full((H, W), np.nan, dtype=np.float32)
        height_abs, slope, _ = _calibrate_depth(depth_rel, kh, bbox)

        # Confidence: higher where we had calibration data nearby
        if known_heights is not None:
            valid_frac = float((~np.isnan(known_heights)).mean())
            conf_base = min(0.5, valid_frac * 2.0)
        else:
            conf_base = 0.2
        confidence = np.full((H, W), conf_base, dtype=np.float32)
        source_name = "depth_anything_v2"

    elif model == "unet":
        ckpt = checkpoint or _UNET_DEFAULT_CKPT
        logger.info("Running U-Net inference from %s", ckpt)
        height_abs = _unet_inference(sat_rgb, ckpt, device=device, tile_size=tile_size)
        confidence = np.full((H, W), 0.7, dtype=np.float32)
        source_name = "unet"

    else:
        raise ValueError(f"Unknown model: {model!r}.  Use 'pretrained' or 'unet'.")

    return HeightResult(
        raster=height_abs,
        confidence=confidence,
        source_name=source_name,
        resolution_m=resolution_m,
    )
