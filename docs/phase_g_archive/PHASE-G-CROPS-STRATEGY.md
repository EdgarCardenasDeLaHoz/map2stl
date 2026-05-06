# Phase G Multi-Scale Crop Training Strategy

**→ For current status, see `PHASE-G-MASTER.md`**

> **Reference file**: Detailed analysis of crop augmentation approach. Keep for implementation reference.

---

**Problem**: Current training uses 128×128 tiles, which only fit ~4 buildings across. This limits:
- Model's ability to learn building detection (not enough context per building)
- Data augmentation (no spatial variation within tiles)
- Generalization to different building densities/scales

**Solution**: Train on **96×96 random crops extracted from 256×256 tiles**

---

## Why This Works

### 1. Data Multiplication Without Storage Overhead
- **Before**: 625 tiles × 1 sample = 625 training samples
- **After**: 625 tiles × 7-10 crops per tile = 4,375-6,250 effective samples
- **Storage cost**: 0 (crops are extracted on-the-fly, not stored)

### 2. Multi-Scale Learning
Each crop comes from a random position in the tile:
```
Original 256x256 tile:
┌─────────────────────────────────────┐
│  B1  ┌──96x96 crop 1──┐             │
│      │     B2      B3 │             │
│  B4  │  ┌──────────┐  │  B5         │
│      └──│ Building │──┘             │
│      ┌──┴──────────┴─┐──────────┐   │
│      │  ┌──96x96 crop 2─┐       │   │
│      │  │   B3    B6    │  B7   │   │
│      │  └───────────────┘       │   │
└──────┴────────────────────────────┘
```
- Crop 1: sees B1, B2, B3 in different frame
- Crop 2: sees B3, B6, B7 in different frame
- **Result**: Model learns building detection regardless of position/scale/density

### 3. Implicit Augmentation
Random crops + flips give:
- Rotation invariance (already in data via random flip)
- Scale variance (crops from different parts of tile)
- Context variety (different neighboring buildings)

---

## Implementation

### Modified Dataset (`HeightTileDataset`)
```python
HeightTileDataset(
    tile_paths,
    tile_size=256,       # Original tile size in .npz files
    crop_size=96,        # Extract 96x96 crops during training
    augment=True,        # Enable random crops + flips
)
```

**Key features**:
- `__len__()` multiplies sample count by ~7 when `augment=True`
- `__getitem__()` extracts random crop at random position
- Random horizontal/vertical flips applied to crop
- No changes to stored tiles (still 256×256)

### Training Script
```bash
python scripts/train_phase_g_with_crops.py
```

Runs grow-prune with:
- `--tile-size 96` (model trained on 96×96 crops)
- `--batch-size 8` (smaller batch due to local crop context)
- `--cycles 12` (more cycles for convergence on more samples)
- `--inner-epochs 30` (longer per cycle)

---

## Expected Improvements

| Metric | Current (128×128) | Expected (96×96 crops) | Reason |
|--------|-------------|-----------|--------|
| **Building detection** | Moderate | High | More samples, varied context |
| **Height MAE** | ~7.7m | ~5.5-6.5m | Better localization from crops |
| **IoU (segmentation)** | 0.414 | 0.45-0.50 | Crop variety forces robust masks |
| **Generalization** | EU-biased | Global-robust | Random positions reduce memorization |
| **Training samples** | 625 | ~4,000 | 6.4x multiplication |

---

## Comparison: Full Tile vs Crops

### Full Tile Training (Current)
```
Input: 128x128 → Conv blocks → Output: 128x128
       (1 sample per tile)
```
- Sees entire tile context
- **Problem**: Can memorize tile patterns, not generalizing to arbitrary areas
- **Problem**: Limited augmentation (only flips + rotation)

### Crop Training (Proposed)
```
Input: 96x96 random crop → Conv blocks → Output: 96x96
       (7-10 crops per tile, random position + flip)
```
- Sees building-local context only
- **Advantage**: Forced to learn building detection, not tile memorization
- **Advantage**: Implicit spatial augmentation
- **Advantage**: More samples for convergence

---

## What's NOT Changed

✅ **Tile collection**: Still using 256×256 tiles (no change to data pipeline)
✅ **Model architecture**: Same retna_pruned starting point
✅ **Loss function**: Same MSE + Dice loss
✅ **Geographic coverage**: Same 625 global tiles (EU + US + Cartagena)
❌ **Roads**: Already excluded (OSM filtering limits to buildings)

---

## Timeline

| Stage | Duration | Status |
|-------|----------|--------|
| Stage 1: OSM training (128×128) | 10 hrs | ✅ Complete |
| Stage 4: Global training (128×128) | ~8 hrs | 🔄 Running |
| **Stage 5: Crop training (96×96)** | **8-10 hrs** | **Pending** |

Total Phase G: ~26-28 hours from start to finished crop model.

---

## Validation Plan

1. **Extract per-tile metrics** on final crop model:
   ```bash
   python scripts/extract_phase_g_tile_metrics.py \
     --checkpoint models/retna_phase_g_global_crops.pt
   ```

2. **Compare regions**:
   - EU tiles (should match or beat Stage 1: ~5.5m)
   - US tiles (should show improvement over Stage 4: <6.5m)
   - Cartagena (IoU > 0.55)

3. **Regional breakdown**:
   ```python
   # Compute per-region MAE
   eu_mae = metrics[metrics['city'].isin(['Amsterdam', 'Barcelona', ...])]['mae'].mean()
   us_mae = metrics[metrics['city'].isin(['Philadelphia', 'NYC', ...])]['mae'].mean()
   ```

---

## Future Optimization

If needed, can further optimize:
- **Smaller crops** (64×64): 15-20x data multiplication, but model sees less context
- **Overlapping crops**: Higher spatial coverage but more computation
- **Adaptive crop size**: Vary size based on building density (osmnx metadata)
