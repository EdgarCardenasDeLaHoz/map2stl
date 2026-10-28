"""Roof detail read from the zoom-18 satellite tiles: ridge direction, hips, courtyards.

The roof classifier (``roof_classifier``) already caches the tiles under
``roof_tiles.CACHE``. This module reads the same tiles, never fetches, and answers three
questions the footprint alone cannot (plan: ``docs/plans/active/F-ROOFIMG-*.md``):

``ridge``     does the ridge run across the footprint's long axis (``roof:orientation=across``)?
``hip``       is a classifier ``gabled`` roof hipped?
``courtyard`` is there an open patio inside the footprint (an interior ring)?

Heights never come from imagery. Precedence: OSM tags > survey > imagery > classifier; a tagged
value is never overwritten.

Pipeline per footprint (all in a pixel frame, which is metric up to one scale factor):

1. :func:`register_map` / :func:`pick_shift`: the aerial image is rectified to bare ground, so
   a roof leans away from its footprint. Search a shift of up to :data:`SHIFT_MAX_PX` that
   best matches the footprint's edge band to image gradients, pulled toward the tile median.
2. :func:`ridge_features`, :func:`hip_features`, :func:`courtyard_candidates` read the shifted
   footprint. They return raw measurements only; the thresholds below act after the cache.
3. :func:`apply` stores the raw measurements per footprint (``roof_cache`` table
   ``roof_imagery``), then applies :func:`lidar_check` (a courtyard whose cells read roofed
   in the survey nDSM is dropped; with no lidar a hole needs :data:`T_HOLE_NOLIDAR`) and the
   thresholds, and returns the changed collection.

The thresholds were measured on Granada with ``tools/eval/eval_roof_imagery.py``.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from collections import OrderedDict

import cv2
import numpy as np
import shapely
from shapely.geometry import Polygon

from geo2stl.geo import M_PER_DEG_SPHERE, m_per_deg_lon

logger = logging.getLogger(__name__)

#: Bump when a stored measurement changes meaning (every row misses and is measured again).
IMAGERY_VERSION = 1
PROVIDER = "esri"
ZOOM = 18

# ---- measured defaults (tools/eval/eval_roof_imagery.py; decision roofs-landmarks.md) ----
SHIFT_MAX_PX = 5            # registration search radius
PRIOR_LAMBDA = 0.02         # pull toward the tile median, per squared pixel of distance
REG_GATE = 1.1              # edge-band score over the search-window mean below which a building is skipped
                            # (Granada median 1.22; gating higher did not improve ridge agreement)
MIN_AREA_M2 = 60.0          # smaller footprints are not read
MAX_FOOTPRINT_PX = 200      # larger footprints (stadiums) are not read

# Ship gates, measured on Granada by tools/eval/eval_roof_imagery.py (docs/decisions/roofs-landmarks.md):
EMIT_RIDGE = True           # imagery 0.75 vs long axis 0.58 on the confident subset (n=52); fixed 14, broke 5
T_RIDGE = 0.10
EMIT_HIP = False            # no signal: hip score AUC 0.18 on 20 hipped / 3 gabled tagged roofs
T_HIP = 0.50
EMIT_COURTYARDS = False     # imagery-only precision 0.045 against nDSM < 2 m (gate 0.85), recall 0.09 of OSM holes
OPEN_PX = 3                 # opening kernel that cuts thin links between a patio and the eaves
ODD_CUT = 30.0              # Lab distance from the roof's median colour that marks a different surface
DARK_CUT = 70               # grey level under which a pixel can be a shaded patio
REL_DARK = 0.65             # ... and under this fraction of the roof's median grey
VEG_CUT = 18                # excess green (2G - R - B) over which a pixel is vegetation
A_MIN_M2 = 12.0             # smallest courtyard
SOLIDITY_MIN = 0.6
MIN_WIDTH_M = 2.5           # inscribed width (print gap and a patio you can see)
EDGE_CLEAR_M = 1.0          # a courtyard keeps this far from the outline
MAX_HOLE_SHARE = 0.5        # and covers less than this share of the footprint
SIMPLIFY_M = 0.5
T_HOLE_NOLIDAR = 0.50       # score a hole needs where there is no lidar to veto it
ROOFED_M = 2.0              # nDSM at or above this is a roof, not a patio
LIDAR_MIN_ROOF_M = 3.0      # the survey sees the building only if its p90 is at least this

#: ``roof_source`` prefixes that outrank imagery (F-ROOFLIDAR).
_HIGHER_SOURCES = ("survey", "lidar", "osm")
_PITCHED_FOR_RIDGE = ("gabled",)
_CLASSIFIER_SOURCES = ("roof_model_gbm", "satellite_classify")


def settings_id() -> str:
    """Short id of the switches and thresholds, for the in-memory memo key in
    ``city_data.with_roof_shapes`` (the stored raw measurements do not depend on them)."""
    blob = json.dumps([IMAGERY_VERSION, EMIT_RIDGE, T_RIDGE, EMIT_HIP, T_HIP, EMIT_COURTYARDS,
                       T_HOLE_NOLIDAR, A_MIN_M2, SOLIDITY_MIN, MIN_WIDTH_M, ROOFED_M],
                      separators=(",", ":"))
    return hashlib.sha1(blob.encode()).hexdigest()[:10]


# ------------------------------------------------------------------ tiles (never fetched)

_TILES: OrderedDict = OrderedDict()
_TILE_LRU = 192


def _tile(tx: int, ty: int):
    """One decoded zoom-18 tile (uint8 RGB) from the disk cache, or None when not cached."""
    from PIL import Image

    from city2stl import roof_tiles
    from geo2stl import imagery

    k = (tx, ty)
    hit = _TILES.get(k)
    if hit is not None:
        _TILES.move_to_end(k)
        return hit
    path = imagery.tile_cache_path(roof_tiles.CACHE, ZOOM, tx, ty)
    if not path.exists():
        return None
    try:
        with Image.open(path) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.uint8)
    except Exception:  # noqa: BLE001 - a corrupt tile is a missing tile here
        return None
    _TILES[k] = arr
    while len(_TILES) > _TILE_LRU:
        _TILES.popitem(last=False)
    return arr


def crop(x0: float, y0: float, x1: float, y1: float):
    """``(rgb, ox, oy)``: the cached imagery over global pixels [x0, x1) x [y0, y1) (integers
    ``ox``, ``oy`` give the crop's origin), or None when a tile under it is not on disk."""
    from geo2stl.imagery import TILE_SIZE as T

    ox, oy = int(math.floor(x0)), int(math.floor(y0))
    ex, ey = int(math.ceil(x1)), int(math.ceil(y1))
    out = np.zeros((ey - oy, ex - ox, 3), np.uint8)
    for ty in range(oy // T, (ey - 1) // T + 1):
        for tx in range(ox // T, (ex - 1) // T + 1):
            t = _tile(tx, ty)
            if t is None:
                return None
            a0, b0 = max(ox, tx * T), max(oy, ty * T)
            a1, b1 = min(ex, tx * T + T), min(ey, ty * T + T)
            out[b0 - oy:b1 - oy, a0 - ox:a1 - ox] = t[b0 - ty * T:b1 - ty * T, a0 - tx * T:a1 - tx * T]
    return out, ox, oy


# ------------------------------------------------------------------ pixel-frame helpers

def _mask(poly: Polygon, hw: tuple[int, int]) -> np.ndarray:
    """uint8 {0,1} raster of ``poly`` (holes kept) at pixel centres, sub-pixel exact edges."""
    m = np.zeros(hw, np.uint8)
    sh = 4

    def fill(ring, val):
        pts = np.round(np.asarray(ring.coords) * (1 << sh)).astype(np.int32)
        cv2.fillPoly(m, [pts], val, lineType=cv2.LINE_8, shift=sh)

    fill(poly.exterior, 1)
    for r in poly.interiors:
        fill(r, 0)
    return m


def long_axis_angle(poly: Polygon) -> tuple[float, float, float]:
    """``(angle, length, width)`` of the minimum rotated rectangle, angle of the long side in
    radians (mod pi), in the polygon's own frame."""
    c = np.asarray(poly.minimum_rotated_rectangle.exterior.coords)[:3]
    a, b = c[1] - c[0], c[2] - c[1]
    la, lb = float(np.hypot(*a)), float(np.hypot(*b))
    v = a if la >= lb else b
    return math.atan2(v[1], v[0]) % math.pi, max(la, lb), min(la, lb)


def _grad(gray: np.ndarray, sigma: float) -> tuple[np.ndarray, np.ndarray]:
    g = cv2.GaussianBlur(gray, (0, 0), sigma)
    return cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3), cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)


def to_gray(rgb: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32)


# ------------------------------------------------------------------ 1. registration

def register_map(gray: np.ndarray, poly: Polygon, max_shift: int = SHIFT_MAX_PX):
    """``(score_map, quality)`` for shifting the image by (dx, dy) in [-max_shift, max_shift].

    ``score_map[dy + S, dx + S]`` is the mean gradient magnitude under the footprint's 3 px edge
    band when the image content is displaced by (dx, dy) from the footprint; ``quality`` is the
    best score over the mean score of the search window (1.0 = no structure at all). None when
    the footprint does not fit the image with the search margin."""
    h, w = gray.shape
    S = max_shift
    gx, gy = _grad(gray, 1.0)
    mag = cv2.magnitude(gx, gy)
    x0, y0, x1, y1 = poly.bounds
    ix0, iy0 = int(math.floor(x0)) - 2, int(math.floor(y0)) - 2
    ix1, iy1 = int(math.ceil(x1)) + 2, int(math.ceil(y1)) + 2
    if ix0 - S < 0 or iy0 - S < 0 or ix1 + S > w or iy1 + S > h:
        return None
    band = np.zeros((iy1 - iy0, ix1 - ix0), np.float32)
    for ring in [poly.exterior, *poly.interiors]:
        pts = np.round((np.asarray(ring.coords) - [ix0, iy0]) * 16).astype(np.int32)
        cv2.polylines(band, [pts], True, 1.0, thickness=3, lineType=cv2.LINE_8, shift=4)
    n = float(band.sum())
    if n < 8:
        return None
    win = mag[iy0 - S:iy1 + S, ix0 - S:ix1 + S]
    sc = cv2.matchTemplate(win, band, cv2.TM_CCORR) / n         # (2S+1, 2S+1), index [dy+S, dx+S]
    # TM_CCORR slides the band over the window: output[j, i] is the band placed at offset
    # (i, j) from the window origin, i.e. the band displaced by (i - S, j - S) from the footprint.
    quality = float(sc.max() / max(float(sc.mean()), 1e-6))
    return sc, quality


def pick_shift(score_map: np.ndarray, prior: tuple[float, float] = (0.0, 0.0),
               lam: float = PRIOR_LAMBDA) -> tuple[int, int]:
    """The (dx, dy) maximising the score minus ``lam`` * squared distance from ``prior``,
    relative to the map's peak so ``lam`` is scale-free."""
    S = score_map.shape[0] // 2
    dy, dx = np.mgrid[-S:S + 1, -S:S + 1]
    pen = lam * ((dx - prior[0]) ** 2 + (dy - prior[1]) ** 2) * float(score_map.max())
    j, i = np.unravel_index(int(np.argmax(score_map - pen)), score_map.shape)
    return int(i - S), int(j - S)


# ------------------------------------------------------------------ 2a. ridge

def _split(gray: np.ndarray, pts: np.ndarray, centre: np.ndarray, n: np.ndarray, extent: float) -> float:
    """Brightness split between the two sides of the line through ``centre`` along ``n``'s
    normal: |mean difference| over the mean within-side spread."""
    s = (pts - centre) @ n
    gap = 0.08 * extent
    a, b = gray[s < -gap], gray[s > gap]
    if len(a) < 8 or len(b) < 8:
        return 0.0
    return float(abs(a.mean() - b.mean()) / (0.5 * (a.std() + b.std()) + 2.0))


def ridge_features(gray: np.ndarray, poly: Polygon, edge_px: float = 1.5) -> dict | None:
    """Raw ridge measurements for a footprint already shifted onto its roof.

    ``coh`` structure-tensor coherence of the roof texture; ``s_across`` whether its dominant
    line direction (the ridge, tile courses) is closer to across the long axis than along it;
    ``s_margin`` 0..1 how clearly; ``b_across`` / ``b_margin`` the same from the brightness
    split (the two slopes catch the sun differently: the split is larger across the true ridge
    line's two sides); ``aspect`` long over short side."""
    h, w = gray.shape
    m = _mask(poly, (h, w))
    inner = cv2.distanceTransform(m, cv2.DIST_L2, 3) > edge_px
    if int(inner.sum()) < 40:
        return None
    gx, gy = _grad(gray, 0.8)
    jxx, jyy, jxy = (float((gx * gx)[inner].sum()), float((gy * gy)[inner].sum()),
                     float((gx * gy)[inner].sum()))
    tr = jxx + jyy
    if tr <= 1e-6:
        return None
    coh = math.hypot(jxx - jyy, 2 * jxy) / tr
    ridge_theta = 0.5 * math.atan2(2 * jxy, jxx - jyy) + math.pi / 2
    theta, length, width = long_axis_angle(poly)
    d = abs(ridge_theta - theta) % math.pi
    d = min(d, math.pi - d)                                       # 0 along .. pi/2 across
    u = np.array([math.cos(theta), math.sin(theta)])
    nrm = np.array([-u[1], u[0]])
    yy, xx = np.nonzero(inner)
    pts = np.column_stack([xx, yy]).astype(np.float64)
    centre = pts.mean(0)
    vals = gray[yy, xx]
    sp_along = _split(vals, pts, centre, nrm, width)              # sides of a ridge along u
    sp_across = _split(vals, pts, centre, u, length)              # sides of a ridge across u
    return {"coh": coh, "s_across": bool(d > math.pi / 4),
            "s_margin": abs(d - math.pi / 4) / (math.pi / 4),
            "b_across": bool(sp_across > sp_along),
            "b_margin": abs(sp_across - sp_along) / (sp_across + sp_along + 1e-6),
            "aspect": length / max(width, 1e-6)}


def ridge_conf(f: dict) -> float:
    """One confidence from the two votes (both must agree for :func:`ridge_across`)."""
    return float(f["coh"] * math.sqrt(f["s_margin"] * f["b_margin"]))


def ridge_across(f: dict | None, t: float | None = None) -> bool:
    """True when texture and brightness both say across and the confidence reaches ``t``."""
    if not f or not (f["s_across"] and f["b_across"]):
        return False
    return ridge_conf(f) >= (T_RIDGE if t is None else t)


# ------------------------------------------------------------------ 2b. hip

def _line_contrast(gray: np.ndarray, a: np.ndarray, b: np.ndarray, off: float, scale: float) -> float:
    """Brightness difference between the two sides of segment a-b, in units of ``scale``."""
    t = np.linspace(0.15, 0.85, 12)
    p = a[None] + (b - a)[None] * t[:, None]
    d = (b - a) / max(float(np.hypot(*(b - a))), 1e-9)
    nrm = np.array([-d[1], d[0]])

    def sample(q):
        return cv2.remap(gray, q[:, 0:1].T.astype(np.float32), q[:, 1:2].T.astype(np.float32),
                         cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)[0]

    return float(abs(sample(p + off * nrm).mean() - sample(p - off * nrm).mean()) / scale)


def hip_features(gray: np.ndarray, poly: Polygon) -> dict | None:
    """Raw hip measurements for a footprint already shifted onto its roof.

    The footprint's minimum rotated rectangle has a ridge of length ``L - W`` centred on it
    when hipped. ``hl`` is the brightness contrast across the four corner-to-ridge-end lines
    (hip lines), ``ctrl`` the same for four lines of different slant (no edge there on either
    roof), ``tri`` the contrast between the two end triangles and the long slopes."""
    h, w = gray.shape
    rect = poly.minimum_rotated_rectangle
    theta, length, width = long_axis_angle(poly)
    if width < 6 or length - width < 2:                          # too small / too square to tell
        return None
    u = np.array([math.cos(theta), math.sin(theta)])
    nrm = np.array([-u[1], u[0]])
    ctr = np.asarray(rect.centroid.coords[0])
    m = _mask(poly, (h, w)).astype(bool)
    if m.sum() < 60:
        return None
    scale = float(gray[m].std()) + 2.0
    half_l, half_w = length / 2, width / 2
    hip_lines, ctrl_lines = [], []
    for su in (1, -1):
        end = ctr + su * (half_l - half_w) * u                    # ridge end of a hipped roof
        ctrl_end = ctr + su * (half_l - 1.5 * half_w) * u         # steeper slant, same corner
        for sn in (1, -1):
            corner = ctr + su * half_l * u + sn * half_w * nrm
            hip_lines.append((corner, end))
            ctrl_lines.append((corner, ctrl_end))
    off = max(1.5, 0.08 * width)
    hl = float(np.mean([_line_contrast(gray, a, b, off, scale) for a, b in hip_lines]))
    ctrl = float(np.mean([_line_contrast(gray, a, b, off, scale) for a, b in ctrl_lines]))
    # end triangles vs the two long slopes
    tri_mask = np.zeros((h, w), np.uint8)
    for su in (1, -1):
        end = ctr + su * (half_l - half_w) * u
        tri = np.array([ctr + su * half_l * u + half_w * nrm, ctr + su * half_l * u - half_w * nrm, end])
        cv2.fillPoly(tri_mask, [np.round(tri).astype(np.int32)], 1)
    tri_in = (tri_mask > 0) & m
    rest = m & ~(tri_mask > 0)
    tri = (abs(float(gray[tri_in].mean() - gray[rest].mean())) / scale
           if tri_in.sum() > 8 and rest.sum() > 8 else 0.0)
    return {"hl": hl, "ctrl": ctrl, "tri": tri}


def hip_score(f: dict | None) -> float:
    return 0.0 if not f else float(f["hl"] - f["ctrl"] + 0.5 * f["tri"])


# ------------------------------------------------------------------ 2c. courtyards

def courtyard_candidates(rgb: np.ndarray, poly: Polygon, m_per_px: float) -> list[dict]:
    """Dark or vegetated enclosed patches inside a footprint already shifted onto its roof.

    Each has ``ring`` (pixel coordinates, simplified to :data:`SIMPLIFY_M`), ``area_m2``,
    ``width_m`` (inscribed), ``solidity`` and ``score`` in 0..1 (how dark / green). A patch
    qualifies when it keeps :data:`EDGE_CLEAR_M` from the outline, is at least
    :data:`A_MIN_M2`, :data:`MIN_WIDTH_M` wide and :data:`SOLIDITY_MIN` solid, and covers under
    :data:`MAX_HOLE_SHARE` of the roof. Footprints that already have holes return nothing."""
    if poly.interiors:
        return []
    h, w = rgb.shape[:2]
    m = _mask(poly, (h, w))
    area_px = int(m.sum())
    if area_px < 40:
        return []
    gray = to_gray(rgb)
    gray = cv2.GaussianBlur(gray, (0, 0), 0.8)
    rf = rgb.astype(np.float32)
    exg = 2 * rf[..., 1] - rf[..., 0] - rf[..., 2]
    inside = m > 0
    med = float(np.median(gray[inside]))
    cut = min(DARK_CUT, REL_DARK * med)
    dark = gray < cut
    veg = exg > VEG_CUT
    lab_img = cv2.cvtColor(cv2.GaussianBlur(rgb, (0, 0), 0.8), cv2.COLOR_RGB2LAB).astype(np.float32)
    odd = np.linalg.norm(lab_img - np.median(lab_img[inside], axis=0), axis=2)   # unlike the roof
    cand = ((dark | veg | (odd > ODD_CUT)) & inside).astype(np.uint8)
    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((OPEN_PX, OPEN_PX), np.uint8))
    dist_edge = cv2.distanceTransform(m, cv2.DIST_L2, 3)
    clear_px = EDGE_CLEAR_M / m_per_px
    n, lab, stats, _ = cv2.connectedComponentsWithStats(cand, connectivity=8)
    out = []
    for i in range(1, n):
        area_m2 = stats[i, cv2.CC_STAT_AREA] * m_per_px ** 2
        if area_m2 < A_MIN_M2 or stats[i, cv2.CC_STAT_AREA] > MAX_HOLE_SHARE * area_px:
            continue
        comp = (lab == i)
        if dist_edge[comp].min() <= clear_px:                     # touches the edge band: not enclosed
            continue
        cu8 = comp.astype(np.uint8)
        cnts, _ = cv2.findContours(cu8, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        cnt = max(cnts, key=cv2.contourArea)
        filled = np.zeros_like(cu8)
        cv2.drawContours(filled, [cnt], -1, 1, -1)
        hull = cv2.convexHull(cnt)
        solidity = float(filled.sum()) / max(float(cv2.contourArea(hull)), 1.0)
        width_m = 2 * float(cv2.distanceTransform(filled, cv2.DIST_L2, 3).max()) * m_per_px
        if solidity < SOLIDITY_MIN or width_m < MIN_WIDTH_M:
            continue
        ring = Polygon(cnt[:, 0, :].astype(np.float64) + 0.5)
        ring = ring.buffer(0.5).simplify(SIMPLIFY_M / m_per_px)
        if ring.geom_type != "Polygon" or ring.is_empty or len(ring.exterior.coords) < 4:
            continue
        sel = filled > 0
        strength = np.maximum.reduce([(cut - gray[sel]) / max(cut, 1.0), (exg[sel] - VEG_CUT) / 120.0,
                                      (odd[sel] - ODD_CUT) / 60.0])
        out.append({"ring": [[float(x), float(y)] for x, y in ring.exterior.coords],
                    "area_m2": float(area_m2), "width_m": width_m, "solidity": solidity,
                    "score": float(np.clip(np.median(strength) + 0.5, 0.0, 1.0))})
    return out


# ------------------------------------------------------------------ geometry <-> pixels

def _ring_px(ring_ll) -> np.ndarray:
    from geo2stl import imagery
    a = np.asarray(ring_ll, dtype=np.float64)
    gx = np.array([imagery.lon_to_global_px(x, ZOOM) for x in a[:, 0]])
    gy = np.array([imagery.lat_to_global_px(y, ZOOM) for y in a[:, 1]])
    return np.column_stack([gx, gy])


def _px_ring_to_ll(ring_px) -> list[list[float]]:
    from geo2stl import imagery
    return [list(imagery.global_px_to_lonlat(float(x), float(y), ZOOM)) for x, y in ring_px]


def footprint_px(geometry: dict) -> np.ndarray | None:
    """Global zoom-18 pixel exterior ring of a GeoJSON Polygon, else None."""
    if not geometry or geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
        return None
    ring = geometry["coordinates"][0]
    return _ring_px(ring) if len(ring) >= 4 else None


def footprint_area_m2(geometry: dict) -> float:
    """Approximate exterior area in m2 (equirectangular), 0 when not a Polygon."""
    if not geometry or geometry.get("type") != "Polygon" or not geometry.get("coordinates"):
        return 0.0
    a = np.asarray(geometry["coordinates"][0], dtype=np.float64)
    if len(a) < 4:
        return 0.0
    k = math.cos(math.radians(float(a[:, 1].mean())))
    x, y = a[:, 0] * k, a[:, 1]
    return abs(0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))) * M_PER_DEG_SPHERE ** 2


