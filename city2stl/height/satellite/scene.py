"""Satellite scenes: which image a tile shows (Esri Wayback metadata identify), its date, the sun
at capture, and the lean of tall buildings (roof shift per metre of height).

Ported from the validated scratch scripts (2026-10-07, ``S/satellite/s25-s27``,
``S/satellite_cities/val/wb/w1_meta.py``, ``w2_fetch.py``, ``w3_geom.py`` with the fixed sun-time
search, ``wlib.py``). Pixel frame: global web-mercator pixels at zoom 18 (x east, y south);
vectors in metres are (east, south).

- ``Scene``: one dated image (tile folder + geometry).
- ``TileSource``: cached z18 tiles of one scene; ``crop`` reads a global-pixel window.
- ``sunpos``: NOAA solar position.
- ``wayback_config`` / ``identify`` / ``fetch_tiles``: free Esri imagery (no key), at most 4
  concurrent requests, cached on disk.
- ``fit_lean``: lean vector and OSM->scene registration from tagged towers (RANSAC) and low
  buildings; ``solve_sun``: the capture time on the scene date from tower shadows.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
import os
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

Z = 18
UA = {"User-Agent": "map2stl/1.0 (cached, polite)"}
WAYBACK_CONFIG_URL = "https://s3-us-west-2.amazonaws.com/config.maptiles.arcgis.com/waybackconfig.json"
#: Concurrent Esri requests (free imagery; user rule 2026-10-07: at most 4).
MAX_CONCURRENT = 4


def mpp(lat: float, z: int = Z) -> float:
    """Metres per pixel of web-mercator zoom ``z`` at latitude ``lat``."""
    return 40075016.686 * math.cos(math.radians(lat)) / (256 * 2 ** z)


def uv(az: float) -> np.ndarray:
    """Unit vector of a compass bearing in the pixel frame (x east, y south)."""
    return np.array([math.sin(math.radians(az)), -math.cos(math.radians(az))])


def outline(P: np.ndarray, step: float = 0.5):
    """Samples and outward unit normals of a closed pixel ring (``(None, None)`` if degenerate)."""
    from matplotlib.path import Path as MplPath  # noqa: PLC0415

    Q = P if np.allclose(P[0], P[-1]) else np.vstack([P, P[:1]])
    path = MplPath(Q)
    S, N = [], []
    for a, b in zip(Q[:-1], Q[1:], strict=False):
        L = np.hypot(*(b - a))
        if L < 1e-6:
            continue
        e = (b - a) / L
        n = np.array([e[1], -e[0]])
        if path.contains_point((a + b) / 2 + 0.7 * n):
            n = -n
        k = max(1, int(L / step))
        S.append(a + (np.arange(k) + 0.5)[:, None] / k * (b - a))
        N.append(np.repeat(n[None], k, 0))
    return (np.vstack(S), np.vstack(N)) if S else (None, None)


def area_m2(P: Sequence[np.ndarray], M: float) -> float:
    """Area of pixel rings in m^2 (shoelace)."""
    return float(sum(abs(0.5 * np.sum(p[:, 0] * np.roll(p[:, 1], 1) - np.roll(p[:, 0], 1) * p[:, 1]))
                     for p in P) * M * M)


def sunpos(t_utc: dt.datetime, lat: float, lon: float) -> tuple[float, float]:
    """NOAA solar position: (azimuth deg from north clockwise, elevation deg); no refraction."""
    jd = (t_utc - dt.datetime(2000, 1, 1, 12)) / dt.timedelta(days=1) + 2451545.0
    T = (jd - 2451545.0) / 36525
    L0 = (280.46646 + T * (36000.76983 + T * 0.0003032)) % 360
    M = 357.52911 + T * (35999.05029 - 0.0001537 * T)
    e = 0.016708634 - T * (0.000042037 + 0.0000001267 * T)
    Mr = math.radians(M)
    C = (math.sin(Mr) * (1.914602 - T * (0.004817 + 0.000014 * T))
         + math.sin(2 * Mr) * (0.019993 - 0.000101 * T) + math.sin(3 * Mr) * 0.000289)
    tl = L0 + C
    om = 125.04 - 1934.136 * T
    lam = tl - 0.00569 - 0.00478 * math.sin(math.radians(om))
    eps0 = 23 + (26 + (21.448 - T * (46.815 + T * (0.00059 - T * 0.001813))) / 60) / 60
    eps = eps0 + 0.00256 * math.cos(math.radians(om))
    dec = math.degrees(math.asin(math.sin(math.radians(eps)) * math.sin(math.radians(lam))))
    y = math.tan(math.radians(eps / 2)) ** 2
    L0r = math.radians(L0)
    eot = 4 * math.degrees(y * math.sin(2 * L0r) - 2 * e * math.sin(Mr)
                           + 4 * e * y * math.sin(Mr) * math.cos(2 * L0r)
                           - 0.5 * y * y * math.sin(4 * L0r) - 1.25 * e * e * math.sin(2 * Mr))
    mins = t_utc.hour * 60 + t_utc.minute + t_utc.second / 60
    tst = (mins + eot + 4 * lon) % 1440
    ha = tst / 4 - 180
    la, d, h = math.radians(lat), math.radians(dec), math.radians(ha)
    cz = math.sin(la) * math.sin(d) + math.cos(la) * math.cos(d) * math.cos(h)
    zen = math.acos(max(-1, min(1, cz)))
    az = math.degrees(math.atan2(math.sin(h), math.cos(h) * math.sin(la) - math.tan(d) * math.cos(la))) + 180
    return az % 360, 90 - math.degrees(zen)


# ----------------------------------------------------------------------------------- tiles
class TileSource:
    """The cached z18 tiles of one scene: ``<dir>/18_<x>_<y>.jpg``."""

    def __init__(self, folder: str | os.PathLike):
        self.dir = Path(folder)
        self._tiles: set | None = None

    @property
    def tiles(self) -> set:
        if self._tiles is None:
            self._tiles = ({tuple(map(int, f[:-4].split("_")[1:])) for f in os.listdir(self.dir)
                            if f.startswith(f"{Z}_") and f.endswith(".jpg")}
                           if self.dir.is_dir() else set())
        return self._tiles

    def refresh(self) -> None:
        self._tiles = None

    def crop(self, x0, y0, x1, y1, rgb: bool = False):
        """Global-pixel window -> (gray float32, have bool) or (rgb uint8, have)."""
        from PIL import Image  # noqa: PLC0415

        x0, y0, x1, y1 = int(x0), int(y0), int(x1), int(y1)
        g = np.zeros((y1 - y0, x1 - x0, 3), np.uint8)
        have = np.zeros((y1 - y0, x1 - x0), bool)
        TS = self.tiles
        for tx in range(x0 // 256, (x1 - 1) // 256 + 1):
            for ty in range(y0 // 256, (y1 - 1) // 256 + 1):
                if (tx, ty) not in TS:
                    continue
                try:
                    a = np.asarray(Image.open(self.dir / f"{Z}_{tx}_{ty}.jpg").convert("RGB"))
                except Exception:  # noqa: BLE001 - a broken tile reads as missing
                    continue
                gx0, gy0 = tx * 256, ty * 256
                sx0, sy0 = max(x0, gx0), max(y0, gy0)
                sx1, sy1 = min(x1, gx0 + 256), min(y1, gy0 + 256)
                g[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = a[sy0 - gy0:sy1 - gy0, sx0 - gx0:sx1 - gx0]
                have[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0] = True
        if rgb:
            return g, have
        return g.astype(np.float32).mean(2), have


@dataclass
class Scene:
    """One dated satellite image and its geometry.

    ``lean``: roof shift per metre of height, metres (east, south) (``L_E``, ``L_S``);
    ``reg_m``: OSM -> scene registration, metres (east, south) (``r_E``, ``r_S``);
    ``shadow_bearing`` / ``sun_el``: the sun at capture (None: unsolved, stereo only);
    ``dark``: shadow grey threshold (None: from the scene's grey percentiles);
    ``covered``: footprint ids inside the scene polygon (None: all).
    """

    name: str
    tiles: TileSource
    date: str | None = None
    sensor: str | None = None
    lean: np.ndarray = field(default_factory=lambda: np.zeros(2))
    reg_m: np.ndarray = field(default_factory=lambda: np.zeros(2))
    shadow_bearing: float | None = None
    sun_el: float | None = None
    dark: float | None = None
    covered: set | None = None
    meta: dict = field(default_factory=dict)

    @property
    def cot(self) -> float:
        return 0.0 if self.sun_el is None else 1 / math.tan(math.radians(self.sun_el))

    def to_json(self) -> dict:
        return dict(name=self.name, tiles=str(self.tiles.dir), date=self.date, sensor=self.sensor,
                    L_E=float(self.lean[0]), L_S=float(self.lean[1]), r_E=float(self.reg_m[0]),
                    r_S=float(self.reg_m[1]), shadow_bearing=self.shadow_bearing,
                    sun_el=self.sun_el, dark=self.dark, meta=self.meta)

    @classmethod
    def from_json(cls, d: dict, tiles_dir: str | os.PathLike | None = None) -> Scene:
        return cls(name=d["name"], tiles=TileSource(tiles_dir or d["tiles"]), date=d.get("date"),
                   sensor=d.get("sensor"), lean=np.array([d["L_E"], d["L_S"]], float),
                   reg_m=np.array([d["r_E"], d["r_S"]], float),
                   shadow_bearing=d.get("shadow_bearing"), sun_el=d.get("sun_el"),
                   dark=d.get("dark"), meta=d.get("meta") or {})


# --------------------------------------------------------------------------------- Wayback
def _get_json(url: str, timeout: float = 40):
    return json.load(urllib.request.urlopen(urllib.request.Request(url, headers=UA),
                                            timeout=timeout))


def wayback_config(cache: str | os.PathLike) -> dict:
    """Release id -> {itemURL, metadataLayerUrl, itemTitle, ...}; cached in ``cache``."""
    p = Path(cache)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    cfg = _get_json(WAYBACK_CONFIG_URL, 60)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return cfg


def release_date(cfg: dict, rel: str) -> str:
    """The release date (YYYY-MM-DD) from its title."""
    import re  # noqa: PLC0415

    return re.search(r"(\d{4}-\d{2}-\d{2})", cfg[rel]["itemTitle"]).group(1)


def identify(cfg: dict, rel: str, lat: float, lon: float, geometry: bool = False) -> dict | None:
    """The scene a Wayback release shows at a point: {attrs: SRC_DATE, SRC_DESC, SRC_RES, ...,
    rings}. Layers 5 then 4 of the release's metadata service (the z18-19 source layers)."""
    url = cfg[rel]["metadataLayerUrl"]
    p = dict(geometry=f"{lon},{lat}", geometryType="esriGeometryPoint", inSR="4326", outSR="4326",
             spatialRel="esriSpatialRelIntersects",
             outFields="SRC_DATE,SRC_DESC,SRC_RES,SRC_ACC,NICE_NAME,NICE_DESC",
             returnGeometry="true" if geometry else "false", f="json")
    if geometry:
        p["maxAllowableOffset"] = "0.0003"
    for lay in (5, 4):
        try:
            r = _get_json(f"{url}/{lay}/query?" + urllib.parse.urlencode(p), 60)
        except Exception as exc:  # noqa: BLE001
            log.warning("[satellite] identify %s layer %s failed: %s", rel, lay, exc)
            continue
        fs = r.get("features") or []
        if fs:
            return dict(attrs=fs[0]["attributes"], rings=(fs[0].get("geometry") or {}).get("rings"))
    return None


def scene_name(attrs: dict) -> str:
    """``YYYY-MM-DD_<sensor>`` from identify attributes (SRC_DATE 20260210, SRC_DESC LG01)."""
    d = str(attrs["SRC_DATE"])
    return f"{d[:4]}-{d[4:6]}-{d[6:8]}_{str(attrs.get('SRC_DESC') or '').replace(' ', '')}"


def scene_outlines(cfg: dict, rel: str, bbox: Sequence[float]) -> dict:
    """``{scene name: rings}`` of every scene release ``rel`` shows inside ``bbox`` (lon/lat
    west, south, east, north): the source layer's polygons (layer 5, else 4) intersecting it, all
    polygons of one scene merged (2026-10-09: a scene is often several polygons in one release;
    Honolulu 2025 WV02 and WV03 two each, 2024 WV03 four, and the first one found held 218 of the
    region's 3,114 footprints). {} when the service does not answer."""
    from shapely.geometry import Polygon  # noqa: PLC0415
    from shapely.ops import unary_union  # noqa: PLC0415

    url = cfg[rel]["metadataLayerUrl"]
    p = dict(geometry=",".join(str(float(v)) for v in bbox), geometryType="esriGeometryEnvelope",
             inSR="4326", outSR="4326", spatialRel="esriSpatialRelIntersects",
             outFields="SRC_DATE,SRC_DESC,SRC_RES,SRC_ACC,NICE_NAME,NICE_DESC",
             returnGeometry="true", maxAllowableOffset="0.0003", f="json")
    for lay in (5, 4):
        try:
            r = _get_json(f"{url}/{lay}/query?" + urllib.parse.urlencode(p), 60)
        except Exception as exc:  # noqa: BLE001
            log.warning("[satellite] outlines %s layer %s failed: %s", rel, lay, exc)
            continue
        fs = r.get("features") or []
        if not fs:
            continue
        parts: dict = {}
        for f in fs:
            g = None
            for ring in (f.get("geometry") or {}).get("rings") or []:
                if len(ring) >= 4:                      # even-odd: holes and parts
                    q = Polygon(np.asarray(ring, float)[:, :2]).buffer(0)
                    g = q if g is None else g.symmetric_difference(q)
            if g is not None and not g.is_empty:
                parts.setdefault(scene_name(f["attributes"]), []).append(g)
        out = {}
        for name, gs in parts.items():
            u = unary_union(gs)
            polys = [u] if u.geom_type == "Polygon" else [q for q in getattr(u, "geoms", []) if q.geom_type == "Polygon"]
            out[name] = [[list(c) for c in q.exterior.coords] for q in polys] + \
                        [[list(c) for c in h.coords] for q in polys for h in q.interiors]
        return out
    return {}


def scene_polygon(cfg: dict, rel: str, name: str, points: Iterable[tuple[float, float]],
                  bbox: Sequence[float] | None = None) -> list | None:
    """The outline (lon/lat rings) of scene ``name`` in release ``rel``. With ``bbox`` (lon/lat
    west, south, east, north): all its polygons there (:func:`scene_outlines`); else, or when
    that finds none, the polygon identify gives at the first of ``points`` ``(lat, lon)`` that
    shows the scene (one polygon only). A release mosaics several captures, so outside the
    outline its tiles show another image, whose lean and sun differ."""
    if bbox is not None:
        rings = scene_outlines(cfg, rel, bbox).get(name)
        if rings:
            return rings
    for lat, lon in points:
        got = identify(cfg, rel, lat, lon, geometry=True)
        if got and scene_name(got["attrs"]) == name and got.get("rings"):
            return got["rings"]
    return None


def in_rings(rings: Sequence, lon, lat) -> np.ndarray:
    """Points inside Esri polygon rings (even-odd over every ring: holes and parts)."""
    from matplotlib.path import Path as MplPath  # noqa: PLC0415

    pts = np.c_[np.ravel(lon), np.ravel(lat)]
    odd = np.zeros(len(pts), bool)
    for r in rings or []:
        if len(r) >= 3:
            odd ^= MplPath(np.asarray(r, float)[:, :2]).contains_points(pts)
    return odd


def fetch_tiles(url_template: str, folder: str | os.PathLike, tiles: Iterable[tuple[int, int]],
                *, offline: bool = False) -> dict:
    """Fetch missing z18 tiles of one release into ``folder`` (4 concurrent, 3 tries each).
    ``offline``: count what would be fetched, fetch nothing. Returns {needed, missing, fetched}."""
    d = Path(folder)
    d.mkdir(parents=True, exist_ok=True)
    jobs = []
    for x, y in sorted(set(tiles)):
        p = d / f"{Z}_{x}_{y}.jpg"
        if not (p.exists() and p.stat().st_size > 0):
            jobs.append((url_template.format(level=Z, row=y, col=x), p))
    out = dict(needed=len(set(tiles)), missing=len(jobs), fetched=0)
    if offline or not jobs:
        return out

    def get(j):
        u, p = j
        for a in range(3):
            try:
                b = urllib.request.urlopen(urllib.request.Request(u, headers=UA), timeout=40).read()
                p.write_bytes(b)
                time.sleep(0.03)
                return 1
            except Exception:  # noqa: BLE001
                time.sleep(2 + 2 * a)
        return 0

    with ThreadPoolExecutor(MAX_CONCURRENT) as ex:
        out["fetched"] = sum(ex.map(get, jobs))
    return out


# ----------------------------------------------------------------------- geometry solves
def _peaks_for(P, gray, gx, gy, off, V, M, n=3):
    from scipy import ndimage as ndi  # noqa: PLC0415

    S, N = outline(P - off)
    if S is None:
        return None
    if len(S) > 600:
        j = np.linspace(0, len(S) - 1, 600).astype(int)
        S, N = S[j], N[j]
    sc = np.zeros(len(V))
    for i0 in range(0, len(V), 3000):
        q = S[None] + V[i0:i0 + 3000, None]
        ys, xs = q[..., 1].ravel(), q[..., 0].ravel()
        a = ndi.map_coordinates(gx, [ys, xs], order=1).reshape(q.shape[:2])
        b = ndi.map_coordinates(gy, [ys, xs], order=1).reshape(q.shape[:2])
        sc[i0:i0 + 3000] = np.abs(a * N[None, :, 0] + b * N[None, :, 1]).mean(1)
    order = np.argsort(-sc)
    pk: list = []
    for j in order:
        if all(np.hypot(*(V[j] - V[p])) * M >= 6 for p in pk):
            pk.append(j)
        if len(pk) == n:
            break
    return [(V[p] * M).tolist() for p in pk], [float(sc[p]) for p in pk], float(np.median(sc))


def fit_lean(src: TileSource, footprints: Sequence[dict], M: float, *,
             search_m: float = 95.0) -> dict:
    """Lean vector ``L`` and registration ``r`` of one scene (w3_geom, 2026-10-07c).

    ``footprints``: dicts with ``P`` (list of global-pixel rings, OSM position), ``tag`` (OSM
    height or None), ``src`` (height source), ``fid``. Towers tagged 40-175 m: the 3 best outline
    shifts each (|shift| <= ``search_m``); registration from low buildings' best shifts (median);
    RANSAC over tower candidates: shift_i - r_low = (H_i - 9 m) L. Truth heights are never read.
    Returns the ``lean`` dict of w3 (L_E, L_S, k, az, r_E, r_S, inliers ...) or {error}.
    """
    from scipy import ndimage as ndi  # noqa: PLC0415

    TS = src.tiles

    def covered(bb, pad):
        xs = range(int((bb[0] - pad) // 256), int((bb[2] + pad) // 256) + 1)
        ys = range(int((bb[1] - pad) // 256), int((bb[3] + pad) // 256) + 1)
        return all((x, y) in TS for x in xs for y in ys)

    R = int(search_m / M)
    V = np.array([(dx, dy) for dy in range(-R, R + 1, 2) for dx in range(-R, R + 1, 2)
                  if 4 / M <= math.hypot(dx, dy) <= R], float)
    TW = [b for b in footprints if b["tag"] and 40 <= b["tag"] <= 175 and area_m2(b["P"], M) > 250
          and covered(b["bb"], 0)]
    TW.sort(key=lambda b: (b["src"] != "osm_tag", -b["tag"]))
    TW = TW[:40]
    obs = []
    for b in TW:
        x0, y0, x1, y1 = b["bb"]
        off = np.array([x0 - R - 8, y0 - R - 8])
        gray, hv = src.crop(x0 - R - 8, y0 - R - 8, x1 + R + 8, y1 + R + 8)
        gx = ndi.gaussian_filter(gray, 1.0, order=(0, 1))
        gy = ndi.gaussian_filter(gray, 1.0, order=(1, 0))
        bad = ~ndi.binary_erosion(hv, iterations=4, border_value=1)
        gx[bad] = 0
        gy[bad] = 0
        r = _peaks_for(b["P"][0], gray, gx, gy, off, V, M)
        if r:
            obs.append(dict(fid=b["fid"], H=b["tag"], cands=r[0], scores=r[1], base=r[2]))
    RL = int(12 / M)
    VL = np.array([(dx, dy) for dy in range(-RL, RL + 1) for dx in range(-RL, RL + 1)], float)
    lowc = [b for b in footprints if (b["src"] == "default" or (b["tag"] and b["tag"] <= 8))
            and len(b["P"]) == 1 and 100 < area_m2(b["P"], M) < 1500 and covered(b["bb"], 0)]
    rng = np.random.default_rng(0)
    if len(lowc) > 350:
        lowc = [lowc[i] for i in rng.choice(len(lowc), 350, replace=False)]
    lows = []
    for b in lowc:
        x0, y0, x1, y1 = b["bb"]
        off = np.array([x0 - RL - 6, y0 - RL - 6])
        gray, hv = src.crop(x0 - RL - 6, y0 - RL - 6, x1 + RL + 6, y1 + RL + 6)
        gx = ndi.gaussian_filter(gray, 1.0, order=(0, 1))
        gy = ndi.gaussian_filter(gray, 1.0, order=(1, 0))
        bad = ~ndi.binary_erosion(hv, iterations=4, border_value=1)
        gx[bad] = 0
        gy[bad] = 0
        S, N = outline(b["P"][0] - off, 1.0)
        if S is None:
            continue
        q = S[None] + VL[:, None]
        ys, xs = q[..., 1].ravel(), q[..., 0].ravel()
        a_ = ndi.map_coordinates(gx, [ys, xs], order=1).reshape(q.shape[:2])
        b_ = ndi.map_coordinates(gy, [ys, xs], order=1).reshape(q.shape[:2])
        sc = np.abs(a_ * N[None, :, 0] + b_ * N[None, :, 1]).mean(1)
        if sc.max() / max(np.median(sc), 1e-6) > 1.3:
            lows.append(VL[np.argmax(sc)] * M)
    lows = np.array(lows) if lows else np.zeros((1, 2))
    r_low = np.median(lows, 0)
    mad = np.median(np.abs(lows - r_low), 0)
    H_LOW = 9.0
    best = None
    for o in obs:
        for cnd in o["cands"]:
            L = (np.array(cnd) - r_low) / (o["H"] - H_LOW)
            if np.hypot(*L) > 0.9:
                continue
            res = []
            for o2 in obs:
                d = [np.hypot(*(np.array(c2) - (r_low + (o2["H"] - H_LOW) * L))) for c2 in o2["cands"]]
                k = int(np.argmin(d))
                res.append((d[k], k))
            inl = [(o2, k) for o2, (d, k) in zip(obs, res, strict=False) if d < max(6.0, 0.06 * o2["H"])]
            score = len(inl) + 0.001 * sum(o2["scores"][k] / o2["base"] for o2, k in inl)
            if best is None or score > best[0]:
                best = (score, inl)
    if best is None or len(best[1]) < 2:
        return dict(error="lean solve failed", n_towers=len(obs))
    inl = best[1]
    Hs = np.array([o["H"] - H_LOW for o, k in inl])
    Y = np.array([o["cands"][k] for o, k in inl]) - r_low
    L = (Hs[:, None] * Y).sum(0) / (Hs ** 2).sum()
    r = r_low - H_LOW * L
    resid = np.hypot(*(Y - Hs[:, None] * L).T)
    kk = [float(np.hypot(*y) / h) for y, h in zip(Y, Hs, strict=False)]
    return dict(L_E=float(L[0]), L_S=float(L[1]), k=float(np.hypot(*L)),
                k_se=float(np.std(kk, ddof=1) / math.sqrt(len(kk))) if len(kk) > 1 else None,
                az=float(math.degrees(math.atan2(L[0], -L[1])) % 360), r_E=float(r[0]),
                r_S=float(r[1]), r_low_mad=mad.tolist(), n_low=len(lows), n_inliers=len(inl),
                n_towers=len(obs), resid_rms_m=float(np.sqrt((resid ** 2).mean())),
                inlier_fids=[o["fid"] for o, k in inl], tower_fids=[o["fid"] for o in obs])


def solve_sun(src: TileSource, footprints_by_fid: dict, tower_fids: Sequence, reg_m: np.ndarray,
              date_yyyymmdd: str, lat0: float, lon0: float, M: float) -> dict | None:
    """Capture time on the scene date from the shadows of tagged towers (w3_geom, fixed).

    The search spans +-4 h around ~10:30 local solar time, ``m0 = (10.5 - lon/15) h`` UTC
    (2026-10-07c: the old window was centred on ~04:00 UTC the next day: the time zone was
    subtracted twice). Missing tiles are masked. Returns {time_utc, az, el, shadow_bearing, cot,
    score, ...} or None.
    """
    from scipy import ndimage as ndi  # noqa: PLC0415

    d0 = dt.datetime.strptime(str(date_yyyymmdd), "%Y%m%d")
    m0 = int((10.5 - lon0 / 15) * 60)
    rp = np.asarray(reg_m, float) / M
    TWs = [(footprints_by_fid[f]["tag"], footprints_by_fid[f]["P"][0] + rp) for f in list(tower_fids)[:25]]

    def contrast(gray, S, N, ub, Ls, hv):
        lead = (N @ ub) > 0.35
        S2, N2 = S[lead], N[lead]
        off_ = 1.5 / M
        if len(S2) < 3:
            return None
        q = S2[None] + ub[None, None] * Ls[:, None, None] / M
        qi, qo = q - N2[None] * off_, q + N2[None] * off_
        gi = ndi.map_coordinates(gray, [qi[..., 1].ravel(), qi[..., 0].ravel()], order=1).reshape(qi.shape[:2])
        go = ndi.map_coordinates(gray, [qo[..., 1].ravel(), qo[..., 0].ravel()], order=1).reshape(qo.shape[:2])
        d = go - gi
        ok = np.ones(d.shape, bool)
        for qq in (qi, qo, q):
            ix = np.round(qq[..., 0]).astype(int)
            iy = np.round(qq[..., 1]).astype(int)
            inb = (ix >= 0) & (iy >= 0) & (ix < hv.shape[1]) & (iy < hv.shape[0])
            ok &= inb & hv[np.clip(iy, 0, hv.shape[0] - 1), np.clip(ix, 0, hv.shape[1] - 1)]
        d = np.where(ok, d, np.nan)
        frac = ok.mean(1)
        with np.errstate(all="ignore"):
            v = np.nanmedian(d, 1)
        v[(frac < 0.6) | ~np.isfinite(v)] = np.nan
        return v if np.isfinite(v).any() else None

    crops = []
    for H, P in TWs:
        ext = (H * 3.0 + 30) / M
        x0, y0 = P.min(0) - ext
        x1, y1 = P.max(0) + ext
        gray, hv = src.crop(x0, y0, x1, y1)
        S, N = outline(P - np.array([int(x0), int(y0)]))
        hv = ndi.binary_erosion(hv, iterations=3, border_value=1)
        if hv.mean() > 0.3 and S is not None:
            crops.append((H, gray, S, N, hv))
    sc = []
    for m in range(m0 - 240, m0 + 241, 3):
        T = d0 + dt.timedelta(minutes=m)
        az, el = sunpos(T, lat0, lon0)
        if el < 12:
            continue
        bdir = (az + 180) % 360
        cot = 1 / math.tan(math.radians(el))
        ub = uv(bdir)
        s, n = 0.0, 0
        for H, gray, S, N, hv in crops:
            Lp = H * cot
            v = contrast(gray, S, N, ub, np.arange(max(5, Lp - 10), Lp + 10, 0.5), hv)
            if v is not None:
                s += np.nanmax(v)
                n += 1
        if n >= 3:
            sc.append((s / n, T, az, el, bdir, cot, n))
    if not sc:
        return None
    sc.sort(key=lambda x: -x[0])
    s, T, az, el, bdir, cot, nn = sc[0]
    alt = [x for x in sc if abs((x[1] - T).total_seconds()) > 1800]
    return dict(time_utc=str(T), az=az, el=el, shadow_bearing=bdir, cot=cot, score=float(s),
                n_towers=len(crops), n_used=nn, second_best_time=str(alt[0][1]) if alt else None,
                second_score=float(alt[0][0]) if alt else None)


__all__ = ["Scene", "TileSource", "sunpos", "mpp", "uv", "outline", "area_m2", "wayback_config",
           "release_date", "identify", "scene_name", "scene_outlines", "scene_polygon", "in_rings",
           "fetch_tiles",
           "fit_lean", "solve_sun", "Z", "MAX_CONCURRENT"]
