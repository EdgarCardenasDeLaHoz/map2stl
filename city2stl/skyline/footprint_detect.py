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
    # elevation (deg, this pano's row model) of the open-sea horizon after any tilt correction;
    # NaN when not measured. The true horizon dips atan(sqrt(2h/R)): the pitch fix follows.
    horizon_deg: float = float("nan")

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


def load_seed_pano(region: str, seed_name: str, step_deg: float = 30.0,
                   hires: bool = False, hires_pitches: tuple[float, ...] | None = None) -> Pano:
    """Rebuild a seed's stitched pano from its spin views the way the pipeline captures them
    (Photo Sphere seeds by pano id). Views come from ``runs/image_cache`` when cached. ``hires``:
    the drone capture of ``_pano.elevated.capture_sphere_pano`` (30-deg views in pitch
    rows reprojected onto one sphere; ``hires_pitches`` overrides the rows)."""
    import json
    from pathlib import Path

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
    if hires:
        from ._pano.elevated import capture_sphere_pano

        got = capture_sphere_pano(seed, _resolve_api_key(), headings, is_photosphere,
                                  **({"pitches": hires_pitches} if hires_pitches else {}))
        if got is None:
            raise RuntimeError(f"{seed_name}: hi-res capture failed")
        return got
    prefetch, eff_pitch, _cached = _capture_pano_views(
        seed, _resolve_api_key(), headings, is_photosphere=is_photosphere)
    return pano_from_views(seed_name, lat, lon, prefetch, fov, step_deg, eff_pitch)


def pano_from_views(name: str, lat: float, lon: float, views: list[dict], fov_deg: float,
                    step_deg: float, pitch_deg: float) -> Pano:
    """Stitch captured spin views (``_capture_pano_views``' prefetch list: dicts with
    ``image`` and ``geo_heading``) and their ADE20K labels into a :class:`Pano`."""
    from ._core.pano import stitch_pano_views

    views = [v for v in views if v.get("image") is not None]
    rgb, frame = stitch_pano_views([{"image": v["image"], "geo_heading": v["geo_heading"]}
                                    for v in views], fov_deg, step_deg)
    labels = _stitch_labels(views, fov_deg, step_deg)
    w_view = views[0]["image"].shape[1]
    f = 0.5 * w_view / math.tan(math.radians(fov_deg) / 2)
    return Pano(name, lat, lon, rgb, labels, np.asarray(frame, float), f, float(pitch_deg))


# --------------------------------------------------------------------------- waterline


#: Rows below this elevation (deg, capture pitch) are left out of the waterline: looking
#: steeply down, SegFormer labels shallow bay water by its bottom. Miami seed_4 (2026-10-07), a
#: sphere with a -44 deg pitch row: the bay under -30 deg came out vegetation (seagrass) where
#: OSM has water, only 492 of 7504 columns ended in water and the fit put the camera 190 m up
#: (84 m from the 8/-18 rows alone). -28, not -35: the sphere takes a pixel from the view whose
#: axis is nearest, so the -44 view labels rows up to -31 (at -35: 1259 columns, 64 m; -30:
#: 6966; -28: all 7504 and the 8/-18 fit, 76 m before the outline step). The 8/-18 and spin
#: panos reach -25..-33 and keep their fits. None: every row.
WATERLINE_MIN_ELEV_DEG: float | None = -28.0


