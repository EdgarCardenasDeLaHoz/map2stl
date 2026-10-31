# models/

The only model the code loads at runtime is **`roof_shape_gbm.joblib`**, the flat/pitched
roof classifier. `city2stl/roof_model.py` loads it for `roof_classifier`. See
[../docs/reference/roof-shape-model.md](../docs/reference/roof-shape-model.md).

- `scoreboard.json`: training-run log `tools/ml/eval/scoreboard.py` appends to.
- `retna_*.pt`: building-height CNN checkpoints (`retna_pruned`, `retna_ultra`, `retna_phase_g_global`). Git ignores `*.pt`. Their training code is `tools/ml/`. **They are not used at runtime.**
- `retna_*_history.json`, `PHASE_G_*_REPORT.pdf`: training curves and reports from those runs.
- `height_unet.pt`, the default checkpoint in `city2stl/height/predict.py`, does not exist.

For the history of the height CNN, see [../docs/history/ml-height/README.md](../docs/history/ml-height/README.md).

## Geopose overhead height weights (outside the repo, F-SATTALL)

`city2stl/height/satellite/geopose.py` loads these from `$GEOPOSE_MODELS` or
`~/data/models/geopose/<name>/` (`C:\Users\eac84\data\models\geopose\` here). They are **not in
OneDrive or git**; fetch once per PC. All load with `torch.load(..., weights_only=True)`. They need
torch + torchvision (pubgeo) and timm (RS3DAda / AltiMap) in the venv, and a CUDA GPU
(`device="cpu"` is an explicit opt-in for a handful of tiles).

| Backend | File | Size | Source | Licence |
|---|---|---|---|---|
| `pubgeo` | `pubgeo_baseline/geopose_baseline.pth` (from `geopose_baseline.zip`) | 98 MB | pubgeo `monocular-geocentric-pose` GitHub release | Apache-2.0 |
| `rs3dada` | `rs3dada/RS3DAda_vitl_DPT_height.pth` | 1.47 GB | Hugging Face, SynRS3D / RS3DAda | MIT |
| `altimap` | `altimap_v1/best.pth` | 1.47 GB | Hugging Face, AltiMap v1 (fine-tune of RS3DAda) | MIT |
| `first` | `$GEOPOSE_1ST_DIR` (slot only, no loader yet) | 10.5 GB zip | IEEE DataPort, Geopose 1st place; the user downloads it | see DataPort |
