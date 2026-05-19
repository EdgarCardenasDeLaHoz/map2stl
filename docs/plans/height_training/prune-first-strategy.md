# Prune-First Training Strategy — 2026-05-04

## Hypothesis

Pruning before growing normally improves learning because:
1. **Removes noise first** — Dead channels that consume compute but don't contribute
2. **Leaner baseline** — Model starts from minimal viable architecture
3. **Targeted growth** — New neurons fill real capacity gaps, not noise
4. **Better convergence** — Less bloat to optimize around

## Strategy

### Phase A: Aggressive Pruning (≈10 min)
**Input**: `retna_phase2_regression.pt` (Phase 2 checkpoint, 75k params)

**Process**:
1. Single-channel ablation (zero channels one at a time)
2. Only test **bottom 20%** by grad×activation score (most dead)
3. Accept channel zero if val_loss penalty < +0.01
4. Compact architecture
5. Retrain 20 epochs for recovery

**Expected Output**: `retna_pruned_first.pt` (50–65k params, −20–30%)

### Phase B: Grow from Pruned Baseline (≈2–3 hrs)
**Input**: `retna_pruned_first.pt`

**Process**:
1. 30-cycle grow/prune NAS
2. All-block widening +1 per cycle (no extra hot-block growth)
3. Smart-init enabled (clone top-scoring channels)
4. Periodic ablation every 3 cycles (keep lean)
5. Final single-channel pruning + 20-epoch retrain

**Expected Output**: `retna_pruned_and_grown.pt` (60–100k params)

### Inspection
- **Samples**: 30 (vs previous 20)
- **Seed**: 42 (fresh random selection = new pictures)
- **Output**: `output/retna_pruned_and_grown_inspect_pruned_and_grown.pdf`

## Why This Works

| Stage | Bloat Removed | Benefit |
|---|---|---|
| Phase 2 (baseline) | 75k params (full) | Good starting point but has dead channels |
| Phase A (prune) | −15–25k params | Removes noise, forces efficiency |
| Phase B (grow) | +40–50k params added, periodic ablation | Fresh growth targets real gaps |
| Final | 60–100k after final prune | Lean, targeted model |

**Expected result**: Better convergence than joint or two-phase because:
- No inherited bloat from Phase 2
- Growth happens from minimal baseline
- Periodic ablation prevents re-bloating

## Comparison Matrix

| Approach | Baseline Loss | Process | Expected Final | Advantage |
|---|---|---|---|---|
| **Joint NAS** | 0.4557 (rebuild) | Grow all at once | 0.41–0.42 | Simple |
| **Two-Phase** | 0.4434 (phase 2) | Segmentation + regression | 0.4434 (done) | Interpretable |
| **Prune-First** | 0.4434 (phase 2 pruned) | Prune → 30-cycle grow | 0.40–0.41 ← **Target** | Clean start, no bloat |

---

## Timeline

| Phase | Time | Tasks |
|---|---|---|
| **A (Prune)** | 10 min | Ablate bottom-20% channels, retrain 20 ep |
| **B (Grow)** | 2–3 hrs | 30 cycles × 40–50 min/cycle |
| **Inspect** | 5 min | Generate 30-sample PDF with new seed |
| **Total** | ≈2.5–3.5 hrs | — |

---

## Metrics to Watch

**Phase A (Pruning)**:
- `[ablation] baseline val_loss` — Starting point
- `[ablation] block X: zeroed Y/Z channels` — How much pruned per layer
- `[ablation] final val_loss` — Penalty from pruning
- `[ablation] compacted params` — Param reduction %
- `retraining 20 epochs` → final val_loss — Recovery training result

**Phase B (Growing)**:
- Cycle 1 → 30: val_loss trend
- Per-cycle Δloss to confirm improvement
- Final IoU (building mask detection)
- Final params after final-prune

**Inspection**:
- Per-tile MAE histogram (tall vs short)
- Sample predictions: do tall buildings look better?
- Compare to Phase 2 PDF (same regions, different samples)

---

## Success Criteria

| Criterion | Target | Why |
|---|---|---|
| Phase A params reduction | > 15% | Good pruning |
| Phase A val_loss penalty | < +0.01 | Minimal loss from pruning |
| Phase B final val_loss | < 0.41 | Better than Phase 2 (0.4434) and NAS (≈0.423) |
| Phase B final IoU | > 0.45 | Good mask detection |
| Tall-building MAE | < 10m | Progress toward 5–8m target |

---

## Rationale vs Alternatives

### Why not just retrain Phase 2 more?
- Phase 2 is at local optimum, additional epochs yield diminishing returns
- Model likely has inherited bloat from Phase 1 → Phase 2

### Why not start fresh (cold start)?
- Prune-first reuses Phase 2's learned features (shapes, basic patterns)
- Faster convergence than cold start
- More stable than random init

### Why periodic ablation?
- Prevents re-bloating as model grows
- Early cycles grow fast, later cycles grow slower (natural ablation schedule)
- Keeps model lean throughout training

---

## Implementation Details

**Pruning algorithm** (Phase A):
```
for block in model.blocks:
    candidates = channels sorted by (grad × activation) ascending
    for channel in candidates[0:20%]:
        test channel = 0
        val_loss_new = eval()
        if val_loss_new - val_loss_baseline <= tolerance:
            keep = 0
        else:
            restore
```

**Growing algorithm** (Phase B):
```
for cycle in 1..30:
    # Train current arch
    for epoch in 1..inner_epochs:
        train()
        if val_loss improves:
            save()
    
    # Score blocks
    for block in model.blocks:
        score[block] = mean(abs(activation)) × mean(abs(gradient))
    
    # Growth decision
    if overfit_detected and train_dropped:
        prune 25% of all blocks
    else:
        grow all blocks +1
        grow hottest block +1 (using smart-init)
    
    # Periodic ablation (every 3 cycles)
    if cycle % 3 == 0:
        run single-channel ablation
        compact architecture
```

---

## Next Steps After Phase B Completes

1. **Extract final metrics**:
   ```bash
   tail logs/phase_b_growing.log | grep "Final channels"
   ```

2. **Compare to baselines**:
   - Prune-first final vs Phase 2 (0.4434) vs NAS (~0.423)
   - Look at IoU, IoU improvement is a good sign

3. **Inspect PDFs**:
   - `retna_pruned_and_grown_inspect_pruned_and_grown.pdf` (new samples)
   - Compare tall-building tiles to Phase 2 PDF

4. **Decision**:
   - If Prune-First < 0.41 → **Use Prune-First**
   - If Prune-First ≥ Phase 2 but < 0.41 → **Use this approach going forward**
   - Proceed to US tile collection & fine-tuning

---

## Documentation

| File | Purpose |
|---|---|
| `scripts/train_prune_first.py` | Prune-first entry point |
| `logs/phase_a_pruning.log` | Pruning phase transcript |
| `logs/phase_b_growing.log` | Growing phase transcript |
| `logs/prune_first_strategy.log` | Combined transcript |
| `output/retna_pruned_and_grown_inspect_pruned_and_grown.pdf` | Final inspection report |
| `models/retna_pruned_first.pt` | Pruned baseline (intermediate) |
| `models/retna_pruned_and_grown.pt` | Final checkpoint |

---

**Status**: Running. Phase A (aggressive pruning) ~10 min, then Phase B (30-cycle grow) ~2–3 hrs, then inspection ~5 min. Total est. 2.5–3.5 hrs.
