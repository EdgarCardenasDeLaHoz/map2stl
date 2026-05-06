# Phase G Cleanup Summary

**Date**: May 6, 2026  
**What was done**: Consolidated all Phase G documentation and evaluation scripts into a single, clear structure.

---

## The Problem

Before cleanup:
- **9 markdown files** with overlapping content, unclear which was current
- **7 Python scripts** scattered across `scripts/`, many with hardcoded paths and broken references
- **No single source of truth** — Phase G status was spread across MASTER, COMPLETE, EXECUTION-STATUS, FINAL-SUMMARY, and others
- Users had to read multiple docs to understand: What phase are we in? What was the result? How do I evaluate?

## The Solution

### 1. Documentation: One Master File

**Active:**
- `PHASE-G-README.md` — Single source of truth. Everything a user needs to know: status, results, how to evaluate, how to deploy.

**Archived:**
- All 7 old markdown files moved to `docs/phase_g_archive/` for historical reference but out of the working directory.

### 2. Scripts: Unified Module

**Consolidated into:** `scripts/phase_g.py`

One entry point for all evaluation:
```bash
python scripts/phase_g.py extract    # Extract metrics
python scripts/phase_g.py report     # Generate PDF report  
python scripts/phase_g.py inspect    # Visual tile inspection
python scripts/phase_g.py compare    # Compare vs baseline
```

**Standalone tools preserved** (in case they're used elsewhere):
- `train_phase_g_global_dataset.py`
- `train_phase_g_hires.py`
- `extract_phase_g_tile_metrics.py`
- `compare_phase_g_vs_baseline.py`
- `analyze_phase_g_tiles.py`

**Deleted (broken, obsolete):**
- `phase_g_summary.py` — hardcoded old dataset + deleted references

### 3. Reports: Real Prediction Results

**New:**
- `models/PHASE_G_FINAL_REPORT.pdf` (55KB) — 3-page report with real per-tile metrics aggregated by city
  - Page 1: Model summary + architecture
  - Page 2: Per-city MAE breakdown (16 cities)
  - Page 3: Regional analysis (EU vs US vs South America)

**Old:**
- `models/PHASE_G_STATUS_REPORT.pdf` (65KB) — Simulated loss curves (not real data), replaced by above

---

## New Structure (Working Directory)

```
strm2stl/
├── PHASE-G-README.md                    ← START HERE (single source of truth)
├── docs/phase_g_archive/                ← Historical docs (git-preserved)
│   ├── PHASE-G-MASTER.md
│   ├── PHASE-G-STAGE4-FINAL.md
│   ├── PHASE-G-FINAL-SUMMARY.md
│   ├── PHASE-G-RESOLUTION-STRATEGY.md
│   ├── PHASE-G-CROPS-STRATEGY.md
│   ├── CLEANUP-PHASE-G-LOG.md
│   └── PHASE-G-STATUS-FINAL.md
├── scripts/
│   ├── phase_g.py                       ← NEW: Unified evaluation
│   ├── train_phase_g_global_dataset.py  (kept)
│   ├── train_phase_g_hires.py           (kept)
│   ├── extract_phase_g_tile_metrics.py  (kept, used by phase_g.py)
│   ├── compare_phase_g_vs_baseline.py   (kept, used by phase_g.py)
│   └── analyze_phase_g_tiles.py         (kept, used by phase_g.py)
├── output/
│   └── phase_g_global_metrics.json      ← Per-tile metrics (generated)
└── models/
    ├── retna_phase_g_global.pt          ← CURRENT BEST
    └── PHASE_G_FINAL_REPORT.pdf         ← Real prediction results
```

---

## Key Facts (From PHASE-G-README)

| Item | Value |
|------|-------|
| **Status** | ✅ Complete |
| **Final Model** | `retna_phase_g_global.pt` |
| **Mean MAE** | 8.10m (on test tiles) |
| **Median MAE** | 5.43m |
| **Architecture** | [6,7,6,8,7,7,7,7,9], 22,184 params |
| **Training Time** | ~4 hours |
| **Dataset** | 625 tiles (462 EU + 81 US + 30 Cartagena) |
| **Phase 5 Status** | Attempted but did not complete |

---

## How to Use Now

### Evaluate the Model

```bash
# One-time: extract metrics
python scripts/phase_g.py extract

# Generate reports
python scripts/phase_g.py report    # City-level results
python scripts/phase_g.py inspect   # Visual predictions
python scripts/phase_g.py compare   # vs EU baseline
```

### Deploy

```bash
cp models/retna_phase_g_global.pt models/deployment.pt
```

### Understand What Happened

Start with `PHASE-G-README.md`. For detailed design rationale, refer to the archived docs in `docs/phase_g_archive/`.

---

## What Changed

| What | Before | After | Why |
|-----|--------|-------|-----|
| Documentation | 9 files with overlap | 1 master + 7 archived | Reduce confusion, single source of truth |
| Scripts | 7 scattered tools | 1 unified `phase_g.py` + standalones | Easier to discover, consistent interface |
| Status clarity | "which doc is current?" | PHASE-G-README is THE answer | Clear navigation |
| Reports | Simulated loss curves | Real per-tile + city-level results | Actual data, not estimates |
| Deleted files | `phase_g_summary.py` | (cleaned up) | Broken references + hardcoded old data |

---

## Files Deleted

- `PHASE-G-COMPLETE.md` — Entirely superseded by PHASE-G-README
- `PHASE-G-EXECUTION-STATUS.md` — Mid-run tracking document, obsolete
- `scripts/phase_g_summary.py` — Broken (old dataset, deleted references)

---

## Git History

All deleted files are preserved in git history. To recover any, run:
```bash
git log --name-status --diff-filter=D -- <filename>
git show <commit>:<path-to-file> > <filename>
```

---

## Future Work

1. **Phase 5 retry** (optional): `python scripts/train_phase_g_hires.py`
   - Note: Currently warmstarts from `retna_pruned.pt`; consider updating to warmstart from `retna_phase_g_global.pt`

2. **Better per-tile visualization**: `analyze_phase_g_tiles.py` generates PDF; consider integrating into `phase_g.py inspect`

3. **Deployment**: Copy `retna_phase_g_global.pt` to production model path

---

## Questions?

Refer to `PHASE-G-README.md` for:
- How to use the model
- Results breakdown by city
- Evaluation workflow
- Deployment instructions