def near_water_top(pano: Pano, min_run: int = 6,
                   min_elev_deg: float | None = None) -> np.ndarray:
    """Per column: the row where the bottom water run ends going up (NaN when the column's
    bottom is not water). From an elevated camera over the bay this is the near shoreline, or
    the horizon in open-sea columns. The column's bottom is the lowest row at or above
    ``min_elev_deg`` (default :data:`WATERLINE_MIN_ELEV_DEG`)."""
    water = np.isin(pano.labels, _ADE20K_WATER_CLASSES)
    h, w = water.shape
    lim = WATERLINE_MIN_ELEV_DEG if min_elev_deg is None else min_elev_deg
    if lim is not None:
        yb = int(math.floor(float(pano.row_of_elevation(lim))))
        h = int(np.clip(yb + 1, min_run + 1, h))
    out = np.full(w, np.nan)
    for x in range(w):
        col = water[:h, x]
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

    def _land(self, ex, ny):
        n = self.codes.shape[0]
        return self.codes[np.clip(((self.half_m - ny) / self.res_m).astype(int), 0, n - 1),
                          np.clip(((ex + self.half_m) / self.res_m).astype(int), 0, n - 1)] != G_WATER

    def shore_distances(self, dx_m: float = 0.0, dy_m: float = 0.0, max_m: float = 4000.0,
                        step_m: float = 12.0) -> np.ndarray:
        """Like :func:`shore_distance_table` for a camera ``dx_m`` east and ``dy_m`` north of
        the map centre: distance to the first non-water cell per 0.1-deg bearing (``inf``:
        none within ``max_m``). Rays are sampled every ``step_m``, then the first crossing is
        refined to the map's cell size: about 0.1 s, fast enough for a grid of positions (a
        strip of land narrower than ``step_m`` can be stepped over)."""
        r = np.arange(5.0, max_m, step_m)
        t = np.radians(np.arange(N_BEARINGS) * BEARING_BIN_DEG)
        s, c = np.sin(t)[:, None], np.cos(t)[:, None]
        land = self._land(dx_m + s * r[None, :], dy_m + c * r[None, :])
        hit = land.any(axis=1)
        first = r[land.argmax(axis=1)]
        fine = first[:, None] - step_m + np.arange(1, int(step_m / self.res_m) + 1) * self.res_m
        land2 = self._land(dx_m + s * fine, dy_m + c * fine)
        return np.where(hit, fine[np.arange(len(fine)), land2.argmax(axis=1)], np.inf)


def waterline_position_scan(pano: Pano, pose: PanoPose, gmap: GroundMap, search_m: float = 150.0,
                            step_m: float = 50.0):
    """Waterline misfit over camera positions around the seed, heading and height refit near
    ``pose`` at each: ``(grid_m, misfit[i_north, j_east], (dx, dy, PanoPose) of the best)``.
    The waterline pins the position across the shore and loosely along it (seed_5, 2026-10-04:
    0.39 deg at the seed, 1.8 deg 150 m north, 0.58 deg 150 m east)."""
    grid = np.arange(-search_m, search_m + 1e-6, step_m)
    hs = pose.camera_h_m * np.exp(np.linspace(math.log(0.6), math.log(1.6), 25))
    offs = pose.offset_deg + np.arange(-3.0, 3.01, 0.25)
    mis, best = _waterline_grid(pano, gmap, grid, grid, offs, hs)
    return grid, mis, best


def _waterline_grid(pano: Pano, gmap: GroundMap, xs, ys, offs, hs):
    """Waterline misfit at every (x east, y north) camera position, heading and height refit
    at each: ``(misfit[i_y, j_x], (dx, dy, PanoPose) of the best)``."""
    mis = np.full((len(ys), len(xs)), np.nan)
    best = None
    for i, dy in enumerate(ys):
        for j, dx in enumerate(xs):
            p = fit_pose_from_waterline(pano, gmap.shore_distances(dx, dy), heights_m=hs,
                                        offsets_deg=offs)
            mis[i, j] = p.misfit_deg
            if best is None or p.misfit_deg < best[2].misfit_deg:
                best = (float(dx), float(dy), p)
    return mis, best


@dataclass(frozen=True)
class PositionFit:
    dx_m: float                  # camera east of the recorded position
    dy_m: float                  # north
    pose: PanoPose               # heading offset, camera height, pitch there
    waterline_at_seed_deg: float
    waterline_deg: float         # waterline misfit at the waterline fit
    ground_at_waterline: float   # parks/streets/water score there
    ground: float                # and at the final position
    source: str                  # recorded, waterline, ground or waterline+ground


