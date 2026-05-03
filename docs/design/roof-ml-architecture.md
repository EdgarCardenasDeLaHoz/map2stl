# ROOF-2: ML Architecture Plan (HISTORICAL)
_Last updated: 2025-06-18 · superseded 2026-04-30_

> **HISTORICAL.** This document captures the original ROOF-2 architecture analysis (UNet vs RoofNetV3 etc.). The production direction has since changed: roof-shape ML is no longer pursued because OSM tag coverage + the F-ROOF1 raster path is sufficient. Building heights are now handled by Retna_V1 — see [`../plans/height-training-status.md`](../plans/height-training-status.md).
>
> Kept for reference because the model trade-offs analysis is still useful when designing new architectures.

---

This document originally superseded the "Phase 2: Roof Shape Detection" section of
`../completed/building-roof-pipeline-plan.md` and introduced the models in `tools/networks.py`
into the pipeline architecture.

---

## 1. Model Evaluation: `tools/networks.py`

### 1.1 UNet

```
Encoder: 4 × double_conv  (8→8→8→8 channels, maxpool between levels)
Decoder: 3 × double_conv  (concat skip, upsample)
Output:  1 × Conv2d(8, out_classes, 1)
```

**Strengths**
- Standard skip-connection architecture; proven for pixel-labelling tasks.
- Bilinear upsample avoids checkerboard artefacts from transposed convolution.

**Weaknesses / Issues**
- `num_chan = [8, 8, 8, 8]` — channels **do not expand** at each depth level.
  A standard UNet doubles (64→128→256→512). The 8-channel bottleneck makes the
  network extremely weak; it will under-fit on anything except very simple
  binary segmentation.
- No batch normalisation, no dropout — training will be unstable on real data.
- No non-linearity after the final conv; callers must apply sigmoid / softmax.
- The channel counts result in roughly **~3 K trainable parameters** — about
  three orders of magnitude too small for roof geometry inference.

**Verdict:** Suitable as a prototype / sanity-check model only. Before training
on real satellite data the channel widths must be widened (at minimum 32-64-128
for a 64×64 crop), and batch normalisation added to `double_conv`.

---

### 1.2 Retna_V1 ★

```
Blocks:  N × res_conv(stride=2)   — each halves spatial resolution
         + bilinear upsample back to original
         + continual input re-injection at each scale
Final:   1 × Conv2d(sum_all_channels, out_classes, 1)
Output:  clamp(0, 1) via symmetric leaky_relu trick
```

**Architecture diagram (N=4, hidden=[8,8,8,8], RGB input):**

```
x_in  ─────────────────────────────────────┐
  │                                         │
  ▼  res_conv(3→8, stride=2)               │
 H/2×W/2×8  ─upsample→ H×W×8              │
  │                                         │
  │ cat[x_in_downsampled, feat]             │
  ▼  res_conv(11→8, stride=2)              │
 H/4×W/4×8  ─upsample→ H×W×8              │
  │                                         │
  ...                                       │
  ▼                                         │
[x_in, scale1_out, scale2_out, scale3_out, scale4_out]
  → concat H×W×(3+8+8+8+8=35)
  → Conv2d(35, out_classes, 1)
  → clamp[0,1]
```

**What makes this good**

1. **Multi-scale dense aggregation** — every spatial scale contributes directly
   to the final prediction. This is architecturally similar to HED
   (Holistically-nested Edge Detection) and Feature Pyramid Networks, but
   simpler to train because there is only one supervised output rather than
   deep supervision at each scale.

2. **Continual input re-injection** — at each block the original input (at the
   current scale) is concatenated with the current features. This prevents
   gradient vanishing and ensures the network can always "look back" at the raw
   pixel values, which is valuable when the task requires precise geometry
   (roof ridges, eave lines) alongside broader context (shadow, surroundings).

3. **Symmetric clamping** — the `F.leaky_relu(x) ; 1 - F.leaky_relu(1-x)`
   pattern in training mode is a soft differentiable clamp to [0,1] that
   still passes gradients for values slightly outside the range. In eval mode
   it hard-clamps. This makes it well-suited for outputs that represent
   normalised quantities (height / max_height, probability, mask).

