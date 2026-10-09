"""
Google Open Buildings 2.5D Temporal (v1): building presence and height rasters.

Not ``open_buildings.py``: that module is Overture's per-footprint ``height`` / ``num_floors``.
This one reads Google's raster product (Sirko et al. 2023, arXiv:2310.11622).

- **Product:** annual layers 2016-2023, each from 32 Sentinel-2 frames centred on 30 June.
- **Coverage:** Africa, South and South-East Asia, Latin America and the Caribbean. It
  includes Colombia, Puerto Rico and the USVI, but not the US mainland or Hawaii (review
  2026-10-09 item 4, phase 1).
- **Bands,** in manifest order (rasterio indices 1-3; ``BANDS``):
  - ``building_fractional_count``;
  - ``building_height``: metres above terrain, **capped at 100 m**;
  - ``building_presence``: an uncalibrated confidence, good only for thresholding.
- **Values:** float32, no-data ``-99``. Rasters are 0.5 m, with about 4 m effective
  resolution.
- **Access:** an anonymous public bucket, so no Earth Engine and no account. Layout:
  - ``v1/manifests/<S2 level-2 token>_EPSG_<UTM>_<YYYY>_06_30.json``: Earth Engine ingestion
    manifests giving each tile's affine and size;
  - ``v1/geotiffs/<S2 level-7 token>_<YYYY>_06_30/tile_<id>.tif``: 25,000 × 25,000 px
    (12.5 km) per tile, in that UTM zone.
- **Tiles are cloud-optimised GeoTIFFs:**
  - 512 × 512 internal tiles;
  - DEFLATE with the floating-point predictor;
  - band-separate planes;
  - 14 overviews;
  - every IFD within the first ~82 KB. Checked on ``8ef64_2023_06_30/tile_AJEU8LcTBR8.tif``,
    2026-10-09.
- **Read windows only.** One tile is 7.5 GB in memory.
- **Neighbouring level-7 cells have offset tile grids that overlap.** File sizes suggest each
  tile holds data for its own cell plus a margin. A footprint is read from the tile whose
  folder cell contains its centroid (``cell_token``). Any tile containing the centroid is the
  fallback.
- **Local windows** (``window_dir``, default ``~/.cache/ob25d/<year>/``, outside OneDrive) are
  GeoTIFFs named ``<cell>_<tile id>__c<col>_r<row>_w<width>_h<height>.tif``: the tile's pixel
  window, three bands, the tile's UTM CRS. They are tried first. ``fetch=True`` reads a
  missing window over ``/vsicurl/`` and saves it.
- **Licence:** CC BY 4.0 or ODbL v1.0, at the user's choice. **Use CC BY 4.0**, so the result
  can combine with the CC BY-SA 4.0 Colombian cadastre. Attribution: ``ATTRIBUTION``.

API::

    covers(bbox) -> bool                        # bbox = (north, south, east, west)
    tiles_for_bbox(bbox, year=2023, fetch=True) -> [Tile]
    footprint_heights(polygons, year=2023, tau=0.5, fetch=False) -> {key: Reading}
    reading_from_pixels(height, presence, inside, tau, count=None) -> Reading | None
    GoogleOB25DProvider().fetch_heights(bbox, dim) -> HeightResult   # presence-masked height

Not in ``service._REGISTRY``. The export merge is ranked on measurement, and this provider has
been measured only as a low-rise prior (``city2stl/skyline/lowrise_prior.py``).
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from city2stl.height import BBox, HeightResult

from ._cache import make_cache_key, read_height_result, register_ttl, write_height_result

logger = logging.getLogger(__name__)

NAMESPACE = "google_ob25d"
TTL_DAYS = 365                     # v1 is frozen (files dated 2024-10-31)
register_ttl(NAMESPACE, TTL_DAYS)

BUCKET = "open-buildings-temporal-data"
BASE_URL = f"https://storage.googleapis.com/{BUCKET}/"
LIST_URL = f"https://storage.googleapis.com/storage/v1/b/{BUCKET}/o"
VERSION = "v1"
YEARS = tuple(range(2016, 2024))
DEFAULT_YEAR = 2023
#: Band names in manifest order (rasterio band = index + 1). The project page lists them in
#: another order; the manifest is what the GeoTIFFs follow.
BANDS = ("building_fractional_count", "building_height", "building_presence")
B_COUNT, B_HEIGHT, B_PRESENCE = 1, 2, 3
NODATA = -99.0
PIXEL_M = 0.5
EFFECTIVE_M = 4.0
HEIGHT_CAP_M = 100.0
MANIFEST_S2_LEVEL = 2
FOLDER_S2_LEVEL = 7

#: A footprint reading needs this many pixels at presence >= tau (16 m^2 at 0.5 m, one
#: effective 4 m pixel) ...
MIN_PIXELS = 64
#: ... making up at least this share of the footprint's pixels.
MIN_SHARE = 0.25
DEFAULT_TAU = 0.5

CONFIDENCE = 0.5
RESOLUTION_M = EFFECTIVE_M
ATTRIBUTION = ("Building heights: Google Open Buildings 2.5D Temporal v1, CC BY 4.0; "
               "contains modified Copernicus Sentinel-2 data")

_TIMEOUT_S = 60
_GDAL_ENV = {"GDAL_DISABLE_READDIR_ON_OPEN": "EMPTY_DIR",
             "CPL_VSIL_CURL_ALLOWED_EXTENSIONS": ".tif",
             "GDAL_HTTP_TIMEOUT": str(_TIMEOUT_S), "GDAL_HTTP_MAX_RETRY": "2"}


def cache_dir() -> Path:
    """Root of the local store: ``$CITY2STL_OB25D_DIR`` or ``~/.cache/ob25d`` (outside
    OneDrive: windows are re-downloadable and large)."""
    return Path(os.environ.get("CITY2STL_OB25D_DIR") or Path.home() / ".cache" / "ob25d")


def window_dir(year: int = DEFAULT_YEAR) -> Path:
    return cache_dir() / str(year)


# --------------------------------------------------------------------------- S2 cells
# Pure-python S2 cell ids (no s2geometry dependency): enough to name the manifest (level 2)
# and the tile folder (level 7) of a point.

_POS_TO_IJ = ((0, 1, 3, 2), (0, 2, 3, 1), (3, 2, 0, 1), (3, 1, 0, 2))
_IJ_TO_POS = ((0, 1, 3, 2), (0, 3, 1, 2), (2, 3, 1, 0), (2, 1, 3, 0))
_POS_TO_ORIENT = (1, 0, 0, 3)
_MAX_IJ = 1 << 30


def _xyz_to_face_uv(x: float, y: float, z: float) -> tuple[int, float, float]:
    ax, ay, az = abs(x), abs(y), abs(z)
    if ax >= ay and ax >= az:
        face = 0 if x > 0 else 3
    elif ay >= az:
        face = 1 if y > 0 else 4
    else:
        face = 2 if z > 0 else 5
    if face == 0:
        return face, y / x, z / x
    if face == 1:
        return face, -x / y, z / y
    if face == 2:
        return face, -x / z, -y / z
    if face == 3:
        return face, z / x, y / x
    if face == 4:
        return face, z / y, -x / y
    return face, -y / z, -x / z


def _uv_to_st(u: float) -> float:
    return 0.5 * math.sqrt(1 + 3 * u) if u >= 0 else 1 - 0.5 * math.sqrt(1 - 3 * u)


def cell_id(lat: float, lon: float, level: int) -> int:
    """S2 cell id of the level-``level`` cell containing (lat, lon)."""
    la, lo = math.radians(lat), math.radians(lon)
    x, y, z = math.cos(la) * math.cos(lo), math.cos(la) * math.sin(lo), math.sin(la)
    face, u, v = _xyz_to_face_uv(x, y, z)
    i = min(_MAX_IJ - 1, max(0, int(_uv_to_st(u) * _MAX_IJ)))
    j = min(_MAX_IJ - 1, max(0, int(_uv_to_st(v) * _MAX_IJ)))
    orient, pos_bits = face & 1, 0
    for k in range(level):
        ij = (((i >> (29 - k)) & 1) << 1) | ((j >> (29 - k)) & 1)
        pos = _IJ_TO_POS[orient][ij]
        pos_bits = (pos_bits << 2) | pos
        orient ^= _POS_TO_ORIENT[pos]
    return (face << 61) | (pos_bits << (61 - 2 * level)) | (1 << (60 - 2 * level))


def cell_token(lat: float, lon: float, level: int) -> str:
    """S2 token (hex id without trailing zeros), as the bucket names manifests and folders."""
    return format(cell_id(lat, lon, level), "016x").rstrip("0")


# --------------------------------------------------------------------------- manifests


def utm_epsg(lat: float, lon: float) -> int:
    zone = min(60, max(1, int(math.floor((lon + 180.0) / 6.0)) + 1))
    return (32600 if lat >= 0 else 32700) + zone


@dataclass(frozen=True)
class Tile:
    """One GeoTIFF of a manifest: ``object`` (bucket path), UTM ``crs``, the top-left corner
    ``x0, y0``, the size in pixels, and its folder's S2 level-7 ``cell`` token."""
    object: str
    crs: str
    x0: float
    y0: float
    width: int
    height: int
    cell: str

    @property
    def tile_id(self) -> str:
        return self.object.rsplit("/", 1)[-1].removeprefix("tile_").removesuffix(".tif")

    @property
    def url(self) -> str:
        return BASE_URL + self.object

    def bounds(self) -> tuple[float, float, float, float]:
        """``(xmin, ymin, xmax, ymax)`` in the tile's CRS."""
        return (self.x0, self.y0 - self.height * PIXEL_M, self.x0 + self.width * PIXEL_M, self.y0)

    def transform(self):
        from rasterio.transform import Affine

        return Affine(PIXEL_M, 0.0, self.x0, 0.0, -PIXEL_M, self.y0)


