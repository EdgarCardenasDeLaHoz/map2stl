"""
GlobalBuildingAtlas (GBA) building height provider.

Data: GBA.LoD1 -- 2.75 billion building footprints carrying a height derived
from GBA.Height, a global 3 m building height raster built at TUM from Sentinel
and stereo imagery. Published with the ESSD 2025 paper.

- Access: anonymous S3 parquet on Source Cooperative
- Coverage: global and complete, by design -- this is its whole point
- Resolution: 3 m, the native GBA.Height grid, aggregated per footprint
- License: CC-BY 4.0 (the ODbL-derived polygons ship as a separate product)

Why the Source Cooperative mirror and not the upstream hosts. The originals are
5 degree tiles: GeoJSON on HuggingFace and zipped GeoTIFF on mediaTUM, both
between 20 MB and 1.7 GB per tile, and neither carries a spatial index, so
serving a 3 km window would mean downloading a whole continent-scale file. The
Source Cooperative copy is the same 922 tiles converted to GeoParquet with
per-row-group ``bbox`` statistics, which pyarrow pushes down: a 3 km window over
Cartagena reads 21,577 footprints in about three seconds.

Note that the parquet stores coordinates in WGS84 degrees, matching the bbox
convention used everywhere else in this project, even though the upstream
README states EPSG:3857. The values were checked against known city centres
rather than taken on trust.

This provider exists for the cities the better sources do not reach. Overture
finds a height for 9 % of Cartagena's registered footprints and OSM tags almost
none, which is why Cartagena exports as a field of 10 m boxes. GBA is complete
there.

Accuracy, measured against Miami's OSM-tagged heights on the export grid (the
warped vendor plate is useless for this -- see the note below):

    source     n       MAE      bias      corr
    Overture   45641   5.86 m   +0.49 m   +0.912
    GBA        46263   25.90 m  -11.70 m  +0.489

    restricted to buildings above 50 m
    Overture   10667   11.16 m  -7.07 m   +0.862
    GBA        10082   67.55 m  -67.42 m  +0.380

So GBA is markedly the weaker source, and its error is not spread evenly: it
flattens tall buildings, losing 67 m of height on Miami's towers. That is why
it is registered *below* Overture. It fills where Overture is silent; it does
not overrule it.

What makes it still worth having is that its weakness sits in a regime the
target cities do not occupy. Above 50 m, GBA covers 18.06 % of Miami's
footprint pixels, 0.31 % of Cartagena's and 0.00 % of Tunis's -- Tunis's
tallest reading in the export window is 27 m. A source that fails on skyscrapers
is a poor choice for Miami, which has other options, and an acceptable one for
a low-rise Caribbean or North African city, which has none.

Do not raise the confidence on the strength of the MAE alone without re-checking
the tall-building row; a city with real towers will expose it.

Two things deliberately not done here:

- The parquet carries a per-building ``var`` column (height variance, -1.0 being
  the unknown sentinel, so it must not be read as a bad reading). It is not used
  to scale confidence. Bug 3 in ``strm2stl/docs/issues.md`` is what happens when
  a provider's ranking is tuned on an argument rather than a measurement.
- No correction is applied for the tall-building deficit. It is a real, measured,
  consistent bias and it is tempting to fit it out, but it has been measured in
  exactly one city, and a curve fitted on Miami alone would be the same mistake
  as the per-city ``roof:height`` calibration that turned out biased differently
  in every city.

A note on references, because it cost time. Scoring this provider against the
registered vendor plate ranked it *above* Overture (MAE 22.0 against 39.8) and
gave both a correlation near 0.1. Both warped plate modes carry a registration
error -- "truth" is known misaligned and ``register_transform``'s scale is about
7 % off -- and a misregistered reference flatters a flat prediction and punishes
a sharp one, which inverted the ranking. The tell was that GBA and Overture
agreed with each other far better (+0.519) than either agreed with the plate.
``plate["osm_heights"]`` is never warped and shares the provider's own grid, so
its registration error is zero by construction; it is the reference to use.
"""

from __future__ import annotations

import functools
import logging
import math

import numpy as np

from city2stl.skyline.height import BBox, HeightResult, _resample

from ._cache import (
    make_cache_key,
    read_height_result,
    register_ttl,
    write_height_result,
)

logger = logging.getLogger(__name__)

register_ttl("gba", 90)

_CONFIDENCE = 0.55   # below Overture's 0.60: complete, but not validated here
_RESOLUTION_M = 3.0  # native GBA.Height grid, aggregated per footprint
_NAMESPACE = "gba"

_ENDPOINT = "data.source.coop"
_PREFIX = "tge-labs/globalbuildingatlas-lod1"
_TILE_DEG = 5


def _tile_name(west: int, north: int, east: int, south: int) -> str:
    """Build the tile filename for one 5 degree cell.

    The published order is west, north, east, south. Longitudes take three
    digits and latitudes two, and the sign lives in the prefix letter rather
    than in a minus: zero is written ``e000`` / ``n00``, so the test is on
    negativity, not on truthiness.
    """
    def lon(v: int) -> str:
        return f"{'w' if v < 0 else 'e'}{abs(v):03d}"

    def lat(v: int) -> str:
        return f"{'s' if v < 0 else 'n'}{abs(v):02d}"

    return f"{lon(west)}_{lat(north)}_{lon(east)}_{lat(south)}.parquet"


