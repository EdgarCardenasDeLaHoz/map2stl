"""geo2stl/hydrorivers.py — HydroRIVERS-based high-detail river rasterization.

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
import zipfile
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Cache root resolved from this file's location — no server dependency.
# geo2stl/hydrorivers.py → geo2stl/ → strm2stl/
_STRM2STL_ROOT = Path(__file__).parent.parent


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
        cross = (p1[0] - p0[0]) * (p2[1] - p0[1]) - (p1[1] - p0[1]) * (p2[0] - p0[0])

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
    "na": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_na_shp.zip",   # North America
    "sa": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_sa_shp.zip",   # South America
    "si": "https://data.hydrosheds.org/file/HydroRIVERS/HydroRIVERS_v10_si_shp.zip",   # Siberia
}

# Coarse region bounding boxes: (west, south, east, north)
# Note: HydroRIVERS regions overlap by design; pyogrio bbox parameter clips at read time.
_REGION_BBOX: dict[str, Tuple[float, float, float, float]] = {
    "af": (-20,  -35,  55,  38),
    "ar": (-180,  60, 180,  90),
    "as": (  57,  -5, 180,  60),
    "au": ( 112, -48, 180,  -5),
    "eu": (-25,   35,  65,  72),
    "na": (-170, -10, -35,  85),
    "sa": (-82,  -56, -28,  15),
    "si": (  50,  47, 180,  75),
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
    d = _STRM2STL_ROOT / "cache" / "hydrorivers"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ensure_region_shapefile(region: str) -> Optional[Path]:
    """Download and unzip the HydroRIVERS shapefile for *region* if not cached.

    On first use, extracts and simplifies geometries (removes collinear points)
    to reduce file size and read time.

    Returns the path to the simplified .shp file, or None on failure.
    """
    cache = _cache_dir()

    simplified_glob = list(cache.glob(f"HydroRIVERS_v10_{region}_simplified/*.shp"))
    if simplified_glob:
        simplified_shp = simplified_glob[0]
        try:
            import geopandas as gpd
            probe = gpd.read_file(str(simplified_shp), rows=1)
            if len(probe) > 0:
                logger.debug("HydroRIVERS '%s': using cached simplified shapefile", region)
                return simplified_shp
            logger.warning("HydroRIVERS '%s': simplified shapefile is empty; regenerating", region)
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
        logger.error("HydroRIVERS download failed for region '%s': %s", region, e)
        return None


def _simplify_and_cache_shapefile(shp_path: Path, region: str) -> Optional[Path]:
    """Simplify geometries in a shapefile and cache as a new simplified version.

    Removes collinear points from all LineString/MultiLineString geometries.

    Returns the path to the simplified .shp file, or None if simplification failed.
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
        logger.debug("HydroRIVERS '%s': reading original %.1f MB...", region, orig_size / 1e6)
        gdf = gpd.read_file(str(shp_path))

        if len(gdf) == 0:
            logger.warning("HydroRIVERS '%s': original shapefile is empty", region)
            return None

        logger.debug("HydroRIVERS '%s': simplifying %d features...", region, len(gdf))
        gdf["geometry"] = gdf["geometry"].apply(_simplify_geometry)

        dest_shp = dest_dir / shp_path.name
        gdf.to_file(str(dest_shp))
        new_size = dest_shp.stat().st_size
        reduction = 100 * (1 - new_size / orig_size) if orig_size else 0.0
        logger.info("HydroRIVERS '%s': simplified %.1f MB -> %.1f MB (%.0f%% reduction)",
                    region, orig_size / 1e6, new_size / 1e6, reduction)
        return dest_shp

    except Exception as e:
        logger.error("HydroRIVERS simplification failed for '%s': %s", region, e)
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


