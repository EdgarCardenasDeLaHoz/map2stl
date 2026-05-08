"""Compare Phase G test model vs retna_pruned baseline on same tiles."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]


def load_metrics(json_path: Path) -> dict:
    """Load metrics JSON from extract script."""
    with open(json_path) as f:
        return json.load(f)


def main():
    phase_g_metrics = REPO / "output" / "phase_g_tile_metrics.json"
    baseline_metrics = REPO / "output" / "retna_pruned_tile_metrics.json"

    if not phase_g_metrics.exists():
        print("ERROR: Phase G metrics not found. Run first:")
        print("  python scripts/extract_phase_g_tile_metrics.py")
        return 1

    if not baseline_metrics.exists():
        print("ERROR: Baseline metrics not found. Run first:")
        print("  python scripts/extract_phase_g_tile_metrics.py --model models/retna_pruned.pt")
        return 1

    phase_g = load_metrics(phase_g_metrics)
    baseline = load_metrics(baseline_metrics)

    print("\n" + "=" * 80)
    print("PHASE G vs RETNA_PRUNED BASELINE — PER-TILE COMPARISON")
    print("=" * 80)

    # Overall summary
    pg_summary = phase_g["metrics_summary"]
    bs_summary = baseline["metrics_summary"]

    print("\nOVERALL METRICS:")
    print(f"{'Metric':<20} {'Phase G':<15} {'Baseline':<15} {'Delta':<15}")
    print("-" * 65)

    for key in ["mean_mae", "median_mae", "std_mae", "min_mae", "max_mae"]:
        pg_val = pg_summary[key]
        bs_val = bs_summary[key]
        delta = pg_val - bs_val
        delta_pct = (delta / bs_val * 100) if bs_val != 0 else 0
        print(
            f"{key:<20} {pg_val:>6.2f}m       {bs_val:>6.2f}m       "
            f"{delta:>+6.2f}m ({delta_pct:>+5.1f}%)"
        )

    # Per-tile comparison
    print("\n\nPER-TILE BREAKDOWN:")
    print(
        f"{'Tile':<6} {'Phase G MAE':<15} {'Baseline MAE':<15} "
        f"{'Delta':<15} {'Verdict':<20}"
    )
    print("-" * 80)

    pg_tiles = {t["tile_idx"]: t for t in phase_g["per_tile"]}
    bs_tiles = {t["tile_idx"]: t for t in baseline["per_tile"]}

    # Sort by Phase G performance
    sorted_indices = sorted(pg_tiles.keys(), key=lambda i: pg_tiles[i]["mae"])

    for tile_idx in sorted_indices:
        pg_tile = pg_tiles[tile_idx]
        bs_tile = bs_tiles.get(tile_idx)

        if not bs_tile:
            print(f"{tile_idx:<6} {pg_tile['mae']:>6.2f}m         (no baseline)")
            continue

        pg_mae = pg_tile["mae"]
        bs_mae = bs_tile["mae"]
        delta = pg_mae - bs_mae
        delta_pct = (delta / bs_mae * 100) if bs_mae != 0 else 0

        # Classify
        if delta < -1:
            verdict = "IMPROVED"
        elif delta > 1:
            if delta > 5:
                verdict = "DEGRADED (bad)"
            else:
                verdict = "DEGRADED"
        else:
            verdict = "SIMILAR"

        print(
            f"{tile_idx:<6} {pg_mae:>6.2f}m       {bs_mae:>6.2f}m       "
            f"{delta:>+6.2f}m ({delta_pct:>+5.1f}%) {verdict:<20}"
        )

    # Highlight problem tiles
    print("\n\nPROBLEM TILES (Phase G much worse than baseline):")
    print("-" * 80)
    problems = []
    for tile_idx in sorted_indices:
        pg_tile = pg_tiles[tile_idx]
        bs_tile = bs_tiles.get(tile_idx)
        if bs_tile and (pg_tile["mae"] - bs_tile["mae"]) > 3:
            problems.append((tile_idx, pg_tile, bs_tile))

    if not problems:
        print("None found — Phase G performs similarly to baseline on all tiles.")
    else:
        for tile_idx, pg_tile, bs_tile in problems:
            print(
                f"\nTile {tile_idx}: Phase G {pg_tile['mae']:.2f}m vs Baseline "
                f"{bs_tile['mae']:.2f}m (+{pg_tile['mae'] - bs_tile['mae']:.2f}m worse)"
            )
            print(
                f"  Phase G:  target_std={pg_tile.get('target_std', 0):.1f}m, "
                f"R={pg_tile.get('r', 0):+.3f}"
            )
            print(
                f"  Baseline: target_std={bs_tile.get('target_std', 0):.1f}m, "
                f"R={bs_tile.get('r', 0):+.3f}"
            )

    print("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