def _http_json(url: str):
    with urllib.request.urlopen(url, timeout=_TIMEOUT_S) as r:
        return json.load(r)


def manifest_names(l2_token: str, year: int = DEFAULT_YEAR, fetch: bool = True) -> list[str]:
    """Manifest file names of one S2 level-2 cell and year: cached listing, else a bucket
    listing (metadata only)."""
    idx = cache_dir() / "manifests" / f"index_{l2_token}.json"
    if idx.exists():
        names = json.loads(idx.read_text(encoding="utf-8"))
    elif fetch:
        names, tok = [], None
        while True:
            q = {"prefix": f"{VERSION}/manifests/{l2_token}_EPSG_", "fields": "items(name),nextPageToken"}
            if tok:
                q["pageToken"] = tok
            j = _http_json(LIST_URL + "?" + urllib.parse.urlencode(q))
            names += [it["name"].rsplit("/", 1)[-1] for it in j.get("items", [])]
            tok = j.get("nextPageToken")
            if not tok:
                break
        idx.parent.mkdir(parents=True, exist_ok=True)
        idx.write_text(json.dumps(sorted(names)), encoding="utf-8")
    else:
        local = (cache_dir() / "manifests").glob(f"{l2_token}_EPSG_*.json")
        names = [p.name for p in local]
    return sorted(n for n in names if n.endswith(f"_{year}_06_30.json"))


