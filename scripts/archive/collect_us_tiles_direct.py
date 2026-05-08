"""Collect US city tiles using direct OSM data fetching."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ml.data.collect_osm_tiles import collect_and_save

REPO = Path(__file__).resolve().parents[1]

# US cities to collect (name, lat, lon, tile count)
US_CITIES = [
    ("Philadelphia", 39.9526, -75.1652, 50),
    ("Chicago", 41.8781, -87.6298, 50),
    ("New York City", 40.7128, -74.0060, 60),
    ("Boston", 42.3601, -71.0589, 40),
    ("Los Angeles", 34.0522, -118.2437, 40),
    ("Seattle", 47.6062, -122.3321, 40),
]

def main():
    print("\n" + "=" * 70)
    print("COLLECTING US CITY TILES FOR HEIGHT TRAINING")
    print("=" * 70)
    print("\nUS Cities to collect:")
    total_tiles = 0
    for city, lat, lon, count in US_CITIES:
        print(f"  • {city}: {count} tiles")
        total_tiles += count
    print(f"\nTotal tiles to collect: {total_tiles}")
    print()

    output_dir = REPO / "cache" / "height_tiles_us"
    output_dir.mkdir(parents=True, exist_ok=True)

    total_collected = 0

    for city, lat, lon, tiles_per_city in US_CITIES:
        print(f"\n{'='*70}")
        print(f"Collecting {tiles_per_city} tiles from {city}")
        print(f"{'='*70}")

        try:
            n_collected = collect_and_save(
                city_name=city,
                output_dir=str(output_dir),
                tiles_per_city=tiles_per_city,
                tile_size=128,
            )
            print(f"Successfully collected {n_collected} tiles from {city}")
            total_collected += n_collected
        except Exception as e:
            print(f"WARNING: Collection for {city} failed: {e}")
            print("Continuing with next city...")
            continue

    # Verify collection
    print(f"\n{'='*70}")
    print("VERIFICATION")
    print(f"{'='*70}")
    collected = list(output_dir.glob("*.npz"))
    print(f"\nTotal tiles collected: {len(collected)} (expected: {total_tiles})")
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
    print(f"  1. Combine US tiles with OSM European tiles:")
    print(f"     mkdir cache/height_tiles_global")
    print(f"     cp cache/height_tiles_osm/*.npz cache/height_tiles_global/")
    print(f"     cp cache/height_tiles_us/*.npz cache/height_tiles_global/")
    print(f"     cp cache/height_tiles_osm_cartagena/*.npz cache/height_tiles_global/")
    print(f"  2. Retrain Phase G on global dataset:")
    print(f"     python scripts/train_phase_g_global_dataset.py")
    print()

    return 0 if len(collected) > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
