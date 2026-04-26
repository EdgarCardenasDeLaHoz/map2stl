"""
core/height/train -- Server-side training orchestration.

Pure training code lives in city2stl.height.train.  This module re-exports
all of those symbols for backward compatibility and retains collect_tiles()
which requires the server's height data providers (cache-backed).

Usage (standalone):
  python -m app.server.core.height.train \
      --cities Barcelona Granada \
      --epochs 50 \
      --output models/height_unet.pt

Usage (session):
  s.train_height_model(cities=["Barcelona"], epochs=50)
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from city2stl.height.train import (  # noqa: F401
    TILE_SIZE,
    TARGET_RES_M,
    MAX_NAN_FRAC,
    TileDataset,
    gradient_loss,
    combined_loss,
    CitySpec,
    _DEFAULT_CITIES,
    TrainConfig,
    train,
)

logger = logging.getLogger(__name__)


def collect_tiles(
    city_names: Sequence[str],
    tile_dir: Path,
    providers: Sequence[str] = ("ndsm", "wsf3d", "google3d"),
    tiles_per_city: int = 100,
    tile_size: int = TILE_SIZE,
    resolution_m: float = TARGET_RES_M,
    api_key: Optional[str] = None,
) -> List[Path]:
    """Collect (RGB, height) tile pairs from provider data for training.

    For each city, the bbox is subdivided into a grid.  For each grid cell:
      1. A satellite tile is fetched via the terrain session's satellite endpoint.
      2. A height raster is fetched from the listed providers and merged.
      3. Tiles with >MAX_NAN_FRAC NaN pixels are dropped.
      4. Valid pairs are saved to tile_dir as .npz files.

    Parameters
    ----------
    city_names    : City keys from _DEFAULT_CITIES.
    tile_dir      : Directory to write tile .npz files.
    providers     : Height provider names (see fetch_building_heights docs).
    tiles_per_city: Maximum tiles to collect per city.
    resolution_m  : Target metres per pixel.

    Returns
    -------
    List of Paths to collected .npz tile files.
    """
    from app.server.core.height import merge_height_rasters
    from app.server.core.height.providers.wsf3d import WSF3DProvider
    from app.server.core.height.providers.ndsm import NDSMProvider
    from app.server.core.height.providers.google_3d import Google3DProvider
    from app.server.core.height.providers.copernicus import CopernicusProvider
    from app.server.core.height.providers.lidar_3dep import LiDAR3DEPProvider
    from app.server.core.height.providers.ghsl import GHSLProvider
    from app.server.core.height.providers.shadow_height import ShadowHeightProvider

    _prov_map = {
        "wsf3d":       WSF3DProvider(),
        "ndsm":        NDSMProvider(),
        "google3d":    Google3DProvider(),
        "copernicus":  CopernicusProvider(),
        "lidar_3dep":  LiDAR3DEPProvider(),
        "ghsl":        GHSLProvider(),
        "shadow":      ShadowHeightProvider(),
    }

    active_providers = [_prov_map[p] for p in providers if p in _prov_map]
    tile_dir.mkdir(parents=True, exist_ok=True)

    collected: List[Path] = []

    for city_name in city_names:
        city = _DEFAULT_CITIES.get(city_name)
        if city is None:
            logger.warning("Unknown city %r - skipping", city_name)
            continue

        # Compute grid: tile footprint in degrees
        lat_span = city.north - city.south
        lon_span = city.east - city.west
        mid_lat = (city.north + city.south) / 2.0
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))

        tile_deg_lat = tile_size * resolution_m / m_per_deg_lat
        tile_deg_lon = tile_size * resolution_m / m_per_deg_lon

        rows = max(1, int(lat_span / tile_deg_lat))
        cols = max(1, int(lon_span / tile_deg_lon))
        total_tiles = rows * cols
        step = max(1, total_tiles // tiles_per_city)

        logger.info("%s: %d x %d grid -> %d tiles", city_name, rows, cols, total_tiles)
        tile_count = 0

        for r in range(0, rows, step):
            if tile_count >= tiles_per_city:
                break
            for c in range(0, cols, step):
                if tile_count >= tiles_per_city:
                    break

                north = city.north - r * tile_deg_lat
                south = north - tile_deg_lat
                west = city.west + c * tile_deg_lon
                east = west + tile_deg_lon
                bbox = (north, south, east, west)

                # Fetch height rasters
                results = []
                for prov in active_providers:
                    try:
                        if prov.covers(bbox):
                            hr = prov.fetch_heights(bbox, (tile_size, tile_size))
                            results.append(hr)
                    except Exception as exc:
                        logger.debug("Provider %s failed: %s", prov.name, exc)

                if not results:
                    continue

                merged = merge_height_rasters(results, (tile_size, tile_size))
                nan_frac = float(np.isnan(merged.raster).mean())
                if nan_frac > MAX_NAN_FRAC:
                    logger.debug("Dropping tile (%.0f%% NaN)", nan_frac * 100)
                    continue

                # Placeholder RGB: zeros until satellite fetch is wired.
                # Replace NaN in height with 0 for training.
                height_filled = np.where(
                    np.isnan(merged.raster), 0.0, merged.raster
                ).astype(np.float32)
                rgb_placeholder = np.zeros((3, tile_size, tile_size), dtype=np.float32)

                out_path = tile_dir / f"{city_name}_{r:04d}_{c:04d}.npz"
                np.savez_compressed(
                    out_path,
                    rgb=rgb_placeholder,
                    height=height_filled[np.newaxis],
                )
                collected.append(out_path)
                tile_count += 1

        logger.info("Collected %d tiles for %s", tile_count, city_name)

    return collected


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Train U-Net height predictor")
    ap.add_argument("--cities", nargs="+", default=["Barcelona"])
    ap.add_argument("--epochs", type=int, default=50)
    ap.add_argument("--batch-size", type=int, default=8)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--device", default="cpu")
    ap.add_argument("--tile-dir", type=Path, default=Path("cache/height_tiles"))
    ap.add_argument(
        "--output", type=Path,
        default=Path(__file__).resolve().parents[5] / "models" / "height_unet.pt"
    )
    ap.add_argument("--tiles-per-city", type=int, default=100)
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    print(f"Collecting tiles for: {args.cities}")
    paths = collect_tiles(
        args.cities,
        tile_dir=args.tile_dir,
        tiles_per_city=args.tiles_per_city,
    )
    print(f"Collected {len(paths)} tiles -> training")

    cfg = TrainConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        device=args.device,
    )
    result = train(paths, args.output, cfg)
    print(
        f"Training complete.  Best val loss: {result['best_val_loss']:.4f}  "
        f"Checkpoint: {result['checkpoint']}"
    )
