"""geo2stl/hydrology.py — Hydrology data fetching and rasterization.

Provides Natural Earth rivers, lakes, and coastlines for multi-scale hydrology rendering,
and HydroRIVERS-based high-detail regional river rasterization.
Includes adaptive buffering to prevent thin-feature aliasing during downsampling.

Source choice (do not re-litigate — see docs/reference/layer-system.md "Hydrology Layer"):
HydroRIVERS is the default. It (1) buffers thin features to ≥1 pixel before rasterization
so tributaries survive downsampling, and (2) carries Strahler order so depression depth
scales with river size. Natural Earth has neither and is only a fallback for regions
HydroRIVERS does not cover or before the regional shapefile has been downloaded.
"""

from __future__ import annotations

import io
import json
import logging
import zipfile
from pathlib import Path

import numpy as np
from numpy2stl.raster import burn_polygons

from geo2stl.geo import M_PER_DEG_LAT, M_PER_DEG_LON_EQ

logger = logging.getLogger(__name__)


class HydrologyLayerBase:
    """Base interface for hydrology providers."""

    name: str = "hydrology-provider"

    def fetch_and_rasterize(
        self,
        north,
        south,
        east,
        west,
        dim,
        scale_m,
        depression_m,
        min_order=3,
        order_exponent=1.5,
    ):
        raise NotImplementedError()


def fetch_natural_earth_rivers(scale_m: int = 10) -> dict | None:
    """
    Fetch Natural Earth rivers dataset as GeoJSON.

    Args:
        scale_m: 10, 50, or 110 (1:10M, 1:50M, 1:110M)

    Returns:
        Dict with 'type'='FeatureCollection' and 'features' list, or None if failed
    """
    try:
        import geopandas as gpd
        import requests
    except ImportError:
        logger.warning(
            "geopandas or requests not installed for hydrology fetch")
        return None

    url = f"https://naciscdn.org/naturalearth/{scale_m}m/physical/ne_{scale_m}m_rivers_lake_centerlines.zip"

    try:
        logger.info(f"Fetching Natural Earth {scale_m}m rivers from {url}")
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()

        with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
            shp_files = [f for f in z.namelist() if f.endswith('.shp')]
            if not shp_files:
                logger.warning("No .shp file in Natural Earth archive")
                return None

            import os
            import tempfile
            with tempfile.TemporaryDirectory() as tmpdir:
                z.extractall(tmpdir)
                shp_path = os.path.join(tmpdir, shp_files[0])
                gdf = gpd.read_file(shp_path)

                # Convert to GeoJSON
                geojson = json.loads(gdf.to_json())
                logger.info(
                    f"Fetched {len(gdf)} river features from Natural Earth")
                return geojson

    except Exception as e:
        logger.error(f"Natural Earth rivers fetch failed: {e}")
        return None


def filter_rivers_by_bbox(geojson: dict, bbox: tuple[float, float, float, float]) -> dict:
    """
    Filter GeoJSON features to a bounding box.

    Args:
        geojson: GeoJSON FeatureCollection
        bbox: (west, south, east, north)

    Returns:
        Filtered GeoJSON FeatureCollection
    """
    west, south, east, north = bbox

    filtered_features = []
    for feature in geojson.get('features', []):
        coords = feature.get('geometry', {}).get('coordinates')
        if not coords:
            continue

        # Simple bounds check for LineString coordinates
        try:
            if feature['geometry']['type'] == 'LineString':
                lons = [c[0] for c in coords]
                lats = [c[1] for c in coords]
                if (max(lons) >= west and min(lons) <= east and
                        max(lats) >= south and min(lats) <= north):
                    filtered_features.append(feature)
            elif feature['geometry']['type'] == 'MultiLineString':
                for line_coords in coords:
                    lons = [c[0] for c in line_coords]
                    lats = [c[1] for c in line_coords]
                    if (max(lons) >= west and min(lons) <= east and
                            max(lats) >= south and min(lats) <= north):
                        filtered_features.append(feature)
                        break
        except (KeyError, TypeError):
            continue

    return {
        'type': 'FeatureCollection',
        'features': filtered_features
    }


