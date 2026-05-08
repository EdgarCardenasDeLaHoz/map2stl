"""Re-collect training tiles at higher resolution.

Current: 256×256 px at 2 m/px = 512m coverage
Proposed: 512×512 px at 1 m/px = 512m coverage (same geographic area, 4x pixels)

Benefits:
  - Double pixel density per building
  - Better CNN feature extraction
  - Finer roof/height detail
  - Only 4x storage increase (manageable)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ml.data.collect_osm_tiles import collect_osm_tiles
from tools.ml.config import TRAIN_CITIES

REPO = Path(__file__).resolve().parents[1]

# Higher resolution collection parameters
TILE_SIZE = 512          # Pixels (was 256)
TARGET_RES_M = 1.0       # m/pixel (was 2.0) — double the detail
TILES_PER_CITY = 50      # Adjust as needed

def main():
    print("\n" + "=" * 70)
    print("RE-COLLECTING TILES AT HIGHER RESOLUTION")
    print("=" * 70)
    print()
    print("Current configuration:")
    print("  Tile size: 256×256 px at 2.0 m/px = 512m coverage")
    print()
    print("New configuration:")
    print(f"  Tile size: {TILE_SIZE}×{TILE_SIZE} px at {TARGET_RES_M} m/px = {TILE_SIZE * TARGET_RES_M}m coverage")
    print(f"  Benefit: Same geographic area, but {(TILE_SIZE / 256) ** 2:.0f}x more pixels per building")
    print(f"  Storage: ~{(TILE_SIZE / 256) ** 2:.0f}x per tile (~1MB instead of 250KB)")
    print()

    cities = list(TRAIN_CITIES.keys())
    eu_cities = [c for c in cities if c in ['Amsterdam', 'Barcelona', 'Berlin', 'Bruges', 'Cologne',
                                               'Florence', 'Munich', 'Paris', 'Prague', 'Rotterdam', 'Vienna']]
    us_cities = [c for c in cities if c in ['Philadelphia', 'Chicago', 'NYC', 'Boston']]

    print(f"Will collect from {len(eu_cities)} EU cities and {len(us_cities)} US cities")
    print()

    # Output directories
    eu_output = REPO / "cache" / "height_tiles_osm_hires"
    us_output = REPO / "cache" / "height_tiles_us_hires"

    eu_output.mkdir(parents=True, exist_ok=True)
    us_output.mkdir(parents=True, exist_ok=True)

    print("Collecting EU tiles...")
    eu_paths = collect_osm_tiles(
        city_names=eu_cities,
        tile_dir=eu_output,
        tile_size=TILE_SIZE,
        target_res_m=TARGET_RES_M,
        tiles_per_city=TILES_PER_CITY,
        label_source="osm",
        verbose=True,
    )
    print(f"\nEU: Collected {len(eu_paths)} tiles")
    print()

    print("Collecting US tiles...")
    us_paths = collect_osm_tiles(
        city_names=us_cities,
        tile_dir=us_output,
        tile_size=TILE_SIZE,
        target_res_m=TARGET_RES_M,
        tiles_per_city=TILES_PER_CITY,
        label_source="osm",
        verbose=True,
    )
    print(f"\nUS: Collected {len(us_paths)} tiles")
    print()

    print("=" * 70)
    print("HIGH-RES COLLECTION COMPLETE")
    print("=" * 70)
    print()
    print(f"EU tiles: {eu_output} ({len(eu_paths)} tiles)")
    print(f"US tiles: {us_output} ({len(us_paths)} tiles)")
    print()
    print("Next steps:")
    print("  1. Verify tile quality (spot check a few)")
    print("  2. Combine EU + US + Cartagena into global_hires:")
    print("     mkdir cache/height_tiles_global_hires")
    print("     cp cache/height_tiles_osm_hires/*.npz cache/height_tiles_global_hires/")
    print("     cp cache/height_tiles_us_hires/*.npz cache/height_tiles_global_hires/")
    print("     cp cache/height_tiles_osm_cartagena/*.npz cache/height_tiles_global_hires/")
    print("  3. Train Phase G on high-res tiles:")
    print("     python scripts/train_phase_g_hires.py")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
