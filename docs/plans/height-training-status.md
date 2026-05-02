# Building-Height Training — Current State (2026-04-30)

## TL;DR

The production height model is **Retna_V1** — a tiny (9.7k–75k params) first-principles network in `tools/example/networks.py`. It replaced RoofNetV3 (1.4M params) after the latter was diagnosed with **marginal-mean collapse**. Retna outputs are properly spatial, not constant.

**Current champion:** `retna_pruned.pt` — [8,8,10,20,14,14,16,16,22], **75,554 params**, val_loss=0.2691, MAE=3.82m, IoU=0.625, r=+0.90 on the 130-tile combined dataset.

How it got there:
1. Cold-start training → `retna_grow3_long` (9.7k params, val=0.3048)
2. 10-cycle grow (NAS) → `retna_grow4` (69k, val=0.2950)
3. Deep+grow (allow new blocks) → `retna_deepgrow2` (149k, val=0.2685)
4. Single-channel ablation + compact + retrain → `retna_pruned` (75k, val=0.2691) — **half the params, same loss**.

## What changed since the last update

| Topic | Old (RoofNetV3) | New (Retna) |
|---|---|---|
| Params | 1.4 M | 9.7k–70k |
| Loss | L1 + Sobel + IoU | Soft Dice + λ·L2 (or λ·L3) on heights/200m |
| Training time | ~30 min/epoch | ~2 s/epoch |
| Output behaviour | Constant ~7m blob | Per-pixel spatial map; r=+0.87 |
| Mask IoU | 0.49 | 0.62 |
| Files in `models/` | 50+ checkpoints | 5 keepers (grow3_long, grow4, deepgrow, deepgrow2, pruned) |

## Models retained

| File | Arch | Params | Best val | MAE | IoU | r | Notes |
|---|---|---|---|---|---|---|---|
| `retna_grow3_long.pt` | [5,5,8,13] | 9,712 | 0.3048 | 4.14m | 0.584 | +0.87 | initial training champion |
| `retna_grow4.pt` | [14,14,23,43] | 68,848 | 0.2950 | 4.26m | 0.626 | +0.86 | 10-cycle grow ablation reference |
| `retna_deepgrow.pt` | [9,9,12,26,17,17,20] | 71,788 | 0.2778 | 4.17m | 0.614 | +0.83 | first deepen run, 7 blocks |
| `retna_deepgrow2.pt` | [12,12,15,29,20,20,23,23,32] | 149,216 | 0.2685 | 4.03m | 0.631 | +0.92 | 9 blocks, capacity peak |
| **`retna_pruned.pt`** | **[8,8,10,20,14,14,16,16,22]** | **75,554** | **0.2691** | **3.82m** | **0.625** | **+0.90** | **champion: ablation+compact halved deepgrow2's params** |

All on `cache/height_tiles_combined` (130 tiles, dice_l2 loss, HEIGHT_NORM_M=200). The pruned model is the new default starting point for further experiments — see `scripts/train.py`'s `RESUME` constant.

## Open issue: tall-building underprediction

Per-sample MAE on the inspector:
- Short-building tiles: 2.4–3.5m
- Tall-building tiles: 13–16m (consistent across both grown models)

The model has learned the spatial mask (where buildings are) but compresses height amplitude. Capacity is not the bottleneck — the 7× param growth in retna_grow4 did not move the tall-building MAE.

Top hypotheses, ranked:
1. **Input resolution.** ESRI imagery resampled to 128px tiles ≈ 1.5 m/px effective. Shadow-length cues for skyscrapers span more pixels than the receptive field can see at this scale.
2. **Loss saturation.** Dice + L2 on heights/100m flattens above ~50m. Adopted heights/200m + cubic residual (L3) for the next run.
3. **Receptive field.** 4-block stride-2 RF ≈ 16 px. Skyscraper shadow cues exceed this.

## Insights from the 512 px run

The 512 px / `dice_l3` / `HEIGHT_NORM_M=200` run is complete (`retna_512.pt`, 80 epochs, best at ep 76). Key findings:

