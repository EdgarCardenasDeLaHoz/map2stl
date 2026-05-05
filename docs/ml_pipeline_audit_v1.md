# ML Pipeline Audit — Breaking Through the 0.42 Plateau

**Date:** 2025-07  
**Context:** Retna_V1 NAS run stuck at val_loss ≈ 0.42–0.43. Target is 0.37.  
**Model:** Retna_V1, 9 blocks, channels=[8,8,10,20,14,14,16,16,22], ~75K–90K params  
**Dataset:** 100 tiles, 128×128 .npz, 85 train / 15 val  

---

## Issues Found

### 1. Spatial Resolution Collapse (Critical)

Each `res_conv` block applies a stride-2 convolution, halving spatial resolution:

| Block | Resolution after (128px input) |
|-------|-------------------------------|
| 0     | 64×64                          |
| 1     | 32×32                          |
| 2     | 16×16                          |
| 3     | 8×8                            |
| 4     | 4×4                            |
| 5     | 2×2                            |
| 6     | 1×1 ← spatial collapse          |
| 7     | 1×1 (padded; no change)        |
| 8     | 1×1 (padded; no change)        |

**Blocks 6, 7, 8** compute at 1×1 resolution. They are pure channel-mixing layers with no spatial inductive bias. The high `block_scores` seen at these blocks are an artifact of all global information being concentrated at 1×1 — they look "important" but the importance is a consequence of collapse, not genuine feature learning.

**With `--max-depth 12`, the NAS was free to add more 1×1 blocks.**

**Fix applied:** A spatial-resolution guard was added to `grow_prune.py`. Deepening is now blocked when `depth >= floor(log2(tile_size))` (= 7 for 128px tiles). The guard logs a message when it redirects deepening to widening.

---

### 2. Minimal Augmentation (Major)

`HeightTileDataset` previously applied only horizontal and vertical flips (effective ≈ 4× coverage).

With 85 training tiles, this is the primary data-scarcity bottleneck.

**Fix applied** in `train_retna.py`:

| Augmentation | Effect |
|---|---|
| Horizontal + vertical flip | ×4 (unchanged) |
| Random 90°/180°/270° rotation (p=0.75) | additional ×4 on top |
| Brightness/contrast jitter ±15% on RGB | domain diversity |
| Gaussian noise σ≈0.01 on RGB (p=0.3) | sensor noise simulation |

**Total effective multiplier: ~16×** (340 effective unique augmented samples per epoch, up from ~85).

**Note:** Rotations are applied to both `rgb` and `height` arrays identically — height maps are rotation-invariant so this is label-safe.

---

### 3. Loss Function Mismatch (Moderate)

**Before:**
- Training optimised `squared_residual_dice` (Dice + MSE)
- `evaluate()` computed val_loss using plain `dice_loss`
- The LR scheduler (`ReduceLROnPlateau`) stepped on `dice_loss`

This disconnect meant the scheduler could decay the LR based on dice-only improvements while the actual training objective (dice+MSE) was still improving. Conversely, improvements in MSE were invisible to the scheduler.

**Fix applied:**
- `evaluate()` now accepts an optional `loss_fn` argument
- `train_cycle()` in `grow_prune.py` passes `loss_fn=_loss_fn` so the scheduler steps on the same loss being optimised
- Historical logs used dice_loss, so the val_loss numbers will be slightly higher after this change (because `squared_residual_dice >= dice_loss` always)

---

### 4. Learning Rate Conservatism (Moderate)

`--lr 2e-5` with `--lr-patience 6` means:
- If val_loss hasn't improved in 6 epochs, the LR halves
- Minimum LR floor: 1e-5 (as coded in `train_cycle`)
- At LR=2e-5 with a flat plateau the model may be in a region it can't escape by gradient descent alone

**Recommendation for next run restart:** Try `--lr 1e-4` (or even `5e-5`). The scheduler will back off naturally; starting too low means exploration is too narrow.

---

### 5. Dead Spatial Blocks in Score Ranking

Block scores for blocks 6-8 at 1×1 resolution tend to be artificially high because all spatial information has collapsed there. This caused the NAS grow logic to favour widening these already-over-represented blocks.

The weak-grow warmup and alternate-weak-best strategies already partially address this by rotating focus. The spatial-ceiling guard (fix #1) prevents making it worse via deepening.

**Longer-term:** Consider adding a depth-aware score penalty: `penalised_score[i] = score[i] * (spatial_res[i] / tile_size)` to normalise block importance by resolution.

---

## Fixes Applied (This Session)

| File | Change |
|---|---|
| `tools/ml/train/train_retna.py` | `HeightTileDataset`: added 90° rotation, brightness/contrast jitter, Gaussian noise |
| `tools/ml/train/train_retna.py` | `evaluate()`: now accepts `loss_fn` parameter (default: `dice_loss` for backward compat) |
| `tools/ml/train/grow_prune.py` | `train_cycle()`: passes `loss_fn=_loss_fn` to `evaluate()` to align scheduler signal |
| `tools/ml/train/grow_prune.py` | `main()`: deepening guard — blocks when `depth >= floor(log2(tile_size))` |

---

## Path to 0.37

The 0.42 plateau is primarily a data-scarcity problem amplified by the spatial collapse waste. The augmentation fix addresses data scarcity directly. The loss alignment fix improves training stability. The spatial guard prevents future capacity waste.

**Expected trajectory after these fixes + run restart:**
- Augmentation alone should drive val_loss below 0.40 within 10–15 cycles
- 0.37 is achievable if the architecture has enough capacity (~100K+ params), but requires the model to learn from genuinely diverse samples, not just flipped repeats

**If still stuck at 0.40 after restart:**
- Consider switching to `Retna_V2` (GroupNorm + skip connections, already in `models.py`)
- Or increase dataset size by generating more tiles from different cities/regions
