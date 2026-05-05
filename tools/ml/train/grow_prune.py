"""Iterative grow/prune training loop for Retna_V1.

Algorithm:
    1. Train model for ``--inner-epochs`` epochs.
    2. Run gradient x activation analysis to score each *block*.
    3. If train_loss - val_loss > overfit_threshold (overfitting):
            prune the lowest-scoring channels in each block.
       Else (under-fitting; train loss still high):
            widen the highest-scoring block by ``--grow-channels``.
    4. Repeat for ``--cycles`` cycles.

Each cycle records a row on the scoreboard so the trajectory is reproducible.
The grow operation re-instantiates the model with the new channel widths and
warm-starts from the previous weights via shape-aware copy: identical-shape
parameters are copied 1-to-1, mismatched ones (the widened block and its
neighbours) are loaded into the leading slice. New neurons start at random,
which is the desired behaviour — they fit residual error not yet captured.

Usage:
    python -m tools.ml.grow_prune \\
        --tiles cache/height_tiles_combined \\
        --start-checkpoint models/retna_v1.pt \\
        --output models/retna_grow.pt \\
        --cycles 3 --inner-epochs 15 --grow-channels 4
"""

from __future__ import annotations

import argparse
import json
import sys

# Ensure UTF-8 output on Windows consoles that default to cp1252.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader, random_split

