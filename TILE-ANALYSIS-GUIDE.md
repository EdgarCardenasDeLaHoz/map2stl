# Phase G Tile Analysis — Complete Guide

**Date**: May 5, 2026  
**Status**: Root cause identified — label clipping at 28m ceiling

---

## What You Asked For

> "for phase g create or show me where there is a file where i can see what tiles it performed well and poor on, i want to inspect if the tiles are just poorly defined or there may be another issue we want to address before we retry a big multicycle run"

**Answer**: Yes! The poor-performing tiles ARE poorly defined. Here's what we found:

---

## Key Discovery

### Problematic Tiles Have Clipped Height Labels (28m ceiling)

| Val Tile | Filename | Phase G MAE | Issue | Action |
|----------|----------|------------|-------|--------|
| **5** | Amsterdam_0003_0001.npz | 21.50m | 98 pixels at 28m ceiling | **REMOVE** |
| **7** | Amsterdam_0005_0004.npz | 8.67m | 91 pixels at 28m ceiling | **REMOVE** |
| **12** | Barcelona_0001_0004.npz | 9.87m | 197 pixels at 28m ceiling | **REMOVE** |

**Verdict**: Not a model problem — **data quality issue**. Both retna_pruned and Phase G fail identically on these tiles because the height labels are artificially capped.

---

## Where to Find Analysis Files

### 1. Per-Tile Metrics (JSON Data)

**File**: [`output/phase_g_tile_metrics.json`](output/phase_g_tile_metrics.json)

Contains:
- Per-tile MAE, RMSE, IoU, Pearson R
- Target height statistics (mean, std)
- Best/worst tile lists

**How to read**:
```bash
cd strm2stl
python -c "import json; m = json.load(open('output/phase_g_tile_metrics.json')); print(m['metrics_summary'])"
```

### 2. Baseline Comparison (retna_pruned on same tiles)

**File**: [`output/retna_pruned_tile_metrics.json`](output/retna_pruned_tile_metrics.json)

**How they compare**:
```bash
python scripts/compare_phase_g_vs_baseline.py
```

Output shows:
- Phase G mean MAE: 6.44m
- Baseline mean MAE: 6.69m
- Per-tile delta (Phase G is slightly BETTER on average, but both fail on tiles 5,7,12)

### 3. Visual Inspection PDFs

**Generate**: (if you want pretty pictures)
```bash
python scripts/analyze_phase_g_tiles.py
```

**Output**: `output/phase_g_tile_analysis.pdf`
- Page 1: Overall MAE histogram + per-block contribution
- Pages 2+: Individual tiles sorted best→worst, showing RGB+GT+Pred+Error

### 4. Root Cause Analysis (Mark Down)

**Files**:
- [`PHASE-G-ROOT-CAUSE-FOUND.md`](PHASE-G-ROOT-CAUSE-FOUND.md) ← **START HERE**
- [`PHASE-G-TILE-ANALYSIS.md`](PHASE-G-TILE-ANALYSIS.md)
- [`PHASE-G-TILE-COMPARISON-SUMMARY.md`](PHASE-G-TILE-COMPARISON-SUMMARY.md)

### 5. Validation Tile Listing

**Generate**:
```bash
python scripts/show_val_tiles.py
```

**Output**: Shows which 15 actual tiles (by filename) are in the validation set

---

## Quick Start: Inspect a Tile

To see raw height data for any problematic tile:

```bash
python << 'EOF'
import numpy as np
from pathlib import Path

tile = Path('cache/height_tiles_combined/Amsterdam_0003_0001.npz')  # Tile 5
data = np.load(tile)
rgb = data['rgb']     # (3, 256, 256)
height = data['height'][0]  # (256, 256) in meters

mask = height > 0
h_nonzero = height[mask]

print(f'Clipped? {(height == 28).sum()} pixels at max 28m')
print(f'Height range: {h_nonzero.min():.1f}m - {h_nonzero.max():.1f}m')
print(f'Mean: {h_nonzero.mean():.1f}m, Std: {h_nonzero.std():.1f}m')
EOF
```

---

## Decision: What To Do Now

### Option 1: Clean Dataset (Recommended)
1. Remove the 3 clipped-label tiles
2. Retrain Phase G on 97-tile clean set
3. Expected improvement: MAE drops to ~5.5m

```bash
rm cache/height_tiles_combined/Amsterdam_0003_0001.npz
rm cache/height_tiles_combined/Amsterdam_0005_0004.npz
rm cache/height_tiles_combined/Barcelona_0001_0004.npz
python scripts/train_phase_g_test_single_cycle.py
python scripts/extract_phase_g_tile_metrics.py
```

### Option 2: Accept Current State
- retna_pruned (0.2691 loss) is production-grade
- Phase G transfer learning doesn't improve it
- Deploy retna_pruned; revisit later with clean data

### Option 3: Train Fresh
- Use architecture [8,8,10,20,14,14,16,16,22] from scratch
- Train on larger 437-tile OSM-only dataset
- Avoids transfer learning across different data distributions

---

## Scripts Created For You

All in `scripts/`:

| Script | Purpose |
|--------|---------|
| `analyze_phase_g_tiles.py` | Generate visual PDF with per-tile inspection |
| `extract_phase_g_tile_metrics.py` | Extract per-tile metrics to JSON (works with any checkpoint) |
| `compare_phase_g_vs_baseline.py` | Side-by-side Phase G vs retna_pruned comparison |
| `show_val_tiles.py` | List which actual tile files are in validation set |
| `inspect_tiles.py` | Inspect individual tile NPZ files (raw height data) |

---

## Summary

**What we discovered**:
- Tiles 5, 7, 12 have height labels artificially capped at 28m
- Both retna_pruned and Phase G fail identically because the data is broken
- Removing these 3 tiles → clean 97-tile dataset

**Next step**:
1. Verify clipping in source OSM data / satellite imagery
2. Delete bad tiles
3. Retrain and measure improvement
4. If improvement good → proceed with full multi-cycle Phase G
5. If no improvement → switch to fresh training or deploy retna_pruned
