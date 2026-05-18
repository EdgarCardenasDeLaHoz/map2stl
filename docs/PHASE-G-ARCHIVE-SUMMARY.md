# Phase G Training — Archive Summary

**Date**: May 5-6, 2026  
**Conclusion**: Halted; retna_pruned.pt (0.2691 loss) remains production model

## What Was Done

Phase G attempted to improve building height prediction by retraining the learned architecture [8,8,10,20,14,14,16,16,22] from retna_pruned.pt on a larger dataset.

### Results

| Stage | Dataset | Architecture | Loss | MAE | Status |
|-------|---------|--------------|------|-----|--------|
| **retna_pruned** | 111 curated | [8,8,10,20,14,14,16,16,22] | **0.2691** ⭐ | 3.82m | Production |
| **Phase G Stage 4** | 625 tiles (15 cities) | [6,7,6,8,7,7,7,7,9] | 0.4231 | 7.55m | Completed |
| **Phase G Phase 5** | 573 high-res tiles | — | — | — | Not completed |

### Key Finding

Architecture is **dataset-specific**: retna_pruned learned its shape for the original 111-tile curated dataset. When applied to a different 85-tile subset with identical training config, loss degraded 40.8% (0.2691 → 0.3787). Multi-cycle training (which would allow LR adaptation) hangs due to infrastructure issue in `tools.ml.train.grow_prune` module.

## Why Phase G Stopped

1. **Multi-cycle training hangs** — `--grow-channels 0` config causes hang after "Cycle 1/N" header (attempted 40 and 10 cycles)
2. **Architecture doesn't transfer** — Different dataset distribution causes overfitting; would need different hyperparameters
3. **No clear path forward** — Fixing grow_prune hang would be exploratory; production has solid model

## Recommendation

Use retna_pruned.pt and close the thread. If future work needs larger-dataset training:
- Fix grow_prune multi-cycle hang (debug `tools.ml.train` module)
- Or train fresh architecture on OSM-only dataset from scratch
- Or iterate on skyline_cv height sources (F-SKY series) which is more productive

## Files Generated

- `retna_phase_g_global.pt` (22,184 params, Stage 4 result)
- `retna_phase_g_final_1cycle.pt` (309 KB, diagnostic test)
- Training scripts: `train_phase_g_*.py` (3 files)
- Analysis docs in `phase_g_archive/` (now archived)

## Archived Files

8 detailed Phase G docs archived (consolidated here):
- PHASE-G-FINAL-SUMMARY.md → this summary
- PHASE-G-MASTER.md, PHASE-G-STATUS-FINAL.md, PHASE-G-STAGE4-FINAL.md → Stage 4 results
- PHASE-G-RESOLUTION-STRATEGY.md, PHASE-G-CROPS-STRATEGY.md → training approach
- CLEANUP-PHASE-G-LOG.md → what was cleaned up
- Other Phase G docs (2 more)

**Access**: Old docs remain in `phase_g_archive/` if detailed historical context needed.
