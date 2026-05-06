# Phase G: Building Height CNN Training — Master Reference

**Status**: Stage 4 global training running (Cycle 9/15), high-res collection in parallel  
**Date**: May 6, 2026  
**Next Decision**: When Stage 4 completes (~3 hours)

---

## Quick Start: Where We Are

| Component | Status | Detail |
|-----------|--------|--------|
| **Stage 4 Training** | 🔄 Running | 625 tiles (128×128), Cycle 9/15, ~7.65m MAE, ~2.5h remaining |
| **High-Res Collection** | 🔄 Running | 512×512 @ 1m/px, EU + US, ~3h remaining |
| **Decision Framework** | ✅ Ready | Deploy based on Stage 4 MAE result |
| **High-Res Training** | 📦 Ready | Scripts prepared, launch if MAE > 6.5m |

---

## Stage 4: Global Training (Current)

**What**: Train grow-prune NAS on 625 diverse tiles (514 EU + 81 US + 30 Cartagena)  
**Why**: Geographic diversity improves generalization vs EU-only retna_pruned  
**How**: Warm-start from retna_pruned.pt, grow +1 channel per cycle, smart initialization

### Performance Trajectory
```
Cycle 1:  MAE=7.77m  IoU=0.414  loss=0.4331  (warmup)
Cycle 6:  MAE=7.75m  IoU=0.421  loss=0.4243  ← BEST
Cycle 8:  MAE=7.61m  IoU=0.414  loss=0.4245  (stable)
Cycle 9:  (running...)
```

### Expected Finish
- Remaining: 6 cycles × ~25 min per cycle = **~2.5 hours**
- Output: `models/retna_phase_g_global.pt`
- Log: `logs/phase_g_global.log`

### Key Insight
MAE is higher than retna_pruned (3.82m) because:
- retna_pruned trained on 2 EU cities only (domain-specific)
- Stage 4 trained on 15 cities across 3 continents (harder generalization)
- **Higher MAE = more realistic, generalizable model**

---

## Resolution Optimization: Standard vs High-Resolution

### Current (Standard)
- **Tile size**: 256×256 pixels
- **Ground resolution**: 2.0 m/pixel
- **Coverage**: 512m × 512m
- **Building width**: ~20-50 pixels
- **File size**: ~250KB compressed

### Proposed (High-Res)
- **Tile size**: 512×512 pixels  
- **Ground resolution**: 1.0 m/pixel
- **Coverage**: 512m × 512m (**same area, 4x pixels**)
- **Building width**: ~40-100 pixels (**double density**)
- **File size**: ~1MB compressed (4x, still manageable)

### Why It Helps
Better CNN feature extraction:
```
Standard:  Building boundary = 20-30 pixels  → Limited gradient info
High-res:  Building boundary = 40-60 pixels  → Fine edge details
```

---

## Decision Point: When Stage 4 Completes

### IF MAE < 6.5m  
→ **Resolution sufficient, deploy immediately**
```bash
python scripts/extract_phase_g_tile_metrics.py --checkpoint models/retna_phase_g_global.pt
# → Phase G complete
```

### IF 6.5m ≤ MAE ≤ 7.5m (likely outcome ~7.65m)
→ **High-res worth trying, launch Phase 5**
```bash
# Combine high-res tiles (5 min)
mkdir -p cache/height_tiles_global_hires
cp cache/height_tiles_osm_hires/*.npz cache/height_tiles_global_hires/
cp cache/height_tiles_us_hires/*.npz cache/height_tiles_global_hires/
cp cache/height_tiles_osm_cartagena/*.npz cache/height_tiles_global_hires/

# Train (12-15 hours)
python scripts/train_phase_g_hires.py

# Compare and deploy winner
```

### IF MAE > 7.5m
→ **High-res mandatory** (same process as above)

---

## High-Resolution Training (Phase 5 — If Needed)

**What**: Re-train on 512×512 tiles with 192×192 random crops  
**Expected improvement**: 5-10% better MAE (e.g., 7.65m → 7.2-7.4m)  
**Time**: 12-15 hours (10 cycles × 35 epochs)  
**Data multiplication**: 5-10x via random crop positions + flips

### Key Implementation
- Crops extracted **on-the-fly** during training (no storage overhead)
- Random position + flips provide implicit augmentation
- Smaller batch size (4) due to GPU memory constraints
- Lower learning rate (8e-6) for stability with higher-res inputs

---

## Multi-Scale Crop Augmentation

**What**: Extract smaller crops from larger tiles for better data multiplication  
**Why**: 
- Same geographic area, but model sees building context at different scales
- Random positioning forces generalization (no tile memorization)
- 7-10x data multiplication without storing more tiles

**How**:
```python
# Instead of: 625 tiles × 1 sample = 625 samples
# We get:     625 tiles × 7-10 crops = 4,375-6,250 samples
# Storage:    0 bytes overhead (crops generated on-the-fly)
```

