# Scripts Archive

This folder contains historical and experimental training/analysis scripts that are no longer actively used.

## Contents

### Old Phase Training Scripts (A-F, early G)
- `train_phase_c_wider_early.py` — Phase C: Early wider architecture experiments
- `train_phase_d_extended.py` — Phase D: Extended training experiments
- `train_phase_e_ultra.py` — Phase E: Ultra configuration experiments
- `train_phase_f_prune_first.py` — Phase F: Prune-first strategy experiments
- `train_phase_g_hires.py` — Phase G variant: High-res specific training
- `train_phase_g_global_dataset.py` — Phase G variant: Global dataset training
- `train_master_phases_d_and_e.py` — Combined phases D+E experiments
- `train.py` — Generic training script (superseded)
- `train_two_phase.py` — Two-phase training experiment
- `train_prune_first.py` — Prune-first experiment

**Status:** Historical - Phase G and H are the current baseline and test versions

### Data Collection Scripts
- `collect_us_tiles.py` — US SRTM tile collection (completed)
- `collect_us_tiles_v2.py` — Variant version (superseded)
- `collect_us_tiles_direct.py` — Direct collection method (superseded)
- `recollect_tiles_hires.py` — High-res tile recollection (completed)
- `show_val_tiles.py` — Validation tile visualization
- `tile_review.py` — Manual tile review utility

**Status:** Data collection complete - scripts kept for reference only

### Report Generation
- `generate_phase_g_final_report.py` — Phase G final report generation
- `generate_phase_g_report_pdf.py` — Phase G PDF report generation

**Status:** Reports already generated and stored in `models/`

## Active Scripts (in parent directory)

- `train_phase_h.py` — **Current training script** (Phase H testing)
- `phase_g.py` — **Phase G utilities and extraction**
- `evaluate_stratified.py` — **Regional stratified evaluation**
- `extract_phase_g_tile_metrics.py` — **Tile-level metrics extraction**
- `compare_models.py` — **Model comparison utilities**
- `compare_phase_g_vs_baseline.py` — **Performance comparison**
- `analyze_phase_g_tiles.py` — **Phase G tile analysis**
- `analyze_regional_tiles.py` — **Regional tile analysis**
- `visualize_regional_tiles.py` — **Regional tile visualization**
- `explore_losses.py` — **Loss function exploration**
- `inspect_tiles.py` — **Tile inspection utility**

## To Use Old Scripts

If you need to reference or revive an old script:

```bash
cd scripts/archive
python <script_name>.py
```

Or copy back to parent directory if needed.

## Cleanup Stats

**Archived:** 18 scripts
**Kept Active:** 11 scripts
**Space Saved:** ~110 KB in main scripts folder (cleaner organization)
**Date:** May 8, 2026