def rasterize_rivers_with_buffering(
    geojson: dict,
    bbox: tuple[float, float, float, float],
    dim: int,
    depression_m: float = -3.0,
) -> np.ndarray:
    """
    Rasterize river geometries with adaptive buffering to prevent aliasing.

    Args:
        geojson: GeoJSON FeatureCollection with LineString/MultiLineString features
        bbox: (west, south, east, north)
        dim: Output grid resolution (pixels per side)
        depression_m: Elevation depression for rivers (negative = downward)

    Returns:
        Float32 array of shape (dim, dim) with river elevation values
    """
    try:
        from shapely.geometry import shape
    except ImportError:
        logger.warning("shapely not installed for hydrology rasterization")
        return np.zeros((dim, dim), dtype=np.float32)

    west, south, east, north = bbox

    # Calculate pixel size in metres (approximate)
    pixel_size_lon_m = (east - west) * M_PER_DEG_LON_EQ / dim
    pixel_size_lat_m = (north - south) * M_PER_DEG_LAT / dim
    pixel_size_m = (pixel_size_lon_m + pixel_size_lat_m) / 2.0

    # Rivers must be at least 2 pixels wide to avoid aliasing
    min_buffer_m = pixel_size_m * 2

    logger.info(
        f"Rasterizing rivers: pixel size {pixel_size_m:.0f} m, min buffer {min_buffer_m:.0f} m")

    # Convert to degrees for buffering (approximate; best approach is UTM reprojection)
    min_buffer_deg = min_buffer_m / M_PER_DEG_LAT

    shapes = []
    for feature in geojson.get('features', []):
        try:
            geom = shape(feature['geometry'])

            if geom.geom_type in ('LineString', 'MultiLineString'):
                # Buffer to ensure minimum width
                shapes.append(geom.buffer(min_buffer_deg))
        except Exception as e:
            logger.debug(f"Skipping river feature: {e}")
            continue

    if not shapes:
        logger.warning("No river features to rasterize")
        return np.zeros((dim, dim), dtype=np.float32)

    # Every river carries the same depression, so overlaps simply keep it ("set").
    try:
        river_grid = burn_polygons(shapes, (dim, dim), bounds=(west, south, east, north),
                                   values=depression_m, mode="set", dtype=np.float32)

        logger.info(
            f"Rasterized {len(shapes)} river features to {dim}x{dim} grid")
        return river_grid
    except Exception as e:
        logger.error(f"Rasterization failed: {e}")
        return np.zeros((dim, dim), dtype=np.float32)


class NaturalEarthHydrologyLayer(HydrologyLayerBase):
    """Natural Earth provider for global medium/coarse river coverage."""

    name = "natural_earth"

    def fetch_and_rasterize(
        self,
        north,
        south,
        east,
        west,
        dim,
        scale_m,
        depression_m,
        min_order=3,
        order_exponent=1.5,
    ):
        geojson = fetch_natural_earth_rivers(scale_m=scale_m)
        if geojson is None:
            logger.warning("Natural Earth hydrology fetch failed")
            return None

        bbox_tuple = (west, south, east, north)
        geojson_filtered = filter_rivers_by_bbox(geojson, bbox_tuple)
        n_features = len(geojson_filtered.get("features", []))
        if n_features == 0:
            logger.info("No Natural Earth rivers found in region")
            return None

        river_grid = rasterize_rivers_with_buffering(
            geojson_filtered, bbox_tuple, dim, depression_m=depression_m
        )
        return {
            "river_grid": river_grid,
            "feature_count": n_features,
            "source": self.name,
        }


# ---------------------------------------------------------------------------
# HydroRIVERS support (merged from hydrorivers.py)
# ---------------------------------------------------------------------------

# Cache root resolved from this file's location — no server dependency.
# geo2stl/hydrology.py → geo2stl/ → map2stl/
_MAP2STL_ROOT = Path(__file__).parent.parent


def _collinear_point_reduction(coords: list, tolerance: float = 1e-4) -> list:
    """
    Remove collinear points from a coordinate list.

    A point is collinear if it lies on the line between its neighbors.
    This significantly reduces geometry complexity without losing visual detail.

    Parameters
    ----------
    coords : list of (x, y) tuples
        LineString coordinates
    tolerance : float
        Numerical tolerance for collinearity check (default 1e-4, ~11 m at equator)

    Returns
    -------
    list
        Simplified coordinates with collinear points removed
    """
    if len(coords) <= 2:
        return coords

    simplified = [coords[0]]

    for i in range(1, len(coords) - 1):
        p0 = coords[i - 1]
        p1 = coords[i]
        p2 = coords[i + 1]

        # Cross product to determine collinearity; ≈ 0 means collinear.
        cross = (p1[0] - p0[0]) * (p2[1] - p0[1]) - \
            (p1[1] - p0[1]) * (p2[0] - p0[0])

        if abs(cross) > tolerance:
            simplified.append(p1)

    simplified.append(coords[-1])
    return simplified


