"""Read the WSF3D global mosaic by HTTP range, as a fallback for the per-tile endpoint.

The tile endpoint at ``WSF3D/files/tiles/`` publishes **453** one-degree tiles, not a global grid,
and a request for anything else answers 404. Read as "no settlements here" that silently empties
the raster for most of the populated world -- Frankfurt's Bankenviertel (``e008_n51_e009_n50``),
Rotterdam, Panama, Miami and Dubai all 404 -- and in those cities WSF3D is usually the only coarse
global height product available at all.

The whole product is one 2.1 GB LZW-tiled BigTIFF under ``files/global/``, at the same 86.58 m/px
and the same Int16-with-0.1-gain encoding as the tiles. The host serves byte ranges, so only the
TIFF tiles covering the requested box are transferred: a few hundred kilobytes for a city, against
2.1 GB for the file.

GDAL is not used to do this. Its Windows build resolves TLS through schannel, and the DLR
certificate chain fails a revocation-status check there (``CERT_TRUST_REVOCATION_STATUS_UNKNOWN``)
that ``requests`` passes with certifi; ``CURL_CA_BUNDLE``, ``SSL_CERT_FILE``, ``GDAL_HTTP_CAINFO``
and ``GDAL_HTTP_SSL_VERIFYSTATUS=NO`` do not change it. Rather than disable verification, the file
is read with ``tifffile`` over the small range-request adapter below.

Requires ``tifffile`` and ``imagecodecs`` (the mosaic is LZW). Both are optional at import time;
without them this module reports itself unavailable and the provider keeps its tile-only
behaviour.
"""

from __future__ import annotations

import io
import logging
import math
import threading

import numpy as np
import requests

logger = logging.getLogger(__name__)

URL = "https://download.geoservice.dlr.de/WSF3D/files/global/WSF3D_V02_BuildingHeight.tif"
GAIN = 0.1                     # Int16 -> metres, same as the per-tile product
_CHUNK = 1 << 20               # range granularity; the header walk makes many small reads
_HEAD_TIMEOUT = 60
_READ_TIMEOUT = 120


class _RangeFile(io.RawIOBase):
    """Minimal seekable read-only file over HTTP range requests, with a block cache."""

    def __init__(self, url: str, session: requests.Session | None = None,
                 chunk: int = _CHUNK) -> None:
        self.url, self.chunk = url, chunk
        self.session = session or requests.Session()
        head = self.session.head(url, timeout=_HEAD_TIMEOUT, allow_redirects=True)
        head.raise_for_status()
        self.size = int(head.headers["Content-Length"])
        self._pos = 0
        self._blocks: dict[int, bytes] = {}
        self.bytes_fetched = 0

    def readable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = 0) -> int:
        base = {0: 0, 1: self._pos, 2: self.size}[whence]
        self._pos = max(0, min(base + offset, self.size))
        return self._pos

    def _block(self, idx: int) -> bytes:
        if idx not in self._blocks:
            lo = idx * self.chunk
            hi = min(lo + self.chunk, self.size) - 1
            r = self.session.get(self.url, headers={"Range": f"bytes={lo}-{hi}"},
                                 timeout=_READ_TIMEOUT)
            r.raise_for_status()
            self._blocks[idx] = r.content
            self.bytes_fetched += len(r.content)
        return self._blocks[idx]

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self._pos
        n = min(n, self.size - self._pos)
        out = bytearray()
        while n > 0:
            idx, off = divmod(self._pos, self.chunk)
            blk = self._block(idx)
            take = min(n, len(blk) - off)
            out += blk[off:off + take]
            self._pos += take
            n -= take
        return bytes(out)

    def readinto(self, b) -> int:                                  # type: ignore[override]
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)


_STATE: dict = {}
_LOCK = threading.Lock()


def available() -> bool:
    """Whether the optional decoders needed to read the mosaic are installed."""
    try:
        import imagecodecs  # noqa: F401
        import tifffile  # noqa: F401
    except ImportError:
        return False
    return True