def load_manifest(name: str, fetch: bool = True) -> dict | None:
    """A manifest (cached under ``cache_dir()/manifests``), or None when absent."""
    p = cache_dir() / "manifests" / name
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    if not fetch:
        return None
    try:
        m = _http_json(f"{BASE_URL}{VERSION}/manifests/{name}")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(m), encoding="utf-8")
    return m


def manifest_tiles(manifest: dict) -> list[Tile]:
    """Tiles of a manifest (``uriPrefix`` + ``uris``, ``affineTransform``, ``dimensions``)."""
    prefix = manifest["uriPrefix"].split(f"{BUCKET}/", 1)[-1]
    out = []
    for ts in manifest["tilesets"]:
        for s in ts["sources"]:
            obj = prefix + s["uris"][0]
            a, d = s["affineTransform"], s["dimensions"]
            if abs(a["scaleX"] - PIXEL_M) > 1e-9 or abs(a["scaleY"] + PIXEL_M) > 1e-9:
                raise ValueError(f"{obj}: unexpected pixel size {a['scaleX']}, {a['scaleY']}")
            out.append(Tile(obj, ts["crs"], float(a["translateX"]), float(a["translateY"]),
                            int(d["width"]), int(d["height"]), obj.split("/")[2].split("_")[0]))
    return out


def _sample_points(bbox: BBox, n: int = 9) -> list[tuple[float, float]]:
    north, south, east, west = bbox
    return [(south + (north - south) * a / (n - 1), west + (east - west) * b / (n - 1))
            for a in range(n) for b in range(n)]


