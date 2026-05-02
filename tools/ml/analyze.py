"""tools.ml.analyze — Inspect a trained checkpoint's behaviour.

Reports four diagnostic signals so we can make grounded decisions about
how to improve training:

  1. Per-sample MAE distribution on the validation set.
     Identifies which tiles are easy vs hard.

  2. Loss-component decomposition (mask BCE, Dice, height L1, Sobel,
     height-IoU). Tells us which signal is and isn't moving.

  3. Layer-wise gradient norms after one validation backward pass.
     A "dead" layer (≈0) signals frozen / collapsed weights;
     an enormous norm signals instability.

  4. Activation statistics (mean / std / fraction-saturated) at each
     decoder level after the same forward pass. Detects mode collapse
     (uniform output) and saturation (every value at 0 or 1).

Usage:
    python -m tools.ml.analyze \\
        --checkpoint models/roofnet_v3_session.pt \\
        --tiles cache/height_tiles_osm_small \\
        --arch v3 \\
        --n-iters 1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np

try:
    import torch
    import torch.nn.functional as F
except ImportError as e:
    raise SystemExit(f"PyTorch required: {e}")


def _resolve_paths(checkpoint: str, tiles: str) -> tuple[Path, Path]:
    strm2stl = Path(__file__).resolve().parents[2]
    ckpt = Path(checkpoint)
    if not ckpt.is_absolute():
        ckpt = strm2stl / checkpoint
    tdir = Path(tiles)
    if not tdir.is_absolute():
        tdir = strm2stl / tiles
    return ckpt, tdir


def _load_model(checkpoint: Path, arch: str, n_iters: int):
    from tools.ml.models import build_model
    model = build_model(arch=arch, pretrained=False)
    state = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    sd = state.get("model_state_dict", state)
    model.load_state_dict(sd, strict=False)
    model.eval()
    return model


def _per_sample_mae(model, loader, n_iters: int) -> tuple[np.ndarray, np.ndarray]:
    """Return arrays (mae_per_tile, target_mean_height_per_tile)."""
    maes, gt_means = [], []
    with torch.no_grad():
        for rgb, height in loader:
            mask_logits, height_pred, _ = model(rgb, n_iters=n_iters)
            mask_prob = torch.sigmoid(mask_logits)
            gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
            target_mask = (height > 0).float()
            for b in range(rgb.size(0)):
                tm = target_mask[b].sum().clamp(min=1.0)
                mae = ((target_mask[b] * (gated[b] - height[b]).abs()).sum() / tm).item()
                gt_mean = (target_mask[b] * height[b]).sum().item() / max(tm.item(), 1.0)
                maes.append(mae)
                gt_means.append(gt_mean)
    return np.array(maes), np.array(gt_means)


def _loss_decomposition(model, loader, n_iters: int, grad_weight: float = 0.2) -> dict[str, float]:
    """Compute average value of each loss component across the validation set."""
    parts = {
        "mask_bce": 0.0, "mask_dice": 0.0,
        "height_l1": 0.0, "height_sobel": 0.0, "height_iou": 0.0,
    }
    n = 0
    with torch.no_grad():
        for rgb, height in loader:
            mask_logits, height_pred, _ = model(rgb, n_iters=n_iters)
            target_mask = (height > 0).float()
            bs = rgb.size(0)
            n += bs

            # Mask BCE
            bce = F.binary_cross_entropy_with_logits(mask_logits, target_mask)
            parts["mask_bce"] += bce.item() * bs

            # Dice
            mp = torch.sigmoid(mask_logits)
            num = 2.0 * (mp * target_mask).sum()
            den = mp.sum() + target_mask.sum() + 1e-6
            parts["mask_dice"] += (1.0 - num / den).item() * bs

            # Height L1 (building pixels only)
            tm_sum = target_mask.sum().clamp(min=1.0)
            l1 = (target_mask * (height_pred - height).abs()).sum() / tm_sum
            parts["height_l1"] += l1.item() * bs

            # Sobel gradient loss
            kx = torch.tensor([[-1, 0, 1], [-2, 0, 2], [-1, 0, 1]], dtype=torch.float32).view(1, 1, 3, 3)
            ky = kx.transpose(2, 3)
            grad_pred_x = F.conv2d(height_pred, kx, padding=1)
            grad_pred_y = F.conv2d(height_pred, ky, padding=1)
            grad_tgt_x  = F.conv2d(height,      kx, padding=1)
            grad_tgt_y  = F.conv2d(height,      ky, padding=1)
            sob = ((grad_pred_x - grad_tgt_x).abs().mean()
                 + (grad_pred_y - grad_tgt_y).abs().mean())
            parts["height_sobel"] += grad_weight * sob.item() * bs

            # Height IoU (overlap of predicted-positive vs target-positive volumes)
            pred_pos = torch.clamp(height_pred, min=0.0)
            inter = torch.minimum(pred_pos, height).sum()
            union = torch.maximum(pred_pos, height).sum() + 1e-6
            parts["height_iou"] += (1.0 - inter / union).item() * bs

    nn = max(n, 1)
    return {k: v / nn for k, v in parts.items()}


def _layer_grad_norms(model, batch, n_iters: int, grad_weight: float = 0.2) -> dict[str, float]:
    """Run one backward pass on a single batch; return ‖grad‖₂ per parameter group."""
    model.train()
    rgb, height = batch
    mask_logits, height_pred, _ = model(rgb, n_iters=n_iters)
    target_mask = (height > 0).float()
    tm_sum = target_mask.sum().clamp(min=1.0)
    bce = F.binary_cross_entropy_with_logits(mask_logits, target_mask)
    l1  = (target_mask * (height_pred - height).abs()).sum() / tm_sum
    loss = bce + l1
    model.zero_grad(set_to_none=True)
    loss.backward()

    norms: dict[str, float] = {}
    for name, p in model.named_parameters():
        if p.grad is None:
            norms[name] = 0.0
        else:
            norms[name] = float(p.grad.norm().item())
    model.eval()
    return norms


def _summarise_grad_norms(norms: dict[str, float]) -> dict[str, Any]:
    """Bucket grad norms by top-level module to keep the summary readable."""
    buckets: dict[str, list[float]] = {}
    for name, val in norms.items():
        bucket = name.split(".")[0]
        buckets.setdefault(bucket, []).append(val)
    summary = {}
    for bucket, vals in buckets.items():
        a = np.array(vals)
        summary[bucket] = {
            "n_params": int(a.size),
            "n_zero": int((a == 0).sum()),
            "mean": float(a.mean()),
            "max":  float(a.max()),
            "p50":  float(np.median(a)),
        }
    return summary


def _render_examples(
    model, val_loader, n_iters: int, out_dir: Path, n_samples: int = 6,
) -> list[Path]:
    """Save composite PNGs of (RGB / GT / pred-mask / pred-height / error) for
    a spread of validation tiles: best, median, worst by per-tile MAE.

    Returns list of saved paths.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        from PIL import Image
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.cm as cm
    except Exception as e:
        print(f"  (matplotlib not available — skipping render: {e})")
        return []

    # Pass 1: collect per-tile (rgb, target, mask_pred, h_pred, mae)
    samples = []
    with torch.no_grad():
        for rgb, height in val_loader:
            mask_logits, height_pred, _ = model(rgb, n_iters=n_iters)
            mask_prob = torch.sigmoid(mask_logits)
            gated = torch.clamp(height_pred, min=0.0) * (mask_prob > 0.3).float()
            target_mask = (height > 0).float()
            for b in range(rgb.size(0)):
                tm = target_mask[b].sum().clamp(min=1.0)
                mae = ((target_mask[b] * (gated[b] - height[b]).abs()).sum() / tm).item()
                samples.append({
                    "rgb": rgb[b].cpu().numpy(),
                    "target": height[b, 0].cpu().numpy(),
                    "pred_mask": mask_prob[b, 0].cpu().numpy(),
                    "pred_height": height_pred[b, 0].cpu().numpy(),
                    "gated_height": gated[b, 0].cpu().numpy(),
                    "mae": mae,
                })

    if not samples:
        return []

    # Sort by MAE; pick spread: 2 best + 2 median + 2 worst
    samples.sort(key=lambda s: s["mae"])
    n = len(samples)
    indices: list[int] = []
    if n >= 6:
        indices = [0, 1, n // 2 - 1, n // 2, n - 2, n - 1]
    else:
        indices = list(range(n))[:n_samples]
    chosen = [samples[i] for i in indices]

    saved: list[Path] = []
    for rank, s in enumerate(chosen, start=1):
        rgb = s["rgb"].transpose(1, 2, 0)  # (H, W, 3) in [0, 1]
        gt = s["target"]
        pm = s["pred_mask"]
        ph = s["gated_height"]
        err = ph - gt  # signed error: + means overpredict, - underpredict

        vmax_h = float(max(gt.max(), ph.max(), 1.0))
        vmin_err = float(np.percentile(err, 1))
        vmax_err = float(np.percentile(err, 99))
        ev = max(abs(vmin_err), abs(vmax_err), 0.5)

        fig, axes = plt.subplots(1, 5, figsize=(20, 4))
        axes[0].imshow(np.clip(rgb, 0, 1));            axes[0].set_title("RGB")
        axes[1].imshow(gt, cmap="viridis", vmin=0, vmax=vmax_h)
        axes[1].set_title(f"GT height (max={gt.max():.1f}m)")
        axes[2].imshow(pm, cmap="gray",     vmin=0, vmax=1)
        axes[2].set_title(f"Pred mask prob (mean={pm.mean():.2f})")
        axes[3].imshow(ph, cmap="viridis", vmin=0, vmax=vmax_h)
        axes[3].set_title(f"Pred height (max={ph.max():.1f}m)")
        im = axes[4].imshow(err, cmap="RdBu_r", vmin=-ev, vmax=ev)
        axes[4].set_title(f"Pred − GT (MAE={s['mae']:.2f}m)")
        for ax in axes:
            ax.set_xticks([]); ax.set_yticks([])
        fig.colorbar(im, ax=axes[4], shrink=0.7)
        fig.suptitle(f"sample rank {rank}/{len(chosen)}  per-tile MAE = {s['mae']:.2f} m",
                     fontsize=10)
        fig.tight_layout()

        path = out_dir / f"sample_{rank:02d}_mae{s['mae']:05.2f}.png"
        fig.savefig(path, dpi=80, bbox_inches="tight")
        plt.close(fig)
        saved.append(path)

    return saved


def _activation_stats(model, batch, n_iters: int) -> dict[str, dict[str, float]]:
    """Hook every nn.Module child of the decoder, record output stats."""
    stats: dict[str, dict[str, float]] = {}
    handles = []

    def _hook(name):
        def _fn(_module, _inp, out):
            if not isinstance(out, torch.Tensor):
                return
            x = out.detach().float()
            stats[name] = {
                "mean": float(x.mean()),
                "std":  float(x.std()),
                "frac_zero":      float((x.abs() < 1e-6).float().mean()),
                "frac_saturated": float((x.abs() > 6.0).float().mean()),
            }
        return _fn

    # Hook all named submodules but skip the very leaves (params) — module-level only.
    for name, module in model.named_modules():
        if name == "" or any(p is module for _, p in model.named_modules() if name and _.startswith(name + ".")):
            continue
        # Only register on direct children (depth 1 or 2) to keep output small
        depth = name.count(".")
        if depth <= 2:
            handles.append(module.register_forward_hook(_hook(name)))

    rgb, _ = batch
    with torch.no_grad():
        model(rgb, n_iters=n_iters)
    for h in handles:
        h.remove()
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--arch", default="v3")
    ap.add_argument("--n-iters", type=int, default=1)
    ap.add_argument("--tile-size", type=int, default=64)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--render", default=None,
                    help="Directory to save best/median/worst sample PNGs")
    ap.add_argument("--n-samples", type=int, default=6)
    args = ap.parse_args()

    ckpt, tdir = _resolve_paths(args.checkpoint, args.tiles)
    if not ckpt.exists():
        raise SystemExit(f"checkpoint not found: {ckpt}")

    print(f"Checkpoint: {ckpt}")
    print(f"Tiles: {tdir}")

    model = _load_model(ckpt, args.arch, args.n_iters)
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Model: {args.arch}, {n_params:,} params")

    from tools.ml.data import make_height_loaders
    tile_paths = sorted(tdir.glob("*.npz"))
    if not tile_paths:
        raise SystemExit(f"no tiles in {tdir}")
    train_loader, val_loader, split = make_height_loaders(
        tile_paths, tile_size=args.tile_size,
        batch_size=args.batch_size, seed=args.seed, num_workers=0,
    )
    print(f"Split: {split['n_train']} train / {split['n_val']} val")

    print("\n[1/4] per-sample MAE on validation set …")
    maes, gt_means = _per_sample_mae(model, val_loader, args.n_iters)
    print(f"      n={maes.size}  mean MAE={maes.mean():.2f}m  median={np.median(maes):.2f}m")
    print(f"      worst 25%: {np.percentile(maes, 75):.2f}m  best 25%: {np.percentile(maes, 25):.2f}m")
    if maes.size > 0:
        # Correlation: do tiles with taller buildings have higher error?
        if gt_means.std() > 0 and maes.std() > 0:
            r = float(np.corrcoef(maes, gt_means)[0, 1])
            print(f"      pearson(MAE, target-mean-height) = {r:+.3f}")

    print("\n[2/4] loss-component decomposition (mean over val set) …")
    parts = _loss_decomposition(model, val_loader, args.n_iters)
    total = sum(parts.values()) or 1.0
    for k, v in sorted(parts.items(), key=lambda kv: -kv[1]):
        print(f"      {k:<14s} {v:>7.3f}   ({v/total*100:>5.1f}% of total)")

    # Pull one batch for grad-norm + activation-stats analysis
    one_batch = next(iter(val_loader))

    print("\n[3/4] layer-wise gradient norms (single backward on val batch) …")
    norms = _layer_grad_norms(model, one_batch, args.n_iters)
    summary = _summarise_grad_norms(norms)
    for bucket, s in summary.items():
        flag = " ⚠ DEAD" if s["max"] < 1e-6 else (" ⚠ HUGE" if s["max"] > 100 else "")
        print(f"      {bucket:<14s}  n={s['n_params']:<4d} zero={s['n_zero']:<3d} "
              f"p50={s['p50']:.2e} mean={s['mean']:.2e} max={s['max']:.2e}{flag}")

    print("\n[4/4] activation statistics (forward pass on val batch) …")
    acts = _activation_stats(model, one_batch, args.n_iters)
    # Show only modules where we have something interesting
    interesting = [(n, s) for n, s in acts.items()
                   if s["frac_saturated"] > 0.5 or s["frac_zero"] > 0.95
                   or s["std"] < 1e-4 or s["std"] > 100]
    if interesting:
        print(f"      {len(interesting)} modules look unusual:")
        for n, s in interesting[:20]:
            tag = ""
            if s["frac_saturated"] > 0.5: tag += " saturated"
            if s["frac_zero"] > 0.95:     tag += " mostly_zero"
            if s["std"] < 1e-4:           tag += " collapsed"
            if s["std"] > 100:            tag += " exploded"
            print(f"        {n:<35s} mean={s['mean']:+.2f} std={s['std']:.2f} "
                  f"zero%={s['frac_zero']*100:.0f}{tag}")
    else:
        print("      no obviously broken activation layers detected (depth ≤ 2)")

    if args.render:
        out_dir = Path(args.render)
        if not out_dir.is_absolute():
            out_dir = Path(__file__).resolve().parents[2] / args.render
        print(f"\n[5/5] rendering {args.n_samples} sample tiles → {out_dir} …")
        saved = _render_examples(model, val_loader, args.n_iters, out_dir, args.n_samples)
        for p in saved:
            print(f"        {p}")

    print("\nDone.")


if __name__ == "__main__":
    main()