def _open() -> dict:
    """Open the mosaic once per process and keep its header state.

    The header walk is the expensive part -- a few hundred small reads over 2.1 GB of offsets --
    so it is done once and the open handle, with its block cache, is reused.
    """
    with _LOCK:
        if "page" not in _STATE:
            import tifffile

            fh = _RangeFile(URL)
            tf = tifffile.TiffFile(fh)
            page = tf.pages[0]
            tags = page.tags
            # GeoTIFF placement. Both are in degrees for this product.
            sx, sy = tags["ModelPixelScaleTag"].value[:2]
            tie = tags["ModelTiepointTag"].value
            _STATE.update(fh=fh, tf=tf, page=page,
                          x0=tie[3], y0=tie[4], sx=sx, sy=sy,
                          w=page.imagewidth, h=page.imagelength,
                          tw=page.tilewidth, th=page.tilelength)
            logger.info("WSF3D global mosaic opened: %d x %d at %.6f deg/px",
                        page.imagewidth, page.imagelength, sx)
        return _STATE


def _decode_tile(page, idx: int) -> np.ndarray | None:
    offs, counts = page.dataoffsets, page.databytecounts
    if idx >= len(offs) or counts[idx] == 0:
        return None
    fh = _STATE["fh"]
    fh.seek(offs[idx])
    raw = fh.read(counts[idx])
    seg, _idx, shape = page.decode(raw, idx)
    return np.asarray(seg).reshape(shape[-3:-1] if len(shape) >= 3 else shape)


def read_window(bbox) -> tuple[np.ndarray, float, tuple[float, float, float, float]]:
    """Heights in metres over *bbox*, on the mosaic's own grid.

    Returns ``(array, resolution_m, geo)`` where *geo* is the (north, south, east, west) actually
    covered by the returned array -- the window is snapped outwards to whole source pixels, so it
    is not identical to *bbox* and the caller needs it to place the samples.
    """
    st = _open()
    north, south, east, west = bbox
    col0 = int(math.floor((west - st["x0"]) / st["sx"]))
    col1 = int(math.ceil((east - st["x0"]) / st["sx"]))
    row0 = int(math.floor((st["y0"] - north) / st["sy"]))
    row1 = int(math.ceil((st["y0"] - south) / st["sy"]))
    col0, row0 = max(col0, 0), max(row0, 0)
    col1, row1 = min(col1, st["w"]), min(row1, st["h"])
    if col1 <= col0 or row1 <= row0:
        return np.zeros((0, 0), np.float32), 0.0, tuple(bbox)      # type: ignore[return-value]

    tw, th = st["tw"], st["th"]
    tiles_x = (st["w"] + tw - 1) // tw
    out = np.zeros((row1 - row0, col1 - col0), np.float32)
    page = st["page"]
    for ty in range(row0 // th, (row1 - 1) // th + 1):
        for tx in range(col0 // tw, (col1 - 1) // tw + 1):
            seg = _decode_tile(page, ty * tiles_x + tx)
            if seg is None:
                continue
            y_lo, x_lo = ty * th, tx * tw
            ys, xs = max(row0, y_lo), max(col0, x_lo)
            ye, xe = min(row1, y_lo + th), min(col1, x_lo + tw)
            out[ys - row0:ye - row0, xs - col0:xe - col0] = \
                seg[ys - y_lo:ye - y_lo, xs - x_lo:xe - x_lo]

    geo = (st["y0"] - row0 * st["sy"], st["y0"] - row1 * st["sy"],
           st["x0"] + col1 * st["sx"], st["x0"] + col0 * st["sx"])
    # Metres per pixel north-south. The product is a geographic grid, so this is the honest
    # single figure to report as its resolution.
    return out * GAIN, st["sy"] * 111320.0, geo


def read_grid(bbox, shape: tuple[int, int]) -> tuple[np.ndarray, float]:
    """The mosaic resampled onto a (north, south, east, west) target grid of *shape*.

    Nearest neighbour, because the source cells are 90 m and the target is metres: anything
    smoother would invent detail the product does not have. Zero means "no building in this cell"
    in WSF3D and becomes NaN here, matching how the per-tile provider reports absence.
    """
    arr, res, geo = read_window(bbox)
    if arr.size == 0:
        return np.full(shape, np.nan, np.float32), res
    gn, gs, ge, gw = geo
    north, south, east, west = bbox
    h, w = shape
    lat = north - (np.arange(h) + 0.5) * (north - south) / h
    lon = west + (np.arange(w) + 0.5) * (east - west) / w
    rows = np.clip(((gn - lat) / (gn - gs) * arr.shape[0]).astype(int), 0, arr.shape[0] - 1)
    cols = np.clip(((lon - gw) / (ge - gw) * arr.shape[1]).astype(int), 0, arr.shape[1] - 1)
    out = arr[np.ix_(rows, cols)].astype(np.float32)
    out[out <= 0] = np.nan
    return out, res
