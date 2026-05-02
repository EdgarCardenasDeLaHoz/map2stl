"""Minimal Retna_V1 trainer — first-principles building-height baseline.

Why this exists:

The full RoofNetV3 (1.4 M params, MobileNetV3 + FPN) achieved a 0.96 Pearson
correlation between per-tile MAE and target-mean-height on the combined
European + Cartagena dataset — i.e. it learned to predict the marginal mean
and nothing else. A constant-7m predictor beats it on MAE.

`Retna_V1` from `tools/example/networks.py` is the opposite design philosophy:

- 8 channels per level, ~1k parameters (vs 1.4M)
- raw RGB re-injected at every block (no information bottleneck)
- output bounded to [0, 1] (no marginal-mean collapse)
- Dice loss on a normalised target (no L1-mean trap)

If a tiny model with the right inductive bias outperforms RoofNetV3, the
problem is over-parameterisation, not data scarcity.

Targets are normalised by ``HEIGHT_NORM_M`` (default 50 m) and clipped to
[0, 1] before the Dice loss. Inference is denormalised back to metres.

Usage:
    python -m tools.ml.train_retna \\
        --tiles cache/height_tiles_combined \\
        --output models/retna_v1.pt \\
        --epochs 30 --batch-size 4 --tile-size 128
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, Dataset, random_split

_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parents[1]
for _p in (_STRM2STL, _STRM2STL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.example.networks import Retna_V1   # noqa: E402

HEIGHT_NORM_M = 200.0  # divisor that maps real metres → [0, 1]. 200m covers skyscrapers.
                       # Bumped from 50 → 100m after audit found ~3% of pixels
                       # in tall-building tiles (Barcelona, Manhattan) were
                       # being clipped to 1.0, removing exactly the signal we
                       # need to break the marginal-mean trap.


class HeightTileDataset(Dataset):
    """Loads (rgb, height) tiles. Heights are normalised to [0, 1]."""

    def __init__(self, paths: list[Path], tile_size: int, augment: bool = True):
        self.paths = list(paths)
        self.tile_size = tile_size
        self.augment = augment

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, idx):
        with np.load(self.paths[idx]) as d:
            rgb = d["rgb"]            # (3, H, W) float32 in [0, 1]
            height = d["height"][0]   # (H, W) float32 metres

        ts = self.tile_size
        if rgb.shape[1] > ts:
            off_y = np.random.randint(0, rgb.shape[1] - ts + 1) if self.augment else (rgb.shape[1] - ts) // 2
            off_x = np.random.randint(0, rgb.shape[2] - ts + 1) if self.augment else (rgb.shape[2] - ts) // 2
            rgb = rgb[:, off_y:off_y + ts, off_x:off_x + ts]
            height = height[off_y:off_y + ts, off_x:off_x + ts]

        if self.augment:
            if np.random.rand() < 0.5:
                rgb = rgb[:, :, ::-1].copy(); height = height[:, ::-1].copy()
            if np.random.rand() < 0.5:
                rgb = rgb[:, ::-1, :].copy(); height = height[::-1, :].copy()

        target = np.clip(height / HEIGHT_NORM_M, 0.0, 1.0).astype(np.float32)
        return torch.from_numpy(rgb), torch.from_numpy(target).unsqueeze(0)


def dice_loss(pred, target, smooth: float = 1e-5):
    """Soft Dice loss (single channel). pred, target ∈ [0, 1]."""
    inter = 2.0 * (pred * target).mean(dim=(2, 3))
    comb  = (pred ** 2 + target ** 2).mean(dim=(2, 3))
    dsc = (inter + smooth) / (comb + smooth)
    return (1.0 - dsc).mean()


def cubic_residual_dice(pred, target, l3_weight: float = 1.0,
                        smooth: float = 1e-5):
    """Dice + λ·E[|res|³]. Tall-building errors weighted more than L2."""
    d = dice_loss(pred, target, smooth=smooth)
    l3 = ((pred - target).abs() ** 3).mean()
    return d + l3_weight * l3


def squared_residual_dice(pred, target, l2_weight: float = 1.0,
                          smooth: float = 1e-5):
    """Dice + λ·MSE. The MSE term penalises big-magnitude errors quadratically.

    Reasoning: pure Dice rewards overlap shape but treats a small underprediction
    of a tall building the same as a small underprediction of a short one.
    Adding an L2 term in normalised-height space means a 0.5 underprediction
    costs 4× more than a 0.25 underprediction — i.e. "wronger answers cost
    much more". When both pred and target are in [0, 1], L2 is bounded ≤ 1 so
    its contribution stays comparable to Dice in magnitude.
    """
    d = dice_loss(pred, target, smooth=smooth)
    l2 = ((pred - target) ** 2).mean()
    return d + l2_weight * l2


def evaluate(model, loader, device):
    """Return dict of validation metrics in metres."""
    model.eval()
    total_loss = 0.0
    total_mae_m = 0.0
    total_sq_m = 0.0
    iou_num, iou_den = 0.0, 0.0
    target_means: list[float] = []
    per_tile_mae: list[float] = []
    n = 0
    with torch.no_grad():
        for rgb, target in loader:
            rgb, target = rgb.to(device), target.to(device)
            pred = model(rgb)
            loss = dice_loss(pred, target)

            target_m = target * HEIGHT_NORM_M
            pred_m   = pred   * HEIGHT_NORM_M
            mask = (target_m > 0).float()
            mask_n = mask.sum().clamp(min=1.0)

            mae_m = (mask * (pred_m - target_m).abs()).sum() / mask_n
            sq_m  = (mask * (pred_m - target_m) ** 2).sum() / mask_n

            pred_mask = (pred_m > 3.0).float()
            iou_num += (pred_mask * mask).sum().item()
            iou_den += (torch.maximum(pred_mask, mask)).sum().item()

            for b in range(rgb.size(0)):
                tm = mask[b].sum().clamp(min=1.0)
                m_b = ((mask[b] * (pred_m[b] - target_m[b]).abs()).sum() / tm).item()
                per_tile_mae.append(m_b)
                target_means.append(target_m[b][mask[b] > 0].mean().item() if mask[b].sum() > 0 else 0.0)

            bs = rgb.size(0)
            total_loss += loss.item() * bs
            total_mae_m += mae_m.item() * bs
            total_sq_m += sq_m.item() * bs
            n += bs

    nn_ = max(n, 1)
    pmae = np.array(per_tile_mae)
    pmean = np.array(target_means)
    pearson = (
        float(np.corrcoef(pmae, pmean)[0, 1])
        if pmae.std() > 0 and pmean.std() > 0 else None
    )
    return {
        "val_loss": total_loss / nn_,
        "val_mae":  total_mae_m / nn_,
        "val_rmse": float(np.sqrt(total_sq_m / nn_)),
        "val_mask_iou": iou_num / max(iou_den, 1.0),
        "pearson_mae_height": pearson,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--output", default="models/retna_v1.pt")
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--tile-size", type=int, default=128)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lr-patience", type=int, default=5,
                    help="ReduceLROnPlateau patience: epochs without val improvement before lr ×= 0.5")
    ap.add_argument("--loss", choices=["dice", "dice_l2", "dice_l3"], default="dice_l2",
                    help="dice = soft Dice; dice_l2 = +λ·MSE; dice_l3 = +λ·E[|res|³]")
    ap.add_argument("--l2-weight", type=float, default=1.0,
                    help="λ for residual term in --loss=dice_l2 or dice_l3")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--hidden-channels", nargs="+", type=int, default=[8, 8, 8, 8])
    ap.add_argument("--resume", default=None,
                    help="Path to a checkpoint to warm-start from. "
                         "If shape mismatches, only matching parameters are copied.")
    args = ap.parse_args()

    torch.manual_seed(args.seed)
    np.random.seed(args.seed)

    strm2stl = Path(__file__).resolve().parents[2]
    tiles = Path(args.tiles)
    if not tiles.is_absolute():
        tiles = strm2stl / args.tiles
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = strm2stl / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    paths = sorted(tiles.glob("*.npz"))
    if not paths:
        raise SystemExit(f"no tiles in {tiles}")

    n = len(paths)
    n_val = max(1, int(0.15 * n))
    n_train = n - n_val

    full = HeightTileDataset(paths, args.tile_size, augment=True)
    train_ds, _ = random_split(
        full, [n_train, n_val],
        generator=torch.Generator().manual_seed(args.seed),
    )
    val_ds_full = HeightTileDataset(paths, args.tile_size, augment=False)
    _, val_ds = random_split(
        val_ds_full, [n_train, n_val],
        generator=torch.Generator().manual_seed(args.seed),
    )

    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=args.batch_size, shuffle=False, num_workers=0)

    device = torch.device("cpu")
    model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=args.hidden_channels)
    if args.resume:
        rp = Path(args.resume)
        if not rp.is_absolute():
            rp = strm2stl / args.resume
        if rp.exists():
            state = torch.load(str(rp), map_location="cpu", weights_only=False)
            sd = state.get("model_state_dict", state)
            own = model.state_dict()
            n_copied = 0
            for k, v in sd.items():
                if k in own and own[k].shape == v.shape:
                    own[k].copy_(v); n_copied += 1
            model.load_state_dict(own)
            print(f"Resumed from {rp} ({n_copied}/{len(own)} params copied)")
    model.to(device)
    n_params = sum(p.numel() for p in model.parameters())

    loss_name = args.loss
    print(f"Tiles    : {n_train} train / {n_val} val")
    print(f"Model    : Retna_V1 hidden={args.hidden_channels}, {n_params:,} params")
    print(f"Loss     : {loss_name} on heights/{HEIGHT_NORM_M}m, output ∈ [0,1]")
    print(f"Output   : {out_path}")
    print()

    optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
    # ReduceLROnPlateau: drop LR by 0.5× after `patience` epochs without
    # improvement on val_loss. Floor at 1e-5 so we don't grind to a stop.
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5,
        patience=args.lr_patience, min_lr=1e-5,
    )
    history = []
    best_loss = float("inf")
    best_state = None
    best_epoch = 0

    if loss_name == "dice":
        loss_fn = dice_loss
    elif loss_name == "dice_l2":
        loss_fn = lambda p, t: squared_residual_dice(p, t, l2_weight=args.l2_weight)
    elif loss_name == "dice_l3":
        loss_fn = lambda p, t: cubic_residual_dice(p, t, l3_weight=args.l2_weight)
    else:
        raise ValueError(f"unknown loss {loss_name!r}")

    for epoch in range(1, args.epochs + 1):
        t0 = time.perf_counter()
        model.train()
        train_loss = 0.0
        for rgb, target in train_loader:
            rgb, target = rgb.to(device), target.to(device)
            pred = model(rgb)
            loss = loss_fn(pred, target)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            train_loss += loss.item() * rgb.size(0)
        train_loss /= max(n_train, 1)

        metrics = evaluate(model, val_loader, device)
        scheduler.step(metrics["val_loss"])
        cur_lr = optimizer.param_groups[0]["lr"]
        dt = time.perf_counter() - t0
        is_best = metrics["val_loss"] < best_loss
        marker = " * best" if is_best else ""
        print(
            f"  Epoch {epoch:3d}/{args.epochs}  "
            f"train={train_loss:.4f}  "
            f"val={metrics['val_loss']:.4f}  "
            f"mae={metrics['val_mae']:.2f}m  "
            f"rmse={metrics['val_rmse']:.2f}m  "
            f"iou={metrics['val_mask_iou']:.3f}  "
            f"r={metrics['pearson_mae_height']:+.2f}  "
            f"lr={cur_lr:.1e}{marker}  ({dt:.1f}s)",
            flush=True,
        )
        if is_best:
            best_loss = metrics["val_loss"]
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            best_epoch = epoch
        history.append({"epoch": epoch, "train_loss": train_loss, **metrics})

    if best_state is not None:
        model.load_state_dict(best_state)
    torch.save({
        "model_state_dict": model.state_dict(),
        "arch": "retna_v1",
        "hidden_channels": args.hidden_channels,
        "height_norm_m": HEIGHT_NORM_M,
        "best_loss": best_loss,
        "best_epoch": best_epoch,
        "config": vars(args),
    }, str(out_path))

    history_path = out_path.parent / (out_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump({"history": history, "best_loss": best_loss, "best_epoch": best_epoch}, f, indent=2)
    print(f"\nSaved {out_path}")
    print(f"Best   : val_loss={best_loss:.4f} at epoch {best_epoch}")

    try:
        from tools.ml.scoreboard import record_run
        best_metrics = next(h for h in history if h["epoch"] == best_epoch)
        record_run(
            model_path=str(out_path),
            arch="retna_v1",
            task="height",
            best_metrics={
                "val_loss": best_metrics["val_loss"],
                "val_mae":  best_metrics["val_mae"],
                "val_rmse": best_metrics["val_rmse"],
                "val_mask_iou": best_metrics["val_mask_iou"],
                "pearson_mae_height": best_metrics["pearson_mae_height"],
            },
            n_train=n_train, n_val=n_val, epochs=best_epoch,
            config=vars(args),
            notes=f"tile_dir={tiles.name}; norm={HEIGHT_NORM_M}m; n_params={n_params}",
        )
    except Exception as e:
        print(f"(scoreboard record failed: {e})")


if __name__ == "__main__":
    main()