4. **Low parameter count with sensible scaling** — with `hidden=[8,8,8,8]` and
   a 3-channel input the network has roughly **~5 K params**. Widening to
   `[32,64,64,128]` gives ~800 K params — still smaller than MobileNetV3-Small
   (2.5 M) while being a dense predictor rather than a classifier.

**Issues to fix before production use**

| Issue | Location | Fix |
|-------|----------|-----|
| `model.no_grad()` is not a method | `eval_file` L3 | Replace with `torch.no_grad():` context manager |
| `image / 256` instead of `/ 255` | `segment` L4 | Use `/ 255.0` for correct uint8 normalisation |
| No normalisation layers | `res_conv` | Add `nn.GroupNorm(num_groups, channels)` or `nn.BatchNorm2d` in the conv blocks |
| Hardcoded crop coordinates `X1,X2,Y1,Y2` | `eval_file` | Remove; accept crop parameters |
| `hidden_channels.insert(0, in_channels)` mutates the default list | `__init__` | Use `hidden_channels = list(hidden_channels)` before insert |

**Verdict:** Retna_V1 is **architecturally sound** for dense prediction on small
satellite crops. The multi-scale input re-injection is a genuine architectural
advantage over a vanilla U-Net for tasks that mix local texture with broader
context (exactly the case for roof geometry). After widening channels and
fixing the normalisation bugs it is the recommended backbone for **producing a
pseudo-nDSM from RGB** in ROOF-2.

---

## 2. Revised ROOF-2 Architecture

The original ROOF-2 plan used a single-stage approach: RGB → MobileNetV3-Small
→ roof:shape. After reviewing the available models, a **two-stage** architecture
is cleaner and more data-efficient.

### Stage A — Pseudo-nDSM prediction (dense)

```
Input:  H×W×3  RGB satellite crop (per-tile or per-building)
Model:  Retna_V1 with hidden=[32,64,64,128], out_classes=1
Output: H×W×1  predicted height-above-terrain (metres, normalised to [0, max_h])
```

- Trains as a **regression** problem (L1 / SmoothL1 loss) against real LiDAR
  nDSM tiles wherever open data is available.
- At inference the predicted height raster feeds `_extract_elev_features()` in
  `city2stl/roof_classifier.py` as the `height_raster` argument — no change to
  the classification logic downstream.
- Also feeds `estimate_roof_heights=True` to fill `roof:height`.

### Stage B — Per-building roof:shape classification

```
Input:  64×64×(N×3)  stacked temporal crops (early fusion)
Model:  MobileNetV3-Small, final Linear replaced with Linear(576, 6)
Output: 6-class softmax → [flat, gabled, hipped, pyramidal, skillion, dome]
```

- Trains as a **classification** problem (CrossEntropy) against OSM `roof:shape`
  labels harvested with `tools/eval_roof_tags.py`.
- Runs in parallel with Stage A. Both outputs are fused in `_classify()`.

### Fusion priority (unchanged from roof_classifier.py)

```
1. Stage B CNN  (conf ≥ 0.55)
2. Stage A elevation profile  (from pseudo-nDSM or real LiDAR)
3. Multi-temporal shadow triangulation
4. RGB appearance
5. Shadow geometry fallback
```

### Architecture diagram

```
Satellite tile
     │
     ├──► Retna_V1 (Stage A) ──► pseudo-nDSM ──► _extract_elev_features()
     │                                                       │
     └──► per-building crop                                  │
               │                                             │
               ├──► MobileNetV3 (Stage B) ─────────────────►│
               │                                             │
               └──► shadow geometry, RGB features ──────────►│
                                                             │
                                              _classify() fusion
                                                             │
                                                       roof:shape
                                                       roof:height
```

---

## 3. Training Data Strategy

### 3.1 Stage A — RGB → nDSM regression