@functools.lru_cache(maxsize=1)
def _available_tiles() -> frozenset[str]:
    """List the tiles that actually exist, once per process.

    GBA is global but not every 5 degree cell is published -- open ocean cells
    are simply absent. Listing the bucket costs one request and removes the
    need to keep a 922-entry table in sync with the upstream release.
    """
    try:
        from pyarrow.fs import FileSelector, S3FileSystem
    except ImportError as exc:
        logger.debug("pyarrow[s3] not installed - skipping GBA: %s", exc)
        return frozenset()

    try:
        fs = S3FileSystem(anonymous=True, endpoint_override=_ENDPOINT, scheme="https")
        infos = fs.get_file_info(FileSelector(_PREFIX, recursive=False))
        return frozenset(
            info.path.rsplit("/", 1)[-1]
            for info in infos
            if info.path.endswith(".parquet")
        )
    except Exception as exc:
        logger.warning("GBA tile listing failed: %s", exc)
        return frozenset()


def _intersecting_tiles(bbox: BBox) -> tuple[str, ...]:
    """Return the published tiles overlapping *bbox*.

    Cells are floored to the 5 degree grid, so a bbox straddling a tile edge
    yields both neighbours.
    """
    north, south, east, west = bbox
    available = _available_tiles()
    if not available:
        return ()

    lon0 = int(math.floor(west / _TILE_DEG) * _TILE_DEG)
    lon1 = int(math.floor((east - 1e-9) / _TILE_DEG) * _TILE_DEG)
    lat0 = int(math.floor(south / _TILE_DEG) * _TILE_DEG)
    lat1 = int(math.floor((north - 1e-9) / _TILE_DEG) * _TILE_DEG)

    names = []
    for lon in range(lon0, lon1 + 1, _TILE_DEG):
        for lat in range(lat0, lat1 + 1, _TILE_DEG):
            name = _tile_name(lon, lat + _TILE_DEG, lon + _TILE_DEG, lat)
            if name in available:
                names.append(name)
    return tuple(names)


def _fetch_buildings_for_bbox(bbox: BBox, dim: tuple[int, int]) -> np.ndarray | None:
    """Fetch GBA footprint heights for *bbox* and rasterize them to *dim*.

    Footprints are burned as polygons rather than as bounding boxes. Overture's
    provider fills each footprint's bbox because its footprint count is modest;
    GBA returns twenty thousand buildings over a 3 km window, and at that
    density bbox fill closes the courtyards and alleys between them, which is
    exactly the "measures the block, not the roof" failure that bug 3 was
    about. Shapes are burned tallest-last so an overlap keeps the taller
    building, matching the ``np.maximum`` the bbox-fill path uses.

    Returns None when the optional dependencies are absent or the fetch fails,
    which the caller turns into an empty result rather than an error.
    """
    try:
        import pyarrow.dataset as ds
        from pyarrow.fs import S3FileSystem
        from rasterio.features import rasterize
        from rasterio.transform import from_bounds
        from shapely import wkb as shapely_wkb
    except ImportError as exc:
        logger.debug("GBA dependencies missing (pyarrow[s3]/rasterio/shapely): %s", exc)
        return None

    north, south, east, west = bbox
    h, w = dim

    tiles = _intersecting_tiles(bbox)
    if not tiles:
        logger.debug("GBA has no published tile for bbox %s", bbox)
        return None

    try:
        fs = S3FileSystem(anonymous=True, endpoint_override=_ENDPOINT, scheme="https")
        shapes: list[tuple[object, float]] = []

        for tile in tiles:
            dataset = ds.dataset(f"{_PREFIX}/{tile}", filesystem=fs, format="parquet")
            filt = (
                (ds.field(("bbox", "xmin")) <= float(east))
                & (ds.field(("bbox", "xmax")) >= float(west))
                & (ds.field(("bbox", "ymin")) <= float(north))
                & (ds.field(("bbox", "ymax")) >= float(south))
            )
            table = dataset.to_table(columns=["geometry", "height"], filter=filt)
            if len(table) == 0:
                continue

            for geom_bytes, bld_height in zip(
                table.column("geometry").to_pylist(),
                table.column("height").to_pylist(),
                strict=False,
            ):
                if geom_bytes is None or bld_height is None:
                    continue
                bld_h = float(bld_height)
                if not math.isfinite(bld_h) or bld_h <= 0:
                    continue
                try:
                    geom = shapely_wkb.loads(bytes(geom_bytes))
                except Exception:
                    continue
                if geom.is_empty:
                    continue
                shapes.append((geom, bld_h))

        if not shapes:
            return None

        shapes.sort(key=lambda pair: pair[1])
        grid = rasterize(
            shapes,
            out_shape=(h, w),
            transform=from_bounds(west, south, east, north, w, h),
            fill=np.nan,
            dtype="float32",
        )

        if np.all(np.isnan(grid)):
            return None
        return grid

    except Exception as exc:
        logger.warning("GBA parquet fetch failed: %s", exc)
        return None


class GBAProvider:
    """GlobalBuildingAtlas LoD1 — complete global footprint heights."""

    name = "gba"

    def covers(self, bbox: BBox) -> bool:
        """True wherever GBA publishes a tile.

        Unlike the other global providers this is answered from the real tile
        list rather than asserted, because GBA's completeness claim is about
        land: ocean cells are genuinely absent.
        """
        return bool(_intersecting_tiles(bbox))

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int]) -> HeightResult:
        """Fetch and rasterize GBA building heights for bbox."""
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

        confidence = np.where(np.isnan(raster), 0.0, _CONFIDENCE).astype(np.float32)
        result = HeightResult(raster, confidence, self.name, _RESOLUTION_M)

        write_height_result(_NAMESPACE, cache_key, result)
        return result


def _empty_result(dim: tuple[int, int]) -> HeightResult:
    h, w = dim
    return HeightResult(
        raster=np.full((h, w), np.nan, dtype=np.float32),
        confidence=np.zeros((h, w), dtype=np.float32),
        source_name="gba",
        resolution_m=_RESOLUTION_M,
    )
