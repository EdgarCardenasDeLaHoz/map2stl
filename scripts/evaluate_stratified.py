#!/usr/bin/env python3
"""
Stratified evaluation for Phase H and beyond.

Evaluates model performance across:
- Height bins: 0-5m, 5-20m, 20-100m, 100m+
- Regions: EU, US, Cartagena
- Combined: (region, height_bin) pairs

Outputs:
- Stratified MAE/RMSE/IoU per bin and region
- Statistical summary (mean, std, min, max)
- Tier A/B/C benchmark compliance
"""

import json
import numpy as np
import torch
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[1]

# Configuration
HEIGHT_BINS = [
    (0, 5, "Low (0-5m)"),
    (5, 20, "Medium (5-20m)"),
    (20, 100, "High (20-100m)"),
    (100, 300, "Very High (100m+)"),
]

REGIONS = {
    "EU": ["Amsterdam", "Barcelona", "Berlin", "Brussels", "Budapest", "Cologne",
           "Dresden", "Dublin", "Florence", "Frankfurt", "Geneva", "Hamburg",
           "Istanbul", "Krakow", "London", "Madrid", "Milan", "Munich",
           "Prague", "Rome", "Vienna", "Warsaw", "Zurich"],
    "US": ["SanFrancisco", "LosAngeles", "Seattle", "Denver", "Chicago",
           "NewYork", "Boston", "Washington", "Miami", "Atlanta"],
    "Cartagena": ["Cartagena"],
}

TIER_A_THRESHOLD = 7.0  # Best-of-best: <7.0m MAE
TIER_B_THRESHOLD = 7.5  # Cross-city generalization: <7.5m MAE
TIER_C_THRESHOLD = 8.0  # Regional slices: <8.0m MAE


def infer_region_from_tile(tile_name: str) -> str:
    """Infer region from tile file name."""
    for region, cities in REGIONS.items():
        for city in cities:
            if city.lower() in tile_name.lower():
                return region
    return "Unknown"


def stratify_metrics(predictions, ground_truth, tile_names):
    """
    Compute stratified metrics.

    Returns:
        dict: {
            "by_height_bin": {bin_name: {mae, rmse, iou, count, mean_height}},
            "by_region": {region: {mae, rmse, iou, count}},
            "by_region_and_bin": {region: {bin_name: {mae, rmse, iou, count}}},
            "overall": {mae, rmse, iou},
        }
    """
    preds_flat = predictions.flatten()
    truth_flat = ground_truth.flatten()

    # Overall metrics
    overall_mae = np.abs(preds_flat - truth_flat).mean()
    overall_rmse = np.sqrt(((preds_flat - truth_flat) ** 2).mean())
    overall_iou = compute_iou(predictions, ground_truth)

    by_height_bin = {}
    for min_h, max_h, bin_name in HEIGHT_BINS:
        mask = (truth_flat >= min_h) & (truth_flat < max_h)
        if mask.sum() > 0:
            bin_preds = preds_flat[mask]
            bin_truth = truth_flat[mask]
            mae = np.abs(bin_preds - bin_truth).mean()
            rmse = np.sqrt(((bin_preds - bin_truth) ** 2).mean())
            iou = compute_iou_masked(predictions, ground_truth, mask)
            by_height_bin[bin_name] = {
                "mae": float(mae),
                "rmse": float(rmse),
                "iou": float(iou),
                "count": int(mask.sum()),
                "mean_height": float(bin_truth.mean()),
            }

    by_region = defaultdict(lambda: {
        "mae": [], "rmse": [], "iou": [], "count": 0
    })

    by_region_and_bin = defaultdict(lambda: {
        bin_name: {
            "mae": [], "rmse": [], "iou": [], "count": 0
        } for _, _, bin_name in HEIGHT_BINS
    })

    # Process per-tile
    for i, tile_name in enumerate(tile_names):
        region = infer_region_from_tile(tile_name)
        if region not in by_region:
            by_region[region] = {"mae": [], "rmse": [], "iou": [], "count": 0}

        tile_pred = predictions[i] if len(predictions.shape) == 3 else predictions
        tile_truth = ground_truth[i] if len(ground_truth.shape) == 3 else ground_truth

        tile_mae = np.abs(tile_pred - tile_truth).mean()
        tile_rmse = np.sqrt(((tile_pred - tile_truth) ** 2).mean())
        tile_iou = compute_iou(tile_pred.reshape(1, *tile_pred.shape), tile_truth.reshape(1, *tile_truth.shape))

        by_region[region]["mae"].append(tile_mae)
        by_region[region]["rmse"].append(tile_rmse)
        by_region[region]["iou"].append(tile_iou)
        by_region[region]["count"] += 1

        # By region and bin
        for min_h, max_h, bin_name in HEIGHT_BINS:
            mask = (tile_truth >= min_h) & (tile_truth < max_h)
            if mask.sum() > 0:
                bin_preds = tile_pred[mask]
                bin_truth = tile_truth[mask]
                mae = np.abs(bin_preds - bin_truth).mean()
                rmse = np.sqrt(((bin_preds - bin_truth) ** 2).mean())

                by_region_and_bin[region][bin_name]["mae"].append(mae)
                by_region_and_bin[region][bin_name]["rmse"].append(rmse)
                by_region_and_bin[region][bin_name]["count"] += 1

    # Aggregate region metrics
    region_metrics = {}
    for region, metrics in by_region.items():
        if len(metrics["mae"]) > 0:
            region_metrics[region] = {
                "mae": float(np.mean(metrics["mae"])),
                "rmse": float(np.mean(metrics["rmse"])),
                "iou": float(np.mean(metrics["iou"])),
                "count": int(metrics["count"]),
            }

    # Aggregate region+bin metrics
    region_bin_metrics = {}
    for region, bin_dict in by_region_and_bin.items():
        region_bin_metrics[region] = {}
        for bin_name, metrics in bin_dict.items():
            if len(metrics["mae"]) > 0:
                region_bin_metrics[region][bin_name] = {
                    "mae": float(np.mean(metrics["mae"])),
                    "rmse": float(np.mean(metrics["rmse"])),
                    "count": int(metrics["count"]),
                }

    return {
        "by_height_bin": by_height_bin,
        "by_region": region_metrics,
        "by_region_and_bin": region_bin_metrics,
        "overall": {
            "mae": float(overall_mae),
            "rmse": float(overall_rmse),
            "iou": float(overall_iou),
        },
    }


