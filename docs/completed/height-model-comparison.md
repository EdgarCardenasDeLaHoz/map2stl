# Plan: Height Model Comparison — RoofNetV3 (joint) vs Cascade

_Created: 2026-04-28._
_Status: **archived**. Session 1 infrastructure (scoreboard, metrics extension, provider integration) shipped. Session 2 cascade architecture was never built — the RoofNetV3 line was abandoned and replaced by Retna_V1. See [plans/height-training-status.md](../plans/height-training-status.md) for the current model lineage._

## Why this exists

The repo has multiple model architectures for building-height regression
(`HeightUNet`, `RoofNetV2`, `RoofNetV3`, `RoofNetV3_1`) and several
checkpoint files in `models/`, but **no single source of truth for which is
best**. Per-epoch validation metrics existed (`*_history.json` files) but
were not aggregated, and no formal comparison had been run.

This plan records two improvements completed in session 1, and the
cascade-vs-joint comparison planned for session 2.

## Session 1 — completed

### Metrics extension

Added per-epoch logging of:

- `val_mask_iou` — IoU of the predicted building mask vs ground truth.
- `val_mask_acc` — pixel accuracy of the mask head.
- `val_rmse` — RMSE of mask-gated height predictions.

All three are computed inside `tools/ml/train.py:_eval_epoch_v3()` and
written into the per-checkpoint history JSON. Existing fields
(`val_loss`, `val_mae`, `lr`) are preserved, so old histories remain
parseable.

### Scoreboard

New module `tools/ml/scoreboard.py` writes a single
`models/scoreboard.json` registry:

- One entry per training run with `arch`, `task`, `model_path`,
  `best_metrics`, dataset size, epochs, config hash, timestamp, notes.
- CLI: `python -m tools.ml.scoreboard show` prints a sorted leaderboard.
- `train_v3()` now records to the scoreboard automatically on completion.

### Provider integration

New `app/server/core/height/providers/roofnet.py`:

- Activates only when a checkpoint is present at `models/roofnet_v3.pt`,
  `models/roofnet_v3_1.pt`, `models/roofnet_unet_iou_v2.pt`, or
  `models/roofnet_unet.pt` (or via `ROOFNET_CKPT` env var).
- Fetches ESRI satellite RGB for the bbox via `fetch_satellite_tiles()`.
- Calls `city2stl.height.predict.predict()` with the right model kind
  (`unet` or `height_unet` based on checkpoint name).
- Returns a standard `HeightResult` so it joins the parallel fetch pool
  in `app/server/routers/height.py`.

Registered in `_ALL_PROVIDERS` between Copernicus (0.70) and OpenBuildings
(0.60) at confidence 0.65. Will appear in `/api/height/sources` only when
a checkpoint exists.

## Session 2 — planned

### Cascade architecture

A 3-stage cascade as the user originally proposed:

1. **Stage 1 — Footprint detector**: standalone building/non-building
   segmentation. Either reuse `HeightUNet.mask_head` extracted to its own
   module, or use a pretrained segmentation backbone. Train with
   BCE+Dice; target val IoU ≥ 0.85 before downstream stages train.
2. **Stage 2 — Per-pixel height (given footprint)**: input is
   `[RGB, predicted_mask]` (4 channels). Output is dense height. Loss
   supervised only where `mask > 0.5`. L1+Sobel, no IoU loss (already
   covered by stage 1).
3. **Stage 3 — Roof-height residual**: input is
   `[RGB, mask, stage2_height]` (5 channels). Output is per-pixel
   residual to add to the stage-2 prediction. Supervised on
   `(LiDAR_height − stage2_height)`. L1.

Each stage trains independently; the next stage uses the previous
stage's *frozen* outputs.

### Comparison protocol

Both architectures train on the same dataset split (same seed, same
tile dir, same train/val partition). Each writes one row to
`models/scoreboard.json`. Decision metric is **val_mae on building
pixels**, with `val_mask_iou` as a tiebreaker.

If the cascade wins: extend the RoofNet provider to load all three
checkpoints and run them in sequence. If joint wins: keep the current
single-call path.

### Out of scope for session 2

- ML-based roof shape classifier training (covered separately by
  `roof_classifier.py`'s CNN path).
- Ground-truth dataset expansion beyond OSM tiles
  (`cache/height_tiles_osm/`).
- GPU acceleration — both runs target CPU for reproducibility on the
  user's current hardware.

## Files changed in session 1

| File | Change |
|---|---|
| `tools/ml/train.py` | `_eval_epoch_v3` returns 5-tuple incl. mask IoU + mask acc + RMSE; `train_v3` logs them per epoch and records run on scoreboard |
| `tools/ml/scoreboard.py` | NEW. Read/write/show registry of training runs |
| `app/server/core/height/providers/roofnet.py` | NEW. RoofNet provider wrapping `city2stl.height.predict.predict` |
| `app/server/routers/height.py` | Imported and registered RoofNetProvider in `_ALL_PROVIDERS` and `_PROVIDER_META` |

## Verification (session 1)

1. Smoke-train RoofNetV3 on 100 OSM tiles for 5 epochs at `tile_size=64,
   batch_size=8, n_iters=1`. Confirm history contains `val_mask_iou` and
   `val_rmse` keys.
2. `python -m tools.ml.scoreboard show` prints the run.
3. Restart server, hit `POST /api/height/sources`. Verify response
   includes `"name": "roofnet"` (only if a checkpoint exists).
4. Hit `POST /api/height/diagnostics`. RoofNet appears in the providers
   list with stats.
