"""tools.ml.gradient_analysis -- Hook-based layer contribution analysis.

Computes per-module contribution scores from activation and gradient statistics
for a trained model checkpoint.

Usage:
  python -m tools.ml.gradient_analysis \
      --task height \
      --arch v2 \
      --model models/roofnet_height_v3.pt \
      --tile-dir cache/height_tiles_osm \
      --batches 8
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

from tools.ml.data import make_height_loaders, make_roof_loaders
from tools.ml.models import build_model


@dataclass
class _Stats:
    activation_l1: float = 0.0
    grad_l1: float = 0.0
    score_sum: float = 0.0
    n: int = 0


def _is_tracked_module(name: str, module: torch.nn.Module) -> bool:
    # Track primary learnable blocks; skip container modules.
    if isinstance(module, (torch.nn.Conv2d, torch.nn.Linear, torch.nn.BatchNorm2d)):
        return True
    return any(
        key in name
        for key in ("backbone", "fpn", "height_head", "mask_head", "shape_head")
    )


def _detect_arch_from_checkpoint(ckpt_path: Path, default_arch: str = "v2") -> str:
    if not ckpt_path.exists():
        return default_arch
    state = torch.load(str(ckpt_path), map_location="cpu", weights_only=False)
    sd = state.get("model_state_dict", state) if isinstance(state, dict) else {}
    keys = list(sd.keys()) if isinstance(sd, dict) else []
    if any(k.startswith("mask_head.") for k in keys):
        return "v3"
    if any(k.startswith("height_refine.") for k in keys):
        return "v31"
    return default_arch


def analyze_height_contributions(
    model_path: str,
    tile_dir: str,
    arch: str = "auto",
    device: str = "cpu",
    batch_size: int = 4,
    batches: int = 8,
) -> dict:
    dev = torch.device(device)
    ckpt = Path(model_path)

    if arch == "auto":
        arch = _detect_arch_from_checkpoint(ckpt, default_arch="v2")

    model = build_model(task="height", arch=arch, checkpoint=str(ckpt), device=str(dev))
    model.train()  # keep grads enabled on all modules

    strm2stl = Path(__file__).resolve().parents[2]
    tile_root = Path(tile_dir)
    if not tile_root.is_absolute():
        tile_root = strm2stl / tile_dir
    tile_paths = sorted(tile_root.glob("*.npz"))
    if not tile_paths:
        raise FileNotFoundError(f"No .npz tiles found in {tile_root}")

    _, val_loader, split = make_height_loaders(tile_paths, batch_size=batch_size)

    stats: dict[str, _Stats] = defaultdict(_Stats)
    handles = []

    def _fwd_hook(name: str):
        def _hook(_module, _inputs, output):
            if isinstance(output, (tuple, list)):
                out = output[0]
            else:
                out = output
            if not torch.is_tensor(out):
                return
            with torch.no_grad():
                stats[name].activation_l1 += float(out.detach().abs().mean().item())
                stats[name].n += 1

            # Capture gradient flowing through this module output.
            if out.requires_grad:
                out.register_hook(_tensor_grad_hook(name))
        return _hook

    def _tensor_grad_hook(name: str):
        def _hook(gout):
            if not torch.is_tensor(gout):
                return gout
            with torch.no_grad():
                g = float(gout.detach().abs().mean().item())
                s = stats[name]
                s.grad_l1 += g
                # contribution proxy: activation magnitude * gradient magnitude
                s.score_sum += g * max(s.activation_l1 / max(s.n, 1), 0.0)
            return gout
        return _hook

    for name, module in model.named_modules():
        if not name:
            continue
        if _is_tracked_module(name, module):
            handles.append(module.register_forward_hook(_fwd_hook(name)))

    seen_batches = 0
    for rgb, height in val_loader:
        rgb = rgb.to(dev)
        height = height.to(dev)
        model.zero_grad(set_to_none=True)

        out = model(rgb)
        if isinstance(out, tuple) and len(out) == 3:
            mask_logits, height_pred, _ = out
            target_mask = (height > 0).float()
            loss = F.l1_loss(height_pred, height) + 0.2 * F.binary_cross_entropy_with_logits(mask_logits, target_mask)
        elif isinstance(out, tuple) and len(out) == 2:
            height_pred, _ = out
            loss = F.l1_loss(height_pred, height)
        else:
            # Defensive fallback for unusual forward signatures
            height_pred = out if torch.is_tensor(out) else out[0]
            loss = F.l1_loss(height_pred, height)

        loss.backward()
        seen_batches += 1
        if seen_batches >= batches:
            break

    for h in handles:
        h.remove()

    rows = []
    for name, st in stats.items():
        n = max(st.n, 1)
        act = st.activation_l1 / n
        grad = st.grad_l1 / n
        score = st.score_sum / n
        rows.append(
            {
                "module": name,
                "activation_l1": act,
                "grad_l1": grad,
                "contribution_score": score,
            }
        )

    rows.sort(key=lambda r: r["contribution_score"], reverse=True)

    return {
        "task": "height",
        "arch": arch,
        "model": str(ckpt),
        "tile_dir": str(tile_root),
        "batches_analyzed": seen_batches,
        "split": split,
        "top_modules": rows[:25],
        "all_modules": rows,
    }


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Hook-based gradient contribution analysis")
    parser.add_argument("--task", choices=["height"], default="height")
    parser.add_argument("--model", required=True)
    parser.add_argument("--tile-dir", default="cache/height_tiles_osm")
    parser.add_argument("--arch", default="auto", help="auto, v2, v3, v31, v3s")
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--batches", type=int, default=8)
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--out", default="output/gradient_contributions.json")
    args = parser.parse_args()

    result = analyze_height_contributions(
        model_path=args.model,
        tile_dir=args.tile_dir,
        arch=args.arch,
        device=args.device,
        batch_size=args.batch_size,
        batches=args.batches,
    )

    strm2stl = Path(__file__).resolve().parents[2]
    out_path = Path(args.out)
    if not out_path.is_absolute():
        out_path = strm2stl / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(result, fh, indent=2)

    print(f"Saved: {out_path}")
    print("Top contributing modules:")
    for row in result["top_modules"][:10]:
        print(
            f"  {row['module']:<45} "
            f"score={row['contribution_score']:.6f} "
            f"act={row['activation_l1']:.6f} grad={row['grad_l1']:.6f}"
        )


if __name__ == "__main__":
    main()
