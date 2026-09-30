"""Export per-city rasters + pipeline initial-guess transforms for the manual
drag-align tool (drag_align.html).

For each of the 8 micropolitan cities this runs the registration pipeline
(report writing suppressed) and saves, in the SAME pixel frame the pipeline
uses (resolution=512 output grid):

    data/<slug>/osm_buildings.png   OSM building heightmap, normalized grayscale
    data/<slug>/osm_water.png       OSM water mask (0/255)
    data/<slug>/sat.png             ESRI World Imagery for the same bbox, resampled
                                    to the OSM grid (so OSM and satellite share pixels)
    data/<slug>/stl_heightmap.png   Unaligned STL heightmap projection, normalized
    data/<slug>/stl_mask.png        stl_heightmap > 0 (0/255)
    data/<slug>/stl_water.png       River footprint cut out of the vendor's
                                    *_L_Water.stl companion plate (0/255)
    data/<slug>/meta.json           cell_size_m, osm_bbox, shapes, pipeline guess

plus a single data/align_data.js embedding every image as a base64 data URI so
drag_align.html works when opened directly from file:// (no server, no CORS).

The pipeline transform convention (see numpy2stl/registration/align/transform.py):
2x3 affine mapping STL source pixels -> OSM target pixels, applied with
cv2.warpAffine; rotation about the origin, translation in target pixels;
scale = hypot(M[0,0], M[1,0]), angle_deg = degrees(atan2(M[1,0], M[0,0])).

Run with the working venv:
    ~/.venvs/map2stl/Scripts/python.exe tools/align_tool/export_align_data.py
(from the Code/ directory so numpy2stl is importable)
"""
from __future__ import annotations

import base64
import json
import math
import os
import re
import sys
import time
import traceback

import numpy as np
import paths  # noqa: E402
import refine_guess

from city2stl.registration import street_place

ROOT = paths.CITIES / "micropolitan" / "_extracted"

# Packs come from more than one vendor and do not share a directory.  The micropolitan packs
# under ROOT are square 2 km plates with a water cut-out; the miniatures are a separate
# vendor's 170.5 by 119.5 mm plates with no water and no stated ground coverage.  A folder is
# looked up in each root in turn, so a pack can be named without also naming where it lives.
PACK_ROOTS = (
    ROOT,
    paths.CITIES / "miniatures",
    paths.CITIES / "granada_stl",
)

# Parts of a pack that are not the terrain plate: the printable frame, the engraved city
# name, and the flat water backing slab.
NON_PLATE = ("label", "frame", "water", "base", "logo")
HERE = paths.HERE
DATA = paths.DATA

# Both exported rasters are square and this wide.  Overridable so a resolution experiment
# can be run into a separate ALIGN_DATA_DIR without disturbing the exports the align tool
# and the evaluator read.  Everything sized in metres -- the top-hat kernel, the bridge
# buffers, the OSM coverage cut -- takes its pixel size from cell_size_m, so changing this
# changes the sampling and nothing else.
RESOLUTION = int(os.environ.get("ALIGN_RESOLUTION") or 512)


def plate_relief(rep):
    """The plate at three stages: as rendered, above local ground, and segmented.

    ``buildings`` is the raster the solver uses -- height above local ground, NaN where there
    is none.  ``absolute`` is the render it was derived from, terrain and structures together.
    ``residual`` is the terrain-subtracted height before the threshold, which is what
    ``buildings`` is a cut through.  ``threshold`` and ``label`` record where that cut fell.

    Two things stand between the registration report and a raster that means "building".

    The first is which render to take.  With ``simplify_mode="prism"`` the report's
    ``stl_heightmap`` is a re-render of the decimated prism model, whose ground is filled
    flat and whose NaNs are mapped to zero -- the streets that separate one block from the
    next are gone, and thresholding it marks four fifths of the plate as built.  The
    original render survives on the report as ``_stl_heightmap_original``.

    The second is the datum.  That original render is absolute model z, terrain and
    structures together, so on a plate with any relief a fixed cut-off follows the hillside
    rather than the buildings.  ``terrain_residual`` estimates the ground by a
    morphological opening sized in metres and returns the height each cell rises above it,
    which is the same physical quantity ``osm_buildings.npy`` already holds.

    The split between ground and building is three-class Otsu over a residual clipped at its
    85th percentile.  The triangle threshold that ``building_mask`` defaults to is tuned for
    one peak and a tail and reads far too low here, marking 0.76 of Barcelona and 0.81 of
    Prague against OSM's 0.46 and 0.43.  The clip is what makes Otsu safe on a skyline: the
    rule minimises within-class variance, so a long tail of towers buys a large reduction by
    claiming a class of its own and drags both boundaries up behind it.  On Miami that cuts
    at 1.17 m where every other city cuts between 0.28 and 0.67, and two thirds of the
    low-rise is called ground; clipping the tail first takes Miami from 74% of the best any
    threshold could do to 99%, and moves the cities that have no tail by at most two percent.
    """
    from numpy2stl.raster.segment import (
        _adaptive_residual_threshold,
        terrain_residual,
    )

    raw = getattr(rep, "_stl_heightmap_original", None)
    if raw is None:                      # no simplification ran; the report render is the render
        render = np.asarray(rep.stl_heightmap, dtype=np.float64)
        return {"buildings": render, "absolute": render, "residual": None,
                "threshold": None, "label": None}
    absolute = np.asarray(raw, dtype=np.float64)
    resid, valid = terrain_residual(absolute, cell_size_m=float(rep.cell_size_m))
    if not valid.any():
        render = np.asarray(rep.stl_heightmap, dtype=np.float64)
        return {"buildings": render, "absolute": absolute, "residual": None,
                "threshold": None, "label": None}
    thr, label = _adaptive_residual_threshold(resid[valid], "multiotsu_p85")
    out = np.where(valid & (resid > thr), resid, np.nan)
    print("    plate segmentation: %s thr=%.3f  coverage %.3f"
          % (label, thr, float(np.isfinite(out).mean())), flush=True)
    # The residual is kept with the plate's own NaN outside it, so that "no plate here" and
    # "plate here, but flat" stay distinguishable -- the segmented raster cannot tell them
    # apart, which is exactly what makes it unable to describe the terrain.
    return {"buildings": out,
            "absolute": np.where(valid, absolute, np.nan),
            "residual": np.where(valid, resid, np.nan),
            "threshold": float(thr), "label": label}