def tiles_for_bbox(bbox: BBox, year: int = DEFAULT_YEAR, fetch: bool = True) -> list[Tile]:
    """Tiles meeting ``bbox`` from the manifests of its S2 level-2 cells and UTM zones (one
    zone either side, since tiles near a zone edge sit in the next zone's manifest)."""
    from pyproj import Transformer
    from shapely.geometry import box
    from shapely.ops import transform

    north, south, east, west = bbox
    pts = _sample_points(bbox)
    l2 = sorted({cell_token(la, lo, MANIFEST_S2_LEVEL) for la, lo in pts})
    epsg = {utm_epsg(la, lo) for la, lo in pts}
    epsg |= {e + d for e in epsg for d in (-1, 1)}
    ll = box(west, south, east, north)
    out = []
    for tok in l2:
        for name in manifest_names(tok, year, fetch):
            if int(name.split("_EPSG_")[1].split("_")[0]) not in epsg:
                continue
            m = load_manifest(name, fetch)
            if m is None:
                continue
            ts = manifest_tiles(m)
            if not ts:
                continue
            g = transform(Transformer.from_crs("EPSG:4326", ts[0].crs, always_xy=True).transform, ll)
            out += [t for t in ts if box(*t.bounds()).intersects(g)]
    return out


def covers(bbox: BBox, year: int = DEFAULT_YEAR, fetch: bool = True) -> bool:
    """True when some tile of ``year`` meets ``bbox`` (False for Miami or Honolulu: their
    UTM zones have no manifest in the S2 cells that hold them)."""
    try:
        return bool(tiles_for_bbox(bbox, year, fetch))
    except (OSError, urllib.error.URLError) as exc:
        logger.warning("[google_ob25d] coverage check failed: %s", exc)
        return False


# --------------------------------------------------------------------------- windows

_WIN_RE = re.compile(r"^(?P<cell>[0-9a-f]+)_(?P<tile>.+)__c(?P<c>\d+)_r(?P<r>\d+)_w(?P<w>\d+)_h(?P<h>\d+)\.tif$")


def window_name(tile: Tile, col: int, row: int, width: int, height: int) -> str:
    return f"{tile.cell}_{tile.tile_id}__c{col}_r{row}_w{width}_h{height}.tif"


def local_windows(tile: Tile, year: int = DEFAULT_YEAR) -> list[tuple[Path, tuple[int, int, int, int]]]:
    """Saved windows of ``tile``: ``[(path, (col, row, width, height))]``."""
    d = window_dir(year)
    out = []
    if d.is_dir():
        for p in d.glob(f"{tile.cell}_{tile.tile_id}__*.tif"):
            m = _WIN_RE.match(p.name)
            if m:
                out.append((p, (int(m["c"]), int(m["r"]), int(m["w"]), int(m["h"]))))
    return out


def pixel_window(tile: Tile, xmin: float, ymin: float, xmax: float, ymax: float,
                 pad_px: int = 0) -> tuple[int, int, int, int] | None:
    """``(col, row, width, height)`` of the tile pixels under a UTM box, clipped; None if empty."""
    c0 = max(0, int(math.floor((xmin - tile.x0) / PIXEL_M)) - pad_px)
    c1 = min(tile.width, int(math.ceil((xmax - tile.x0) / PIXEL_M)) + pad_px)
    r0 = max(0, int(math.floor((tile.y0 - ymax) / PIXEL_M)) - pad_px)
    r1 = min(tile.height, int(math.ceil((tile.y0 - ymin) / PIXEL_M)) + pad_px)
    return (c0, r0, c1 - c0, r1 - r0) if c1 > c0 and r1 > r0 else None


