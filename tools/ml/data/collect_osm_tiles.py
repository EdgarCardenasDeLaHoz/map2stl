"""
tools.ml.collect_osm_tiles -- Collect (RGB, height) training tiles using
OSM building heights as ground truth.

Why this approach:
    External height providers (WSF3D, GHSL, Copernicus, etc.) often 404 for
    European cities or require API keys.  OSM buildings have height tags
    (`height` directly, or `building:levels * 4m` fallback) for a substantial
    fraction of city buildings.  We rasterize these into a height-per-pixel
    label and pair with ESRI satellite RGB.

Usage:
    python -m tools.ml.collect_osm_tiles --cities Berlin --tiles-per-city 80
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
import base64
from io import BytesIO
from pathlib import Path

import numpy as np

# Path bootstrap so this can run as `python -m tools.ml.collect_osm_tiles`
_HERE = Path(__file__).resolve().parent
_STRM2STL = _HERE.parents[1]
for _p in (_STRM2STL, _STRM2STL.parent):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from tools.ml.config import TRAIN_CITIES, DEFAULT_TILE_SIZE  # noqa: E402

logger = logging.getLogger(__name__)


def _fetch_buildings_via_osmnx(
    north: float, south: float, east: float, west: float,
) -> dict:
    """Fetch OSM buildings directly via osmnx.  Returns a GeoJSON FeatureCollection
    with `height_m` filled from `height`, `building:levels`, or default 10 m."""
    import osmnx as ox

    ox_bbox = (west, south, east, north)
    gdf = ox.features_from_bbox(ox_bbox, tags={"building": True})
    gdf = gdf[gdf.geometry.geom_type.isin(["Polygon", "MultiPolygon"])].reset_index(drop=True)

    features: list[dict] = []
    for _, row in gdf.iterrows():
        try:
            geom = row.geometry.__geo_interface__
        except Exception:
            continue

        # Resolve height
        h = None
        for col in ("height", "building:height"):
            v = row.get(col, None) if col in gdf.columns else None
            if v and not (isinstance(v, float) and math.isnan(v)):
                try:
                    h = float(str(v).split()[0])
                    break
                except Exception:
                    pass
        if h is None and "building:levels" in gdf.columns:
            v = row.get("building:levels", None)
            if v and not (isinstance(v, float) and math.isnan(v)):
                try:
                    h = float(str(v).split()[0]) * 3.5  # avg storey height
                except Exception:
                    pass
        if h is None:
            h = 10.0  # default fallback

        h = max(3.0, min(300.0, h))  # clamp to plausible range

        features.append({
            "type": "Feature",
            "geometry": geom,
            "properties": {"height_m": h},
        })

    return {"type": "FeatureCollection", "features": features}


def _rasterize_buildings(
    buildings_geojson: dict,
    north: float, south: float, east: float, west: float,
    dim: int,
) -> np.ndarray:
    """Burn building footprints into a `dim x dim` height-per-pixel array (metres)."""
    from rasterio.transform import from_bounds
    from rasterio.features import rasterize as _rasterize
    from shapely.geometry import shape, mapping

    transform = from_bounds(west, south, east, north, dim, dim)
    grid = np.zeros((dim, dim), dtype=np.float32)

    for feat in buildings_geojson.get("features", []):
        geom = feat.get("geometry")
        if geom is None:
            continue
        h = float((feat.get("properties") or {}).get("height_m", 0.0))
        if h <= 0:
            continue
        try:
            s = shape(geom)
            if s.is_empty:
                continue
            mask = _rasterize([(mapping(s), 1.0)], out_shape=(dim, dim),
                              transform=transform, dtype="float32")
            np.maximum(grid, h * mask, out=grid)
        except Exception:
            continue

    return grid


def _fetch_satellite(
    north: float, south: float, east: float, west: float,
    dim: int,
) -> np.ndarray:
    """Fetch ESRI WMTS RGB tiles, return (3, dim, dim) float32 in [0, 1]."""
    from geo2stl.sat2stl import fetch_satellite_tiles
    from PIL import Image

    sat_b64 = fetch_satellite_tiles(north, south, east, west, dim=dim)
    img = Image.open(BytesIO(base64.b64decode(sat_b64))).convert("RGB")
    if img.size != (dim, dim):
        img = img.resize((dim, dim), Image.BILINEAR)
    rgb = np.asarray(img, dtype=np.float32) / 255.0
    return rgb.transpose(2, 0, 1)  # (3, H, W)


def _fetch_provider_label(
    north: float, south: float, east: float, west: float,
    dim: int,
    provider_names: list[str] | None = None,
) -> tuple[np.ndarray, dict]:
    """Fetch a per-pixel height label by merging trusted height providers.

    Used when OSM is sparse (Latin America, Africa, parts of Asia). Calls
    the same provider stack that runs in `/api/height/fetch` and returns the
    merged raster + a stats dict for logging.

    Returns
    -------
    (label, stats)
        label: (dim, dim) float32 â€” heights in metres. NaN where no provider
               had data; converted to 0.0 by the caller before saving so the
               loss masks (target > 0) keep ignoring those pixels.
        stats: dict with per-provider coverage + max so the collector can
               decide whether the tile is worth saving.
    """
    # Lazy imports â€” keeps this module importable on minimal environments
    from city2stl.skyline_cv.height import HeightResult, merge_height_rasters
    from city2stl.skyline_cv.height.providers.ndsm import NDSMProvider
    from city2stl.skyline_cv.height.providers.wsf3d import WSF3DProvider
    from city2stl.skyline_cv.height.providers.copernicus import CopernicusProvider
    from city2stl.skyline_cv.height.providers.lidar_3dep import LiDAR3DEPProvider
    from city2stl.skyline_cv.height.providers.ghsl import GHSLProvider
    from city2stl.skyline_cv.height.providers.open_buildings import OpenBuildingsProvider

    all_providers = {
        "lidar_3dep":     LiDAR3DEPProvider(),
        "ndsm":           NDSMProvider(),
        "copernicus":     CopernicusProvider(),
        "open_buildings": OpenBuildingsProvider(),
        "wsf3d":          WSF3DProvider(),
        "ghsl":           GHSLProvider(),
    }
    if provider_names:
        wanted = [all_providers[n] for n in provider_names if n in all_providers]
    else:
        wanted = list(all_providers.values())

    bbox = (north, south, east, west)
    target_dim = (dim, dim)

    results: list[HeightResult] = []
    stats: dict = {"providers": {}, "total_pixels": dim * dim}

    for p in wanted:
        if not p.covers(bbox):
            continue
        try:
            hr = p.fetch_heights(bbox, target_dim)
            valid = ~np.isnan(hr.raster)
            n_valid = int(valid.sum())
            stats["providers"][p.name] = {
                "coverage_pct": round(100.0 * n_valid / (dim * dim), 1),
                "max_m": float(np.nanmax(hr.raster)) if n_valid > 0 else None,
            }
            if n_valid > 0:
                results.append(hr)
        except Exception as e:
            logger.debug("provider %s fetch failed: %s", p.name, e)

    if not results:
        return np.full((dim, dim), np.nan, dtype=np.float32), stats

    # filter_outliers=False â€” for training labels we want to KEEP the
    # tall buildings (high-rises in Cartagena, Manhattan, etc.). The
    # default IQR clip would prune them as anomalies relative to the
    # neighbourhood's median, which is the opposite of what we want.
    merged = merge_height_rasters(results, target_shape=target_dim,
                                  filter_outliers=False)
    valid = ~np.isnan(merged.raster)
    stats["merged"] = {
        "coverage_pct": round(100.0 * valid.sum() / (dim * dim), 1),
        "max_m": float(np.nanmax(merged.raster)) if valid.any() else None,
        "min_m": float(np.nanmin(merged.raster)) if valid.any() else None,
        "mean_m": float(np.nanmean(merged.raster)) if valid.any() else None,
    }
    return merged.raster.astype(np.float32), stats


def collect_osm_tiles(
    city_names: list[str],
    tile_dir: Path,
    tile_size: int = DEFAULT_TILE_SIZE,
    target_res_m: float = 2.0,        # ~2 m/px so a 256-px tile = ~512 m square
    tiles_per_city: int = 80,
    min_height_pixels: int = 200,     # min non-zero pixels per tile (else skip)
    label_source: str = "osm",        # "osm" or "providers"
    provider_names: list[str] | None = None,
    verbose: bool = True,
) -> list[Path]:
    """Collect (RGB, height) tiles using OSM building heights as ground truth.

    Each city is gridded into tile_size * target_res_m squares.  For each cell:
      1. Fetch ESRI satellite RGB (uint8 -> float32 [0, 1])
      2. Fetch OSM buildings via osmnx
      3. Rasterize buildings into a height-per-pixel array
      4. Skip if fewer than `min_height_pixels` building pixels
      5. Save (rgb, height) as compressed .npz

    Returns the list of saved paths.
    """
    tile_dir.mkdir(parents=True, exist_ok=True)
    collected: list[Path] = []

    for city_name in city_names:
        spec = TRAIN_CITIES.get(city_name)
        if spec is None:
            if verbose:
                print(f"  Unknown city: {city_name}, skipping")
            continue

        north, south, east, west = spec.north, spec.south, spec.east, spec.west
        mid_lat = (north + south) / 2.0
        m_per_deg_lat = 111_320.0
        m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))

        tile_deg_lat = tile_size * target_res_m / m_per_deg_lat
        tile_deg_lon = tile_size * target_res_m / m_per_deg_lon

        rows = max(1, int((north - south) / tile_deg_lat))
        cols = max(1, int((east - west) / tile_deg_lon))
        total = rows * cols

        # Sample tiles evenly across the grid via 2D stride; use sqrt(total/target)
        # so the resulting positions cover both axes.
        if total > tiles_per_city:
            stride = max(1, int(math.sqrt(total / tiles_per_city)))
        else:
            stride = 1

        if verbose:
            print(f"\n  {city_name}: {rows}x{cols} grid -> {total} potential tiles "
                  f"(stride={stride}, target {tiles_per_city})")

        tile_count = 0
        skipped_no_buildings = 0
        skipped_low_coverage = 0
        skipped_fetch_fail = 0

        # Iterate the grid with stride on both axes
        positions: list[tuple[int, int]] = []
        for r in range(0, rows, stride):
            for c in range(0, cols, stride):
                positions.append((r, c))

        for r, c in positions:
            if tile_count >= tiles_per_city:
                break

            t_north = north - r * tile_deg_lat
            t_south = t_north - tile_deg_lat
            t_west = west + c * tile_deg_lon
            t_east = t_west + tile_deg_lon

            label_stats: dict = {}
            if label_source == "osm":
                try:
                    buildings = _fetch_buildings_via_osmnx(
                        t_north, t_south, t_east, t_west,
                    )
                except Exception as exc:
                    logger.debug("OSM fetch failed (%d,%d): %s", r, c, exc)
                    skipped_fetch_fail += 1
                    continue

                if not buildings.get("features"):
                    skipped_no_buildings += 1
                    continue

                height = _rasterize_buildings(
                    buildings, t_north, t_south, t_east, t_west, tile_size,
                )
            elif label_source == "providers":
                try:
                    height, label_stats = _fetch_provider_label(
                        t_north, t_south, t_east, t_west,
                        tile_size, provider_names,
                    )
                except Exception as exc:
                    logger.debug("provider fetch failed (%d,%d): %s", r, c, exc)
                    skipped_fetch_fail += 1
                    continue
                # Replace NaN with 0 â€” the loss masks (target > 0) ignore zero pixels
                height = np.where(np.isnan(height), 0.0, height).astype(np.float32)
            else:
                raise ValueError(f"unknown label_source: {label_source!r}")

            n_building_pixels = int((height > 0).sum())
            if n_building_pixels < min_height_pixels:
                skipped_low_coverage += 1
                continue

            try:
                rgb = _fetch_satellite(t_north, t_south, t_east, t_west, tile_size)
            except Exception as exc:
                logger.debug("Satellite fetch failed (%d,%d): %s", r, c, exc)
                skipped_fetch_fail += 1
                continue

            out_path = tile_dir / f"{city_name}_{r:04d}_{c:04d}.npz"
            np.savez_compressed(
                out_path,
                rgb=rgb,
                height=height[np.newaxis],
            )
            collected.append(out_path)
            tile_count += 1

            if verbose and tile_count % 5 == 0:
                pct_cov = 100 * n_building_pixels / (tile_size * tile_size)
                if label_source == "osm":
                    print(f"    {tile_count}/{tiles_per_city}  "
                          f"buildings={len(buildings['features'])}  "
                          f"coverage={pct_cov:.1f}%  "
                          f"hmax={height.max():.1f}m")
                else:
                    sources = ",".join(label_stats.get("providers", {}).keys())
                    print(f"    {tile_count}/{tiles_per_city}  "
                          f"sources=[{sources}]  "
                          f"coverage={pct_cov:.1f}%  "
                          f"hmax={height.max():.1f}m")

        if verbose:
            print(f"  Saved {tile_count} tiles for {city_name}  "
                  f"(skipped: {skipped_no_buildings} no-bldg, "
                  f"{skipped_low_coverage} low-cov, "
                  f"{skipped_fetch_fail} fetch-fail)")

    return collected


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Collect height tiles for training")
    p.add_argument("--cities", nargs="+", default=["Berlin"],
                   help="Cities (from tools.ml.config.TRAIN_CITIES)")
    p.add_argument("--tile-dir", type=Path, default=_STRM2STL / "cache" / "height_tiles_osm")
    p.add_argument("--tile-size", type=int, default=DEFAULT_TILE_SIZE)
    p.add_argument("--target-res-m", type=float, default=2.0)
    p.add_argument("--tiles-per-city", type=int, default=80)
    p.add_argument("--min-height-pixels", type=int, default=200)
    p.add_argument("--label-source", choices=["osm", "providers"], default="osm",
                   help="osm = rasterize OSM building tags; providers = merge "
                        "trusted height providers (WSF3D, OpenBuildings, etc.) "
                        "for regions where OSM tags are sparse")
    p.add_argument("--providers", nargs="+", default=None,
                   help="When --label-source=providers, restrict to these "
                        "provider names (default: all that cover the bbox)")
    p.add_argument("--quiet", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    paths = collect_osm_tiles(
        city_names=args.cities,
        tile_dir=args.tile_dir,
        tile_size=args.tile_size,
        target_res_m=args.target_res_m,
        tiles_per_city=args.tiles_per_city,
        min_height_pixels=args.min_height_pixels,
        label_source=args.label_source,
        provider_names=args.providers,
        verbose=not args.quiet,
    )
    print(f"\nTotal: {len(paths)} tiles -> {args.tile_dir}")