def fit_camera_position(pano: Pano, pose: PanoPose, gmap: GroundMap, search_m: float = 600.0,
                        min_gain: float = 0.3, ground_m: float = 80.0,
                        min_ground_gain: float = 0.01, ground_max_misfit_deg: float = 0.3,
                        d_range=(30.0, 1500.0), coarse_step_m: float = 100.0,
                        fine_half_m: float = 100.0, fine_step_m: float = 20.0) -> PositionFit:
    """Where the drone was. A Photo Sphere's recorded position can be where the pilot stood:
    seed_4 (2026-10-05) sat ~360 m from where its waterline fits.

    1. The waterline misfit over +-``search_m`` (100 m steps, then 20 m around the best),
       heading (+-6 deg) and height (x0.5-2) refit at each position. The camera moves only when
       the misfit drops by ``min_gain`` of its value at the recorded position (seed_4: 0.68 ->
       0.18 deg; seed_5: lowest at its recorded position; seed_1: flat, 1.1 deg).
    2. Parks, streets and water (the score of :func:`fit_position_from_ground`) within
       +-``ground_m`` of that position, offset +-1.5 deg, height +-15 %: they pin the position
       along the shore, where the waterline is loose. Only after a good waterline fit (misfit
       under ``ground_max_misfit_deg``), kept when the score gains ``min_ground_gain`` and the
       waterline misfit there stays within 1.5x its best. seed_4: 0.395 -> 0.430 at 57 m, its
       tagged towers then landed on their buildings and the OSM-tag error fell from 113 to
       33 m; seed_1 (waterline 1.14 deg, flat) moved 64 m on the ground score alone and its
       OSM-tag error rose from 11-16 to 34 m, hence the waterline condition.

    ``gmap`` must reach ``search_m`` + 4 km from the recorded position (``ground_map`` with
    ``half_m`` ~4800 and ``res_m`` 3).
    """
    hs = pose.camera_h_m * np.exp(np.linspace(math.log(0.5), math.log(2.0), 21))
    offs = pose.offset_deg + np.arange(-6.0, 6.01, 0.5)
    at_seed = fit_pose_from_waterline(pano, gmap.shore_distances(0.0, 0.0), heights_m=hs,
                                      offsets_deg=offs)
    coarse = np.arange(-search_m, search_m + 1e-6, coarse_step_m)  # 1 deg, 11 heights: 4x faster
    _, (bx, by, bp) = _waterline_grid(pano, gmap, coarse, coarse, offs[::2], hs[::2])
    fine = np.arange(-fine_half_m, fine_half_m + 1e-6, fine_step_m)
    _, (bx, by, bp) = _waterline_grid(pano, gmap, bx + fine, by + fine, offs, hs)
    if bp.misfit_deg > (1.0 - min_gain) * at_seed.misfit_deg:
        bx, by, bp = 0.0, 0.0, at_seed
    source = "recorded" if (bx, by) == (0.0, 0.0) else "waterline"
    if bp.misfit_deg > ground_max_misfit_deg:
        return PositionFit(float(bx), float(by), bp, at_seed.misfit_deg, bp.misfit_deg, float("nan"),
                           float("nan"), source)
    sc = _GroundScorer(pano, bp, gmap, d_range, 3)
    ex, ny, obs = sc.project(bp.offset_deg, bp.camera_h_m)
    g0 = sc.score(ex, ny, obs, bx, by)
    best = (g0, bx, by, bp.offset_deg, bp.camera_h_m)
    steps = np.arange(-ground_m, ground_m + 1e-6, 10.0)
    for off in bp.offset_deg + np.arange(-1.5, 1.51, 0.75):
        for h in bp.camera_h_m * np.array([0.9, 1.0, 1.1]):
            ex, ny, obs = sc.project(off, h)
            for ddx in steps:
                for ddy in steps:
                    s = sc.score(ex, ny, obs, bx + ddx, by + ddy)
                    if s > best[0]:
                        best = (s, bx + ddx, by + ddy, float(off), float(h))
    g, gx, gy, goff, gh = best
    fx, fy, fp, gf = bx, by, bp, g0
    if g >= g0 + min_ground_gain:
        there = fit_pose_from_waterline(pano, gmap.shore_distances(gx, gy), heights_m=hs,
                                        offsets_deg=offs)
        if there.misfit_deg <= 1.5 * bp.misfit_deg:
            fx, fy, gf = gx, gy, g
            fp = PanoPose(goff % 360.0, gh, bp.pitch_fix_deg, there.misfit_deg, bp.n_cols)
            source = "waterline+ground" if source == "waterline" else "ground"
    return PositionFit(float(fx), float(fy), fp, at_seed.misfit_deg, bp.misfit_deg, g0,
                       float(gf), source)