def plate_building_heights(rep):
    """The segmented buildings raster alone, for callers that want nothing else."""
    return plate_relief(rep)["buildings"]

# How many times to retry a city whose OSM building fetch failed, and how
# long to wait in between.
REGISTER_ATTEMPTS = 3
REGISTER_RETRY_S = 20.0

# The same, for the drag tool's water overlay when a selector comes back missing.
WATER_ATTEMPTS = 2
WATER_RETRY_S = 15.0

# The vendor's "Instructions and Framing" PDF states that the LARGE plate covers
# roughly 2 km in length and width.  That is the only external scale anchor we
# have, and it beats the pipeline's own guess by a wide margin: the pipeline
# estimates metres-per-unit as tallest_m / stl_z_max, but stl_z_max is base plate
# plus terrain plus buildings, so any city with a thick base or real relief comes
# out far too small.  Measured coverage under that estimate ranged from 386 m
# (Salzburg) to 3245 m (Paris) against a ~2 km frame, which is exactly why the
# fetched OSM and satellite windows showed the wrong ground.
#
# Deriving the scale from the plate's own XY extent instead means new cities need
# no hand-tuned constant.  For seven of the eight cities this lands within about
# 11% of a flat 19.7 m/unit; Salzburg is the outlier at 25.1 because its plate is
# 79.6 units wide where the others are 101-112.  Salzburg is also the one city
# whose STL mask floods with hillside, so registration cannot arbitrate it --
# confirm it by hand in the align tool with the scale checkbox unlocked, and add
# an entry to PLATE_COVERAGE_OVERRIDE_M here if 2 km turns out to be wrong.
PLATE_COVERAGE_M = 2000.0
PLATE_COVERAGE_OVERRIDE_M: dict[str, float] = {
    # A monument, not a city block, and measured rather than estimated.  The plate does not
    # reach the far end of the Generalife as its outline suggests; it is cut short of it.
    # Sweeping span against rotation over a 2 km window at under 4 m to the cell puts thirty
    # two results on the same hill within twelve metres of each other, and the span they
    # agree on is 592 m.  The overlay confirms it: the Alcazaba sits at the west tip and
    # Charles V's circular courtyard reads as a hole in the right place.
    "Alhambra": 592.0,
}

# Centre of the ground the vendor plate actually shows, as (lat, lon).
#
# Without one, estimate_bbox_from_stl geocodes the city name and centres the OSM
# window on the administrative centroid, which for most of these plates is
# kilometres away from the framed area -- the fetched raster then contains none
# of the ground the plate depicts, so no alignment exists to be found. Small
# cities (Bilbao, Salzburg) happen to work because centroid and frame coincide.
#
# Fill these in from the align tool: re-fetch with an explicit centre until the
# reference window matches the plate, then copy the "window centre" readout here
# so the next export is right without any hand-holding.
PLATE_CENTER: dict[str, tuple[float, float]] = {}

CITIES = {
    "Barcelona": ("Barcelona, Spain",       "Barcelona,_Spain_-_S,_M,_L,_&_XL",       144.0),
    "Bilbao":    ("Bilbao, Spain",          "Bilbao,_Spain_-_S,_M,_L,_&_XL",          165.0),
    "Lisbon":    ("Lisbon, Portugal",       "Lisbon,_Portugal_-_S,_M,_L,_&_XL",       145.0),
    "Miami":     ("Miami, FL, USA",         "Miami,_FL_-_L_&_XL",                     256.0),
    "Paris":     ("Paris, France",          "Paris,_France_-_S,_M,_L,_&_XL",          210.0),
    "Prague":    ("Prague, Czech Republic", "Prague,_Czech_Republic_-_S,_M,_L,_&_XL", 109.0),
    "Salzburg":  ("Salzburg, Austria",      "Salzburg,_Austria_-_S,_M,_L_&_XL",        60.0),
    "Valencia":  ("Valencia, Spain",        "Valencia,_Spain_-_S,_M,_L,_&_XL",         96.0),
    # The first pack with no water plate.  It is located from its buildings instead, by
    # locate_buildings, which also measures the span its plate really covers -- about
    # 1560 m rather than the 2000 every other plate assumes.
    "Philadelphia": ("Philadelphia, Pennsylvania, USA", "Philadelphia  PA - L   XL", 342.0),
    # The miniatures, a different vendor entirely.  Their plates are 170.5 by 119.5 mm rather
    # than square, they ship no water cut-out, and nothing in the packs states what ground
    # they cover -- so their span is measured by locate_buildings rather than assumed.
    "Boston Miniature": ("Boston, Massachusetts, USA",
                         "Boston Massachusetts 3D Miniature - 7184144", 240.0),
    "Denver Miniature": ("Denver, Colorado, USA",
                         "Denver Colorado 3D Miniature - 7029832", 217.0),
    "Paris Miniature": ("Paris, France",
                        "Paris France 3D Miniature - 6992260", 210.0),
    "Philadelphia Miniature": ("Philadelphia, Pennsylvania, USA",
                               "Philadelphia Pennsylvania 3D Miniature - 6932766", 342.0),
    # A monument rather than a city: the walls, palaces and gardens of the Alhambra, cut to
    # their own outline with no surrounding streets and no rectangular border.  An eighth of
    # its raster is occupied, against roughly a half for a city plate, and what is occupied
    # runs as one diagonal band, so most crops of it are empty.
    #
    # The region names the monument rather than the city because that is what the plate
    # shows.  For a city pack the distinction does not arise, but here it decides the seed:
    # "Granada, Spain" geocodes to the cathedral, 991 m from the plate, while this puts the
    # search within eleven metres of it.
    "Alhambra": ("Alhambra, Granada, Spain", "Alhambra - 5141408", 27.0),
}