def fetch_window(tile: Tile, win: tuple[int, int, int, int], year: int = DEFAULT_YEAR) -> Path:
    """Read ``win`` of ``tile`` over ``/vsicurl/`` (only the 512 px blocks it touches) and save
    it as a local window GeoTIFF. Returns the path."""
    import rasterio
    from rasterio.windows import Window

    c0, r0, w, h = win
    dst = window_dir(year) / window_name(tile, c0, r0, w, h)
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_suffix(".part.tif")
    with rasterio.Env(**_GDAL_ENV), rasterio.open("/vsicurl/" + tile.url) as src:
        prof = dict(driver="GTiff", width=w, height=h, count=3, dtype="float32", nodata=NODATA,
                    crs=src.crs, transform=src.window_transform(Window(c0, r0, w, h)),
                    tiled=True, blockxsize=512, blockysize=512, compress="deflate", predictor=3,
                    interleave="band", BIGTIFF="IF_SAFER")
        with rasterio.open(tmp, "w", **prof) as out:
            for b, name in enumerate(BANDS, 1):
                out.set_band_description(b, name)
            for y in range(0, h, 1024):
                hh = min(1024, h - y)
                out.write(src.read([1, 2, 3], window=Window(c0, r0 + y, w, hh)),
                          window=Window(0, y, w, hh))
    tmp.replace(dst)
    logger.info("[google_ob25d] saved %s", dst.name)
    return dst


def _source_for(tile: Tile, win, year: int, fetch: bool):
    """``(path or url, col_off, row_off)`` that serves ``win`` of ``tile``: a saved window that
    contains it, else (with ``fetch``) a newly fetched one, else None."""
    c0, r0, w, h = win
    for p, (wc, wr, ww, wh) in local_windows(tile, year):
        if wc <= c0 and wr <= r0 and c0 + w <= wc + ww and r0 + h <= wr + wh:
            return str(p), wc, wr
    if not fetch:
        return None
    p = fetch_window(tile, win, year)
    return str(p), c0, r0


# --------------------------------------------------------------------------- footprints


@dataclass(frozen=True)
class Reading:
    """A footprint's Google 2.5D values: height percentiles over the in-footprint pixels with
    presence >= ``tau``; ``share`` of the footprint's pixels that qualify; ``n_px`` qualifying
    pixels; ``count`` the fractional building count summed over the footprint. ``valid`` is
    False when the pixels fall short of ``MIN_PIXELS`` / ``MIN_SHARE`` (then the heights are
    None)."""
    p50: float | None
    p70: float | None
    p90: float | None
    share: float
    n_px: int
    count: float | None
    tau: float
    valid: bool
    tile: str = ""

    def stat(self, name: str) -> float | None:
        return getattr(self, name)


def reading_from_pixels(height: np.ndarray, presence: np.ndarray, inside: np.ndarray,
                        tau: float = DEFAULT_TAU, count: np.ndarray | None = None,
                        min_pixels: int = MIN_PIXELS, min_share: float = MIN_SHARE,
                        tile: str = "") -> Reading | None:
    """The ``Reading`` of one footprint from aligned pixel arrays (``inside``: the footprint
    mask). None when no pixel is inside."""
    inside = np.asarray(inside, bool)
    n_in = int(inside.sum())
    if n_in == 0:
        return None
    h = np.asarray(height, np.float32)
    p = np.asarray(presence, np.float32)
    ok = inside & (h != NODATA) & (p != NODATA) & np.isfinite(h) & np.isfinite(p)
    q = ok & (p >= tau)
    n = int(q.sum())
    share = n / n_in
    cnt = None
    if count is not None:
        c = np.asarray(count, np.float32)
        cv = c[ok & (c != NODATA)]
        cnt = float(cv.sum()) if cv.size else None
    if n < min_pixels or share < min_share:
        return Reading(None, None, None, round(share, 4), n, cnt, tau, False, tile)
    p50, p70, p90 = (float(v) for v in np.percentile(h[q], [50, 70, 90]))
    return Reading(round(p50, 2), round(p70, 2), round(p90, 2), round(share, 4), n, cnt, tau,
                   True, tile)


def _pick_tile(tiles: list[Tile], lat: float, lon: float, xy_by_crs: dict) -> Tile | None:
    """The tile whose folder cell holds the point and whose extent contains it, else any tile
    containing it."""
    own = cell_token(lat, lon, FOLDER_S2_LEVEL)
    inside = []
    for t in tiles:
        x, y = xy_by_crs[t.crs]
        xmin, ymin, xmax, ymax = t.bounds()
        if xmin <= x < xmax and ymin < y <= ymax:
            inside.append(t)
    return next((t for t in inside if t.cell == own), inside[0] if inside else None)