def _ensure_region_parquet(region: str) -> Optional[Path]:
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
        logger.info("HydroRIVERS '%s': building parquet(s) from shapefile...", region)
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
                gdf_o3 = gdf[gdf["ORD_STRA"] >= _ORDER3PLUS_THRESHOLD].reset_index(drop=True)
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
) -> Optional[dict]:
    """Fetch HydroRIVERS features intersecting the bbox as a GeoJSON FeatureCollection.

    Uses a three-tier cache:
    1. Regional shapefiles (simplified with collinear point reduction) — permanent
    2. Per-region GeoParquet with bbox covering columns — fast bbox-filtered reads
    3. In-memory Strahler order filter — no I/O for parameter changes

    Args:
        north/south/east/west: bounding box in WGS-84 degrees
        min_order: minimum Strahler order (1=all, 3=medium+, 5=major only).

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
            logger.error("HydroRIVERS parquet read failed for '%s': %s", region, e)

    if not gdfs:
        logger.info("HydroRIVERS: no features found in bbox")
        return None

    import pandas as pd
    combined = pd.concat(gdfs, ignore_index=True)

    if "ORD_STRA" in combined.columns and min_order > 1:
        combined = combined[combined["ORD_STRA"] >= min_order].reset_index(drop=True)

    logger.info("HydroRIVERS: %d features after order-%d+ filter", len(combined), min_order)

    if len(combined) == 0:
        return None

    import json
    t_json = _time.perf_counter()
    result = json.loads(combined.to_json())
    dt_total = _time.perf_counter() - t0
    logger.info("HydroRIVERS fetch total: %.2fs (to_json: %.2fs, %d features)",
                dt_total, _time.perf_counter() - t_json, len(combined))
    return result


def rasterize_hydrorivers(
    geojson: dict,
    north: float, south: float, east: float, west: float,
    dim: int,
    depression_base: float = -5.0,
    order_exponent: float = 1.5,
) -> np.ndarray:
    """Rasterize HydroRIVERS GeoJSON to a (dim×dim) float32 depression grid.

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
    min_buf_deg = pixel_deg * 0.6  # at least 1 pixel width for thin streams

    grid = np.zeros((dim, dim), dtype=np.float32)
    features = geojson.get("features", [])
    max_order = 9  # HydroRIVERS Strahler max
    logger.info("rasterize_hydrorivers: %d features, dim=%d, pixel_deg=%.5f",
                len(features), dim, pixel_deg)

    # Group features by order so we rasterize each order in one pass
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
                s = s.simplify(pixel_deg, preserve_topology=False)
                if s.is_empty:
                    skipped_empty += 1
                    continue
                buf = max(min_buf_deg, min_buf_deg * order / 3)
                s = s.buffer(buf)
            if not s.is_empty:
                by_order[order].append(mapping(s))
        except Exception:
            continue
    dt_prep = _time.perf_counter() - t_prep
    total_shapes = sum(len(v) for v in by_order.values())
    logger.info("  simplify+buffer: %.2fs, %d shapes (%d skipped), %d order groups",
                dt_prep, total_shapes, skipped_empty, len(by_order))

    # Rasterize lowest order first so higher-order rivers overwrite
    t_rast = _time.perf_counter()
    for order in sorted(by_order.keys()):
        depth = depression_base * (order / max_order) ** order_exponent
        shapes = [(geom, depth) for geom in by_order[order]]
        if not shapes:
            continue
        try:
            layer = np.zeros((dim, dim), dtype=np.float32)
            _rasterize(shapes, out=layer, transform=transform, dtype="float32")
            mask = layer != 0.0
            grid[mask] = np.minimum(grid[mask], layer[mask])
        except Exception as e:
            logger.warning("HydroRIVERS rasterize order %d: %s", order, e)
    dt_rast = _time.perf_counter() - t_rast

    n_river = int(np.sum(grid != 0))
    dt_total = _time.perf_counter() - t0
    logger.info("rasterize_hydrorivers done: %d river pixels at %dx%d, "
                "total=%.2fs (prep=%.2fs, rasterize=%.2fs)",
                n_river, dim, dim, dt_total, dt_prep, dt_rast)
    return grid
