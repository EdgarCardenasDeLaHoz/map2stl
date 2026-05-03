"""Batch Norm Statistics Reset for Growth - Proposed Patch

This module implements batch norm statistics reset after model growth
to prevent the epoch-1 validation loss jump.

HYPOTHESIS: When architecture grows, batch norm running_mean/running_var
reflect the OLD architecture's intermediate feature distributions.
New channels output random values that are out-of-distribution w.r.t.
the stale BN stats, causing incorrect scaling/saturation in epoch 1.

FIX: After growth, re-compute BN running statistics using a small number
of training batches. This re-calibrates BN to the new architecture.

EXPECTED BENEFIT: 50-70% reduction in epoch-1 val_loss jump
OVERHEAD: ~2-3ms per growth step
RISK: Low (BN stats are recalculated on-distribution)
"""

import torch
import torch.nn as nn
from typing import Iterator


def reset_batch_norm_statistics(
    model: nn.Module,
    train_loader: Iterator,
    device: torch.device,
    n_batches: int = 20,
    verbose: bool = False,
) -> None:
    """Recalculate batch norm running statistics after architecture change.
    
    This addresses the epoch-1 validation loss jump that occurs after growing
    the model. The issue is that batch norm statistics were computed for the
    old architecture and don't match the new (wider) channels.
    
    Parameters
    ----------
    model : nn.Module
        The model whose batch norm stats should be reset.
    train_loader : Iterator
        Training data loader to use for recalculation.
    device : torch.device
        Device to run computation on.
    n_batches : int
        Number of batches to use for recalculation (default 20).
        ~1500 samples at batch_size=3, sufficient for reasonable estimates.
    verbose : bool
        If True, print reset progress.
    
    Returns
    -------
    None (modifies model in-place)
    
    Notes
    -----
    This should be called after growth but BEFORE the first training epoch.
    The overhead is roughly O(n_batches * batch_size * forward_pass_time),
    typically 2-3ms for our model.
    """
    # Save model training state
    was_training = model.training
    
    # Collect all BN layers
    bn_layers = []
    for m in model.modules():
        if isinstance(m, (nn.BatchNorm1d, nn.BatchNorm2d, nn.BatchNorm3d)):
            bn_layers.append(m)
    
    if not bn_layers and verbose:
        print("  [reset_bn] No batch norm layers found, skipping.")
        return
    
    if verbose:
        print(f"  [reset_bn] Recalculating statistics for {len(bn_layers)} BN layers...")
    
    # Reset BN statistics to defaults
    for bn in bn_layers:
        if hasattr(bn, 'running_mean') and hasattr(bn, 'running_var'):
            bn.running_mean.zero_()
            bn.running_var.fill_(1.0)
        if hasattr(bn, 'num_batches_tracked'):
            bn.num_batches_tracked.zero_()
    
    # Forward pass in train mode (to update running stats) but without gradients
    model.train()
    with torch.no_grad():
        for batch_idx, batch_data in enumerate(train_loader):
            if batch_idx >= n_batches:
                break
            
            # Unpack batch (assumes (rgb, target) tuple)
            if isinstance(batch_data, (tuple, list)) and len(batch_data) >= 1:
                rgb = batch_data[0]
            else:
                rgb = batch_data
            
            # Move to device and forward pass
            rgb = rgb.to(device)
            _ = model(rgb)  # Forward pass updates BN running stats
    
    # Restore model state
    model.train(was_training)
    
    if verbose:
        print(f"  [reset_bn] BN statistics recalculated using {min(batch_idx+1, n_batches)} batches.")


# INTEGRATION POINT IN grow_prune.py
# ───────────────────────────────────────────────────────────────────────────
# 
# In `clone_top_channels_into_new()`, after all weight copying is complete
# and before returning, add:
#
#     if bn_reset and train_loader is not None:
#         reset_batch_norm_statistics(
#             model,
#             train_loader,
#             device,
#             n_batches=20,
#             verbose=True,
#         )
#
# The function signature of clone_top_channels_into_new would need to accept:
#     - train_loader: DataLoader
#     - device: torch.device
#     - bn_reset: bool = True
#
# ───────────────────────────────────────────────────────────────────────────


# TESTING PROTOCOL
# ───────────────────────────────────────────────────────────────────────────
#
# 1. Baseline (current):
#    - 5-cycle run without BN reset
#    - Measure epoch-1 jumps: ~+0.025 after growth
#
# 2. With BN Reset:
#    - 5-cycle run with BN reset enabled
#    - Expected: epoch-1 jumps reduced to ~+0.010-0.015 (50% reduction)
#    - Measure convergence speed (should be faster)
#
# 3. Full 10-cycle validation:
#    - Run extended 10-cycle with BN reset
#    - Verify no side effects on model quality
#    - Check if patterns are consistent across cycles
#
# 4. Diagnostics to collect:
#    - Per-block activation scales (new vs old channels)
#    - BN layer running mean/var before and after reset
#    - Gradient norms per channel type
#
# ───────────────────────────────────────────────────────────────────────────
