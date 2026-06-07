"""
nDSM provider — Normalized Digital Surface Model from GLO-30 minus FABDEM.

GLO-30 (Copernicus DEM) = DSM including buildings + vegetation.
FABDEM (Forest And Buildings-removed Copernicus DEM) = bare-earth DTM.
nDSM = GLO-30 − FABDEM = building/vegetation heights above ground.

Data sources (both free, no API key):
  - GLO-30: AWS Open Data  s3://copernicus-dem-30m/  (public, no auth)
  - FABDEM: Bristol uni via OpenTopography or direct HTTP

Both use 1°×1° tiles named by the SW corner, e.g.:
  GLO-30:  Copernicus_DSM_COG_10_N41_00_E002_00_DEM/
  FABDEM:  N41E002_FABDEM_V1-2.tif

Coverage: Global (GLO-30), near-global (FABDEM: ±80° lat).
Resolution: ~30m.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Tuple

import numpy as np

from app.server.core.cache import (
    CACHE_ROOT, make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)
from city2stl.skyline.height import BBox, HeightResult

logger = logging.getLogger(__name__)

# Register TTL for ndsm namespace
NAMESPACE_TTL.setdefault("ndsm", 90 * 86400)  # 90 days

# Confidence assigned to nDSM-derived heights
NDSM_CONFIDENCE = 0.8


# ── Tile naming ──────────────────────────────────────────────────

def _tile_name_glo30(lat: int, lon: int) -> str:
    """Return the GLO-30 COG tile prefix for the 1° cell whose SW corner
    is (lat, lon).

    Format: Copernicus_DSM_COG_10_N41_00_E002_00_DEM
    """
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    return (f"Copernicus_DSM_COG_10_{ns}{abs(lat):02d}_00_"
            f"{ew}{abs(lon):03d}_00_DEM")


def _tile_url_glo30(lat: int, lon: int) -> str:
    """HTTPS URL for a GLO-30 COG tile on AWS Open Data."""
    name = _tile_name_glo30(lat, lon)
    return f"https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/{name}/{name}.tif"


def _tile_url_fabdem(lat: int, lon: int) -> str:
    """HTTPS URL for a FABDEM tile.

    FABDEM is distributed via several mirrors. We use the GAIA/Bristol
    direct download link pattern.
    """
    ns = "N" if lat >= 0 else "S"
    ew = "E" if lon >= 0 else "W"
    name = f"{ns}{abs(lat):02d}{ew}{abs(lon):03d}_FABDEM_V1-2"
    return f"https://data.bris.ac.uk/datasets/s5hqmjcdj8yo2ibzi9b4ew3sn/{name}.tif"


def _tiles_for_bbox(bbox: BBox) -> list[Tuple[int, int]]:
    """Return list of (lat, lon) SW corners covering *bbox*.

    bbox = (north, south, east, west).
    """
    north, south, east, west = bbox
    lat_min = int(np.floor(south))
    lat_max = int(np.floor(north))
    lon_min = int(np.floor(west))
    lon_max = int(np.floor(east))
    tiles = []
    for lat in range(lat_min, lat_max + 1):
        for lon in range(lon_min, lon_max + 1):
            tiles.append((lat, lon))
    return tiles


# ── Tile download + cache ────────────────────────────────────────

def _tile_cache_dir() -> Path:
    d = CACHE_ROOT / "ndsm" / "tiles"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _download_tile(url: str, dest: Path) -> bool:
    """Download a GeoTIFF from *url* to *dest*. Returns False on 404/error."""
    import urllib.request
    import urllib.error

    try:
        logger.info(f"Downloading {url}")
        urllib.request.urlretrieve(url, str(dest))
        return True
    except urllib.error.HTTPError as e:
        if e.code == 404:
            logger.warning(f"Tile not found (404): {url}")
        else:
            logger.warning(f"HTTP {e.code} downloading {url}: {e}")
        return False
    except Exception as e:
        logger.warning(f"Failed to download {url}: {e}")
        return False


def _read_geotiff(path: Path) -> Tuple[np.ndarray, dict] | None:
    """Read a GeoTIFF as float32 array + transform metadata.

    Uses rasterio if available, else falls back to PIL + manual georef.
    Returns (array, {"transform": [a,b,c,d,e,f], "crs": "EPSG:4326"}) or None.
    """
    try:
        import rasterio
        with rasterio.open(str(path)) as ds:
            arr = ds.read(1).astype(np.float32)
            nodata = ds.nodata
            if nodata is not None:
                arr[arr == nodata] = np.nan
            t = ds.transform
            meta = {
                "transform": [t.a, t.b, t.c, t.d, t.e, t.f],
                "width": ds.width,
                "height": ds.height,
                "crs": str(ds.crs),
            }
            return arr, meta
    except ImportError:
        pass

    # Fallback: PIL (no CRS info, assume EPSG:4326 for 1° tiles)
    try:
        from PIL import Image
        img = Image.open(str(path))
        arr = np.array(img, dtype=np.float32)
        return arr, {"width": arr.shape[1], "height": arr.shape[0]}
    except Exception as e:
        logger.warning(f"Cannot read GeoTIFF {path}: {e}")
        return None


def _get_tile(source: str, lat: int, lon: int) -> np.ndarray | None:
    """Fetch a single 1° tile (cached). *source* is 'glo30' or 'fabdem'.

    Returns float32 array or None if tile unavailable.
    """
    cache_dir = _tile_cache_dir()
    cache_name = f"{source}_{lat:+03d}_{lon:+04d}.npz"
    cache_path = cache_dir / cache_name

    # Check local cache
    if cache_path.exists():
        try:
            loaded = np.load(str(cache_path))
            return loaded["arr"].astype(np.float32)
        except Exception:
            cache_path.unlink(missing_ok=True)

    # Download
    if source == "glo30":
        url = _tile_url_glo30(lat, lon)
    else:
        url = _tile_url_fabdem(lat, lon)

    with tempfile.NamedTemporaryFile(suffix=".tif", delete=False) as tmp:
        tmp_path = Path(tmp.name)

    try:
        if not _download_tile(url, tmp_path):
            return None
        result = _read_geotiff(tmp_path)
        if result is None:
            return None
        arr, _ = result
        # Cache as compressed npz
        np.savez_compressed(str(cache_path), arr=arr)
        return arr
    finally:
        tmp_path.unlink(missing_ok=True)


# ── Crop + resample ──────────────────────────────────────────────

def _crop_to_bbox(tile_arr: np.ndarray, tile_lat: int, tile_lon: int,
                  bbox: BBox) -> np.ndarray:
    """Crop a 1° tile array to the portion overlapping *bbox*.

    Assumes the tile covers [tile_lat, tile_lat+1) × [tile_lon, tile_lon+1),
    with row 0 = north edge (top).
    """
    north, south, east, west = bbox
    h, w = tile_arr.shape

    # Fraction of the tile covered by bbox (clamped to tile extent)
    tile_n = tile_lat + 1
    tile_s = tile_lat
    tile_e = tile_lon + 1
    tile_w = tile_lon

    frac_n = max(0.0, min(1.0, (tile_n - north) / (tile_n - tile_s)))
    frac_s = max(0.0, min(1.0, (tile_n - south) / (tile_n - tile_s)))
    frac_w = max(0.0, min(1.0, (west - tile_w) / (tile_e - tile_w)))
    frac_e = max(0.0, min(1.0, (east - tile_w) / (tile_e - tile_w)))

    r0 = int(round(frac_n * h))
    r1 = int(round(frac_s * h))
    c0 = int(round(frac_w * w))
    c1 = int(round(frac_e * w))

    r0 = max(0, min(r0, h - 1))
    r1 = max(r0 + 1, min(r1, h))
    c0 = max(0, min(c0, w - 1))
    c1 = max(c0 + 1, min(c1, w))

    return tile_arr[r0:r1, c0:c1]


def _stitch_tiles(tiles: dict[Tuple[int, int], np.ndarray],
                  bbox: BBox) -> np.ndarray:
    """Stitch and crop multiple 1° tile arrays into a single array for *bbox*."""
    if not tiles:
        return np.array([], dtype=np.float32).reshape(0, 0)

    # Crop each tile to the bbox portion
    cropped: dict[Tuple[int, int], np.ndarray] = {}
    for (lat, lon), arr in tiles.items():
        c = _crop_to_bbox(arr, lat, lon, bbox)
        if c.size > 0:
            cropped[(lat, lon)] = c

    if not cropped:
        return np.array([], dtype=np.float32).reshape(0, 0)

    # Arrange: rows sorted by lat descending (north first), cols by lon ascending
    lats = sorted({k[0] for k in cropped}, reverse=True)
    lons = sorted({k[1] for k in cropped})

    rows = []
    for lat in lats:
        row_tiles = []
        for lon in lons:
            if (lat, lon) in cropped:
                row_tiles.append(cropped[(lat, lon)])
            else:
                # Missing tile — fill with NaN (matching shape from adjacent)
                ref_shape = next(
                    (cropped[(lat, lo)].shape for lo in lons if (lat, lo) in cropped),
                    (1, 1)
                )
                row_tiles.append(np.full(ref_shape, np.nan, dtype=np.float32))
        if row_tiles:
            rows.append(np.concatenate(row_tiles, axis=1))

    if not rows:
        return np.array([], dtype=np.float32).reshape(0, 0)

    return np.concatenate(rows, axis=0)


# ── Public API ───────────────────────────────────────────────────

class NDSMProvider:
    """nDSM = GLO-30 DSM − FABDEM DTM.  Global, ~30m, free, no API key."""

    name = "ndsm"

    def covers(self, bbox: BBox) -> bool:
        """nDSM is global (FABDEM covers ±80° latitude)."""
        north, south, _, _ = bbox
        return -80 <= south and north <= 80

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch nDSM for *bbox*, resample to *dim* = (H, W)."""
        from city2stl.skyline.height import _resample

        north, south, east, west = bbox
        cache_key = make_cache_key("ndsm", north, south, east, west,
                                   {"dim": list(dim)})

        # Check merged cache
        cached = read_array_cache("ndsm", cache_key)
        if cached is not None:
            arrays, meta = cached
            return HeightResult(
                raster=arrays["raster"],
                confidence=arrays["confidence"],
                source_name=self.name,
                resolution_m=meta.get("resolution_m", 30.0),
            )

        # Determine which 1° tiles we need
        tile_coords = _tiles_for_bbox(bbox)
        logger.info(f"nDSM: fetching {len(tile_coords)} tile(s) for bbox "
                    f"N={north:.3f} S={south:.3f} E={east:.3f} W={west:.3f}")

        # Download DSM and DTM tiles
        dsm_tiles: dict[Tuple[int, int], np.ndarray] = {}
        dtm_tiles: dict[Tuple[int, int], np.ndarray] = {}

        for lat, lon in tile_coords:
            dsm = _get_tile("glo30", lat, lon)
            dtm = _get_tile("fabdem", lat, lon)
            if dsm is not None:
                dsm_tiles[(lat, lon)] = dsm
            if dtm is not None:
                dtm_tiles[(lat, lon)] = dtm

        # Stitch into bbox-sized arrays
        dsm_stitched = _stitch_tiles(dsm_tiles, bbox)
        dtm_stitched = _stitch_tiles(dtm_tiles, bbox)

        if dsm_stitched.size == 0:
            logger.warning("nDSM: no DSM tiles available for bbox")
            raster = np.full(dim, np.nan, dtype=np.float32)
            conf = np.zeros(dim, dtype=np.float32)
            return HeightResult(raster, conf, self.name, 30.0)

        # Compute nDSM = DSM − DTM
        if dtm_stitched.size > 0 and dtm_stitched.shape == dsm_stitched.shape:
            ndsm = dsm_stitched - dtm_stitched
        elif dtm_stitched.size > 0:
            # Shapes differ — resample DTM to match DSM
            dtm_resampled = _resample(dtm_stitched, dsm_stitched.shape)
            ndsm = dsm_stitched - dtm_resampled
        else:
            # No FABDEM available — cannot compute nDSM, return NaN
            logger.warning("nDSM: FABDEM not available, returning NaN")
            ndsm = np.full(dsm_stitched.shape, np.nan, dtype=np.float32)

        # Clamp negative values (below-ground artefacts) to 0
        ndsm = np.where(ndsm < 0, 0.0, ndsm).astype(np.float32)

        # Resample to target dim
        raster = _resample(ndsm, dim)
        confidence = np.where(np.isnan(raster), 0.0, NDSM_CONFIDENCE).astype(np.float32)

        # Approximate resolution
        lat_span = north - south
        resolution_m = (lat_span * 111_000) / dim[0]  # metres per pixel

        result = HeightResult(raster, confidence, self.name, resolution_m)

        # Cache
        write_array_cache("ndsm", cache_key,
                          {"raster": raster, "confidence": confidence},
                          {"resolution_m": resolution_m})

        return result
