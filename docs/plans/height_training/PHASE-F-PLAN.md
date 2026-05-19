# Phase F: Prune-First Strategy toward 0.35 Loss

**Status**: Planning  
**Start**: Phase E best (0.4108 loss, 9.3k params)  
**Target**: 0.35–0.38 loss (breakthrough)  
**Estimated Duration**: 8–10 hours  
**Strategy**: Aggressive pruning + intensive cycles with 3-scope validation

---

## Objective

Push from Phase E (0.4108) toward the previously achieved ~0.35 loss level using an aggressive pruning-first approach with:
- **Scope 1: ALL** — Full validation set (baseline)
- **Scope 2: TALL** — Buildings >20m (historical, churches, cathedrals, skyscrapers)
- **Scope 3: COMPLEX** — High-gradient regions (intersections, complex shapes, landmarks)

---

## Current State

### Phase E Baseline
- **Loss**: 0.4108 (Cycle 47)
- **MAE**: 6.33m
- **IoU**: 0.493
- **Params**: 9,324
- **Model Size**: 57 KB
- **Architecture**: [4,4,4,4,4,4,4,4,4] (uniform)

### Available Datasets
```
Dataset                    Size    Tiles   Use Case
────────────────────────────────────────────────────────
height_tiles_combined       25M    100     Current (Amsterdam, Barcelona)
height_tiles_osm           128M    514     NEW: All regions (most diverse) ← USE FOR F
height_tiles_hr             95M    102     High-resolution European
height_tiles_eu11           43M    660     Small tiles, many regions
height_tiles_osm_small      25M    100     Subset for testing
```

**Strategy**: Use `height_tiles_osm` (514 tiles, all regions) for maximum signal and diversity.

---

## Three-Phase Approach

### Phase F-A: Aggressive One-Shot Pruning

**Goal**: Remove bottom 40% dead capacity in one cycle  
**Rationale**: Phase E reached a local minimum with uniform [4,4,4,4,4,4,4,4,4]. Pruning aggressively removes neuron-level dead capacity that growth/ablation cycles may have left behind.

**Configuration**:
```
Cycles:                1 (single cycle: prune + retrain)
Epochs:               30 (quick recovery)
Grow channels:         0 (only pruning, no growth)
Prune floor:          40% (aggressive)
Tolerance:           ±0.01 loss (allow degradation for learning)
Recovery epochs:      30 (retraining after prune)
Final floor:         40% of pruned architecture
LR:                  2e-5 (higher for recovery)
```

**Expected Result**:
- Loss: 0.4108 → 0.4120–0.4140 (temporary increase acceptable)
- Params: 9,324 → 5,500–6,500 (40% reduction)
- Signal: Clear view of which neurons are truly necessary

**Output**: `models/retna_pruned_aggressive.pt`

---

### Phase F-B: Intensive Growth/Prune Cycles

**Goal**: Exhaustive architecture search from pruned baseline toward 0.35  
**Rationale**: Aggressive pruning finds minimal skeleton. Intensive cycles allow:
- Fine-grained channel growth (0.3 channels/cycle)
- Immediate ablation (every cycle) to prevent bloat
- Ultra-low LR (5e-6) for fine-grained parameter optimization
- Maximum training depth (100 epochs/cycle)

**Configuration**:
```
Cycles:              60 (most cycles yet: exhaustive search)
Epochs/cycle:       100 (2.5× longer than Phase E)
LR:                5e-6 (2.5× lower: ultra-fine)
Grow channels:      0.3 (minimal, gradual growth)
Ablate:           Every 1 cycle (maximum pressure)
Smart-init jitter: 0.04 (highest exploration)
Batch size:         3
Final prune:
  Tolerance:       0.002 (tighter)
  Floor:           35%
  Retrain:         40 epochs
```

