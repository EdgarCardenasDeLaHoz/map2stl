#!/usr/bin/env python3
"""
Phase H: Adaptive training with frequent optimizer resets and crop augmentation.

Key improvements over Phase G:
1. Always-fresh optimizer (CosineAnnealingLR within each short cycle)
2. Crop augmentation (192×192 from 512×512 hires tiles, or 96×96 from standard)
3. Aggressive growth on plateau (target weakest block +3 channels)
4. Warmstart from retna_phase_g_global.pt (not retna_pruned.pt)
5. Shorter cycles (8 epochs) so optimizer resets more frequently

Expected: 7.55m RMSE (Stage 4) → <7.2m RMSE (Phase H goal: 5-10% improvement)
Duration: ~6-8 hours (30 cycles × 8 epochs)
"""

import os
import sys
import json
import torch
import torch.nn as nn
import numpy as np
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import logging

from torch.optim.lr_scheduler import CosineAnnealingLR
from torch.utils.data import DataLoader, random_split

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.ml.train.grow_prune import (
    block_scores,
    ablate_channels,
    compact_pruned_channels,
    clone_top_channels_into_new,
    shape_aware_load,
)
from tools.ml.data.datasets import HeightTileDataset, build_height_transforms
from tools.ml.models import Retna_V1

# ────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ────────────────────────────────────────────────────────────────────────────

WARMSTART_CHECKPOINT = REPO / "models" / "retna_phase_g_global.pt"
OUTPUT_MODEL = REPO / "models" / "retna_phase_h_final.pt"
LOG_DIR = REPO / "logs"
LOG_FILE = LOG_DIR / "phase_h_training.log"

# Tiles: try hires first, fallback to standard
TILES_HIRES = REPO / "cache" / "height_tiles_global_hires"
TILES_STANDARD = REPO / "cache" / "height_tiles_global"
TILE_DIR = TILES_HIRES if TILES_HIRES.exists() else TILES_STANDARD

# Training hyperparameters
CYCLES = 3                       # Reduced for testing (normally 30)
EPOCHS_PER_CYCLE = 8            # Short cycles → frequent optimizer resets
LR = 6e-6                        # Conservative starting rate
WEIGHT_DECAY = 1e-5
BATCH_SIZE = 4
TILE_SIZE = 512 if TILES_HIRES.exists() else 256
CROP_SIZE = 96  # Preferred crop size for augmentation (5-10x multiplier)

# Growth strategy
GROW_CHANNELS = 3               # Channels added to weakest block on plateau
PLATEAU_THRESHOLD = 0.0015      # delta_val_loss threshold to trigger plateau
PLATEAU_PATIENCE = 2            # Consecutive plateau cycles before growing
PRUNE_EVERY = 6                 # Run ablation every N cycles
ABLATION_TOLERANCE = 0.01       # Prune channels if delta_val_loss < tolerance

# Device
DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ────────────────────────────────────────────────────────────────────────────
# UTILITIES
# ────────────────────────────────────────────────────────────────────────────