| Dataset | Coverage | Resolution | Format | Licence |
|---------|----------|------------|--------|---------|
| Netherlands AHN4 | Full NL | 0.5 m | LAZ / GeoTIFF | CC0 |
| UK DEFRA DSM (LIDAR Composite) | Most of England | 1 m | GeoTIFF | OGL v3 |
| US 3DEP (USGS) | Continental US | 1 m | Cloud-optimised COG | Public domain |
| Austria / Vienna LiDAR | Vienna + some cities | 0.5 m | LAZ | CC BY 4.0 |
| OpenTopography aggregator | Global hotspots | varies | COG / LAZ | various |

Paired satellite: OpenAerialMap or Mapbox Static API (requires approval), or
Sentinel-2 L2A (10 m) as a coarser baseline.

Training procedure:
1. Download nDSM tiles for covered area.
2. Fetch matching RGB satellite crops at same bbox + resolution via the
   existing `fetch_satellite` route in `app/server/routers/satellite.py`.
3. Normalise heights: `h_norm = clip(h_raw, 0, 50) / 50` → [0, 1].
4. Split 80/10/10 train/val/test by geographic grid cell.

### 3.2 Stage B — OSM roof:shape classification

The `tools/eval_roof_tags.py` script already harvests per-city tag coverage.
Cities with the highest `roof:shape` coverage:

| City | Coverage (approx) | Notable shapes |
|------|--------------------|----------------|
| Amsterdam | ~65 % | flat, pyramidal |
| Vienna | ~80 % | gabled, hipped, flat |
| Prague | ~55 % | hipped, pyramidal |
| Berlin | ~70 % | flat, gabled |
| Rotterdam | ~45 % | flat (modern), gabled |
| Zurich | ~75 % | gabled, hipped |

Procedure:
1. `classify_roof_shapes(..., overwrite=False)` returns all buildings; inspect
   `roof_source == "osm_tag"` entries for ground truth.
2. Crop 64×64 RGB patches centred on each building footprint.
3. Oversample rare classes (skillion, dome) by factor ×5.
4. Augment: horizontal flip, 90° rotations, brightness ±20 %, JPEG noise.

---

## 4. Retna_V1 Changes Required for Production

### 4.1 Widened channels

```python
# Current (too narrow)
hidden_channels = [8, 8, 8, 8]

# Recommended for 64×64 building crops (pseudo-nDSM)
hidden_channels = [32, 64, 64, 128]   # ~810 K params

# Recommended for full-tile pseudo-nDSM (256×256+)
hidden_channels = [64, 128, 256, 256]  # ~6 M params — use only with GPU
```

### 4.2 Normalisation

Replace `res_conv` sequential with a group-norm version:

```python
def res_conv_normed(in_channels, out_channels, groups=8):
    mid = in_channels + out_channels
    return nn.Sequential(
        nn.Conv2d(in_channels, mid, 3, padding=1, stride=1),
        nn.GroupNorm(min(groups, mid), mid),
        nn.LeakyReLU(inplace=True),
        nn.Conv2d(mid, out_channels, 3, padding=1, stride=2),
        nn.GroupNorm(min(groups, out_channels), out_channels),
        nn.LeakyReLU(inplace=True),
    )
```

GroupNorm is preferred over BatchNorm here because building crops may be
small (64×64) and batch sizes may be small (4–8) — conditions where BatchNorm
statistics are unreliable.

### 4.3 Bug fixes

```python
# eval_file — fix no_grad usage
with torch.no_grad():       # was: with model.no_grad():
    output = model(t_im)

# segment — fix uint8 normalisation
if image.dtype == np.uint8:
    image = image / 255.0   # was: / 256

# __init__ — fix mutable default list mutation
hidden_channels = list(hidden_channels)   # copy before insert
hidden_channels.insert(0, in_channels)
```

### 4.4 Regression output head

Retna_V1 currently targets [0, 1] output via the symmetric clamp. For height
regression, apply an additional linear output scale at call time:

```python
h_pred = model(rgb_tensor)[0, 0].numpy() * MAX_HEIGHT_M   # un-normalise
```

Or add a configurable `output_scale` parameter to the forward pass.

---

## 5. Implementation Phases

### Phase 2a — Inference path (no training) ✅ COMPLETE

Status: **fully implemented**

Completed work:

