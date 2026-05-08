"""Analyze which tiles Phase G test model performed well/poorly on."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def main():
    """Generate inspection PDF showing per-tile performance for Phase G model."""

    checkpoint = REPO / "models" / "retna_phase_g_test.pt"
    tiles_dir = REPO / "cache" / "height_tiles_combined"
    output_pdf = REPO / "output" / "phase_g_tile_analysis.pdf"

    if not checkpoint.exists():
        print(f"ERROR: Phase G test model not found: {checkpoint}")
        print("\nTo generate it, run first:")
        print("  python scripts/train_phase_g_test_single_cycle.py")
        return 1

    if not tiles_dir.exists():
        print(f"ERROR: Tile directory not found: {tiles_dir}")
        return 1

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 70)
    print("PHASE G TILE ANALYSIS")
    print("=" * 70)
    print(f"Model:  {checkpoint}")
    print(f"Tiles:  {tiles_dir} ({len(list(tiles_dir.glob('*.npz')))} tiles)")
    print(f"Output: {output_pdf}")
    print()
    print("This will generate a PDF showing:")
    print("  • Per-tile MAE distribution histogram")
    print("  • Best/worst/median performing tiles (RGB + GT + Pred + Error)")
    print("  • Per-block contribution and weight analysis")
    print()

    cmd = [
        sys.executable,
        "-u",
        "-m",
        "tools.ml.analysis.inspect_retna",
        "--checkpoint",
        str(checkpoint.resolve()),
        "--tiles",
        str(tiles_dir.resolve()),
        "--out",
        str(output_pdf.resolve()),
        "--n-samples",
        "30",
    ]

    print(f"$ {' '.join(str(c) for c in cmd)}\n")
    rc = subprocess.run(cmd, cwd=str(REPO))

    if rc.returncode == 0:
        print(f"\n[OK] Analysis complete: {output_pdf}")
        print(f"\nTo inspect tile performance:")
        print(f"  • Open the PDF: {output_pdf}")
        print(f"  • Page 1: Overall MAE distribution + per-block analysis")
        print(f"  • Pages 2+: Individual tiles sorted by MAE (best to worst)")
        print(f"\nTo extract detailed metrics programmatically:")
        print(f"  python scripts/extract_phase_g_tile_metrics.py")

    return rc.returncode

if __name__ == "__main__":
    sys.exit(main())