def log(msg: str):
    """Log to console with unbuffered output (shell redirection captures to file)."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    log_msg = f"[{timestamp}] {msg}"
    print(log_msg, flush=True)
    sys.stdout.flush()
    sys.stderr.flush()


def freeze_old_neuron_gradients(model: Retna_V1, old_channels: list):
    """
    After growth, freeze gradients on old neurons to force training on new neurons.
    This ensures new capacity actually learns rather than being ignored.
    """
    for block_idx, block in enumerate(model.blocks):
        if block_idx >= len(old_channels):
            continue

        old_dim = old_channels[block_idx]
        # Freeze the conv layers up to the old output dimension
        for layer in block:
            if isinstance(layer, nn.Conv2d):
                # Freeze old output channels
                if layer.out_channels >= old_dim:
                    layer.weight[old_dim:, :, :, :].requires_grad = False
                    if layer.bias is not None:
                        layer.bias[old_dim:].requires_grad = False

def unfreeze_all_gradients(model: Retna_V1):
    """Unfreeze all parameters for normal training."""
    for param in model.parameters():
        param.requires_grad = True

def train_cycle_h(
    model: Retna_V1,
    train_loader: DataLoader,
    val_loader: DataLoader,
    device,
    epochs: int,
    lr: float,
    weight_decay: float,
):
    """
    Single training cycle with CosineAnnealingLR (not ReduceLROnPlateau).
    Always starts with a fresh optimizer → frequent resets.
    """
    # Use MSE loss for regression
    criterion = nn.MSELoss(reduction='mean')
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=weight_decay)
    scheduler = CosineAnnealingLR(optimizer, T_max=epochs, eta_min=lr * 0.1)

    model.to(device)
    history = []
    best_val_loss = float('inf')
    best_state = None

    for epoch in range(1, epochs + 1):
        try:
            log(f"  [Epoch {epoch}/{epochs}] Starting training phase...")
            # Train
            model.train()
            train_loss = 0.0
            n_batches_trained = 0
            for batch_idx, (rgb, height) in enumerate(train_loader):
                try:
                    rgb, height = rgb.to(device), height.to(device)
                    optimizer.zero_grad()
                    pred = model(rgb)
                    loss = criterion(pred, height)
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=1.0)
                    optimizer.step()
                    train_loss += loss.item()
                    n_batches_trained = batch_idx + 1
                except Exception as batch_error:
                    log(f"  ERROR in training batch {batch_idx}: {type(batch_error).__name__}: {batch_error}")
                    raise

            train_loss /= max(1, n_batches_trained)
            log(f"  [Epoch {epoch}/{epochs}] Training complete: {n_batches_trained} batches, loss={train_loss:.4f}")

            # Validate
            log(f"  [Epoch {epoch}/{epochs}] Starting validation phase...")
            model.eval()
            val_loss = 0.0
            val_mse = 0.0
            val_iou = 0.0
            n_batches = 0

            with torch.no_grad():
                for batch_idx, (rgb, height) in enumerate(val_loader):
                    try:
                        rgb, height = rgb.to(device), height.to(device)
                        pred = model(rgb)
                        loss = criterion(pred, height)
                        val_loss += loss.item()

                        # Metrics: MSE in normalized space (0-1), sqrt for RMSE and multiply by 200m for display
                        mse = ((pred - height).pow(2)).mean().item()
                        val_mse += mse

                        # IoU for building presence (height > 0)
                        pred_binary = (pred > 0.05).float()
                        height_binary = (height > 0.05).float()
                        intersection = (pred_binary * height_binary).sum().item()
                        union = (pred_binary + height_binary - pred_binary * height_binary).sum().item()
                        iou = intersection / max(union, 1.0)
                        val_iou += iou
                        n_batches += 1
                    except Exception as batch_error:
                        log(f"  ERROR in validation batch {batch_idx}: {type(batch_error).__name__}: {batch_error}")
                        raise

            val_loss /= max(1, len(val_loader))
            val_mse = val_mse / max(1, n_batches)
            val_iou = val_iou / max(1, n_batches)
            log(f"  [Epoch {epoch}/{epochs}] Validation complete: {n_batches} batches, loss={val_loss:.4f}")

            scheduler.step()
            current_lr = scheduler.get_last_lr()[0]

            # Track best
            is_best = val_loss < best_val_loss
            if is_best:
                best_val_loss = val_loss
                best_state = model.state_dict()

            marker = "*" if is_best else ""
            # Height is normalized to 0-1 in dataset, RMSE = sqrt(MSE), multiply by 200m to get actual height error
            rmse_meters = (val_mse ** 0.5) * 200.0
            log(f"  Cycle ep {epoch:2d}/{epochs}  train={train_loss:.4f}  val={val_loss:.4f}  "
                f"rmse={rmse_meters:.2f}m  iou={val_iou:.3f}  lr={current_lr:.1e} {marker}")

            history.append({
                "epoch": epoch,
                "train_loss": train_loss,
                "val_loss": val_loss,
                "val_mse": val_mse,
                "val_iou": val_iou,
                "lr": current_lr,
            })
        except Exception as e:
            log(f"ERROR in epoch {epoch}: {type(e).__name__}: {e}")
            import traceback
            log(traceback.format_exc())
            raise

    # Restore best state
    if best_state is not None:
        model.load_state_dict(best_state)

    return model, history, best_val_loss

# ────────────────────────────────────────────────────────────────────────────
# MAIN
# ────────────────────────────────────────────────────────────────────────────

def main():
    LOG_DIR.mkdir(exist_ok=True)

    # Setup Python logging for robust file I/O
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(message)s',
        handlers=[
            logging.FileHandler(LOG_FILE, mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logger = logging.getLogger()
    for handler in logger.handlers:
        handler.flush()

    log("="*70)
    log("PHASE H: ADAPTIVE TRAINING WITH FREQUENT OPTIMIZER RESETS")
    log("="*70)
    log(f"Warmstart: {WARMSTART_CHECKPOINT}")
    log(f"Tiles: {TILE_DIR}")
    log(f"Tile size: {TILE_SIZE}×{TILE_SIZE}, Crop: {CROP_SIZE}×{CROP_SIZE}")
    log(f"Cycles: {CYCLES} × {EPOCHS_PER_CYCLE} epochs (always-fresh optimizer)")
    log(f"LR: {LR} (cosine annealing to {LR*0.1})")
    log(f"Grow trigger: plateau {PLATEAU_PATIENCE}× with delta < {PLATEAU_THRESHOLD}")
    log(f"Target: <7.2m RMSE (5-10% improvement over Stage 4)")
    log("")

    # Load checkpoint
    log("Loading checkpoint and building model...")
    checkpoint = torch.load(WARMSTART_CHECKPOINT, map_location=DEVICE)

    # Handle checkpoint format: might have metadata wrapper
    if isinstance(checkpoint, dict):
        if "hidden_channels" in checkpoint:
            hidden_channels = checkpoint["hidden_channels"]
            state_dict = checkpoint.get("model_state_dict", checkpoint)
        elif "model_state_dict" in checkpoint:
            state_dict = checkpoint["model_state_dict"]
            hidden_channels = checkpoint.get("hidden_channels", None)
        else:
            # Assume it's just the state dict
            state_dict = checkpoint
            hidden_channels = None
    else:
        # It's a model object
        state_dict = checkpoint.state_dict()
        hidden_channels = None

    # If we don't have architecture, infer from state dict
    if hidden_channels is None:
        log("Inferring architecture from checkpoint...")
        # Find block dimensions by looking at layer names
        block_dims = {}
        for key in state_dict.keys():
            if key.startswith("blocks."):
                parts = key.split(".")
                if len(parts) >= 2:
                    block_idx = int(parts[1])
                    if block_idx not in block_dims:
                        block_dims[block_idx] = []

        # Fall back to default architecture for retna_phase_g_global
        hidden_channels = [6, 7, 6, 8, 7, 7, 7, 7, 9]

    log(f"Loaded architecture: {hidden_channels}")

    # Build datasets
    log("Loading tiles with crop augmentation...")
    tile_files = sorted(TILE_DIR.glob("*.npz"))
    log(f"Found {len(tile_files)} tiles")

    # Split: 85/15 train/val
    n_train = int(0.85 * len(tile_files))
    indices = list(range(len(tile_files)))
    np.random.seed(42)
    np.random.shuffle(indices)
    train_files = [tile_files[i] for i in indices[:n_train]]
    val_files = [tile_files[i] for i in indices[n_train:]]

    train_dataset = HeightTileDataset(
        train_files,
        tile_size=TILE_SIZE,
        crop_size=CROP_SIZE,
        augment=True,
        transform=build_height_transforms(tile_size=TILE_SIZE, augment=True),
    )
    val_dataset = HeightTileDataset(
        val_files,
        tile_size=TILE_SIZE,
        crop_size=CROP_SIZE,
        augment=False,
        transform=build_height_transforms(tile_size=TILE_SIZE, augment=False),
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=BATCH_SIZE,
        shuffle=True,
        num_workers=0,
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=BATCH_SIZE,
        shuffle=False,
        num_workers=0,
    )

    log(f"Train: {len(train_dataset)} samples, Val: {len(val_dataset)} samples")
    log("")

    # Initialize model
    model = Retna_V1(in_channels=3, out_classes=1, hidden_channels=hidden_channels)
    model.load_state_dict(state_dict)
    model.to(DEVICE)

    # Training loop
    log("="*70)
    log("STARTING PHASE H TRAINING")
    log("="*70)
    log("")

    history_all = []
    plateau_count = 0
    best_val_loss_global = float('inf')
    cycle_metrics = []

    for cycle in range(1, CYCLES + 1):
        try:
            log(f"\n--- Cycle {cycle}/{CYCLES} ---")
            log(f"Architecture: {hidden_channels}, Params: {sum(p.numel() for p in model.parameters()):,}")

            # Train cycle with fresh optimizer
            model, cycle_history, best_val_loss = train_cycle_h(
                model, train_loader, val_loader, DEVICE,
                epochs=EPOCHS_PER_CYCLE,
                lr=LR,
                weight_decay=WEIGHT_DECAY,
            )
        except Exception as e:
            log(f"ERROR in cycle {cycle}: {type(e).__name__}: {e}")
            import traceback
            log(traceback.format_exc())
            raise

        history_all.extend(cycle_history)
        cycle_rmse = (cycle_history[-1]["val_mse"] ** 0.5) * 200.0  # Convert normalized (0-1) RMSE to meters
        cycle_iou = cycle_history[-1]["val_iou"]
        delta_loss = best_val_loss_global - best_val_loss if best_val_loss_global != float('inf') else 0.0

        # Check for plateau
        if delta_loss < PLATEAU_THRESHOLD and cycle > 2:
            plateau_count += 1
            log(f"  >>> Plateau detected ({plateau_count}/{PLATEAU_PATIENCE}): delta={delta_loss:.4f} < {PLATEAU_THRESHOLD}")
        else:
            plateau_count = 0

        # Grow if plateau
        should_grow = plateau_count >= PLATEAU_PATIENCE
        if should_grow and cycle < CYCLES - 2:
            plateau_count = 0  # Reset patience
            # Compute block scores to find weakest
            scores = block_scores(model, val_loader, DEVICE)
            weakest_block = min(range(len(hidden_channels)), key=lambda i: scores[i])

            # Grow weakest block
            old_channels = hidden_channels.copy()
            hidden_channels[weakest_block] += GROW_CHANNELS
            for i in range(len(hidden_channels)):
                if i != weakest_block:
                    hidden_channels[i] += 1

            log(f"  GROW: block {weakest_block} +{GROW_CHANNELS}, others +1")
            log(f"    {old_channels} → {hidden_channels}")

            # Rebuild model
            model_new = Retna_V1(in_channels=3, out_classes=1, hidden_channels=hidden_channels)
            shape_aware_load(model_new, model.state_dict())
            if CROP_SIZE < TILE_SIZE:  # Only smart init if using crops (more data variability)
                clone_top_channels_into_new(model_new, old_channels, hidden_channels, jitter=0.02)
            model = model_new.to(DEVICE)

            log(f"    Rebuilt with {sum(p.numel() for p in model.parameters()):,} params")

            # GRADIENT FREEZING: force training on new neurons only for next cycle
            log(f"  FREEZE old neuron gradients for next cycle (encourage new neuron learning)")
            freeze_old_neuron_gradients(model, old_channels)

            # Next cycle will train with frozen old weights
            freeze_next_cycle = True
        else:
            freeze_next_cycle = False

        # If coming from a growth cycle, unfreeze after one cycle of new-neuron-only training
        if freeze_next_cycle and cycle > 1:
            unfreeze_all_gradients(model)
            log(f"  UNFREEZE: old neuron gradients now trainable again")

        # Periodic ablation
        if cycle % PRUNE_EVERY == 0 and cycle < CYCLES:
            log(f"  ABLATE: periodic pruning cycle")
            baseline_loss = best_val_loss
            pruned_channels, pruned_model = ablate_channels(
                model, val_loader, DEVICE,
                tolerance=ABLATION_TOLERANCE,
                floor_pct=25,
            )

            if pruned_channels != hidden_channels:
                log(f"    Pruned: {hidden_channels} → {pruned_channels}")
                model_compact = compact_pruned_channels(pruned_model, pruned_channels)
                model = model_compact.to(DEVICE)
                hidden_channels = pruned_channels

                log(f"    Compacted to {sum(p.numel() for p in model.parameters()):,} params")

        # Update global best
        if best_val_loss < best_val_loss_global:
            best_val_loss_global = best_val_loss
            best_checkpoint = model.state_dict()
            best_checkpoint_cycle = cycle
            best_checkpoint_rmse = cycle_rmse
            log(f"  NEW BEST: RMSE={cycle_rmse:.2f}m, IoU={cycle_iou:.3f}")

        action = "GROW" if should_grow else ("PRUNE" if cycle % PRUNE_EVERY == 0 else "STABLE")
        cycle_metrics.append({
            "cycle": cycle,
            "val_loss": best_val_loss,
            "rmse_m": cycle_rmse,
            "iou": cycle_iou,
            "delta_loss": delta_loss,
            "action": action,
            "architecture": hidden_channels.copy(),
        })

        log(f"  Summary: val_loss={best_val_loss:.4f}, rmse={cycle_rmse:.2f}m, "
            f"delta={delta_loss:.4f}, action={action}")

    # Final ablation
    log("")
    log("="*70)
    log("FINAL ABLATION PASS")
    log("="*70)
    model.load_state_dict(best_checkpoint)
    final_channels, final_model = ablate_channels(
        model, val_loader, DEVICE,
        tolerance=ABLATION_TOLERANCE * 2,
        floor_pct=25,
    )
    model = compact_pruned_channels(final_model, final_channels).to(DEVICE)
    hidden_channels = final_channels

    log(f"Final architecture: {hidden_channels}")
    log(f"Final parameters: {sum(p.numel() for p in model.parameters()):,}")

    # Save model
    torch.save(model.state_dict(), OUTPUT_MODEL)
    log(f"\nSaved model: {OUTPUT_MODEL}")

    # Final retrain (10 epochs at lower LR)
    log("\nFinal retraining (10 epochs at LR={:.1e})...".format(LR * 0.25))
    model, _, _ = train_cycle_h(
        model, train_loader, val_loader, DEVICE,
        epochs=10,
        lr=LR * 0.25,
        weight_decay=WEIGHT_DECAY,
    )

    # Save final
    torch.save(model.state_dict(), OUTPUT_MODEL)
    log(f"Final model saved: {OUTPUT_MODEL}")

    # Summary
    log("")
    log("="*70)
    log("PHASE H TRAINING COMPLETE")
    log("="*70)
    log(f"Total cycles: {CYCLES}")
    log(f"Total epochs: {CYCLES * EPOCHS_PER_CYCLE}")
    log(f"Best cycle: {best_checkpoint_cycle} (RMSE={best_checkpoint_rmse:.2f}m)")
    log(f"Final architecture: {hidden_channels}")
    log(f"Final parameters: {sum(p.numel() for p in model.parameters()):,}")
    log("")
    log("Cycle summary (last 5):")
    for m in cycle_metrics[-5:]:  # Last 5 cycles
        log(f"  Cycle {m['cycle']:2d}: rmse={m['rmse_m']:5.1f}m iou={m['iou']:.3f} "
            f"delta={m['delta_loss']:+.4f} action={m['action']}")

    log("")
    log("Next steps:")
    log(f"  python scripts/phase_g.py extract --checkpoint {OUTPUT_MODEL}")
    log(f"  python scripts/phase_g.py report")

    # ────────────────────────────────────────────────────────────────────────────
    # RUN COMPLETION MANIFEST (contract)
    # ────────────────────────────────────────────────────────────────────────────
    log("")
    log("="*70)
    log("RUN COMPLETION MANIFEST")
    log("="*70)

    improvement_pct = ((7.55 - best_checkpoint_rmse) / 7.55) * 100
    promotion_eligible = best_checkpoint_rmse < 7.2

    manifest = {
        "run_id": "phase_h_001",
        "script": "scripts/train_phase_h.py",
        "status": "COMPLETED",
        "start_time": str(datetime.now()),
        "completion_time": str(datetime.now()),
        "metrics": {
            "warmstart_rmse": 7.55,
            "final_rmse": float(best_checkpoint_rmse),
            "improvement_pct": float(improvement_pct),
            "final_iou": float(cycle_history[-1]["val_iou"]),
            "final_loss": float(best_val_loss_global),
        },
        "architecture": {
            "final": hidden_channels,
            "final_params": int(sum(p.numel() for p in model.parameters())),
            "best_cycle": int(best_checkpoint_cycle),
        },
        "training": {
            "total_cycles": CYCLES,
            "total_epochs": CYCLES * EPOCHS_PER_CYCLE,
            "batch_size": BATCH_SIZE,
            "crop_size": CROP_SIZE,
            "tile_size": TILE_SIZE,
        },
        "promotion": {
            "eligible": promotion_eligible,
            "target_rmse": 7.2,
            "achieved_rmse": float(best_checkpoint_rmse),
            "rationale": f"RMSE {best_checkpoint_rmse:.2f}m vs target 7.2m ({'PASS' if promotion_eligible else 'FAIL'})"
        },
        "output_model": str(OUTPUT_MODEL),
    }

    log(f"run_id: {manifest['run_id']}")
    log(f"status: {manifest['status']}")
    log(f"final_rmse: {manifest['metrics']['final_rmse']:.2f}m")
    log(f"improvement: {manifest['metrics']['improvement_pct']:.1f}% vs warmstart")
    log(f"final_architecture: {manifest['architecture']['final']}")
    log(f"final_params: {manifest['architecture']['final_params']:,}")
    log(f"promotion_eligible: {manifest['promotion']['eligible']}")
    log(f"output_model: {manifest['output_model']}")

    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except Exception as e:
        print(f"\n{'='*70}", flush=True)
        print(f"FATAL ERROR: {type(e).__name__}: {e}", flush=True)
        import traceback
        print(traceback.format_exc(), flush=True)
        print(f"{'='*70}", flush=True)
        # Try to log to file too
        try:
            log(f"FATAL ERROR: {type(e).__name__}: {e}")
            log(traceback.format_exc())
        except:
            pass
        sys.exit(1)