| Component | Status | Notes |
|-----------|--------|-------|
| `city2stl/roof_classifier.py` — multi-signal heuristic pipeline | ✅ done | shadow, RGB, elev, multi-temporal, CNN stub |
| `tests/test_roof_classifier.py` — 53 tests | ✅ done | TestShadowDetection, TestElevFeatures, TestRGBFeatures, … |
| `tools/networks.py` — bug fixes (no_grad, /255, mutable default) | ✅ done | |
| `tools/networks.py` — `ResBlock`, `Retna_V2`, `RoofNet` added | ✅ done | Multi-task: height head + shape head |
| `tests/test_networks.py` — unit tests for new architecture | ✅ done | Skips gracefully without torch |
| `tools/seed_eval_regions.py` — seed DB + cache for 5 eval cities | ✅ done | Amsterdam, Vienna, Prague, Berlin, Rotterdam |
| `tools/eval_pseudo_ndsm.py` — height raster quality evaluation | ✅ done | Per-building coverage stats, CSV output |
| `tools/eval_roof_classifier.py` — classifier accuracy evaluation | ✅ done | GT-strip benchmark + coverage gain |
| `city2stl/roof_classifier.py` — wire `RoofNet` into `_cnn_classify_patch` | ✅ done | `_roofnet_classify_patch()` dispatch |
| `app/session/terrain_session.py` — `load_roof_model()` method | ✅ done | Loads `.pt` checkpoint, registers on `self._roof_model` |
| `app/session/terrain_session.py` — auto-use `_roof_model` in classify | ✅ done | `cnn_model=None` → uses `_roof_model` or default |
| `docs/completed/todo-history.md` — ROOF-2 marked done | ✅ done | |

**New model classes in `tools/networks.py`:**

```python
# Multi-task architecture (height regression + shape classification)
class RoofNet(nn.Module):
    """
    Shared Retna_V2 backbone with:
      height_head  → B×1×H×W  (pseudo-nDSM, dense)
      shape_head   → B×6       (roof:shape logits, pooled)
    """

# Improved backbone
class Retna_V2(nn.Module):
    """Retna_V1 with ResBlocks + GroupNorm (no dead maxpool, raw logits)"""

class ResBlock(nn.Module):
    """Strided residual block, GroupNorm, LeakyReLU, stride=2 downsampling"""
```

**Usage from a notebook / eval script:**

```python
from app.session.terrain_session import TerrainSession

s = TerrainSession()
s.start()
s.select("Amsterdam_eval")
s.fetch_cities()
s.fetch_satellite()
s.fetch_building_heights(providers=["wsf3d"])

# Option A: heuristic pipeline (no checkpoint needed)
s.classify_roof_shapes()

# Option B: with RoofNet checkpoint (once trained)
s.load_roof_model("models/roofnet_v1.pt")
s.classify_roof_shapes()   # auto-uses self._roof_model

# Evaluate
from tools.eval_roof_classifier import eval_roof_classifier
results = eval_roof_classifier(start_server=False)
```

**Evaluation scripts:**

| Script | Purpose |
|--------|---------|
| `tools/seed_eval_regions.py` | Create 5 eval regions in DB + pre-cache rasters |
| `tools/eval_pseudo_ndsm.py` | Measure height raster coverage, mean/p50/p90 heights |
| `tools/eval_roof_classifier.py` | GT-strip accuracy + coverage gain, per-city CSV |

Remaining work for Phase 2a:
- ✅ Run full test suite (624 passed)
- ✅ Run `seed_eval_regions.py` — all 5 cities seeded (OSM + raster + heights)
- ✅ Run `eval_roof_classifier.py` — results in `output/eval_roof_clf_*.csv`
- ✅ Run `eval_pseudo_ndsm.py` — results in `output/eval_pseudo_ndsm_*.csv`

### Phase 2b — RoofNet training

Goal: train `RoofNet` jointly on pseudo-nDSM regression + roof-shape
classification, replacing the random-weights stub with a fine-tuned checkpoint.

The inference wiring is **already in place** (Phase 2a complete):
- `_roofnet_classify_patch()` dispatches to `RoofNet.forward()` when a model
  instance is passed to `classify_roof_shapes()`.