def footprint_heights(polygons: dict, year: int = DEFAULT_YEAR, tau: float = DEFAULT_TAU,
                      fetch: bool = False, tiles: list[Tile] | None = None) -> dict:
    """``{key: Reading}`` for ``polygons`` (key -> shapely Polygon/MultiPolygon in lon/lat).

    Each footprint is read from one tile (``_pick_tile``), out of saved windows. With
    ``fetch``, a window covering every footprint of a tile (+50 m) is fetched first when no
    saved window contains it. Footprints with no tile or no data are left out.
    """
    import rasterio
    from pyproj import Transformer
    from rasterio.features import geometry_mask
    from rasterio.transform import Affine
    from rasterio.windows import Window
    from shapely.ops import transform

    if not polygons:
        return {}
    cents = {k: g.centroid for k, g in polygons.items()}
    lats = [c.y for c in cents.values()]
    lons = [c.x for c in cents.values()]
    bbox = (max(lats), min(lats), max(lons), min(lons))
    tiles = tiles if tiles is not None else tiles_for_bbox(bbox, year, fetch=fetch)
    if not tiles:
        return {}
    fwd = {crs: Transformer.from_crs("EPSG:4326", crs, always_xy=True) for crs in {t.crs for t in tiles}}
    groups: dict[Tile, list] = {}
    for k, c in cents.items():
        xy = {crs: tr.transform(c.x, c.y) for crs, tr in fwd.items()}
        t = _pick_tile(tiles, c.y, c.x, xy)
        if t is not None:
            groups.setdefault(t, []).append(k)
    out: dict = {}
    with rasterio.Env(**_GDAL_ENV):
        for t, keys in groups.items():
            geoms = {k: transform(fwd[t.crs].transform, polygons[k]) for k in keys}
            xs0, ys0, xs1, ys1 = zip(*(g.bounds for g in geoms.values()), strict=True)
            need = pixel_window(t, min(xs0) - 50, min(ys0) - 50, max(xs1) + 50, max(ys1) + 50)
            if need is None:
                continue
            # None: no saved window holds the whole group; _Opened then takes the largest saved
            # window and footprints outside it are skipped
            src_info = _source_for(t, need, year, fetch)
            with _Opened(t, src_info, year) as ds:
                if ds is None:
                    continue
                src, coff, roff = ds
                # north to south, west to east: neighbouring reads share GDAL's block cache
                for k in sorted(keys, key=lambda k: (-geoms[k].bounds[3], geoms[k].bounds[0])):
                    g = geoms[k]
                    win = pixel_window(t, *g.bounds, pad_px=1)
                    if win is None:
                        continue
                    c0, r0, w, h = win
                    wl = Window(c0 - coff, r0 - roff, w, h)
                    if (wl.col_off < 0 or wl.row_off < 0 or wl.col_off + w > src.width
                            or wl.row_off + h > src.height):
                        continue
                    arr = src.read([B_COUNT, B_HEIGHT, B_PRESENCE], window=wl)
                    tf = Affine(PIXEL_M, 0.0, t.x0 + c0 * PIXEL_M, 0.0, -PIXEL_M, t.y0 - r0 * PIXEL_M)
                    inside = geometry_mask([g], out_shape=(h, w), transform=tf, invert=True)
                    r = reading_from_pixels(arr[1], arr[2], inside, tau, count=arr[0],
                                            tile=f"{t.cell}/{t.tile_id}")
                    if r is not None:
                        out[k] = r
    return out


class _Opened:
    """Context manager: ``(dataset, col_off, row_off)`` for a tile's source, or None."""

    def __init__(self, tile: Tile, src_info, year: int):
        self.tile, self.src_info, self.year, self.ds = tile, src_info, year, None

    def __enter__(self):
        import rasterio

        if self.src_info is None:
            # largest saved window of the tile, footprints outside it are skipped
            wins = sorted(local_windows(self.tile, self.year), key=lambda pw: -pw[1][2] * pw[1][3])
            if not wins:
                return None
            path, (c, r, _w, _h) = wins[0]
            self.src_info = (str(path), c, r)
        path, c, r = self.src_info
        self.ds = rasterio.open(path)
        return self.ds, c, r

    def __exit__(self, *exc):
        if self.ds is not None:
            self.ds.close()


# --------------------------------------------------------------------------- raster provider


