"""
Google 3D Tiles height provider — photogrammetric building heights.

Data: Cesium 3D Tiles (glTF/glb), sub-metre resolution.
Source: Google Map Tiles API (Photorealistic 3D Tiles).
Auth: Google Maps API key (``GOOGLE_MAPS_API_KEY`` env var or config.json).
Coverage: Major cities globally.

Extracts a Digital Surface Model (DSM) by ray-casting downward over the
photogrammetric mesh, then subtracts the terrain DEM to get building
height above ground.
"""

from __future__ import annotations

import io
import json
import logging
import math
import os
from pathlib import Path
from typing import Tuple

import numpy as np
import requests

from app.server.core.height import BBox, HeightProvider, HeightResult, _resample
from app.server.core.cache import (
    make_cache_key,
    write_array_cache,
    read_array_cache,
)

logger = logging.getLogger(__name__)

_CONFIDENCE = 0.9  # high — photogrammetric mesh
_RESOLUTION_M = 1.0  # sub-metre mesh, output at ~1 m
_NAMESPACE = "google3d"
_REQUEST_TIMEOUT = 30  # seconds per tile
_MAX_TILES = 200  # safety guard

# Register cache TTL
from app.server.core import cache as _cache_mod
_cache_mod.NAMESPACE_TTL.setdefault(_NAMESPACE, 30 * 86400)

_ROOT_URL = "https://tile.googleapis.com/v1/3dtiles/root.json"


# ── API key resolution ──────────────────────────────────────────

def _get_api_key() -> str | None:
    """Read Google Maps API key from env or config.json."""
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if key:
        return key
    try:
        cfg_path = Path(__file__).parent.parent.parent.parent.parent / "config.json"
        if cfg_path.exists():
            cfg = json.loads(cfg_path.read_text())
            return cfg.get("google_maps_api_key") or None
    except Exception:
        pass
    return None


# ── ECEF ↔ WGS84 transforms ────────────────────────────────────

# WGS84 ellipsoid constants
_A = 6378137.0           # semi-major axis (m)
_F = 1 / 298.257223563   # flattening
_B = _A * (1 - _F)       # semi-minor axis
_E2 = 1 - (_B / _A) ** 2  # first eccentricity squared