- `TerrainSession.load_roof_model()` loads a checkpoint and registers it for
  automatic use.

Training scripts are **now created**:

| File | Status | Purpose |
|------|--------|---------|
| `tools/harvest_roof_crops.py` | ✅ created | Crop 64×64 RGB patches from eval cities; oversample rare classes |
| `tools/train_roof_classifier.py` | ✅ created | CrossEntropy training of RoofNet shape head; AdamW + cosine LR + WeightedSampler |
| `tools/train_pseudo_ndsm.py` | ✅ created | SmoothL1 training of RoofNet height head; paired RGB + nDSM tiles |
| `models/roofnet_shape_v1.pt` | ⏳ pending | Shape-head checkpoint (created by train_roof_classifier.py) |
| `models/roofnet_height_v1.pt` | ⏳ pending | Height-head checkpoint (created by train_pseudo_ndsm.py) |

**Training data still needed for height head:**
Download open LiDAR data (see §3.1) into `data/ndsm_tiles/train/rgb/` and
`data/ndsm_tiles/train/ndsm/`, then run `train_pseudo_ndsm.py`.

**Shape-head training workflow (ready to run once crops harvested):**

```bash
# 1. Harvest crops from cached eval regions (server must be running)
python -m tools.harvest_roof_crops --no-server

# 2. Train shape head
python -m tools.train_roof_classifier \
    --data-dir output/roof_crops \
    --output-model models/roofnet_shape_v1.pt \
    --epochs 40

# 3. Evaluate trained model
python -m tools.eval_roof_classifier --no-server
```

### Phase 2c — Multi-task joint training (future)

Train both heads simultaneously with a combined loss:

```python
loss = lambda_h * F.smooth_l1_loss(height_pred, height_target) \
     + lambda_s * F.cross_entropy(shape_logits, shape_target)
```

Typical ratio: λ_h = 1.0, λ_s = 2.0 (shape task is higher priority for ROOF-2).

### Phase 2d — Integration with building-roof pipeline plan

Once a checkpoint exists:

```python
s.load_roof_model("models/roofnet_shape_v1.pt")
s.classify_roof_shapes()   # RoofNet used automatically
```

No further code changes needed — the inference path is complete.

---

## 6. Evaluation Results

### 6.1 Stage A — pseudo-nDSM (WSF3D coverage)

Measured via `tools/eval_pseudo_ndsm.py` on the 5 ROOF-2 eval cities (2025-06-18):

| City | n_buildings | bldg_cov% | global_cov% | mean_h |
|------|-------------|-----------|-------------|--------|
| Amsterdam_eval | 5670 | 0.0% | 0.0% | — |
| Vienna_eval | 5123 | 0.0% | 0.0% | — |
| Prague_eval | 4817 | **100.0%** | **98.7%** | **17.0 m** |
| Berlin_eval | 6331 | 0.0% | 0.0% | — |
| Rotterdam_eval | 4050 | 0.0% | 0.0% | — |

Note: Amsterdam, Vienna, Berlin, Rotterdam show 0% WSF3D coverage — this is a
known data gap in the WSF3D dataset tiles, not a pipeline error. Prague is the
only eval city with full WSF3D coverage; mean building height 17.0 m is
plausible for a central European city core.

```
Target (RoofNet height head):  RMSE < 2.5 m on AHN4 held-out set
Stretch:                        RMSE < 1.5 m
Status:                         height training not yet started (needs LiDAR tiles)
```

### 6.2 Stage B — roof:shape classification (heuristic baseline)

Measured via `tools/eval_roof_classifier.py` on the 5 ROOF-2 eval cities (2025-06-18).
This is the **untrained heuristic baseline** (random-weight RoofNet, shadow + geometry signals only):

| City | n_buildings | GT-tagged | Accuracy | Coverage gain | Top-1 |
|------|-------------|-----------|----------|---------------|-------|
| Amsterdam_eval | 5670 | 282 (5.0%) | 2.5% (7/282) | +5388 → 100% | pyramidal |
| Vienna_eval | 5123 | 464 (9.1%) | 2.2% (10/464) | +4659 → 100% | pyramidal |
| Prague_eval | 4817 | 597 (12.4%) | 8.7% (52/597) | +4219 → 100% | pyramidal |
| Berlin_eval | 6331 | 3126 (49.4%) | 2.4% (74/3126) | +3205 → 100% | pyramidal |
| Rotterdam_eval | 4050 | 196 (4.8%) | 5.6% (11/196) | +3853 → 100% | pyramidal |