# Two packs can depict the same place -- Paris and Philadelphia each have both a micropolitan
# plate and a miniature -- and the slug is what names a plate's cache entries, its exported
# rasters and its hand alignment.  Deriving it from the region alone would make those two
# pairs share a slug and overwrite each other, so a pack that collides names its own.
SLUGS = {
    "Paris Miniature": "paris_france_miniature",
    "Philadelphia Miniature": "philadelphia_pennsylvania_usa_miniature",
    # Not a collision but a rename: the Alhambra's region was "Granada, Spain" when its
    # rasters and hand alignment were first written, and naming the pack after the monument
    # improved the geocoder seed without changing which plate it is.  Pinning the slug keeps
    # what is already on disk addressable.
    "Alhambra": "granada_spain",
}

# Packs whose ground coverage is not stated anywhere and cannot be assumed.  For these the
# span is measured first, by a wide sweep over a factor of six, rather than nudged within the
# 1560 to 2400 m the ordinary sweep reaches around `PLATE_COVERAGE_M`.
COVERAGE_UNKNOWN = {
    "Boston Miniature", "Denver Miniature", "Paris Miniature", "Philadelphia Miniature",
}


def slug(region: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", region.lower()).strip("_")


def pack_dir(folder: str):
    """Where a pack lives, given only its folder name.

    Returns None rather than a non-existent path, so a pack named in `CITIES` but not yet
    extracted is reported as missing instead of failing later with an empty glob.
    """
    for root in PACK_ROOTS:
        d = root / folder
        if d.is_dir():
            return d
    return None


def find_stl(folder: str):
    """The pack's L plate, whatever the vendor happened to ship it as.

    Packs are not uniform.  Most carry `*_L_Solid.stl`; Philadelphia's L plate is a `.3mf`
    and its XL is four quadrant tiles.  `mesh_to_heightmap` reads STL, OBJ and 3MF alike, so
    the only thing that has to know the difference is this lookup.
    """
    d = pack_dir(folder)
    if d is None:
        return None
    for pat in ("*_L_Solid.stl", "*_L.stl", "*_L_Solid_A1.stl", "*_L_Solid.3mf", "*_L.3mf"):
        hits = sorted(d.glob(pat))
        if hits:
            return hits[0]
    # The miniatures name the plate after the city -- Boston.stl, Philadelphia_v2.stl -- with
    # no size letter to match on, so there is no pattern that covers them.  What is reliable
    # is bulk: the plate is a terrain mesh of tens of megabytes and everything else in the
    # folder is a label, a frame or the water slab, none of which reaches two.
    meshes = [f for f in sorted(d.rglob("*.stl"))
              if not any(w in f.stem.lower() for w in NON_PLATE)]
    if meshes:
        return max(meshes, key=lambda f: f.stat().st_size)
    return None


def find_water_stl(folder: str):
    """The vendor's *_L_Water.stl companion to the solid plate, if present.

    Four packs ship none, and one -- the miniatures' `Water_v2.stl` -- is a twelve-triangle
    flat backing slab rather than a cut shape, so it is deliberately not matched here.  A
    missing water plate is not an error: `locate.resolve_center` falls through to
    `locate_buildings`, which needs only the solid plate.
    """
    d = pack_dir(folder)
    if d is None:
        return None
    hits = sorted(d.rglob("*_L_Water.stl"))
    return hits[0] if hits else None


def plate_river_mask(water_path, solid_path, shape_hw: tuple[int, int]) -> np.ndarray:
    """Water footprint of the plate, as a 0/255 mask in the solid plate's frame.

    *_L_Water.stl is the same plate with the water cut out of it, and its XY
    bounds are byte-identical to *_L_Solid.stl, so the two share a pixel frame
    exactly.  Rasterising it leaves empty cells -- NaN -- precisely where the
    water is.  That makes it a sparse, highly distinctive registration cue that
    pairs one-to-one with the OSM water mask, unlike the STL height mask, which
    is terrain plus buildings and so covers most of the plate.

    The solid plate is differenced out because an isotropic raster centre-pads a
    non-square plate with NaN too, and that padding is otherwise indistinguishable
    from water -- it gave Lisbon a straight full-width bar of false water along
    one edge.  `locate.plate_water_mask` is the single implementation of that
    rule; this wrapper only reshapes its result to the caller's frame.
    """
    import cv2
    import locate

    mask = (locate.plate_water_mask(water_path, solid_path,
                                    resolution=RESOLUTION) * 255).astype(np.uint8)
    h, w = shape_hw
    if mask.shape[:2] != (h, w):
        mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)
    return mask


def plate_scale_m_per_unit(name: str, stl_path) -> float:
    """Metres of ground per mesh unit, from the plate's XY extent.

    Reads the mesh bounds directly rather than going through the heightmap so
    the value is available before registration runs and can be passed into it.
    """
    import trimesh

    m = trimesh.load(str(stl_path), force="mesh")
    lo, hi = m.bounds
    extent_u = max(float(hi[0] - lo[0]), float(hi[1] - lo[1]))
    if extent_u <= 0:
        raise RuntimeError(f"{name}: degenerate STL XY extent")
    coverage = PLATE_COVERAGE_OVERRIDE_M.get(name, PLATE_COVERAGE_M)
    return coverage / extent_u