1. **Per-tile MAE distribution improved at the bottom but not the top.** The best-tile MAE dropped from ~2.5 m (grow3_long, 256px/100m) to ~1.4 m on the same scale, but tall-building tiles (sample 6) are still ~17 m off (`17.27 m × 100/200 = 8.6 m on grow3_long's scale`, marginally better). 4× more pixels did not unlock skyscraper height inference.
2. **r=+0.88 vs +0.87.** Spatial signal essentially unchanged — the model still learns *where* before *how tall*.
3. **dice_l3 was less stable.** With `HEIGHT_NORM_M=200`, normalized residuals are smaller (≤ 0.5 typical), so cubed they vanish; the L3 term contributed almost nothing to gradient. Effective behaviour: pure Dice + tiny ε. This is why dice_l3 trained smoothly but didn't move the needle.
4. **The 92-tile dataset is too small.** Eu collection got 67 tiles vs the original 100; ~30% of bboxes failed coverage filters at the new bbox size. Net training set shrank.

**Conclusions for next iteration:**
- **Larger λ for dice_l3** (or square-root rescale: `λ · |res|³ / max(|res|³, ε)`) so tall residuals actually drive gradient
- Or revert to `dice_l2` and let dataset size drive improvements
- More tile collection at 512 px (different city subset, smaller bbox) to recover the 130-tile baseline
- Consider per-pixel inverse-frequency weighting on `target_height` quantile (tall pixels become loud minority)

## NAS feature matrix (`tools/ml/grow_prune.py`)

| Feature | CLI flag | Default | What it does |
|---|---|---|---|
| Smart-init clone | `--smart-init` / `--no-smart-init` | on | When growing, clone the highest-scoring existing channels into new slots (instead of random Kaiming init). |
| Smart-init jitter | `--smart-init-jitter <σ>` | 0.05 | Gaussian noise (relative to source magnitude) added to cloned weights to break symmetry. |
| Deepen | `--allow-deepen` | off | When the deepest block scores highest, append a new block instead of widening. |
| Max depth | `--max-depth <N>` | 8 | Cap on number of blocks when deepening. |
| All-block widen | (always on during grow) | — | Every block gets +1 channel, hottest gets +`--grow-channels`. |
| Periodic ablation | `--prune-every <N>` | 0 (off) | Replace every Nth grow cycle with an ablation+compact pass. Interleaves growth and lean-up. |
| Final ablation | `--final-prune` | off | Run a single-channel zero-ablation pass after all cycles, then briefly retrain. |
| Final ablation tolerance | `--final-prune-tolerance <Δ>` | 0.005 | Accept a channel zero if val_loss Δ stays below this against the *running* baseline. |
| Final ablation candidates | `--final-prune-floor-pct <P>` | 25 | Only test the bottom-P% of channels by grad×act score. |
| Final ablation retrain | `--final-prune-retrain-epochs <N>` | 10 | Brief recovery training after the prune. |
| Persistent optimizer | (built-in) | on | Adam's running moments are reused across cycles when arch is unchanged; reset on every grow/prune/ablate. |
| Overfit prune trigger | `--overfit-stale-epochs <N>` | 8 | Stale val_loss epochs (with falling train loss) before defensive prune. |
| Defensive prune amount | `--prune-fraction <f>` | 0.25 | Channels to drop per block on overfit-triggered prune. |

## Inspector (`tools/ml/inspect_retna.py`)

Single-file PDF report. First page contains:
- Architecture summary, total params, height-norm
- Per-block contribution bar chart (mean|act| × mean|grad|, normalized)
- Per-block static weight L2 (capacity proxy)
- Per-tile MAE histogram with mean/median markers

Subsequent pages: one per sample (RGB / GT height / Pred height / Pred − GT). Configurable count via `--n-samples` (default 20). Sample selection spreads across MAE quantiles, always including the best and worst tiles.

## Smart-init grow strategy (verified)

`grow_prune.py` supports **smart-init**: when adding new channels, the highest-scoring existing channels (by `mean(|act|)·mean(|grad|)`) are cloned into the new slots with small Gaussian jitter (default σ=0.05), instead of leaving them at random Kaiming init. Default on; disable with `--no-smart-init`.

### A/B verification (2026-04-30)

Same seed, same data (130 tiles, combined), same loss (`dice_l2`), 4 cycles × 25 epochs from `retna_grow3_long.pt`:

