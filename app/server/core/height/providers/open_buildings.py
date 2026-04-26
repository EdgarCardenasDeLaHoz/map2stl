"""
Google Open Buildings 2.5D provider.

Data: Google Open Buildings — V3 with height attributes
- ML-derived building footprints + estimated height
- Coverage: Africa, South/Southeast Asia, Latin America, Caribbean, Middle East
- Access: Google Cloud Storage (public, no key)
- Format: CSV with WKT geometry + height columns
- Resolution: Per-building footprint, ~1-5m accuracy
- License: CC-BY-4.0

The Open Buildings dataset provides individual building polygons with
height estimates derived from satellite imagery. We rasterize these
into a grid matching the target dimensions.

Note: Coverage is limited to developing regions. For Europe/US/Japan,
other providers (3DEP, Copernicus, nDSM) are more appropriate.
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

from app.server.core.height import BBox, HeightResult, _resample
from app.server.core.cache import (
    make_cache_key, read_array_cache, write_array_cache,
    NAMESPACE_TTL,
)

logger = logging.getLogger(__name__)

NAMESPACE_TTL.setdefault("open_buildings", 90 * 86400)

_CONFIDENCE = 0.6  # ML-derived heights, reasonable but not survey-grade
_RESOLUTION_M = 5.0  # effective per-building resolution
_NAMESPACE = "open_buildings"
_DOWNLOAD_TIMEOUT = 120

# Open Buildings V3 is distributed as country-level CSV files on GCS.
# For bbox-based access, the community Overture Maps GERS or
# Microsoft Planetary Computer STAC endpoint is simpler.
# We use Overture Maps S3 distribution which includes Open Buildings data.
_OVERTURE_BASE = (
    "https://overturemaps-us-west-2.s3.us-west-2.amazonaws.com/"
    "release/2024-07-22.0/theme=buildings/type=building/"
)

# Coverage regions (approximate bboxes where Open Buildings has data)
_COVERAGE_REGIONS = [
    # Africa
    {"name": "africa", "n": 37, "s": -35, "e": 52, "w": -18},
    # South Asia
    {"name": "south_asia", "n": 38, "s": 5, "e": 98, "w": 60},
    # Southeast Asia
    {"name": "southeast_asia", "n": 28, "s": -11, "e": 150, "w": 92},
    # Latin America & Caribbean
    {"name": "latam", "n": 33, "s": -55, "e": -34, "w": -118},
    # Middle East
    {"name": "middle_east", "n": 42, "s": 12, "e": 63, "w": 25},
]


def _is_in_coverage(bbox: BBox) -> bool:
    """Check if bbox overlaps any Open Buildings coverage region."""
    north, south, east, west = bbox
    for region in _COVERAGE_REGIONS:
        if (south < region["n"] and north > region["s"] and
                west < region["e"] and east > region["w"]):
            return True
    return False


def _fetch_buildings_for_bbox(bbox: BBox, dim: Tuple[int, int]) -> np.ndarray | None:
    """Fetch building heights from Overture Maps GeoParquet on S3 and rasterize.

    Uses pyarrow.dataset with an anonymous S3FileSystem to scan the Overture
    Maps buildings parquet files for the given bounding box.  PyArrow 14+
    pushes the bbox filter expression down to parquet row-group statistics,
    so only matching files and row-groups are read.

    Requires the optional dependencies: pyarrow[s3] (includes s3fs/fsspec).
    Returns None gracefully when the dependencies are absent or the fetch fails.
    """
    try:
        import pyarrow.dataset as ds
        import pyarrow.compute as pc
        from pyarrow.fs import S3FileSystem
        from shapely import wkb as shapely_wkb
    except ImportError as exc:
        logger.debug("pyarrow[s3] not installed - skipping Open Buildings: %s", exc)
        return None

    north, south, east, west = bbox
    h, w = dim

    try:
        fs = S3FileSystem(anonymous=True, region="us-west-2")
        s3_path = (
            "overturemaps-us-west-2/release/2024-07-22.0/"
            "theme=buildings/type=building/"
        )
        dataset = ds.dataset(s3_path, filesystem=fs, format="parquet")

        # Build a filter expression using pyarrow.compute struct field access.
        # Overture Maps 2024 buildings parquet stores bbox as a struct column
        # with subfields minx/miny/maxx/maxy.  PyArrow 14+ supports predicate
        # pushdown for struct subfields when row-group statistics are available.
        bbox_field = ds.field("bbox")
        filt = pc.and_(
            pc.and_(
                pc.less_equal(pc.struct_field(bbox_field, "minx"), float(east)),
                pc.greater_equal(pc.struct_field(bbox_field, "maxx"), float(west)),
            ),
            pc.and_(
                pc.less_equal(pc.struct_field(bbox_field, "miny"), float(north)),
                pc.greater_equal(pc.struct_field(bbox_field, "maxy"), float(south)),
            ),
        )

        table = dataset.to_table(
            columns=["geometry", "height", "num_floors"],
            filter=filt,
        )

        if len(table) == 0:
            logger.debug("No buildings found in bbox %s", bbox)
            return None

        grid = np.full((h, w), np.nan, dtype=np.float32)
        lon_step = (east - west) / max(w, 1)
        lat_step = (north - south) / max(h, 1)

        geom_col   = table.column("geometry").to_pylist()
        height_col = table.column("height").to_pylist()
        floors_col = table.column("num_floors").to_pylist()

        for geom_bytes, bld_height, bld_floors in zip(geom_col, height_col, floors_col):
            if geom_bytes is None:
                continue
            bld_h = (
                float(bld_height) if bld_height is not None
                else (float(bld_floors) * 3.0 if bld_floors else None)
            )
            if bld_h is None:
                continue
            try:
                geom = shapely_wkb.loads(bytes(geom_bytes))
            except Exception:
                continue

            gx0, gy0, gx1, gy1 = geom.bounds
            c0 = max(0, int((gx0 - west) / lon_step))
            c1 = min(w, int((gx1 - west) / lon_step) + 1)
            r0 = max(0, int((north - gy1) / lat_step))
            r1 = min(h, int((north - gy0) / lat_step) + 1)
            if c0 >= c1 or r0 >= r1:
                continue
            patch = grid[r0:r1, c0:c1]
            grid[r0:r1, c0:c1] = np.where(
                np.isnan(patch), bld_h, np.maximum(patch, bld_h)
            )

        if np.all(np.isnan(grid)):
            return None
        return grid

    except Exception as exc:
        logger.warning("Open Buildings S3 fetch failed: %s", exc)
        return None


class OpenBuildingsProvider:
    """Google Open Buildings 2.5D — ML-derived building heights."""

    name = "open_buildings"

    def covers(self, bbox: BBox) -> bool:
        """Returns True if bbox overlaps Open Buildings coverage."""
        return _is_in_coverage(bbox)

    def fetch_heights(self, bbox: BBox, dim: Tuple[int, int]) -> HeightResult:
        """Fetch and rasterize Open Buildings heights for bbox."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        cached = read_array_cache(_NAMESPACE, cache_key)
        if cached is not None:
            arrays, meta = cached
            return HeightResult(
                raster=arrays["raster"],
                confidence=arrays["confidence"],
                source_name=self.name,
                resolution_m=meta.get("resolution_m", _RESOLUTION_M),
            )

        raster = _fetch_buildings_for_bbox(bbox, dim)

        if raster is None:
            return _empty_result(dim)

        if raster.shape != dim:
            raster = _resample(raster, dim)

        confidence = np.where(
            np.isnan(raster), 0.0, _CONFIDENCE
        ).astype(np.float32)

        result = HeightResult(raster, confidence, self.name, _RESOLUTION_M)

        write_array_cache(_NAMESPACE, cache_key,
                          {"raster": raster, "confidence": confidence},
                          {"resolution_m": _RESOLUTION_M})
        return result


def _empty_result(dim: Tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="open_buildings",
        resolution_m=_RESOLUTION_M,
    )
