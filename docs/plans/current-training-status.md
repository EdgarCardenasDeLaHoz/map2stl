# Current Training Status — 2026-05-04

## Active Experiments

Three concurrent experiments running to improve building height prediction:

### 1. Full 30-Cycle Grow/Prune NAS (Primary)
- **Status**: In progress (Cycle 1/30, ep 30/30 completed)
- **Baseline**: `retna_rebuild.pt` (9-block, 75k params)
- **Strategy**: Growing all blocks +1 per cycle, periodic ablation every 3 cycles, smart-init
- **Expected duration**: 4–5 hours total
- **Checkpoint**: `retna_grow_continue.pt` (auto-updated each cycle)
- **Loss**: `dice_l2` (Dice + 0.5·MSE)
- **Target**: val_loss ≤ 0.38 (currently 0.42–0.43)

**Progress (Cycle 1 complete)**:
- Started: val_loss=0.4557, MAE=5.81m, IoU=0.430
- After 30 epochs: val_loss=0.4290, MAE=5.41m, IoU=0.444
- Improvement: −0.0267 loss (−5.8%), +0.05m MAE, +0.014 IoU

### 2. Loss Function Exploration (Parallel)
- **Status**: In progress (Testing: dice → dice_l2 → dice_l3)
- **Purpose**: Understand loss landscape before two-phase commitment
- **Duration**: 5 epochs per loss (quick comparative test)
- **Losses tested**:
  1. `dice` — Pure Dice (shape/overlap focus)
  2. `dice_l2` — Dice + MSE (balanced)
  3. `dice_l3` — Dice + cubic (tall-building emphasis)
- **Output**: `output/loss_exploration.csv` (final metrics comparison)

### 3. Two-Phase Training (Preparation)
- **Status**: Implemented, awaiting loss exploration results
- **Files**: `scripts/train_two_phase.py`, `docs/plans/two-phase-height-strategy.md`
- **Phase 1**: Segmentation (Dice loss on binary mask, 20 epochs)
- **Phase 2**: Regression (MSE on height, 15 epochs, frozen segmentation)
- **Rationale**: Decompose to avoid marginal-mean collapse on tall buildings

---

## Diagnostic Results (3-Cycle Test — 2026-05-04)

Before committing to 30-cycle full run, we validated the approach on 3 cycles:

| Metric | Baseline | After Cycle 3 | Improvement |
|---|---|---|---|
| Val Loss | 0.4557 | 0.4306 | −0.0251 (−5.5%) ✓ |
| MAE | 5.81m | 5.20m | −0.61m (−10.5%) ✓ |
| IoU | 0.430 | 0.445 | +0.015 (+3.5%) ✓ |
| Params (after ablation) | 75k | 54k | −29% reduction ✓ |

**Verdict**: Growth is working. Smart-init prevents neuron addition regression. Periodic ablation compacts effectively.

---

## Known Issues

### Tall-Building Height Gap (Primary Open)
- **Observation**: 13–17m MAE on tall buildings vs 3–5m on short buildings
- **Hypothesis**: Input resolution (128px, 1.5m/px) insufficient for shadow-based height cues on skyscrapers
- **Alternative hypothesis**: Joint Dice+L2 loss doesn't emphasize magnitude enough
- **Two-phase mitigation**: Phase 2 MSE-only training might recover amplitude
- **Backup**: Collect larger US tiles (skyscrapers in Philadelphia, NYC, Chicago) for domain-specific training

### US Tile Collection Using Shadow Layer
- **Current state**: `cache/height_tiles_us/` in progress, but using provider-merge (nDSM, GHSL, shadow inference)
- **Problem**: Shadow-inferred heights unreliable for training
- **Fix needed**: Modify `tools/ml/data/collect_osm_tiles.py` to use OSM `building:height` tags where available
- **Scope**: Philadelphia, Chicago, NYC, Boston (all have good OSM coverage)

---

## Experiment Priorities

1. **Finish 30-cycle NAS** (4–5 hr) → generates final checkpoint + inspection PDF
2. **Finish loss exploration** (∼1 hr) → pick best loss for two-phase
3. **Run Phase 1 segmentation** (30 min) → `retna_phase1_segmentation.pt`
4. **Run Phase 2 regression** (20 min) → `retna_phase2_regression.pt`
5. **Compare inspection PDFs** (baseline vs two-phase) → decide direction
6. **Fix US tile collection** (1–2 hr) → switch from shadow to OSM labels
7. **Collect US tiles** (2–3 hr) → Philadelphia, Chicago, NYC, Boston
8. **Fine-tune on US data** (2–3 hr) → improve tall-building MAE

---

## Checkpoints & Models

| File | Type | Size | Status | Last Updated |
|---|---|---|---|---|
| `retna_rebuild.pt` | 9-block, 75k params | 313 KB | Baseline | 2026-05-03 |
| `retna_grow_continue.pt` | Current cycle | 29 KB | Active | Updating each cycle |
| `retna_loss_test_dice.pt` | Loss exploration | — | Active | Testing |
| `retna_phase1_segmentation.pt` | Segmentation | — | Pending | — |
| `retna_phase2_regression.pt` | Regression | — | Pending | — |

---

## Quick Commands

```bash
# Check 30-cycle progress
tail -20 logs/retna_grow_continue.log

# Check loss exploration
cat output/loss_exploration.csv

# Run Phase 1 (after loss exploration)
python scripts/train_two_phase.py phase1

# Run Phase 2 (after Phase 1)
python scripts/train_two_phase.py phase2

# Run both phases
python scripts/train_two_phase.py both

# Inspect a checkpoint
python scripts/train.py inspect models/retna_phase2_regression.pt
```

---

## Timeline

| Time | Event |
|---|---|
| Now | 30-cycle NAS running (est. 4–5 hr) + Loss exploration (est. 1 hr) |
| +1 hr | Loss exploration completes → pick best loss |
| +5 hr | 30-cycle NAS completes → inspect final checkpoint |
| +6 hr | Phase 1 segmentation runs (30 min) |
| +6.5 hr | Phase 2 regression runs (20 min) |
| +7 hr | Compare baseline vs two-phase on tall-building MAE |

---

## Next Steps When You Return

1. **Check loss exploration results**: `output/loss_exploration.csv`
2. **Check 30-cycle completion**: Tail log, view final inspection PDF
3. **Decide on two-phase**: If loss exploration shows promise, run `python scripts/train_two_phase.py both`
4. **Compare tall-building MAE**: Baseline (13–17m) vs two-phase (target: 5–8m)
5. **Start US tile collection** if two-phase improves baseline

