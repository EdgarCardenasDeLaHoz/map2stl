# Models Reference — Quick Lookup

## Current Best Model

**File**: `models/retna_wider_early.pt`  
**Loss**: 0.4157  
**Params**: 9.3k  
**Architecture**: [4,4,4,4,4,4,4,4,4]  
**Speed**: ~0.5 ms/tile (CPU)  
**Status**: ✅ Production ready  

---

## All Available Models

| Name | File | Loss | Params | Size | Use Case | Status |
|---|---|---|---|---|---|---|
| **Phase C (Wider Early)** | `retna_wider_early.pt` | **0.4157** | **9.3k** | 55 KB | 🏆 Production | ✅ Ready |
| Phase B (Grow Pruned) | `retna_pruned_and_grown.pt` | 0.4169 | 26.2k | 89 KB | Backup (larger) | ✅ Working |
| Phase A (Pruned) | `retna_pruned_first.pt` | — | 47.9k | 201 KB | Intermediate | ✓ Archive |
| Phase 2 (Two-Phase) | `retna_phase2_regression.pt` | 0.4434 | 75.5k | 310 KB | Baseline | ✓ Reference |
| Baseline | `retna_rebuild.pt` | 0.4557 | 75.5k | 309 KB | Original | ✓ Archive |

---

## Quick Comparison

```
Model                  Loss    Params   Δ Loss   Δ Params
────────────────────────────────────────────────────────
Baseline               0.4557  75.5k    —        —
Phase 2 (two-phase)    0.4434  75.5k    −2.7%    —
Phase B (grow)         0.4169  26.2k    −6.0%    −65%
Phase C (final) ✅     0.4157   9.3k    −8.8%    −88%
```

---

## Loading Phase C in Code

### Python
```python
import torch

model = torch.load('models/retna_wider_early.pt')
model.eval()

# Architecture: [4,4,4,4,4,4,4,4,4] (uniform 4-channel blocks)
# Input: (batch, 4, 128, 128) height tile [B, RGB, X, Y]
# Output: (batch, 1, 128, 128) predicted height [B, height, X, Y]
```

### Training (fine-tune)
```bash
python scripts/train_prune_first.py  # Use as starting point
# or
python -m tools.ml.train.grow_prune \
  --start-checkpoint models/retna_wider_early.pt \
  --tiles cache/height_tiles_us/ \
  --cycles 10 \
  --inner-epochs 20 \
  --lr 1e-5
```

---

## Inference

**Tile size**: 128×128 pixels  
**Input**: RGB satellite image + DEM  
**Output**: Per-pixel height (meters)  
**Speed**: ~0.5 ms/tile (CPU, single)  
**Batch speed**: ~50 ms/batch-100 (parallelizable)  

---

## Next Steps

### Short-term (Production)
✅ Deploy Phase C (retna_wider_early.pt)

### Medium-term (Improve Tall Buildings)
⏳ Phase D: Fine-tune on US high-rise tiles
- Collect Philadelphia, Chicago, NYC, Boston
- 10–20 epochs fine-tuning
- Target: Tall-building MAE <8m

### Long-term (Scaling)
- Expand to larger EU dataset (660 tiles)
- Integrate into web viewer
- Real-time STL generation

---

## Documentation

- **Full report**: `docs/plans/height_training/ml-training-final-report-2026-05-04.md`
- **Phase C details**: `docs/plans/height_training/phase-c-completion-and-final-decision.md`
- **Phase B details**: `docs/plans/height_training/phase-b-completion-summary.md`
- **Strategy design**: `docs/plans/height_training/prune-first-strategy.md`

---

## Model Inspection

- **Phase B PDF** (30 samples): `output/retna_pruned_and_grown_inspect_pruned_and_grown.pdf`
- **Phase C PDF** (30 samples): `output/retna_wider_early_inspect_wider_early.pdf`

Compare tall-building predictions between Phase B and Phase C to assess early-layer widening impact.

---

**Last updated**: 2026-05-04  
**Status**: Phase C selected as final model  
**Ready for**: Production deployment + US fine-tuning