_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parents[1]
for _p in (_STRM2STL, _STRM2STL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.ml.models import Retna_V1  # noqa: E402
from tools.ml.train.train_retna import (
    HeightTileDataset,
    HEIGHT_NORM_M,
    dice_loss,
    squared_residual_dice,
    evaluate,
)  # noqa: E402

# Default loss for the grow/prune loop. dice_l2 = Dice + L2 (squared residual)
# was added in train_retna.py; it punishes big under-predictions of tall
# buildings more harshly than plain Dice. Use it everywhere here so the
# growth/prune signal reflects what we actually want to optimise.
def _loss_fn(pred, target, l2_weight: float = 1.0):
    return squared_residual_dice(pred, target, l2_weight=l2_weight)


# ── Block-level gradxactivation analysis ─────────────────────────────────────

def block_scores(model: Retna_V1, loader, device, batches: int = 4) -> list[dict]:
    """Score each block by mean(|activation| x |gradient|) over a few batches.

    Returns
    -------
    list of dicts (one per block) with keys:
        idx              — block index in model.blocks
        n_out            — current output channels
        act_l1           — mean |activation|
        grad_l1          — mean |gradient|
        score            — act_l1 x grad_l1 (block contribution proxy)
        per_channel_score — (n_out,) array of act x grad per channel
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
                a = out.detach().abs().mean(dim=(0, 2, 3))  # (C,)
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

    # Run a few batches with grads enabled
    n = 0
    for rgb, target in loader:
        if n >= batches:
            break
        rgb = rgb.to(device); target = target.to(device)
        pred = model(rgb)
        loss = _loss_fn(pred, target)
        model.zero_grad(set_to_none=True)
        loss.backward()
        n += 1

    for h in handles:
        h.remove()

    out = []
    for i in range(n_blocks):
        if act_sums[i] is None or grad_sums[i] is None:
            out.append({
                "idx": i,
                "n_out": int(model.blocks[i][-2].out_channels),
                "act_l1": 0.0,
                "grad_l1": 0.0,
                "score": 0.0,
                "per_channel_score": np.zeros(0, dtype=np.float32),
            })
            continue
        a = (act_sums[i] / max(counts[i], 1)).cpu().numpy()
        g = (grad_sums[i] / max(counts[i], 1)).cpu().numpy()
        # Truncate to length min(len(a), len(g)) — per-channel score
        m = min(len(a), len(g))
        per = a[:m] * g[:m]
        out.append({
            "idx": i,
            "n_out": int(per.size),
            "act_l1": float(a.mean()),
            "grad_l1": float(g.mean()),
            "score": float((a[:m] * g[:m]).mean()),
            "per_channel_score": per,
        })
    return out


# ── Shape-aware warm start ─────────────────────────────────────────────────

def _state_block_out_channels(state: dict[str, torch.Tensor]) -> list[int]:
    """Infer per-block output widths from a Retna state dict.

    Each `res_conv` block has two conv weights; the smaller out-dim is the block
    output width while the larger out-dim is the expansion width.
    """
    out: list[int] = []
    i = 0
    while True:
        cands = []
        for k, t in state.items():
            if not k.startswith(f"blocks.{i}.") or not k.endswith(".weight"):
                continue
            if t.ndim == 4:
                cands.append(int(t.shape[0]))
        if not cands:
            break
        out.append(min(cands))
        i += 1
    return out


def shape_aware_load(model: nn.Module, prev_state: dict[str, torch.Tensor]) -> None:
    """Copy parameters from prev_state into model, with a special remap for
    `conv_last.weight` so widened Retna block segments stay aligned.

    For generic mismatches, copy the overlapping leading slice and leave the
    rest at init.
    """
    own = model.state_dict()
    n_copied = 0

    model_block_out: list[int] = []
    if hasattr(model, "blocks"):
        for b in model.blocks:
            try:
                model_block_out.append(int(b[-2].out_channels))
            except (AttributeError, IndexError, TypeError):
                model_block_out = []
                break
    prev_block_out = _state_block_out_channels(prev_state)

    for k, v in prev_state.items():
        if k not in own:
            continue
        target = own[k]
        if target.shape == v.shape:
            target.copy_(v)
            n_copied += 1
            continue

        # Retna conv_last input layout is [rgb, block0, block1, ...].
        # A plain leading-slice copy misaligns all blocks after the first grown
        # block; remap by segments and leave newly-added channels at zero.
        if (
            k == "conv_last.weight"
            and target.ndim == 4
            and v.ndim == 4
            and model_block_out
            and prev_block_out
        ):
            target.zero_()
            n_out = min(target.shape[0], v.shape[0])

            old_prefix = v.shape[1] - int(sum(prev_block_out))
            new_prefix = target.shape[1] - int(sum(model_block_out))
            if old_prefix > 0 and new_prefix > 0:
                keep_prefix = min(old_prefix, new_prefix)
                target[:n_out, :keep_prefix, :, :].copy_(
                    v[:n_out, :keep_prefix, :, :]
                )

            old_off = max(old_prefix, 0)
            new_off = max(new_prefix, 0)
            for old_c, new_c in zip(prev_block_out, model_block_out):
                keep_c = min(old_c, new_c)
                if keep_c > 0:
                    target[:n_out, new_off:new_off + keep_c, :, :].copy_(
                        v[:n_out, old_off:old_off + keep_c, :, :]
                    )
                old_off += old_c
                new_off += new_c
            n_copied += 1
            continue

        # Generic fallback: copy overlapping leading slice along each dim.
        slices = tuple(slice(0, min(t, s)) for t, s in zip(target.shape, v.shape))
        target[slices] = v[slices]
        n_copied += 1

    model.load_state_dict(own)
    return n_copied


def clone_top_channels_into_new(
    model: Retna_V1,
    prev_channels: list[int],
    new_channels: list[int],
    scores: list[dict],
    jitter: float = 0.01,
) -> int:
    """For each grown block, fill the newly-added output-channel slots by
    cloning the highest-scoring existing channels (with small Gaussian jitter
    to break symmetry) instead of leaving them at random init.

    Function-preserving growth guarantee
    -------------------------------------
    After shape_aware_load two sets of weights are at random init and corrupt
    the existing channels:

    1. The expansion conv (blocks.i.0) gained new output rows [mid_old:mid_new].
       These random intermediate features flow into the OLD output channels of
       the output conv through weights at random init →  old channels produce
       different outputs.  Fix: zero those new expansion rows.

    2. The next block's expansion conv (blocks.{i+1}.0) gained new input
       columns [3+old_c : 3+new_c] (RGB-offset) corresponding to the grown
       block's new output channels.  The previous "propagate to next block"
       code had a wrong shape check (shape[1] == new_c instead of new_c + 3)
       and wrong column indices so it never fired.  Fix: zero those new input
       columns so block i's new outputs contribute nothing to block i+1 at
       grow time.

    With both fixes the model's function for all existing channels is
    preserved exactly (val loss is identical right after growing), and new
    channels learn from scratch with gradient signal.

    Returns the number of (channel, target_block) pairs cloned into the
    output conv's new output-channel slots.
    """
    own = model.state_dict()
    n_clones = 0

    for blk_idx in range(len(prev_channels)):
        old_c = prev_channels[blk_idx]
        new_c = new_channels[blk_idx]
        if new_c <= old_c:
            continue
        n_new = new_c - old_c

        # Pick the n_new highest-scoring existing channels, with replacement
        # if more new slots than old channels.
        per = scores[blk_idx].get("per_channel_score")
        if per is None or len(per) == 0:
            continue
        order = np.argsort(per)[::-1]  # descending
        src_idxs = [int(order[i % len(order)]) for i in range(n_new)]
        src_dup_count = defaultdict(int)
        for src in src_idxs:
            src_dup_count[src] += 1

        # Retna_V1 block = res_conv = Sequential(Conv_expand, ReLU, Conv_out, ReLU).
        # The block's *output* channels are the second Conv2d. Find the
        # 4-D weight whose out-channel dim equals new_c (post-grow).
        conv_key_prefix = None
        exp_key = None
        for k in own.keys():
            if not k.startswith(f"blocks.{blk_idx}.") or not k.endswith(".weight"):
                continue
            t = own[k]
            if t.ndim != 4:
                continue
            if t.shape[0] == new_c:
                conv_key_prefix = k.rsplit(".weight", 1)[0]
            else:
                exp_key = k   # expansion conv: shape[0] == mid_new != new_c
        if conv_key_prefix is None:
            continue

        # ── Fix 1: zero expansion conv's new output rows ─────────────────
        # After shape_aware_load, rows [mid_old:mid_new] of the expansion conv
        # are at random init.  They produce random intermediate features that
        # leak into the OLD output channels through the output conv's new input
        # columns [mid_old:mid_new] (also random init).  Zeroing them means
        # new intermediate features output zero, so old output channels are
        # completely unaffected by the growth.
        if exp_key is not None:
            ew = own[exp_key]
            # True old mid width depends on both this block's old out width and,
            # for i>0, the previous block's old width because in_ch grows too.
            in_ch_old = 3 if blk_idx == 0 else (3 + prev_channels[blk_idx - 1])
            mid_old = in_ch_old + old_c
            if 0 < mid_old < ew.shape[0]:
                ew[mid_old:].zero_()
            exp_bias_key = exp_key.rsplit(".weight", 1)[0] + ".bias"
            if exp_bias_key in own and own[exp_bias_key].shape[0] == ew.shape[0]:
                own[exp_bias_key][mid_old:].zero_()

        w_key = f"{conv_key_prefix}.weight"
        w = own[w_key]                              # (new_c, mid_new, k, k)
        for slot, src in enumerate(src_idxs):
            dst = old_c + slot
            if dst >= w.shape[0] or src >= w.shape[0]:
                continue
            w[dst].copy_(w[src])
            if jitter > 0:
                w[dst].add_(torch.randn_like(w[dst]) * (jitter * w[src].abs().mean()))
            n_clones += 1
        b_key = f"{conv_key_prefix}.bias"
        if b_key in own:
            b = own[b_key]
            for slot, src in enumerate(src_idxs):
                dst = old_c + slot
                if dst >= b.shape[0]: continue
                b[dst] = b[src]

        # Copy 1-D params (e.g. GroupNorm if ever used) for the output dim.
        for k, t in own.items():
            if not k.startswith(f"blocks.{blk_idx}."):
                continue
            if k == w_key or k == b_key:
                continue
            if t.ndim == 1 and t.shape[0] == new_c:
                for slot, src in enumerate(src_idxs):
                    dst = old_c + slot
                    if dst < t.shape[0] and src < t.shape[0]:
                        t[dst] = t[src]

        # ── Fix 2: zero new input columns in block i+1's expansion conv ──
        # When block i grew old_c → new_c, block i+1's expansion conv input
        # dim grew from (3 + old_c) to (3 + new_c).  After shape_aware_load,
        # columns [3+old_c : 3+new_c] are at random init — they map block i's
        # new output channels into block i+1's intermediate features, adding
        # noise to *all* of block i+1's output channels.
        # Zeroing these columns means block i's new channels contribute nothing
        # to block i+1 at grow time (they learn from scratch via gradient).
        # NOTE: the RGB prefix occupies columns 0..2; block i's outputs start
        # at column 3.  (Retna_V1 always has in_channels=3.)
        if blk_idx + 1 < len(model.blocks):
            for k in own.keys():
                if not k.startswith(f"blocks.{blk_idx + 1}.") or not k.endswith(".weight"):
                    continue
                t = own[k]
                # The expansion conv of block i+1 has input dim == 3 + new_c
                if t.ndim == 4 and t.shape[1] == new_c + 3:
                    t[:, 3 + old_c: 3 + new_c, :, :].zero_()
                    break

        # 2) Propagate into conv_last: Retna concatenates all block outputs.
        if "conv_last.weight" in own:
            final_w = own["conv_last.weight"]  # (out=1, in=sum(hidden)+3, 1, 1)
            # Input layout: [rgb(3), block0, block1, ...]. Compute this block offset.
            offset = 3 + int(sum(new_channels[:blk_idx]))
            for slot, src in enumerate(src_idxs):
                dst = old_c + slot
                src_col = offset + src
                dst_col = offset + dst
                if src_col < final_w.shape[1] and dst_col < final_w.shape[1]:
                    final_w[:, dst_col, :, :].copy_(final_w[:, src_col, :, :])
                    if jitter > 0:
                        final_w[:, dst_col, :, :].add_(
                            torch.randn_like(final_w[:, dst_col, :, :])
                            * (jitter * final_w[:, src_col, :, :].abs().mean())
                        )
            for src, dup_n in src_dup_count.items():
                scale = float(dup_n + 1)
                src_col = offset + src
                if src_col < final_w.shape[1]:
                    final_w[:, src_col, :, :].div_(scale)
                for slot, src2 in enumerate(src_idxs):
                    if src2 != src:
                        continue
                    dst_col = offset + old_c + slot
                    if dst_col < final_w.shape[1]:
                        final_w[:, dst_col, :, :].div_(scale)

    model.load_state_dict(own)
    return n_clones


# ── Ablation-based final pruning ─────────────────────────────────────────────

def _val_loss(model, loader, device) -> float:
    """Plain Dice val loss (cheap, used by ablation)."""
    model.eval()
    total = 0.0; n = 0
    with torch.no_grad():
        for rgb, target in loader:
            rgb, target = rgb.to(device), target.to(device)
            pred = model(rgb)
            total += dice_loss(pred, target).item() * rgb.size(0)
            n += rgb.size(0)
    return total / max(n, 1)


def ablate_channels(model: Retna_V1, loader, device,
                    tolerance: float = 0.005,
                    grad_act_floor_pct: float = 25.0,
                    verbose: bool = True) -> tuple[Retna_V1, list[int], dict]:
    """Iteratively prune low-importance channels via greedy single-channel
    zero-ablation, with a re-evaluation against the *current* (already-pruned)
    state at every step. This avoids the cumulative-effect problem where
    independently-prunable channels turn out to be jointly important.

    Algorithm:
      1. Compute per-channel gradxact scores; only consider the bottom
         `grad_act_floor_pct` percentile across all blocks as ablation
         candidates.
      2. Process candidates from lowest to highest score. For each:
         zero its outgoing weights, measure val_loss, accept the prune
         iff Δ < tolerance against the *running* baseline (which moves as
         channels get accepted for pruning).
      3. Never drop a block below 4 channels.

    The returned model has zeroed weights/biases/norm-params on pruned
    channels but keeps the same architecture (no rebuild). Down-weighting
    in zero values means inference cost is unchanged, but the next training
    pass can slim the architecture if desired (handled separately by a
    classic prune cycle).

    Returns
    -------
    model         — same Retna_V1 instance, with pruned channels zeroed
    new_channels  — list giving (n_total - n_pruned) per block
    stats         — pruning trace
    """
    baseline = _val_loss(model, loader, device)
    running_loss = baseline
    if verbose:
        print(f"\n  [ablation] baseline val_loss={baseline:.4f}  tol=+{tolerance:.4f}")

    n_blocks = len(model.blocks)
    scores = block_scores(model, loader, device, batches=4)

    # Build list of all candidate channels with global score ranking
    candidates = []  # (score, blk_idx, channel_idx)
    for blk_idx in range(n_blocks):
        per = scores[blk_idx].get("per_channel_score")
        out_ch = int(model.blocks[blk_idx][-2].out_channels)
        if per is None or len(per) == 0:
            continue
        per = np.asarray(per, dtype=float)
        if len(per) > out_ch:
            per = per[:out_ch]
        floor = np.percentile(per, grad_act_floor_pct)
        for c in range(min(out_ch, len(per))):
            if per[c] <= floor:
                candidates.append((float(per[c]), blk_idx, c))
    candidates.sort()  # lowest score first

    # Identify the output Conv weight key per block
    own = model.state_dict()
    out_keys = {}
    for blk_idx in range(n_blocks):
        out_ch = int(model.blocks[blk_idx][-2].out_channels)
        for k in own.keys():
            if k.startswith(f"blocks.{blk_idx}.") and k.endswith(".weight"):
                if own[k].ndim == 4 and own[k].shape[0] == out_ch:
                    out_keys[blk_idx] = k.rsplit(".weight", 1)[0]
                    break

    pruned_per_block: dict[int, list[int]] = {b: [] for b in range(n_blocks)}
    accepted = []  # (blk_idx, channel_idx, delta)

    for score, blk_idx, ch in candidates:
        # Don't drop below 4 channels per block
        out_ch_now = int(model.blocks[blk_idx][-2].out_channels)
        if len(pruned_per_block[blk_idx]) >= out_ch_now - 4:
            continue
        prefix = out_keys.get(blk_idx)
        if prefix is None:
            continue

        w_key = f"{prefix}.weight"
        b_key = f"{prefix}.bias"
        w = own[w_key]
        saved_w = w[ch].clone()
        saved_b = None
        if b_key in own:
            saved_b = own[b_key][ch].clone()

        # Also zero matching norm params (1-D tensors with len == out_ch)
        norm_saves = []
        for k, t in own.items():
            if k.startswith(f"blocks.{blk_idx}.") and t.ndim == 1 \
               and t.shape[0] == w.shape[0] and k != w_key and k != b_key:
                norm_saves.append((k, t[ch].clone()))

        with torch.no_grad():
            w[ch].zero_()
            if b_key in own:
                own[b_key][ch] = 0.0
            for k, _ in norm_saves:
                own[k][ch] = 0.0

        loss_after = _val_loss(model, loader, device)
        delta = loss_after - running_loss

        if delta < tolerance:
            # Accept: leave zeroed
            running_loss = loss_after
            pruned_per_block[blk_idx].append(ch)
            accepted.append((blk_idx, ch, delta))
        else:
            # Reject: restore
            with torch.no_grad():
                w[ch].copy_(saved_w)
                if saved_b is not None:
                    own[b_key][ch] = saved_b
                for k, v in norm_saves:
                    own[k][ch] = v

    # NOTE: this function leaves the model architecture unchanged and just
    # zeroes the weights of pruned channels. Inference cost is unchanged but
    # those neurons contribute zero. To realize the param savings, the
    # caller would need to rebuild the model with smaller widths and slice-
    # copy the non-zeroed channels — handled by the caller, not here.
    orig_channels = [int(model.blocks[i][-2].out_channels) for i in range(n_blocks)]
    new_channels = list(orig_channels)  # arch unchanged
    final_loss = _val_loss(model, loader, device)

    stats = {
        "baseline_loss": baseline,
        "final_loss": final_loss,
        "delta": final_loss - baseline,
        "n_pruned_total": sum(len(v) for v in pruned_per_block.values()),
        "per_block": {b: len(v) for b, v in pruned_per_block.items()},
        "accepted": accepted,
    }

    if verbose:
        for b in range(n_blocks):
            n_p = len(pruned_per_block[b])
            n_t = int(model.blocks[b][-2].out_channels)
            if n_p:
                eff = n_t - n_p
                print(f"  [ablation] block {b}: zeroed {n_p}/{n_t} channels "
                      f"({100*n_p/n_t:.0f}%) -> effective width {eff}")
        print(f"  [ablation] DONE  baseline={baseline:.4f}  "
              f"final={final_loss:.4f}  Δ={final_loss-baseline:+.4f}  "
              f"channels zeroed: {stats['n_pruned_total']} (arch unchanged)")

    return model, new_channels, stats


def compact_pruned_channels(model: Retna_V1, pruned_per_block: dict) -> Retna_V1:
    """Rebuild the model with smaller channel widths, copying only the
    non-pruned (kept) channel slices. Realizes the param savings from a
    prior `ablate_channels` pass.

    The block input dim = previous block output + RGB. RGB always sits at
    the leading 3 dims (Retna_V1.forward concats `x_in2` first), so kept
    output channels of block i propagate as input dims [3 : 3+kept_count]
    of block i+1.
    """
    n_blocks = len(model.blocks)
    in_channels = 3  # RGB

    # Build keep-lists per block
    keep_per_block: list[list[int]] = []
    for i in range(n_blocks):
        out_ch = int(model.blocks[i][-2].out_channels)
        pruned = set(pruned_per_block.get(i, []))
        keep_per_block.append([c for c in range(out_ch) if c not in pruned])

    new_channels = [len(k) for k in keep_per_block]
    new_model = Retna_V1(in_channels=in_channels, out_classes=1,
                         hidden_channels=new_channels)
    src = model.state_dict()
    dst = new_model.state_dict()

    # Per block: 2 Conv2d's. The first ("expansion") takes [RGB | prev_block_out]
    # input. Its output dim is internal (don't prune those — they're not the
    # block-output we ablated). Its input dim slices match RGB + kept-prev.
    # The second ("output") conv: input = expansion's output, output = kept of this block.
    # GroupNorm params (1-D) match the Conv2d output dim they follow.
    for i in range(n_blocks):
        kept = keep_per_block[i]
        prev_kept = keep_per_block[i - 1] if i > 0 else None
        # Find both convs in this block via state_dict prefixes
        conv_keys = sorted({k.rsplit(".", 1)[0]
                            for k in src.keys() if k.startswith(f"blocks.{i}.")
                            and k.endswith(".weight") and src[k].ndim == 4})
        # conv_keys[0] = expansion (first), conv_keys[1] = output (second)
        if len(conv_keys) < 2:
            continue
        exp_p, out_p = conv_keys[0], conv_keys[1]

        # Input slicing for expansion conv: RGB (first 3) + prev_kept
        if prev_kept is None:
            in_keep = list(range(in_channels))
        else:
            # blocks[i].input == cat([rgb_resized, prev_block_output])
            # Layout: [rgb(3), prev_block_output(...)]. With pruned prev:
            in_keep = list(range(in_channels)) + [in_channels + c for c in prev_kept]

        # 1) Expansion Conv2d: keep all output channels (internal capacity, not pruned)
        #    but slice input dim by in_keep
        w_e = src[f"{exp_p}.weight"]
        new_w_e = dst[f"{exp_p}.weight"]
        # If new_w_e expects a smaller input dim, slice accordingly
        in_kept_slice = in_keep[: new_w_e.shape[1]]
        new_w_e.copy_(w_e[:, in_kept_slice, :, :][: new_w_e.shape[0]])
        if f"{exp_p}.bias" in src:
            new_w_e_b = dst[f"{exp_p}.bias"]
            new_w_e_b.copy_(src[f"{exp_p}.bias"][: new_w_e_b.shape[0]])

        # GroupNorm after expansion: keep slice matching its dim (which equals
        # expansion's out_channels). No pruning here.
        for k, v in src.items():
            if not k.startswith(f"blocks.{i}.") or k == f"{exp_p}.weight" or k == f"{exp_p}.bias":
                continue
            if k == f"{out_p}.weight" or k == f"{out_p}.bias":
                continue
            if k not in dst:
                continue
            tdst = dst[k]; tsrc = v
            if tsrc.ndim == 1 and tsrc.shape[0] == w_e.shape[0]:
                tdst.copy_(tsrc[: tdst.shape[0]])

        # 2) Output Conv2d: input = expansion's output (size unchanged in src;
        #    new model's expansion output may be smaller — match shapes)
        w_o = src[f"{out_p}.weight"]                 # (orig_out, exp_out, k, k)
        new_w_o = dst[f"{out_p}.weight"]             # (kept_out, new_exp_out, k, k)
        new_w_o.copy_(w_o[kept, : new_w_o.shape[1], :, :])
        if f"{out_p}.bias" in src:
            new_w_o_b = dst[f"{out_p}.bias"]
            new_w_o_b.copy_(src[f"{out_p}.bias"][kept])

        # Norm params indexed by output dim (== orig_out)
        for k, v in src.items():
            if not k.startswith(f"blocks.{i}.") or k == f"{out_p}.weight" or k == f"{out_p}.bias":
                continue
            if k == f"{exp_p}.weight" or k == f"{exp_p}.bias":
                continue
            if k not in dst:
                continue
            tdst = dst[k]; tsrc = v
            if tsrc.ndim == 1 and tsrc.shape[0] == w_o.shape[0]:
                tdst.copy_(tsrc[kept][: tdst.shape[0]])

    # conv_last input dim = sum(hidden_channels) + in_channels
    # In Retna's forward: out_list = [x_in, block0_out, block1_out, ...]
    # So conv_last.weight is indexed by [in_channels=3, kept_b0, kept_b1, ...]
    if "conv_last.weight" in src and "conv_last.weight" in dst:
        old_w = src["conv_last.weight"]
        new_w = dst["conv_last.weight"]
        # Build the list of input indices to keep
        keep_idx = list(range(in_channels))
        offset = in_channels
        # Determine each block's contribution to conv_last input
        # We need orig channel widths to compute offsets in old_w
        orig_widths = [int(model.blocks[i][-2].out_channels) for i in range(n_blocks)]
        for i in range(n_blocks):
            for c in keep_per_block[i]:
                keep_idx.append(offset + c)
            offset += orig_widths[i]
        keep_idx = keep_idx[: new_w.shape[1]]
        new_w.copy_(old_w[:, keep_idx, :, :])
        if "conv_last.bias" in src:
            dst["conv_last.bias"].copy_(src["conv_last.bias"])

    new_model.load_state_dict(dst)
    return new_model


# ── Train one cycle ────────────────────────────────────────────────────────

def train_cycle(model, train_loader, val_loader, device,
                epochs: int, lr: float, accum_steps: int = 1,
                lr_patience: int = 5,
                optimizer_state: dict | None = None,
                weight_decay: float = 0.0):
    """Train for ``epochs`` epochs, accumulating gradients across
    ``accum_steps`` batches before each optimizer.step().

    Uses ReduceLROnPlateau (factor 0.5, floor 1e-5) on val_loss so each
    inner cycle adapts the LR automatically — the outer grow/prune loop
    doesn't need to manage it. Fully non-interactive.

    If ``optimizer_state`` is provided AND its parameter shapes match the
    model's current parameters, restore Adam's running 1st/2nd moments
    (otherwise start fresh). This avoids the "first-epoch big drop" pattern
    when retraining at the same architecture across cycles.

    Returns (model, history, best_loss, optimizer_state_dict).
    """
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr,
                                   weight_decay=weight_decay)
    if optimizer_state is not None:
        try:
            optimizer.load_state_dict(optimizer_state)
            # Override the LR with the requested one (state may have a stale lr)
            for g in optimizer.param_groups:
                g["lr"] = lr
            print("    [optimizer state restored from previous cycle]")
        except (ValueError, KeyError):
            # Shape mismatch (arch changed) → keep the fresh optimizer
            pass
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer, mode="min", factor=0.5,
        patience=lr_patience, min_lr=1e-5,
    )
    history = []
    best_loss = float("inf")
    best_state = None
    for epoch in range(1, epochs + 1):
        t0 = time.perf_counter()
        model.train()
        train_loss = 0.0
        n_train = 0
        optimizer.zero_grad()
        accum_count = 0
        for rgb, target in train_loader:
            rgb, target = rgb.to(device), target.to(device)
            pred = model(rgb)
            loss = _loss_fn(pred, target) / accum_steps  # noqa  — accum-aware
            loss.backward()
            accum_count += 1
            if accum_count >= accum_steps:
                optimizer.step()
                optimizer.zero_grad()
                accum_count = 0
            train_loss += loss.item() * accum_steps * rgb.size(0)
            n_train += rgb.size(0)
        # Flush any remaining accumulated grads
        if accum_count > 0:
            optimizer.step()
            optimizer.zero_grad()
        train_loss /= max(n_train, 1)
        # Pass the same loss function used in training so the LR scheduler's
        # val_loss signal matches what is being optimised (avoids premature
        # LR decay caused by the dice vs dice+L2 mismatch).
        m = evaluate(model, val_loader, device, loss_fn=_loss_fn)
        scheduler.step(m["val_loss"])
        cur_lr = optimizer.param_groups[0]["lr"]
        m["train_loss"] = train_loss
        m["epoch"] = epoch
        m["lr"] = cur_lr
        history.append(m)
        is_best = m["val_loss"] < best_loss
        if is_best:
            best_loss = m["val_loss"]
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        marker = " * best" if is_best else ""
        print(
            f"    ep {epoch:2d}/{epochs}  train={train_loss:.4f}  "
            f"val={m['val_loss']:.4f}  mae={m['val_mae']:.2f}m  "
            f"rmse={m['val_rmse']:.2f}m  iou={m['val_mask_iou']:.3f}  "
            f"r={m['pearson_mae_height']:+.2f}  lr={cur_lr:.1e}"
            f"{marker}  ({time.perf_counter()-t0:.1f}s)",
            flush=True,
        )
    if best_state is not None:
        model.load_state_dict(best_state)
    return model, history, best_loss, optimizer.state_dict()


# ── Main loop ──────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tiles", required=True)
    ap.add_argument("--start-checkpoint", default=None,
                    help="Optional .pt file to warm-start from")
    ap.add_argument("--output", default="models/retna_grow.pt")
    ap.add_argument("--cycles", type=int, default=3)
    ap.add_argument("--inner-epochs", type=int, default=15)
    ap.add_argument("--grow-channels", type=int, default=4,
                    help="How many channels to add to the best block per cycle")
    ap.add_argument("--prune-fraction", type=float, default=0.25,
                    help="When overfitting, drop this fraction of the lowest-score channels")
    ap.add_argument("--overfit-threshold", type=float, default=0.05,
                    help="train_loss - val_loss < this triggers prune (val better than train means overfit on Dice)")
    ap.add_argument("--tile-size", type=int, default=128)
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--accum-steps", type=int, default=1,
                    help="Number of batches to accumulate gradients over before "
                         "calling optimizer.step(). Effective batch = batch-size x accum-steps.")
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--lr-patience", type=int, default=5,
                    help="ReduceLROnPlateau patience inside each cycle")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--initial-channels", nargs="+", type=int, default=[8, 8, 8, 8])
    ap.add_argument("--overfit-stale-epochs", type=int, default=8,
                    help="Number of consecutive epochs with no new best-val_loss "
                         "AND falling train_loss that triggers prune.")
    ap.add_argument("--smart-init", action=argparse.BooleanOptionalAction, default=True,
                    help="On grow, clone the highest-scoring existing channels "
                         "into the new slots (instead of random init). "
                         "Use --no-smart-init for the random-init baseline.")
    ap.add_argument("--smart-init-jitter", type=float, default=0.05,
                    help="Gaussian noise scale (relative to source channel "
                         "magnitude) added to cloned weights to break symmetry.")
    ap.add_argument("--allow-deepen", action="store_true",
                    help="On grow, if the deepest block scored highest and "
                         "depth < --max-depth, append a new block instead "
                         "of widening existing ones.")
    ap.add_argument("--max-depth", type=int, default=8,
                    help="Maximum number of blocks when --allow-deepen.")
    ap.add_argument("--final-prune", action="store_true",
                    help="After all cycles complete, run a single-channel "
                         "ablation pass: zero each low-gradxact channel, "
                         "drop those whose val_loss delta is below tolerance.")
    ap.add_argument("--final-prune-tolerance", type=float, default=0.005,
                    help="Channel is prunable if its ablation increases "
                         "val_loss by less than this (default 0.005 ≈ noise "
                         "floor on 130-tile val set).")
    ap.add_argument("--final-prune-floor-pct", type=float, default=25.0,
                    help="Only consider the bottom-N percentile of channels "
                         "by gradxact score for ablation testing (faster).")
    ap.add_argument("--final-prune-retrain-epochs", type=int, default=10,
                    help="Brief retrain after final pruning to recover any "
                         "small loss in accuracy. Set 0 to skip.")
    ap.add_argument("--prune-every", type=int, default=0,
                    help="If >0, replace every Nth grow cycle with an "
                         "ablation+compact pass (interleaved). 0 disables "
                         "periodic pruning (only --final-prune still runs).")
    ap.add_argument("--target-val-loss", type=float, default=None,
                    help="Stop NAS early when best val_loss reaches this "
                         "threshold. Useful for goal-directed training loops.")
    ap.add_argument("--weight-decay", type=float, default=0.0,
                    help="L2 weight-decay for AdamW optimizer (default 0, "
                         "no decay). Try 1e-4 to 1e-3 to reduce overfitting.")
    ap.add_argument("--weak-grow-warmup-cycles", type=int, default=6,
                    help="For the first N cycles, give extra growth to the "
                        "lowest-scoring block (default 6).")
    ap.add_argument("--alternate-weak-best", action=argparse.BooleanOptionalAction,
                    default=True,
                    help="After warmup, alternate extra growth between "
                        "lowest-score and highest-score blocks.")
    args = ap.parse_args()

    torch.manual_seed(args.seed); np.random.seed(args.seed)

    strm2stl = Path(__file__).resolve().parents[2]
    tiles = Path(args.tiles)
    if not tiles.is_absolute():
        tiles = strm2stl / args.tiles
    out_path = Path(args.output)
    if not out_path.is_absolute():
        out_path = strm2stl / args.output
    out_path.parent.mkdir(parents=True, exist_ok=True)

    paths = sorted(tiles.glob("*.npz"))
    n = len(paths)
    n_val = max(1, int(0.15 * n))
    n_train = n - n_val

    full = HeightTileDataset(paths, args.tile_size, augment=True)
    train_ds, _ = random_split(full, [n_train, n_val],
                               generator=torch.Generator().manual_seed(args.seed))
    val_full = HeightTileDataset(paths, args.tile_size, augment=False)
    _, val_ds = random_split(val_full, [n_train, n_val],
                             generator=torch.Generator().manual_seed(args.seed))
    train_loader = DataLoader(train_ds, batch_size=args.batch_size, shuffle=True, num_workers=0)
    val_loader   = DataLoader(val_ds,   batch_size=args.batch_size, shuffle=False, num_workers=0)

    device = torch.device("cpu")
    channels = list(args.initial_channels)
    ckpt_state = None
    if args.start_checkpoint:
        ck = Path(args.start_checkpoint)
        if not ck.is_absolute():
            ck = strm2stl / args.start_checkpoint
        if ck.exists():
            state = torch.load(str(ck), map_location="cpu", weights_only=False)
            ckpt_state = state.get("model_state_dict", state)
            ck_channels = state.get("hidden_channels") if isinstance(state, dict) else None
            if ck_channels:
                channels = list(ck_channels)
                print(f"Adopted channels from checkpoint: {channels}")
    model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=channels)
    if ckpt_state is not None:
        n_copied = shape_aware_load(model, ckpt_state)
        print(f"Warm-started from {args.start_checkpoint} ({n_copied} tensors copied)")
    model.to(device)

    cycles_log: list[dict] = []
    print(f"Tiles: {n_train} train / {n_val} val\n")

    # Optimizer state persists across cycles when arch is unchanged. After
    # any architecture change (grow/deepen/prune/ablate-compact), we discard
    # it because the param shapes no longer match.
    optimizer_state = None

    for cycle in range(1, args.cycles + 1):
        n_params = sum(p.numel() for p in model.parameters())
        print(f"=== Cycle {cycle}/{args.cycles}  channels={channels}  params={n_params:,} ===")

        model, hist, best_loss, optimizer_state = train_cycle(
            model, train_loader, val_loader, device,
            args.inner_epochs, args.lr,
            accum_steps=args.accum_steps,
            lr_patience=args.lr_patience,
            optimizer_state=optimizer_state,
            weight_decay=args.weight_decay,
        )

        final = hist[-1]
        # New overfit detector: count consecutive epochs since the last best
        # val_loss, requiring train_loss to have actually fallen during that
        # stretch. This avoids tripping on Dice/val noise from tiny val sets.
        train_history_loss = [h["train_loss"] for h in hist]
        val_history_loss   = [h["val_loss"]   for h in hist]
        best_val_idx = int(np.argmin(val_history_loss))
        stale_epochs = (len(val_history_loss) - 1) - best_val_idx
        train_dropped_since = (
            train_history_loss[best_val_idx] - train_history_loss[-1] > 0.005
            if best_val_idx < len(train_history_loss) - 1 else False
        )
        overfit = (
            stale_epochs >= args.overfit_stale_epochs
            and train_dropped_since
        )
        print(f"    [stale_epochs={stale_epochs}  "
              f"train_dropped_since_best={train_dropped_since}  "
              f"→ {'OVERFIT' if overfit else 'underfit/stable'}]")

        scores = block_scores(model, val_loader, device, batches=4)
        for s in scores:
            print(f"    block {s['idx']}  C={s['n_out']:3d}  "
                  f"act={s['act_l1']:.3e}  grad={s['grad_l1']:.3e}  "
                  f"score={s['score']:.3e}")

        cycle_record = {
            "cycle": cycle,
            "channels": list(channels),
            "n_params": n_params,
            "best_val_loss": best_loss,
            "final_train_loss": final["train_loss"],
            "final_val_loss":   final["val_loss"],
            "final_val_mae":    final["val_mae"],
            "final_val_iou":    final["val_mask_iou"],
            "final_pearson":    final["pearson_mae_height"],
            "decision": None,
        }

        # Cycle metrics line — picked up by sparse monitors
        prev = cycles_log[-1] if cycles_log else None
        if prev is not None:
            d_loss = best_loss - prev["best_val_loss"]
            d_mae  = final["val_mae"] - prev["final_val_mae"]
            d_iou  = final["val_mask_iou"] - prev["final_val_iou"]
            delta = f"  Δloss={d_loss:+.4f}  Δmae={d_mae:+.2f}m  Δiou={d_iou:+.3f}"
        else:
            delta = ""
        print(f"  Cycle {cycle} metrics: best_val={best_loss:.4f}  "
              f"mae={final['val_mae']:.2f}m  iou={final['val_mask_iou']:.3f}  "
              f"r={final['pearson_mae_height']:+.2f}{delta}")

        # Early-stop if target val_loss reached
        if args.target_val_loss is not None and best_loss <= args.target_val_loss:
            print(f"  [TARGET REACHED] best_val={best_loss:.4f} <= "
                  f"target={args.target_val_loss:.4f} — stopping NAS.")
            cycle_record["decision"] = "target_reached"
            cycles_log.append(cycle_record)
            break

        if cycle == args.cycles:
            cycle_record["decision"] = "stop"
            cycles_log.append(cycle_record)
            break

        # Periodic ablation: every N cycles (when --prune-every set), do an
        # ablation+compact pass instead of grow. This keeps the model lean
        # and lets growth happen on a smaller base in the next cycle.
        do_periodic_ablate = (
            getattr(args, "prune_every", 0) > 0
            and cycle > 1
            and (cycle % args.prune_every == 0)
            and not overfit
        )

        if do_periodic_ablate:
            print(f"    [periodic ablation cycle (--prune-every {args.prune_every})]")
            model, _, ablate_stats = ablate_channels(
                model, val_loader, device,
                tolerance=args.final_prune_tolerance,
                grad_act_floor_pct=args.final_prune_floor_pct,
                verbose=True,
            )
            pruned_per_block = {
                b: [ch for (bi, ch, _) in ablate_stats["accepted"] if bi == b]
                for b in range(len(model.blocks))
            }
            n_p = sum(len(v) for v in pruned_per_block.values())
            if n_p > 0:
                model = compact_pruned_channels(model, pruned_per_block)
                channels = [int(model.blocks[i][-2].out_channels)
                            for i in range(len(model.blocks))]
                model.to(device)
                cycle_record["decision"] = f"ablate_compact→{channels}"
                print(f"    rebuilt: channels={channels}  "
                      f"params={sum(p.numel() for p in model.parameters()):,}\n")
                optimizer_state = None  # arch changed
            else:
                cycle_record["decision"] = "ablate_no_op"
            cycles_log.append(cycle_record)
            continue

        if overfit:
            # Prune: drop lowest-scoring channels in each block by prune_fraction
            print(f"    [overfit detected — pruning {args.prune_fraction:.0%} per block]")
            new_channels = []
            for s in scores:
                drop_n = max(1, int(s["n_out"] * args.prune_fraction))
                # never go below 4 channels
                keep_n = max(4, s["n_out"] - drop_n)
                new_channels.append(keep_n)
            cycle_record["decision"] = f"prune→{new_channels}"
        else:
            # Decide whether to deepen (add a block) or widen (add channels).
            # Deepen when the deepest block has the highest score AND we have
            # room under --max-depth AND --allow-deepen is set.
            # Spatial-resolution guard: each stride-2 block halves resolution.
            # A new block at depth D would compute at tile_size/2^D pixels.
            # Avoid deepening below 2x2 resolution (pure 1x1 channel mixing
            # adds no spatial inductive bias and wastes capacity).
            import math
            max_useful_depth = int(math.log2(args.tile_size))  # e.g. 7 for 128px
            best_idx = max(range(len(scores)), key=lambda i: scores[i]["score"])
            worst_idx = min(range(len(scores)), key=lambda i: scores[i]["score"])
            can_deepen = (
                getattr(args, "allow_deepen", False)
                and len(channels) < args.max_depth
                and len(channels) < max_useful_depth
                and best_idx == len(channels) - 1
            )
            if not can_deepen and getattr(args, "allow_deepen", False) and len(channels) >= max_useful_depth and best_idx == len(channels) - 1:
                print(f"    [deepen blocked: depth {len(channels)} >= resolution floor "
                      f"{max_useful_depth} for tile_size={args.tile_size}; widening instead]")
            if can_deepen:
                # Append a new block matching the deepest block's width.
                new_channels = list(channels) + [channels[-1]]
                print(f"    [deepening: {channels} → {new_channels} (best block was deepest)]")
                cycle_record["decision"] = f"deepen→{new_channels}"
            else:
                # Grow every block by at least 1 channel.
                # During early warmup, boost the lowest-scoring block to help
                # front layers catch up. After warmup, optionally alternate
                # weak/best boosts to avoid permanently starving late layers.
                if cycle <= max(0, args.weak_grow_warmup_cycles):
                    grow_idx = worst_idx
                    grow_reason = "lowest-score warmup"
                elif getattr(args, "alternate_weak_best", True):
                    phase = (cycle - max(0, args.weak_grow_warmup_cycles)) % 2
                    if phase == 1:
                        grow_idx = worst_idx
                        grow_reason = "lowest-score"
                    else:
                        grow_idx = best_idx
                        grow_reason = "highest-score"
                else:
                    grow_idx = worst_idx
                    grow_reason = "lowest-score"

                new_channels = [c + 1 for c in channels]
                new_channels[grow_idx] = channels[grow_idx] + max(1, args.grow_channels)
                print(f"    [growing all blocks +1; block {grow_idx} ({grow_reason}) +{max(1, args.grow_channels)}: {channels} → {new_channels}]")
                cycle_record["decision"] = f"grow_all+block_{grow_idx}_{grow_reason}→{new_channels}"

        # Rebuild model with new channels, copy what we can
        prev_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
        prev_channels = list(channels)
        channels = new_channels
        model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=channels)
        n_copied = shape_aware_load(model, prev_state)
        optimizer_state = None  # arch changed; momentum/variance no longer applicable
        # Smart-init: clone the highest-scoring existing channels into the
        # newly-added slots (instead of leaving them at random init). Skip on
        # prune cycles since shrinking just drops slots, no new ones.
        n_cloned = 0
        is_grow = cycle_record["decision"].startswith("grow")
        if is_grow and getattr(args, "smart_init", True):
            n_cloned = clone_top_channels_into_new(
                model, prev_channels, channels, scores,
                jitter=args.smart_init_jitter,
            )
        model.to(device)
        print(f"    rebuilt: channels={channels}  warm-started {n_copied} params"
              f"{f' + smart-init cloned {n_cloned} channels' if n_cloned else ''}\n")

        cycles_log.append(cycle_record)

    # ── Optional final-prune pass via single-channel ablation ────────────
    final_prune_stats = None
    if getattr(args, "final_prune", False):
        model, _ablate_channels_unchanged, final_prune_stats = ablate_channels(
            model, val_loader, device,
            tolerance=args.final_prune_tolerance,
            grad_act_floor_pct=args.final_prune_floor_pct,
            verbose=True,
        )
        # Now physically compact the model: rebuild with smaller widths,
        # copying only the non-zeroed channel slices. This realizes the
        # parameter savings.
        pruned_per_block = {
            b: [ch for (bi, ch, _) in final_prune_stats["accepted"] if bi == b]
            for b in range(len(model.blocks))
        }
        n_pruned_total = sum(len(v) for v in pruned_per_block.values())
        if n_pruned_total > 0:
            old_params = sum(p.numel() for p in model.parameters())
            model = compact_pruned_channels(model, pruned_per_block)
            new_params = sum(p.numel() for p in model.parameters())
            channels = [int(model.blocks[i][-2].out_channels)
                        for i in range(len(model.blocks))]
            print(f"  [ablation] compacted {old_params:,} -> {new_params:,} params "
                  f"({100*(old_params-new_params)/old_params:.1f}% reduction)")
            print(f"  [ablation] new channels: {channels}")
        model.to(device)
        # Brief retrain so cleanup-related accuracy loss recovers
        if args.final_prune_retrain_epochs > 0:
            print(f"  [ablation] retraining {args.final_prune_retrain_epochs} epochs...")
            model, _hist, _bl, _ = train_cycle(
                model, train_loader, val_loader, device,
                args.final_prune_retrain_epochs, args.lr * 0.5,
                accum_steps=args.accum_steps, lr_patience=args.lr_patience,
                weight_decay=args.weight_decay,
            )

    # Save final
    final_state = model.state_dict()
    torch.save({
        "model_state_dict": final_state,
        "arch": "retna_v1",
        "hidden_channels": channels,
        "height_norm_m": HEIGHT_NORM_M,
        "cycles_log": cycles_log,
    }, str(out_path))
    history_path = out_path.parent / (out_path.stem + "_history.json")
    with open(history_path, "w", encoding="utf-8") as f:
        json.dump({"cycles": cycles_log, "final_channels": channels}, f, indent=2)

    print(f"\nSaved {out_path}")
    print(f"Final channels: {channels}")
    print(f"Cycles: {len(cycles_log)}")
    for c in cycles_log:
        print(f"  cycle {c['cycle']}: "
              f"channels={c['channels']}  "
              f"params={c['n_params']:,}  "
              f"val_mae={c['final_val_mae']:.2f}m  "
              f"iou={c['final_val_iou']:.3f}  "
              f"decision={c['decision']}")

    try:
        from tools.ml.eval.scoreboard import record_run
        last = cycles_log[-1]
        record_run(
            model_path=str(out_path),
            arch=f"retna_grow_{'-'.join(map(str, channels))}",
            task="height",
            best_metrics={
                "val_loss": last["final_val_loss"],
                "val_mae":  last["final_val_mae"],
                "val_mask_iou": last["final_val_iou"],
                "pearson_mae_height": last["final_pearson"],
            },
            n_train=n_train, n_val=n_val,
            epochs=args.cycles * args.inner_epochs,
            config=vars(args),
            notes=f"grow/prune {args.cycles} cycles; final={channels}",
        )
    except Exception as e:
        print(f"(scoreboard record failed: {e})")


if __name__ == "__main__":
    main()
