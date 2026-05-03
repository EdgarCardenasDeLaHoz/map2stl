"""
tools.ml.predict_demo -- Run the trained RoofNetV2 height head on a satellite
RGB tile and visualize against OSM ground truth.

Usage:
    python -m tools.ml.predict_demo --bbox 52.530 52.515 13.405 13.385 \
        --output output/predict_demo.png
"""

from __future__ import annotations
import argparse
import sys
import base64
from io import BytesIO
from pathlib import Path

import numpy as np
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parents[1]
for _p in (_STRM2STL, _STRM2STL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def predict_and_plot(
    north: float, south: float, east: float, west: float,
    checkpoint: str = "models/roofnet_height_v1.pt",
    tile_size: int = 256,
    output: str = "output/predict_demo.png",
):
    import torch
    from PIL import Image
    from geo2stl.sat2stl import fetch_satellite_tiles
    from tools.ml.collect_osm_tiles import _fetch_buildings_via_osmnx, _rasterize_buildings
    from tools.ml.models import RoofNetV2

    # 1. Fetch satellite RGB
    print("Fetching satellite...")
    sat_b64 = fetch_satellite_tiles(north, south, east, west, dim=tile_size)
    img = Image.open(BytesIO(base64.b64decode(sat_b64))).convert("RGB")
    if img.size != (tile_size, tile_size):
        img = img.resize((tile_size, tile_size), Image.BILINEAR)
    sat_rgb = np.asarray(img, dtype=np.float32) / 255.0  # (H, W, 3)

    # 2. Fetch OSM ground truth
    print("Fetching OSM buildings...")
    try:
        buildings = _fetch_buildings_via_osmnx(north, south, east, west)
        gt = _rasterize_buildings(buildings, north, south, east, west, tile_size)
        print(f"  OSM ground truth: {(gt > 0).sum()} building pixels, max={gt.max():.1f}m")
    except Exception as exc:
        print(f"  OSM fetch failed: {exc}")
        gt = None

    # 3. Run RoofNetV2 prediction
    print(f"Loading checkpoint: {checkpoint}")
    ckpt_path = Path(checkpoint)
    if not ckpt_path.is_absolute():
        ckpt_path = _STRM2STL / checkpoint
    state = torch.load(ckpt_path, map_location="cpu", weights_only=True)
    sd = state.get("model_state_dict", state)

    model = RoofNetV2(pretrained=False)
    model.load_state_dict(sd)
    model.eval()

    # ImageNet normalize
    mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
    std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
    rgb_norm = (sat_rgb - mean) / std
    inp = torch.from_numpy(rgb_norm.transpose(2, 0, 1)).unsqueeze(0)

    print("Running prediction...")
    with torch.no_grad():
        pred_t, _ = model(inp)
    pred = pred_t.squeeze().numpy()
    pred = np.clip(pred, 0, 100)

    print(f"  Prediction: range=[{pred.min():.1f}, {pred.max():.1f}] m, mean={pred.mean():.1f}m")

    # 4. Plot
    if gt is not None:
        fig, axes = plt.subplots(2, 2, figsize=(12, 12))
        ax_rgb, ax_gt = axes[0]
        ax_pred, ax_diff = axes[1]
        vmax = max(gt.max(), pred.max(), 30)

        ax_gt.imshow(gt, cmap="hot", vmin=0, vmax=vmax)
        ax_gt.set_title(f"OSM ground truth (max {gt.max():.0f}m)")
        ax_gt.axis("off")

        # Compute MAE in building-only pixels
        mask = gt > 0
        if mask.any():
            mae = np.mean(np.abs(pred[mask] - gt[mask]))
            print(f"  MAE on building pixels: {mae:.2f} m")
        else:
            mae = float("nan")

        diff = np.abs(pred - gt)
        ax_diff.imshow(diff, cmap="Reds", vmin=0, vmax=20)
        ax_diff.set_title(f"|pred - gt|  (MAE on bldg = {mae:.1f}m)")
        ax_diff.axis("off")
    else:
        fig, axes = plt.subplots(1, 2, figsize=(12, 6))
        ax_rgb, ax_pred = axes
        vmax = max(pred.max(), 30)

    ax_rgb.imshow(sat_rgb)
    ax_rgb.set_title("Satellite RGB")
    ax_rgb.axis("off")

    im = ax_pred.imshow(pred, cmap="hot", vmin=0, vmax=vmax)
    ax_pred.set_title(f"RoofNetV2 prediction (max {pred.max():.0f}m)")
    ax_pred.axis("off")

    plt.tight_layout()
    out_path = _STRM2STL / output if not Path(output).is_absolute() else Path(output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(str(out_path), dpi=120, bbox_inches="tight")
    print(f"Saved: {out_path}")


def _parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--bbox", nargs=4, type=float, metavar=("N", "S", "E", "W"),
                   default=[52.530, 52.515, 13.405, 13.385],
                   help="N S E W")
    p.add_argument("--checkpoint", default="models/roofnet_height_v1.pt")
    p.add_argument("--tile-size", type=int, default=256)
    p.add_argument("--output", default="output/predict_demo.png")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    n, s, e, w = args.bbox
    predict_and_plot(n, s, e, w, args.checkpoint, args.tile_size, args.output)
