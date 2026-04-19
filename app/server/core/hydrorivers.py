"""core/hydrorivers.py — HydroRIVERS-based high-detail river rasterization.

HydroRIVERS (Lehner et al.) provides ~8.5 M river reaches globally at
15 arc-second resolution (~500 m), with Strahler order and mean discharge.

Download strategy
-----------------
Shapefiles are downloaded on-demand by region and cached permanently under
``cache/hydrorivers/``.  pyogrio's ``bbox`` parameter clips at read time so
only features intersecting the requested bbox are loaded into memory.

The download URLs are the public hydrosheds.org S3 bucket — no login required
for the "standard" resolution shapefiles.

Tiered parquet caching
----------------------
Two parquet files are built per region:
- ``HydroRIVERS_v10_{region}.parquet`` — all features (for min_order <= 2)
- ``HydroRIVERS_v10_{region}_order3plus.parquet`` — order 3+ only (~25% of
  features, ~12 MB vs ~50 MB).  Used by default since the UI default is
  min_order=3, eliminating 75% of features from disk I/O.

Query-time simplification
-------------------------
Before buffering, each geometry is simplified with Shapely's RDP at a
tolerance of one output pixel in degrees.  This is imperceptible at the
output resolution and reduces vertex count for the expensive ``.buffer()``
call.

River depression scaling
------------------------
Depression depth is scaled by Strahler order so major rivers get deep cuts
and small streams get shallow ones::

    depression = depression_base * (strahler_order / max_order) ** order_exponent

Default: base=-5 m, exponent=1.5 → order-9 Amazon ~= -5 m, order-1 stream ~= -0.04 m.

Future optimisations
--------------------
- **RDP simplify during parquet build:** Apply ``geom.simplify(tolerance)``
  with a moderate tolerance (e.g. 0.005° ≈ 550 m) as a complement to the
  collinear reduction.  Most HydroRIVERS features are already 2-point
  segments so the gain is modest (~5–10% further size reduction).
- **Topology-aware segment merging:** Many order-1/2 rivers are chains of
  2-point segments that could be merged into single multi-point LineStrings
  via ``shapely.ops.linemerge``, reducing per-feature overhead.  Complex to
  implement correctly.
"""

from __future__ import annotations

import io
import logging
import os
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


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
        
        # Cross product to determine collinearity
        # For collinear points, cross product ≈ 0
        cross = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])
        
        # If cross product is significant, point is not collinear
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


logger = logging.getLogger(__name__)

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
    "na": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_na_shp.zip",   # North America (includes Central America)
    "sa": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_sa_shp.zip",   # South America
    "si": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_si_shp.zip",   # Siberia
}

# Coarse region bounding boxes: (west, south, east, north)
# Note: HydroRIVERS regions overlap by design; pyogrio bbox parameter clips at read time
_REGION_BBOX: dict[str, Tuple[float, float, float, float]] = {
    "af": (-20,  -35,  55,  38),      # Africa
    "ar": (-180,  60, 180,  90),      # Arctic
    "as": (  57,  -5, 180,  60),      # Asia (extended south to cover more of SE Asia)
    "au": ( 112, -48, 180,  -5),      # Australia (extended north slightly)
    "eu": (-25,   35,  65,  72),      # Europe
    "na": (-170, -10, -35,  85),      # North America (reduced south to 10°N to avoid cutting SA)
    "sa": (-82,  -56, -28,  15),      # South America (extended north to 15°N to include Colombia, Venezuela)
    "si": (  50,  47, 180,  75),      # Siberia (extended west and south slightly)
}


