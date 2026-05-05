# Growth Degradation: Root Cause Analysis & Solutions

## Problem Statement
After growing model channels, epoch 1 shows a ~1-4% validation loss jump:
- Cycle 2→3: epoch 1 val_loss 0.4044 vs prev cycle best 0.3766 (+2.8%)
- Cycle 3→4: epoch 1 val_loss 0.4192 vs prev cycle best 0.4008 (+4.1%)

The model recovers, but this initial shock suggests the new architecture is poorly conditioned for training.

## Root Cause Analysis

### Hypothesis 1: Batch Norm Statistics Mismatch (HIGH CONFIDENCE — NOT APPLICABLE TO Retna_V1)

> ⚠️ **Applicability Note (updated 2026-05-03):** This hypothesis was written before confirming the active model's architecture.  
> **Retna_V1 uses `res_conv` blocks (`Conv2d → LeakyReLU → Conv2d → LeakyReLU`)** with **no `nn.BatchNorm2d`** at any layer.  
> BN statistics cannot be the cause of epoch-1 jumps in the current training run.  
> See `strm2stl/tools/ml/models.py` — search for `res_conv` to verify.  
> The actual root cause for Retna_V1 growth degradation was **gradient-flow bias toward deep blocks** (see Hypothesis 3 and the mitigation doc), resolved by the lowest-score-block growth warmup strategy in `grow_prune.py`.

**Why it happens (for BN-based architectures):**
- When architecture grows, batch norm running_mean/running_var are NOT adapted
- They reflect statistics of the OLD architecture's intermediate features
- New channels output random values; BN statistics are out-of-distribution
- Result: BN scaling is incorrect, features get saturated/exploded

**Evidence:**
- Worst jumps occur at growth steps, not pruning
- Jump happens at epoch 1 before adaptive training stabilizes
- Pattern suggests systematic scaling issue, not random training noise

**Solution: Recalculate BN Statistics**
```python
def reset_batch_norm_stats(model, train_loader, device, n_batches=20):
    """Recalculate batch norm running statistics post-growth."""
    model.eval()
    for m in model.modules():
        if isinstance(m, nn.BatchNorm2d):
            m.running_mean.zero_()
            m.running_var.fill_(1.0)
            m.num_batches_tracked.zero_()
    
    # Run forward pass to recalculate stats
    model.train()
    with torch.no_grad():
        for i, (rgb, _) in enumerate(train_loader):
            if i >= n_batches:
                break
            model(rgb.to(device))
    model.eval()
```

**Effort:** 10 lines of code, ~2-3ms overhead
**Expected improvement:** 50-70% reduction in epoch-1 jump

---

### Hypothesis 2: New Channels Have Unmatched Learning Rates (MEDIUM CONFIDENCE)
**Why it happens:**
- New channels start at random (~0.001 scale)
- Existing channels have learned scales (~0.01-0.1)
- Adam optimizer sets step sizes based on running 2nd moment estimates
- Existing channels have warm estimates from previous training
- New channels get large initial updates due to cold estimates

**Evidence:**
- Worse in later cycles where existing channels are more trained
- LR is reset for architecture changes (optimizer state discarded)

**Solution: Graduated Learning Rate for New Channels**
```python
# During grow, tag new channels with special marker
# In train loop, apply 0.1x LR scaling to new channels for first N epochs
def apply_graduated_lr(optimizer, epoch, new_channel_markers, n_warmup=5):
    if epoch <= n_warmup:
        for group in optimizer.param_groups:
            # Base LR × scaling factor depending on parameter
            for p in group['params']:
                if p in new_channel_markers:
                    group['lr'] *= 0.5  # 50% of base LR
```

**Effort:** ~30 lines, parameter tracking overhead
**Expected improvement:** 20-30% reduction

---

### Hypothesis 3: Loss Landscape Becomes Steeper with New Dimensions (LOW-MEDIUM CONFIDENCE)
**Why it happens:**
- Adding channels increases dimensionality
- Gradient norms may be higher in the extended space
- First-epoch updates are larger in magnitude