**Training Strategy**:
- **Cycles 1–20**: Recovery phase (learn from aggressive prune)
- **Cycles 21–40**: Growth phase (explore beneficial expansion)
- **Cycles 41–60**: Refinement phase (fine-tune optimal architecture)

**Expected Result**:
- Loss: 0.4120–0.4140 (F-A) → 0.35–0.38 (F-B)
- Params: 5,500–6,500 → 7,000–12,000 (selective growth)
- Cycles with sub-0.38: Target ≥20 elite cycles
- Cycles with sub-0.35: Target ≥5 cycles (breakthrough)

**Output**: `models/retna_v1_phase_f_final.pt`

---

## Tall Buildings & Complex Architecture Focus

### Scope 1: ALL (Baseline)
- Full validation set from `height_tiles_osm`
- ~80 train / ~20 val split
- Includes all building types, all heights

### Scope 2: TALL (>20m buildings)
**Implementation**:
- Filter validation tiles to regions with >20m buildings
- Track loss/MAE/IoU separately for tall buildings
- Document tall-building performance in final report

**Expected Challenge**:
- Larger buildings have larger prediction errors (MAE)
- IoU often drops due to edge detection in large areas
- Need aggressive regularization to prevent overfitting

**Buildings Included**:
- Historical cathedrals (Barcelona, Amsterdam, Paris)
- Modern skyscrapers (financial districts)
- Church spires, bell towers
- Historical town halls and monuments

### Scope 3: COMPLEX (High-gradient regions)
**Implementation**:
- Identify tiles with high spatial gradient in height maps
- Focus training on intersections, curved streets, mixed-height zones
- Log these separately; note if specific regions need dedicated data

**Expected Challenge**:
- Complex shapes require more fine-grained predictions
- Risk of local overfitting if not balanced
- May require larger architecture if achievable

---

## Data Collection Notes

### Current Gaps Identified
As Phase F runs, document:
- [ ] **Tall buildings**: Do we have enough >25m samples?
- [ ] **Churches/cathedrals**: Are church roof pitches well-represented?
- [ ] **Landmarks**: Are unique shapes (domes, spires) captured?
- [ ] **Complex districts**: Are dense, curved street layouts covered?
- [ ] **Non-European cities**: Do we have tall buildings outside EU?

### Future Collection Priorities
If Phase F identifies gaps:
1. **Tall building regions**: NYC, Tokyo, Dubai, Shanghai skyscrapers
2. **Historic centers**: Prague, Vienna, Rome; complex medieval layouts
3. **Religious landmarks**: Assemble cathedral/mosque/temple roof data
4. **Asian cities**: Curved roofs (temples, pagodas), different architecture styles

---

## Failure Modes & Mitigation

### Risk 1: Aggressive Pruning Too Extreme
- **If**: F-A loss jumps to >0.415
- **Mitigation**: Reduce prune floor to 30% and retry; allow higher recovery loss (±0.02)

### Risk 2: F-B Cycles Oscillate Without Converging
- **If**: Loss bounces 0.39–0.42, never sustains <0.38
- **Mitigation**: Increase LR patience, reduce learn rate to 2e-6, extend cycles to 80

### Risk 3: Cannot Reach 0.35 Within 60 Cycles
- **If**: Best remains 0.37–0.38 after 60 cycles
- **Mitigation**: 
  - Declare 0.37–0.38 as new baseline (still +0.03 vs Phase E)
  - Optional Phase G: Focus only on tall buildings (fine-tune on filtered dataset)
  - Document architectural limits (may be approaching theoretical minimum)

### Risk 4: Tall Building MAE Remains High
- **If**: >25m buildings still have 7–10m MAE
- **Mitigation**: Create dedicated tall-building dataset for Phase G

---

## Success Metrics

