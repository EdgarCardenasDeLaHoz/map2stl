"""Visual inspector for the Retna_V1 baseline model.

Renders sample tiles (RGB / GT / Pred / Error) and a per-block contribution
panel (activation magnitude × gradient magnitude) into a single PDF.

Usage:
    python -m tools.ml.inspect_retna \\
        --checkpoint models/retna_v1.pt \\
        --tiles cache/height_tiles_combined \\
        --out output/inspector_retna.pdf
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn

_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parents[1]
for _p in (_STRM2STL, _STRM2STL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.ml.models import Retna_V1  # noqa: E402
from tools.ml.train.train_retna import (  # noqa: E402
    HeightTileDataset,
    HEIGHT_NORM_M,
    dice_loss,
)


def _block_contributions(model: Retna_V1, loader, device, batches: int = 4) -> list[dict]:
    """For each block return mean(|activation|), mean(|gradient|), and their product.

    Mirrors `tools/ml/grow_prune.py::block_scores` but returns only summary stats
    (no per-channel breakdown) for plotting.
    """
    model.train()
    n_blocks = len(model.blocks)
    act_sums = [None] * n_blocks
    grad_sums = [None] * n_blocks
    counts = [0] * n_blocks
    handles = []

    def _make_hook(idx):
        def _hook(_m, _inp, out):
            if isinstance(out, (tuple, list)):
                out = out[0]
            if not torch.is_tensor(out):
                return
            with torch.no_grad():
                a = out.detach().abs().mean(dim=(0, 2, 3))
                if act_sums[idx] is None:
                    act_sums[idx] = a.clone()
                else:
                    act_sums[idx] = act_sums[idx] + a
                counts[idx] += 1
            if out.requires_grad:
                def _grad_hook(g, idx=idx):
                    if not torch.is_tensor(g):
                        return g
                    with torch.no_grad():
                        gm = g.detach().abs().mean(dim=(0, 2, 3))
                        if grad_sums[idx] is None:
                            grad_sums[idx] = gm.clone()
                        else:
                            grad_sums[idx] = grad_sums[idx] + gm
                    return g
                out.register_hook(_grad_hook)
        return _hook

    for i, blk in enumerate(model.blocks):
        handles.append(blk.register_forward_hook(_make_hook(i)))

    n = 0
    for rgb, target in loader:
        if n >= batches:
            break
        rgb = rgb.to(device); target = target.to(device)
        pred = model(rgb)
        loss = dice_loss(pred, target)
        model.zero_grad(set_to_none=True)
        loss.backward()
        n += 1

    for h in handles:
        h.remove()

    out = []
    for i in range(n_blocks):
        a = act_sums[i]; g = grad_sums[i]
        a_mean = float((a / max(counts[i], 1)).abs().mean()) if a is not None else 0.0
        g_mean = float((g / max(counts[i], 1)).abs().mean()) if g is not None else 0.0
        n_out = int(model.blocks[i][-2].out_channels)
        out.append({
            "idx": i,
            "n_out": n_out,
            "act": a_mean,
            "grad": g_mean,
            "score": a_mean * g_mean,
        })
    model.eval()
    return out


def _conv_weight_norms(model: Retna_V1) -> list[dict]:
    """Per-block aggregate weight L2 (a static measure of how much capacity
    each block holds, vs the dynamic activation×gradient contribution)."""
    out = []
    for i, blk in enumerate(model.blocks):
        norms = []
        for m in blk.modules():
            if isinstance(m, nn.Conv2d):
                norms.append(float(m.weight.detach().norm().item()))
        out.append({
            "idx": i,
            "weight_l2": float(np.mean(norms)) if norms else 0.0,
            "n_convs": len(norms),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--out", required=True,
                    help="Output PDF path (e.g. output/inspector.pdf)")
    ap.add_argument("--tile-size", type=int, default=128)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--n-samples", type=int, default=20,
                    help="Number of sample tiles to render in the PDF")
    args = ap.parse_args()

    strm2stl = Path(__file__).resolve().parents[2]
    ckpt_path = Path(args.checkpoint)
    if not ckpt_path.is_absolute():
        ckpt_path = strm2stl / args.checkpoint
    tiles = Path(args.tiles)
    if not tiles.is_absolute():
        tiles = strm2stl / args.tiles
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = strm2stl / args.out
    out_path.parent.mkdir(parents=True, exist_ok=True)

    state = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    hidden = state.get("hidden_channels", [8, 8, 8, 8])
    norm_m = state.get("height_norm_m", HEIGHT_NORM_M)
    model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=hidden)
    model.load_state_dict(state["model_state_dict"])
    model.eval()
    n_params = sum(p.numel() for p in model.parameters())
    print(f"Checkpoint: {ckpt_path}")
    print(f"Arch     : Retna_V1, hidden={hidden}, {n_params:,} params")
    print(f"Norm     : heights / {norm_m:.0f} m")
    print(f"Tiles    : {tiles}")

    paths = sorted(tiles.glob("*.npz"))
    n = len(paths)
    n_val = max(1, int(0.15 * n))
    n_train = n - n_val

    full = HeightTileDataset(paths, args.tile_size, augment=False)
    from torch.utils.data import random_split, DataLoader
    _, val_ds = random_split(
        full, [n_train, n_val],
        generator=torch.Generator().manual_seed(args.seed),
    )
    loader = DataLoader(val_ds, batch_size=4, shuffle=False, num_workers=0)
    grad_loader = DataLoader(val_ds, batch_size=4, shuffle=False, num_workers=0)

    # Predictions
    samples = []
    with torch.no_grad():
        for rgb, target in loader:
            pred = model(rgb)
            for b in range(rgb.size(0)):
                target_m = target[b, 0].numpy() * norm_m
                pred_m = pred[b, 0].numpy() * norm_m
                mask = (target_m > 0)
                if mask.any():
                    mae = float(np.abs(pred_m[mask] - target_m[mask]).mean())
                else:
                    mae = float(np.abs(pred_m).mean())
                samples.append({
                    "rgb": rgb[b].numpy(),
                    "target_m": target_m,
                    "pred_m": pred_m,
                    "mae": mae,
                })
    if not samples:
        print("no val tiles found")
        return

    # Block contributions
    contribs = _block_contributions(model, grad_loader, device="cpu", batches=4)
    weights = _conv_weight_norms(model)

    # Render to a single PDF using PdfPages
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.backends.backend_pdf import PdfPages

    samples.sort(key=lambda s: s["mae"])
    nn_ = len(samples)
    n_show = min(args.n_samples, nn_)
    if n_show < nn_:
        # Spread the chosen samples evenly across the MAE-sorted list
        indices = np.linspace(0, nn_ - 1, n_show).round().astype(int).tolist()
        # Always keep best (rank 0) and worst (rank nn_-1)
        if 0 not in indices:
            indices[0] = 0
        if nn_ - 1 not in indices:
            indices[-1] = nn_ - 1
        # De-dupe while preserving order
        seen = set(); chosen_idx = []
        for i in indices:
            if i not in seen:
                seen.add(i); chosen_idx.append(i)
        chosen = [samples[i] for i in chosen_idx]
    else:
        chosen = samples

    with PdfPages(str(out_path)) as pdf:
        # ── Page 1: title + arch summary + per-block contribution chart ─────
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle(
            f"Retna_V1 inspection — {ckpt_path.name}",
            fontsize=14, fontweight="bold",
        )
        gs = fig.add_gridspec(3, 2, height_ratios=[1.2, 1.2, 1])

        ax_summary = fig.add_subplot(gs[0, :])
        ax_summary.axis("off")
        all_mae = np.array([s["mae"] for s in samples])
        summary = (
            f"Architecture: hidden_channels = {hidden}    {n_params:,} params\n"
            f"Height norm:  {norm_m:.0f} m   (model output ∈ [0,1] → ×{norm_m:.0f} m)\n"
            f"Val tiles:    {len(samples)} (showing {len(chosen)} samples)\n"
            f"\n"
            f"Per-tile MAE:    min={all_mae.min():.2f}m    median={np.median(all_mae):.2f}m    "
            f"mean={all_mae.mean():.2f}m    max={all_mae.max():.2f}m\n"
        )
        ax_summary.text(0.02, 0.95, summary, fontsize=10, va="top",
                        family="monospace")

        # Per-block contribution bar chart (act × grad)
        ax_contrib = fig.add_subplot(gs[1, 0])
        idxs = [c["idx"] for c in contribs]
        scores = np.array([c["score"] for c in contribs], dtype=float)
        scores_norm = scores / max(scores.max(), 1e-12)
        bars = ax_contrib.bar(idxs, scores_norm, color="#4a7ec7")
        for b, s, c in zip(bars, scores, contribs):
            ax_contrib.text(b.get_x() + b.get_width() / 2, b.get_height() + 0.02,
                            f"{s:.2e}\n(C={c['n_out']})",
                            ha="center", va="bottom", fontsize=8)
        ax_contrib.set_xticks(idxs)
        ax_contrib.set_xlabel("block index")
        ax_contrib.set_ylabel("normalized contribution")
        ax_contrib.set_ylim(0, 1.25)
        ax_contrib.set_title("Per-block contribution (mean|act| × mean|grad|)")

        # Per-block weight L2 (static capacity)
        ax_weight = fig.add_subplot(gs[1, 1])
        wl2 = np.array([w["weight_l2"] for w in weights])
        wl2_norm = wl2 / max(wl2.max(), 1e-12)
        ax_weight.bar([w["idx"] for w in weights], wl2_norm, color="#c77a4a")
        for i, (w, wn) in enumerate(zip(weights, wl2_norm)):
            ax_weight.text(i, wn + 0.02, f"L2={w['weight_l2']:.2f}",
                           ha="center", va="bottom", fontsize=8)
        ax_weight.set_xticks([w["idx"] for w in weights])
        ax_weight.set_xlabel("block index")
        ax_weight.set_ylabel("normalized weight L2")
        ax_weight.set_ylim(0, 1.25)
        ax_weight.set_title("Per-block weight L2 (static capacity)")

        # MAE histogram
        ax_hist = fig.add_subplot(gs[2, :])
        ax_hist.hist(all_mae, bins=min(20, len(all_mae)), color="#7a9ca8",
                     edgecolor="black")
        ax_hist.axvline(all_mae.mean(), color="red", linestyle="--",
                        label=f"mean {all_mae.mean():.2f}m")
        ax_hist.axvline(np.median(all_mae), color="orange", linestyle="--",
                        label=f"median {np.median(all_mae):.2f}m")
        ax_hist.set_xlabel("per-tile MAE (m)")
        ax_hist.set_ylabel("count")
        ax_hist.set_title("Per-tile MAE distribution (val set)")
        ax_hist.legend(fontsize=9)

        fig.tight_layout(rect=(0, 0, 1, 0.96))
        pdf.savefig(fig); plt.close(fig)

        # ── One page per sample: RGB / GT / Pred / Error ────────────────────
        for rank, s in enumerate(chosen, 1):
            rgb = s["rgb"].transpose(1, 2, 0)
            gt = s["target_m"]
            pr = s["pred_m"]
            err = pr - gt
            vmax = float(max(gt.max(), pr.max(), 1.0))
            ev = float(max(abs(err.min()), abs(err.max()), 1.0))

            fig, axes = plt.subplots(1, 4, figsize=(16, 4))
            axes[0].imshow(np.clip(rgb, 0, 1)); axes[0].set_title("RGB")
            axes[1].imshow(gt, cmap="viridis", vmin=0, vmax=vmax)
            axes[1].set_title(f"GT height (max={gt.max():.1f}m)")
            axes[2].imshow(pr, cmap="viridis", vmin=0, vmax=vmax)
            axes[2].set_title(
                f"Pred height (max={pr.max():.1f}m, mean={pr.mean():.1f}m)")
            im = axes[3].imshow(err, cmap="RdBu_r", vmin=-ev, vmax=ev)
            axes[3].set_title(f"Pred - GT (MAE={s['mae']:.2f}m)")
            for ax in axes:
                ax.set_xticks([]); ax.set_yticks([])
            fig.colorbar(im, ax=axes[3], shrink=0.7)
            fig.suptitle(
                f"sample {rank}/{len(chosen)}   per-tile MAE = {s['mae']:.2f} m",
                fontsize=10)
            fig.tight_layout()
            pdf.savefig(fig); plt.close(fig)

    print(f"\nWrote {len(chosen) + 1} pages -> {out_path}")


if __name__ == "__main__":
    main()
