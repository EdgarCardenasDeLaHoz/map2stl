"""Generate PDF with sample tiles from each geographic region."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

def main():
    """Generate inspection PDF sampling tiles from Amsterdam, Barcelona regions."""

    checkpoint = REPO / "models" / "retna_phase_g_test.pt"
    tiles_dir = REPO / "cache" / "height_tiles_combined"
    output_pdf = REPO / "output" / "phase_g_regional_sampling.pdf"

    if not checkpoint.exists():
        print(f"ERROR: Phase G test model not found: {checkpoint}")
        return 1

    if not tiles_dir.exists():
        print(f"ERROR: Tile directory not found: {tiles_dir}")
        return 1

    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    # Get all tiles sorted
    all_tiles = sorted(tiles_dir.glob("*.npz"))
    print(f"\nTotal tiles: {len(all_tiles)}")

    # Sample from different regions
    # Amsterdam: Amsterdam_* (indices 0-59)
    # Barcelona: Barcelona_* (indices 60-99)

    # Select 20 diverse tiles across all regions
    # Mix of different grid positions to get spatial diversity
    indices_to_sample = [
        0,   # Amsterdam_0000_0000 (best performer)
        5,   # Amsterdam_0000_0005
        10,  # Amsterdam_0001_0000
        15,  # Amsterdam_0001_0005
        20,  # Amsterdam_0001_0010
        25,  # Amsterdam_0002_0007 (problematic - clipped)
        30,  # Amsterdam_0002_0012
        35,  # Amsterdam_0003_0003
        40,  # Amsterdam_0004_0003 (good performer)
        45,  # Amsterdam_0004_0008
        50,  # Amsterdam_0005_0004 (problematic - clipped)
        55,  # Amsterdam_0005_0009
        60,  # Barcelona_0000_0000
        65,  # Barcelona_0000_0005
        70,  # Barcelona_0001_0000
        75,  # Barcelona_0001_0003 (good performer)
        80,  # Barcelona_0001_0008
        85,  # Barcelona_0002_0003 (problematic - clipped)
        92,  # Barcelona_0002_0005 (good performer)
        99,  # Barcelona_0002_0009 (last tile)
    ]

    print("\nSampling tiles from different regions:")
    for idx in indices_to_sample:
        if idx < len(all_tiles):
            print(f"  Index {idx:2d}: {all_tiles[idx].name}")

    print(f"\nOutput: {output_pdf}")
    print("Generating PDF with regional sampling...")

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
        "20",  # Show these 20 samples
        "--seed",
        "42",
    ]

    print(f"\n$ {' '.join(str(c) for c in cmd)}\n")
    rc = subprocess.run(cmd, cwd=str(REPO))

    if rc.returncode == 0:
        print(f"\n[OK] PDF complete: {output_pdf}")
        print(f"\nPDF shows 20-sample regional coverage:")
        print(f"  • Amsterdam grid: 10 tiles (0000-0005 rows/cols)")
        print(f"  • Barcelona grid: 10 tiles (0000-0002 rows/cols)")
        print(f"  • Mix of good/problematic performers to assess overall quality")
        print(f"\nOpen the PDF to visually inspect:")
        print(f"  • Satellite RGB quality across regions")
        print(f"  • Ground truth height label consistency")
        print(f"  • Per-pixel prediction errors")
        print(f"  • Height distribution per tile")

    return rc.returncode

if __name__ == "__main__":
    sys.exit(main())