@dataclass(frozen=True)
class OutlineFit:
    dx_m: float                  # camera moved east of the given pano position
    dy_m: float                  # ... and north
    pose: PanoPose               # with the bearing offset corrected
    shift_deg: float             # added to the bearing offset
    misfit_before_deg: float     # median |observed - OSM tower outline| on tower columns
    misfit_deg: float
    n_cols: int


def outline_misfit(pano: Pano, pose: PanoPose, model: np.ndarray, shift_deg: float = 0.0,
                   min_elev_deg: float = 3.0, top_rows: np.ndarray | None = None) -> tuple[float, int]:
    """Median |observed outline - OSM tower outline| (deg) over the columns where the towers
    rise above ``min_elev_deg``, and that column count. ``model``: ``skyline_match.predicted_outline``
    from the camera; the observed outline is each column's first non-sky row."""
    from .skyline_match import BIN_DEG, N_BINS

    top = (np.argmax(pano.labels != SKY_CLASS, axis=0).astype(float) if top_rows is None
           else top_rows)                       # callers in a search pass it once
    obs = pano.elevation_deg(top) + pose.pitch_fix_deg
    bear = (pano.frame_heading + pose.offset_deg + shift_deg) % 360.0
    pred = model[np.round(bear / BIN_DEG).astype(int) % N_BINS]
    m = pred > min_elev_deg
    if not m.any():
        return math.inf, 0
    return float(np.median(np.abs(obs[m] - pred[m]))), int(m.sum())