def _regions_for_bbox(west: float, south: float, east: float, north: float) -> list[str]:
    """Return HydroRIVERS region codes that intersect the given bbox.
    
    Note: HydroRIVERS regions overlap by design. pyogrio's bbox parameter
    ensures only features intersecting the requested bbox are loaded.
    """
    needed = []
    for code, (rw, rs, re, rn) in _REGION_BBOX.items():
        # Check if bbox intersects with region
        if west < re and east > rw and south < rn and north > rs:
            needed.append(code)
    
    if not needed:
        logger.warning(
            f"No HydroRIVERS region found for bbox ({west:.1f},{south:.1f},"
            f"{east:.1f},{north:.1f}). This may indicate an unsupported region.")
        # Return empty list; caller will handle appropriately
    
    return needed


def _cache_dir() -> Path:
    """Return (and create) the local shapefile cache directory."""
    from app.server.config import _STRM2STL_DIR  # type: ignore
    d = _STRM2STL_DIR / "cache" / "hydrorivers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_region_shapefile(region: str) -> Optional[Path]:
    """
    Download and unzip the HydroRIVERS shapefile for *region* if not cached.

    On first use, extracts and simplifies geometries (removes collinear points)
    to reduce file size and read time. Simplified version is cached alongside original.

    Returns the path to the simplified .shp file, or None on failure.
    """
    cache = _cache_dir()

    simplified_glob = list(
        cache.glob(f"HydroRIVERS_v10_{region}_simplified/*.shp")
    )
    if simplified_glob:
        simplified_shp = simplified_glob[0]
        try:
            import geopandas as gpd

            probe = gpd.read_file(str(simplified_shp), rows=1)
            if len(probe) > 0:
                logger.debug(
                    f"HydroRIVERS '{region}': using cached simplified shapefile"
                )
                return simplified_shp
            logger.warning(
                f"HydroRIVERS '{region}': simplified shapefile is empty; regenerating"
            )
        except Exception as exc:
            logger.warning(
                f"HydroRIVERS '{region}': simplified shapefile invalid: {exc}; regenerating"
            )

    # Fall back to original if simplified doesn't exist yet
    shp_glob = list(cache.glob(f"HydroRIVERS_v10_{region}/*.shp"))
    if shp_glob:
        original_shp = shp_glob[0]
        logger.info(
            f"HydroRIVERS '{region}': simplifying cached geometries (first-use optimization)..."
        )
        simplified_shp = _simplify_and_cache_shapefile(original_shp, region)
        if simplified_shp:
            return simplified_shp
        return original_shp

    url = _REGION_URLS.get(region)
    if not url:
        logger.error(f"No HydroRIVERS URL for region '{region}'")
        return None

    try:
        import requests
    except ImportError:
        logger.error("requests not installed; cannot download HydroRIVERS")
        return None

    logger.info(f"Downloading HydroRIVERS region '{region}' from {url} ...")
    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        raw = resp.content
        logger.info(
            f"HydroRIVERS '{region}': downloaded {len(raw)/1e6:.1f} MB, extracting ..."
        )
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            dest = cache / f"HydroRIVERS_v10_{region}"
            dest.mkdir(exist_ok=True)
            z.extractall(dest)
        shp_glob = list(dest.glob("**/*.shp"))
        if not shp_glob:
            logger.error(f"HydroRIVERS '{region}': no .shp in archive")
            return None
        
        original_shp = shp_glob[0]
        logger.info(
            f"HydroRIVERS '{region}': simplifying geometries for faster subsequent reads..."
        )
        simplified_shp = _simplify_and_cache_shapefile(original_shp, region)
        if simplified_shp:
            logger.info(f"HydroRIVERS '{region}': simplified version ready")
            return simplified_shp
        return original_shp
    except Exception as e:
        logger.error(f"HydroRIVERS download failed for region '{region}': {e}")
        return None


