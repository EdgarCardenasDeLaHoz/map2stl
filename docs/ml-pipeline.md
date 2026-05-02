# ML Pipeline: Building Heights

Satellite-image CNN pipeline for predicting per-pixel building heights, used downstream by the 3D STL export.

For the latest training results, model lineage, and open issues, see [`docs/plans/height-training-status.md`](plans/height-training-status.md).

---

## Active model: Retna_V1

A **first-principles** tiny network that replaced the much larger RoofNetV3 after the latter was diagnosed with marginal-mean collapse (predicting a constant ~7m blob).

| Metric | Value |
|---|---|
| Parameter count | 9.7k base; ~70k after iterative grow |
| Architecture | 4 stride-2 conv blocks with raw RGB re-injected at every block; output bounded ∈ [0, 1] then ×HEIGHT_NORM_M |
| Loss | `dice_l2` (Dice + λ·MSE) or `dice_l3` (Dice + λ·E[|res|³]) |
| HEIGHT_NORM_M | 200 m (covers skyscrapers) |
| Training time | ~2 s/epoch, CPU |

Definition lives in [`tools/example/networks.py`](../tools/example/networks.py).

---

## Directory layout

```
strm2stl/
├── tools/
│   ├── example/
│   │   └── networks.py           # Retna_V1 definition (active model)
│   │
│   └── ml/
│       ├── pipeline.py           # one-CLI driver: collect → train → inspect
│       ├── config.py             # city bboxes, label sets, training defaults
│       ├── train_retna.py        # plain trainer (dice / dice_l2 / dice_l3)
│       ├── grow_prune.py         # iterative NAS: grow + prune from scoring
│       ├── inspect_retna.py      # visual inspector (RGB / GT / Pred / Error)
│       ├── collect_osm_tiles.py  # harvest (RGB, height) tiles from OSM or providers
│       ├── scoreboard.py         # JSON registry of every training run
│       ├── analyze.py            # diagnostic: pearson, activation stats, layer norms
│       ├── baselines.py          # zero-param sanity baselines
│       │
│       └── (deprecated, RoofNetV3-era — kept for legacy notebooks only)
│           ├── train.py models.py data.py eval.py
│           ├── gradient_analysis.py simulate_data.py predict_demo.py
│
├── models/
│   ├── retna_pruned.pt           # CURRENT CHAMPION: 75k params, val=0.269, MAE=3.82m
│   ├── retna_deepgrow2.pt        # 9-block, 149k params (pre-prune)
│   ├── retna_deepgrow.pt         # 7-block intermediate
│   ├── retna_grow4.pt            # 10-cycle widening reference
│   ├── retna_grow3_long.pt       # original 4-block baseline
│   └── scoreboard.json           # registry of every recorded run
│
├── cache/
│   ├── height_tiles_combined/    # 100 EU tiles @ 128px (Cartagena moved to _bad/)
│   ├── height_tiles_eu11/        # 660 tiles, 11 EU cities @ 128px
│   └── height_tiles_us/          # US cities w/ LiDAR_3DEP labels
│
├── scripts/
│   ├── train.py                  # main entry: train / grow / deep / collect / inspect
│   ├── tile_review.py            # manual review PDF + drop-by-index
│   └── README.md                 # usage
│
└── notebooks/                    # legacy RoofNet-era; not used by Retna pipeline
```

---

## End-to-end run (single command)

```bash
python -m tools.ml.pipeline run \
    --cities Amsterdam Barcelona \
    --tiles-per-city 60 --tile-size 512 \
    --tile-dir cache/tiles_new \
    --output models/retna_new.pt \
    --epochs 60 --loss dice_l3 --hidden-channels 11 11 17 34
```

This chains:
1. **Collect** — `tools/ml/collect_osm_tiles.py` fetches per-city OSM building polygons + ESRI satellite RGB, rasterizes heights, writes `<City>_<r>_<c>.npz` files.
2. **Train** — `tools/ml/train_retna.py` runs Adam + ReduceLROnPlateau on the tile dir, recording every run on the scoreboard.
3. **Inspect** — `tools/ml/inspect_retna.py` saves RGB / GT / Pred / Error sample renders.

Use `--grow` to switch step 2 to the iterative grow/prune NAS (`tools/ml/grow_prune.py`).

Use `--skip-collect` to reuse an existing tile dir.

---

## Data sources

### Tile collection

The collector takes city bbox grids and writes `(rgb, height)` pairs per cell:
- `rgb`: `(3, dim, dim)` float32 — ESRI World Imagery, dynamic zoom (currently 16–17 for 200m bboxes at 512px).
- `height`: `(1, dim, dim)` float32 — heights in metres, NaN where no source.

Two label paths:

| `--label-source` | What it does | Best for |
|---|---|---|
| `osm` (default) | Rasterize OSM `building:height` / `building:levels` | Europe (good tag coverage) |
| `providers` | Merge external rasters (nDSM, GHSL, OpenBuildings, WSF3D, Copernicus, 3DEP-LiDAR) via `merge_height_rasters` | Sparse-OSM regions (Cartagena, US suburbs) |

OSM tag coverage drops sharply outside Europe — `--label-source providers` is the workaround. See [`docs/plans/height-data-sources.md`](plans/height-data-sources.md) for the merge strategy.

### Cache

The tile collector skips re-fetching tiles already on disk. It also skips:
- low building coverage (< 5% of pixels) — degenerate
- failed satellite fetches (intermittent ESRI 5xx)

---

## Loss functions

```
dice    = 1 - 2·|p ∩ t| / (|p| + |t|)
dice_l2 = dice + λ · E[(p − t)²]
dice_l3 = dice + λ · E[|p − t|³]
```

Targets are normalized by `HEIGHT_NORM_M = 200` to bring them into [0,1] for Dice. The L2/L3 term penalizes magnitude error — needed because pure Dice rewards overlap shape and is satisfied by predicting marginal-mean heights with the right footprint.

`dice_l3` weights tall-building errors more aggressively. Trade-off: less stable training at high λ.

---

## Grow/prune mode

Iterative neural architecture search:

1. Train current arch for N inner-epochs (with ReduceLROnPlateau).
2. Score each block by `mean(|activation|) × mean(|gradient|)` over a few val batches.
3. **If overfit detected** (`stale_epochs ≥ N AND train_dropped_since_best`): prune 25% per block, min 4 channels.
4. **Else**: grow every block by +1 channel, plus the highest-scoring block by `--grow-channels`.
5. Reload weights via `shape_aware_load` (copy where shapes match, leave new channels random).
6. Repeat for `--cycles` cycles.

Each cycle records a scoreboard row. The CLI prints per-cycle Δloss/ΔMAE/ΔIoU so you can see whether added capacity helped.

---

## Provider integration

Once trained, a Retna_V1 checkpoint can be exposed to the height pipeline by writing a thin provider that:
1. Fetches an ESRI RGB tile for the bbox.
2. Runs the model on `RGB / 255 → [0,1]`.
3. Returns `pred × HEIGHT_NORM_M` as a `HeightResult` raster.

The legacy `app/server/core/height/providers/roofnet.py` is currently auto-disabled (no compatible RoofNetV3 checkpoint exists). A Retna provider has not yet been wired in.

---

## Reproducibility

Every training run gets one row on `models/scoreboard.json`:
- `arch`, `task`, `model_path`, `best_metrics` (val_loss / val_mae / val_iou / val_rmse)
- `n_train`, `n_val`, `epochs`, `config_hash`, `notes`, `ts`

```bash
python -m tools.ml.scoreboard show           # all runs sorted by MAE/loss
python -m tools.ml.scoreboard show --task height
```