**Solution: Adaptive Gradient Clipping**
```python
def adaptive_grad_clip(model, epoch_ratio):
    """Clip gradients more aggressively in early epochs after growth."""
    clip_norm = 1.0 / (1.0 + epoch_ratio)  # Relax from 1.0 toward 1.0
    torch.nn.utils.clip_grad_norm_(model.parameters(), clip_norm)
```

**Effort:** ~5 lines
**Expected improvement:** 10-20% reduction

---

## Recommended Solution Strategy

### Phase 1: Quick Fix (Est. 1-2 hours)
1. **Add batch norm statistics reset** (Hypothesis 1)
   - Highest ROI, lowest risk
   - Call in `clone_top_channels_into_new()` or after growth
   - Test on 5-cycle run

### Phase 2: Advanced Tuning (Est. 4-6 hours)
2. **Add graduated LR for new channels** (Hypothesis 2)
   - More complex but higher potential gain
   - Requires parameter tracking during grow
   - Test on full 10-cycle run

### Phase 3: Analysis (Est. 2-3 hours)
3. **Collect diagnostics** during training:
   - Per-channel activation scales (new vs old)
   - Per-channel gradient norms
   - BN statistics pre/post growth
   - Determine which hypothesis dominates

---

## Implementation Plan

### Option A: Batch Norm Reset (Lowest Risk)
**File:** `strm2stl/tools/ml/train/grow_prune.py`
**Location:** In `clone_top_channels_into_new()`, after weight copying:

```python
# After all weight copies complete
if bn_reset:
    _reset_batch_norm_stats(model, train_loader, device, n_batches=20)
```

**Metrics to track:**
- Epoch 1 val_loss after growth
- Recovery time to previous cycle best
- Final cycle performance

### Option B: Warm-Up Epoch (Medium Risk)
**Location:** In `train_cycle()`, add before epoch loop:

```python
# Warm-up epoch after architecture change (discard results)
if is_first_epoch_after_growth:
    model.train()
    for rgb, target in train_loader:
        pred = model(rgb.to(device))
        loss = _loss_fn(pred, target)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()
    # Results are discarded; statistics have stabilized
```

**Overhead:** +30-60 seconds per cycle
**Benefit:** "Burn-in" time for features to stabilize

---

## Expected Outcomes

**With Batch Norm Reset alone:**
- Epoch 1 jump reduced from ±0.030 to ±0.015 (50% reduction)
- Faster convergence in cycle 1-2
- Minimal overhead

**With BN Reset + Graduated LR:**
- Epoch 1 jump reduced to ±0.005-0.010 (70%+ reduction)
- More stable loss curves
- ~10-15% slower training per epoch (BN stats recalculation)

**With all three:**
- Near-elimination of epoch 1 shock
- Smoother growth trajectory
- 5-10% slower per cycle (startup costs)

---

## Testing Plan

1. **Baseline:** 5-cycle run with current code (already done)
   - Cycle 1→2 jump: +0.0013
   - Cycle 2→3 jump: +0.0230

2. **Test BN Reset:** 5-cycle run with batch norm reset enabled
   - Expected: 50% reduction in jumps
   - Should see faster convergence in first 5 epochs

3. **Test Graduated LR:** 5-cycle run with LR scaling
   - Expected: 20-30% additional reduction
   - May see smoother loss curves

4. **Full 10-cycle run:** Final validation with best-performing combination
   - Measure cycle-end improvements (after growth recovery)
   - Verify no negative side effects on model quality

---

## Questions for Investigation

1. Are new channels being too active (high act scale)?
2. Do batch norm statistics actually stabilize after 5 epochs?
3. Is the jump primarily in new channels or old channels?
4. Does gradient clipping alone help?
5. Can we use exponential moving average instead of full reset?

**Next steps:** Collect per-channel diagnostics during training run.
