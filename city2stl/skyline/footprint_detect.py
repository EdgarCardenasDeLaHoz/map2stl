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

from ._core.segmentation import _ADE20K_VEGETATION_CLASSES, _ADE20K_WATER_CLASSES

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
                            max_shore_m: float = 4000.0, offsets_deg=None) -> PanoPose:
    """Heading offset, camera height and pitch correction from the near waterline.

    For each candidate offset and height, the predicted elevation of the near shore in every
    observed column is ``-atan(h / d)`` (``d`` = OSM distance to land along the column's
    bearing; open sea -> 0). The pitch correction is the median residual; the misfit is the
    mean absolute residual after it over the best 90 % of columns (robust to a breakwater or
    an island OSM has and the photo does not, yet not blind to a minority of shore columns).
    ``offsets_deg`` limits the search (a refit near a known heading); default: the full circle.
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
    for off in (np.arange(0.0, 360.0, offset_step_deg) if offsets_deg is None else offsets_deg):
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


# --------------------------------------------------------------------------- ground (position)

#: ADE20K classes counted as building surface when tracing a building's top: building,
#: house, skyscraper, tower. ("wall" is left out: sea walls and fences.)
BUILDING_CLASSES = (1, 25, 48, 84)
SKY_CLASS = 2
#: SegFormer classes seen as road from above: road, sidewalk, path, car.
ROAD_CLASSES = (6, 11, 52, 20)
#: Ground codes, shared by the OSM map and the classified image.
G_LAND, G_WATER, G_ROAD, G_GREEN, G_BUILDING, G_SKIP = 0, 1, 2, 3, 4, 5


@dataclass(frozen=True)
class GroundMap:
    """OSM ground classes on a north-up local-metre grid (row 0 = north) centred on the seed."""
    codes: np.ndarray            # (n, n) uint8 G_* codes
    lat: float
    lon: float
    half_m: float
    res_m: float

    def shore_distances(self, dx_m: float = 0.0, dy_m: float = 0.0, max_m: float = 4000.0,
                        step_m: float = 2.0) -> np.ndarray:
        """Like :func:`shore_distance_table` for a camera ``dx_m`` east and ``dy_m`` north of
        the map centre: distance to the first non-water cell per 0.1-deg bearing (``inf``:
        none within ``max_m``). Within a cell of the vector table; fast enough for a grid of
        positions."""
        n = self.codes.shape[0]
        r = np.arange(5.0, max_m, step_m)
        t = np.radians(np.arange(N_BEARINGS) * BEARING_BIN_DEG)
        ex = dx_m + np.sin(t)[:, None] * r[None, :]
        ny = dy_m + np.cos(t)[:, None] * r[None, :]
        land = self.codes[np.clip(((self.half_m - ny) / self.res_m).astype(int), 0, n - 1),
                          np.clip(((ex + self.half_m) / self.res_m).astype(int), 0, n - 1)] != G_WATER
        return np.where(land.any(axis=1), r[land.argmax(axis=1)], np.inf)


def waterline_position_scan(pano: Pano, pose: PanoPose, gmap: GroundMap, search_m: float = 150.0,
                            step_m: float = 50.0):
    """Waterline misfit over camera positions around the seed, heading and height refit near
    ``pose`` at each: ``(grid_m, misfit[i_north, j_east], (dx, dy, PanoPose) of the best)``.
    The waterline pins the position across the shore and loosely along it (seed_5, 2026-10-04:
    0.39 deg at the seed, 1.8 deg 150 m north, 0.58 deg 150 m east)."""
    grid = np.arange(-search_m, search_m + 1e-6, step_m)
    hs = pose.camera_h_m * np.exp(np.linspace(math.log(0.6), math.log(1.6), 25))
    offs = pose.offset_deg + np.arange(-3.0, 3.01, 0.25)
    mis = np.full((grid.size, grid.size), np.nan)
    best = None
    for i, dy in enumerate(grid):
        for j, dx in enumerate(grid):
            p = fit_pose_from_waterline(pano, gmap.shore_distances(dx, dy), heights_m=hs,
                                        offsets_deg=offs)
            mis[i, j] = p.misfit_deg
            if best is None or p.misfit_deg < best[2].misfit_deg:
                best = (float(dx), float(dy), p)
    return grid, mis, best


@dataclass(frozen=True)
class GroundFit:
    dx_m: float                  # camera east of the seed position
    dy_m: float                  # camera north of it
    offset_deg: float
    camera_h_m: float
    score: float                 # mean IoU of water, road and green
    score_at_seed: float         # same, at the seed position with the waterline pose
    n_px: int


def _to_local(geom, lat0: float, lon0: float):
    import shapely

    kx = M_PER_DEG_LAT * math.cos(math.radians(lat0))
    return shapely.transform(geom, lambda c: np.column_stack(
        [(c[:, 0] - lon0) * kx, (c[:, 1] - lat0) * M_PER_DEG_LAT]))


def ground_map(lat: float, lon: float, coast_lines=(), water_polys=(), roads=(), green=(),
               buildings=(), half_m: float = 2000.0, res_m: float = 2.0) -> GroundMap:
    """Rasterise OSM ground around (lat, lon): green areas, roads (``(line, width_m)`` pairs,
    buffered), building footprints, then water where nothing else is. Water is the region
    around the camera's cell that the coastline does not cross, plus the water polygons: the
    drone hovers over the bay (both Cartagena seeds; the waterline pose needs water below
    it anyway). Geometries are shapely, in lon/lat."""
    from numpy2stl.raster import burn_polygons
    from scipy import ndimage

    n = int(round(2 * half_m / res_m))
    bounds = (-half_m, -half_m, half_m, half_m)
    polys, vals = [], []
    for g in green:
        polys.append(_to_local(g, lat, lon))
        vals.append(G_GREEN)
    for g, w in roads:
        polys.append(_to_local(g, lat, lon).buffer(w / 2.0, cap_style="flat"))
        vals.append(G_ROAD)
    for g in buildings:
        polys.append(_to_local(g, lat, lon))
        vals.append(G_BUILDING)
    codes = (burn_polygons(polys, (n, n), bounds=bounds, values=vals, mode="set", dtype=np.uint8)
             if polys else np.zeros((n, n), np.uint8))
    barrier = np.zeros((n, n), bool)
    if coast_lines:
        barrier = burn_polygons([_to_local(g, lat, lon).buffer(res_m) for g in coast_lines],
                                (n, n), bounds=bounds, values=1, dtype=np.uint8) > 0
    lab, _ = ndimage.label(~barrier)
    water = lab == lab[n // 2, n // 2] if lab[n // 2, n // 2] else np.zeros((n, n), bool)
    if water_polys:
        water |= burn_polygons([_to_local(g, lat, lon) for g in water_polys], (n, n),
                               bounds=bounds, values=1, dtype=np.uint8) > 0
    codes = codes.astype(np.uint8)
    codes[(codes == G_LAND) & water] = G_WATER
    return GroundMap(codes, lat, lon, half_m, res_m)


def observed_ground(labels: np.ndarray) -> np.ndarray:
    """SegFormer labels as G_* codes (sky and unlabelled: G_SKIP)."""
    obs = np.full(labels.shape, G_LAND, np.uint8)
    obs[np.isin(labels, _ADE20K_WATER_CLASSES)] = G_WATER
    obs[np.isin(labels, ROAD_CLASSES)] = G_ROAD
    obs[np.isin(labels, _ADE20K_VEGETATION_CLASSES)] = G_GREEN
    obs[np.isin(labels, BUILDING_CLASSES)] = G_BUILDING
    obs[(labels == SKY_CLASS) | (labels < 0)] = G_SKIP
    return obs


class _GroundScorer:
    """Projects the image's ground pixels onto a GroundMap for candidate cameras (flat ground at
    the water level) and scores the class agreement."""

    def __init__(self, pano: Pano, pose: PanoPose, gmap: GroundMap, d_range, step_px: int):
        ys = np.arange(0, pano.height, step_px)
        elev = np.asarray(_elev_of(pano, pose, ys), float)
        ys, tan_dep = ys[elev < -0.5], np.tan(np.radians(-elev[elev < -0.5]))
        xs = np.arange(0, pano.width, step_px)
        obs = observed_ground(pano.labels)[np.ix_(ys, xs)]
        keep = (obs != G_BUILDING) & (obs != G_SKIP)   # walls and roofs are not ground
        self.obs = obs[keep]
        self.tan = np.broadcast_to(tan_dep[:, None], obs.shape)[keep]
        self.frame = np.broadcast_to(pano.frame_heading[xs][None, :], obs.shape)[keep]
        self.gmap, self.d_range = gmap, d_range

    def project(self, off: float, h: float):
        d = h / self.tan
        ok = (d >= self.d_range[0]) & (d <= self.d_range[1])
        b = np.radians(self.frame[ok] + off)
        return d[ok] * np.sin(b), d[ok] * np.cos(b), self.obs[ok]

    def score(self, ex, ny, obs, dx: float, dy: float) -> float:
        g = self.gmap
        n = g.codes.shape[0]
        ci = np.clip(((ex + dx + g.half_m) / g.res_m).astype(int), 0, n - 1)
        ri = np.clip(((g.half_m - ny - dy) / g.res_m).astype(int), 0, n - 1)
        cm = np.bincount(obs.astype(int) * 6 + g.codes[ri, ci], minlength=36).reshape(6, 6)
        ious = []
        for c in (G_WATER, G_ROAD, G_GREEN):
            union = cm[c, :].sum() + cm[:, c].sum() - cm[c, c]
            if union:
                ious.append(cm[c, c] / union)
        return float(np.mean(ious)) if ious else 0.0


def fit_position_from_ground(pano: Pano, pose: PanoPose, gmap: GroundMap,
                             search_m: float = 120.0, d_range=(30.0, 1500.0),
                             step_px: int = 2) -> GroundFit:
    """Camera position, heading offset and height from the ground below the horizon (the
    user's idea, 2026-10-04: parks, streets and the shore seen from the drone, all in OSM).

    Every image pixel classed water, road or green (or other ground) is cast onto flat ground
    at the water level from a candidate camera and looked up in the OSM ground map; the score
    is the mean IoU of the three classes (walls, roofs and sky are left out). Coarse grid over
    +-``search_m`` (30 m steps, offset +-2 deg, height +-15 %) around the waterline pose, then a
    fine one (5 m, 0.3 deg, 3 %) around the best. The waterline alone pins heading, height and
    pitch but barely the position along the shore; streets and parks pin that.
    """
    sc = _GroundScorer(pano, pose, gmap, d_range, step_px)
    proj = {}

    def best_of(dxs, dys, offs, hs):
        best = None
        for off in offs:
            for h in hs:
                key = (round(float(off), 4), round(float(h), 3))
                if key not in proj:
                    proj[key] = sc.project(off, h)
                ex, ny, obs = proj[key]
                for dx in dxs:
                    for dy in dys:
                        s = sc.score(ex, ny, obs, dx, dy)
                        if best is None or s > best[0]:
                            best = (s, float(dx), float(dy), float(off), float(h))
        return best

    ex, ny, obs = sc.project(pose.offset_deg, pose.camera_h_m)
    at_seed = sc.score(ex, ny, obs, 0.0, 0.0)
    grid = np.arange(-search_m, search_m + 1e-6, 30.0)
    s, dx, dy, off, h = best_of(grid, grid, pose.offset_deg + np.arange(-2.0, 2.01, 1.0),
                                pose.camera_h_m * np.array([0.85, 1.0, 1.15]))
    fine = np.arange(-20.0, 20.01, 5.0)
    s, dx, dy, off, h = best_of(dx + fine, dy + fine, off + np.arange(-0.6, 0.61, 0.3),
                                h * np.array([0.94, 0.97, 1.0, 1.03, 1.06]))
    return GroundFit(dx, dy, off % 360.0, h, s, at_seed, int(sc.obs.size))


def moved(pano: Pano, dx_m: float, dy_m: float) -> Pano:
    """The same pano with the camera ``dx_m`` east and ``dy_m`` north of where it was."""
    from dataclasses import replace

    kx = M_PER_DEG_LAT * math.cos(math.radians(pano.lat))
    return replace(pano, lat=pano.lat + dy_m / M_PER_DEG_LAT, lon=pano.lon + dx_m / kx)


# --------------------------------------------------------------------------- footprints


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
    visible_frac: float = 1.0        # share of the base-to-top image height that is seen
    top_edge: str = "sky"            # what ends the run in most columns: sky, depth, other


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


def _column_run(labels_col: np.ndarray, depth_col, start_row: int, near_lim: float,
                stop: float, gap_px: int):
    """One building's rows in one column: ``(top, bottom, edge)`` or None.

    Going up from ``start_row``: skip building rows nearer than this building (inverse depth
    above ``near_lim``: something in front that the occlusion line missed) and up to ``gap_px``
    other rows (a palm, a lamp post); the first building row at this building's depth is its
    visible bottom. Then follow building rows while the inverse depth stays at or above
    ``stop`` (below it: the surface behind). ``edge`` says what ends the run: sky, depth (a
    building behind), other (a label that is neither), or image (the top row).
    """
    is_b = np.isin(labels_col, BUILDING_CLASSES)
    y, gap = int(start_row), 0
    if y < 0 or y >= len(labels_col):
        return None
    while y >= 0:
        if is_b[y]:
            if depth_col is None or depth_col[y] <= near_lim:
                break
        else:
            gap += 1
            if gap > gap_px:
                return None
        y -= 1
    if y < 0:
        return None
    bottom = y
    while y - 1 >= 0 and is_b[y - 1] and (depth_col is None or depth_col[y - 1] >= stop):
        y -= 1
    if y == 0:
        edge = "image"
    elif labels_col[y - 1] == SKY_CLASS:
        edge = "sky"
    elif is_b[y - 1]:
        edge = "depth"
    else:
        edge = "other"
    return y, bottom, edge


def measurement_weight(m) -> float:
    """Reliability of one measurement: the seen share of the building over distance squared
    (a pixel is ``d`` x 0.13 deg of height, and from past ~1 km a tower behind is within the
    depth noise). A visible base counts as fully seen. Takes a Measured or its dict."""
    get = m.get if isinstance(m, dict) else lambda k: getattr(m, k)
    seen = 1.0 if get("base_visible") else float(get("visible_frac"))
    return seen / max(float(get("dist_m")), 100.0) ** 2


def fuse_heights(by_seed: dict, agree: float = 0.25) -> dict:
    """One height per footprint from several seeds' measurements (``{seed: [Measured or dict]}``).

    Seeds within ``agree`` (relative) of the most reliable one (:func:`measurement_weight`) are
    averaged with those weights; the rest are outvoted, and the footprint is marked disputed
    when any were. 2026-10-04, Cartagena: where seed_1 (400-600 m) and seed_5 (1.1-1.3 km)
    disagreed, the far seed read the tower behind (13 vs 81 m, 26 vs 111 m); the near seed is
    17 m off the OSM tags, the far one 56 m."""
    rows: dict = {}
    for seed, ms in by_seed.items():
        for m in ms:
            d = m if isinstance(m, dict) else m.__dict__
            rows.setdefault(int(d["footprint"]), []).append((seed, d, measurement_weight(d)))
    out = {}
    for fp, got in rows.items():
        got.sort(key=lambda r: -r[2])
        best = got[0][1]["height_m"]
        keep = [r for r in got if abs(r[1]["height_m"] - best) <= agree * max(1.0, best)]
        w = np.array([r[2] for r in keep])
        out[fp] = {"name": got[0][1]["name"],
                   "height_m": float(np.average([r[1]["height_m"] for r in keep], weights=w)),
                   "seeds": {r[0]: round(float(r[1]["height_m"]), 1) for r in got},
                   "used": [r[0] for r in keep], "disputed": len(keep) < len(got),
                   "osm_height_m": got[0][1].get("osm_height_m")}
    return out


def fit_depth_model(dist_m, inv_depth) -> tuple[float, float] | None:
    """``inv_depth ~ a / d + b`` from buildings whose base is visible (Depth Anything output is
    inverse depth up to scale and shift). Least squares, refit once without points beyond 3
    MADs; under 8 points only the scale (b = 0). None without points."""
    d = np.asarray(dist_m, float)
    z = np.asarray(inv_depth, float)
    if not d.size:
        return None
    if d.size < 8:
        return float(np.median(z * d)), 0.0
    A = np.column_stack([1.0 / d, np.ones(d.size)])
    coef = np.linalg.lstsq(A, z, rcond=None)[0]
    r = z - A @ coef
    keep = np.abs(r - np.median(r)) <= 3 * 1.4826 * np.median(np.abs(r - np.median(r))) + 1e-9
    if keep.sum() >= 8:
        coef = np.linalg.lstsq(A[keep], z[keep], rcond=None)[0]
    return float(coef[0]), float(coef[1])


def measure_footprints(pano: Pano, pose: PanoPose, footprints: list[Footprint],
                       depth: np.ndarray | None = None, max_dist_m: float = 3000.0,
                       min_cols: int = 6, core: float = 0.6, depth_ratio: float = 0.7,
                       base_window_px: int = 6, near_ratio: float = 1.35,
                       max_step: float = 0.92, gap_px: int = 8,
                       min_visible_frac: float = 0.25, min_run_px: int = 3) -> list[Measured]:
    """Measure every footprint in view, nearest first (see module docstring).

    ``depth``: Depth Anything V2 inverse depth (closer = higher) on the pano grid, or None
    (labels only). Pass 1 measures with each base-visible building's own base depth as its
    reference; the base-visible buildings then fit ``depth ~ a / d + b`` (:func:`fit_depth_model`)
    and pass 2 measures again with it:

    - reference level: the base depth when the base is visible and agrees with the model,
      else the model level at the building's distance;
    - nearer surfaces (above ``near_ratio`` x the reference) are skipped, so a building the
      occlusion line missed is not measured as this one (2026-10-04: a 620 m building read as
      a footprint 887 m away);
    - the run stops where the depth falls to the midpoint between the reference and the level
      of the next OSM footprint behind it in that column, clipped to ``depth_ratio`` ..
      ``max_step`` x the reference (a fixed 0.7 only separated a tower 1.4x farther; the rows
      of Bocagrande are 10-30 % apart);
    - a building whose base is hidden and of which under ``min_visible_frac`` of the
      base-to-top height shows is dropped: the rows just above a nearer roof are as likely the
      building behind it;
    - a column counts only with a run of ``min_run_px`` rows or more (single-row runs read
      0.9-2.7 m for whole buildings, 2026-10-04);
    - a footprint that holds smaller ones (a podium with its tower mapped separately, the
      Plaza Bocagrande mall) is measured on the columns its inner footprints leave free, so
      the tower is not read as the podium.
    """
    import shapely

    H, W = pano.labels.shape
    bu, b0 = bearing_columns(pano, pose)
    h = pose.camera_h_m
    cand, behind, rings = [], [[] for _ in range(W)], {}
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
        if not (np.isfinite(c0) and np.isfinite(c1)):
            continue
        x0, x1 = int(math.ceil(c0)), int(math.floor(c1))
        for x in range(max(0, x0), min(W, x1 + 1)):
            behind[x].append(dn)
        if c1 - c0 < min_cols:
            continue
        base = float(_row_of(pano, pose, -math.degrees(math.atan2(h, dn))))
        cand.append((dn, i, x0, x1, base))
        rings[i] = xy
    cand.sort()
    behind = [np.sort(np.asarray(v, float)) for v in behind]
    # inner footprints: candidates whose centre lies in a footprint at least 1.4x their area
    polys = [shapely.Polygon(rings[i]) for _dn, i, *_ in cand]
    polys = [q if q.is_valid else q.buffer(0) for q in polys]
    area = np.array([p.area for p in polys])
    tree = shapely.STRtree([p.representative_point() for p in polys])
    inner = {}
    for k, p in enumerate(polys):
        js = [j for j in tree.query(p, predicate="contains") if j != k and area[j] * 1.4 <= area[k]]
        if js:
            inner[k] = [(cand[j][2], cand[j][3]) for j in js]
    dz = None
    if depth is not None:
        from scipy.ndimage import median_filter

        dz = median_filter(np.asarray(depth, float), size=(5, 1))    # vertical: keeps edges

    def core_cols(x0, x1):
        n = x1 - x0 + 1
        cut = int(n * (1 - core) / 2)
        return np.arange(x0 + cut, x1 - cut + 1)

    def run(model):
        occ = np.full(W, H, dtype=float)   # topmost row of nearer measured buildings
        out = []
        for k, (dn, i, x0, x1, base) in enumerate(cand):
            level = None if model is None else model[0] / dn + model[1]
            cols = core_cols(x0, x1)
            for a, b in inner.get(k, ()):
                cols = cols[(cols < a) | (cols > b)]
            if cols.size < 3:
                continue
            tops, bottoms, edges, vis = [], [], [], 0
            for x in cols:
                y0 = int(round(min(base, occ[x] - 1))) - 1
                if y0 >= H or y0 < 1:
                    continue
                near_lim, stop = np.inf, -np.inf
                if dz is not None:
                    ref = None
                    if occ[x] - 1 >= base - 0.5:          # base not behind a measured roof
                        ref = float(np.median(dz[max(0, y0 - base_window_px):y0 + 1, x]))
                        if level is not None and not level / near_ratio <= ref <= level * near_ratio:
                            ref = None                    # the base row shows something else
                    if ref is None:
                        if level is None:                 # pass 1: no model yet
                            ref = float(dz[y0, x])
                        else:
                            ref = level
                    near_lim = near_ratio * ref
                    stop = depth_ratio * ref
                    if model is not None:
                        k = np.searchsorted(behind[x], dn * 1.08)
                        if k < len(behind[x]):            # next OSM footprint behind, this column
                            far = (model[0] / behind[x][k] + model[1]) * ref / level
                            stop = min(max(0.5 * (ref + far), depth_ratio * ref), max_step * ref)
                got = _column_run(pano.labels[:, x], None if dz is None else dz[:, x], y0,
                                  near_lim, stop, gap_px)
                if got is None or got[2] == "image" or got[1] - got[0] + 1 < min_run_px:
                    continue
                t, bot, edge = got
                tops.append(t)
                bottoms.append(bot)
                edges.append(edge)
                vis += bot >= base - 3
            if len(tops) < max(3, len(cols) // 3):
                continue
            top = float(np.median(tops))
            bottom = float(np.median(bottoms))
            base_vis = vis >= len(tops) / 2
            frac = float(np.clip((bottom - top) / max(1.0, base - top), 0.0, 1.0))
            if not base_vis and frac < min_visible_frac:
                continue
            hm = h + dn * math.tan(math.radians(float(_elev_of(pano, pose, top))))
            fp = footprints[i]
            edge = max(set(edges), key=edges.count)
            out.append(Measured(i, fp.name, x0, x1, top, bottom, base, dn, hm, len(tops),
                                base_vis, fp.osm_height_m, frac, edge))
            occ[x0:x1 + 1] = np.minimum(occ[x0:x1 + 1], top)
        return out

    first = run(None)
    if dz is None:
        return first
    pts = []
    for m in first:
        if m.base_visible:
            y0 = int(round(m.base_row)) - 1
            if 0 <= y0 < H:
                xs = core_cols(m.x0, m.x1)
                pts.append((m.dist_m, float(np.median(dz[max(0, y0 - base_window_px):y0 + 1, xs]))))
    model = fit_depth_model([p[0] for p in pts], [p[1] for p in pts]) if pts else None
    return run(model) if model is not None else first