def norm_u8(hm: np.ndarray) -> np.ndarray:
    """NaN-safe percentile normalization to uint8 for display."""
    a = np.nan_to_num(np.asarray(hm, dtype=np.float64), nan=0.0)
    a = np.clip(a, 0, None)
    hi = np.percentile(a[a > 0], 99) if np.any(a > 0) else 1.0
    if hi <= 0:
        hi = 1.0
    return np.clip(a / hi * 255.0, 0, 255).astype(np.uint8)


def png_bytes(img: np.ndarray) -> bytes:
    import cv2
    ok, buf = cv2.imencode(".png", img)
    if not ok:
        raise RuntimeError("PNG encode failed")
    return buf.tobytes()


def data_uri(img: np.ndarray) -> str:
    return "data:image/png;base64," + base64.b64encode(png_bytes(img)).decode("ascii")


def semantic_exclusion(bbox_nsew, matrix, shape_hw):
    """Cells the plate calls a building that OSM files under some other layer.

    The top-hat measures height above local ground, and a wooded slope, a motorway
    viaduct and an apartment block all rise above local ground.  OSM knows which is
    which, but not in its building layer -- a forest is `landuse`, a bridge deck is a
    `highway` with `bridge=yes` -- so the plate has no way to tell them apart on height
    alone and calls all three built.

    Measured against the exported masks, vegetation alone covers 65% of Salzburg's false
    positives, 41% of Prague's and 20-29% of everywhere else; elevated roadways cover 20%
    of Miami's.  Removing both lifts mask IoU from 0.590 to 0.645 as a mean over the eight
    cities, at the cost of 1.2-4.7% of true building area -- the buildings that genuinely
    stand inside a park or under a flyover.

    The exclusion is deliberately not guarded against OSM's own building layer, which
    would drop that cost to nearly zero.  Guarding would define the plate's mask partly by
    the answer it is about to be scored against, which inflates the correlation the
    refinement maximises and contaminates the raster as a segmentation target.  Land cover
    is legitimate side information; the footprints are not.

    `matrix` maps plate pixels to OSM pixels, so it is inverted to bring the OSM-frame
    masks back the other way.  It has to be the placement that is actually being exported,
    which is why this runs after refinement rather than before it.
    """
    import cv2

    from city2stl.osm_raster import get_osm_semantic_masks

    sem = get_osm_semantic_masks(bbox_nsew, resolution=RESOLUTION)
    src = (np.asarray(sem["vegetation"], dtype=bool)
           | np.asarray(sem["elevated_roadway"], dtype=bool))
    if not src.any():
        return np.zeros(shape_hw, dtype=bool)

    inv = cv2.invertAffineTransform(np.asarray(matrix, dtype=np.float64))
    warped = cv2.warpAffine(src.astype(np.float32), inv, (shape_hw[1], shape_hw[0]),
                            flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_CONSTANT,
                            borderValue=0.0)
    return warped >= 0.5