def _simplify_geometry(geom):
    """Simplify a Shapely geometry by removing collinear points.

    Parameters
    ----------
    geom : shapely.geometry
        LineString or MultiLineString

    Returns
    -------
    shapely.geometry
        Simplified geometry
    """
    try:
        from shapely.geometry import LineString, MultiLineString
    except ImportError:
        return geom

    if geom.geom_type == "LineString":
        simplified_coords = _collinear_point_reduction(list(geom.coords))
        return LineString(simplified_coords) if len(simplified_coords) >= 2 else geom

    elif geom.geom_type == "MultiLineString":
        simplified_lines = []
        for line in geom.geoms:
            simplified_coords = _collinear_point_reduction(list(line.coords))
            if len(simplified_coords) >= 2:
                simplified_lines.append(LineString(simplified_coords))
        return MultiLineString(simplified_lines) if simplified_lines else geom

    return geom


# ---------------------------------------------------------------------------
# HydroRIVERS regional download URLs (public S3, no auth required)
# Standard resolution shapefiles (~500 m); compressed sizes given for info.
# ---------------------------------------------------------------------------

_REGION_URLS: dict[str, str] = {
    "af": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_af_shp.zip",   # Africa
    "ar": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_ar_shp.zip",   # Arctic
    "as": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_as_shp.zip",   # Asia
    "au": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_au_shp.zip",   # Australia
    "eu": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_eu_shp.zip",   # Europe
    # North America
    "na": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_na_shp.zip",
    # South America
    "sa": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_sa_shp.zip",
    "si": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_si_shp.zip",   # Siberia
}

# Coarse region bounding boxes: (west, south, east, north)
# Note: HydroRIVERS regions overlap by design; pyogrio bbox parameter clips at read time.
_REGION_BBOX: dict[str, tuple[float, float, float, float]] = {
    "af": (-20,  -35,  55,  38),
    "ar": (-180,  60, 180,  90),
    "as": (57,  -5, 180,  60),
    "au": (112, -48, 180,  -5),
    "eu": (-25,   35,  65,  72),
    "na": (-170, -10, -35,  85),
    "sa": (-82,  -56, -28,  15),
    "si": (50,  47, 180,  75),
}


def _regions_for_bbox(west: float, south: float, east: float, north: float) -> list[str]:
    """Return HydroRIVERS region codes that intersect the given bbox."""
    needed = [
        code for code, (rw, rs, re, rn) in _REGION_BBOX.items()
        if west < re and east > rw and south < rn and north > rs
    ]
    if not needed:
        logger.warning(
            "No HydroRIVERS region found for bbox (%.1f,%.1f,%.1f,%.1f). "
            "This may indicate an unsupported region.", west, south, east, north)
    return needed


def _cache_dir() -> Path:
    """Return (and create) the local shapefile cache directory."""
    d = _MAP2STL_ROOT / "cache" / "hydrorivers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_region_shapefile(region: str) -> Path | None:
    """Download and unzip the HydroRIVERS shapefile for *region* if not cached.

    On first use, extracts and simplifies geometries (removes collinear points)
    to reduce file size and read time.

    Returns the path to the simplified .shp file, or None on failure.
    """
    cache = _cache_dir()

    simplified_glob = list(cache.glob(
        f"HydroRIVERS_v10_{region}_simplified/*.shp"))
    if simplified_glob:
        simplified_shp = simplified_glob[0]
        try:
            import geopandas as gpd
            probe = gpd.read_file(str(simplified_shp), rows=1)
            if len(probe) > 0:
                logger.debug(
                    "HydroRIVERS '%s': using cached simplified shapefile", region)
                return simplified_shp
            logger.warning(
                "HydroRIVERS '%s': simplified shapefile is empty; regenerating", region)
        except Exception as exc:
            logger.warning("HydroRIVERS '%s': simplified shapefile invalid: %s; regenerating",
                           region, exc)

    # Fall back to original if simplified doesn't exist yet
    shp_glob = list(cache.glob(f"HydroRIVERS_v10_{region}/*.shp"))
    if shp_glob:
        original_shp = shp_glob[0]
        logger.info("HydroRIVERS '%s': simplifying cached geometries (first-use optimization)...",
                    region)
        simplified_shp = _simplify_and_cache_shapefile(original_shp, region)
        return simplified_shp or original_shp

    url = _REGION_URLS.get(region)
    if not url:
        logger.error("No HydroRIVERS URL for region '%s'", region)
        return None

    try:
        import requests
    except ImportError:
        logger.error("requests not installed; cannot download HydroRIVERS")
        return None

    logger.info("Downloading HydroRIVERS region '%s' from %s ...", region, url)
    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        raw = resp.content
        logger.info("HydroRIVERS '%s': downloaded %.1f MB, extracting ...",
                    region, len(raw) / 1e6)
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            dest = cache / f"HydroRIVERS_v10_{region}"
            dest.mkdir(exist_ok=True)
            z.extractall(dest)
        shp_glob = list(dest.glob("**/*.shp"))
        if not shp_glob:
            logger.error("HydroRIVERS '%s': no .shp in archive", region)
            return None

        original_shp = shp_glob[0]
        logger.info("HydroRIVERS '%s': simplifying geometries for faster subsequent reads...",
                    region)
        simplified_shp = _simplify_and_cache_shapefile(original_shp, region)
        if simplified_shp:
            logger.info("HydroRIVERS '%s': simplified version ready", region)
            return simplified_shp
        return original_shp
    except Exception as e:
        logger.error(
            "HydroRIVERS download failed for region '%s': %s", region, e)
        return None


