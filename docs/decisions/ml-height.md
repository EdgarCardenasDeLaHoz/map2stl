# Learned height models

What happened to the Retna building-height CNN work, what is actually live, and how checkpoints are kept. The training history is in `history/ml-height/`. Related: [architecture.md](architecture.md).

### 2026-09-28 — No learned height model runs at runtime; only the roof-shape GBM is live
- **Decision:** treat the Retna CNN as history. Building heights come from providers and OSM tags, not a CNN.
  - The only live model is `models/roof_shape_gbm.joblib`, loaded by `map2stl/city2stl/roof_model.py`.
  - No `retna*.pt` is loaded by `app/`, `city2stl/` or `geo2stl/` (only tools, notebooks, tests).
  - `map2stl/city2stl/height/predict.py::predict` (used only by the SDK's `predict_heights`) defaults to `models/height_unet.pt`, which does not exist.
- **Why:** several docs claimed "production = Retna" or "deploy retna_wider_early.pt"; neither was ever wired, and `retna_wider_early.pt` is not in `models/`.
- **Supersedes / superseded by:** supersedes [2026-05-04 — Phase C is the final model](#2026-05-04--phase-c-retna_wider_early-is-the-final-model-superseded).
- **Source:** code check 2026-09-28; docs inventory.

### 2026-05-17 — retna_pruned stays the best checkpoint and the architecture search is closed
- **Decision:** keep `retna_pruned.pt` (loss 0.2691, MAE 3.82 m, IoU 0.625) as the reference checkpoint; stop the Phase G/H search.
- **Why:** nothing beat it: Phase C-E stuck at ≥ 0.41 loss, Phase G ≥ 0.38. The model was still never promoted into the runtime (see 2026-09-28).
- **Supersedes / superseded by:** —
- **Source:** 2026-05-17 cleanup session; [Phase G archive summary](../history/ml-height/PHASE-G-ARCHIVE-SUMMARY.md).

### 2026-05-06 — Never lose a good checkpoint
- **Rule:**
  - Commit (or otherwise back up) a model that beats the reference before experimenting further.
  - Never delete a checkpoint during cleanup without a backup.
  - Give checkpoints names that state what they are.
- **Why:** `retna_pruned.pt` was deleted during a cleanup while Phases C-E trained, and cost 2+ hours to recover from backups.
- **Supersedes / superseded by:** —
- **Source:** [MODEL-STRATEGY](../history/ml-height/MODEL-STRATEGY.md).

### 2026-05-04 — Phase C (retna_wider_early) is the final model [superseded]
- **Decision (then):** deploy `retna_wider_early.pt` (val loss 0.4157, 9.3 k params, architecture [4,4,4,4,4,4,4,4,4]) over Phase B (0.4169, 26.2 k) and Phase 2 (0.4434, 75.5 k).
- **Why (then):** best loss of the NAS series and smallest model.
- **Supersedes / superseded by:** superseded by [2026-05-17 retna_pruned stays best](#2026-05-17--retna_pruned-stays-the-best-checkpoint-and-the-architecture-search-is-closed) and [2026-09-28 not wired at runtime](#2026-09-28--no-learned-height-model-runs-at-runtime-only-the-roof-shape-gbm-is-live).
- **Source:** [Phase C decision](../history/ml-height/training-2026-05/phase-c-completion-and-final-decision.md); [MODELS-REFERENCE](../history/ml-height/MODELS-REFERENCE.md).

## Rejected hypotheses

### 2026-05-06 — retna_pruned's learned architecture transfers to a larger dataset
- **Hypothesis:** training the [8,8,10,20,14,14,16,16,22] architecture on more tiles beats 0.2691.
- **Measured:** 1 cycle on an 85/15 set gave 0.3787 loss (40.8 % worse); multi-cycle `grow_prune` runs hung after cycle 1.
- **Verdict:** refused — the architecture is dataset-specific (curated Amsterdam/Barcelona tiles).

### 2026-05-04 — Forcing a minimal uniform network finds a better model
- **Hypothesis:** growing from a lean baseline (Phases C-E) discovers a better architecture.
- **Measured:** converged to uniform 4-channel blocks at 0.41+ loss vs 0.2691 for the prune-first retna_pruned.
- **Verdict:** refused — forced minimalism locks into a local minimum; prune-first found the better shape.
