"""Script version of notebooks/Train_Height_CNN.ipynb.

Height CNN training workflow using tools.ml (RoofNetV3 family).
"""

from __future__ import annotations

import sys
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT))

from tools.ml.train import train_v3, TrainConfig  # noqa: E402
from tools.ml.eval import detect_arch             # noqa: E402

TILE_DIR   = PROJECT_ROOT / "cache" / "height_tiles_osm"
CHECKPOINT = PROJECT_ROOT / "models" / "roofnet_v3s.pt"

# Configuration
ARCH       = "v3s"
EPOCHS     = 30
BATCH_SIZE = 8
LR         = 3e-4
DEVICE     = "cpu"


def main() -> None:
    print(f"Tile dir  : {TILE_DIR}")
    print(f"Checkpoint: {CHECKPOINT}")
    print(f"Arch      : {ARCH}  epochs={EPOCHS}  lr={LR}  device={DEVICE}")

    tile_paths = sorted(TILE_DIR.glob("*.npz"))
    print(f"\nTiles found: {len(tile_paths)}")
    if not tile_paths:
        print("No tiles — run collect_osm_tiles.py first")
        return

    # 1) Inspect one sample tile
    sample = np.load(tile_paths[0])
    rgb    = sample["rgb"]
    height = sample["height"]
    fig, axes = plt.subplots(1, 2, figsize=(10, 4))
    axes[0].imshow(np.clip(rgb.transpose(1, 2, 0), 0, 1))
    axes[0].set_title("RGB tile")
    axes[0].axis("off")
    im = axes[1].imshow(height[0] if height.ndim == 3 else height, cmap="hot")
    axes[1].set_title("Height tile (metres)")
    axes[1].axis("off")
    plt.colorbar(im, ax=axes[1], label="m")
    plt.tight_layout()
    plt.show()
    print(f"Height range: [{height.min():.1f}, {height.max():.1f}] m")

    # 2) Train
    result = train_v3(
        tile_dir=str(TILE_DIR),
        output_model=str(CHECKPOINT),
        config=TrainConfig(
            task="height",
            epochs=EPOCHS,
            batch_size=BATCH_SIZE,
            lr=LR,
            patience=10,
            tile_size=256,
            device=DEVICE,
            num_workers=0,
            freeze_backbone_epochs=6,
            grad_loss_weight=0.2,
            pixel_loss_mode="hybrid",
            bldg_loss_weight=5.0,
            per_image_backprop=True,
            selection_metric="rmse",
            grad_clip_norm=1.0,
        ),
        n_iters=2,
        arch=ARCH,
    )

    print(f"\nBest val loss : {result['best_val_loss']:.4f}")
    print(f"Best epoch    : {result['best_epoch']}")
    print(f"Checkpoint    : {result['model_path']}")

    # 3) Plot training history
    history = result["history"]
    if history:
        epochs_x  = [h["epoch"] for h in history]
        val_loss  = [h["val_loss"] for h in history]
        val_mae   = [h["val_mae"] for h in history]
        train_loss = [h["train_loss"] for h in history]

        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 4))
        ax1.plot(epochs_x, train_loss, label="train loss")
        ax1.plot(epochs_x, val_loss,   label="val loss")
        ax1.axvline(result["best_epoch"], color="r", linestyle="--", alpha=0.5, label="best")
        ax1.set_xlabel("Epoch")
        ax1.set_ylabel("Loss")
        ax1.legend()
        ax1.set_title("Training curves")

        ax2.plot(epochs_x, val_mae, label="val MAE (m)")
        ax2.axvline(result["best_epoch"], color="r", linestyle="--", alpha=0.5)
        ax2.set_xlabel("Epoch")
        ax2.set_ylabel("MAE (m)")
        ax2.set_title("Validation MAE")
        plt.tight_layout()
        plt.show()

    # 4) Quick inference check on one val tile
    if CHECKPOINT.exists():
        import torch
        from tools.ml.models import build_model
        from tools.ml.data import make_height_loaders

        arch_detected = detect_arch(CHECKPOINT) or ARCH
        model = build_model(arch=arch_detected, task="both", pretrained=False, device=DEVICE)
        state = torch.load(str(CHECKPOINT), map_location="cpu", weights_only=False)
        model.load_state_dict(state["model_state_dict"])
        model.eval()

        _, val_loader, split = make_height_loaders(
            tile_paths, tile_size=256, batch_size=1, num_workers=0
        )
        rgb_t, ht = next(iter(val_loader))
        with torch.no_grad():
            if arch_detected.startswith("v3"):
                mask_logits, pred, _ = model(rgb_t, n_iters=2)
                mask = torch.sigmoid(mask_logits)[0, 0].numpy()
            else:
                pred, _ = model(rgb_t)
                mask = None

        pred_np = pred[0, 0].numpy()
        gt_np   = ht[0, 0].numpy() if ht.ndim == 4 else ht[0].numpy()
        rgb_np  = rgb_t[0].numpy().transpose(1, 2, 0)
        # Undo ImageNet normalisation for display
        mean = np.array([0.485, 0.456, 0.406])
        std  = np.array([0.229, 0.224, 0.225])
        rgb_display = np.clip(rgb_np * std + mean, 0, 1)

        ncols = 4 if mask is not None else 3
        fig, axes = plt.subplots(1, ncols, figsize=(4 * ncols, 4))
        axes[0].imshow(rgb_display);      axes[0].set_title("RGB");          axes[0].axis("off")
        axes[1].imshow(gt_np,  cmap="hot"); axes[1].set_title("GT height");  axes[1].axis("off")
        axes[2].imshow(pred_np, cmap="hot"); axes[2].set_title("Predicted"); axes[2].axis("off")
        if mask is not None:
            axes[3].imshow(mask, cmap="Blues"); axes[3].set_title("Mask"); axes[3].axis("off")
        plt.tight_layout()
        plt.show()

        bldg = gt_np > 0
        if bldg.any():
            mae = np.abs(pred_np[bldg] - gt_np[bldg]).mean()
            print(f"Building-pixel MAE on sample tile: {mae:.2f} m")


if __name__ == "__main__":
    main()
