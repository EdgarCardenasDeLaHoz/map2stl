"""Collect US city tiles using OSM building heights."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ml.data.collect_osm_tiles import collect_osm_tiles

REPO = Path(__file__).resolve().parents[1]

# US cities already defined in tools.ml.config
US_CITIES = [
    "Philadelphia",
    "Chicago",
    "NYC",
    "Boston",
]

def main():
    print("\n" + "=" * 70)
    print("COLLECTING US CITY TILES FOR HEIGHT TRAINING")
    print("=" * 70)
    print("\nUS Cities to collect:")
    for city in US_CITIES:
        print(f"  • {city}: 50 tiles")
    print(f"\nTotal tiles to collect: {len(US_CITIES) * 50} = 200 tiles")
    print()

    output_dir = REPO / "cache" / "height_tiles_us"
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Output directory: {output_dir}\n")

    collected_paths = collect_osm_tiles(
        city_names=US_CITIES,
        tile_dir=output_dir,
        tile_size=256,          # Match OSM dataset (256px)
        target_res_m=2.0,       # ~2 m/px
        tiles_per_city=50,      # 50 tiles per city
        label_source="osm",     # Use OSM building heights
        verbose=True,
    )

    print(f"\n{'='*70}")
    print("COLLECTION COMPLETE")
    print(f"{'='*70}")
    print(f"\nTotal tiles collected: {len(collected_paths)}")
    if len(collected_paths) > 0:
        print(f"\nSample tiles:")
        for tile_path in sorted(collected_paths)[:5]:
            print(f"  • {tile_path.name}")
        if len(collected_paths) > 5:
            print(f"  ... and {len(collected_paths) - 5} more")
    else:
        print("WARNING: No tiles collected!")

    print("\nNext steps:")
    print(f"  1. Combine US tiles with OSM European tiles:")
    print(f"     mkdir cache/height_tiles_global")
    print(f"     cp cache/height_tiles_osm/*.npz cache/height_tiles_global/")
    print(f"     cp cache/height_tiles_us/*.npz cache/height_tiles_global/")
    print(f"     cp cache/height_tiles_osm_cartagena/*.npz cache/height_tiles_global/")
    print(f"  2. Train Phase G on global dataset:")
    print(f"     python scripts/train_phase_g_global_dataset.py")
    print()

    return 0 if len(collected_paths) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