class Site:
    """A footprint's imagery crop: the ring in crop pixels, the image and its scale."""

    __slots__ = ("rgb", "gray", "poly", "ox", "oy", "m_per_px", "tile")

    def __init__(self, rgb, poly, ox, oy, m_per_px, tile):
        self.rgb, self.poly, self.ox, self.oy, self.m_per_px, self.tile = rgb, poly, ox, oy, m_per_px, tile
        self.gray = to_gray(rgb)


def open_site(geometry: dict) -> Site | None:
    """The cached imagery around a Polygon feature, or None (not a polygon, too big, a tile
    is not on disk)."""
    from geo2stl import imagery
    px = footprint_px(geometry)
    if px is None:
        return None
    x0, y0 = px.min(0)
    x1, y1 = px.max(0)
    if x1 - x0 > MAX_FOOTPRINT_PX or y1 - y0 > MAX_FOOTPRINT_PX:
        return None
    margin = SHIFT_MAX_PX + 8
    got = crop(x0 - margin, y0 - margin, x1 + margin, y1 + margin)
    if got is None:
        return None
    rgb, ox, oy = got
    lat = float(np.mean([p[1] for p in geometry["coordinates"][0]]))
    poly = Polygon(px - [ox, oy])
    if not poly.is_valid:
        poly = poly.buffer(0)
        if poly.geom_type != "Polygon" or poly.is_empty:
            return None
    tile = (int(((x0 + x1) / 2) // imagery.TILE_SIZE), int(((y0 + y1) / 2) // imagery.TILE_SIZE))
    return Site(rgb, poly, ox, oy, imagery.m_per_px(lat, ZOOM), tile)


def measure(site: Site, shape: str, prior: tuple[float, float] = (0.0, 0.0)) -> dict | None:
    """The raw measurements of one footprint, ready to cache (None: no usable registration).

    ``shape`` is the ``roof:shape`` in force; ridge and hip are read only for ``gabled``."""
    reg = register_map(site.gray, site.poly)
    if reg is None:
        return None
    sc, quality = reg
    dx, dy = pick_shift(sc, prior)
    row: dict = {"shift": [dx, dy], "reg": round(quality, 4)}    # REG_GATE is applied in _decide
    moved = shapely.affinity.translate(site.poly, dx, dy)
    if shape in _PITCHED_FOR_RIDGE:
        row["ridge"] = ridge_features(site.gray, moved)
    if shape in _PITCHED_FOR_RIDGE or shape in ("hipped", "pyramidal"):   # tagged hips calibrate the score
        row["hip"] = hip_features(site.gray, moved)
    holes = []
    for c in courtyard_candidates(site.rgb, moved, site.m_per_px):
        ring = np.asarray(c.pop("ring")) - [dx, dy] + [site.ox, site.oy]   # back onto the footprint
        c["ring_ll"] = _px_ring_to_ll(ring)
        holes.append(c)
    row["holes"] = holes
    return row


# ------------------------------------------------------------------ 3. lidar

def lidar_check(ndsm, geometry: dict, hole_ll) -> tuple[bool, float | None]:
    """``(has_lidar, roofed_share)`` of one candidate hole against a survey nDSM
    ``(array, affine)`` (``city2stl.height.providers.survey.ndsm_for_bbox``).

    ``has_lidar`` is True when the survey sees the building at all (p90 of the cells under the
    footprint, minus the hole, at least :data:`LIDAR_MIN_ROOF_M`); ``roofed_share`` the share
    of the hole's 0.5 m sample points whose cell reads at least :data:`ROOFED_M` (None without
    lidar)."""
    if ndsm is None:
        return False, None
    arr, tf = ndsm
    ext = Polygon(geometry["coordinates"][0])
    hole = Polygon(hole_ll)
    if ext.is_empty or hole.is_empty:
        return False, None
    lat = ext.centroid.y
    dlon = 0.5 / float(m_per_deg_lon(lat))
    dlat = 0.5 / M_PER_DEG_SPHERE

    def cells(poly):
        x0, y0, x1, y1 = poly.bounds
        gx, gy = np.meshgrid(np.arange(x0, x1 + dlon, dlon), np.arange(y0, y1 + dlat, dlat))
        gx, gy = gx.ravel(), gy.ravel()
        ok = shapely.contains_xy(poly, gx, gy)
        gx, gy = gx[ok], gy[ok]
        col = np.floor((gx - tf.c) / tf.a).astype(int)
        row = np.floor((gy - tf.f) / tf.e).astype(int)
        inb = (col >= 0) & (col < arr.shape[1]) & (row >= 0) & (row < arr.shape[0])
        v = np.full(len(gx), np.nan, np.float32)
        v[inb] = arr[row[inb], col[inb]]
        return v

    roof = cells(ext.difference(hole))
    roof = roof[np.isfinite(roof)]
    if len(roof) < 10 or float(np.percentile(roof, 90)) < LIDAR_MIN_ROOF_M:
        return False, None
    inside = cells(hole)
    inside = inside[np.isfinite(inside)]
    if len(inside) < 3:
        return False, None
    return True, float((inside >= ROOFED_M).mean())


def survey_ndsm(bbox):
    """The survey nDSM for ``bbox`` as ``survey_fill`` reads it (disk-cached), or None."""
    try:
        from city2stl.height import survey_fill
        from city2stl.height.providers.survey import ndsm_for_bbox
        src = survey_fill.source_for(bbox)
        res = survey_fill.fill_resolution(bbox)
        if src is None or res is None:
            return None
        return ndsm_for_bbox(src.primary, bbox, res)
    except Exception as exc:  # noqa: BLE001
        logger.info("Roof imagery: no survey nDSM (%s)", exc)
        return None


# ------------------------------------------------------------------ 4. apply

def feature_key(feat: dict, shape: str) -> str:
    """Cache key of one footprint: version, imagery provider and zoom, geometry, ``roof:shape``."""
    blob = json.dumps([IMAGERY_VERSION, PROVIDER, ZOOM, (feat or {}).get("geometry"), shape],
                      separators=(",", ":"), default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _higher_source(props: dict) -> bool:
    return str(props.get("roof_source") or "").lower().startswith(_HIGHER_SOURCES)


def _eligible(feat: dict) -> bool:
    g = (feat or {}).get("geometry") or {}
    return g.get("type") == "Polygon" and footprint_area_m2(g) >= MIN_AREA_M2


def measure_missing(feats: list[dict], shapes: dict[int, str], progress=None) -> dict[int, dict | None]:
    """Measure the footprints ``shapes`` (position in ``feats`` -> ``roof:shape``): registration
    over all of them first to get each tile's median shift, then the full measurement with that
    shift as the prior. A footprint with no cached tile maps to None."""
    def spot(i):
        x, y = feats[i]["geometry"]["coordinates"][0][0][:2]
        return (math.floor(y / 0.002), math.floor(x / 0.002))      # near ones share tiles

    order = sorted(shapes, key=spot)
    first: dict[tuple, list] = {}
    for i in order:
        site = open_site(feats[i]["geometry"])
        reg = register_map(site.gray, site.poly) if site else None
        if reg is not None and reg[1] >= REG_GATE:
            first.setdefault(site.tile, []).append(pick_shift(reg[0]))
    prior = {t: (float(np.median([s[0] for s in v])), float(np.median([s[1] for s in v])))
             for t, v in first.items()}
    out: dict[int, dict | None] = {}
    for n, i in enumerate(order):
        site = open_site(feats[i]["geometry"])
        out[i] = None if site is None else measure(site, shapes[i], prior.get(site.tile, (0.0, 0.0)))
        if progress and n % 200 == 0:
            progress(n, len(order))
    return out


def apply(fc: dict, bbox: tuple, *, ndsm="auto", progress=None) -> tuple[dict | None, dict]:
    """``(changed collection or None, stats)``; never raises. See the module docstring."""
    from city2stl import roof_cache

    feats = fc.get("features") or []
    stats = {"enabled": {"ridge": EMIT_RIDGE, "hip": EMIT_HIP, "courtyards": EMIT_COURTYARDS},
             "read": 0, "cached": 0, "no_tiles": 0, "ridge_across": 0, "hipped": 0, "courtyards": 0,
             "vetoed": 0}
    if not (EMIT_RIDGE or EMIT_HIP or EMIT_COURTYARDS):
        return None, stats
    try:
        elig = [i for i, f in enumerate(feats) if _eligible(f)]
        shape = {i: str((feats[i].get("properties") or {}).get("roof:shape") or "").lower() for i in elig}
        if not (EMIT_HIP or EMIT_COURTYARDS):                 # ridge reads gabled roofs only
            elig = [i for i in elig if shape[i] in _PITCHED_FOR_RIDGE]
        keys = {i: feature_key(feats[i], shape[i]) for i in elig}
        rows = roof_cache.lookup_imagery(sorted(set(keys.values())))
        miss = [i for i in elig if keys[i] not in rows]
        if miss:
            got = measure_missing(feats, {i: shape[i] for i in miss}, progress)
            new = []
            for i, r in got.items():
                if r is None:
                    stats["no_tiles"] += 1
                    continue
                rows[keys[i]] = r
                new.append((keys[i], json.dumps(r, separators=(",", ":"))))
            roof_cache.store_imagery(new)
        stats["read"], stats["cached"] = len(rows), len(elig) - len(miss)
        nd = ndsm
        out = list(feats)
        changed = False
        for i in elig:
            row = rows.get(keys[i])
            if row is None:
                continue
            if nd == "auto" and row.get("holes") and EMIT_COURTYARDS:
                nd = survey_ndsm(bbox)
            new_f = _decide(feats[i], row, shape[i], None if isinstance(nd, str) else nd, stats)
            if new_f is not None:
                out[i] = new_f
                changed = True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Roof imagery skipped: %s", exc)
        stats["error"] = str(exc)
        return None, stats
    return ({**fc, "features": out} if changed else None), stats


def _decide(feat: dict, row: dict, shape: str, nd, stats: dict) -> dict | None:
    """``feat`` with the detail the raw measurements ``row`` support, or None for no change."""
    props = dict(feat.get("properties") or {})
    geom = feat["geometry"]
    changed = False
    higher = _higher_source(props)
    if row.get("reg", 0.0) < REG_GATE:                           # weak registration: leave it alone
        return None
    if EMIT_RIDGE and shape in _PITCHED_FOR_RIDGE and not props.get("roof:orientation") and not higher:
        if ridge_across(row.get("ridge")):
            props["roof:orientation"] = "across"
            props["roof_orientation_source"] = "imagery"
            stats["ridge_across"] += 1
            changed = True
    if (EMIT_HIP and shape == "gabled" and props.get("roof_source") in _CLASSIFIER_SOURCES
            and hip_score(row.get("hip")) >= T_HIP):
        props["roof:shape"] = "hipped"
        props["roof_detail_source"] = "imagery"
        stats["hipped"] += 1
        changed = True
    if EMIT_COURTYARDS and row.get("holes") and not higher and len(geom["coordinates"]) == 1:
        rings = []
        ext = Polygon(geom["coordinates"][0])
        for h in row["holes"]:
            has, roofed = lidar_check(nd, geom, h["ring_ll"])
            if has and roofed > 0.5:
                stats["vetoed"] += 1
                continue
            if not has and h["score"] < T_HOLE_NOLIDAR:
                continue
            hp = Polygon(h["ring_ll"])
            if not hp.is_valid or not ext.contains(hp) or any(Polygon(r).intersects(hp) for r in rings):
                continue
            rings.append(h["ring_ll"])
        if rings:
            geom = {**geom, "coordinates": [*geom["coordinates"], *rings]}
            props["courtyards"] = len(rings)
            stats["courtyards"] += len(rings)
            changed = True
    return {**feat, "properties": props, "geometry": geom} if changed else None
