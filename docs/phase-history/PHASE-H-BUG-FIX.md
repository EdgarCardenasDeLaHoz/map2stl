# Phase H Critical Bug Fix — Height Scaling Issue

**Date**: 2026-05-06 19:08 UTC  
**Status**: FIXED & RESTARTED  
**Impact**: Critical (training metrics were off by 200x factor)

---

## The Bug

### Symptom
First epoch of Phase H training showed MAE values of ~993.55m instead of expected ~5-8m range.

```
[2026-05-06 18:55:00]   Cycle ep  1/8  train=5.0091  val=4.5824  mae=993.55m  iou=0.332
[2026-05-06 18:56:29]   Cycle ep  1/8  train=5.0974  val=4.5841  mae=994.56m  iou=0.330
[2026-05-06 19:01:47]   Cycle ep  2/8  train=5.0450  val=4.5876  mae=998.96m  iou=0.326
```

### Root Cause
Training script had incorrect height scaling logic:

**train_phase_h.py, line 193 (WRONG)**:
```python
# Height is normalized to 0-1, multiply by 200m to get actual height error
mae_meters = val_mae * 200.0
```

But the height tiles contain **actual height in meters** (0-300m range), not normalized (0-1).

### Data Verification
Analyzed all 573×512×512 = 144M height samples:
- Min: 0.0m
- Max: 300.0m  
- Mean: 5.7m
- Median: 0.0m (background pixels)

Confirmed: heights are in **actual meters**, not normalized values.

---

## The Fix

### Changes Made

**File**: `strm2stl/scripts/train_phase_h.py`

**Change 1 (line 193)**:
```python
# BEFORE:
mae_meters = val_mae * 200.0

# AFTER:
mae_meters = val_mae  # Already in meters (0-300 range)
```

**Change 2 (line 343)**:
```python
# BEFORE:
cycle_mae = cycle_history[-1]["val_mae"] * 200.0  # Convert normalized (0-1) to meters

# AFTER:
cycle_mae = cycle_history[-1]["val_mae"]  # Already in meters (0-300 range)
```

**Change 3 (line 165)** — Comment fix:
```python
# BEFORE:
# Metrics: MAE in normalized space (0-1)

# AFTER:
# Metrics: MAE in meters (0-300 range)
```

### Restart
- **Old process (PID 61484)**: Killed
- **Restarted (PID 103352)**: 2026-05-06 19:08:00 UTC
- **Log cleared** for clean restart
- **PYTHONUNBUFFERED=1** enabled for real-time output

---

## Expected MAE Range (Corrected)

With the fix, MAE values should now be in the correct range:

| Phase | Expected MAE | Actual Data Range |
|---|---|---|
| **Phase G (warmstart)** | 7.55m | ✓ Measured from holdout |
| **Phase H Cycle 1** | 5-8m | 0-300m (whole dataset) |
| **Phase H Target** | <7.2m | 5-10% improvement |
| **Tier A** | <7.0m | Best-of-best |
| **Tier B** | <7.5m | Cross-city generalization |

---

## Why This Bug Happened

1. **Legacy comment** — Old protocol may have used normalized heights (0-1)
2. **Checkpoint metadata** — Has `height_norm_m: 200.0` from older training
3. **Data evolution** — Hires tiles changed to raw meters, but script wasn't updated
4. **Insufficient validation** — No sanity check comparing log MAE to actual data range

---

## Verification & Confidence

✅ **Data verified**: All 144M samples in (0, 300m) range  
✅ **Height normalization confirmed**: Raw meters, no preprocessing  
✅ **Fix applied**: Both log scaling and cycle metrics corrected  
✅ **Restart clean**: Log cleared, process restarted with fixed code  
✅ **Process healthy**: PID 103352 running, no errors in setup  

**Expected first epoch completion**: ~40-60 minutes from 19:08 UTC (19:48-20:08 UTC)  
**Next verification**: Check log for MAE values in 5-8m range

---

## Timeline of Bug Detection & Fix

| Time (UTC) | Event |
|---|---|
| 18:47 | Original Phase H launch (PID 61484) |
| 18:55–19:03 | First epochs logged with mae=993m+ |
| 19:08 | Bug detected during progress check |
| 19:08 | Height data analyzed → confirmed actual meters |
| 19:08 | Script corrected (2 lines changed, 1 comment fixed) |
| 19:08 | Process killed and restarted (PID 103352) |
| 19:08+ | Monitoring active, waiting for first epoch completion |

