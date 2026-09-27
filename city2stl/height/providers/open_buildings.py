"""
Overture Maps buildings height provider.

Data: Overture Maps buildings theme -- footprints carrying ``height`` and
``num_floors``, conflated from OSM, Esri, Microsoft and Google Open Buildings
among other sources.

- Access: anonymous S3 parquet, located through the Overture STAC catalog
- Coverage: global, though the height column is populated unevenly
- Resolution: per-building footprint
- License: CDLA Permissive 2.0 / ODbL, by source

The module is named ``open_buildings`` and long claimed to fetch Google Open
Buildings, which it does not. The name is kept because the provider namespace
is written into cache keys and region reports; the description is corrected
instead.

Measured against the Miami reference plate: MAE 20.95 m, correlation +0.866
over 61 footprints. The accuracy holds on buildings OSM does not tag, so the
height column carries real information rather than echoing the OSM tag.

Two limits decide when to reach for this source. Coverage is thin outside
well-mapped cities -- Cartagena yields a height for 9% of registered
footprints, against 26% of grid pixels in Miami -- and footprints without a
height fall back to ``num_floors`` times a nominal storey height.
"""

from __future__ import annotations

import functools
import logging

import numpy as np
import requests

from city2stl.height import BBox, HeightResult, _resample

from ._cache import (
    make_cache_key,
    read_height_result,
    register_ttl,
    write_height_result,
)

logger = logging.getLogger(__name__)

register_ttl("open_buildings", 90)

_CONFIDENCE = 0.6  # ML-derived heights, reasonable but not survey-grade
_RESOLUTION_M = 5.0  # effective per-building resolution
_NAMESPACE = "open_buildings"
_DOWNLOAD_TIMEOUT = 120
_STAC_CATALOG = "https://stac.overturemaps.org/catalog.json"
_STAC_BUILDING_COLLECTION = "https://stac.overturemaps.org/{release}/buildings/building/collection.json"
_DEFAULT_RELEASE = "2026-04-15.0"



def _is_in_coverage(bbox: BBox) -> bool:
    """Overture is global, so every bbox is covered."""
    # Overture publishes globally, so there is nothing to gate on. The region
    # list that used to be tested here was Google Open Buildings' footprint --
    # Africa, South and Southeast Asia, Latin America, the Middle East -- which
    # does not describe what this provider fetches. It admitted Miami only
    # because the Latin America box reaches north to 33 degrees.
    return True


@functools.lru_cache(maxsize=1)
def _latest_overture_release() -> str:
    """Resolve the latest Overture release from STAC with a stable fallback."""
    try:
        response = requests.get(_STAC_CATALOG, timeout=30)
        response.raise_for_status()
        payload = response.json()
        latest = str(payload.get("latest") or "").strip()
        if latest:
            return latest
    except Exception as exc:
        logger.debug("Overture STAC latest release lookup failed: %s", exc)
    return _DEFAULT_RELEASE


@functools.lru_cache(maxsize=4)
def _overture_building_partitions(release: str) -> tuple[dict, ...]:
    """Load Overture STAC building partitions and cache their bbox + asset path."""
    collection_url = _STAC_BUILDING_COLLECTION.format(release=release)
    response = requests.get(collection_url, timeout=60)
    response.raise_for_status()
    collection = response.json()

    partitions: list[dict] = []
    session = requests.Session()
    base_url = collection_url.rsplit("/", 1)[0] + "/"
    for link in collection.get("links") or []:
        if link.get("rel") != "item":
            continue
        href = str(link.get("href") or "")
        if not href:
            continue
        item_url = href if href.startswith("http") else base_url + href.replace("./", "")
        item_resp = session.get(item_url, timeout=60)
        item_resp.raise_for_status()
        item = item_resp.json()
        bbox = item.get("bbox") or []
        if len(bbox) != 4:
            continue
        west, south, east, north = [float(v) for v in bbox]
        assets = item.get("assets") or {}
        aws_asset = assets.get("aws") or {}
        s3_alt = ((aws_asset.get("alternate") or {}).get("s3") or {}).get("href")
        s3_href = str(s3_alt or "")
        if not s3_href.startswith("s3://"):
            continue
        partitions.append({
            "west": west,
            "south": south,
            "east": east,
            "north": north,
            "s3_path": s3_href.replace("s3://", "", 1),
        })
    return tuple(partitions)


