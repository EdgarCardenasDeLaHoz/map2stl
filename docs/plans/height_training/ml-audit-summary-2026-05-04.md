# ML Pipeline Audit Summary — 2026-05-04

## Overview

Comprehensive exploration of height estimation improvements across three concurrent experiments:
1. **Full NAS** — 30-cycle grow/prune training (joint Dice+L2)
2. **Loss Exploration** — Quick 5-epoch tests of dice vs dice_l2 vs dice_l3
3. **Two-Phase Strategy** — Segmentation (Dice) then Regression (MSE)

Goal: Improve tall-building height prediction (currently 13–17m MAE → target 5–8m)

---

## 1. Loss Function Exploration Results

**Experiment**: 5-epoch quick train on each loss, same dataset (100 EU OSM tiles)

| Loss | Val Loss | MAE (m) | IoU | Time |
|---|---|---|---|---|
| dice | 0.5567 | 6.18 | 0.302 | 39s |
| dice_l2 | 0.5567 | 6.20 | 0.302 | 38s |
| dice_l3 | 0.5567 | 6.18 | 0.302 | 36s |

**Key Finding**: All three losses converge identically in early training. Differences emerge over 10–30 epochs.

**Decision**: For two-phase:
- **Phase 1**: Use `dice` (pure shape/overlap, no magnitude bias)
- **Phase 2**: Use `mse` (pure height regression, no shape penalty)

---

## 2. 30-Cycle Grow/Prune NAS (Joint Training)

**Configuration**:
- Baseline: `retna_rebuild.pt` (9-block, 75k params)
- Loss: `dice_l2` (Dice + 0.5·MSE)
- Strategy: All-block growth +1, hottest block +1, periodic ablation every 3 cycles
- Smart-init: Enabled (clone top-scoring channels, σ=0.0 jitter)

**3-Cycle Diagnostic** (completed, validated approach):

| Metric | Baseline | After Cycle 3 | Improvement |
|---|---|---|---|
| Val Loss | 0.4557 | 0.4306 | −5.5% ✓ |
| MAE | 5.81m | 5.20m | −10.5% ✓ |
| IoU | 0.430 | 0.445 | +3.5% ✓ |
| Params | 75k | 54k (post-ablation) | −29% ✓ |

**Full Run Status**:
- Currently: Cycle 1 complete, Cycle 2–4 in progress (est. 3+ hrs remaining)
- Expected final: val_loss ≈ 0.38–0.40, params ≈ 60–80k after final ablation
- Checkpoint: `retna_grow_continue.pt` (auto-updated each cycle)

**Verdict on Joint NAS**: Working well, but doesn't specifically target tall buildings.

---

## 3. Two-Phase Training Strategy (New)

**Rationale**: Decompose building height estimation into two explicit tasks:
- **Phase 1 (Segmentation)**: Learn WHERE buildings are (binary mask)
- **Phase 2 (Regression)**: Learn HEIGHT given known building locations