def refine_on_outline(pano: Pano, pose: PanoPose, towers, search_deg: float = 10.0,
                      step_deg: float = 0.2, move_m: float = 150.0, move_step_m: float = 50.0,
                      min_cols: int = 50, min_gain_deg: float = 0.3,
                      height_factors=(1.0,)) -> OutlineFit:
    """Bearing (and a small position nudge) from the OSM-tagged towers' outline.

    The waterline pins the camera height and pitch but leaves the bearing loose: on the three
    Cartagena drone seeds it was 4-6 deg off, so a footprint's columns fell on the building
    beside it (Nautica, a 160 m tower, read 42 m off a low block next to it). Slides the
    predicted outline of ``towers`` (``skyline_match.Towers``, OSM heights) over the observed
    one by up to ``search_deg`` and moves the camera up to ``move_m``; kept only when the
    misfit drops by ``min_gain_deg``. 2026-10-05: published tower heights MAE 82.5 -> 32.9 m,
    readings within 25 % of their tag 19 -> 40 %.

    ``height_factors`` also searches the camera height (x the given one): from a drone high
    over the city the waterline height can be far off, and a tower's elevation above the
    horizon depends on it as much as on the bearing (2026-10-06, Cartagena seed_7: waterline
    180 m up, no tower left above 3 deg, so no outline to fit).
    """
    from dataclasses import replace

    from .skyline_match import predicted_outline

    cam0 = np.array(towers.to_xy(pano.lat, pano.lon), float)
    model0 = predicted_outline(towers, tuple(cam0), h_cam=pose.camera_h_m)
    top = np.argmax(pano.labels != SKY_CLASS, axis=0).astype(float)
    before, n0 = outline_misfit(pano, pose, model0, top_rows=top)
    best = (before if n0 >= min_cols else math.inf, 0.0, 0.0, 0.0, n0, 1.0)
    steps = np.arange(-move_m, move_m + 1e-6, move_step_m)
    for hf in height_factors:
        hp = replace(pose, camera_h_m=pose.camera_h_m * hf)
        for dx in steps:
            for dy in steps:
                model = model0 if dx == 0 and dy == 0 and hf == 1.0 else predicted_outline(
                    towers, tuple(cam0 + (dx, dy)), h_cam=hp.camera_h_m)
                for s in np.arange(-search_deg, search_deg + 1e-6, step_deg):
                    e, n = outline_misfit(pano, hp, model, s, top_rows=top)
                    if n >= min_cols and e < best[0]:
                        best = (e, float(s), float(dx), float(dy), n, float(hf))
    # then a finer pass around the best: the readings of towers deep in a cluster change with
    # tens of metres (Cartagena seed_4 from two starts 100 m apart: Gran Bay 155 vs 84 m)
    if move_m > 0 and math.isfinite(best[0]) and best[1:4] != (0.0, 0.0, 0.0):
        _e, s0, dx0, dy0, _n, hf = best
        hp = replace(pose, camera_h_m=pose.camera_h_m * hf)
        fine = np.arange(-move_step_m, move_step_m + 1e-6, move_step_m / 5)
        for ddx in fine:
            for ddy in fine:
                model = predicted_outline(towers, tuple(cam0 + (dx0 + ddx, dy0 + ddy)),
                                          h_cam=hp.camera_h_m)
                for s in np.arange(s0 - step_deg, s0 + step_deg + 1e-6, step_deg / 4):
                    e, n = outline_misfit(pano, hp, model, s, top_rows=top)
                    if n >= min_cols and e < best[0]:
                        best = (e, float(s), float(dx0 + ddx), float(dy0 + ddy), n, hf)
    gain = (before if n0 >= min_cols else math.inf) - best[0]
    if not math.isfinite(best[0]) or gain < min_gain_deg:
        return OutlineFit(0.0, 0.0, pose, 0.0, before, before, n0)
    e, s, dx, dy, n, hf = best
    return OutlineFit(dx, dy, replace(pose, offset_deg=(pose.offset_deg + s) % 360.0,
                                      camera_h_m=pose.camera_h_m * hf), s, before, e, n)


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


#: A footprint's ``top_edge`` is its columns' most common edge; a tie goes to the first here.
#: ``max(set(edges), key=edges.count)`` broke ties by string-hash order, so a reading's trust
#: changed between runs (Cartagena seed_4, 2026-10-06: 12 of 172 readings, 84 or 72 trusted).
#: Depth first: half the columns not reaching the sky is not a sky-topped reading.
EDGE_TIE_ORDER = ("depth", "other", "sky")


def _top_edge(edges: list[str]) -> str:
    """The most common of a footprint's column edges, a tie broken by :data:`EDGE_TIE_ORDER`."""
    return max(EDGE_TIE_ORDER, key=lambda e: (edges.count(e), -EDGE_TIE_ORDER.index(e)))


#: Labels that hide a building's foot from a drone without being another building: trees and
#: palms, grass and plants, road surface (road, sidewalk, path, car), water, sand and pier (the
#: beach front of Bocagrande). A run may cross more of them than ``gap_px``
#: (:func:`_column_run`'s ``soft_gap_px``). Cartagena seed_5 (2026-10-07): 13-22 pier rows under
#: Nautica and Ravello at 260-271 m, so both runs ended in the gap and Nautica's columns went
#: to the footprint behind it (b0111, read 173 m).
OCCLUDER_CLASSES = tuple(_ADE20K_VEGETATION_CLASSES) + (72,) + ROAD_CLASSES + tuple(
    _ADE20K_WATER_CLASSES) + (46, 140)