def _simplify_and_cache_shapefile(shp_path: Path, region: str) -> Optional[Path]:
    """
    Simplify geometries in a shapefile and cache as a new simplified version.

    Removes collinear points from all LineString/MultiLineString geometries,

    Parameters
    ----------
    shp_path : Path
        Path to original .shp file
    region : str
        Region code (for logging and naming)

    Returns
    -------
    Path or None
        Path to simplified .shp file, or None if simplification failed
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.warning("geopandas not available; skipping shapefile simplification")
        return None

    try:
        cache = _cache_dir()
        dest_dir = cache / f"HydroRIVERS_v10_{region}_simplified"
        dest_dir.mkdir(exist_ok=True)

        orig_size = shp_path.stat().st_size
        logger.debug(
            f"HydroRIVERS '{region}': reading original {orig_size / 1e6:.1f} MB..."
        )
        gdf = gpd.read_file(str(shp_path))

        if len(gdf) == 0:
            logger.warning(f"HydroRIVERS '{region}': original shapefile is empty")
            return None

        # Simplify geometries
        logger.debug(f"HydroRIVERS '{region}': simplifying {len(gdf)} features...")
        gdf["geometry"] = gdf["geometry"].apply(_simplify_geometry)

        # Save simplified version in same directory structure
        dest_shp = dest_dir / shp_path.name
        gdf.to_file(str(dest_shp))
        new_size = dest_shp.stat().st_size
        reduction = 100 * (1 - new_size / orig_size) if orig_size else 0.0

        logger.info(
            f"HydroRIVERS '{region}': simplified {orig_size/1e6:.1f} MB -> "
            f"{new_size/1e6:.1f} MB ({reduction:.0f}% reduction)"
        )
        return dest_shp

    except Exception as e:
        logger.error(f"HydroRIVERS simplification failed for '{region}': {e}")
        return None


# Strahler order threshold for the "order3plus" tiered parquet file.
_ORDER3PLUS_THRESHOLD = 3


def _region_parquet_path(region: str, *, order3plus: bool = False) -> Path:
    """Return the GeoParquet path for a region's cached data.

    Parameters
    ----------
    region : str
        HydroRIVERS region code.
    order3plus : bool
        If True, return the path for the order-3+ subset parquet.
    """
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
            logger.debug(f"HydroRIVERS parquet valid: {pq.name} "
                         f"({pq.stat().st_size / 1e6:.1f} MB, {meta.num_rows} rows)")
            return True
    except Exception as exc:
        logger.warning(f"HydroRIVERS parquet invalid ({pq.name}): {exc}")
    try:
        pq.unlink(missing_ok=True)
    except Exception:
        pass
    return False


def _ensure_region_parquet(region: str) -> Optional[Path]:
    """Return the *full* GeoParquet file for *region*, building both the
    full and order-3+ variants from the shapefile if needed.

    The parquet files include bbox covering columns so that
    ``gpd.read_parquet(path, bbox=...)`` can skip irrelevant row groups
    without scanning the entire file.

    Returns the path to the **full** .parquet file, or None on failure.
    """
    pq_full = _region_parquet_path(region, order3plus=False)
    pq_o3 = _region_parquet_path(region, order3plus=True)

    # If both variants already exist and are valid, nothing to do.
    if _parquet_is_valid(pq_full) and _parquet_is_valid(pq_o3):
        return pq_full

    # Build both from the simplified shapefile.
    shp = _ensure_region_shapefile(region)
    if shp is None:
        return None

    try:
        import geopandas as gpd
        logger.info(f"HydroRIVERS '{region}': building parquet(s) from shapefile...")
        gdf = gpd.read_file(str(shp), engine="pyogrio")
        if len(gdf) == 0:
            logger.warning(f"HydroRIVERS '{region}': shapefile is empty")
            return None

        # ── Full parquet ──
        if not _parquet_is_valid(pq_full):
            gdf.to_parquet(pq_full, write_covering_bbox=True)
            logger.info(f"HydroRIVERS '{region}': full parquet written "
                        f"({pq_full.stat().st_size / 1e6:.1f} MB, {len(gdf)} features)")

        # ── Order-3+ subset parquet ──
        if not _parquet_is_valid(pq_o3):
            if "ORD_STRA" in gdf.columns:
                gdf_o3 = gdf[gdf["ORD_STRA"] >= _ORDER3PLUS_THRESHOLD].reset_index(drop=True)
            else:
                gdf_o3 = gdf  # can't filter — keep all
            gdf_o3.to_parquet(pq_o3, write_covering_bbox=True)
            logger.info(f"HydroRIVERS '{region}': order3+ parquet written "
                        f"({pq_o3.stat().st_size / 1e6:.1f} MB, {len(gdf_o3)} features)")

        return pq_full
    except Exception as e:
        logger.error(f"HydroRIVERS '{region}': parquet build failed: {e}")
        return None


def fetch_hydrorivers(
    north: float, south: float, east: float, west: float,
    min_order: int = 3,
) -> Optional[dict]:
    """
    Fetch HydroRIVERS features intersecting the bbox as a GeoJSON FeatureCollection.

    Uses a three-tier cache:
    1. Regional shapefiles (simplified with collinear point reduction) — permanent
    2. Per-region GeoParquet with bbox covering columns — fast bbox-filtered reads
    3. In-memory Strahler order filter — no I/O for parameter changes

    Args:
        north/south/east/west: bounding box in WGS-84 degrees
        min_order: minimum Strahler order to include (1=all, 3=medium+, 5=major only).
                   Higher values reduce feature count and rasterize faster.

    Returns:
        GeoJSON FeatureCollection with properties ``ORD_STRA`` and ``DIS_AV_CMS``,
        or None on failure.
    """
    try:
        import geopandas as gpd
    except ImportError:
        logger.error("geopandas not installed; cannot read HydroRIVERS")
        return None

    import time as _time
    t0 = _time.perf_counter()

    regions = _regions_for_bbox(west, south, east, north)
    logger.info(f"HydroRIVERS bbox ({west:.2f},{south:.2f},{east:.2f},{north:.2f}) "
                f"regions={regions}, min_order={min_order}")

    # Pick the smallest parquet that satisfies the requested min_order.
    use_o3 = min_order >= _ORDER3PLUS_THRESHOLD

    gdfs = []
    for region in regions:
        # Ensure both parquet variants exist (builds from shapefile if needed).
        t_pq = _time.perf_counter()
        if _ensure_region_parquet(region) is None:
            continue
        pq = _region_parquet_path(region, order3plus=use_o3)
        if not pq.exists():
            # Fallback to full parquet if the subset file is missing.
            pq = _region_parquet_path(region, order3plus=False)
        try:
            t_read = _time.perf_counter()
            gdf = gpd.read_parquet(pq, bbox=(west, south, east, north))
            dt_read = _time.perf_counter() - t_read
            if len(gdf):
                gdfs.append(gdf)
                tag = "order3+" if use_o3 else "full"
                logger.info(f"  {region}: {len(gdf)} features ({tag} parquet bbox read, "
                            f"{dt_read:.2f}s read, {pq.stat().st_size / 1e6:.1f} MB)")
        except Exception as e:
            logger.error(f"HydroRIVERS parquet read failed for '{region}': {e}")

    if not gdfs:
        logger.info("HydroRIVERS: no features found in bbox")
        return None

    import pandas as pd
    combined = pd.concat(gdfs, ignore_index=True)

    # Filter by Strahler order (cheap in-memory filter)
    if "ORD_STRA" in combined.columns and min_order > 1:
        combined = combined[combined["ORD_STRA"] >= min_order].reset_index(drop=True)

    logger.info(f"HydroRIVERS: {len(combined)} features after order-{min_order}+ filter")

    if len(combined) == 0:
        return None

    t_json = _time.perf_counter()
    import json
    result = json.loads(combined.to_json())
    dt_json = _time.perf_counter() - t_json
    dt_total = _time.perf_counter() - t0
    logger.info(f"HydroRIVERS fetch_hydrorivers total: {dt_total:.2f}s "
                f"(to_json: {dt_json:.2f}s, {len(combined)} features)")
    return result


def rasterize_hydrorivers(
    geojson: dict,
    north: float, south: float, east: float, west: float,
    dim: int,
    depression_base: float = -5.0,
    order_exponent: float = 1.5,
) -> np.ndarray:
    """
    Rasterize HydroRIVERS GeoJSON to a (dim×dim) float32 depression grid.

    Depression depth is scaled by Strahler order::

        depth = depression_base * (order / 9) ** order_exponent

    So order-9 Amazon = ``depression_base``, order-1 stream ≈ 0.

    Args:
        geojson: FeatureCollection from fetch_hydrorivers()
        depression_base: depth (metres, negative) for the largest rivers
        order_exponent: controls how steeply smaller rivers are cut

    Returns:
        float32 array shape (dim, dim), 0 where no river, negative where river.
    """
    try:
        from shapely.geometry import shape, mapping
        from rasterio.features import rasterize as _rasterize
        from rasterio.transform import from_bounds
    except ImportError:
        logger.error("shapely/rasterio not installed; returning zero grid")
        return np.zeros((dim, dim), dtype=np.float32)

    import time as _time
    t0 = _time.perf_counter()

    transform = from_bounds(west, south, east, north, dim, dim)
    pixel_deg = (north - south) / dim
    # Buffer each line to at least 1 pixel width so thin streams are visible
    min_buf_deg = pixel_deg * 0.6

    grid = np.zeros((dim, dim), dtype=np.float32)

    features = geojson.get("features", [])
    max_order = 9  # HydroRIVERS Strahler max
    logger.info(f"rasterize_hydrorivers: {len(features)} features, dim={dim}, "
                f"pixel_deg={pixel_deg:.5f}")

    # Group features by order so we rasterize each order in one pass (faster)
    t_prep = _time.perf_counter()
    from collections import defaultdict
    by_order: dict[int, list] = defaultdict(list)
    skipped_empty = 0
    for feat in features:
        props = feat.get("properties") or {}
        order = int(props.get("ORD_STRA") or 1)
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            if s.geom_type in ("LineString", "MultiLineString"):
                # Query-time RDP simplify: remove detail finer than one
                # output pixel — imperceptible and speeds up .buffer().
                s = s.simplify(pixel_deg, preserve_topology=False)
                if s.is_empty:
                    skipped_empty += 1
                    continue
                # Scale buffer with order so major rivers appear wider
                buf = max(min_buf_deg, min_buf_deg * order / 3)
                s = s.buffer(buf)
            if not s.is_empty:
                by_order[order].append(mapping(s))
        except Exception:
            continue
    dt_prep = _time.perf_counter() - t_prep
    total_shapes = sum(len(v) for v in by_order.values())
    logger.info(f"  simplify+buffer: {dt_prep:.2f}s, {total_shapes} shapes "
                f"({skipped_empty} skipped empty), {len(by_order)} order groups")

    # Rasterize lowest order (shallowest) first so higher-order rivers overwrite
    t_rast = _time.perf_counter()
    for order in sorted(by_order.keys()):
        depth = depression_base * (order / max_order) ** order_exponent
        shapes = [(geom, depth) for geom in by_order[order]]
        if not shapes:
            continue
        try:
            layer = np.zeros((dim, dim), dtype=np.float32)
            _rasterize(shapes, out=layer, transform=transform, dtype="float32")
            # Apply: keep the more-negative value (deeper river wins)
            mask = layer != 0.0
            grid[mask] = np.minimum(grid[mask], layer[mask])
        except Exception as e:
            logger.warning(f"HydroRIVERS rasterize order {order}: {e}")
    dt_rast = _time.perf_counter() - t_rast

    n_river = int(np.sum(grid != 0))
    dt_total = _time.perf_counter() - t0
    logger.info(f"rasterize_hydrorivers done: {n_river} river pixels at {dim}x{dim}, "
                f"total={dt_total:.2f}s (prep={dt_prep:.2f}s, rasterize={dt_rast:.2f}s)")
    return grid