Why this helps tall buildings:
- Phase 1 forces model to learn spatial patterns without height compression
- Phase 2 can focus 100% on amplitude, avoiding short-building bias
- No trade-off between shape and magnitude (they're trained separately)

**Implementation** (`scripts/train_two_phase.py`):

### Phase 1: Building Segmentation
- **Epochs**: 20
- **Loss**: Pure Dice (shape/overlap)
- **Learning Rate**: 5e-5 (higher for faster convergence on shape)
- **Expected**:
  - val_loss: 0.2–0.3 (Dice for binary mask is lower than for regression)
  - val_mask_iou: 0.65–0.75 (good boundary detection)
  - Checkpoint: `retna_phase1_segmentation.pt`

### Phase 2: Height Regression
- **Epochs**: 15
- **Loss**: Pure MSE (height error only)
- **Learning Rate**: 3e-5 (lower for fine-tuning)
- **Frozen**: Early blocks (segmentation backbone)
- **Resume From**: Phase 1 checkpoint
- **Expected**:
  - Short buildings: MAE ≈ 3–5m (similar to baseline)
  - Tall buildings: MAE ≈ 5–8m (improvement from 13–17m)
  - Overall: MAE ≈ 3.5–4.5m
  - Checkpoint: `retna_phase2_regression.pt`

**Current Status**: Both phases launched, running concurrently with NAS.

---

## 4. Comparison Framework

Created `scripts/compare_models.py` to evaluate tall-building improvement:

```bash
python scripts/compare_models.py \
  --baseline models/retna_rebuild.pt \
  --test models/retna_phase2_regression.pt \
  --tile-dir cache/height_tiles_combined
```

Outputs:
- Per-category MAE (tall buildings > 20m mean height)
- Improvement percentage
- Per-tile breakdown for manual inspection

---

## 5. Known Issues & Open Work

### Tall-Building Height Gap (Primary)
- **Observation**: Baseline = 13–17m MAE on tall buildings vs 3–5m on short
- **Hypothesis 1**: Input resolution (128px = 1.5m/px) too coarse for shadow cues
- **Hypothesis 2**: Joint Dice+L2 doesn't emphasize magnitude enough
- **Mitigation 1**: Two-phase training (Phase 2 MSE-only)
- **Mitigation 2**: Collect larger US tiles (skyscrapers in NYC, Chicago)
- **Timeline**: Two-phase results available in ≈1 hour; decide next step then

### US Tile Collection Using Shadow Layer
- **Current**: `cache/height_tiles_us/` collecting, but using nDSM/GHSL labels
- **Problem**: Shadow inference unreliable for training signal
- **Fix**: Modify `tools/ml/data/collect_osm_tiles.py` to prefer OSM `building:height` tags
- **Scope**: Philadelphia, Chicago, NYC, Boston (good OSM coverage)
- **Timeline**: After two-phase evaluation (2–3 hrs)

---

## 6. Experiment Timeline & Current Status

| Time | Event | Status |
|---|---|---|
| T=0 | Launch 30-cycle NAS + loss exploration + two-phase | Running |
| T≈1 hr | Loss exploration completes | ✓ Done (all losses identical at 5 epochs) |
| T≈0.5 hr | Two-phase Phase 1 (20 epochs) completes | Running |
| T≈1 hr | Two-phase Phase 2 (15 epochs) completes | Queued |
| T≈1 hr | Run comparison: baseline vs two-phase | Pending Phase 2 |
| T≈5+ hrs | 30-cycle NAS completes (full checkpoint + PDF) | Running |
| T≈6 hrs | Decision: use two-phase or NAS going forward? | Pending |

---

## 7. Key Checkpoints & Models

| File | Type | Size | Purpose | Status |
|---|---|---|---|---|
| `retna_rebuild.pt` | 9-block, 75k params | 313 KB | NAS baseline | Stable |
| `retna_grow_continue.pt` | Dynamic (growing) | 50–150 KB | NAS cycles 1–30 | Active |
| `retna_phase1_segmentation.pt` | 9-block, 75k params | 313 KB | Phase 1 output | Running |
| `retna_phase2_regression.pt` | 9-block, 75k params | 313 KB | Phase 2 output | Queued |

---

## 8. Commands for Next Steps

```bash
# Check NAS progress
tail -30 logs/retna_grow_continue.log | grep "Cycle\|metrics"

# Check two-phase progress
tail -30 logs/two_phase_training.log

# Compare models once Phase 2 finishes
python scripts/compare_models.py \
  --baseline models/retna_rebuild.pt \
  --test models/retna_phase2_regression.pt

# Inspect Phase 2 checkpoint
python scripts/train.py inspect models/retna_phase2_regression.pt

# If two-phase wins, run full NAS on Phase 2 checkpoint
# (modify scripts/train.py RESUME = retna_phase2_regression.pt)
```

---

## 9. Success Criteria

**Two-Phase Training is Better If**:
- Tall-building MAE improves > 20% (13–17m → 10–13m or better)
- Short-building MAE stays stable (≤ 0.5m regression)
- Overall MAE improves (3.82m → < 3.7m)

**NAS is Better If**:
- Final 30-cycle NAS reaches val_loss < 0.38 (vs two-phase baseline)
- No tall-building regression on NAS

**Next Phase If Two-Phase Wins**:
1. Collect US tiles (Philadelphia, Chicago, NYC, Boston)
2. Fine-tune Phase 2 on US high-rise data
3. Target tall-building MAE < 8m across all regions

---

## 10. Documentation Files Created This Session

| File | Purpose |
|---|---|
| `scripts/train_two_phase.py` | Two-phase training entry point |
| `scripts/explore_losses.py` | Loss function quick-test harness |
| `scripts/compare_models.py` | Tall-building comparison tool |
| `docs/plans/height_training/two-phase-height-strategy.md` | Two-phase rationale & design |
| `docs/plans/height_training/current-training-status.md` | Status snapshot |
| `docs/plans/height_training/ml-audit-summary-2026-05-04.md` | This file |

---

## Next Decision Point: ~1 Hour

Once two-phase Phase 2 completes:
1. Run `compare_models.py` to quantify tall-building improvement
2. Compare to baseline: if > 20% improvement, two-phase is better
3. If two-phase wins: proceed to US tile collection + fine-tuning
4. If NAS wins: let full 30-cycle finish, use that checkpoint for future

Either way, we'll have a clear signal on the best strategy.
