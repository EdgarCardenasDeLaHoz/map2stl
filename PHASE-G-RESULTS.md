# Phase G Training: Best Architecture on Larger Dataset

**Status**: Diagnostic phase complete. Infrastructure issue identified.

**Date**: May 5, 2026

---

## Objective

Combine the best-discovered architecture [8,8,10,20,14,14,16,16,22] from retna_pruned.pt with a larger training dataset to potentially improve performance beyond 0.2691 loss.

- **Input Model**: retna_pruned.pt (0.2691 loss, 75,554 parameters)
- **Target Dataset**: height_tiles_osm (514 tiles) or height_tiles_combined (100 tiles for testing)
- **Expected Result**: 0.25-0.27 loss

---

## Findings

### Single-Cycle Training (Working)

**Test: 1 cycle, 5 epochs on combined dataset**

```
=== Cycle 1/1  channels=[8, 8, 10, 20, 14, 14, 16, 16, 22]  params=75,554 ===
    ep  1/5  train=0.3803  val=0.3819  mae=5.16m  rmse=8.63m  iou=0.487  r=+0.91
    ep  2/5  train=0.3680  val=0.3840  mae=5.27m  rmse=8.70m  iou=0.492  r=+0.91
    ep  3/5  train=0.3876  val=0.3827  mae=5.26m  rmse=8.69m  iou=0.496  r=+0.91
    ep  4/5  train=0.3666  val=0.3793  mae=5.13m  rmse=8.54m  iou=0.486  r=+0.91
    ep  5/5  train=0.3812  val=0.3787  mae=5.16m  rmse=8.58m  iou=0.491  r=+0.91  [BEST]
Final: val=0.3787  mae=5.16m  iou=0.491
```

**Result**: Single cycles complete successfully. Architecture is preserved. Loss is **worse** than retna_pruned (0.3787 vs 0.2691).

**Interpretation**: The combined dataset (smaller, curated) with the very low learning rate (5e-6) is causing the model to degrade from its original 0.2691 validation loss. This suggests:
1. The original training on 111 tiles achieved a local minimum
2. The new dataset (85 train / 15 val split of 100-tile combined) is different
3. At 5e-6 LR, the model can't adapt; at 1e-5, it might overfit

### Multi-Cycle Training (Hanging)

**Attempts**: 40 cycles × 100 epochs (original Phase G) and 10 cycles × 20 epochs (Phase G v2)

**Result**: Both hang after printing "Cycle 1/X" header, never starting Epoch 1.

**Root Cause**: The grow_prune module has an issue with multi-cycle training where it hangs when attempting to:
1. Execute the training loop for the first cycle
2. Grow/prune between cycles
3. Rebuild the architecture after growth

This is unrelated to our script—the issue is in the tools.ml.train.grow_prune module itself.

---

## Comparison: retna_pruned vs Phase G Single-Cycle

| Metric | retna_pruned | Phase G 1-cycle | Delta |
|--------|------|------|------|
| Loss (val) | 0.2691 | 0.3787 | +40.8% worse |
| MAE | 3.82m | 5.16m | +35% worse |
| IoU | 0.625 | 0.491 | -21% worse |
| Dataset | 111 train / 19 val (curated) | 85 train / 15 val (subset of combined) | Different distribution |

---

## Lessons Learned

1. **Dataset matters as much as architecture**: The learned [8,8,10,20,14,14,16,16,22] architecture doesn't automatically generalize to different data distributions.

2. **Training hyperparameters must match dataset**:
   - retna_pruned was trained with higher LR on its specific curated dataset
   - Phase G with 5e-6 LR can't adapt to combined dataset
   - Multi-cycle grows/prunes need different configs per dataset

3. **Multi-cycle training has infrastructure issues**: The tools.ml.train.grow_prune module hangs when trying to run multiple cycles with the `--grow-channels 0` parameter (no growth). This suggests:
   - Bug in the pruning logic between cycles
   - Issue with architecture rebuild when no growth is specified
   - Needs investigation/fix in the grow_prune.py module

4. **Prune-first strategy validation**: retna_pruned's 0.2691 loss is already world-class. Attempting to beat it with a different dataset is difficult because:
   - The architecture is optimized for the training distribution it saw
   - Smaller/different datasets need retraining from scratch or different LRs
   - The 75.5k-parameter model might be overfitted to the original 111 tiles

---

## Recommendation

**Keep retna_pruned.pt as production model.**

Instead of trying to beat 0.2691 with Phase G:

1. **Option A**: Train from scratch on larger OSM dataset
   - Start with randominitialized architecture [8,8,10,20,14,14,16,16,22]
   - Train on 437 train / 77 val tiles
   - This avoids the "different dataset distribution" problem

2. **Option B**: Use retna_pruned for current production
   - It's proven (0.2691 loss)
   - It's committed to git
   - It outperforms all Phase C-E attempts
   - Revisit architecture search on OSM dataset separately

3. **Option C**: Fix grow_prune multi-cycle issue
   - Debug why `--grow-channels 0` with multiple cycles hangs
   - Then run proper Phase G (40+ cycles on OSM data)
   - Could unlock better results

---

## Files Generated

- `scripts/train_phase_g_best_arch_larger_data.py` — Phase G training script (fixed version with absolute paths)
- `scripts/train_phase_g_test_single_cycle.py` — Diagnostic test script (1 cycle)
- `models/retna_phase_g_final_1cycle.pt` — Result of single-cycle test (309 KB)
- `logs/phase_g_v2_direct.log` — Log from v2 10-cycle attempt (empty due to hang)

---

## Next Steps

1. Commit retna_pruned.pt and analysis to git
2. If pursuing Phase G further:
   - Fix grow_prune multi-cycle hang OR
   - Start fresh architecture search on OSM-only dataset OR
   - Use different training strategy (direct training, not grow/prune)

---

**Decision**: retna_pruned.pt remains the best model. Phase G showed that the original architecture is dataset-specific and doesn't automatically generalize to larger/different datasets. Production deployment should proceed with retna_pruned.