def ecef_to_wgs84(x: np.ndarray, y: np.ndarray,
                  z: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Convert ECEF (m) to WGS84 (lon°, lat°, alt_m).

    Uses Bowring's iterative method (converges in 2-3 iterations).
    """
    lon = np.degrees(np.arctan2(y, x))

    p = np.sqrt(x ** 2 + y ** 2)
    lat = np.arctan2(z, p * (1 - _E2))  # initial estimate

    for _ in range(5):
        sin_lat = np.sin(lat)
        N = _A / np.sqrt(1 - _E2 * sin_lat ** 2)
        lat = np.arctan2(z + _E2 * N * sin_lat, p)

    sin_lat = np.sin(lat)
    cos_lat = np.cos(lat)
    N = _A / np.sqrt(1 - _E2 * sin_lat ** 2)

    alt = np.where(
        np.abs(cos_lat) > 1e-10,
        p / cos_lat - N,
        np.abs(z) / np.abs(sin_lat) - N * (1 - _E2),
    )

    return lon, np.degrees(lat), alt


def wgs84_to_ecef(lon: float, lat: float,
                  alt: float = 0.0) -> tuple[float, float, float]:
    """Convert a single WGS84 point to ECEF (m)."""
    lon_r, lat_r = math.radians(lon), math.radians(lat)
    sin_lat = math.sin(lat_r)
    cos_lat = math.cos(lat_r)
    N = _A / math.sqrt(1 - _E2 * sin_lat ** 2)
    x = (N + alt) * cos_lat * math.cos(lon_r)
    y = (N + alt) * cos_lat * math.sin(lon_r)
    z = (N * (1 - _E2) + alt) * sin_lat
    return x, y, z


# ── Bounding-volume helpers ─────────────────────────────────────

def _bv_intersects_bbox(bounding_volume: dict,
                        bbox: BBox) -> bool:
    """Check whether a 3D Tiles bounding volume intersects *bbox*.

    Supports ``region`` (preferred) and ``box`` (ECEF OBB) types.
    For ``box`` we convert the centre to WGS84 and check containment.
    """
    north, south, east, west = bbox

    if "region" in bounding_volume:
        # region = [west, south, east, north, minHeight, maxHeight] in radians
        r = bounding_volume["region"]
        rw, rs, re, rn = [math.degrees(r[i]) for i in range(4)]
        return not (rn < south or rs > north or re < west or rw > east)

    if "box" in bounding_volume:
        b = bounding_volume["box"]
        cx, cy, cz = b[0], b[1], b[2]
        lon, lat, _alt = ecef_to_wgs84(
            np.array([cx]), np.array([cy]), np.array([cz]),
        )
        lon, lat = float(lon[0]), float(lat[0])
        # Half-axes give approximate extent; use rough 1° ≈ 111 km
        half_extent_m = max(
            math.sqrt(b[3] ** 2 + b[4] ** 2 + b[5] ** 2),
            math.sqrt(b[6] ** 2 + b[7] ** 2 + b[8] ** 2),
            math.sqrt(b[9] ** 2 + b[10] ** 2 + b[11] ** 2),
        )
        half_deg = half_extent_m / 111_000
        return not (lat + half_deg < south or lat - half_deg > north
                    or lon + half_deg < west or lon - half_deg > east)

    if "sphere" in bounding_volume:
        s = bounding_volume["sphere"]
        cx, cy, cz, radius = s[0], s[1], s[2], s[3]
        lon, lat, _alt = ecef_to_wgs84(
            np.array([cx]), np.array([cy]), np.array([cz]),
        )
        lon, lat = float(lon[0]), float(lat[0])
        half_deg = radius / 111_000
        return not (lat + half_deg < south or lat - half_deg > north
                    or lon + half_deg < west or lon - half_deg > east)

    # Unknown type — be conservative, assume it intersects
    return True


# ── Tileset traversal ───────────────────────────────────────────

def _collect_content_uris(
    tileset: dict,
    bbox: BBox,
    api_key: str,
    session: requests.Session,
    max_tiles: int = _MAX_TILES,
) -> list[str]:
    """Walk the 3D Tiles tree and collect content URIs for leaf tiles
    that intersect *bbox*.

    Returns absolute URLs for .glb content.
    """
    uris: list[str] = []

    def _walk(node: dict, base_url: str) -> None:
        if len(uris) >= max_tiles:
            return

        bv = node.get("boundingVolume", {})
        if bv and not _bv_intersects_bbox(bv, bbox):
            return

        # Leaf tile with content
        content = node.get("content", {})
        uri = content.get("uri") or content.get("url")
        if uri:
            if uri.startswith("http"):
                full = uri
            else:
                full = base_url.rsplit("/", 1)[0] + "/" + uri
            # Append API key
            sep = "&" if "?" in full else "?"
            full = f"{full}{sep}key={api_key}"

            # Check if this is a sub-tileset (.json)
            if uri.endswith(".json"):
                try:
                    r = session.get(full, timeout=_REQUEST_TIMEOUT)
                    r.raise_for_status()
                    sub = r.json()
                    root = sub.get("root")
                    if root:
                        _walk(root, full)
                except Exception as exc:
                    logger.debug("Failed to fetch sub-tileset %s: %s",
                                 uri, exc)
                return

            uris.append(full)

        # Recurse into children
        for child in node.get("children", []):
            if len(uris) >= max_tiles:
                return
            _walk(child, base_url)

    root = tileset.get("root")
    if root:
        _walk(root, _ROOT_URL)

    return uris


# ── Mesh → DSM ray-casting ──────────────────────────────────────

def _meshes_to_dsm(
    meshes: list,
    bbox: BBox,
    dim: Tuple[int, int],
) -> np.ndarray:
    """Ray-cast downward over *meshes* (in ECEF) to build a DSM in WGS84.

    Returns a (H, W) float32 array of altitudes in metres (WGS84 ellipsoidal).
    Pixels with no mesh intersection are NaN.
    """
    import trimesh

    north, south, east, west = bbox
    h, w = dim

    if not meshes:
        return np.full((h, w), np.nan, dtype=np.float32)

    # Merge all meshes
    combined = trimesh.util.concatenate(meshes)

    # Build ray origins on a regular lat/lon grid, at a high altitude
    lats = np.linspace(north, south, h)
    lons = np.linspace(west, east, w)
    lon_grid, lat_grid = np.meshgrid(lons, lats)

    # Convert grid to ECEF at a high altitude (above any building)
    ray_alt = 10_000.0  # 10 km
    lon_flat = lon_grid.ravel()
    lat_flat = lat_grid.ravel()

    origins = np.zeros((len(lon_flat), 3), dtype=np.float64)
    for i in range(len(lon_flat)):
        origins[i] = wgs84_to_ecef(lon_flat[i], lat_flat[i], ray_alt)

    # Ray directions: inward toward Earth centre (≈ straight down in ECEF)
    # For each origin, direction = -normalize(origin) (pointing toward centre)
    norms = np.linalg.norm(origins, axis=1, keepdims=True)
    directions = -origins / norms

    # Ray-cast
    try:
        locations, index_ray, _ = combined.ray.intersects_location(
            ray_origins=origins,
            ray_directions=directions,
            multiple_hits=True,
        )
    except Exception as exc:
        logger.warning("Ray-cast failed: %s", exc)
        return np.full((h, w), np.nan, dtype=np.float32)

    if len(locations) == 0:
        return np.full((h, w), np.nan, dtype=np.float32)

    # Convert hit locations to WGS84 to get altitude
    hit_lon, hit_lat, hit_alt = ecef_to_wgs84(
        locations[:, 0], locations[:, 1], locations[:, 2],
    )

    # For each ray, take the HIGHEST altitude hit (roof, not ground)
    dsm = np.full(len(lon_flat), np.nan, dtype=np.float64)
    for i, ray_idx in enumerate(index_ray):
        alt = hit_alt[i]
        if np.isnan(dsm[ray_idx]) or alt > dsm[ray_idx]:
            dsm[ray_idx] = alt

    return dsm.reshape(h, w).astype(np.float32)


# ── HeightProvider implementation ────────────────────────────────

class Google3DProvider:
    """Google Photorealistic 3D Tiles building height provider."""

    name = "google3d"

    def __init__(self, api_key: str | None = None,
                 max_tiles: int = _MAX_TILES):
        self._api_key = api_key or _get_api_key()
        self._max_tiles = max_tiles

    def covers(self, bbox: BBox) -> bool:
        """Returns True if an API key is configured (coverage is global
        for major cities, but we can't know without querying)."""
        return self._api_key is not None

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int],
                      dem: np.ndarray | None = None) -> HeightResult:
        """Fetch 3D Tiles for *bbox*, ray-cast DSM, subtract *dem*.

        Parameters
        ----------
        bbox : (north, south, east, west)
        dim  : (H, W) target output shape
        dem  : Optional terrain DEM array (H, W) in metres (WGS84 ellipsoidal
               altitude).  If provided, building height = DSM - DEM.
               If None, returns raw DSM altitude (still useful as a
               height raster).
        """
        if not self._api_key:
            logger.warning("Google 3D Tiles: no API key configured")
            return _empty_result(dim)

        north, south, east, west = bbox

        # Check cache first
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   extra={"dim": list(dim)})
        hit = read_array_cache(_NAMESPACE, cache_key)
        if hit is not None:
            arrays, _meta = hit
            raster = arrays.get("height")
            if raster is not None:
                confidence = np.where(np.isnan(raster), 0.0,
                                      _CONFIDENCE).astype(np.float32)
                return HeightResult(
                    raster=raster,
                    confidence=confidence,
                    source_name=self.name,
                    resolution_m=_RESOLUTION_M,
                )

        # Fetch tileset
        session = requests.Session()
        try:
            root_url = f"{_ROOT_URL}?key={self._api_key}"
            r = session.get(root_url, timeout=_REQUEST_TIMEOUT)
            r.raise_for_status()
            tileset = r.json()
        except Exception as exc:
            logger.warning("Google 3D Tiles: failed to fetch root: %s", exc)
            return _empty_result(dim)

        # Collect content URIs
        uris = _collect_content_uris(tileset, bbox, self._api_key, session,
                                     max_tiles=self._max_tiles)
        if not uris:
            logger.info("Google 3D Tiles: no tiles found for bbox")
            return _empty_result(dim)

        logger.info("Google 3D Tiles: downloading %d tiles", len(uris))

        # Download and parse meshes
        import trimesh
        meshes = []
        for uri in uris:
            try:
                resp = session.get(uri, timeout=_REQUEST_TIMEOUT)
                resp.raise_for_status()
                scene_or_mesh = trimesh.load(
                    io.BytesIO(resp.content),
                    file_type="glb",
                    process=False,
                )
                if isinstance(scene_or_mesh, trimesh.Scene):
                    for geom in scene_or_mesh.geometry.values():
                        if isinstance(geom, trimesh.Trimesh):
                            meshes.append(geom)
                elif isinstance(scene_or_mesh, trimesh.Trimesh):
                    meshes.append(scene_or_mesh)
            except Exception as exc:
                logger.debug("Failed to load tile: %s", exc)
                continue

        if not meshes:
            logger.info("Google 3D Tiles: no valid meshes parsed")
            return _empty_result(dim)

        # Ray-cast to get DSM
        dsm = _meshes_to_dsm(meshes, bbox, dim)

        # Convert DSM to building height if DEM provided
        if dem is not None:
            dem_resampled = _resample(dem.astype(np.float32), dim)
            raster = dsm - dem_resampled
            # Clamp negative heights to 0 (mesh below terrain = ground)
            raster = np.where(raster < 0, 0.0, raster).astype(np.float32)
            # Where DSM is NaN, building height is NaN
            raster[np.isnan(dsm)] = np.nan
        else:
            raster = dsm

        confidence = np.where(np.isnan(raster), 0.0,
                              _CONFIDENCE).astype(np.float32)

        # Cache result
        write_array_cache(_NAMESPACE, cache_key, {"height": raster},
                          metadata={"n_tiles": len(uris),
                                    "resolution_m": _RESOLUTION_M})

        return HeightResult(
            raster=raster,
            confidence=confidence,
            source_name=self.name,
            resolution_m=_RESOLUTION_M,
        )


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="google3d",
        resolution_m=_RESOLUTION_M,
    )
