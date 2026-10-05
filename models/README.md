# models/

The only model the code loads at runtime is **`roof_shape_gbm.joblib`**, the flat/pitched
roof classifier. `city2stl/roof_model.py` loads it for `roof_classifier`. See
[../docs/reference/roof-shape-model.md](../docs/reference/roof-shape-model.md).

- `scoreboard.json`: training-run log `tools/ml/eval/scoreboard.py` appends to.
- `retna_*.pt`: building-height CNN checkpoints (`retna_pruned`, `retna_ultra`, `retna_phase_g_global`). Git ignores `*.pt`. Their training code is `tools/ml/`. **They are not used at runtime.**
- `retna_*_history.json`, `PHASE_G_*_REPORT.pdf`: training curves and reports from those runs.
- `height_unet.pt`, the default checkpoint in `city2stl/height/predict.py`, does not exist.

For the history of the height CNN, see [../docs/history/ml-height/README.md](../docs/history/ml-height/README.md).