def _simplify_and_cache_shapefile(shp_path: Path, region: str) -> Path | None:
    """Simplify geometries in a shapefile and cache as a new simplified version.

    Removes collinear points from all LineString/MultiLineString geometries.

    Returns the path to the simplified .shp file, or None if simplification failed.
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.warning(
            "geopandas not available; skipping shapefile simplification")
        return None

    try:
        cache = _cache_dir()
        dest_dir = cache / f"HydroRIVERS_v10_{region}_simplified"
        dest_dir.mkdir(exist_ok=True)

        orig_size = shp_path.stat().st_size
        logger.debug("HydroRIVERS '%s': reading original %.1f MB...",
                     region, orig_size / 1e6)
        gdf = gpd.read_file(str(shp_path))

        if len(gdf) == 0:
            logger.warning(
                "HydroRIVERS '%s': original shapefile is empty", region)
            return None

        logger.debug(
            "HydroRIVERS '%s': simplifying %d features...", region, len(gdf))
        gdf["geometry"] = gdf["geometry"].apply(_simplify_geometry)

        dest_shp = dest_dir / shp_path.name
        gdf.to_file(str(dest_shp))
        new_size = dest_shp.stat().st_size
        reduction = 100 * (1 - new_size / orig_size) if orig_size else 0.0
        logger.info("HydroRIVERS '%s': simplified %.1f MB -> %.1f MB (%.0f%% reduction)",
                    region, orig_size / 1e6, new_size / 1e6, reduction)
        return dest_shp

    except Exception as e:
        logger.error(
            "HydroRIVERS simplification failed for '%s': %s", region, e)
        return None


# Strahler order threshold for the "order3plus" tiered parquet file.
_ORDER3PLUS_THRESHOLD = 3


def _region_parquet_path(region: str, *, order3plus: bool = False) -> Path:
    """Return the GeoParquet path for a region's cached data."""
    suffix = "_order3plus" if order3plus else ""
    return _cache_dir() / f"HydroRIVERS_v10_{region}{suffix}.parquet"


def _parquet_is_valid(pq: Path) -> bool:
    """Return True if *pq* exists and contains at least one row."""
    if not pq.exists():
        return False
    try:
        import pyarrow.parquet as _pq_mod
        meta = _pq_mod.read_metadata(str(pq))
        if meta.num_rows > 0:
            logger.debug("HydroRIVERS parquet valid: %s (%.1f MB, %d rows)",
                         pq.name, pq.stat().st_size / 1e6, meta.num_rows)
            return True
    except Exception as exc:
        logger.warning("HydroRIVERS parquet invalid (%s): %s", pq.name, exc)
    try:
        pq.unlink(missing_ok=True)
    except Exception:
        pass
    return False


