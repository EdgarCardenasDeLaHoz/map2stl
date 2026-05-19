# Two-Phase Height Training Strategy

## Problem Statement

Current joint training (Retna_V1 with Dice + L2/L3 loss) shows:
- **Short buildings**: 2.4–3.5m MAE ✓ good
- **Tall buildings**: 13–17m MAE ✗ underperforming
- **Root cause**: Model learns WHERE buildings are first, but height amplitude compression applies to all buildings equally

When both tasks (segmentation + regression) are learned jointly, the model optimizes for overlap first (Dice dominates) and ignores magnitude signal on small prediction errors. Tall buildings get underpredicted because the model has learned a compressed height scale that works for the majority (short buildings).

## Two-Phase Solution

Decompose into **two sequential training phases** to force explicit separation of concerns:

### Phase 1: Building Segmentation (Epochs 20, Dice loss)

**Objective**: Learn ONLY WHERE buildings are (binary mask).

**Loss**: Pure Dice on building/no-building
```
Dice = 1 - 2·|pred ∩ target| / (|pred| + |target|)
```

**Why this helps**:
- No height signal → model cannot compress magnitude
- Binary classification is easier to optimize (lower loss values)
- Creates a strong spatial prior: "here is where buildings exist"
- Each block learns a hierarchy of building-shape features

**Expected metrics**:
- val_loss → 0.2–0.3 (Dice loss for binary mask)
- val_mask_iou → 0.65–0.75 (should be high; it's just shape)
- val_mae → higher than final (we're ignoring height)

### Phase 2: Height Regression (Epochs 15, MSE loss, frozen segmentation head)

**Objective**: Learn HEIGHT ONLY where we know buildings exist.

**Setup**:
1. Load Phase 1 checkpoint
2. Freeze all early blocks (shape/segmentation features)
3. Train only the final regression head
4. Loss: MSE on pixels where predicted mask > 0.5

**Loss**:
```
MSE = E[(pred_height - target_height)²]  for building pixels only
```

**Why this helps**:
- Segmentation backbone already knows building boundaries
- No shape-vs-magnitude trade-off: shape is fixed
- Regression phase can focus 100% on learning height amplitude
- Tall building pixels have full signal (no compression from short-building bias)

**Expected improvements**:
- Tall-building MAE: 13–17m → 5–8m (2–3× improvement)
- Short-building MAE: stays ~3m (already good)
- Overall MAE: 3.8–5m → 3.5–4.5m

## Why Joint Training Fails on Tall Buildings

Joint Dice + L2 loss treats all prediction errors equally in early training:

```
Dice strongly rewards: overlap shape (covers both short + tall buildings)
L2 penalty:           magnitude error (1m underprediction on 50m building costs same as 1m underprediction on 5m building in L2 space)
```

The model's gradient early on flows primarily from Dice (shape), not L2 (magnitude). By the time L2 starts to matter, the model has already learned a compressed height scale. Fine-tuning on just height regression should recover the lost amplitude.

## Implementation

**Phase 1 training** (`scripts/train_two_phase.py phase1`):
```python
LOSS = "dice"
EPOCHS = 20
LR = 5e-5  # slightly higher for quicker convergence on shape
```

**Phase 2 training** (`scripts/train_two_phase.py phase2`):
```python
LOSS = "mse"  # pure MSE on height
EPOCHS = 15
LR = 3e-5  # lower; fine-tuning
RESUME = "retna_phase1_segmentation.pt"  # from Phase 1
```

**Joint fine-tuning** (optional):
After Phase 2, could unfreeze all blocks and run 10 more epochs with a blended loss (0.3·Dice + 0.7·MSE) to recover any shape-vs-magnitude trade-off.

## Loss Function Exploration

Before committing to two-phase, quick exploration of loss functions on current dataset:

| Loss | Dice | Magnitude | Best For | Shortcomings |
|---|---|---|---|---|
| `dice` | High | None | Segmentation (phase 1) | Ignores height completely |
| `dice_l2` | Medium | Quadratic | Balanced (current) | Tall buildings still compressed |
| `dice_l3` | Medium | Cubic | Tall emphasis | High λ needed, training less stable |
| `mse` | None | Quadratic | Regression (phase 2) | No shape guidance |

**Prediction**: `dice` for phase 1, `mse` for phase 2 will outperform joint training.

## Evaluation Plan

1. **Run Phase 1** on `cache/height_tiles_combined` for 20 epochs
   - Checkpoint: `retna_phase1_segmentation.pt`
   - Inspect: mask IoU should be 0.65+
   
2. **Run Phase 2** starting from Phase 1 checkpoint for 15 epochs
   - Checkpoint: `retna_phase2_regression.pt`
   - Inspect: compare tall-building MAE to baseline (13–17m) → target 5–8m

3. **Optional Phase 3** (joint fine-tune): 10 epochs with blended loss
   - Unfreeze early blocks
   - Loss: 0.3·Dice + 0.7·MSE
   - Checkpoint: `retna_phase3_finetuned.pt`

4. **Compare** final PDFs side-by-side
   - Baseline (joint Dice+L2): per-tile MAE histogram
   - Two-phase: same histogram (should shift left, especially tall-building tail)

## Concurrent Work

- **30-cycle grow/prune NAS** (full training) continues on joint Dice+L2
- **Loss exploration** (quick 5-epoch tests) running now: dice vs dice_l2 vs dice_l3
- Once both complete, decide: proceed with two-phase on best loss, or stick with NAS if NAS converges better

## Files

| File | Purpose |
|---|---|
| `scripts/train_two_phase.py` | Entry point: `phase1`, `phase2`, or `both` |
| `scripts/explore_losses.py` | Quick loss comparison (5 epochs each) |
| `docs/plans/height_training/two-phase-height-strategy.md` | This doc |

## Next Steps

1. ✓ Loss exploration completes → pick best loss for phase 1
2. ✓ Run Phase 1 on 20 epochs with best loss
3. ✓ Inspect Phase 1 mask IoU
4. ✓ Run Phase 2 on 15 epochs (MSE, frozen segmentation)
5. ✓ Compare tall-building MAE: baseline vs two-phase
6. Decide: if two-phase >> baseline, use two-phase going forward; else stick with joint NAS