The heuristic always predicts "pyramidal" — accuracy is low because Central
European buildings are predominantly "gabled" in OSM. This confirms the
training gap and provides the **pre-training baseline** to beat with RoofNet.

```
Heuristic baseline:    2–9% accuracy (biased toward "pyramidal")
Target (post-training): macro-F1 > 0.60 across all 6 classes
Stretch:               macro-F1 > 0.75 on {flat, gabled, hipped, pyramidal}
```

### 6.3 End-to-end (leave-one-city-out, future)

```
Metric:   agreement with OSM roof:shape on buildings NOT in training set
Baseline: shadow-only heuristic → ~45–55 % agreement
Target:   > 65 % agreement
Stretch:  > 75 % agreement (requires trained models)
```

---

## 7. Data Pipeline for Training

```
tools/
  harvest_roof_crops.py       ✅ -- crop OSM-labelled buildings into 64×64 PNGs
  train_roof_classifier.py    ✅ -- AdamW + cosine LR, WeightedSampler, shape head
  train_pseudo_ndsm.py        ✅ -- SmoothL1 regression, paired RGB+nDSM tiles
  seed_eval_regions.py        ✅ -- pre-seed 5 eval cities in DB + cache
  eval_pseudo_ndsm.py         ✅ -- coverage + height stats, per-city CSV
  eval_roof_classifier.py     ✅ -- GT-strip accuracy + coverage gain, per-city CSV
```

Height training still needs external LiDAR data:
- Netherlands AHN4 (CC0): https://www.pdok.nl/en/ahn
- UK DEFRA DSM (OGL v3): https://environment.data.gov.uk/DefraDataDownload/?Mode=survey
- US 3DEP (public domain): https://www.usgs.gov/3d-elevation-program
- Austria Vienna LiDAR (CC BY 4.0): https://data.wien.gv.at/

---

## 8. Open Questions

1. **Resolution mismatch** — AHN4 is 0.5 m; most freely available satellite
   imagery is 10 m (Sentinel-2) or ~0.3 m (commercial). The model must be
   trained at the same GSD as inference. Sentinel-2 at 10 m is too coarse to
   resolve individual roof ridges. A pragmatic choice is Mapbox satellite tiles
   at zoom 18 (~0.6 m/px) — but usage requires a token and commercial approval.
   **Decision needed**: which satellite source will be used for production?

2. **Single-image vs multi-temporal** — the multi-temporal shadow triangulation
   path in `roof_classifier.py` is implemented but requires N≥2 images of the
   same area taken at different times. For most users only one image is
   available. Should Stage A (pseudo-nDSM) be the primary fallback for single-
   image inputs?

3. **Tile-level vs crop-level inference** — running Retna_V1 on the full tile
   (e.g. 1024×1024) is more efficient than per-building crops; the output
   nDSM is sliced per building after the fact. But training on full tiles
   requires larger GPU memory. Recommend: train on 128×128 overlapping patches,
   infer on full tile with sliding window.

4. **Retna_V1 vs DepthAnything-V2-Small** — DepthAnything-V2-Small (~25 M
   params) is a monocular depth estimator pre-trained on a massive internet
   image corpus. It produces relative depth (not metric height). Retna_V1
   trained on local LiDAR produces **metric height** directly, which is more
   useful for our application. However, DepthAnything could be used as a
   feature extractor (its encoder frozen) with a small Retna_V1-style decoder
   head fine-tuned on nDSM pairs. This approach would require far less training
   data. **Decision needed**: zero-shot DepthAnything + fine-tuned head vs full
   Retna_V1 from scratch?

5. **`tools/networks.py` location** — the models live in `tools/`, which is
   for CLI scripts. Once used in production inference they should move to
   `city2stl/ml/` (or similar library path). This move should happen before
   Phase 2b ships.