def _ensure_region_parquet(region: str) -> Path | None:
    """Return the *full* GeoParquet file for *region*, building both the
    full and order-3+ variants from the shapefile if needed.

    Returns the path to the **full** .parquet file, or None on failure.
    """
    pq_full = _region_parquet_path(region, order3plus=False)
    pq_o3 = _region_parquet_path(region, order3plus=True)

    if _parquet_is_valid(pq_full) and _parquet_is_valid(pq_o3):
        return pq_full

    shp = _ensure_region_shapefile(region)
    if shp is None:
        return None

    try:
        import geopandas as gpd
        logger.info(
            "HydroRIVERS '%s': building parquet(s) from shapefile...", region)
        gdf = gpd.read_file(str(shp), engine="pyogrio")
        if len(gdf) == 0:
            logger.warning("HydroRIVERS '%s': shapefile is empty", region)
            return None

        if not _parquet_is_valid(pq_full):
            gdf.to_parquet(pq_full, write_covering_bbox=True)
            logger.info("HydroRIVERS '%s': full parquet written (%.1f MB, %d features)",
                        region, pq_full.stat().st_size / 1e6, len(gdf))

        if not _parquet_is_valid(pq_o3):
            if "ORD_STRA" in gdf.columns:
                gdf_o3 = gdf[gdf["ORD_STRA"] >=
                             _ORDER3PLUS_THRESHOLD].reset_index(drop=True)
            else:
                gdf_o3 = gdf
            gdf_o3.to_parquet(pq_o3, write_covering_bbox=True)
            logger.info("HydroRIVERS '%s': order3+ parquet written (%.1f MB, %d features)",
                        region, pq_o3.stat().st_size / 1e6, len(gdf_o3))

        return pq_full
    except Exception as e:
        logger.error("HydroRIVERS '%s': parquet build failed: %s", region, e)
        return None