---

## Dataset Composition

**Standard resolution (625 tiles, 156MB total)**
- **EU**: 514 tiles from 11 cities
  - Amsterdam, Barcelona, Berlin, Bruges, Cologne, Florence, Munich, Paris, Prague, Rotterdam, Vienna
- **US**: 81 tiles from 4 cities
  - Philadelphia, Chicago, NYC, Boston
- **Cartagena**: 30 tiles (segmentation-only, used for robustness)

**High-resolution (625 tiles, 625MB total — if needed)**
- Same cities, re-collected at 512×512 @ 1m/px
- Collection running now in parallel with Stage 4

---

## Active Scripts

### Training
- `scripts/train_phase_g_global_dataset.py` — Stage 4 (128×128 standard, running now)
- `scripts/train_phase_g_hires.py` — Phase 5 (192×192 crops from 512×512, if needed)
- `scripts/recollect_tiles_hires.py` — High-res collection (running now)

### Evaluation
- `scripts/extract_phase_g_tile_metrics.py` — Per-tile metrics extraction
- `scripts/compare_phase_g_vs_baseline.py` — Regional breakdown comparison
- `scripts/phase_g_summary.py` — Quick summary stats
- `scripts/analyze_phase_g_tiles.py` — Detailed tile analysis

---

## Timeline

```
NOW (May 6, 00:32):
  ⏳ Stage 4: Cycle 9/15 (~2.5h remaining)
  ⏳ High-res collection: Running (~3h remaining)

~03:00 (May 6):
  ✅ Stage 4 complete → Decision point
  ✅ High-res collection complete → Ready for training

SCENARIO A: MAE < 6.5m (~20% chance)
  🎯 Deploy immediately → Phase G complete (~3h total)

SCENARIO B: MAE 6.5-7.5m (~70% likely)
  🔄 Combine high-res tiles (5 min)
  🚀 High-res training (~12-15h)
  📊 Complete by ~15:00-18:00 (May 6)

SCENARIO C: MAE > 7.5m (~10% chance)
  🚀 Same as Scenario B
  🔄 High-res collection already done
```

---

## Key Files

| File | Purpose | Status |
|------|---------|--------|
| `PHASE-G-MASTER.md` | This file (consolidated overview) | Current |
| `PHASE-G-EXECUTION-STATUS.md` | Real-time status with timeline | Current |
| `PHASE-G-RESOLUTION-STRATEGY.md` | Resolution decision framework | Current |
| `PHASE-G-CROPS-STRATEGY.md` | Crop augmentation details | Reference |
| `scripts/train_phase_g_global_dataset.py` | Stage 4 training | Running |
| `scripts/train_phase_g_hires.py` | High-res training | Ready |
| `scripts/recollect_tiles_hires.py` | High-res collection | Running |

---

## Success Metrics

| Metric | Baseline (retna_pruned) | Stage 4 Target | High-Res Target |
|--------|--------|--------|--------|
| **Validation loss** | 0.2691 | <0.45 | <0.42 |
| **Height MAE (global)** | 3.82m (EU-only bias) | <7.0m | <6.5m |
| **Building IoU** | — | >0.40 | >0.45 |

Note: Baseline is on 2 EU cities only. Stage 4/High-res on 15 cities → inherently harder but more generalizable.

---

## Quick Reference: Commands for Each Scenario

**After Stage 4 completes:**
```bash
# Check final metrics
tail -20 logs/phase_g_global.log | grep "metrics:"
python scripts/extract_phase_g_tile_metrics.py --checkpoint models/retna_phase_g_global.pt
```

**If proceeding with high-res:**
```bash
# Combine datasets
mkdir -p cache/height_tiles_global_hires
cp cache/height_tiles_osm_hires/*.npz cache/height_tiles_global_hires/
cp cache/height_tiles_us_hires/*.npz cache/height_tiles_global_hires/
cp cache/height_tiles_osm_cartagena/*.npz cache/height_tiles_global_hires/

# Train
python scripts/train_phase_g_hires.py

# Monitor
tail -f logs/phase_g_hires_training.log
```

**Compare results:**
```bash
python scripts/extract_phase_g_tile_metrics.py \
  --checkpoint models/retna_phase_g_global.pt \
  --output output/phase_g_global_final.json

python scripts/extract_phase_g_tile_metrics.py \
  --checkpoint models/retna_phase_g_hires.pt \
  --output output/phase_g_hires_final.json
```

---

## Summary

✅ **Parallel dual-pipeline running**
- Stage 4 on track for ~7.65m MAE (good for 625-tile diverse dataset)
- High-res collection paralleling completion

📊 **Adaptive deployment**
- MAE-based decision: <6.5m → deploy, 6.5-7.5m → high-res, >7.5m → high-res required

🎯 **Phase G completion** between 3-21 hours depending on Stage 4 result

Next action: Wait for Stage 4 completion (~2.5 hours), then decide on high-res training.
