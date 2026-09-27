"""Measured building heights from USGS 3DEP lidar point clouds (nDSM = DSM - DEM).

Grid-aligned nDSM for city-STL registration (``city2stl.registration`` passes it
to ``numpy2stl.registration.register_city_stl`` for ``height_source="lidar"``).
Not a registry provider: the per-building COPC reader is ``lidar_3dep_copc`` and
the OpenTopography COP30 - SRTM provider is ``lidar_3dep``.

Why: OSM building heights are unreliable — ~31% are the `default_height` fill and
many come from `building:levels x 3.5`.  A normalized DSM (first-return surface
minus bare-earth terrain) gives a *measured* height for every footprint.

This module is OPTIONAL and US-only (3DEP coverage).  It needs heavy geospatial
deps that are not part of the base install:

    conda install -c conda-forge pdal python-pdal py3dep rioxarray

`get_ndsm()` returns None when the deps are missing or the bbox is outside 3DEP
coverage, so callers fall back to OSM tags transparently.

Pipeline
--------
1. DSM: read USGS 3DEP lidar from the public Entwine Point Tiles (EPT) endpoint
   with PDAL, crop to the bbox, grid the FIRST-RETURN max-Z per cell.
2. DEM: bare-earth terrain via py3dep.get_dem (3DEP 1m/10m).
3. nDSM = DSM - DEM, resampled to the OSM grid (N,S,E,W, resolution), row 0 = south
   like ``city2stl.osm_raster``.  Cached under ``geo2stl.cache.CACHE_ROOT / "ndsm_ept"``.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

import numpy as np

from geo2stl import cache as _geo_cache
from geo2stl.geo import m_per_deg_lon

logger = logging.getLogger(__name__)

# USGS public 3DEP EPT resource index (project boundaries -> ept.json URLs).
_USGS_EPT_RESOURCES = (
    "https://raw.githubusercontent.com/hobuinc/usgs-lidar/master/boundaries/resources.geojson"
)


def _deps():
    try:
        import pdal  # noqa: F401
        import py3dep  # noqa: F401
        return True
    except Exception:
        return False


def _ndsm_cache_path(N, S, E, W, resolution) -> Path:
    key = f"ndsm_{N:.5f}_{S:.5f}_{E:.5f}_{W:.5f}_r{resolution}"
    return Path(_geo_cache.CACHE_ROOT) / "ndsm_ept" / f"{hashlib.md5(key.encode()).hexdigest()[:16]}.npz"


def _find_ept_url(N, S, E, W):
    """Best-effort lookup of a 3DEP EPT resource covering the bbox centre."""
    try:
        import requests
        from shapely.geometry import Point, shape
        gj = requests.get(_USGS_EPT_RESOURCES, timeout=30).json()
        c = Point((E + W) / 2.0, (N + S) / 2.0)
        for feat in gj.get("features", []):
            try:
                if shape(feat["geometry"]).contains(c):
                    name = feat["properties"].get("name") or feat["properties"].get("id")
                    if name:
                        return (f"https://s3-us-west-2.amazonaws.com/usgs-lidar-public/"
                                f"{name}/ept.json")
            except Exception:
                continue
    except Exception as exc:
        logger.debug("EPT resource lookup failed: %s", exc)
    return None


def get_ndsm(bbox, resolution: int = 512, cache: bool = True, ept_url: str | None = None):
    """
    Return an (resolution, resolution) nDSM (metres above ground) aligned to the
    OSM grid for ``bbox`` = (N, S, E, W), or None if unavailable.

    Pass ``ept_url`` to skip the resource lookup and read a specific EPT.
    """
    N, S, E, W = bbox
    if cache:
        cp = _ndsm_cache_path(N, S, E, W, resolution)
        if cp.exists():
            logger.info("Loading nDSM from cache: %s", cp.name)
            return np.load(cp)["ndsm"]

    if not _deps():
        logger.warning("nDSM unavailable: pdal/py3dep not installed "
                       "(conda install -c conda-forge pdal python-pdal py3dep). "
                       "Falling back to OSM heights.")
        return None

    import json

    import pdal
    import py3dep

    ept = ept_url or _find_ept_url(N, S, E, W)
    if ept is None:
        logger.warning("No 3DEP EPT resource found for bbox (outside US coverage?); "
                       "falling back to OSM heights.")
        return None

    try:
        # --- DSM via PDAL: crop EPT to bbox, keep first returns, grid max-Z ---
        # EPT is EPSG:3857; bbox is lon/lat — PDAL reprojects via the bounds filter.
        pipeline = {
            "pipeline": [
                {"type": "readers.ept", "filename": ept,
                 "bounds": f"([{W},{E}],[{S},{N}])", "spatialreference": "EPSG:4326"},
                {"type": "filters.range", "limits": "ReturnNumber[1:1]"},
                {"type": "filters.reprojection", "out_srs": "EPSG:4326"},
            ]
        }
        p = pdal.Pipeline(json.dumps(pipeline))
        p.execute()
        arr = p.arrays[0]
        xs, ys, zs = arr["X"], arr["Y"], arr["Z"]
        # grid first-return max-Z to the OSM cells (north-up: row 0 = south flip later)
        from scipy.stats import binned_statistic_2d
        dsm, _, _, _ = binned_statistic_2d(
            xs, ys, zs, statistic="max",
            bins=[resolution, resolution], range=[[W, E], [S, N]])
        # binned_statistic_2d indexes [x, y] with y ascending, so the transpose is
        # already row0=south (city2stl.osm_raster); a flipud here mirrored the DSM
        # against the DEM below.
        dsm = dsm.T

        # --- DEM via py3dep (bare earth) on the same grid ---
        cell_m = (E - W) * m_per_deg_lon((N + S) / 2) / resolution
        dem_da = py3dep.get_dem((W, S, E, N), resolution=max(1, int(cell_m)))
        import rioxarray  # noqa: F401
        dem = dem_da.rio.reproject(
            "EPSG:4326",
            shape=(resolution, resolution),
        ).values.astype(np.float64)
        dem = np.flipud(dem)

        ndsm = dsm - dem
        ndsm[~np.isfinite(ndsm)] = np.nan
        ndsm[ndsm < 0] = 0.0  # height above ground is non-negative

        if cache:
            cp.parent.mkdir(parents=True, exist_ok=True)
            np.savez_compressed(cp, ndsm=ndsm)
            logger.info("Cached nDSM: %s", cp.name)
        logger.info("nDSM ready: %d x %d, median building-ish height %.1f m",
                    resolution, resolution, float(np.nanmedian(ndsm[ndsm > 2])))
        return ndsm
    except Exception as exc:
        logger.warning("nDSM build failed (%s); falling back to OSM heights.", exc)
        return None
