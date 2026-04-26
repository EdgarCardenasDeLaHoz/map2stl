"""
tools.ml.eval — Evaluation metrics, confusion matrices, and reporting.

Provides both programmatic evaluation (for notebooks) and CLI evaluation
(for automated runs).  All functions return plain dicts/arrays suitable
for matplotlib plotting in notebooks.

Usage (CLI):
    python -m tools.ml.eval --model models/roofnet_shape_v2.pt --data-dir output/roof_crops

Usage (Python / notebook):
    from tools.ml.eval import evaluate_shape_model, plot_confusion_matrix
    results = evaluate_shape_model("models/roofnet_shape_v2.pt", "output/roof_crops")
    plot_confusion_matrix(results["confusion_matrix"])
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import numpy as np

from tools.ml.config import (
    SHAPE_LABELS,
    DEFAULT_CROP_SIZE,
    DEFAULT_SHAPE_MODEL,
    MAX_HEIGHT_M,
)

logger = logging.getLogger(__name__)

_TORCH_AVAILABLE = False
try:
    import torch
    _TORCH_AVAILABLE = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Shape evaluation
# ---------------------------------------------------------------------------

def evaluate_shape_model(
    model_path: str = DEFAULT_SHAPE_MODEL,
    data_dir: str = "output/roof_crops",
    crop_size: int = DEFAULT_CROP_SIZE,
    batch_size: int = 32,
    device: str = "auto",
    split: str = "test",
) -> dict:
    """Evaluate a RoofNetV2 shape model on the test (or val) split.

    Returns
    -------
    dict with keys:
      - accuracy: float (overall)
      - per_class_accuracy: dict[str, float]
      - per_class_count: dict[str, int]
      - confusion_matrix: np.ndarray (N x N)
      - predictions: list of (true_label, pred_label, confidence)
      - n_samples: int
    """
    if not _TORCH_AVAILABLE:
        raise ImportError("PyTorch required")

    from tools.ml.models import build_model
    from tools.ml.data import make_roof_loaders

    if device == "auto":
        if torch.cuda.is_available():
            dev = torch.device("cuda")
        elif hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            dev = torch.device("mps")
        else:
            dev = torch.device("cpu")
    else:
        dev = torch.device(device)

    strm2stl = Path(__file__).resolve().parents[2]
    data_root = Path(data_dir)
    if not data_root.is_absolute():
        data_root = strm2stl / data_dir

    manifest = data_root / "manifest.csv"
    if not manifest.exists():
        raise FileNotFoundError(f"No manifest at {manifest}")

    _, val_loader, test_loader, split_info = make_roof_loaders(
        manifest, data_root, crop_size=crop_size, batch_size=batch_size
    )

    loader = test_loader if (split == "test" and test_loader) else val_loader

    model = build_model(checkpoint=str(model_path), device=str(dev))
    model.eval()

    n_classes = len(SHAPE_LABELS)
    confusion = np.zeros((n_classes, n_classes), dtype=int)
    predictions: list[tuple[str, str, float]] = []

    with torch.no_grad():
        for imgs, labels in loader:
            imgs, labels = imgs.to(dev), labels.to(dev)
            _, logits = model(imgs)
            probs = torch.softmax(logits, dim=1)
            confs, preds = probs.max(dim=1)

            for gt, pred, conf in zip(labels.tolist(), preds.tolist(), confs.tolist()):
                confusion[gt, pred] += 1
                predictions.append((SHAPE_LABELS[gt], SHAPE_LABELS[pred], conf))

    # Compute metrics
    per_class_acc = {}
    per_class_count = {}
    for i, lbl in enumerate(SHAPE_LABELS):
        total = confusion[i].sum()
        correct = confusion[i, i]
        per_class_acc[lbl] = correct / max(total, 1)
        per_class_count[lbl] = int(total)

    overall_acc = confusion.diagonal().sum() / max(confusion.sum(), 1)

    return {
        "accuracy": float(overall_acc),
        "per_class_accuracy": per_class_acc,
        "per_class_count": per_class_count,
        "confusion_matrix": confusion,
        "predictions": predictions,
        "n_samples": len(predictions),
        "split_info": split_info,
        "labels": SHAPE_LABELS,
    }


def evaluate_height_model(
    model_path: str = "models/roofnet_height_v1.pt",
    tile_dir: str = "cache/height_tiles",
    batch_size: int = 8,
    device: str = "auto",
) -> dict:
    """Evaluate a height regression model on validation tiles.

    Returns
    -------
    dict with mae, rmse, per_tile_errors, predictions, targets.
    """
    if not _TORCH_AVAILABLE:
        raise ImportError("PyTorch required")

    from tools.ml.models import build_model
    from tools.ml.data import make_height_loaders

    if device == "auto":
        dev = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    else:
        dev = torch.device(device)

    strm2stl = Path(__file__).resolve().parents[2]
    tile_root = Path(tile_dir)
    if not tile_root.is_absolute():
        tile_root = strm2stl / tile_dir

    tile_paths = sorted(tile_root.glob("*.npz"))
    if not tile_paths:
        raise FileNotFoundError(f"No tiles in {tile_root}")

    _, val_loader, split_info = make_height_loaders(tile_paths, batch_size=batch_size)

    model = build_model(task="height", checkpoint=model_path, device=str(dev))
    model.eval()

    all_preds = []
    all_targets = []

    with torch.no_grad():
        for rgb, height in val_loader:
            rgb = rgb.to(dev)
            pred, _ = model(rgb)
            all_preds.append(pred.cpu().numpy())
            all_targets.append(height.numpy())

    preds = np.concatenate(all_preds).ravel()
    targets = np.concatenate(all_targets).ravel()

    mae = float(np.mean(np.abs(preds - targets)))
    rmse = float(np.sqrt(np.mean((preds - targets) ** 2)))

    return {
        "mae": mae,
        "rmse": rmse,
        "n_tiles": len(tile_paths),
        "n_val": split_info["n_val"],
        "predictions": preds,
        "targets": targets,
    }


# ---------------------------------------------------------------------------
# Plotting helpers (for notebooks)
# ---------------------------------------------------------------------------

def plot_confusion_matrix(
    confusion: np.ndarray,
    labels: "list[str] | None" = None,
    title: str = "Roof Shape Confusion Matrix",
    figsize: tuple = (8, 7),
    save_path: "str | None" = None,
):
    """Plot a confusion matrix heatmap.  Returns the matplotlib figure."""
    import matplotlib.pyplot as plt

    labels = labels or SHAPE_LABELS
    n = len(labels)

    # Normalise rows to percentages
    row_sums = confusion.sum(axis=1, keepdims=True)
    row_sums = np.maximum(row_sums, 1)
    normed = confusion / row_sums * 100

    fig, ax = plt.subplots(figsize=figsize)
    im = ax.imshow(normed, cmap="Blues", vmin=0, vmax=100)

    ax.set_xticks(range(n))
    ax.set_yticks(range(n))
    ax.set_xticklabels(labels, rotation=45, ha="right")
    ax.set_yticklabels(labels)
    ax.set_xlabel("Predicted")
    ax.set_ylabel("True")
    ax.set_title(title)

    for i in range(n):
        for j in range(n):
            count = confusion[i, j]
            pct = normed[i, j]
            color = "white" if pct > 50 else "black"
            ax.text(j, i, f"{count}\n({pct:.0f}%)", ha="center", va="center",
                    fontsize=9, color=color)

    plt.colorbar(im, ax=ax, label="% of true class")
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


def plot_training_history(
    history_path: str,
    save_path: "str | None" = None,
):
    """Plot training curves from a history JSON file.  Returns the figure."""
    import matplotlib.pyplot as plt

    with open(history_path) as fh:
        data = json.load(fh)

    history = data["history"]
    epochs = [h["epoch"] for h in history]

    has_acc = "train_acc" in history[0]

    if has_acc:
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5))

        ax1.plot(epochs, [h["train_loss"] for h in history], label="train")
        ax1.plot(epochs, [h["val_loss"] for h in history], label="val")
        ax1.set_xlabel("Epoch")
        ax1.set_ylabel("Loss")
        ax1.set_title("Loss")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

        ax2.plot(epochs, [h["train_acc"] for h in history], label="train")
        ax2.plot(epochs, [h["val_acc"] for h in history], label="val")
        ax2.set_xlabel("Epoch")
        ax2.set_ylabel("Accuracy")
        ax2.set_title("Accuracy")
        ax2.legend()
        ax2.grid(True, alpha=0.3)

        best_epoch = data.get("best_epoch", 0)
        best_acc = data.get("best_val_acc", 0)
        if best_epoch:
            ax2.axvline(best_epoch, color="red", linestyle="--", alpha=0.5,
                        label=f"best ({best_acc:.3f})")
            ax2.legend()

    else:
        fig, ax1 = plt.subplots(figsize=(8, 5))
        ax1.plot(epochs, [h["train_loss"] for h in history], label="train")
        ax1.plot(epochs, [h["val_loss"] for h in history], label="val")
        ax1.set_xlabel("Epoch")
        ax1.set_ylabel("Loss")
        ax1.set_title("Height Regression Loss")
        ax1.legend()
        ax1.grid(True, alpha=0.3)

    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


def plot_height_scatter(
    predictions: np.ndarray,
    targets: np.ndarray,
    mae: float,
    rmse: float,
    save_path: "str | None" = None,
):
    """Scatter plot of predicted vs true heights.  Returns the figure."""
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7, 7))
    lim = max(targets.max(), predictions.max()) * 1.05

    ax.scatter(targets, predictions, alpha=0.05, s=2, c="steelblue")
    ax.plot([0, lim], [0, lim], "r--", lw=1, label="perfect")
    ax.set_xlabel("Ground truth height (m)")
    ax.set_ylabel("Predicted height (m)")
    ax.set_title(f"Height prediction  (MAE={mae:.2f}m, RMSE={rmse:.2f}m)")
    ax.set_xlim(0, lim)
    ax.set_ylim(0, lim)
    ax.set_aspect("equal")
    ax.legend()
    ax.grid(True, alpha=0.3)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


def plot_sample_predictions(
    model_path: str,
    data_dir: str,
    n_samples: int = 16,
    crop_size: int = DEFAULT_CROP_SIZE,
    device: str = "cpu",
    save_path: "str | None" = None,
):
    """Show a grid of sample crops with true vs predicted labels.

    Useful for notebook-based inspection of what the model sees.
    Returns the figure.
    """
    if not _TORCH_AVAILABLE:
        raise ImportError("PyTorch required")
    import matplotlib.pyplot as plt
    from tools.ml.models import build_model
    from tools.ml.data import RoofCropDataset, build_transforms

    strm2stl = Path(__file__).resolve().parents[2]
    data_root = Path(data_dir)
    if not data_root.is_absolute():
        data_root = strm2stl / data_dir

    manifest = data_root / "manifest.csv"
    eval_tf = build_transforms(crop_size, augment=False)
    ds = RoofCropDataset(manifest, data_root, eval_tf)

    model = build_model(checkpoint=model_path, device=device)
    model.eval()

    indices = np.random.choice(len(ds), min(n_samples, len(ds)), replace=False)
    cols = min(4, n_samples)
    rows = (n_samples + cols - 1) // cols

    fig, axes = plt.subplots(rows, cols, figsize=(3.5 * cols, 3.5 * rows))
    if rows == 1:
        axes = [axes] if cols == 1 else list(axes)
    else:
        axes = [ax for row in axes for ax in row]

    mean = np.array([0.485, 0.456, 0.406])
    std = np.array([0.229, 0.224, 0.225])

    for ax_idx, data_idx in enumerate(indices):
        img_tensor, true_label = ds[data_idx]

        with torch.no_grad():
            _, logits = model(img_tensor.unsqueeze(0).to(device))
            probs = torch.softmax(logits, dim=1)[0]
            pred_label = probs.argmax().item()
            confidence = probs[pred_label].item()

        # Denormalise for display
        img_np = img_tensor.cpu().numpy().transpose(1, 2, 0)
        img_np = img_np * std + mean
        img_np = np.clip(img_np, 0, 1)

        ax = axes[ax_idx]
        ax.imshow(img_np)
        true_name = SHAPE_LABELS[true_label]
        pred_name = SHAPE_LABELS[pred_label]
        correct = true_label == pred_label
        color = "green" if correct else "red"
        ax.set_title(f"T:{true_name}\nP:{pred_name} ({confidence:.0%})", fontsize=9, color=color)
        ax.axis("off")

    # Hide unused axes
    for ax_idx in range(len(indices), len(axes)):
        axes[ax_idx].axis("off")

    plt.suptitle("Sample Predictions (green=correct, red=wrong)", fontsize=12)
    plt.tight_layout()

    if save_path:
        plt.savefig(save_path, dpi=150, bbox_inches="tight")

    return fig


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    import argparse

    parser = argparse.ArgumentParser(description="Evaluate RoofNetV2")
    parser.add_argument("--task", choices=["shape", "height"], default="shape")
    parser.add_argument("--model", default=None)
    parser.add_argument("--data-dir", default="output/roof_crops")
    parser.add_argument("--tile-dir", default="cache/height_tiles")
    parser.add_argument("--crop-size", type=int, default=DEFAULT_CROP_SIZE)
    parser.add_argument("--device", default="auto")
    args = parser.parse_args()

    model_path = args.model
    if model_path is None:
        model_path = DEFAULT_SHAPE_MODEL if args.task == "shape" else "models/roofnet_height_v1.pt"

    if args.task == "shape":
        results = evaluate_shape_model(
            model_path=model_path,
            data_dir=args.data_dir,
            crop_size=args.crop_size,
            device=args.device,
        )
        print(f"\nOverall accuracy: {results['accuracy']:.3f}")
        print(f"Samples: {results['n_samples']}")
        print("\nPer-class:")
        for lbl in SHAPE_LABELS:
            acc = results["per_class_accuracy"][lbl]
            cnt = results["per_class_count"][lbl]
            print(f"  {lbl:<12} {acc:.3f}  (n={cnt})")
    else:
        results = evaluate_height_model(
            model_path=model_path,
            tile_dir=args.tile_dir,
            device=args.device,
        )
        print(f"\nMAE:  {results['mae']:.2f} m")
        print(f"RMSE: {results['rmse']:.2f} m")
        print(f"Tiles: {results['n_tiles']} total, {results['n_val']} val")


if __name__ == "__main__":
    main()