def compute_iou(pred_binary, truth_binary, threshold=0.05):
    """Compute IoU for building presence."""
    pred_bin = (pred_binary > threshold).astype(float)
    truth_bin = (truth_binary > threshold).astype(float)
    intersection = (pred_bin * truth_bin).sum()
    union = (pred_bin + truth_bin - pred_bin * truth_bin).sum()
    return intersection / max(union, 1.0)


def compute_iou_masked(pred, truth, mask):
    """Compute IoU for masked region."""
    if isinstance(mask, np.ndarray):
        pred_masked = pred.flatten()[mask]
        truth_masked = truth.flatten()[mask]
    else:
        pred_masked = pred[mask]
        truth_masked = truth[mask]

    pred_bin = (pred_masked > 0.05).astype(float)
    truth_bin = (truth_masked > 0.05).astype(float)
    intersection = (pred_bin * truth_bin).sum()
    union = (pred_bin + truth_bin - pred_bin * truth_bin).sum()
    return intersection / max(union, 1.0)


def assess_tier_eligibility(metrics):
    """Determine benchmark tier eligibility."""
    overall_mae = metrics["overall"]["mae"]
    region_maes = [m["mae"] for m in metrics["by_region"].values()]

    tier_a_eligible = overall_mae < TIER_A_THRESHOLD
    tier_b_eligible = overall_mae < TIER_B_THRESHOLD and all(m < TIER_B_THRESHOLD for m in region_maes)
    tier_c_eligible = overall_mae < TIER_C_THRESHOLD

    return {
        "tier_a": tier_a_eligible,
        "tier_b": tier_b_eligible,
        "tier_c": tier_c_eligible,
        "thresholds": {
            "tier_a": TIER_A_THRESHOLD,
            "tier_b": TIER_B_THRESHOLD,
            "tier_c": TIER_C_THRESHOLD,
        },
    }


if __name__ == "__main__":
    print("Stratified evaluation framework loaded.")
    print(f"Height bins: {[b[2] for b in HEIGHT_BINS]}")
    print(f"Tier thresholds: A={TIER_A_THRESHOLD}m, B={TIER_B_THRESHOLD}m, C={TIER_C_THRESHOLD}m")
