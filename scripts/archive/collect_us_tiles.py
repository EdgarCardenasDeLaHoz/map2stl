"""Collect US city tiles using OSM building heights."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# US cities to collect
US_CITIES = [
    ("Philadelphia", 50),
    ("Chicago", 50),
    ("New York City", 60),
    ("Boston", 40),
    ("Los Angeles", 40),
    ("Seattle", 40),
]

def main():
    print("\n" + "=" * 70)
    print("COLLECTING US CITY TILES FOR HEIGHT TRAINING")
    print("=" * 70)
    print("\nUS Cities to collect:")
    total_tiles = 0
    for city, count in US_CITIES:
        print(f"  • {city}: {count} tiles")
        total_tiles += count
    print(f"\nTotal tiles to collect: {total_tiles}")
    print()

    output_dir = REPO / "cache" / "height_tiles_us"
    output_dir.mkdir(parents=True, exist_ok=True)

    for city, tiles_per_city in US_CITIES:
        print(f"\n{'='*70}")
        print(f"Collecting {tiles_per_city} tiles from {city}")
        print(f"{'='*70}")

        cmd = [
            sys.executable,
            "-u",
            "-m",
            "tools.ml.collect_osm_tiles",
            "--cities",
            city,
            "--tiles-per-city",
            str(tiles_per_city),
            "--output-dir",
            str(output_dir.resolve()),
        ]

        print(f"\n$ {' '.join(str(c) for c in cmd)}\n")
        rc = subprocess.run(cmd, cwd=str(REPO))

        if rc.returncode != 0:
            print(f"\nWARNING: Collection for {city} failed (exit {rc.returncode})")
            print("Continuing with next city...")
            continue

        print(f"\nSuccessfully collected {tiles_per_city} tiles from {city}")

    # Verify collection
    print(f"\n{'='*70}")
    print("VERIFICATION")
    print(f"{'='*70}")
    collected = list(output_dir.glob("*.npz"))
    print(f"\nTotal tiles collected: {len(collected)}")
    if len(collected) > 0:
        print(f"Output directory: {output_dir}")
        print(f"Sample tiles:")
        for tile in sorted(collected)[:5]:
            print(f"  • {tile.name}")
        if len(collected) > 5:
            print(f"  ... and {len(collected) - 5} more")
    else:
        print("WARNING: No tiles collected!")

    print("\nNext steps:")
    print(f"  1. Review tiles visually for quality:")
    print(f"     python scripts/tile_review.py render --tiles-dir {output_dir}")
    print(f"  2. Combine US tiles with OSM European tiles:")
    print(f"     mkdir cache/height_tiles_osm_us")
    print(f"     cp cache/height_tiles_osm/*.npz cache/height_tiles_osm_us/")
    print(f"     cp cache/height_tiles_us/*.npz cache/height_tiles_osm_us/")
    print(f"  3. Retrain Phase G on combined dataset with Cartagena for segmentation:")
    print(f"     python scripts/train_phase_g_global_dataset.py")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