class GoogleOB25DProvider:
    """Presence-masked Google 2.5D heights on the request grid (mean of the 0.5 m pixels with
    presence >= ``DEFAULT_TAU`` in each output cell). Not registered in ``service._REGISTRY``."""

    name = NAMESPACE

    def __init__(self, year: int = DEFAULT_YEAR, tau: float = DEFAULT_TAU, fetch: bool = True):
        self.year, self.tau, self.fetch = year, tau, fetch

    def covers(self, bbox: BBox) -> bool:
        return covers(bbox, self.year, fetch=self.fetch)

    def fetch_heights(self, bbox: BBox, dim: tuple[int, int]) -> HeightResult:
        north, south, east, west = bbox
        key = make_cache_key(NAMESPACE, north, south, east, west,
                             {"dim": list(dim), "year": self.year, "tau": self.tau})
        hit = read_height_result(NAMESPACE, key, self.name, RESOLUTION_M)
        if hit is not None:
            return hit
        try:
            raster = _grid_heights(bbox, dim, self.year, self.tau, self.fetch)
        except Exception as exc:  # noqa: BLE001 - a provider never fails the merge
            logger.warning("[google_ob25d] fetch failed: %s", exc)
            raster = None
        if raster is None:
            return HeightResult.empty(dim, self.name, RESOLUTION_M)
        conf = np.where(np.isnan(raster), 0.0, CONFIDENCE).astype(np.float32)
        result = HeightResult(raster, conf, self.name, RESOLUTION_M)
        write_height_result(NAMESPACE, key, result)
        return result


def _grid_heights(bbox: BBox, dim: tuple[int, int], year: int, tau: float,
                  fetch: bool) -> np.ndarray | None:
    import rasterio
    from pyproj import Transformer
    from rasterio.transform import from_bounds
    from rasterio.warp import Resampling, reproject
    from rasterio.windows import Window
    from shapely.geometry import box
    from shapely.ops import transform

    north, south, east, west = bbox
    tiles = tiles_for_bbox(bbox, year, fetch)
    if not tiles:
        return None
    h_out, w_out = dim
    dst_tf = from_bounds(west, south, east, north, w_out, h_out)
    acc = np.zeros(dim, np.float64)
    wsum = np.zeros(dim, np.float64)
    ll = box(west, south, east, north)
    with rasterio.Env(**_GDAL_ENV):
        for t in tiles:
            g = transform(Transformer.from_crs("EPSG:4326", t.crs, always_xy=True).transform, ll)
            win = pixel_window(t, *g.bounds, pad_px=8)
            if win is None:
                continue
            info = _source_for(t, win, year, fetch)
            if info is None:      # no saved window holds it all: the largest one, read boundless
                wins = sorted(local_windows(t, year), key=lambda pw: -pw[1][2] * pw[1][3])
                if not wins:
                    continue
                info = (str(wins[0][0]), wins[0][1][0], wins[0][1][1])
            path, coff, roff = info
            c0, r0, w, h = win
            wl = Window(c0 - coff, r0 - roff, w, h)
            with rasterio.open(path) as src:
                arr = src.read([B_HEIGHT, B_PRESENCE], window=wl, boundless=True,
                               fill_value=NODATA)
                tf = src.window_transform(wl)
                crs = src.crs
            hgt = np.where((arr[1] >= tau) & (arr[0] != NODATA), arr[0], np.nan).astype(np.float32)
            dst = np.full(dim, np.nan, np.float32)
            reproject(hgt, dst, src_transform=tf, src_crs=crs, dst_transform=dst_tf,
                      dst_crs="EPSG:4326", resampling=Resampling.average, src_nodata=np.nan,
                      dst_nodata=np.nan)
            ok = np.isfinite(dst)
            acc[ok] += dst[ok]
            wsum[ok] += 1
    if not wsum.any():
        return None
    out = np.full(dim, np.nan, np.float32)
    nz = wsum > 0
    out[nz] = (acc[nz] / wsum[nz]).astype(np.float32)
    return out


__all__ = ["ATTRIBUTION", "BANDS", "DEFAULT_TAU", "DEFAULT_YEAR", "GoogleOB25DProvider",
           "HEIGHT_CAP_M", "MIN_PIXELS", "MIN_SHARE", "NODATA", "Reading", "Tile", "cache_dir",
           "cell_token", "covers", "fetch_window", "footprint_heights", "load_manifest",
           "manifest_names", "manifest_tiles", "pixel_window", "reading_from_pixels",
           "tiles_for_bbox", "utm_epsg"]
