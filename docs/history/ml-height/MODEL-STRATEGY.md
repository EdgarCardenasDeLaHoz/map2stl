# Model Management & Git Strategy

**Purpose**: Maintain best models safely, prevent accidental loss like retna_pruned.pt

---

## Why We Lost retna_pruned.pt & How We Found It

### What Happened
1. retna_pruned.pt was created (0.2691 loss - excellent)
2. Continued training Phases C-E (trying to improve further)
3. Got stuck at 0.4108 loss (Phase E)
4. Forgot about older retna_pruned.pt checkpoint
5. Accidentally deleted it during cleanup (restored from backups)

### Key Lesson: Lost 2+ hours of training time because
- No systematic model tracking
- No git commits of best models
- No backup strategy
- Experimental models cluttered the directory

---

## Current Production State

### Best Model: retna_pruned.pt
```
Loss:        0.2691 ← PRODUCTION (keep using this!)
MAE:         3.82m
IoU:         0.625
Params:      75,554
Architecture: [8, 8, 10, 20, 14, 14, 16, 16, 22]
Status:      Committed to git (safe)
Date:        April 30, 2026
```

### Reference Models (for comparison)
```
retna_ultra.pt     (Phase E: 0.4108) - uniform constraint reference
retna_extended.pt  (Phase D: 0.4141) - extended training reference
retna_wider_early.pt (Phase C: 0.4157) - baseline reference
```

---

## Why Phases C-E Couldn't Beat 0.2691

### Root Cause: Architectural Constraint

**Phases C-E forced uniform [4,4,4,4,4,4,4,4,4]:**
```
Layer 0-2: 4 channels (ok for early features)
Layer 3:   4 channels (BOTTLENECK - TOO NARROW!)
           Can't compress features properly
           Information loss at critical stage
Layer 4-7: 4 channels (ok for synthesis)
Layer 8:   4 channels (OUTPUT - TOO NARROW!)
           Can't generate diverse per-pixel predictions
           Limited to 0.41+ loss ceiling
```

**retna_pruned discovered [8,8,10,20,14,14,16,16,22]:**
```
Layer 3:   20 channels (proper bottleneck for feature compression)
Layer 8:   22 channels (rich per-pixel prediction capability)
Result:    0.2691 loss (found much better local minimum)
```

### Why Uniform Failed
- **Assumption**: Minimal = Optimal (wrong!)
- **Reality**: Layer 3 needs 20 channels for features
- **Reality**: Output needs 22 channels for diversity
- **Constraint**: Forcing all layers to 4 created information bottleneck

### Training Strategy Lesson
| Approach | Result | Why |
|----------|--------|-----|
| **retna_pruned**: Start large → prune to find essential | 0.2691 | Discovers true optimum |
| **Phases C-E**: Grow from minimal → uniform | 0.41+ | Locked into local minimum early |

**Prune-first > Grow-minimal** (for architecture discovery)

---

## Going Forward: New Strategy

### 1. Commit Every Good Model

**When to commit:**
- ✅ New best model achieved (loss < previous)
- ✅ Phase completes (even if not best)
- ✅ Before major experimental changes

**How to commit:**
```bash
git add models/retna_phase_g.pt
git commit -m "Model: Phase G achieves 0.25 loss (improved from 0.2691)

- Loss: 0.25
- MAE: 3.5m
- IoU: 0.65
- Architecture: [learned during training]
- Strategy: Started from retna_pruned architecture
- Dataset: 437+77 tiles (full OSM)

Improvement: 7% better than retna_pruned
"
```

### 2. Never Delete Without Backup

```bash
# BEFORE experimenting:
git add models/retna_pruned.pt
git commit -m "Backup: Committing retna_pruned (0.2691) before Phase G"

# THEN run Phase G

# AFTER Phase G:
# If better: commit new model
# If worse: git checkout HEAD~1 -- models/  (restore retna_pruned)
```

### 3. Clear Filenames

```
retna_pruned.pt                    (best ever, keep!)
retna_phase_g_0_25_loss.pt        (phase G result)
retna_phase_h_tall_buildings.pt   (specialized variant)
```

Not:
```
model_new.pt
test.pt
retna_v2.pt
```

### 4. Tag Major Milestones

```bash
git tag -a "best-0.2691" -m "retna_pruned: best model achieved"
git tag -a "phase-G-start" -m "Beginning Phase G from 0.2691"
git tag -a "phase-G-end" -m "Phase G: achieved [loss]"
```

---

## Phase G: What to Do

### Step 1: Commit Current Best
```bash
git add models/retna_pruned.pt
git commit -m "Checkpoint: retna_pruned (0.2691) before Phase G"
git tag "checkpoint-before-phase-g"
```

### Step 2: Start Phase G

**Use retna_pruned's architecture:** [8, 8, 10, 20, 14, 14, 16, 16, 22]  
**Use larger dataset:** retna_pruned was 111/19, Phase G use 437/77  
**Expected:** 0.25-0.27 loss (combine best arch + best dataset)

### Step 3: Commit Results
```bash
# If Phase G achieves better loss:
git add models/retna_phase_g_[LOSS].pt
git commit -m "Model: Phase G achieves [LOSS] loss ([+X]% vs retna_pruned)"

# If Phase G worse or equal:
git checkout HEAD~1 -- models/
# (keeps retna_pruned as production)
```

---

## Git Commands Reference

### Safe Workflow
```bash
# Check current state
git status

# Commit a model
git add models/retna_*.pt
git commit -m "Model: [Name] achieves [Loss]"

# Before experiment
git tag "before-experiment-X"

# After experiment (if worse)
git checkout HEAD~1 -- models/

# View model history
git log models/retna_pruned.pt
```

### View Model Evolution
```bash
# See all model commits
git log --oneline -- models/retna*.pt

# Compare two models
git show abc1234:models/retna_phase_g.pt > /tmp/model1.pt
git show def5678:models/retna_phase_g.pt > /tmp/model2.pt
# (compare in production)
```

---

## Prevent Future Loss

### ✅ Do This
- Commit models immediately after training
- Use clear filenames with metrics
- Document architecture in commit message
- Tag milestones
- Keep reference checkpoints (C, D, E) in git

### ❌ Don't Do This
- Store "latest" or "best" without metrics in filename
- Delete old models without backup
- Experiment without committing first
- Forget to document improvements
- Rely on external storage only

---

## Current Model Backup Status

| Model | Status | Backed up | Notes |
|-------|--------|-----------|-------|
| retna_pruned.pt (0.2691) | Production | ✅ Git commit | BEST EVER |
| retna_ultra.pt (0.4108) | Reference | ✅ Git commit | Phase E reference |
| retna_extended.pt (0.4141) | Reference | ✅ Git commit | Phase D reference |
| retna_wider_early.pt (0.4157) | Reference | ✅ Git commit | Phase C baseline |

**All safe. None will be accidentally lost again.**

---

## Next Steps

1. ✅ Commit retna_pruned.pt as production (DONE)
2. ✅ Keep reference models C, D, E (DONE)
3. ⏳ Start Phase G training from retna_pruned architecture
4. ⏳ Commit Phase G results
5. ⏳ Compare: retna_pruned (0.2691) vs Phase G vs Phase C-E

---

**Golden Rule**: If it's a good model, commit it to git immediately. Never lose a breakthrough again.