def _column_run(labels_col: np.ndarray, depth_col, start_row: int, near_lim: float,
                stop_ratio: float, floor: float, gap_px: int, local_px: int = 8,
                inst_col: np.ndarray | None = None, soft_gap_px: int | None = None):
    """One building's rows in one column: ``(top, bottom, edge)`` or None.

    Going up from ``start_row``: skip building rows nearer than this building (inverse depth
    above ``near_lim``: something in front that the occlusion line missed) and up to ``gap_px``
    other rows (a palm, a lamp post); the first building row at this building's depth is its
    visible bottom. Then follow building rows until the inverse depth drops below
    ``stop_ratio`` x the median of the ``local_px`` rows just below (a step to the surface
    behind) or below ``floor``. Against the local level, not the base: Depth Anything lets a
    tall facade drift 10-20 % farther towards its top, and a stop at 0.92 x the base level cut
    the tallest towers short (2026-10-05: Ravello, tag 160 m, read 17 m); a roofline is a step
    over a few rows. ``edge`` says what ends the run: sky, depth (a building behind), other (a
    label that is neither), or image (the top row).

    ``inst_col``: building instance labels (``building_instances``) for the column. Inside one
    instance the run never stops (Depth Anything's drift up a tall facade); the depth step is
    tested only where the instance changes. Stopping at every instance change instead cut
    towers at the podium in front of them (2026-10-06, Cartagena seed_4: Portomarine 180 -> 28 m).

    ``soft_gap_px``: below the building, up to this many rows in all may be skipped as long as
    no more than ``gap_px`` of them are other than :data:`OCCLUDER_CLASSES` (trees, road, sea
    in front of the foot).
    """
    is_b = np.isin(labels_col, BUILDING_CLASSES)
    y, gap, hard = int(start_row), 0, 0
    max_gap = gap_px if soft_gap_px is None else max(gap_px, int(soft_gap_px))
    soft = np.isin(labels_col, OCCLUDER_CLASSES) if max_gap > gap_px else None
    if y < 0 or y >= len(labels_col):
        return None
    while y >= 0:
        if is_b[y]:
            if depth_col is None or depth_col[y] <= near_lim:
                break
        else:
            gap += 1
            hard += soft is None or not soft[y]
            if hard > gap_px or gap > max_gap:
                return None
        y -= 1
    if y < 0:
        return None
    bottom = y
    while y - 1 >= 0 and is_b[y - 1]:
        if inst_col is not None and inst_col[y] > 0 and inst_col[y - 1] == inst_col[y]:
            y -= 1                                  # inside one instance: never a roofline
            continue
        if depth_col is not None:
            d = depth_col[y - 1]
            if d < floor or d < stop_ratio * float(np.median(depth_col[y:min(bottom, y + local_px - 1) + 1])):
                break
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
    get = m.get if isinstance(m, dict) else lambda k: getattr(m, k, None)
    if get("top_edge") == "roof" and get("confidence") is not None:
        seen = float(get("confidence"))         # a roof fit from above (roof_fit): its own score
    else:
        seen = 1.0 if get("base_visible") else float(get("visible_frac"))
    scale = get("weight_scale")
    return (1.0 if scale is None else float(scale)) * seen / max(float(get("dist_m")), 100.0) ** 2