def fetch_hydrorivers(
    north: float, south: float, east: float, west: float,
    min_order: int = 3,
):
    """Fetch HydroRIVERS features intersecting the bbox as a GeoDataFrame.

    Uses a three-tier cache:
    1. Regional shapefiles (simplified with collinear point reduction) — permanent
    2. Per-region GeoParquet with bbox covering columns — fast bbox-filtered reads
    3. In-memory Strahler order filter — no I/O for parameter changes

    Args:
        north/south/east/west: bounding box in WGS-84 degrees
        min_order: minimum Strahler order (1=all, 3=medium+, 5=major only).

    Returns:
        GeoDataFrame with columns ``ORD_STRA``, ``DIS_AV_CMS``, ``geometry``, or
        None on failure.  Returned directly (no GeoJSON serialization) so the
        rasterizer can operate on shapely geometries without a round-trip.
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.error("geopandas not installed; cannot read HydroRIVERS")
        return None

    import time as _time
    t0 = _time.perf_counter()

    regions = _regions_for_bbox(west, south, east, north)
    logger.info("HydroRIVERS bbox (%.2f,%.2f,%.2f,%.2f) regions=%s, min_order=%d",
                west, south, east, north, regions, min_order)

    use_o3 = min_order >= _ORDER3PLUS_THRESHOLD

    gdfs = []
    for region in regions:
        if _ensure_region_parquet(region) is None:
            continue
        pq = _region_parquet_path(region, order3plus=use_o3)
        if not pq.exists():
            pq = _region_parquet_path(region, order3plus=False)
        try:
            t_read = _time.perf_counter()
            gdf = gpd.read_parquet(pq, bbox=(west, south, east, north))
            dt_read = _time.perf_counter() - t_read
            if len(gdf):
                gdfs.append(gdf)
                tag = "order3+" if use_o3 else "full"
                logger.info("  %s: %d features (%s parquet bbox read, %.2fs, %.1f MB)",
                            region, len(gdf), tag, dt_read, pq.stat().st_size / 1e6)
        except Exception as e:
            logger.error(
                "HydroRIVERS parquet read failed for '%s': %s", region, e)

    if not gdfs:
        logger.info("HydroRIVERS: no features found in bbox")
        return None

    import pandas as pd
    combined = pd.concat(gdfs, ignore_index=True)

    effective_min_order = max(1, min_order)
    if "ORD_STRA" in combined.columns and min_order > 1:
        combined = combined[combined["ORD_STRA"]
                            >= min_order].reset_index(drop=True)

    # No automatic thinning: the caller's min_order is honoured (2026-09-30). The
    # Amazon at order 3 is 315,707 reaches; burning them all takes ~1 min since
    # water_layers / rasterize_hydrorivers were vectorised, where the old cap
    # (order 3 -> 5 above 180k reaches) silently dropped two thirds of the rivers.
    logger.info("HydroRIVERS: %d features after order-%d+ filter",
                len(combined), effective_min_order)

    if len(combined) == 0:
        return None

    dt_total = _time.perf_counter() - t0
    logger.info("HydroRIVERS fetch total: %.2fs (%d features, no JSON round-trip)",
                dt_total, len(combined))
    return combined


def rasterize_hydrorivers_orders(
    gdf,
    north: float, south: float, east: float, west: float,
    dim: int,
    width_factor: float = 1.0,
) -> np.ndarray:
    """Rasterize HydroRIVERS reaches to a (dim×dim) uint8 grid of the highest
    Strahler order per pixel (0 = no river); depths come from :func:`order_depth_grid`.

    Args:
        gdf: GeoDataFrame with ``geometry`` and ``ORD_STRA`` columns
        width_factor: multiplier on per-line buffer width (default 1.0).

    Notes:
        - Vectorised simplify + buffer (shapely 2); reaches under ~1.5 px wide are
          burnt as lines, which ``all_touched`` paints at least one pixel wide.
        - Kept separate from the depth so a new min order / depth / exponent is a
          lookup on a cached order grid (``/api/terrain/hydrology``), and the
          grid also drives the "colour by order" view.
    """
    import time as _time
    t0 = _time.perf_counter()

    pixel_deg = (north - south) / dim
    min_buf_deg = pixel_deg * 0.6 * \
        float(width_factor)  # ≥1 px wide × user factor

    if gdf is None or len(gdf) == 0:
        logger.info("rasterize_hydrorivers: empty input")
        return np.zeros((dim, dim), dtype=np.uint8)

    n = len(gdf)
    logger.info("rasterize_hydrorivers: %d features, dim=%d, pixel_deg=%.5f, width_factor=%.2f",
                n, dim, pixel_deg, width_factor)

    # Vectorized prep — simplify all geometries, then per-row buffer based on order.
    t_prep = _time.perf_counter()
    if "ORD_STRA" in gdf.columns:
        order_arr = gdf["ORD_STRA"].clip(
            lower=1, upper=9).astype(int).to_numpy()
    else:
        order_arr = np.ones(n, dtype=int)
    # Buffer width: same formula as the legacy code (max(min_buf, min_buf * order / 3)).
    buffers = np.maximum(min_buf_deg, min_buf_deg * order_arr / 3.0)
    geoms = gdf.geometry.simplify(pixel_deg, preserve_topology=False)
    # A buffer under about a pixel wide burns the same cells as the line itself
    # with all_touched, so only wider rivers are buffered: buffering every reach
    # took 99 s for 88,799 Amazon reaches (buffered polygons have many vertices).
    import shapely

    wide = (2 * buffers > 1.5 * pixel_deg)
    arr = geoms.to_numpy().copy()
    if wide.any():
        arr[wide] = shapely.buffer(arr[wide], buffers[wide], quad_segs=2)
    keep = ~shapely.is_empty(arr)
    buffered = arr[keep]
    order_arr = order_arr[keep]
    dt_prep = _time.perf_counter() - t_prep
    logger.info("  vectorized simplify+buffer: %.2fs, %d shapes",
                dt_prep, len(buffered))

    # Highest Strahler order per pixel: lowest order first, higher orders overwrite.
    t_rast = _time.perf_counter()
    orders = np.zeros((dim, dim), dtype=np.uint8)
    for o in sorted(set(int(x) for x in order_arr)):
        mask = order_arr == o
        try:
            layer = burn_polygons(list(buffered[mask]), (dim, dim),
                                  bounds=(west, south, east, north), values=float(o),
                                  mode="set", all_touched=True, dtype=np.float32)
            orders[layer > 0] = o
        except Exception as e:
            logger.warning("HydroRIVERS rasterize order %d: %s", o, e)
    dt_rast = _time.perf_counter() - t_rast
    dt_total = _time.perf_counter() - t0
    logger.info("rasterize_hydrorivers done: %d river pixels at %dx%d, "
                "total=%.2fs (prep=%.2fs, rasterize=%.2fs)",
                int(np.count_nonzero(orders)), dim, dim, dt_total, dt_prep, dt_rast)
    return orders


def order_depth_grid(orders: np.ndarray, depression_base: float = -5.0,
                     order_exponent: float = 1.5, min_order: int = 1) -> np.ndarray:
    """Depression grid from a Strahler-order grid (0 = no river).

    ``depth = depression_base * (order / 9) ** order_exponent`` for orders at or
    above *min_order*: order 9 (Amazon) gets the full depth, order 1 almost none.
    The depth rises with the order, so the per-pixel highest order is also the
    deepest carve - the same grid the old per-order minimum produced. Changing
    *min_order*, the depth or the exponent is a lookup, not a re-rasterisation.
    """
    o = np.asarray(orders)
    lut = np.zeros(256, dtype=np.float32)
    k = np.arange(1, 256)
    lut[1:] = np.where(k >= max(1, int(min_order)),
                       float(depression_base) * (np.minimum(k, 9) / 9.0) ** float(order_exponent), 0.0)
    return lut[o.astype(np.uint8)]


def rasterize_hydrorivers(
    gdf,
    north: float, south: float, east: float, west: float,
    dim: int,
    depression_base: float = -5.0,
    order_exponent: float = 1.5,
    width_factor: float = 1.0,
) -> np.ndarray:
    """Rasterize a HydroRIVERS GeoDataFrame to a (dim×dim) float32 depression grid.

    :func:`rasterize_hydrorivers_orders` then :func:`order_depth_grid`:
    ``depth = depression_base * (order / 9) ** order_exponent``, 0 off-river.
    """
    return order_depth_grid(
        rasterize_hydrorivers_orders(gdf, north, south, east, west, dim, width_factor),
        depression_base, order_exponent)


class HydroRiversHydrologyLayer(HydrologyLayerBase):
    """HydroRIVERS provider for high-detail regional river data."""

    name = "hydrorivers"

    def fetch_and_rasterize(
        self,
        north,
        south,
        east,
        west,
        dim,
        scale_m,
        depression_m,
        min_order=3,
        order_exponent=1.5,
        width_factor=1.0,
    ):
        gdf = fetch_hydrorivers(north, south, east, west, min_order=min_order)
        if gdf is None:
            logger.info("HydroRIVERS: no features in region")
            return None

        n_features = len(gdf)
        orders = rasterize_hydrorivers_orders(gdf, north, south, east, west, dim,
                                              width_factor=width_factor)
        counts = gdf["ORD_STRA"].clip(lower=1, upper=10).astype(int).value_counts()
        return {
            "river_grid": order_depth_grid(orders, depression_m, order_exponent, min_order),
            "order_grid": orders,
            "order_counts": {int(k): int(v) for k, v in counts.items()},
            "feature_count": n_features,
            "source": self.name,
        }


class HydrologyService:
    """Hydrology orchestrator choosing the requested provider."""

    name = "hydrology"

    def __init__(self):
        self.providers = {
            "natural_earth": NaturalEarthHydrologyLayer(),
            "hydrorivers": HydroRiversHydrologyLayer(),
        }

    def fetch_and_rasterize(
        self,
        north,
        south,
        east,
        west,
        dim,
        scale_m,
        depression_m,
        source="natural_earth",
        min_order=3,
        order_exponent=1.5,
        width_factor=1.0,
    ):
        provider = self.providers.get(
            source) or self.providers["natural_earth"]
        # Natural Earth provider doesn't accept width_factor; pass only when supported.
        kwargs = dict(
            min_order=min_order, order_exponent=order_exponent,
        )
        if isinstance(provider, HydroRiversHydrologyLayer):
            kwargs["width_factor"] = width_factor
        return provider.fetch_and_rasterize(
            north, south, east, west, dim, scale_m, depression_m, **kwargs,
        )


HYDROLOGY_LAYER = HydrologyService()


def water_surface_mask(north, south, east, west, shape) -> np.ndarray | None:
    """Open water on a (rows, cols) grid over the bbox: sea and lakes, for the
    union with the river lines (row 0 north). None when no source is available.

    Union of
      * ESA WorldCover permanent water (class 80) and its no-data (0), which is
        where the ocean lies - WorldCover maps land only;
      * the sea of the local elevation store (GEBCO, ``config.json``
        ``ocean_root``): cells at or below 0 m connected to the bbox edge
        (:func:`geo2stl.water_layers.ocean_mask`).
    Either source may be missing (Earth Engine not set up, no local store).
    """
    import cv2 as _cv2

    from geo2stl.water_layers import ocean_mask

    h, w = int(shape[0]), int(shape[1])
    masks = []
    try:
        from geo2stl.geo import bbox_size_m
        from geo2stl.sat2stl import fetch_water_mask

        wm, ht = bbox_size_m({"north": north, "south": south, "east": east, "west": west})
        scale = max(30, int(max(wm / w, ht / h)))
        _, esa, _ = fetch_water_mask(north, south, east, west, scale, "esa")
        if esa is not None and esa.size:
            esa_water = ((esa == 80) | (esa == 0)).astype(np.uint8)
            masks.append(_cv2.resize(esa_water, (w, h), interpolation=_cv2.INTER_NEAREST) > 0)
    except Exception as exc:   # Earth Engine missing / not authenticated / offline
        logger.warning("water_surface_mask: ESA water unavailable (%s)", exc)
    try:
        from geo2stl.tiles import stitch_tiles_no_rasterio

        elev = stitch_tiles_no_rasterio((north, south, east, west))
        if elev is not None and np.size(elev):
            sea = ocean_mask(np.asarray(elev, dtype=np.float64)).astype(np.uint8)
            masks.append(_cv2.resize(sea, (w, h), interpolation=_cv2.INTER_NEAREST) > 0)
    except Exception as exc:
        logger.warning("water_surface_mask: local elevation store unavailable (%s)", exc)
    if not masks:
        return None
    return np.logical_or.reduce(masks)


def union_water_surface(river_grid: np.ndarray, water: np.ndarray | None,
                        depression_m: float) -> np.ndarray:
    """Rivers plus open water at the full depression depth (the deeper wins).

    HydroRIVERS has no sea or lake surfaces: subtracting rivers alone leaves the
    sea at its old level while every coastal river cuts a notch into the shore -
    a ring of notches around the coastline. With the water surface lowered by
    the same depth as the largest rivers, rivers run into it without a step.
    """
    if water is None or not np.any(water):
        return river_grid
    depth = np.float32(min(float(depression_m), 0.0))
    return np.where(water, np.minimum(river_grid, depth), river_grid).astype(river_grid.dtype)


def fetch_and_rasterize_hydrology(
    north, south, east, west, dim, scale_m, depression_m,
    source="natural_earth", min_order=3, order_exponent=1.5,
    width_factor=1.0, water_surface=True,
):
    """Fetch rivers and rasterize to a depression grid. Sync — call via run_in_executor.

    source='natural_earth': Natural Earth dataset (global, 3 tiers, coarse)
    source='hydrorivers':   HydroRIVERS dataset (regional shapefiles, ~500 m detail,
                            downloaded on first use and cached permanently)

    With *water_surface* (default) the grid is the union of the rivers and the
    open water (sea, lakes) of :func:`water_surface_mask`, so the sea is lowered
    with the rivers instead of leaving a ring of river mouths along the coast.

    Returns dict with keys ``river_grid``, ``feature_count``, ``source``,
    ``water_surface`` (bool: open water was merged) or None if no features
    were found.
    """
    import time as _time

    t0 = _time.perf_counter()
    try:
        result = HYDROLOGY_LAYER.fetch_and_rasterize(
            north,
            south,
            east,
            west,
            dim,
            scale_m,
            depression_m,
            source=source,
            min_order=min_order,
            order_exponent=order_exponent,
            width_factor=width_factor,
        )
        if result is None:
            if not water_surface:
                return None
            # No river reaches here, but the sea / lakes still belong in the layer.
            result = {"river_grid": np.zeros((dim, dim), dtype=np.float32),
                      "feature_count": 0, "source": source}
        result["water_surface"] = False
        if water_surface:
            water = water_surface_mask(north, south, east, west, result["river_grid"].shape)
            if water is not None:
                result["river_grid"] = union_water_surface(result["river_grid"], water,
                                                           depression_m)
                result["water_surface"] = True
            elif result["feature_count"] == 0:
                return None

        dt_total = _time.perf_counter() - t0
        logger.info(
            "Hydrology total: %.2fs, %s features via %s",
            dt_total,
            result.get("feature_count", 0),
            result.get("source", source),
        )
        return result

    except Exception as e:
        logger.error(f"Hydrology fetch/rasterize failed: {e}", exc_info=True)
        return None


def merge_rivers_with_dem(dem: np.ndarray, rivers: np.ndarray) -> np.ndarray:
    """Carve a river depression grid into a DEM.

    ``rivers`` holds depths relative to the ground (negative, 0 off-river; see
    :func:`rasterize_hydrorivers`), so they are added to the DEM. Taking the
    minimum of the two, as this once did, set every river cell above sea level
    to the absolute depth (-5 m) instead of 5 m below its own terrain.
    """
    return (dem + np.minimum(rivers, 0.0)).astype(dem.dtype, copy=False)