def fetch_sat(bbox_nsew, shape_hw: tuple[int, int]) -> np.ndarray:
    """ESRI World Imagery for the bbox, resampled onto the OSM pixel grid.

    fetch_satellite_tiles returns an aspect-preserving base64 JPEG of the bbox
    (already de-projected from Web Mercator to equirectangular) as a normal
    image: row 0 is the NORTH edge. The OSM and STL rasters use the opposite
    convention -- row 0 = south -- set by mesh_to_heightmap and matched by the
    np.flipud in cities.py's _rasterize_rasterio. So the satellite has to be
    flipped vertically before it shares a pixel frame with them; without the
    flip it is mirrored north-south against every other layer in the tool.
    Needs no API key or Earth Engine credentials.
    """
    import cv2

    from geo2stl.sat2stl import fetch_satellite_tiles

    north, south, east, west = (float(v) for v in bbox_nsew)
    b64 = fetch_satellite_tiles(north, south, east, west, dim=max(shape_hw))
    raw = np.frombuffer(base64.b64decode(b64), dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        raise RuntimeError("satellite JPEG decode failed")
    h, w = shape_hw
    img = cv2.resize(img, (w, h), interpolation=cv2.INTER_AREA)
    return np.ascontiguousarray(img[::-1])


def main(only: list[str] | None = None):
    import locate

    from city2stl.osm_raster import get_osm_semantic_masks
    from city2stl.registration import register_city_stl

    # register_city_stl fetches its buildings through osmnx, whose default
    # Overpass host is unreachable from this machine often enough to lose a
    # city per run.  Repoint it at a mirror that answers before the loop starts.
    locate.tune_osmnx()

    DATA.mkdir(parents=True, exist_ok=True)
    all_data = {}

    cities = CITIES
    if only:
        want = {c.lower() for c in only}
        cities = {k: v for k, v in CITIES.items() if k.lower() in want}
        missing = want - {k.lower() for k in cities}
        if missing:
            print(f"unknown city names ignored: {sorted(missing)}", flush=True)

    for name, (region, folder, tall) in cities.items():
        stl = find_stl(folder)
        if stl is None:
            print(f"{name}: STL NOT FOUND ({folder})", flush=True)
            continue
        t0 = time.time()
        try:
            scale_m_per_unit = plate_scale_m_per_unit(name, stl)
            print(f"{name}: scale {scale_m_per_unit:.2f} m/unit "
                  f"(plate covers {PLATE_COVERAGE_OVERRIDE_M.get(name, PLATE_COVERAGE_M):.0f} m)",
                  flush=True)
            center = PLATE_CENTER.get(name)
            if center is None:
                # locate.py resolves the centre from a saved hand alignment, a
                # cached solve, or -- for a plate seen for the first time -- by
                # correlating its water cut-out against OSM water.  Returning
                # None leaves the pipeline on the geocoded centroid as before.
                # The solid STL goes in alongside the water one so the solver can
                # tell the plate's water apart from the NaN padding an isotropic
                # raster adds around a non-square plate.
                center = locate.resolve_center(
                    region,
                    water_stl=find_water_stl(folder),
                    solid_stl=stl,
                    span_m=locate.plate_span_m(stl, scale_m_per_unit),
                    plate_key=SLUGS.get(name),
                    span_known=name not in COVERAGE_UNKNOWN,
                )

            # A hand alignment records the span the plate was actually drawn at,
            # which is not always the nominal coverage -- Salzburg's L plate
            # covers 1651 m, not 2000 -- and getting it wrong scales the whole
            # OSM window against the plate.  Where that figure exists, it wins.
            nominal = PLATE_COVERAGE_OVERRIDE_M.get(name, PLATE_COVERAGE_M)
            span = locate.measured_span_m(region, plate_key=SLUGS.get(name))
            if span is not None and abs(span / nominal - 1.0) > 0.005:
                scale_m_per_unit *= span / nominal
                print(f"{name}: span corrected to {span:.0f} m from {nominal:.0f} m "
                      f"(scale {scale_m_per_unit:.2f} m/unit)", flush=True)
            if center is not None:
                print(f"{name}: centre {center[0]:.5f}, {center[1]:.5f}", flush=True)
            # The building fetch is the one step here that can fail for a
            # reason that has nothing to do with this city -- a mirror going
            # away mid-run.  Retrying costs a minute; not retrying costs the
            # city, and the run is long enough that nobody notices until the
            # drag tool shows a stale export.
            rep = None
            for attempt in range(REGISTER_ATTEMPTS):
                try:
                    rep = register_city_stl(
                        stl_file=str(stl), city_name=region, resolution=RESOLUTION,
                        tallest_m=tall, simplify_mode="prism",
                        regularize_footprints=True,
                        scale_m_per_unit=scale_m_per_unit,
                        center=center,
                        out_dir=False,
                    )
                    break
                except Exception as exc:
                    if attempt + 1 >= REGISTER_ATTEMPTS:
                        raise
                    print(f"{name}: register attempt {attempt + 1} failed ({exc}); "
                          f"retrying", flush=True)
                    locate.tune_osmnx(force=True)
                    time.sleep(REGISTER_RETRY_S)
        except Exception as exc:
            traceback.print_exc()
            print(f"{name}: FAILED: {exc}", flush=True)
            continue

        # Two plates can share a region -- Paris and the Paris miniature, Philadelphia and
        # its own -- and the slug names the export directory as well as the key in
        # align_data.js, so deriving it from the region alone would have the miniature
        # overwrite the pack it is a miniature of.  `SLUGS` is the same override the span
        # and rotation lookups use, so all three agree on which plate is being talked about.
        s = SLUGS.get(name) or slug(region)
        out = DATA / s
        out.mkdir(parents=True, exist_ok=True)

        # Keep the raw arrays WITH their NaNs for the .npy exports: NaN is the
        # "no building here" sentinel that building_mask(source='osm') keys off
        # (mask = ~isnan). Flattening it to 0.0 makes every OSM cell a building,
        # which silently turns the mask/edge signals into a solid frame.
        relief = plate_relief(rep)
        stl_raw = relief["buildings"]
        osm_raw = np.asarray(rep.osm_heightmap, dtype=np.float64)
        stl_hm = np.nan_to_num(stl_raw, nan=0.0)
        osm_hm = np.nan_to_num(osm_raw, nan=0.0)

        osm_buildings = norm_u8(osm_hm)
        stl_heightmap = norm_u8(stl_hm)
        stl_mask = ((stl_hm > 0) * 255).astype(np.uint8)

        # The water reference the drag tool is aligned against comes from
        # locate.osm_water rather than get_osm_semantic_masks.  The latter never
        # asks for natural=coastline and keeps only Polygon rows, so for a
        # coastal plate it returns the harbour basins and leaves out the sea --
        # which is the one feature a human dragging Barcelona or Miami into
        # place has to line up against.  get_osm_semantic_masks stays as the
        # fallback for the case where the direct Overpass call fails outright.
        osm_water = np.zeros_like(osm_buildings)
        water_missing = ["never fetched"]
        for attempt in range(WATER_ATTEMPTS):
            try:
                # Three attempts inside locate, not the solver's six: this layer is
                # an overlay for a human, and a selector that has failed three times
                # is costing minutes per city for something the drag tool can manage
                # without.  The outer loop is a different question -- see below.
                water = locate.osm_water(rep.osm_bbox, RESOLUTION, attempts=3)
                got = ((np.clip(water["filled"], 0, 1)
                        + np.clip(water["outline"], 0, 1) > 0) * 255).astype(np.uint8)
                # Keep the fullest attempt rather than the last one.  Each retry asks
                # for every selector again, so a later attempt can come back thinner
                # than an earlier one when a different selector is the one to fail --
                # and the loop used to export whichever happened to be last.
                if float((got > 0).mean()) >= float((osm_water > 0).mean()):
                    osm_water = got
                    water_missing = list(water["missing"])
                if not water["missing"]:
                    break
                print(f"{name}: osm water layers missing: {water['missing']}", flush=True)
            except Exception as exc:
                print(f"{name}: direct water fetch failed ({exc})", flush=True)
            # A missing coastline selector is not a thin result, it is a hole: for a
            # coastal plate the sea is most of the mask, and the fraction check below
            # cannot see the difference between "this city has little water" and "the
            # ocean did not arrive".  Miami exported at 0.045 water that way, well
            # clear of the threshold and missing its entire sea.  Failed selectors are
            # never cached, so a second pass re-fetches only what is absent.
            if attempt + 1 < WATER_ATTEMPTS:
                time.sleep(WATER_RETRY_S)

        if float((osm_water > 0).mean()) < 0.005:
            try:
                sem = get_osm_semantic_masks(rep.osm_bbox, resolution=RESOLUTION)
                osm_water = (np.asarray(sem["water"], dtype=bool) * 255).astype(np.uint8)
            except Exception as exc:
                print(f"{name}: fallback water mask failed ({exc})", flush=True)
        frac = float((osm_water > 0).mean())
        if frac < 0.005:
            print(f"{name}: osm_water only {frac:.3f} full", flush=True)

        # Coverage of both OSM channels, recorded whether or not anything went wrong, so
        # two exports of the same city can be compared afterwards.  A partial Overpass
        # answer is the failure mode that hides best: the fetch returns, the raster looks
        # plausible, refinement quietly correlates against a river with half of it absent,
        # and the only trace is a log line nobody reads.  Paris re-exported that way with
        # water at 0.066 against its usual 0.131 and its correlation fell from 0.58 to
        # 0.27, which read as a resolution effect until these numbers were compared.
        osm_building_coverage = float((~np.isnan(osm_raw)).mean())
        if water_missing:
            print(f"{name}: WARNING exporting with incomplete water -- selectors "
                  f"{water_missing} never answered, mask is {frac:.3f} full. The "
                  f"placement below is fitted against a partial river.", flush=True)

        stl_water = np.zeros_like(stl_mask)
        water_stl = find_water_stl(folder)
        if water_stl is None:
            print(f"{name}: no *_L_Water.stl found in {folder}", flush=True)
        else:
            try:
                stl_water = plate_river_mask(water_stl, stl, stl_hm.shape[:2])
                print(f"{name}: plate river covers {float((stl_water > 0).mean()):.3f}", flush=True)
            except Exception as exc:
                print(f"{name}: plate river mask failed ({exc})", flush=True)

        try:
            sat = fetch_sat(rep.osm_bbox, osm_buildings.shape[:2])
        except Exception as exc:
            print(f"{name}: satellite fetch failed ({exc}); using black image", flush=True)
            sat = np.zeros((*osm_buildings.shape[:2], 3), dtype=np.uint8)

        # The transform the drag tool opens with is geometric, not the one the
        # registration backend returned.  Both rasters are RESOLUTION pixels
        # wide; the STL one covers the plate's own span and the OSM one covers
        # `cell_size_m * RESOLUTION` of ground centred on the same point, so
        # placing the plate is arithmetic: shrink by the ratio of the two ground
        # widths and centre it.  There is nothing left to search for -- the
        # centre came from `locate`, which solved it against OSM water under a
        # confidence gate, and the window was built around that centre.
        #
        # `rep.registration.transform` was measured against all eight plates and
        # is worse than this on every one of them, by water IoU and by building
        # correlation inside the plate footprint: Lisbon 0.375 -> 0.013 (it
        # invents a 26.6-degree rotation, which is what made the plate look
        # flipped), Prague 0.138 -> 0.000, Paris 0.186 -> 0.011, Barcelona
        # 0.234 -> 0.084.  Where it agrees with geometry -- Bilbao, Miami -- it
        # scores the same, so nothing is given up by dropping it.  It is kept in
        # the metadata as `register_transform` for comparison, and
        # `eval_registration.py` can still read either.
        # The scale is the pipeline's own geometric anchor: the OSM frame is
        # osm_margin times the plate's footprint and both are rendered at the
        # same resolution, so the plate fills exactly 1/osm_margin of the frame.
        # Re-deriving it from the mesh extent instead lands 0.3% short, because
        # the frame is built from the span the pipeline computed, not from a
        # second reading of the same bounds.
        gscale = float(rep.known_scale) if rep.known_scale else 1.0 / 1.5
        offset = (RESOLUTION - RESOLUTION * gscale) / 2.0

        # Scale and translation are not always enough.  A plate drawn square to north does
        # not sit square with a city whose street grid is not, and Philadelphia's is 9.25
        # degrees off north, so its miniature needs turning by nine degrees before any of
        # this arithmetic means anything.  The building solver already measures that angle
        # and has always thrown it away here; `measured_rotation_deg` hands it back as the
        # angle to turn the plate BY, so the solver's sign convention stays where it belongs.
        #
        # Only plates the building solver placed have an angle.  Every water-solved plate
        # gets None, falls to zero, and keeps the matrix it has always had: at zero degrees
        # `getRotationMatrix2D` about `RESOLUTION / 2` reduces to exactly the scale-and-
        # centre form written out below it.
        import cv2
        import locate as _locate
        rot_deg = _locate.measured_rotation_deg(region, SLUGS.get(name)) or 0.0
        centre = (RESOLUTION / 2.0, RESOLUTION / 2.0)
        rot_M = cv2.getRotationMatrix2D(centre, rot_deg, 1.0)
        guess_M = cv2.getRotationMatrix2D(centre, rot_deg, gscale)
        if rot_deg:
            print(f"{name}: plate needs turning {rot_deg:+.2f} deg to sit square with the "
                  f"city", flush=True)
        guess = {
            "matrix": guess_M.tolist(),
            "scale": gscale,
            "rot_deg": float(rot_deg),
            "tx": float(guess_M[0, 2]),
            "ty": float(guess_M[1, 2]),
            "source": "geometric",
        }
        M = np.asarray(rep.registration.transform, dtype=np.float64)
        register_transform = {
            "matrix": M.tolist(),
            "scale": float(math.hypot(M[0, 0], M[1, 0])),
            "rot_deg": float(math.degrees(math.atan2(M[1, 0], M[0, 0]))),
            "tx": float(M[0, 2]),
            "ty": float(M[1, 2]),
        }
        drift_px = math.hypot(register_transform["tx"] - offset,
                              register_transform["ty"] - offset)
        print(f"{name}: register transform sits {drift_px * rep.cell_size_m:.0f} m "
              f"from the geometric placement "
              f"(rot {register_transform['rot_deg']:.2f} deg)", flush=True)
        meta = {
            "city": name,
            "region": region,
            "slug": s,
            "tallest_m": tall,
            "resolution": RESOLUTION,
            "cell_size_m": float(rep.cell_size_m),
            "osm_bbox_nsew": list(rep.osm_bbox),
            "stl_shape": list(stl_hm.shape),
            "stl_water_frac": float((stl_water > 0).mean()),
            "osm_water_frac": float((osm_water > 0).mean()),
            "osm_shape": list(osm_hm.shape),
            "pipeline_guess": guess,
            "register_transform": register_transform,
        }
        # Recorded whether or not anything went wrong, so two exports of the same city can
        # be compared afterwards.  `osm_water_frac` was already here; the building coverage
        # was not, and it is the channel that carries the registration.  See the fetch above.
        meta["osm_building_coverage"] = osm_building_coverage
        if water_missing:
            meta["osm_water_missing"] = water_missing

        # Geometry gets the plate to the right place; the images say how far it still is.
        # `refine_guess` correlates the plate's own footprint against the OSM buildings and
        # corrects the couple of pixels the centre solve leaves behind -- and, for a plate
        # whose span nobody has measured, the span too, which on Prague and Valencia is
        # worth 8-10% and dwarfs the translation.  It only accepts a result that clears its
        # own confidence gates; otherwise the geometric placement stands untouched.
        # Refinement searches translation and scale only, so it has to be handed a plate
        # that is already turned; correlating the untuned raster would measure the angle as
        # a translation and drag the plate sideways to hide it.  Both channels are turned
        # by the same matrix the guess was built from, and the result comes back expressed
        # in that turned frame -- see the composition after the gate.
        # The turn goes on the raw plate, before `height_channel`, not after.  The raw
        # raster carries NaN to mean "no building here", which is what the corners a
        # rotation empties out should say; `height_channel` promises a finite 0..1 field,
        # and turning its output puts NaN back into a channel the correlator is entitled to
        # assume has none.  Doing it the wrong way round drives r to -0.06 and peak z to
        # zero on a plate that scores 0.79 and 8.0 the right way round.
        stl_turned = stl_raw
        stl_w = (np.asarray(stl_water) > 0).astype(np.float32)
        if rot_deg:
            wh = (stl_raw.shape[1], stl_raw.shape[0])
            stl_turned = cv2.warpAffine(stl_raw.astype(np.float32), rot_M, wh,
                                        flags=cv2.INTER_LINEAR,
                                        borderMode=cv2.BORDER_CONSTANT,
                                        borderValue=float("nan"))
            stl_w = cv2.warpAffine(stl_w, rot_M, wh, flags=cv2.INTER_NEAREST,
                                   borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
        stl_h = refine_guess.height_channel(stl_turned)
        try:
            fit = refine_guess.refine_arrays(
                stl_h,
                refine_guess.height_channel(osm_raw),
                stl_w,
                (np.asarray(osm_water) > 127).astype(np.float32),
                gscale, refine_guess.span_is_measured(meta),
                cell_size_m=float(rep.cell_size_m))
        except Exception as exc:
            print(f"{name}: refinement failed ({exc}); keeping the geometric placement",
                  flush=True)
            fit = None

        if fit is not None and fit["accepted"]:
            meta["geometric_guess"] = guess
            guess = refine_guess.as_guess(fit)
            if rot_deg:
                # `refine_arrays` measured its correction against the turned plate, so its
                # matrix maps turned-plate pixels to OSM pixels.  The drag tool is given the
                # plate as it was rendered, so the turn has to be folded back in: composing
                # the two gives one matrix from the plate's own pixels straight to OSM.
                S = np.vstack([np.asarray(guess["matrix"], dtype=np.float64),
                               (0.0, 0.0, 1.0)])
                R = np.vstack([rot_M, (0.0, 0.0, 1.0)])
                composed = (S @ R)[:2]
                guess["matrix"] = composed.tolist()
                guess["rot_deg"] = float(rot_deg)
                guess["tx"] = float(composed[0, 2])
                guess["ty"] = float(composed[1, 2])
            meta["pipeline_guess"] = guess
            meta["refinement"] = {k: v for k, v in fit.items() if k != "matrix"}
            if fit.get("span_m"):
                meta["refined_span_m"] = fit["span_m"]
                print(f"{name}: plate reads {fit['span_m']:.0f} m across, against the "
                      f"nominal {fit['span_m'] / fit['span_factor']:.0f} m", flush=True)
            print(f"{name}: refinement moved the plate {fit['shift_m']:.0f} m "
                  f"(r {fit['r']:.3f}, peak z {fit['peak_z']:.1f})", flush=True)
        elif fit is not None:
            print(f"{name}: kept the geometric placement -- {'; '.join(fit['reasons'])}",
                  flush=True)

        # Street placement has the last word when it is sure.  `refine_guess` correlates
        # density-scale footprints over the whole frame, which on a grid city aliases one block
        # for the next; `street_place` compares raw footprints (plus water with its bridges cut,
        # and terrain where the ground has shape) at 8 m, locally, about the geometric pose.
        # See docs/decisions/registration-refinement.md, 2026-09-11 (grid cities are placed at
        # street scale).  STREET_PLACE=0 turns it off.
        if os.environ.get("STREET_PLACE", "1") != "0" and relief["residual"] is not None:
            meta.setdefault("geometric_guess", guess)
            try:
                plate = street_place.Plate(
                    meta=meta, relief=relief["absolute"], residual=relief["residual"],
                    water=stl_water, gscale=gscale, rot_deg=rot_deg)
                placed = street_place.place_plate(plate)
            except Exception as exc:  # noqa: BLE001 -- a failed placement keeps the pose above
                # Print the traceback: this used to hide an ImportError that left street
                # placement silently off.
                print(f"{name}: street placement failed ({exc!r}); keeping the placement above",
                      flush=True)
                traceback.print_exc()
                placed = None
            if placed is not None:
                meta["street_placement"] = placed
                (out / "placement.json").write_text(json.dumps(placed, indent=1))
                verdict = (f"moved {placed['moved_m']:.0f} m, turn {placed['turn_deg']:.1f}, "
                           f"size {placed['size']:.3f}, unique {placed['unique']:.2f}, "
                           f"size margin {placed['size_margin']:.2f}")
                if placed["confident"] or placed.get("position_confident"):
                    # A size that is not confident is left at the pack's own (as_guess).
                    guess = street_place.as_guess(placed, meta, RESOLUTION)
                    meta["pipeline_guess"] = guess
                    print(f"{name}: street placement {verdict}"
                          f"{'' if placed['confident'] else ' (size kept at the pack scale)'}",
                          flush=True)
                else:
                    print(f"{name}: street placement not confident ({verdict}); "
                          f"keeping the placement above", flush=True)

        # The placement is settled, so the semantic layers can be brought into the plate
        # frame and the ground cover taken out of its building mask.  Refinement is NOT run
        # again on the cleaned raster.  It was, on the theory that the first pass had to
        # find the plate through the trees and a mask that no longer calls a hillside built
        # would be an easier correlation; measured over all eight cities the second pass
        # moves IoU by at most 0.001, and never upwards.  The placement was already
        # converged, so cleaning the mask changes what it scores, not where it sits.
        try:
            drop = semantic_exclusion(rep.osm_bbox, guess["matrix"], stl_raw.shape)
        except Exception as exc:
            print(f"{name}: semantic exclusion failed ({exc}); keeping the raw mask",
                  flush=True)
            drop = None

        if drop is not None and drop.any():
            before = float(np.isfinite(stl_raw).mean())
            stl_raw = np.where(drop, np.nan, stl_raw)
            after = float(np.isfinite(stl_raw).mean())
            print(f"{name}: vegetation and bridges took plate coverage "
                  f"{before:.3f} -> {after:.3f}", flush=True)
            meta["semantic_exclusion"] = {
                "coverage_before": before,
                "coverage_after": after,
                "excluded_frac": float(drop.mean()),
            }
            stl_hm = np.nan_to_num(stl_raw, nan=0.0)
            stl_heightmap = norm_u8(stl_hm)
            stl_mask = ((stl_hm > 0) * 255).astype(np.uint8)

        for fname, img in (("osm_buildings.png", osm_buildings),
                           ("osm_water.png", osm_water),
                           ("sat.png", sat),
                           ("stl_heightmap.png", stl_heightmap),
                           ("stl_mask.png", stl_mask),
                           ("stl_water.png", stl_water)):
            (out / fname).write_bytes(png_bytes(img))
        # Raw float heights alongside the display PNGs: server.py's /api/refine
        # feeds these to ECC, which should see real metres (and the NaN
        # no-building sentinel) rather than the percentile-normalized uint8
        # used for display.
        np.save(out / "stl_heightmap.npy", stl_raw.astype(np.float32))
        np.save(out / "osm_buildings.npy", osm_raw.astype(np.float32))
        # The plate before the segmentation took its terrain and its low relief away.  These
        # carry no vegetation exclusion: that cut answers a question about the OSM reference,
        # and these two rasters are meant to be the plate as it was rendered.
        np.save(out / "stl_relief.npy", relief["absolute"].astype(np.float32))
        if relief["residual"] is not None:
            np.save(out / "stl_residual.npy", relief["residual"].astype(np.float32))
        meta["plate_segmentation"] = {
            "threshold": relief["threshold"],
            "rule": relief["label"],
            "note": "stl_heightmap.npy is stl_residual.npy cut at this threshold",
        }
        (out / "stl_relief.png").write_bytes(
            png_bytes(norm_u8(np.nan_to_num(relief["absolute"], nan=0.0))))
        (out / "meta.json").write_text(json.dumps(meta, indent=2))

        all_data[s] = {
            "meta": meta,
            "images": {
                "osm_buildings": data_uri(osm_buildings),
                "osm_water": data_uri(osm_water),
                "sat": data_uri(sat),
                "stl_heightmap": data_uri(stl_heightmap),
                "stl_mask": data_uri(stl_mask),
                "stl_water": data_uri(stl_water),
            },
        }
        print(f"{name}: exported ({time.time()-t0:.0f}s, guess rot={guess['rot_deg']:.2f} "
              f"scale={guess['scale']:.3f} t=({guess['tx']:.1f},{guess['ty']:.1f}))", flush=True)

    # Merge into whatever align_data.js already holds rather than replacing it.
    # A subset run obviously must not drop the cities it did not rebuild, but a
    # full run must not either: a city whose OSM fetch failed is missing from
    # all_data, and overwriting would delete a good export in favour of nothing.
    js_path = DATA / "align_data.js"
    merged = all_data
    if js_path.exists():
        try:
            prev = json.loads(js_path.read_text().split("=", 1)[1].rstrip().rstrip(";"))
            merged = {**prev, **all_data}
        except Exception as exc:
            print(f"could not merge existing align_data.js ({exc}); writing subset only", flush=True)

    js = "window.ALIGN_DATA = " + json.dumps(merged) + ";\n"
    js_path.write_text(js)
    print(f"Wrote {js_path} ({len(js)/1e6:.1f} MB, {len(merged)} cities)", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or None))