| Metric | Target | Success | Note |
|--------|--------|---------|------|
| **Best loss** | < 0.38 | 0.35–0.38 | Major goal |
| **vs Phase E** | −0.03+ | −0.03+ | 0.7%+ improvement |
| **All-scope loss** | < 0.39 | 0.36–0.39 | Full validation |
| **Tall-building loss** | < 0.41 | TBD | Separate metric |
| **Complex-region loss** | < 0.40 | TBD | Separate metric |
| **Params** | 7k–12k | TBD | Selective growth |
| **Elite cycles** | ≥5 sub-0.35 | TBD | Reliability |
| **Data gaps** | Document | TBD | For future phases |

---

## Timeline

| Phase | Duration | Cumulative | Status |
|-------|----------|-----------|--------|
| F-A (prune) | 20 min | 20 min | Immediate |
| F-B (60 cycles) | ~7.5 hrs | 7:40 | Main effort |
| Inspection + reporting | 30 min | 8:10 | Final |
| **Total** | **~8.25 hrs** | — | — |

---

## Execution

### Step 1: Start Phase F
```bash
cd strm2stl
python scripts/train_phase_f_prune_first.py
```

**Logs**:
- `logs/phase_f_a_aggressive_prune.log` (F-A)
- `logs/phase_f_b_intensive_cycles.log` (F-B)
- `logs/inspect_pruneFirst.log` (inspection)

### Step 2: Monitor Progress
- Tail Phase F-B log: Look for first cycle <0.38
- Check for elite cycles (sub-0.35) after Cycle 30
- Note any tall-building or complex-region data gaps

### Step 3: Extract Results
- Best checkpoint: `models/retna_v1_phase_f_final.pt`
- Inspection PDF: `output/retna_v1_phase_f_final_inspect_pruneFirst.pdf` (60 samples)

### Step 4: Report
- Compare Phase F best vs Phase E (0.4108)
- Identify which cycle achieved lowest loss
- Document data gaps for future phases
- Recommend next phase (G for tall buildings, or production deployment)

---

## Decision Tree (After Phase F)

```
Is Phase F best < 0.38?
├─ YES: 0.35–0.37 loss achieved
│   ├─ Deploy Phase F as production model (major upgrade)
│   └─ Optional Phase G: Fine-tune on tall buildings for +0.5% improvement
├─ MAYBE: 0.38–0.40 loss achieved
│   ├─ Improvement vs Phase E, but modest
│   ├─ Consider Phase G for tall buildings or complex regions
│   └─ Evaluate if production-ready or need further tuning
└─ NO: >0.40 loss (worse than Phase E)
    ├─ Aggressive pruning too extreme
    ├─ Revert to Phase E (0.4108 still best)
    └─ Document architectural constraints
```

---

## Data Collection Checklist

During Phase F, assess need for:
- [ ] Tall-building focused dataset (>25m, clear 3D roofs)
- [ ] Cathedral/church/mosque roof data (curved, pitched, domed)
- [ ] Asian architecture (curved roofs, pagodas, temples)
- [ ] Dense urban intersections (complex height transitions)
- [ ] Non-European cities (tall buildings in different styles)

If gaps identified:
- [ ] Create collection plan (which regions, cities, data sources)
- [ ] Estimate effort (hours, coverage, 3D model quality)
- [ ] Prioritize for Phase G or future rounds

---

## Conclusion

Phase F combines aggressive pruning (F-A) with intensive refinement (F-B) to push from Phase E (0.4108) toward the 0.35 loss target. Using the full OSM dataset (514 tiles) and tracking tall buildings + complex regions separately, we aim to:

1. **Discover minimal-but-powerful architecture** via aggressive pruning
2. **Exhaustively search for optimal channels** via 60 intense cycles
3. **Identify data gaps** for tall buildings and complex geometry
4. **Reach 0.35–0.38 loss** (major breakthrough) or document constraints

Estimated 8–10 hours of training. Potential for significant loss reduction and clearer understanding of model architecture limits.

---

**Next**: Run Phase F and monitor for breakthrough results or data gaps requiring collection.