| Cycle | Arch | smart val | rand val | smart IoU | rand IoU | smart r | rand r |
|---|---|---|---|---|---|---|---|
| 1 | [5,5,8,13] | 0.3024 | 0.3024 | 0.605 | 0.605 | +0.87 | +0.87 |
| 2 | [6,6,9,17] | **0.2956** | 0.3015 | **0.609** | 0.572 | **+0.93** | +0.78 |
| 3 | [7,7,10,21] | **0.2993** | 0.3012 | **0.602** | 0.584 | **+0.88** | +0.80 |
| 4 | [8,8,11,25] | 0.3011 | **0.2985** | 0.604 | 0.590 | +0.75 | +0.77 |

**Best:** smart at cycle 2 (val=0.2956, IoU=0.609, r=+0.93, **13.4k params**). Random's best was cycle 4 (val=0.2985, IoU=0.590, r=+0.77, **22.6k params**) — needed 2 more grow cycles and 1.7× the parameters to approach but not match.

**Verdict:** smart-init dominates on correlation, IoU, and parameter efficiency for the first 2–3 grow cycles. After that random catches up on raw val_loss because the model is large enough to repair bad init via training. Keep enabled by default for grow/prune NAS.

## Datasets

| Tile dir | Source | Count | Notes |
|---|---|---|---|
| `cache/height_tiles_combined/` | 100 European OSM + 30 Cartagena (broken) | 100 (Cartagena moved to `_bad/`) | original training set |
| `cache/height_tiles_eu11/` | 11 European cities OSM @128px | 660 | new larger training set |
| `cache/height_tiles_us/` | Philadelphia/Chicago/NYC/Boston, provider labels | in progress | for high-rise representation |
| `cache/height_tiles_512_eu/` | Amsterdam + Barcelona OSM, 512px | 67 | unused — too few |
| `cache/height_tiles_512_cart/` | Cartagena providers, 512px | 25 | unused — bad labels |

**Cartagena lesson:** the provider-merge label path returned mostly-empty rasters (provider DEMs lacked Cartagena-specific data). Visual review (`scripts/tile_review.py`) caught this when histograms looked plausible but inspection showed empty GT panels. Always render a manual review PDF before training on a new dataset.

## Reproduction

The recommended path is `scripts/train.py`, which has all knobs at the top of the file and chains collect → train → inspect.

```bash
# Show all training runs in scoreboard
python -m tools.ml.scoreboard show --task height

# Inspect a checkpoint (renders 20-tile PDF + per-block contribution chart)
python -m tools.ml.inspect_retna \
    --checkpoint models/retna_pruned.pt \
    --tiles cache/height_tiles_combined \
    --out output/retna_pruned_inspect.pdf --n-samples 20

# Manual review of training tiles (numbered PDF for filtering)
python scripts/tile_review.py render
# then drop bad ones by index:
python scripts/tile_review.py drop 100 101 102 ...

# Plain training (resumes from RESUME constant)
python scripts/train.py train

# Grow/prune NAS (smart-init + periodic ablation)
python scripts/train.py grow

# Grow/prune with deepening (adds blocks when deepest scores highest)
python scripts/train.py deep
```

## Active code paths

| File | Role |
|---|---|
| `tools/example/networks.py` | `Retna_V1` definition |
| `tools/ml/train_retna.py` | Plain trainer (dice / dice_l2 / dice_l3) |
| `tools/ml/grow_prune.py` | Iterative NAS: grad×activation scoring, all-layer growth, overfit-triggered prune |
| `tools/ml/inspect_retna.py` | Visual inspector (RGB / GT / Pred / Error) |
| `tools/ml/collect_osm_tiles.py` | Tile collector (OSM labels or provider merge) |
| `tools/ml/scoreboard.py` | `models/scoreboard.json` registry |
| `tools/ml/analyze.py` | Diagnostic: pearson correlation, activation stats |
| `tools/ml/baselines.py` | Zero-param sanity baselines |
| `tools/ml/pipeline.py` | One-CLI driver chaining the above |

## Deprecated (still on disk for reference, not maintained)

`tools/ml/train.py`, `tools/ml/models.py`, `tools/ml/data.py`, `tools/ml/eval.py`, `tools/ml/gradient_analysis.py`, `tools/ml/simulate_data.py`, `tools/ml/predict_demo.py` — all RoofNetV3-era. Used only by 2 notebooks (`notebooks/train_height_cnn.py`, `notebooks/height_training_inspector.py`). The `RoofNetProvider` is also legacy and auto-disables since no compatible checkpoint exists in `models/`.
