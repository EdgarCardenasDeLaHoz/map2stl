"""Place a plate on the map by its streets, starting from where the pack says it is.

Why this exists
---------------
Every placement study from 2026-09-08 to 2026-09-10 failed on the grid cities, and the failures
came from the solver, not from the packs (memory-bank/decisions.md, 2026-09-10 and 2026-09-11):

* The plate was matched 1.5 times too large.  The exporter's plate raster covers the plate's own
  span and the OSM raster covers 1.5 times that (`export_align_data.py`, the comment above
  `gscale`), so one plate cell is `cell_size_m * geometric_guess.scale` of ground, not
  `cell_size_m`.
* The miniatures were never turned.  `geometric_guess.rot_deg` records the turn the building solver
  measured (Denver Miniature 135.2 degrees, Paris Miniature 44.0) and the plate is turned BY it with
  `cv2.getRotationMatrix2D`, exactly as the exporter does.  A free search over headings is worse:
  grid cities take a wrong heading at a margin of a few hundredths of a standard deviation.
* Density is the wrong criterion for a grid city.  Merging footprints by 25 m closes every street,
  and a grid of blocks has the same density everywhere, so the density search pulled Philadelphia
  512 m away and turned it 17 degrees.  Laid over the raw footprints, the pack's own pose agreed on
  75% of cells and the searched one on 58%.

So the placement compares the plate's footprint mask with the map's raw footprints smoothed by one
8 m cell, where streets survive.  A street grid repeats every block, which makes that criterion
aliased everywhere except near the truth, so the search is local: 400 m around the start, turn
within 6 degrees of the start, size 0.85 to 1.20.  The start is the pack's own pose.  For a plate
with no pose, `density_search` looks coarse and wide (footprints merged into districts, plus the
river and a 90 m SRTM terrain where they apply) and hands its four best distinct places to a
quick street refine; the full refine runs at whichever of them the streets prefer.  A single
density winner was wrong for Barcelona, Lisbon, Miami and the Alhambra from 1 km off; ranking the
candidates by their streets recovered all four.  A district peak can still sit 400-600 m off the
plate on a grid city (Philadelphia Miniature, Valencia), so the quick refine reaches 700 m and its
window follows it when it ends near that edge; with that, all 14 packs are found from 1 km off.

Two extra channels join the footprints where they carry information the footprints lack:

* Water with its bridges.  A river places a plate across its width and hardly at all along its
  length; the bridges are what pin the length.  The plate's water cut-out already has a gap at every
  deck, so the map side is OSM water with every bridge stroked across it.
* Terrain.  A plate that is mostly hillside (the Alhambra) or has a hill on it (Salzburg's
  Moenchsberg, Prague's Petrin) is placed by its ground as well as its buildings.  The map side is
  the 30 m Copernicus DEM; the plate side is its relief minus the exporter's residual, the ground
  the residual was measured from.  The DEM decides whether the channel is used (5-95% spread of at
  least 25 m under the plate), because the plate's own vertical scale is arbitrary.

Both sides of the footprint channel were remodelled on 2026-09-13 (memory-bank/decisions.md):

* Map: `map_buildings` is the covered fraction of each cell (osm_model.coverage), from the
  standing footprint classes plus raised road and rail decks, where it had been every footprint
  burned all_touched, about 40% fatter than the city.
* Plate: `built_height` adds back buildings wider than the exporter's 80 m residual opening
  (`mesa_additions`), which the residual drops, and a cell is built where most of it is, after
  the native mask is cut at half the local top (`built_mask`) to remove the exporter's blur halo.
Together they raised the mean `unique` over the 14 packs from 4.43 to 7.17 at the packs' poses.

Each channel is scored as a z-surface over the local window and the surfaces are summed with
weights.  Every result carries `unique` (the winner's lead over anything more than 80 m away, and
with no pose also over the next-best candidate place) and `size_margin` (its lead over any size
more than 6% different); the placement is `confident` when both are at least 1.0.

Results go to `data/<slug>/placement.json`; the export's own rasters and meta.json are not touched.

Promoted 2026-09-27 from ``tools/align_tool/street_place.py`` so the web app can run it
(``app/server/core/plate_registration.py``); the tool file is now a CLI shim over this module.
`locate` below is ``city2stl.registration.osm_water``, the part of the tool's locate.py it used.
"""

from __future__ import annotations

import argparse
import json
import math
import multiprocessing as mp
import pathlib
import time

import cv2
import numpy as np

from city2stl.registration import align_paths as _paths
from city2stl.registration import osm_model
from city2stl.registration import osm_water as locate
from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

# Packs and caches stay where the align tool has always written them (align_paths).
DATA = _paths.DATA
CACHE = _paths.STREET_CACHE

PLATE_RES = 256          # the plate is read at this many cells across before posing
FLOOR_M = 2.0            # height above ground that counts as built
GROUND_M = 60.0          # wider than any one building, narrower than the terrain beneath it
TARGET_CELL_M = 8.0      # the map's cell: fine enough that a street is a cell or two wide
SMALL_PLATE_M = 1000.0   # below this span the map cell shrinks so the plate stays ~250 cells

# Buildings wider than the exporter's 80 m residual opening, read back from the relief.
MESA_GROUND_M = 300.0        # the wide ground they stand above
MESA_WORK_M = 8.0            # cell the wide ground is taken at
MESA_MIN_M = 8.0             # a candidate cell stands at least this far above the wide ground
MESA_RIM = 0.6               # a roof's rim drops by at least this share of its height
MESA_CAP_M2 = 200000.0       # larger components are landforms
MESA_RING_PX = 2
MESA_TERRAIN_SIGMA_M = 150.0
MESA_TERRAIN_SLOPE = 0.1     # woods and hills stand on sloping ground; roofs here do not

# The plate's built mask stands about one native cell proud of the footprints on every side (the
# exporter blurs the relief by 6 m before its residual), which shows as a red line along every
# street once the map is true coverage.  A cell counts as built only above EDGE_FRAC of the local
# top within EDGE_REACH_M (`built_mask`).  This raised the mean `unique` over the 14 packs from
# 5.44 to 7.17 without moving a pose.  A fixed 6 m erosion reached 6.74 but deleted small
# buildings: San Juan's no-pose `unique` fell from 1.48 to 0.31 (half-max: 1.40).
EDGE_REACH_M = 10.0
EDGE_FRAC = 0.5

# Street-scale refinement.
REFINE_BLOCK_M = 8.0
REFINE_REACH_M = 400.0
REFINE_TURNS = np.arange(-6.0, 6.01, 1.0)
REFINE_SIZES = np.arange(0.85, 1.201, 0.025)
APART_M = 80.0           # a runner-up must be at least a block away to count against the winner
SIZE_APART = 0.06

# Density search, only for a plate with no usable pose.
DENSITY_MERGE_M = 25.0
DENSITY_BLOCKS_M = (60.0, 120.0)
DENSITY_SIZES = (0.9, 1.0, 1.1, 1.2)
DENSITY_TURNS = np.arange(-20.0, 20.1, 5.0)
DENSITY_CANDIDATES = 4   # distinct places handed to the street refine
DENSITY_WITHIN = 3.0     # a candidate this far below the best is not worth refining
QUICK_TURNS = np.arange(-6.0, 6.01, 2.0)    # the refine's grid when it only ranks candidates
QUICK_SIZES = np.arange(0.85, 1.201, 0.05)
REFINE_HOPS = 2          # times a candidate's window may follow a refine that walked to its edge
HOP_FRAC = 0.6           # "near the edge": this fraction of the reach
QUICK_REACH_M = 700.0    # the ranking refine's reach: past a density peak's typical error

