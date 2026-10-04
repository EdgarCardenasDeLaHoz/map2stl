"""Footprint-first detection for elevated panoramas (F-DET6; Cartagena seed_5 first).

The panorama path detects segments in the building mask and then matches them to OSM
(``_pano/detect.py``). From a drone that fails in specific ways (2026-10-04 review): towers
merge into one mask band, the nearest candidate always wins the match, boxes run into the water,
and roofs below the horizon are invisible to sky-based steps. This module inverts the order for
elevated cameras: find the camera from the ground, then measure every OSM footprint in the image.

    pano = load_seed_pano("cartagena", "seed_5")            # cached spin views, no API spend
    shore = shore_distance_table(pano, coast_lines)         # OSM distance to land per bearing
    pose = fit_pose_from_waterline(pano, shore)             # heading offset, camera height, pitch

Geometry (stitched pano of pinhole views): column x has a capture-frame heading
``pano.frame_heading[x]``; geographic bearing = frame heading + ``pose.offset_deg``. A row's
elevation is ``pitch + atan((H/2 - row) / f)``, the same for every column (each column comes
from the central +-15 deg of a 75-deg view, where the off-centre error is under a pixel at the
rows that matter).

Waterline pose (the user's idea: ground features seen from the drone, matched to OSM). From a
camera ``h`` m above the water, the near shore at distance ``d`` along a bearing appears
``atan(h / d)`` below the horizon. The observed top of the near water run in each column, against
the OSM distance to land along that column's bearing, fits the heading offset, ``h`` and a pitch
correction; open-sea columns (no land within reach) must sit at the horizon.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from ._core.segmentation import _ADE20K_WATER_CLASSES

BEARING_BIN_DEG = 0.1
N_BEARINGS = int(round(360 / BEARING_BIN_DEG))
#: Rays longer than this count as open sea (no land in reach).
MAX_SHORE_M = 6000.0
M_PER_DEG_LAT = 111_320.0


@dataclass
class Pano:
    name: str
    lat: float
    lon: float
    rgb: np.ndarray                 # H x W x 3 uint8
    labels: np.ndarray              # H x W int16 ADE20K class per pixel
    frame_heading: np.ndarray       # W, degrees in the capture frame
    f_px: float
    pitch_deg: float

    @property
    def height(self) -> int:
        return self.rgb.shape[0]

    @property
    def width(self) -> int:
        return self.rgb.shape[1]

    def elevation_deg(self, row):
        return self.pitch_deg + np.degrees(np.arctan((self.height / 2.0 - np.asarray(row, float))
                                                     / self.f_px))

    def row_of_elevation(self, elev_deg):
        return self.height / 2.0 - self.f_px * np.tan(np.radians(np.asarray(elev_deg, float)
                                                                 - self.pitch_deg))


@dataclass(frozen=True)
class PanoPose:
    offset_deg: float        # geographic bearing = frame heading + offset
    camera_h_m: float        # above the water
    pitch_fix_deg: float     # added to the capture pitch
    misfit_deg: float        # robust spread of the waterline residuals
    n_cols: int


# --------------------------------------------------------------------------- loading


def _stitch_labels(views: list[dict], fov_deg: float, step_deg: float) -> np.ndarray:
    """ADE20K label maps of the spin views, cropped and stitched like ``stitch_pano_views``."""
    from ._core.segmentation import _ensure_label_map

    vs = sorted(views, key=lambda v: float(v["geo_heading"]))
    h, w = vs[0]["image"].shape[:2]
    f = 0.5 * w / math.tan(math.radians(fov_deg) / 2)
    half = int(round(f * math.tan(math.radians(step_deg / 2))))
    x0, x1 = max(0, w // 2 - half), min(w, w // 2 + half)
    crops = []
    for v in vs:
        if v["image"].shape[0] != h:
            continue
        lm = _ensure_label_map(v["image"])
        crops.append(np.full((h, x1 - x0), -1, np.int16) if lm is None
                     else np.asarray(lm[:, x0:x1], np.int16))
    return np.concatenate(crops, axis=1)


def load_seed_pano(region: str, seed_name: str, step_deg: float = 30.0) -> Pano:
    """Rebuild a seed's stitched pano from its spin views the way the pipeline captures them
    (Photo Sphere seeds by pano id). Views come from ``runs/image_cache`` when cached."""
    import json
    from pathlib import Path

    from ._core.pano import stitch_pano_views
    from ._pano.capture import _capture_pano_views
    from .region_data import _load_site_seed_urls
    from .region_types import SkylinePoint
    from .streetview_io import _parse_streetview_url, _resolve_api_key

    urls = _load_site_seed_urls(region)
    idx = int(seed_name.split("_")[1]) - 1
    lat, lon, heading, fov, pitch, pano_id = _parse_streetview_url(urls[idx])
    # Resolve exactly as ``_pano/orchestrator.py`` does: the cache pins the snapped position
    # and the API-form pano id (a URL's ``CIHM0og...`` Photo Sphere id resolves to
    # ``CAoSF0...``). The pipeline then captures with that id and, the ids differing, not as a
    # photosphere -- so its cached images are keyed that way.
    cache = Path(__file__).parent / "_pano" / "runs" / "seed_resolution_cache.json"
    is_photosphere = pano_id is not None
    if cache.exists():
        hit = json.loads(cache.read_text(encoding="utf-8")).get(
            f"{seed_name}|{lat:.6f}|{lon:.6f}|{pano_id or ''}")
        if hit:
            is_photosphere = pano_id is not None and hit.get("pano_id") == pano_id
            lat, lon, pano_id = float(hit["lat"]), float(hit["lon"]), hit.get("pano_id")
    seed = SkylinePoint(name=seed_name, lat=lat, lon=lon, heading=heading, source="seed",
                        score=1.0, fov=fov, pitch=pitch, pano_id=pano_id)
    headings = tuple(float(x) for x in np.arange(0.0, 360.0, step_deg))
    prefetch, eff_pitch, _cached = _capture_pano_views(
        seed, _resolve_api_key(), headings, is_photosphere=is_photosphere)
    views = [v for v in prefetch if v.get("image") is not None]
    rgb, frame = stitch_pano_views([{"image": v["image"], "geo_heading": v["geo_heading"]}
                                    for v in views], fov, step_deg)
    labels = _stitch_labels(views, fov, step_deg)
    w_view = views[0]["image"].shape[1]
    f = 0.5 * w_view / math.tan(math.radians(fov) / 2)
    return Pano(seed_name, lat, lon, rgb, labels, np.asarray(frame, float), f, float(eff_pitch))


# --------------------------------------------------------------------------- waterline


def near_water_top(pano: Pano, min_run: int = 6) -> np.ndarray:
    """Per column: the row where the bottom water run ends going up (NaN when the column's
    bottom is not water). From an elevated camera over the bay this is the near shoreline, or
    the horizon in open-sea columns."""
    water = np.isin(pano.labels, _ADE20K_WATER_CLASSES)
    h, w = water.shape
    out = np.full(w, np.nan)
    for x in range(w):
        col = water[:, x]
        if not col[h - min_run:].all():
            continue
        y = h - 1
        while y > 0 and col[y - 1]:
            y -= 1
        out[x] = y
    return out


def _segments(lines, lat: float, lon: float) -> np.ndarray:
    """All straight pieces of ``lines`` (lon/lat) as an (n, 4) array of local-metre endpoints."""
    import shapely

    kx = M_PER_DEG_LAT * math.cos(math.radians(lat))
    segs = []
    for g in lines:
        for part in getattr(g, "geoms", [g]):
            c = shapely.get_coordinates(part)
            if len(c) < 2:
                continue
            xy = np.column_stack([(c[:, 0] - lon) * kx, (c[:, 1] - lat) * M_PER_DEG_LAT])
            segs.append(np.hstack([xy[:-1], xy[1:]]))
    return np.vstack(segs) if segs else np.zeros((0, 4))


def shore_distance_table(lat: float, lon: float, lines, max_m: float = MAX_SHORE_M,
                         min_m: float = 5.0) -> np.ndarray:
    """Distance (m) from the camera to the first shoreline crossing per 0.1-deg bearing.

    ``lines``: shapely (Multi)LineStrings in lon/lat: OSM coastline plus water-polygon
    boundaries. ``inf`` where no shore lies within ``max_m`` (open sea). Ray-segment
    intersection in numpy over every shoreline piece at once (shapely ray-by-ray took 5 min
    on Cartagena's coastline; this takes about a second).
    """
    seg = _segments(lines, lat, lon)
    out = np.full(N_BEARINGS, np.inf)
    if not len(seg):
        return out
    p, q = seg[:, :2], seg[:, 2:]
    e = q - p                                            # segment directions
    keep = np.hypot(p[:, 0], p[:, 1]).clip(None, np.hypot(q[:, 0], q[:, 1])) < max_m + 1e3
    p, e = p[keep], e[keep]
    t_all = np.radians(np.arange(N_BEARINGS) * BEARING_BIN_DEG)
    for i0 in range(0, N_BEARINGS, 400):
        t = t_all[i0:i0 + 400]
        r = np.stack([np.sin(t), np.cos(t)], axis=1)     # unit rays (east, north)
        # solve s*r = p + u*e  ->  cross products (2-D)
        den = r[:, None, 0] * e[None, :, 1] - r[:, None, 1] * e[None, :, 0]
        with np.errstate(divide="ignore", invalid="ignore"):
            s_ = (p[None, :, 0] * e[None, :, 1] - p[None, :, 1] * e[None, :, 0]) / den
            u = (p[None, :, 0] * r[:, None, 1] - p[None, :, 1] * r[:, None, 0]) / den
        ok = (np.abs(den) > 1e-12) & (u >= 0) & (u <= 1) & (s_ > min_m) & (s_ <= max_m)
        s_ = np.where(ok, s_, np.inf)
        out[i0:i0 + 400] = s_.min(axis=1)
    return out


def fit_pose_from_waterline(pano: Pano, shore_m: np.ndarray,
                            heights_m=None, offset_step_deg: float = 0.5,
                            max_shore_m: float = 4000.0) -> PanoPose:
    """Heading offset, camera height and pitch correction from the near waterline.

    For each candidate offset and height, the predicted elevation of the near shore in every
    observed column is ``-atan(h / d)`` (``d`` = OSM distance to land along the column's
    bearing; open sea -> 0). The pitch correction is the median residual; the misfit is the
    mean absolute residual after it over the best 90 % of columns (robust to a breakwater or
    an island OSM has and the photo does not, yet not blind to a minority of shore columns).
    """
    if heights_m is None:
        heights_m = np.exp(np.linspace(math.log(3.0), math.log(400.0), 60))
    y = near_water_top(pano)
    cols = np.flatnonzero(np.isfinite(y))
    if cols.size < 50:
        raise ValueError(f"only {cols.size} columns end in water; no waterline to fit")
    e_obs = pano.elevation_deg(y[cols])
    frame = pano.frame_heading[cols]
    best = None
    for off in np.arange(0.0, 360.0, offset_step_deg):
        k = np.round(((frame + off) % 360.0) / BEARING_BIN_DEG).astype(int) % N_BEARINGS
        d = shore_m[k]
        d = np.where(d <= max_shore_m, d, np.inf)
        for hm in heights_m:
            e_pred = -np.degrees(np.arctan(hm / d))          # inf -> 0 (horizon)
            r = e_obs - e_pred
            fix = float(np.median(r))
            # trimmed mean, not the median: when most columns see open sea (residual 0 for
            # any height) the median ignores the shore entirely and every height scores 0
            a = np.sort(np.abs(r - fix))
            mis = float(a[: max(1, int(0.9 * len(a)))].mean())
            if best is None or mis < best[0]:
                best = (mis, float(off), float(hm), fix)
    mis, off, hm, fix = best
    return PanoPose(off, hm, -fix, mis, int(cols.size))


# --------------------------------------------------------------------------- footprints

#: ADE20K classes counted as building surface when tracing a building's top: building,
#: house, skyscraper, tower. ("wall" is left out: sea walls and fences.)
BUILDING_CLASSES = (1, 25, 48, 84)


@dataclass(frozen=True)
class Footprint:
    name: str
    ring: np.ndarray                 # (k, 2) lon/lat
    osm_height_m: float | None = None


@dataclass(frozen=True)
class Measured:
    footprint: int                   # index into the input list
    name: str
    x0: int
    x1: int
    top_row: float
    bottom_row: float                # lowest visible row (the base, or a nearer roof)
    base_row: float                  # predicted base row
    dist_m: float
    height_m: float
    n_cols: int
    base_visible: bool
    osm_height_m: float | None


def bearing_columns(pano: Pano, pose: PanoPose):
    """(unwrapped bearing per column, first bearing): bearing -> column by ``np.interp``."""
    b = (pano.frame_heading + pose.offset_deg) % 360.0
    bu = np.degrees(np.unwrap(np.radians(b)))
    return bu, bu[0]


def column_of(bearing_deg, bu, b0):
    t = b0 + (np.asarray(bearing_deg, float) - b0) % 360.0
    return np.interp(t, bu, np.arange(len(bu)), left=np.nan, right=np.nan)


def _row_of(pano: Pano, pose: PanoPose, elev_deg):
    return pano.row_of_elevation(np.asarray(elev_deg, float) - pose.pitch_fix_deg)


def _elev_of(pano: Pano, pose: PanoPose, row):
    return pano.elevation_deg(row) + pose.pitch_fix_deg


def _local(lat0: float, lon0: float, ring: np.ndarray) -> np.ndarray:
    kx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    return np.column_stack([(ring[:, 0] - lon0) * kx, (ring[:, 1] - lat0) * M_PER_DEG_LAT])


def _trace_top(labels_col: np.ndarray, depth_col, start_row: int, ref_depth, min_ratio: float):
    """Topmost row of the run of building pixels above ``start_row`` that stays at roughly the
    reference depth (inverse depth >= ``min_ratio`` x the reference). None if ``start_row``
    itself is not building."""
    y = int(start_row)
    if y < 0 or y >= len(labels_col) or not np.isin(labels_col[y], BUILDING_CLASSES):
        return None
    top = y
    while y - 1 >= 0 and np.isin(labels_col[y - 1], BUILDING_CLASSES):
        if depth_col is not None and ref_depth is not None and depth_col[y - 1] < min_ratio * ref_depth:
            break
        y -= 1
        top = y
    return top


def measure_footprints(pano: Pano, pose: PanoPose, footprints: list[Footprint],
                       depth: np.ndarray | None = None, max_dist_m: float = 3000.0,
                       min_cols: int = 6, core: float = 0.6, depth_ratio: float = 0.7,
                       base_window_px: int = 6) -> list[Measured]:
    """Measure every footprint in view, nearest first (see module docstring).

    ``depth``: Depth Anything V2 inverse depth (closer = higher) on the pano grid, or None
    (labels only). A building's reference depth is the median over the rows just above its base
    when the base is visible; otherwise the per-pano fit ``depth ~ a / d + b`` from the
    base-visible buildings, which also decides whether a partly hidden building shows at all.
    """
    H, W = pano.labels.shape
    bu, b0 = bearing_columns(pano, pose)
    h = pose.camera_h_m
    cand = []
    for i, fp in enumerate(footprints):
        xy = _local(pano.lat, pano.lon, np.asarray(fp.ring, float))
        d = np.hypot(xy[:, 0], xy[:, 1])
        dn = float(d.min())
        if dn < 20.0 or dn > max_dist_m:
            continue
        bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1])) % 360.0
        rel = (bear - bear[0] + 180.0) % 360.0 - 180.0
        lo, hi = bear[0] + rel.min(), bear[0] + rel.max()
        c0, c1 = column_of(lo % 360.0, bu, b0), column_of(hi % 360.0, bu, b0)
        if not (np.isfinite(c0) and np.isfinite(c1)) or c1 - c0 < min_cols:
            continue
        base = float(_row_of(pano, pose, -math.degrees(math.atan2(h, dn))))
        cand.append((dn, i, int(math.ceil(c0)), int(math.floor(c1)), base))
    cand.sort()

    def core_cols(x0, x1):
        n = x1 - x0 + 1
        cut = int(n * (1 - core) / 2)
        return np.arange(x0 + cut, x1 - cut + 1)

    def run(passes_model):
        occ = np.full(W, H, dtype=float)   # topmost row of nearer measured buildings
        out = []
        for dn, i, x0, x1, base in cand:
            cols = core_cols(x0, x1)
            tops, bottoms, vis = [], [], 0
            for x in cols:
                vis_bottom = min(base, occ[x] - 1)
                y0 = int(round(vis_bottom)) - 1
                if y0 >= H or y0 < 1:
                    continue
                base_vis = vis_bottom >= base - 0.5
                ref = None
                if depth is not None:
                    if base_vis:
                        lo_r = max(0, y0 - base_window_px)
                        ref = float(np.median(depth[lo_r:y0 + 1, x]))
                    elif passes_model is not None:
                        a, b = passes_model
                        ref = a / dn + b
                        if depth[y0, x] < depth_ratio * ref:   # a farther surface: hidden here
                            continue
                t = _trace_top(pano.labels[:, x], None if depth is None else depth[:, x],
                               y0, ref, depth_ratio)
                if t is None:
                    continue
                tops.append(t)
                bottoms.append(vis_bottom)
                vis += base_vis
            if len(tops) < max(3, len(cols) // 3):
                continue
            top = float(np.median(tops))
            hm = h + dn * math.tan(math.radians(float(_elev_of(pano, pose, top))))
            fp = footprints[i]
            out.append(Measured(i, fp.name, x0, x1, top, float(np.median(bottoms)), base, dn, hm,
                                len(tops), vis >= len(tops) / 2, fp.osm_height_m))
            occ[x0:x1 + 1] = np.minimum(occ[x0:x1 + 1], top)
        return out

    first = run(None)
    model = None
    if depth is not None:
        pts = []
        for m in first:
            if m.base_visible:
                xs = core_cols(m.x0, m.x1)
                y0 = int(round(m.base_row)) - 1
                if 0 <= y0 < H:
                    pts.append((1.0 / m.dist_m, float(np.median(depth[max(0, y0 - base_window_px):y0 + 1, xs]))))
        if len(pts) >= 8:
            X = np.array(pts)
            A = np.column_stack([X[:, 0], np.ones(len(X))])
            model = tuple(np.linalg.lstsq(A, X[:, 1], rcond=None)[0])
    return run(model) if model is not None else first
