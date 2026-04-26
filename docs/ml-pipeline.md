# ML Pipeline: Building Height & Roof Shape

Satellite-image CNN pipeline for predicting per-pixel building heights and roof shapes, used downstream by the 3D STL export.

---

## Overview

Two tasks share a common backbone and training infrastructure:

| Task | Model | Output | Ground truth |
|------|-------|--------|--------------|
| **Height regression** | RoofNetV3_S / V3 / V3_1 | Height map (metres/pixel) | OSM building heights rasterized |
| **Roof shape classification** | RoofNetV2 | 6-class logit | OSM `roof:shape` tags |

Both tasks use a **MobileNetV3-Small** ImageNet backbone (1.5 M params), which stays frozen during warm-up then fine-tunes at a reduced LR.

---

## Directory layout

```
strm2stl/
├── tools/ml/
│   ├── config.py            # city bboxes, label sets, training defaults
│   ├── models.py            # RoofNetV2, RoofNetV3, RoofNetV3_S, RoofNetV3_1
│   ├── train.py             # train_v3(), train_shape(), train_height()
│   ├── data.py              # datasets, transforms (ImageNet norm, augmentation)
│   ├── collect_osm_tiles.py # harvest (RGB, height) tiles from OSM + ESRI
│   ├── predict_demo.py      # quick visual check of a checkpoint
│   └── eval.py              # evaluation utilities
│
├── models/                  # saved checkpoints (.pt)
│   ├── roofnet_v3s.pt       # recommended: small/fast (best for < 500 tiles)
│   ├── roofnet_v3.pt        # v3 standard (1.4 M params)
│   ├── roofnet_v3_1.pt      # v3.1 experimental (2.8 M params, skip connections)
│   └── roofnet_shape_v1.pt  # roof shape classifier (needs retraining)
│
├── cache/
│   ├── height_tiles_osm/    # 280 tiles @ 256 px (OSM heights + ESRI RGB)
│   └── height_tiles_hr/     # 102 tiles @ 512 px (high-res, optional)
│
└── notebooks/
    ├── Height_Training_Inspector.ipynb  # visual evaluation of height models
    ├── Height_Prediction.ipynb          # run inference on a new region
    └── Train_Height_CNN.ipynb           # notebook-driven training
```

---

## Models

### RoofNetV3_S  *(recommended for < 500 tiles)*

- ~983 K total params, **56 K trainable** during warm-up (backbone frozen)
- Iterative refinement: 2 forward passes, each feeding previous mask + height back as extra input channels
- Narrow 32-channel FPN, Dropout2d(0.35) in prediction heads
- Backbone unfreezes after `freeze_backbone_epochs` at 10× lower LR

### RoofNetV3  *(standard)*

- 1.4 M params, all trainable from the start
- Same iterative structure, 128-channel FPN
- Better ceiling than V3_S but needs > 500 tiles to avoid overfitting

### RoofNetV3_1  *(experimental, high-data)*

- 2.8 M params
- Skip connections from MobileNetV3 stages 0/2/6/12 + Retna-style multi-scale fusion
- 4 scratchpad channels passed between iterations for learned inter-iteration state
- Full backprop through all iterations (no detach)
- Designed for 512 px tiles and > 1000 training samples

### RoofNetV2  *(shape classification)*

- MobileNetV3 + FPN + dual heads: height regression + 6-class shape classifier
- Used for roof:shape prediction; height head is lower quality than V3 family

---

## Step 1: Collect training tiles

Tiles are `(RGB, height)` pairs stored as `.npz` files.  
The collector fetches OSM building footprints + heights and pairs them with ESRI satellite imagery. No API keys required.

```bash
# Collect 80 tiles per city for all default training cities (Berlin, Vienna, Barcelona, Paris, Amsterdam, Prague, Rotterdam)
python -m tools.ml.collect_osm_tiles --tiles-per-city 80

# Custom: Berlin only, high-res 512 px tiles
python -m tools.ml.collect_osm_tiles \
    --cities Berlin \
    --tiles-per-city 120 \
    --tile-size 512 \
    --target-res-m 1.0 \
    --out-dir cache/height_tiles_hr
```

Each tile is ~600 KB (256 px) or ~2.4 MB (512 px).  
Current tile counts: **280 tiles @ 256 px**, 102 @ 512 px.

---

## Step 2: Train

### Python API (recommended)

