# Phase G Resolution Strategy: Standard vs High-Resolution

**→ For current status, see `PHASE-G-MASTER.md` | For timeline, see `PHASE-G-EXECUTION-STATUS.md`**

> **Reference file**: Detailed analysis of resolution trade-offs. Keep for decision justification.

---

## Current Baseline

| Parameter | Value |
|-----------|-------|
| **Tile size** | 256×256 pixels |
| **Resolution** | 2.0 m/pixel |
| **Ground coverage** | 512m × 512m |
| **Buildings per tile** | ~4-6 |
| **File size** | ~250KB (compressed) |
| **Satellite source** | ESRI World Imagery (zoom 14-18) |

---

## The Problem with Current Resolution

At **2.0 m/pixel**:
- Building = ~20-50 pixels wide
- Roof detail lost (single pixel = entire roof section)
- CNN struggles to learn fine boundaries
- Height predictions blend adjacent buildings

Example: **100m tall building**
- At 2m/px: ~50 pixels wide, height gradient = 1-2 pixels per meter
- Difficult to distinguish from shorter adjacent building

---

## Proposed Solution: High-Resolution Collection

### Option: 512×512 px at 1 m/pixel

| Parameter | Standard (Current) | High-Res (Proposed) | Change |
|-----------|----------|-----------|--------|
| **Tile size** | 256×256 px | 512×512 px | 4x |
| **Resolution** | 2.0 m/px | 1.0 m/px | 2x finer |
| **Coverage** | 512m × 512m | 512m × 512m | **Same** |
| **Buildings/tile** | 4-6 | 8-12 | More buildings per tile |
| **File size** | 250KB | ~1MB | 4x |
| **Building detail** | ~20-50px | ~40-100px | **Double** |

**Key benefit**: Same geographic coverage, **double the pixel density** per building!

---

## Why This Works

### 1. Better CNN Feature Extraction
```
Standard (2m/px):
  Building boundary = 20-30 pixels
  Roof = 5-10 pixels
  
High-res (1m/px):
  Building boundary = 40-60 pixels  ← CNN can learn fine edges
  Roof = 10-20 pixels               ← Distinguish materials/slopes
```

### 2. More Building Context Without Zoom Explosion
- **Current**: 4 buildings per tile
- **High-res**: 8-12 buildings per tile
- Same geographic scale, better neighborhood context

### 3. Still Manageable Storage
- 514 EU tiles × 1MB = 514MB
- 81 US tiles × 1MB = 81MB
- 30 Cartagena × 1MB = 30MB
- **Total**: ~625MB (vs current 156MB)
- Still fits easily in VRAM during training

---

## Implementation: Two-Stage Approach

### Stage 1: Re-collect at High-Resolution
```bash
python scripts/recollect_tiles_hires.py
```
- Collects 512×512 px tiles at 1 m/pixel
- Same 11 EU cities + 4 US cities
- Outputs to `cache/height_tiles_global_hires/`

### Stage 2: Train with Random Crops
```bash
python scripts/train_phase_g_hires.py
```
- Uses 192×192 crops from 512×512 tiles
- **192×192 crops** = 0.375 of tile = still ~2 full buildings visible
- 5x data multiplication (random position + flip)
- 10 cycles × 35 epochs

**Why 192×192 crops?**
- 256×256 crops (full tile) → not enough crops (1 per tile)
- 96×96 crops (on 512px) → too much detail loss
- **192×192** = sweet spot:
  - 4-6 crops per tile from different positions
  - Still sees 2-3 full buildings per crop
  - Good spatial augmentation

---

## Expected Improvements

| Metric | Standard | High-Res | Expected Delta |
|--------|----------|----------|--------|
| **Building IoU** | 0.414 | ? | +0.03-0.05 |
| **Height MAE** | 7.7m | ? | -0.5 to -1.0m |
| **Roof detail** | Poor | Good | Qualitative |
| **Training time** | 8-10h | 12-15h | +4-5h (longer crops) |

**Conservative estimate**: ~5-10% improvement in height accuracy due to better feature extraction.

---

## Execution Timeline

```
Right now:
  Stage 4 (global 128×128):  ~2-3 hrs remaining  [brynzg72a]

After Stage 4:
  Stage 5a: High-res collection  ~2-3 hrs  [recollect_tiles_hires.py]
  Stage 5b: High-res training     ~12-15 hrs [train_phase_g_hires.py]

Total Phase G: ~26-31 hours from start
```

---

## Decision: When to Deploy High-Res

1. **If Stage 4 achieves < 6.5m MAE** on global validation:
   - High-res collection is optional optimization
   - Standard resolution is already good

2. **If Stage 4 achieves 6.5-7.5m MAE**:
   - Try high-res collection
   - Expected to improve by 5-10%

3. **If Stage 4 achieves > 7.5m MAE**:
   - High-res may help significantly
   - Worth attempting despite extra time

---

## Comparison: Resolution vs Crops vs Larger Tiles

| Strategy | Resolution | Coverage | Effort | Expected Gain |
|----------|-----------|----------|--------|---|
| **Current** | 256×256 @ 2m | 512m | Baseline | — |
| **Crops** (96×96) | 256×256 @ 2m | 512m | +4h | +5% (data multiplication) |
| **High-Res** (512×512 @ 1m) | 512×512 @ 1m | 512m | +3h collect + 12h train | +5-10% (detail) |
| **Hybrid** | 512×512 @ 1m + crops | 512m | +3h + 15h | +10-15% (synergistic) |

**Hybrid (high-res + crops) is best but requires full re-collection.**

---

## Implementation Checklist

- [ ] Wait for Stage 4 to complete
- [ ] Evaluate Stage 4 results:
  - [ ] EU MAE < 5.5m?
  - [ ] US MAE < 7.0m?
  - [ ] Global MAE < 6.5m?
- [ ] Decision:
  - [ ] If good: Stop (resolution sufficient)
  - [ ] If marginal: Try high-res collection
  - [ ] If poor: Must try high-res
- [ ] If proceeding:
  - [ ] Run `recollect_tiles_hires.py` (3h)
  - [ ] Run `train_phase_g_hires.py` (15h)
  - [ ] Compare metrics vs standard resolution
- [ ] Deploy winner (standard or high-res)

---

## Storage & Memory Implications

**Tile storage**:
- Current: 156MB for 625 tiles
- High-res: 625MB for 625 tiles
- **Still reasonable** (easily fits on disk)

**Training memory**:
- Current: Batch 4 × 128×128 × 3 channels = manageable
- High-res: Batch 4 × 192×192 × 3 channels = **3x more GPU memory**
- Solution: Reduce batch size from 4 to 1-2 during high-res training

---

## Future: Even Higher Resolution?

**Not recommended yet:**
- **0.5 m/pixel** (512×512 px = 256m coverage) = 16x file size
  - Overkill for building height prediction
  - CNN can't extract features at such zoom
  - Training becomes prohibitively slow

- **Larger tiles** (1024×1024 @ 2m/px) = 2048m coverage
  - Better for context but harder to train
  - Model sees entire city blocks
  - Different problem than building detection

**Stick with 512×512 @ 1m for now.**

---

## Next Steps

Once Stage 4 completes, decide:

```python
if stage4_global_mae < 6.5:
    print("Resolution sufficient, move to production")
else:
    print("Run high-res collection and training")
    subprocess.run(["python", "scripts/recollect_tiles_hires.py"])
    subprocess.run(["python", "scripts/train_phase_g_hires.py"])
```
