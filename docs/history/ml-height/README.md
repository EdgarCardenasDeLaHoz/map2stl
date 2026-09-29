# Building-height CNN (history)

> **Not used at runtime.** No CNN height model runs in the app or the mesh pipeline. Building
> heights come from OSM tags and the providers in `city2stl/height/providers/` (see
> [../../reference/height-providers.md](../../reference/height-providers.md)). Several docs in
> this folder say "production = Retna_V1" or "deployed model: retna_pruned.pt". That was
> never true, and those snapshots carry a "stale snapshot" banner.

## Runtime status (checked 2026-09-28)

- `city2stl/height/predict.py` `predict()` has two modes:
  - `"pretrained"`: Depth Anything V2, zero-shot, calibrated to metres from the OSM heights in the tile.
  - `"unet"`: loads `_UNET_DEFAULT_CKPT = <Code>/models/height_unet.pt`. `_MODELS_DIR` is
    `Path(__file__).parents[3] / "models"`, which is `Code/models/`, not `map2stl/models/`.
    Neither folder has `height_unet.pt`.
- `predict()` is reached only from the SDK method `TerrainSession.predict_heights()`. No app
  route and no mesh stage calls it. `city2stl/skyline/depth_estimation.py` reuses only its
  Depth Anything loader.
- The Retna checkpoints (`map2stl/models/retna_*.pt`, which git ignores) are loaded only by
  `tools/ml/` (training and analysis scripts). The only live model in `models/` is
  `roof_shape_gbm.joblib` (roof shape, not height). See [../../../models/README.md](../../../models/README.md).
- The "never lose a checkpoint" rule and why Retna was not promoted:
  [../../decisions/ml-height.md](../../decisions/ml-height.md).

## Timeline (2026-04 to 2026-05)

| When | Work | Outcome | Docs |
|---|---|---|---|
| to 04-28 | HeightUNet / RoofNetV2 / RoofNetV3 (1.4M params) | RoofNetV3 collapsed to the marginal mean, so it was dropped | [height-model-comparison.md](height-model-comparison.md), [roof-ml-architecture.md](roof-ml-architecture.md) |
| 04-30 | Retna_V1: tiny first-principles CNN (its original tools/example/networks.py has since been deleted; the training code is in `tools/ml/`) | `retna_pruned.pt`: 75.5k params, val loss 0.2691, MAE 3.82 m, IoU 0.625 on the 130-tile set. Best result, never wired in | [BUILDING-HEIGHT-MODEL-SUMMARY.md](BUILDING-HEIGHT-MODEL-SUMMARY.md), [training-2026-05/height-training-status.md](training-2026-05/height-training-status.md) |
| 05-04 | Two-phase (segmentation, then regression) training | Superseded by prune/grow | [training-2026-05/two-phase-height-strategy.md](training-2026-05/two-phase-height-strategy.md), [training-2026-05/two-phase-training-results.md](training-2026-05/two-phase-training-results.md) |
| 05-04 to 05-05 | Phases A–E: prune-first, then grow/prune architecture search | Shrank to a uniform [4×9] net (9.3k params). Loss 0.4157 (C) to 0.4108 (E) on a different validation split, so not comparable to 0.2691 | [training-2026-05/ml-training-final-report-2026-05-04.md](training-2026-05/ml-training-final-report-2026-05-04.md), [training-2026-05/ALL-PHASES-FINAL-COMPARISON.md](training-2026-05/ALL-PHASES-FINAL-COMPARISON.md), [training-2026-05/phase-c-completion-and-final-decision.md](training-2026-05/phase-c-completion-and-final-decision.md) |
| 05-05 to 05-06 | Phase G: retrain on 625 global tiles (15 cities) | `retna_phase_g_global.pt`: MAE 7.55 m. The learned architecture did not transfer, and multi-cycle `grow_prune` runs hung. Halted | [PHASE-G-ARCHIVE-SUMMARY.md](PHASE-G-ARCHIVE-SUMMARY.md), [phase-g/PHASE-G-MASTER.md](phase-g/PHASE-G-MASTER.md) |
| 05-06 to 05-08 | Phase H: 573 high-res tiles, warm start from Phase G, gradient freezing on growth | Height-scaling bug fixed mid-run. The old `map2stl/CLAUDE.md` reported a validation RMSE of 17.75 m and "promotion eligible", but `retna_phase_h_final.pt` is not in `models/` and was never wired in | [phase-history/PHASE-H-LAUNCH-SUMMARY.md](phase-history/PHASE-H-LAUNCH-SUMMARY.md), [phase-history/PHASE-H-BUG-FIX.md](phase-history/PHASE-H-BUG-FIX.md), [growth_degradation_analysis.md](growth_degradation_analysis.md) |

Why it stopped: the Phase G summary recommended keeping `retna_pruned.pt` and closing the
thread, and putting the effort into measured height sources instead. Nobody ever wired a
Retna checkpoint into `predict.py` or a pipeline stage.

## What's here

- Top level: summaries, strategy, the old model reference (`MODELS-REFERENCE.md`, stale),
  tile analysis, growth-degradation studies, the pipeline audit, the old tooling layout
  (`ml-pipeline.md`), height data sources, the improvement plan, the ROOF-2 architecture, and
  `BATCH_NORM_RESET_README.md` (from `tools/ml/train/`).
- [training-2026-05/](training-2026-05/): Phase A–F logs from `plans/height_training/`. The 4
  live-status files and `PHASE-E-COMPLETION-PENDING.txt` are stale snapshots.
- [phase-g/](phase-g/): Phase G working docs. `CLEANUP-PHASE-G-LOG`, `PHASE-G-FINAL-SUMMARY`
  and `PHASE-G-STATUS-FINAL` are duplicates.
- [phase-history/](phase-history/): Phase G/H status reports:

| File | Contents |
|---|---|
| `QUICK-START.md` | Phase G 90-second status snapshot (stale). |
| `PHASE-G-README.md` | Phase G baseline (MAE 7.55 m, 22,184-param architecture). |
| `PHASE-H-LAUNCH-SUMMARY.md` | Phase H launch and validation setup. |
| `PHASE-H-BENCHMARK-TIERS.md` | Phase H promotion tiers. |
| `PHASE-H-BUG-FIX.md` | The height-scaling bug fixed during Phase H. |
| `PHASE-H-RUN-REGISTRY.json` | Phase H run registry (metadata). |