# Channels besides the footprints.
WATER_MIN_FRAC = 0.01    # the plate needs this much water cut-out before the channel is used
WATER_BLOCK_M = 16.0
WATER_WEIGHT = 0.5
BRIDGE_WIDTH_M = 14.0
TERRAIN_MIN_RELIEF_M = 25.0   # the DEM's spread under the plate, 5th to 95th percentile
TERRAIN_BLOCK_M = 60.0
TERRAIN_WEIGHT = 0.5

# Parks are "don't care" for the footprint channel (`map_parks`).  The plate reads canopy as
# built and the map reads a park as empty, so every park scored against the true pose.  Only
# leisure=park: gardens also cover the Alhambra's palaces (Granada's `unique` fell 4.66 -> 3.98),
# wood and forest cover the hills Salzburg and Granada are placed by (both lost), and single
# trees changed nothing.  Tested 2026-09-14 on 14 packs at the pack's pose: mean `unique`
# 7.17 -> 7.69, 11 up and 3 down (Miami -0.39 the worst), no pose moved.
PARK_SELECTOR = '["leisure"="park"]'
PARK_CARE_BELOW = 0.5    # a cell is cared for while less than this fraction of it is park

CONFIDENT = 1.0


# --------------------------------------------------------------------------------------------
# Shared raster helpers

def small(arr, res=PLATE_RES, how=cv2.INTER_AREA):
    return cv2.resize(np.asarray(arr, np.float32), (res, res), interpolation=how)


def smooth(value, cell_m, block_m):
    sigma = max(1.0, block_m / cell_m / 2.0)
    return cv2.GaussianBlur(np.asarray(value, np.float32), (0, 0), sigma)


def standardize(value, valid):
    inside = value[valid]
    if inside.size == 0:
        return np.zeros_like(value)
    out = (value - float(inside.mean())) / (float(inside.std()) or 1.0)
    return np.where(valid, out, 0.0).astype(np.float32)


def prepared(value, valid, cell_m, block_m):
    return standardize(smooth(value, cell_m, block_m), valid)


def merged(built, cell_m, merge_m):
    """Footprints closed across gaps narrower than `merge_m`: a district rather than a building."""
    r = max(1, int(round(merge_m / cell_m / 2.0)))
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1))
    return cv2.morphologyEx(np.asarray(built, np.float32), cv2.MORPH_CLOSE, k)


# --------------------------------------------------------------------------------------------
# The plate

def metres_per_unit(residual, relief, tallest_m, window=3):
    """Metres per plate unit, anchored on the tallest roof rather than the tallest cell.

    A minimum filter leaves a cell only as high as the lowest of its neighbours, so a one-cell
    spike collapses while the middle of a roof is untouched.
    """
    v = np.where(np.isfinite(relief), np.nan_to_num(residual, nan=0.0), 0.0).astype(np.float32)
    top = float(cv2.erode(v, np.ones((window, window), np.uint8)).max())
    if top <= 0:
        top = float(v.max())
    return float(tallest_m) / top


def _opened(v, cell_m, width_m):
    r = max(3, int(round(width_m / cell_m))) | 1
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (r, r))
    return cv2.morphologyEx(v, cv2.MORPH_OPEN, k)


def _filled(a, keep):
    """`a` with every cell outside `keep` given the value of its nearest kept cell."""
    from scipy import ndimage
    idx = ndimage.distance_transform_edt(~keep, return_distances=False, return_indices=True)
    return a[idx[0], idx[1]]


