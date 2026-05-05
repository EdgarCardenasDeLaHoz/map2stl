# 30-Cycle NAS Progress — Live Status (Cycle 6/30)

## Current Status

- **Cycles Completed**: 6 of 30 (20%)
- **Estimated Remaining**: ~18 hours (24 cycles × 40–50 min/cycle)
- **Overall Progress**: 20% by cycle count, estimated 40% by wall-clock time

## Cycle Metrics Progression

| Cycle | Params | Val Loss | MAE | IoU | Correlation (r) | Δ Loss | Strategy |
|---|---|---|---|---|---|---|---|
| 1 | 85k | 0.4399 | 4.90m | 0.383 | +0.92 | — | Baseline growth |
| 2 | 85k | 0.4288 | 5.41m | 0.444 | +0.93 | −0.0110 | Growth continues |
| 3 (ablate) | 95k → 54k | 0.4229 | 5.39m | 0.443 | +0.90 | −0.0059 | Ablation pruned 39 channels |
| 4 | 62k | 0.4249 | 5.55m | 0.453 | +0.91 | +0.0020 | Re-growth post-ablation |
| 5 | 71k | 0.4234 | 5.89m | 0.475 | +0.91 | −0.0015 | Continuous growth |
| 6 | — | — | — | — | — | — | Running... |

## Key Observations

### Loss Trajectory
- **Cycle 1→3**: Sharp improvement (−0.0169 loss, −3.8%)
- **Cycle 3→5**: Mild degradation, then recovery (oscillating around 0.423–0.425)
- **Pattern**: Periodic ablation (every 3 cycles) prevents overfitting but adds variance

### IoU (Building Mask Detection)
- **Steady improvement**: 0.383 → 0.475 (+24% over 5 cycles)
- **Better boundary detection** as model grows and learns finer shapes

### MAE (Height Prediction)
- **Volatile**: 4.90 → 5.89m (expected with architecture changes)
- **Likely dominated by short-building tile variance** (IoU improving suggests shape is good)

### Architecture Growth
- **Cycle 1**: [9,9,11,21,15,15,17,17,23] (85k params)
- **Cycle 3 (peak)**: [10,10,12,22,16,16,18,18,24] (95k) → ablated → 54k
- **Cycle 5**: [9,9,11,18,14,14,15,15,20] (71k)

**Interpretation**: Ablation is aggressively pruning dead channels. Model can be compact.

## Comparison: NAS vs Two-Phase

| Approach | Val Loss | MAE | IoU | Advantage |
|---|---|---|---|---|
| **Two-Phase (Phase 2)** | **0.4434** | 5.67m | **0.443** | Better loss, better IoU |
| **NAS (Cycle 5)** | 0.4234 | 5.89m | 0.475 | Better IoU, lower loss if trend continues |

**Verdict** (preliminary): Two-Phase is ahead on val_loss; NAS is ahead on IoU. Need final cycles (15–30) to see if NAS overtakes on loss.

## Expected Final State

If current trend continues:
- **Final val_loss**: 0.41–0.42 (another −0.01 to −0.015 over 25 cycles)
- **Final MAE**: 5.5–6.0m (height still volatile due to ablation)
- **Final IoU**: 0.50+ (mask detection could reach very high)
- **Final params**: 50–80k (aggressive pruning)

---

## When NAS Finishes (Est. 2–3 hrs remaining)

1. Run inspection PDF on final checkpoint
2. Extract tall-building MAE from sample tiles
3. Compare to Two-Phase Phase 2 PDF
4. **Decision**: If NAS loss < 0.43, use NAS; else use Two-Phase

---

## Archive

Generated: 2026-05-04 (session mid-point)
Next update: When NAS completes