def fuse_heights(by_seed: dict, agree: float = 0.25, overrule: float = 3.0) -> dict:
    """One height per footprint from several seeds' measurements (``{seed: [Measured or dict]}``).

    Seeds within ``agree`` (relative) of the most reliable one (:func:`measurement_weight`) are
    averaged with those weights; the rest are outvoted, and the footprint is marked disputed
    when any were. 2026-10-04, Cartagena: where seed_1 (400-600 m) and seed_5 (1.1-1.3 km)
    disagreed, the far seed read the tower behind (13 vs 81 m, 26 vs 111 m); the near seed is
    17 m off the OSM tags, the far one 56 m.

    ``disputed`` only when the outvoted readings were not much weaker: if the kept group's
    weight is ``overrule`` x every outvoted reading's, the dispute is settled in its favour
    (2026-10-06, Cartagena: Gran Bay read 20 m from seed_4 at a depth edge 1.3 km out and 170 m
    from seed_6's confident roof fit; dropping such footprints lost a published tower).
    """
    rows: dict = {}
    for seed, ms in by_seed.items():
        for m in ms:
            d = m if isinstance(m, dict) else m.__dict__
            rows.setdefault(d["footprint"], []).append((seed, d, measurement_weight(d)))
    out = {}
    for fp, got in rows.items():
        got.sort(key=lambda r: -r[2])
        best = got[0][1]["height_m"]
        keep = [r for r in got if abs(r[1]["height_m"] - best) <= agree * max(1.0, best)]
        w = np.array([r[2] for r in keep])
        out[fp] = {"name": got[0][1]["name"],
                   "height_m": float(np.average([r[1]["height_m"] for r in keep], weights=w)),
                   "seeds": {r[0]: round(float(r[1]["height_m"]), 1) for r in got},
                   "used": [r[0] for r in keep],
                   "disputed": any(r not in keep and w.sum() < overrule * r[2] for r in got),
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


#: Focal length (px per radian) of the default 75-deg spin pano, for which the pixel
#: parameters of :func:`measure_footprints` were tuned (Cartagena seeds 1/4/5, 2026-10-05/06).
REF_F_PX = 417.0


def px_scale(pano: Pano) -> float:
    """Pixel scale of ``pano`` against the spin pano the pixel constants were tuned on; never
    below 1 (a coarser pano keeps the tuned pixels, as the MobileSAM window in ``elevated``)."""
    return max(1.0, float(pano.f_px) / REF_F_PX)


def _scaled(px: int, k: float) -> int:
    return max(1, int(round(px * k)))


def measure_footprints(pano: Pano, pose: PanoPose, footprints: list[Footprint],
                       depth: np.ndarray | None = None, max_dist_m: float = 3000.0,
                       min_cols: int = 6, core: float = 0.6, depth_ratio: float = 0.7,
                       base_window_px: int = 6, near_ratio: float = 1.35,
                       max_step: float = 0.92, gap_px: int = 8,
                       min_visible_frac: float = 0.25, min_run_px: int = 3,
                       local_px: int = 40, instances: np.ndarray | None = None,
                       occluder_h_m: float | None = 10.0) -> list[Measured]:
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
    - the run stops at a step down to the midpoint between this building's level and that of
      the next OSM footprint behind it in that column, as a ratio clipped to ``depth_ratio`` ..
      ``max_step`` (a fixed 0.7 only separated a tower 1.4x farther; the rows of Bocagrande
      are 10-30 % apart), measured against the run's own last rows (see :func:`_column_run`)
      and never below ``depth_ratio`` x the reference -- or x the base's own depth when the
      base is in view and lower (a far tower Depth Anything puts behind the model level);
    - a building whose base is hidden and of which under ``min_visible_frac`` of the
      base-to-top height shows is dropped: the rows just above a nearer roof are as likely the
      building behind it;
    - a column counts only with a run of ``min_run_px`` rows or more (single-row runs read
      0.9-2.7 m for whole buildings, 2026-10-04);
    - a footprint that holds smaller ones (a podium with its tower mapped separately, the
      Plaza Bocagrande mall) is measured on the columns its inner footprints leave free, so
      the tower is not read as the podium.

    ``instances``: a building instance label map on the pano grid (``building_instances``); a
    column's run then ends at its instance's top (see :func:`_column_run`).

    Pixel parameters (``min_cols``, ``base_window_px``, ``gap_px``, ``min_run_px``,
    ``local_px``, the depth median window and the base tolerance) are in pixels of the default
    75-deg spin pano (``f_px`` :data:`REF_F_PX`, 7.3 px/deg) and scale with the pano's
    ``f_px`` (:func:`px_scale`): the same angles on a hi-res sphere (1194 px, 2.86x).
    ``occluder_h_m``: below the building, a run may cross up to ``atan(occluder_h_m / d)`` of
    trees, road, sand, pier or sea (:data:`OCCLUDER_CLASSES`) in front of its foot, beyond
    ``gap_px`` (see :func:`_column_run`); None: ``gap_px`` only. 10 m (2026-10-07, Cartagena
    seeds 1/4/5/6/7): seed_5 reads Nautica 160 m (tag 161) from its own run, no tagged reading
    lost or worse; 12.5 m lost a verified footprint, 14-20 m also crossed Ravello's 22 pier
    rows but added a 40 m building read 123 m (fused tag within 25 % 73 -> 67 %); a flat 24 px
    gap broke the tagged median (4 -> 41 %).
    """
    import shapely

    H, W = pano.labels.shape
    k_px = px_scale(pano)
    min_cols, base_window_px, gap_px, min_run_px, local_px = (
        _scaled(v, k_px) for v in (min_cols, base_window_px, gap_px, min_run_px, local_px))
    med_win = _scaled(5, k_px) | 1                 # odd
    base_tol = 3.0 * k_px
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

        dz = median_filter(np.asarray(depth, float), size=(med_win, 1))    # vertical: keeps edges

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
            soft_gap = None if not occluder_h_m else int(round(
                pano.f_px * math.atan(occluder_h_m / dn)))
            for a, b in inner.get(k, ()):
                cols = cols[(cols < a) | (cols > b)]
            if cols.size < 3:
                continue
            tops, bottoms, edges, vis = [], [], [], 0
            for x in cols:
                y0 = int(round(min(base, occ[x] - 1))) - 1
                if y0 >= H or y0 < 1:
                    continue
                near_lim, ratio, floor = np.inf, 0.0, -np.inf
                if dz is not None:
                    ref = bref = None
                    if occ[x] - 1 >= base - 0.5:          # base not behind a measured roof
                        ref = bref = float(np.median(dz[max(0, y0 - base_window_px):y0 + 1, x]))
                        if level is not None and not level / near_ratio <= ref <= level * near_ratio:
                            ref = None                    # the base row shows something else
                    if ref is None:
                        if level is None:                 # pass 1: no model yet
                            ref = float(dz[y0, x])
                        else:
                            ref = level
                    near_lim = near_ratio * ref
                    # the floor from the lower of the reference and the base's own depth: past
                    # ~700 m Depth Anything puts single buildings at 0.6-1.4x the model level
                    # (Cartagena seed_4, Gran Bay at 809 m: base 0.048, model 0.071), and a floor
                    # at 0.7x the model cut its whole facade (read 20 m, tag 170 m)
                    ratio, floor = depth_ratio, depth_ratio * (ref if bref is None else min(ref, bref))
                    if model is not None:
                        k = np.searchsorted(behind[x], dn * 1.08)
                        if k < len(behind[x]):            # next OSM footprint behind, this column
                            far = (model[0] / behind[x][k] + model[1]) / level
                            ratio = min(max(0.5 * (1.0 + far), depth_ratio), max_step)
                got = _column_run(pano.labels[:, x], None if dz is None else dz[:, x], y0,
                                  near_lim, ratio, floor, gap_px, local_px,
                                  None if instances is None else instances[:, x], soft_gap)
                if got is None or got[2] == "image" or got[1] - got[0] + 1 < min_run_px:
                    continue
                t, bot, edge = got
                tops.append(t)
                bottoms.append(bot)
                edges.append(edge)
                vis += bot >= base - base_tol
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
            edge = _top_edge(edges)
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
