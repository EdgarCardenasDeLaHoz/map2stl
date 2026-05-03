# Quick Reference: Testing Growth Degradation Fixes

## Running the Analysis

Once the 10-cycle training completes:

```bash
cd strm2stl
python tools/ml/analyze_growth_degradation.py
```

This will output a table like:
```
Cycle | Channels         | Epoch-1 Val | Best Val  | Jump  | Recovery | Status
------|------------------|-------------|-----------|-------|----------|----------
1     | [8,8,8,8]        | 1.0385      | 0.5XXX    | —     | —        | baseline
2     | [8,8,8,8]        | 0.XXXX      | 0.5XXX    | +.02X | +.01X    | ⚠ degraded
3     | [8,8,8,8] (grow) | 0.XXXX      | 0.5XXX    | +.03X | +.02X    | ⚠ large-jump
...
```

## Testing Batch Norm Reset Fix

### Step 1: Read the Proposal
- File: `tools/ml/train/batch_norm_reset.py`
- Contains complete implementation and integration points

### Step 2: Integrate into grow_prune.py
The batch norm reset should be called in `clone_top_channels_into_new()`:

```python
# After cloning weights, add:
if train_loader is not None:
    from tools.ml.train.batch_norm_reset import reset_batch_norm_statistics
    reset_batch_norm_statistics(
        model,
        train_loader,
        device,
        n_batches=20,
        verbose=True,
    )
```

### Step 3: Run 5-Cycle Test
```bash
# Modify scripts/train.py: change CYCLES from 10 back to 5
sed -i 's/CYCLES = 10/CYCLES = 5/' scripts/train.py

# Clear old logs
rm models/retna_pruned.pt logs/retna_grow*.log

# Run test
python scripts/train.py grow
```

### Step 4: Compare Results
```python
import subprocess
import re

# Extract cycle metrics from logs
def get_jumps(log_file):
    jumps = []
    with open(log_file) as f:
        prev_best = None
        for line in f:
            # Match: Cycle N metrics: best_val=X.XXXX
            m = re.search(r'Cycle \d+ metrics:.*best_val=([\d.]+)', line)
            if m:
                best = float(m.group(1))
                if prev_best:
                    jumps.append(f"Cycle jump: {best - prev_best:+.4f}")
                prev_best = best
    return jumps

baseline = get_jumps('logs/retna_grow_continue_baseline.log')  # OLD run
improved = get_jumps('logs/retna_grow_continue.log')  # NEW run with BN reset

print("BASELINE:", baseline)
print("WITH BN RESET:", improved)
```

## Graduated Learning Rate Alternative

If batch norm reset alone isn't sufficient, try graduated LR:

### Step 1: Modify train_cycle()
```python
def train_cycle(..., is_post_growth=False):
    ...
    for epoch in range(1, epochs + 1):
        ...
        # Scale LR for new channels in first 5 epochs after growth
        if is_post_growth and epoch <= 5:
            for group in optimizer.param_groups:
                group['lr'] *= 0.5  # 50% of base LR
        ...
```

### Step 2: Pass flag from clone_top_channels_into_new
```python
def clone_top_channels_into_new(...):
    ...
    # After copying weights, train with:
    model, _, _, _ = train_cycle(
        ...,
        is_post_growth=True  # NEW FLAG
    )
```

## Expected Results

### With Batch Norm Reset
```
Baseline (Cycle 2→3):
  Epoch 1 val_loss: 0.4044 (vs prev cycle best 0.3766)
  Jump magnitude: +0.0278 (+7.4%)

With BN Reset (expected):
  Epoch 1 val_loss: 0.3850 (vs prev cycle best 0.3766)
  Jump magnitude: +0.0084 (+2.2%)
  → 70% reduction in jump
```

### With Graduated LR (additional)
```
With BN + Graduated LR (expected):
  Epoch 1 val_loss: 0.3800 (vs prev cycle best 0.3766)
  Jump magnitude: +0.0034 (+0.9%)
  → 87% reduction total
```

## Troubleshooting

**Q: Analysis script says "Log file not found"**
- Wait for training to complete, or
- Run: `python tools/ml/analyze_growth_degradation.py 2>&1 | head -20`

**Q: Cycle 1 has no growth jump (it's baseline)**
- Expected! Jumps only occur when transitioning from old→new architecture
- Look for Cycle 2 onward

**Q: All metrics look the same**
- Architecture didn't change (probably pruning-only cycle)
- Look for cycles marked with `(grow)` in the Channels column

**Q: Batch norm reset makes things worse**
- Possible: wrong n_batches value (try 10-50)
- Or: batch norm isn't actually the bottleneck
- Fall back to graduated LR or other solutions

## Success Metrics

- [ ] Epoch-1 jump reduced from >2% to <1% after growth
- [ ] Convergence faster in first 10 epochs (fewer "best" marker changes)
- [ ] Final cycle achieves same or better val_loss than baseline
- [ ] No regressions in earlier cycles

---

For detailed analysis: See `docs/growth_degradation_analysis.md`
For implementation details: See `tools/ml/train/batch_norm_reset.py`
For full status: See `docs/growth_degradation_mitigation.md`