```python
from tools.ml.train import train_v3, TrainConfig

result = train_v3(
    tile_dir="cache/height_tiles_osm",       # or height_tiles_hr for 512 px
    output_model="models/roofnet_v3s.pt",
    config=TrainConfig(
        task="height",
        epochs=30,
        batch_size=8,
        lr=3e-4,
        patience=8,
        tile_size=256,                        # 512 for high-res tiles
        device="cpu",                         # or "cuda" / "mps"
        num_workers=0,
        freeze_backbone_epochs=6,             # warm-up: backbone stays frozen
        grad_loss_weight=0.5,                 # weight of Sobel edge loss term
    ),
    n_iters=2,       # refinement iterations per forward pass
    arch="v3s",      # "v3s" | "v3" | "v3.1"
)
print(result)
```

### Hyperparameter guidance

| Setting | Small dataset (< 400 tiles) | Larger dataset (> 800 tiles) |
|---------|-----------------------------|------------------------------|
| `arch` | `"v3s"` | `"v3"` or `"v3.1"` |
| `lr` | `3e-4` | `1e-3` |
| `batch_size` | `8` | `16` |
| `freeze_backbone_epochs` | `6` | `3` |
| `epochs` | `30` | `60` |

**Do not use `lr > 1e-3`** — gradient explosion occurs above this threshold with the v3 composite loss.

### Notebook

Open [Train_Height_CNN.ipynb](../notebooks/Train_Height_CNN.ipynb) for a cell-by-cell walkthrough.

---

## Step 3: Evaluate

### Inspect predictions (notebook)

Open [Height_Training_Inspector.ipynb](../notebooks/Height_Training_Inspector.ipynb) and set:

```python
CHECKPOINT = "../models/roofnet_v3s.pt"
TILE_DIR   = "../cache/height_tiles_osm"
```

Cells show:
- Overall val MAE (all pixels vs building pixels only)
- Per-tile 5-panel view: **RGB | GT height | Predicted mask | Predicted height | Error**
- Arch is auto-detected from the checkpoint's state dict keys

### Quick demo script

```bash
python -m tools.ml.predict_demo --checkpoint models/roofnet_v3s.pt --city Berlin
```

---

## Step 4: Run inference in the app

```python
from app.session.terrain_session import TerrainSession

s = TerrainSession(port=9000)
s.start()
s.create_region("MyRegion", north=52.53, south=52.51, east=13.41, west=13.38)
s.fetch_satellite()
s.predict_heights(
    model="unet",
    checkpoint="models/roofnet_v3s.pt",
    device="cpu",
)
result = s.predicted_heights
print(f"Height range: {result.raster.min():.1f} – {result.raster.max():.1f} m")
```

See [Height_Prediction.ipynb](../notebooks/Height_Prediction.ipynb) for the full end-to-end notebook including visualisation and comparison with Phase-1 heights.

---

## Loss functions

The v3 family trains with a composite loss applied at each iteration (deep supervision):

```
L = weighted_L1(height)              # building pixels weighted 5× over background
  + 0.5 × Sobel(height)             # edge sharpness via gradient matching
  + BCE(mask, target > 0)           # building/background segmentation
  + Dice(mask, target > 0)          # overlap for sparse positives
  + 0.5 × ContinuousDice(height)    # energy localisation
```

Later iterations are weighted more heavily: `w = (iter + 1) / n_iters` — so pass 2 of 2 contributes twice as much as pass 1.

---

## Known issues and lessons learned

| Issue | Cause | Fix applied |
|-------|-------|-------------|
| All-zero height predictions | Naive L1 on 70 % background tiles | Weighted L1: building pixels ×5 |
| Blurry predictions | Single 32× bilinear FPN upsample | V3_1 adds skip connections; V3_S uses sharp loss terms |
| Overfitting on 280 tiles | 1.4 M+ trainable params | Use `arch="v3s"` (56 K trainable during warm-up) |
| Gradient explosion | `lr` too high (e.g. `lr=0.3`) | Keep `lr ≤ 1e-3`; default is `3e-4` |
| Backbone input mismatch | Missing ImageNet normalisation | Fixed in `data.py` — `(x − mean) / std` now applied before backbone |
| WSF3D / GHSL provider 404 | External height APIs unavailable for European cities | Use `collect_osm_tiles.py` (OSM + ESRI, no keys needed) |

---

## Configuration reference

[tools/ml/config.py](../tools/ml/config.py) is the single source of truth for cities, label sets, and defaults.

```python
from tools.ml.config import TRAIN_CITIES, EVAL_CITIES, SHAPE_LABELS
```

**Training cities:** Berlin, Vienna, Barcelona, Paris, Amsterdam, Prague, Rotterdam  
**Eval cities:** non-overlapping sub-regions of the same cities

**Roof shape classes** (index order is fixed — do not reorder):

| Index | Label |
|-------|-------|
| 0 | flat |
| 1 | gabled |
| 2 | hipped |
| 3 | pyramidal |
| 4 | skillion |
| 5 | dome |