def _wide_ground(r, cell_m, width_m):
    """A flat `width_m` opening of `r`, taken at MESA_WORK_M cells and smoothed by half its width."""
    h, w = r.shape
    f = cell_m / MESA_WORK_M
    small_r = cv2.resize(np.asarray(r, np.float32), (max(8, int(w * f)), max(8, int(h * f))),
                         interpolation=cv2.INTER_AREA)
    k = max(3, int(round(width_m / MESA_WORK_M)) | 1)
    g = cv2.morphologyEx(small_r, cv2.MORPH_OPEN,
                         cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    g = cv2.blur(g, (k // 2 | 1, k // 2 | 1))
    return cv2.resize(g, (w, h), interpolation=cv2.INTER_LINEAR)


def mesa_additions(f, h_big, base, land, cell_m, tslope):
    """Components of (h_big > MESA_MIN_M) not already in `base` that stand on a steep rim, on flat
    ground.

    `h_big` is the relief above a wide ground, in metres, so a big roof is in it, but so are a
    hillside and a wood.  A roof drops by about its own height at its rim, while a hillside's rim
    is its gentle foot, so a component is kept when the relief just inside its rim, less the
    relief just outside (`base` cells excluded, they are other buildings), is at least MESA_RIM
    of its median height.  A wood is a steep-rimmed plateau too, but woods and hills stand on
    sloping ground, so the component must also sit where `tslope`, the exporter's terrain
    smoothed at MESA_TERRAIN_SIGMA_M, is flatter than MESA_TERRAIN_SLOPE.
    """
    from scipy import ndimage
    cand = (h_big > MESA_MIN_M) & land & ~base
    lab, _ = ndimage.label(cand, structure=np.ones((3, 3)))
    out = np.zeros_like(cand)
    ring_px = MESA_RING_PX
    se = np.ones((2 * ring_px + 1,) * 2, np.uint8)
    for i, sl in enumerate(ndimage.find_objects(lab), start=1):
        if sl is None:
            continue
        y0, y1 = max(0, sl[0].start - ring_px), min(lab.shape[0], sl[0].stop + ring_px)
        x0, x1 = max(0, sl[1].start - ring_px), min(lab.shape[1], sl[1].stop + ring_px)
        sub = lab[y0:y1, x0:x1] == i
        n = int(sub.sum())
        if n < 4 or n * cell_m ** 2 > MESA_CAP_M2:
            continue
        su = sub.astype(np.uint8)
        inner = sub & ~(cv2.erode(su, se) > 0)
        ring = (cv2.dilate(su, se) > 0) & ~sub & land[y0:y1, x0:x1] & ~base[y0:y1, x0:x1]
        ff = f[y0:y1, x0:x1]
        height = float(np.median(h_big[y0:y1, x0:x1][sub]))
        ok = (ring.sum() < 3 or
              np.median(ff[inner]) - np.percentile(ff[ring], 25) >= MESA_RIM * height)
        if ok and np.median(tslope[y0:y1, x0:x1][sub]) < MESA_TERRAIN_SLOPE:
            out[y0:y1, x0:x1] |= sub
    return out


def built_height(relief, residual, land, cell_m, mpu):
    """Height above the street in metres at every native plate cell (zero off the plate).

    The base is the exporter's residual less a GROUND_M opening: the residual is the relief less
    an 80 m opening (numpy2stl segmentation.terrain_residual), so a building is in it only if it
    is narrower than that.  A block of offices, a stadium or a warehouse wider than 80 m is
    therefore added back from the relief itself by `mesa_additions`: relief above a
    MESA_GROUND_M opening, kept where it stands on a steep rim over flat ground.
    """
    finite = np.isfinite(relief)
    v = np.where(finite, np.nan_to_num(residual, nan=0.0), 0.0).astype(np.float32)
    h0 = np.clip(v - _opened(v, cell_m, GROUND_M), 0, None) * mpu
    r = np.where(finite, relief, 0.0).astype(np.float32)
    keep = land & finite
    if not keep.any():
        return np.where(finite, h0, 0.0)
    rf = _filled(r, keep)
    hb = np.clip(r - _wide_ground(rf, cell_m, MESA_GROUND_M), 0, None) * mpu
    tg = np.where(finite, relief - np.nan_to_num(residual), np.nan)
    tk = keep & np.isfinite(tg)
    t = _filled(tg, tk) if tk.any() else np.zeros_like(r)
    t = cv2.GaussianBlur((t * mpu).astype(np.float32), (0, 0), MESA_TERRAIN_SIGMA_M / cell_m)
    gy, gx = np.gradient(t, cell_m)
    add = mesa_additions(rf * mpu, hb, h0 > FLOOR_M, keep, cell_m, np.hypot(gx, gy))
    return np.where(finite, np.where(add, np.maximum(h0, hb), h0), 0.0)


def built_mask(height, cell_m):
    """Built cells at the native resolution: above FLOOR_M and above EDGE_FRAC of the highest
    height within EDGE_REACH_M.  A blurred roof edge crosses half its height at the true edge
    whatever the building's size, so this trims the exporter's blur halo without deleting small
    buildings the way a fixed erosion does."""
    r = max(1, int(round(EDGE_REACH_M / cell_m)))
    top = cv2.dilate(height.astype(np.float32),
                     cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
    return height > np.maximum(FLOOR_M, EDGE_FRAC * top)


class Plate:
    """One pack's plate as the channels the placement compares, plus its recorded pose.

    `cell_m` is the ground one cell covers at PLATE_RES.  The exporter's plate raster spans
    `geometric_guess.scale` of the OSM window, whose cell is `meta.cell_size_m` at the export's
    resolution, so that product is the plate's own cell.
    """

    def __init__(self, slug: str | None = None, *, meta=None, relief=None, residual=None,
                 water=None, gscale=None, rot_deg=None):
        """Read a pack from `data/<slug>`, or take the same things as arrays from the exporter
        before it has written them (`meta` needs cell_size_m, tallest_m and osm_bbox_nsew)."""
        self.slug = slug or (meta or {}).get("slug")
        if meta is None:
            d = _paths.data_dir() / slug
            meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
            relief = np.load(d / "stl_relief.npy")
            residual = np.load(d / "stl_residual.npy")
            water_png = d / "stl_water.png"
            water = (cv2.imread(str(water_png), cv2.IMREAD_GRAYSCALE)
                     if water_png.exists() else None)
            guess = meta.get("geometric_guess") or meta.get("pipeline_guess") or {}
            # A pack with no guess at all is a surveyed surface cut to the OSM window itself
            # (San Juan's lidar), so it spans the whole window.
            gscale = guess.get("scale") if guess else 1.0
            rot_deg = guess.get("rot_deg")
        self.meta = meta
        relief = np.asarray(relief, np.float32)
        residual = np.asarray(residual, np.float32)
        w = None if water is None else np.asarray(water)
        self.gscale = float(gscale or 1.0 / 1.5)
        self.rot_deg = float(rot_deg or 0.0)
        # The long side is read at PLATE_RES; a plate that is not square keeps its aspect.
        ph, pw = relief.shape
        native = max(ph, pw)
        self.res_hw = (max(8, round(PLATE_RES * ph / native)), max(8, round(PLATE_RES * pw / native)))
        native_cell = float(self.meta["cell_size_m"]) * self.gscale
        self.cell_m = native_cell * native / PLATE_RES

        finite = np.isfinite(relief)
        self.valid = self._small(finite) > 0.5
        mpu = metres_per_unit(residual, relief, float(self.meta["tallest_m"]))
        wet = w is not None and w.shape == relief.shape and float((w > 0).mean()) >= WATER_MIN_FRAC
        land = finite & ~(w > 0) if wet else finite
        height = built_height(relief, residual, land, native_cell, mpu)
        # Built where most of the cell is built at the native resolution, rather than where the
        # cell's mean height clears the floor: a street beside a tall block stays a street.
        # The native mask is cut at half the local top first (`built_mask`), which drops the halo
        # the exporter's blur leaves round every block.
        up = built_mask(height, native_cell)
        self.built = np.where(self.valid, self._small(up.astype(np.float32)) > 0.5,
                              False).astype(np.float32)

        self.water = None
        if w is not None and float((w > 0).mean()) >= WATER_MIN_FRAC:
            self.water = np.where(self.valid, self._small((w > 0).astype(np.float32)) > 0.5,
                                  False).astype(np.float32)
            # A plate's river is its lowest layer, and the banks either side stand above an
            # opening that spans it, so the flatten can read the water itself as built.
            self.built = np.where(self.water > 0, 0.0, self.built).astype(np.float32)

        # Ground: the surface the exporter measured the residual from, so relief minus residual
        # is the plate's terrain with its buildings taken off.  An opening wide enough to clear a
        # city block leaves flat-topped discs on every slope; this does not.  Its units are the
        # plate's own, and correlation does not care how the terrain was exaggerated.
        both = finite & np.isfinite(residual)
        g = np.where(both, relief - np.nan_to_num(residual, nan=0.0), np.nan)
        g = np.nan_to_num(g, nan=float(np.nanmedian(g))).astype(np.float32) * mpu
        self.ground = np.where(self.valid, self._small(g), 0.0).astype(np.float32)
        spread = np.percentile(self.ground[self.valid], [5, 95])
        self.relief_m = float(spread[1] - spread[0])

    def _small(self, arr):
        rh, rw = self.res_hw
        return cv2.resize(np.asarray(arr, np.float32), (rw, rh), interpolation=cv2.INTER_AREA)

    def span_m(self):
        return self.cell_m * PLATE_RES


def posed(layer, valid, plate_cell, target_cell, size, turn, grid):
    """A plate layer drawn onto a `grid` canvas at `size` times its nominal scale, turned `turn`.

    Returns the canvas, its validity mask, and the plate's outer diameter in cells.  The turn is
    applied with `cv2.getRotationMatrix2D`, the exporter's own convention.
    """
    k = plate_cell * size / target_cell
    nh = max(8, int(round(layer.shape[0] * k)))
    nw = max(8, int(round(layer.shape[1] * k)))
    b = cv2.resize(layer, (nw, nh), interpolation=cv2.INTER_AREA)
    v = cv2.resize(valid.astype(np.float32), (nw, nh), interpolation=cv2.INTER_AREA)
    d = int(np.ceil(math.hypot(nh, nw))) + 4
    pb = np.zeros((d, d), np.float32)
    pv = np.zeros((d, d), np.float32)
    oy, ox = (d - nh) // 2, (d - nw) // 2
    pb[oy:oy + nh, ox:ox + nw] = b
    pv[oy:oy + nh, ox:ox + nw] = v
    m = cv2.getRotationMatrix2D((d / 2.0, d / 2.0), turn, 1.0)
    pb = cv2.warpAffine(pb, m, (d, d), flags=cv2.INTER_LINEAR, borderValue=0.0)
    pv = cv2.warpAffine(pv, m, (d, d), flags=cv2.INTER_LINEAR, borderValue=0.0) > 0.5
    canvas = np.zeros((grid, grid), np.float32)
    cv = np.zeros((grid, grid), bool)
    off = (grid - d) // 2
    canvas[off:off + d, off:off + d] = pb * pv
    cv[off:off + d, off:off + d] = pv
    ys, xs = np.where(pv)
    extent = int(max(ys.max() - ys.min(), xs.max() - xs.min())) + 1
    return canvas, cv, extent


# --------------------------------------------------------------------------------------------
# Fast masked correlation

class Reference:
    """The map at several smoothings, transformed once so every pose costs one forward transform.

    The map has data everywhere and the plate always sits wholly inside it, so the masked
    correlation collapses: the overlap is the plate's own mask, its sums are constants, and the
    map side needs only its own sliding sums under that mask.  A circular transform at the
    window's own size is exact for every shift that keeps the plate inside the window, which is
    all the search asks for, so nothing is padded.

    With a `care` mask (the footprint map with its parks left out) the map no longer has data
    everywhere and `_masked_surfaces` does the full doubly-masked correlation instead.
    """

    def __init__(self, image, cell, blocks, care=None):
        self.cell = cell
        self.blocks = tuple(blocks)
        self.shape = image.shape
        self.care = None
        self.f = []
        if care is not None and not care.all():
            # Map cells outside `care` neither agree nor disagree.  The map is smoothed by
            # normalized convolution (a park does not pull its neighbours towards empty) and
            # standardized over the cared cells only; `surfaces` then masks both sides.
            c = care.astype(np.float64)
            self.care = np.fft.rfft2(c)
            for b in self.blocks:
                num = smooth(image * care, cell, b)
                den = smooth(care.astype(np.float32), cell, b)
                r = np.where(den > 1e-3, num / np.maximum(den, 1e-3), 0.0)
                r = standardize(r, care).astype(np.float64) * c
                self.f.append((np.fft.rfft2(r), np.fft.rfft2(r * r)))
            return
        ones = np.ones(image.shape, bool)
        for b in self.blocks:
            r = prepared(image, ones, cell, b).astype(np.float64)
            self.f.append((np.fft.rfft2(r), np.fft.rfft2(r * r)))

    def surfaces(self, plate, pv):
        if self.care is not None:
            return self._masked_surfaces(plate, pv)
        M = np.conj(np.fft.rfft2(pv.astype(np.float64)))
        n = float(pv.sum())
        out = []
        for (F, F2), b in zip(self.f, self.blocks):
            g = prepared(plate, pv, self.cell, b).astype(np.float64)
            sg = float(g.sum())
            var_g = float((g * g).sum()) - sg * sg / n
            fg = np.fft.irfft2(F * np.conj(np.fft.rfft2(g)), self.shape)
            fm = np.fft.irfft2(F * M, self.shape)
            var_f = np.clip(np.fft.irfft2(F2 * M, self.shape) - fm * fm / n, 1e-9, None)
            s = (fg - fm * sg / n) / np.sqrt(var_f * max(var_g, 1e-9))
            out.append(np.fft.fftshift(np.clip(s, -1.0, 1.0)))
        return out

    def _masked_surfaces(self, plate, pv):
        """The correlation under the product of the plate's mask and the map's care mask.  The
        overlap and both sides' sums now vary with the shift: 6 inverse transforms per block
        rather than 3.  A shift whose overlap is under 30% of the plate scores 0.  With care
        everywhere this equals the unmasked path to 1e-17."""
        P = np.conj(np.fft.rfft2(pv.astype(np.float64)))
        n = float(pv.sum())
        ir = lambda X: np.fft.irfft2(X, self.shape)
        O = ir(self.care * P)
        On = np.maximum(O, 1.0)
        out = []
        for (F, F2), b in zip(self.f, self.blocks):
            g = prepared(plate, pv, self.cell, b).astype(np.float64)
            G = np.conj(np.fft.rfft2(g))
            G2 = np.conj(np.fft.rfft2(g * g))
            sf, sg = ir(F * P), ir(self.care * G)
            var_f = np.clip(ir(F2 * P) - sf * sf / On, 1e-9, None)
            var_g = np.clip(ir(self.care * G2) - sg * sg / On, 1e-9, None)
            s = (ir(F * G) - sf * sg / On) / np.sqrt(var_f * var_g)
            s = np.where(O > 0.3 * n, s, 0.0)
            out.append(np.fft.fftshift(np.clip(s, -1.0, 1.0)))
        return out


def disc(shape, reach):
    h, w = shape
    yy, xx = np.ogrid[:h, :w]
    return np.hypot(yy - h // 2, xx - w // 2) <= reach


def zsurface(ref, plate, pv, inside):
    """The weakest smoothing's standing above its own background, at every shift in `inside`."""
    total = None
    for surf in ref.surfaces(plate, pv):
        mu = float(surf[inside].mean())
        sd = float(surf[inside].std()) or 1.0
        z = np.where(inside, (surf - mu) / sd, -np.inf)
        total = z if total is None else np.minimum(total, z)
    return total


def argmax_shift(total):
    h, w = total.shape
    top = np.unravel_index(int(np.argmax(total)), total.shape)
    return (int(top[0] - h // 2), int(top[1] - w // 2)), float(total[top])


def runner_up(total, at, radius):
    h, w = total.shape
    yy, xx = np.ogrid[:h, :w]
    far = np.hypot(yy - (h // 2 + at[0]), xx - (w // 2 + at[1])) > radius
    return float(np.where(far, total, -np.inf).max())


# --------------------------------------------------------------------------------------------
# The map

def window(meta, side_m, cell_m=TARGET_CELL_M, centre=None):
    """A square window `side_m` across about `centre` (lat, lon), default the export's own centre.

    Returns (bbox n,s,e,w), grid size, and the actual cell size.
    """
    n, s, e, w = (float(v) for v in meta["osm_bbox_nsew"])
    lat, lon = centre if centre is not None else (0.5 * (n + s), 0.5 * (e + w))
    grid = int(math.ceil(side_m / cell_m / 2.0)) * 2
    side = grid * cell_m
    half_lat = 0.5 * side / M_PER_DEG_LAT
    half_lon = 0.5 * side / m_per_deg_lon(lat)
    return (lat + half_lat, lat - half_lat, lon + half_lon, lon - half_lon), grid, cell_m


def _cache_path(kind, bbox, grid):
    CACHE.mkdir(parents=True, exist_ok=True)
    key = "_".join(f"{v:.5f}" for v in bbox)
    return CACHE / f"{kind}_{key}_{grid}.npy"


RAIL_SELECTOR = '["railway"~"^(rail|light_rail|subway|tram|narrow_gauge|monorail)$"]'
# Only raised roads reach the map, so only ways that could be raised are fetched: every highway
# over a city window times out on the public mirrors (Bilbao's 4 km hidden-test window, 504).
DECK_SELECTORS = {"road_bridges": '["highway"]["bridge"]', "road_layers": '["highway"]["layer"]',
                  "rail": RAIL_SELECTOR}


def map_buildings(bbox, grid):
    """What stands above the street in the window, as the covered fraction of each cell, row 0
    south: OSM footprints of the standing classes (osm_model.STANDING), plus road and rail decks
    that are bridges or on a positive layer, which the plate has as relief too.

    Coverage rather than all_touched: at the 8 m cell all_touched made the map about 40% more
    built than it is.  Tested on all 14 packs at the pack's pose (2026-09-13), this map with the
    mesa-extended plate raised the mean `unique` from 4.43 to 5.44 without moving any answer.
    Tree canopy as a third layer helped some packs and hurt Granada, so it is left out.

    Cached twice: the raw Overpass elements per layer (a wide window is a quarter of a million
    footprints and the public endpoint is paced), and the burned raster per grid.
    """
    path = _cache_path("standing", bbox, grid)
    if path.exists():
        return np.load(path)
    els = osm_model.elements('["building"]', bbox, CACHE, "buildings")
    feet = [g for g, t in osm_model.areal(els)
            if osm_model.building_class(t) in osm_model.STANDING]
    ways, seen = [], set()
    for kind, sel in DECK_SELECTORS.items():
        for el in osm_model.elements(sel, bbox, CACHE, kind):
            if (el.get("type"), el.get("id")) not in seen:
                seen.add((el.get("type"), el.get("id")))
                ways.append(el)
    width = lambda t: osm_model.road_width(t) if "highway" in t else osm_model.RAIL_W
    decks = [g for g, t in osm_model.stroked(ways, width) if osm_model.raised(t)]
    mask = np.maximum(osm_model.coverage(feet, bbox, grid),
                      osm_model.coverage(decks, bbox, grid)).astype(np.float32)
    np.save(path, mask)
    return mask


def map_parks(bbox, grid):
    """OSM parks (PARK_SELECTOR) as the covered fraction of each cell, row 0 south: where the
    footprint map is not trusted to say "empty", because the plate reads a park's canopy as
    built.  The same plate-built-but-unmapped cells are dropped from the exported mask after
    placement by export_align_data's `semantic_exclusion`; this keeps them out of the search."""
    path = _cache_path("parks", bbox, grid)
    if path.exists():
        return np.load(path)
    els = osm_model.elements(PARK_SELECTOR, bbox, CACHE, "parks")
    mask = osm_model.coverage([g for g, _ in osm_model.areal(els)], bbox, grid).astype(np.float32)
    np.save(path, mask)
    return mask


BRIDGE_SELECTORS = (
    '["bridge"]["bridge"!="no"]["highway"]',
    '["bridge"]["bridge"!="no"]["railway"]',
)


def map_water_bridges(bbox, grid):
    """OSM water with every bridge stroked across it, row 0 south: the map-side twin of the
    plate's water cut-out, which has a gap at every deck.

    Bridges are stroked at their tagged width, else BRIDGE_WIDTH_M.  A river places a plate
    across its width; the gaps are what pin it along its length.
    """
    path = _cache_path("waterbridges", bbox, grid)
    if path.exists():
        return np.load(path)
    water = locate.osm_water(bbox, grid, verbose=False)
    if water["missing"]:
        raise RuntimeError(f"water layers failed: {water['missing']}")
    wet = (water["filled"] > 0) & ~(_islands(bbox, grid) > 0)
    decks = np.zeros_like(wet)
    for sel in BRIDGE_SELECTORS:
        elements = locate._overpass(sel, bbox)
        geoms = locate._geometries(elements, line_width_m=BRIDGE_WIDTH_M)
        if geoms:
            decks |= locate._rasterize(geoms, bbox, grid) > 0
    out = (wet & ~decks).astype(np.float32)
    np.save(path, out)
    return out


def _islands(bbox, grid):
    """The inner rings of every water multipolygon: the islands a river flows round.

    `locate._geometries` polygonizes all of a relation's members together, which paints the Ile
    de la Cite as river.  The plate has it as land, so here the inner rings are taken back out.
    """
    from shapely.geometry import LineString
    from shapely.ops import linemerge, polygonize
    geoms = []
    for el in locate._overpass(locate.SELECTORS["water"], bbox):
        if el.get("type") != "relation":
            continue
        lines = [LineString([(q["lon"], q["lat"]) for q in m["geometry"]])
                 for m in el.get("members") or []
                 if m.get("role") == "inner" and len(m.get("geometry") or []) >= 2]
        if lines:
            try:
                geoms.extend(g for g in polygonize(linemerge(lines)) if not g.is_empty)
            except Exception:
                pass
    if not geoms:
        return np.zeros((grid, grid), np.float32)
    return locate._rasterize(geoms, bbox, grid)


TERRAIN_TILE_SIDE_M = 12000.0
_TERRAIN_CACHE_NS = "align_terrain"


def _terrain_tile(lat, lon):
    """A 30 m DEM tile about (lat, lon), row 0 north, with its bbox.

    One tile per city, snapped to a 0.05 degree grid so every window in that city reuses it:
    OpenTopography allows 50 calls a day.  Falls back to the local ~90 m SRTM store.  Cached in
    `geo2stl.cache` (namespace `align_terrain`, kept a year); the raw COP30 GeoTIFF is also kept
    by `geo2stl.opentopo`, so a lost entry is rebuilt without another API call.
    """
    from geo2stl.cache import NAMESPACE_TTL, make_cache_key, read_array_cache, write_array_cache
    NAMESPACE_TTL.setdefault(_TERRAIN_CACHE_NS, 365 * 86400)
    snap = 0.05
    clat, clon = round(lat / snap) * snap, round(lon / snap) * snap
    half_lat = 0.5 * TERRAIN_TILE_SIDE_M / M_PER_DEG_LAT
    half_lon = 0.5 * TERRAIN_TILE_SIDE_M / m_per_deg_lon(clat)
    bbox = (clat + half_lat, clat - half_lat, clon + half_lon, clon - half_lon)
    key = make_cache_key(_TERRAIN_CACHE_NS, *bbox, {"side_m": TERRAIN_TILE_SIDE_M})
    hit = read_array_cache(_TERRAIN_CACHE_NS, key)
    if hit is not None:
        return hit[0]["dem"], tuple(hit[1]["bbox"]), str(hit[1]["source"])

    from geo2stl import dem as geodem
    n, s, e, w = bbox
    dem, source = None, None
    try:
        dem = geodem.fetch_opentopo_dem(n, s, e, w, "COP30", None, 512)
        source = "COP30"
    except Exception as exc:
        print(f"COP30 unavailable ({type(exc).__name__}: {str(exc)[:120]}); using local SRTM",
              flush=True)
        dem = geodem.fetch_h5_dem(n, s, e, w, h5_file=_paths.H5_ROOT / "strm_data.h5")
        source = "SRTM-h5"
    dem = np.asarray(dem, np.float32)
    if not np.isfinite(dem).any():
        raise RuntimeError("DEM tile is empty")
    dem = np.where(np.isfinite(dem), dem, np.nanmedian(dem)).astype(np.float32)
    write_array_cache(_TERRAIN_CACHE_NS, key, {"dem": dem},
                      {"bbox": list(bbox), "source": source})
    return dem, bbox, source


def map_terrain_wide(bbox, grid):
    """Ground elevation over a window wider than one 30 m tile, row 0 south, from the local
    ~90 m SRTM store: enough for a district-scale search, and it spends no OpenTopography call."""
    path = _cache_path("srtm", bbox, grid)
    if path.exists():
        return np.load(path)

    from geo2stl import dem as geodem
    n, s, e, w = bbox
    dem = np.asarray(geodem.fetch_h5_dem(n, s, e, w, h5_file=_paths.H5_ROOT / "strm_data.h5"),
                     np.float32)
    dem = np.where(np.isfinite(dem), dem, np.nanmedian(dem)).astype(np.float32)
    img = cv2.resize(dem, (grid, grid), interpolation=cv2.INTER_CUBIC)[::-1].copy()
    np.save(path, img)
    return img


def map_terrain(bbox, grid):
    """Ground elevation in metres over the window, row 0 south, sampled from the city's tile."""
    n, s, e, w = bbox
    dem, (tn, ts, te, tw), _ = _terrain_tile(0.5 * (n + s), 0.5 * (e + w))
    h, wd = dem.shape
    lat = s + (np.arange(grid) + 0.5) / grid * (n - s)          # row 0 south
    lon = w + (np.arange(grid) + 0.5) / grid * (e - w)
    ty = ((tn - lat) / (tn - ts) * h - 0.5).astype(np.float32)   # tile row 0 north
    tx = ((lon - tw) / (te - tw) * wd - 0.5).astype(np.float32)
    my, mx = np.meshgrid(ty, tx, indexing="ij")
    return cv2.remap(dem, mx, my, cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


# --------------------------------------------------------------------------------------------
# The searches

class Channel:
    def __init__(self, name, plate_layer, image, cell, blocks, weight, care=None):
        self.name = name
        self.layer = plate_layer
        self.ref = Reference(image, cell, blocks, care)
        self.image = image
        self.weight = weight


def score_pose(channels, plate, cell, size, turn, n, inside):
    total = None
    per = {}
    pv = None
    for ch in channels:
        p, pv, _ = posed(ch.layer, plate.valid, plate.cell_m, cell, size, turn, n)
        z = zsurface(ch.ref, p, pv, inside)
        per[ch.name] = z
        total = ch.weight * z if total is None else total + ch.weight * z
    return total, per


def refine(plate, channels, cell, n, start_turn, start_size, reach_m=REFINE_REACH_M,
           turns=REFINE_TURNS, sizes=REFINE_SIZES):
    """Street-scale local search about the centre of an `n`-cell crop."""
    inside = disc((n, n), reach_m / cell)
    best = None
    by_size = {}
    at_start = None
    for size in np.asarray(sizes) * start_size:
        for turn in np.asarray(turns) + start_turn:
            total, per = score_pose(channels, plate, cell, float(size), float(turn), n, inside)
            at, score = argmax_shift(total)
            key = round(float(size), 4)
            by_size[key] = max(by_size.get(key, -np.inf), score)
            if abs(size - start_size) < 1e-6 and abs(turn - start_turn) < 1e-6:
                at_start = float(total[n // 2, n // 2])
            if best is None or score > best["score"]:
                best = {"score": score, "size": float(size), "turn": float(turn), "at": at,
                        "total": total,
                        "channels": {k: float(v[n // 2 + at[0], n // 2 + at[1]])
                                     for k, v in per.items()}}
    best["unique"] = best["score"] - runner_up(best["total"], best["at"], APART_M / cell)
    others = [v for k, v in by_size.items() if abs(k - best["size"]) > SIZE_APART * start_size]
    best["size_margin"] = best["score"] - max(others or [-np.inf])
    best["at_start"] = at_start
    del best["total"]
    return best


def density_search(plate, buildings, cell, n, start_turn, reach_m, extra=()):
    """Coarse and wide: where in the window is the plate's district, at the recorded heading.

    Only for a plate with no usable position.  Works at twice the map cell on merged footprints,
    plus any `extra` (name, plate layer, map image at `cell`, blocks, weight) channels: the river
    and, on a hill, the terrain.  Returns up to DENSITY_CANDIDATES poses at distinct places, best
    first; the street refine decides between them, because at district scale the right place and
    a look-alike district a kilometre off can score within a standard deviation of each other.
    """
    factor = 2
    cc = cell * factor
    m = n // factor

    def coarse(img):
        return cv2.resize(np.asarray(img, np.float32), (m, m), interpolation=cv2.INTER_AREA)

    chans = [(merged(plate.built, plate.cell_m, DENSITY_MERGE_M),
              Reference(coarse(merged(buildings, cell, DENSITY_MERGE_M)), cc, DENSITY_BLOCKS_M),
              1.0)]
    chans += [(layer, Reference(coarse(img), cc, blocks), weight)
              for _, layer, img, blocks, weight in extra]
    inside = disc((m, m), reach_m / cc)
    top = np.full((m, m), -np.inf, np.float32)
    pose = np.zeros((m, m), np.int32)
    poses = [(s, float(t)) for s in DENSITY_SIZES for t in DENSITY_TURNS + start_turn]
    for k, (size, turn) in enumerate(poses):
        total = None
        for layer, ref, weight in chans:
            p, pv, _ = posed(layer, plate.valid, plate.cell_m, cc, size, turn, m)
            z = weight * zsurface(ref, p, pv, inside)
            total = z if total is None else total + z
        better = total > top
        top[better] = total[better]
        pose[better] = k

    # Peaks of the best-over-poses surface, each at least half a plate from the others.
    apart = 0.5 * plate.span_m() / cc
    yy, xx = np.mgrid[:m, :m]
    work = np.where(inside, top, -np.inf)
    out = []
    while len(out) < DENSITY_CANDIDATES:
        y, x = np.unravel_index(int(np.argmax(work)), work.shape)
        score = float(work[y, x])
        if not np.isfinite(score) or (out and score < out[0]["score"] - DENSITY_WITHIN):
            break
        size, turn = poses[int(pose[y, x])]
        out.append({"score": score, "size": size, "turn": turn,
                    "at": (int(y - m // 2) * factor, int(x - m // 2) * factor)})
        work[(yy - y) ** 2 + (xx - x) ** 2 <= apart ** 2] = -np.inf
    rest = float(work.max()) if np.isfinite(work.max()) else out[0]["score"] - DENSITY_WITHIN
    for i, c in enumerate(out):
        c["unique"] = c["score"] - (out[i + 1]["score"] if i + 1 < len(out) else rest)
    return out


# --------------------------------------------------------------------------------------------
# One pack

def agreement(plate, cell, size, turn, n, shift, buildings):
    p, pv, _ = posed(plate.built, plate.valid, plate.cell_m, cell, size, turn, n)
    p = np.roll(np.roll(p, shift[0], 0), shift[1], 1)
    pv = np.roll(np.roll(pv, shift[0], 0), shift[1], 1)
    return float(((p > 0.5) == (buildings >= 0.5))[pv].mean())


def place(slug, hide_pose_m=0.0, channels=("buildings", "water", "terrain"), write=True):
    """Place one pack from `data/<slug>` and record it in `placement.json` beside it."""
    res = place_plate(Plate(slug), hide_pose_m, channels)
    if write and not hide_pose_m:
        (_paths.data_dir() / slug / "placement.json").write_text(json.dumps(res, indent=1))
    return res


def map_cell(plate):
    """8 m for a city plate; a monument plate (the Alhambra is 580 m) keeps ~250 cells across,
    or a 2.5% size step would be under two cells and the size could not be read."""
    span = plate.span_m()
    return TARGET_CELL_M if span >= SMALL_PLATE_M else max(2.5, span / 250.0)


def as_guess(res, meta, resolution=None):
    """A placement as the exporter's `pipeline_guess`: one matrix from plate pixels to OSM pixels.

    Both rasters are row 0 = south and `resolution` across; the OSM raster covers
    `osm_bbox_nsew`.  The plate is turned and scaled about its own centre exactly as the
    exporter's geometric guess is, then its centre is moved onto the placement's lat/lon.  A
    placement that is not `confident` (sure of the place, not of the size) keeps size 1.0.
    """
    if not res.get("confident", True):
        res = dict(res, size=1.0)
    res_px = int(resolution or meta.get("resolution") or 512)
    n, s, e, w = (float(v) for v in meta["osm_bbox_nsew"])
    gscale = float((meta.get("geometric_guess") or meta.get("pipeline_guess") or {})
                   .get("scale") or res.get("gscale") or 1.0 / 1.5)
    centre = (res_px / 2.0, res_px / 2.0)
    m = cv2.getRotationMatrix2D(centre, float(res["turn_deg"]), gscale * float(res["size"]))
    m[0, 2] += (float(res["lon"]) - w) / (e - w) * res_px - centre[0]
    m[1, 2] += (float(res["lat"]) - s) / (n - s) * res_px - centre[1]
    return {
        "matrix": m.tolist(),
        "scale": gscale * float(res["size"]),
        "rot_deg": float(res["turn_deg"]),
        "tx": float(m[0, 2]),
        "ty": float(m[1, 2]),
        "source": "street_place",
    }


def _channels(plate, bbox, n, cell, buildings, water, channels, notes, parks=None):
    """The refine's channels over one window: footprints (parks left out, see PARK_SELECTOR),
    the river if both sides have one, and the terrain where the DEM says the ground has shape.
    Returns them and the DEM's spread."""
    care = None if parks is None else parks < PARK_CARE_BELOW
    used = [Channel("buildings", plate.built, buildings, cell, (REFINE_BLOCK_M,), 1.0, care)]
    if water is not None:
        used.append(Channel("water", plate.water, water, cell, (WATER_BLOCK_M,), WATER_WEIGHT))
    dem_relief = None
    if "terrain" in channels:
        # Terrain is used where the ground itself has shape under the plate.  The plate's own
        # relief cannot decide that: its vertical scale is arbitrary and a dense district
        # survives any opening narrow enough to keep a hill.
        try:
            dem = map_terrain(bbox, n)
            q = np.percentile(dem[disc((n, n), 0.5 * plate.span_m() / cell)], [5, 95])
            dem_relief = float(q[1] - q[0])
            if dem_relief >= TERRAIN_MIN_RELIEF_M:
                used.append(Channel("terrain", plate.ground, dem, cell, (TERRAIN_BLOCK_M,),
                                    TERRAIN_WEIGHT))
        except Exception as exc:
            notes["terrain"] = f"{type(exc).__name__}: {exc}"
    return used, dem_relief


def _parks_or_none(bbox, n, notes):
    """The park map, or None (every cell cared for) if Overpass fails: parks refine the answer,
    they are not needed to find it."""
    try:
        return map_parks(bbox, n)
    except Exception as exc:
        notes["parks"] = f"{type(exc).__name__}: {exc}"
        return None


def place_plate(plate, hide_pose_m=0.0, channels=("buildings", "water", "terrain")):
    """Place one plate.  `hide_pose_m` > 0 moves the start that far off the pack's position and
    finds it again with the density search, the path a pack with no pose takes."""
    t0 = time.time()
    slug = plate.slug
    meta = plate.meta
    n0, s0, e0, w0 = (float(v) for v in meta["osm_bbox_nsew"])
    lat0, lon0 = 0.5 * (n0 + s0), 0.5 * (e0 + w0)
    cell = map_cell(plate)
    diag = plate.span_m() * max(REFINE_SIZES) * math.sqrt(2.0)

    start = (lat0, lon0)
    found = None
    notes = {}
    side = diag + 2 * REFINE_REACH_M + 64 * cell
    if hide_pose_m > 0:
        # A deterministic direction per slug, so a rerun is comparable.
        ang = (sum(map(ord, slug)) % 360) * math.pi / 180.0
        lat_s = lat0 + hide_pose_m * math.sin(ang) / M_PER_DEG_LAT
        lon_s = lon0 + hide_pose_m * math.cos(ang) / m_per_deg_lon(lat0)
        reach = hide_pose_m * 1.5 + 200.0
        big_bbox, big_n, _ = window(meta, diag + 2 * reach + side, cell, (lat_s, lon_s))
        big = map_buildings(big_bbox, big_n)
        big_parks = _parks_or_none(big_bbox, big_n, notes)
        inner = big_n - int(side / cell) - 2
        reach = min(reach, 0.5 * inner * cell)
        big_water = None
        extra = []
        if "water" in channels and plate.water is not None:
            try:
                big_water = map_water_bridges(big_bbox, big_n)
                extra.append(("water", plate.water, big_water, (2 * WATER_BLOCK_M,), WATER_WEIGHT))
            except Exception as exc:
                notes["water"] = f"{type(exc).__name__}: {exc}"
        if "terrain" in channels:
            try:
                wide = map_terrain_wide(big_bbox, big_n)
                q = np.percentile(wide[disc((big_n, big_n), reach / cell)], [5, 95])
                if q[1] - q[0] >= TERRAIN_MIN_RELIEF_M:
                    extra.append(("terrain", plate.ground, wide, (2 * TERRAIN_BLOCK_M,),
                                  TERRAIN_WEIGHT))
            except Exception as exc:
                notes["terrain_wide"] = f"{type(exc).__name__}: {exc}"
        cands = density_search(plate, big, cell, big_n, plate.rot_deg, reach, extra)

        # Each candidate's refinement window is cut out of the big one, cell-aligned, so nothing
        # is fetched twice and the start is exactly the cut's centre.
        n = int(math.ceil(side / cell / 2.0)) * 2
        nq = int(math.ceil((diag + 2 * QUICK_REACH_M + 64 * cell) / cell / 2.0)) * 2
        bn, bs, be, bw = big_bbox
        dlat, dlon = (bn - bs) / big_n, (be - bw) / big_n

        def cut(at, n=n):
            cy, cx = big_n // 2 + at[0], big_n // 2 + at[1]
            y0 = int(np.clip(cy - n // 2, 0, big_n - n))
            x0 = int(np.clip(cx - n // 2, 0, big_n - n))
            bbox = (bs + (y0 + n) * dlat, bs + y0 * dlat, bw + (x0 + n) * dlon, bw + x0 * dlon)
            water = None if big_water is None else big_water[y0:y0 + n, x0:x0 + n]
            parks = None if big_parks is None else big_parks[y0:y0 + n, x0:x0 + n]
            centre = (y0 + n // 2 - big_n // 2, x0 + n // 2 - big_n // 2)
            return bbox, big[y0:y0 + n, x0:x0 + n], water, parks, centre

        ranked = []
        for c in cands:
            # A density peak is a district, not a street: on a grid city it can sit 400-600 m
            # from the plate (Philadelphia Miniature 421 m, Valencia 625 m), past the full
            # refine's reach.  So the quick refine reaches further, and a quick refine that ends
            # near the edge of its reach is walking somewhere, so its window follows it.
            at = c["at"]
            for _ in range(1 + REFINE_HOPS):
                bbox, buildings, water, parks, centre = cut(at, nq)
                used, _ = _channels(plate, bbox, nq, cell, buildings, water, channels, notes,
                                    parks)
                quick = refine(plate, used, cell, nq, plate.rot_deg, 1.0, QUICK_REACH_M,
                               turns=QUICK_TURNS, sizes=QUICK_SIZES)
                walked = math.hypot(*quick["at"]) * cell
                at = (centre[0] + quick["at"][0], centre[1] + quick["at"][1])
                if walked < HOP_FRAC * QUICK_REACH_M:
                    break
            # The full refine starts from the cut's centre, so centre the cut on the answer.
            bbox, buildings, water, parks, _ = cut(at)
            used, dem_relief = _channels(plate, bbox, n, cell, buildings, water, channels, notes,
                                         parks)
            c = dict(c, at=tuple(int(v) for v in at))
            ranked.append((quick["score"], c, bbox, buildings, used, dem_relief))
        ranked.sort(key=lambda r: -r[0])
        # Two candidates that walked into the same place are one candidate, not a doubt.
        kept = []
        for r in ranked:
            if all(math.hypot(r[1]["at"][0] - k[1]["at"][0], r[1]["at"][1] - k[1]["at"][1])
                   * cell > APART_M for k in kept):
                kept.append(r)
        ranked = kept
        _, found, bbox, buildings, used, dem_relief = ranked[0]
        found = dict(found, candidates=[{"score": r[0], "density": r[1]["score"],
                                         "at": r[1]["at"]} for r in ranked])
        start = (0.5 * (bbox[0] + bbox[1]), 0.5 * (bbox[2] + bbox[3]))
    else:
        bbox, n, _ = window(meta, side, cell, start)
        buildings = map_buildings(bbox, n)
        water = None
        if "water" in channels and plate.water is not None:
            try:
                water = map_water_bridges(bbox, n)
            except Exception as exc:
                notes["water"] = f"{type(exc).__name__}: {exc}"
        parks = _parks_or_none(bbox, n, notes)
        used, dem_relief = _channels(plate, bbox, n, cell, buildings, water, channels, notes,
                                     parks)

    best = refine(plate, used, cell, n, plate.rot_deg, 1.0)
    if found is not None and len(found["candidates"]) > 1:
        # A second place that refines nearly as well is as much a doubt as a second peak nearby.
        c = found["candidates"]
        best["unique"] = min(best["unique"], c[0]["score"] - c[1]["score"])
    dy, dx = best["at"]
    lat = start[0] + dy * cell / M_PER_DEG_LAT
    lon = start[1] + dx * cell / m_per_deg_lon(lat0)
    sy = (start[0] - lat0) * M_PER_DEG_LAT / cell
    sx = (start[1] - lon0) * m_per_deg_lon(lat0) / cell
    move_m = math.hypot(sy + dy, sx + dx) * cell
    res = {
        "slug": slug, "city": meta.get("city", slug),
        "lat": lat, "lon": lon, "turn_deg": best["turn"] % 360.0,
        "recorded_turn_deg": plate.rot_deg,
        "size": best["size"], "span_m": plate.span_m() * best["size"],
        "moved_m": move_m, "score": best["score"], "unique": best["unique"],
        "size_margin": best["size_margin"], "at_start": best["at_start"],
        "channels": [c.name for c in used], "channel_z": best["channels"],
        "agree": agreement(plate, cell, best["size"], best["turn"], n, best["at"], buildings),
        "agree_pack": None if hide_pose_m else
        agreement(plate, cell, 1.0, plate.rot_deg, n, (0, 0), buildings),
        "confident": bool(best["unique"] >= CONFIDENT and best["size_margin"] >= CONFIDENT),
        # Sure of the place but not of the size: a small outline-cut plate (the Alhambra) has
        # too few streets across it to tell 2.5% steps apart.  `as_guess` then keeps the pack's
        # own scale, which the building solver measured, rather than the refine's weak choice.
        "position_confident": bool(best["unique"] >= CONFIDENT),
        "plate_relief_m": plate.relief_m, "dem_relief_m": dem_relief,
        "hidden_start_m": hide_pose_m or None,
        "density": None if found is None else
        {k: found[k] for k in ("score", "unique", "size", "turn", "candidates")},
        "notes": notes, "seconds": round(time.time() - t0, 1),
        "cell_m": cell, "gscale": plate.gscale,
    }
    return res


def _safe(args):
    slug, hide, chans = args
    try:
        return place(slug, hide, chans)
    except Exception as exc:
        import traceback
        return {"slug": slug, "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc()}


def packs():
    return sorted(d.name for d in _paths.data_dir().iterdir()
                  if (d / "stl_relief.npy").exists() and (d / "stl_residual.npy").exists())


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("slugs", nargs="*")
    ap.add_argument("--hide", type=float, default=0.0,
                    help="start this many metres off the pack's position and find it again")
    ap.add_argument("--channels", default="buildings,water,terrain")
    ap.add_argument("--procs", type=int, default=4)
    ap.add_argument("--out", help="write all results to this JSON file")
    a = ap.parse_args(argv)
    chans = tuple(a.channels.split(","))
    slugs = a.slugs or packs()
    print(f"{'city':<24}{'moved':>7}{'turn':>7}{'packT':>7}{'size':>6}{'score':>7}{'uniq':>6}"
          f"{'sizeM':>6}{'agree':>7}{'@pack':>7}  channels", flush=True)
    rows = []
    with mp.Pool(a.procs) as pool:
        for r in pool.imap_unordered(_safe, [(s, a.hide, chans) for s in slugs]):
            rows.append(r)
            if "error" in r:
                print(f"{r['slug']:<24} failed: {r['error']}\n{r['trace']}", flush=True)
                continue
            ap_ = "  -  " if r["agree_pack"] is None else f"{r['agree_pack']:7.3f}"
            cz = " ".join(f"{k}={v:.1f}" for k, v in r["channel_z"].items())
            print(f"{r['city']:<24}{r['moved_m']:6.0f}m{r['turn_deg']:7.1f}"
                  f"{r['recorded_turn_deg']:7.1f}{r['size']:6.3f}{r['score']:7.2f}"
                  f"{r['unique']:6.2f}{r['size_margin']:6.2f}{r['agree']:7.3f}{ap_}  {cz}"
                  f"  dem {r['dem_relief_m'] or 0:.0f}m"
                  f"{'  ' + str(r['notes']) if r['notes'] else ''}", flush=True)
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(rows, indent=1))


if __name__ == "__main__":
    main()