def _intersecting_partitions(bbox: BBox) -> tuple[dict, ...]:
    north, south, east, west = bbox
    release = _latest_overture_release()
    matches = []
    for part in _overture_building_partitions(release):
        if south < part["north"] and north > part["south"] and west < part["east"] and east > part["west"]:
            matches.append(part)
    return tuple(matches)


def _fetch_buildings_for_bbox(bbox: BBox, dim: tuple[int, int]) -> np.ndarray | None:
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
        from pyarrow.fs import S3FileSystem
        from shapely import wkb as shapely_wkb
    except ImportError as exc:
        logger.debug("pyarrow[s3] not installed - skipping Overture: %s", exc)
        return None

    north, south, east, west = bbox
    h, w = dim

    try:
        fs = S3FileSystem(anonymous=True, region="us-west-2")
        partitions = _intersecting_partitions(bbox)
        if not partitions:
            logger.debug("Overture STAC has no intersecting partitions for bbox %s", bbox)
            return None

        grid = np.full((h, w), np.nan, dtype=np.float32)
        lon_step = (east - west) / max(w, 1)
        lat_step = (north - south) / max(h, 1)

        for part in partitions:
            dataset = ds.dataset(part["s3_path"], filesystem=fs, format="parquet")
            filt = (
                (ds.field(("bbox", "xmin")) <= float(east))
                & (ds.field(("bbox", "xmax")) >= float(west))
                & (ds.field(("bbox", "ymin")) <= float(north))
                & (ds.field(("bbox", "ymax")) >= float(south))
            )
            table = dataset.to_table(
                columns=["geometry", "height", "num_floors"],
                filter=filt,
            )

            if len(table) == 0:
                continue

            geom_col = table.column("geometry").to_pylist()
            height_col = table.column("height").to_pylist()
            floors_col = table.column("num_floors").to_pylist()

            for geom_bytes, bld_height, bld_floors in zip(geom_col, height_col, floors_col, strict=False):
                if geom_bytes is None:
                    continue
                bld_h = (
                    float(bld_height) if bld_height is not None
                    else (float(bld_floors) * 3.0 if bld_floors else None)
                )
                if bld_h is None or bld_h <= 0:
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
        logger.warning("Overture S3 fetch failed: %s", exc)
        return None


class OpenBuildingsProvider:
    """Overture Maps buildings — footprint heights from a conflated source."""

    name = "open_buildings"

    def covers(self, bbox: BBox) -> bool:
        """True everywhere: Overture publishes worldwide."""
        return _is_in_coverage(bbox)

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int]) -> HeightResult:
        """Fetch and rasterize Overture building heights for bbox."""
        north, south, east, west = bbox
        cache_key = make_cache_key(_NAMESPACE, north, south, east, west,
                                   {"dim": list(dim)})

        hit = read_height_result(_NAMESPACE, cache_key, self.name, _RESOLUTION_M)
        if hit is not None:
            return hit

        raster = _fetch_buildings_for_bbox(bbox, dim)

        if raster is None:
            return _empty_result(dim)

        if raster.shape != dim:
            raster = _resample(raster, dim)

        confidence = np.where(
            np.isnan(raster), 0.0, _CONFIDENCE
        ).astype(np.float32)

        result = HeightResult(raster, confidence, self.name, _RESOLUTION_M)

        write_height_result(_NAMESPACE, cache_key, result)
        return result


def _empty_result(dim: tuple[int, int]) -> HeightResult:
    return HeightResult.empty(dim, "open_buildings", _RESOLUTION_M)
