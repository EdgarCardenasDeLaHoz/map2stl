"""Per-pixel roof / wall / ground map of a photo: depth normals at full resolution, refined by SegFormer.

``roofwall_mask(photo_dir_or_stem)`` reads ``<stem>_depth.npy`` (photo disparity), ``<stem>_orient.json``
(camera + the a/b scale, ``depth = a / (disp - b)``) and ``<stem>_labels.npy`` (SegFormer ADE20K label
map written by city2stl/skyline, one class id per pixel) and returns ``(cls uint8 HxW, stats)`` with the
:mod:`orient` codes 0 none, 1 ground, 2 flat roof, 3 pitched roof, 4 wall. CLI: ``python
tools/photo_label/roofwall.py runs/photo/<folder> ...`` writes ``<stem>_roofwall.png`` (palette PNG, index =
class, colours of static/orient.js::OR_SURF, 0 black) and ``<stem>_roofwall.json`` (class pixel counts).
The route POST /api/depth/orient with ``full: true`` uses :func:`classify_full` on the same inputs.

Method. Per-pixel depth is noisy, so normals use :func:`orient.normals` with an edge-aware mean over a
(2r+1)^2 window and a central difference over +-r pixels, r = round(width / 400) clamped to 1..5 (about
0.5 % of the image width: smooth enough that a roof plane gives one class, small enough to keep walls
and ridges). Both steps drop neighbours across a >15 % range jump, so occlusion edges stay unclassified
(class 0) instead of being smeared. SegFormer then refines (:func:`refine`): sky, vegetation, water,
people and vehicles become 0; building pixels (wall, building, house, skyscraper) keep the normal class,
except that a ground call (only exists with a DEM) is reassigned to flat roof; other pixels (road,
earth, hill ...) can only be ground or 0, since terrain slopes read as pitched. Unclassified pixels get a
3x3 majority vote of their neighbours in the same semantic group, twice, to close the thin edge gaps.

Per-building vote (:func:`vote`): inside each footprint mask (OSM footprints projected with the camera by
:func:`load_buildings`, rasterised by :func:`orient.rasterize`, nearer buildings on top) every roof pixel
(flat + pitched) takes the building's majority roof class, when it has at least MIN_ROOF_CELLS roof
pixels; wall pixels keep their normal-based class (so facing stays per pixel); pixels outside any
footprint keep their per-pixel class. ``<stem>_roofwall.json`` then lists per building the roof class,
pixel count, wall / flat / pitched share, median pitch and wall facing (:func:`orient.building_summary`).

Radius sweep (2026-11, three Granada photos, stencil half-width as a share of image width; coherence = mean
share of a roof pixel's 5x5 neighbours with its own class): 0.25 % (old) 0.86, 0.5 % 0.87-0.90, 1 % 0.91,
1.5 % 0.92, 2 % 0.92-0.93 - but walls fall from 7 % to 1.6 % (Albaicin) and 4 % to 0.1 % (pano) from 0.5 % to
2 %, because a wide stencil straddles the thin wall strips. 1 % (``RADIUS_FRAC``) keeps most of the
coherence gain and the wall bands; the mean window is half the stencil, at most 4 px.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import orient  # noqa: E402

# ADE20K ids (0-based, as in city2stl/skyline/_core/segmentation.py)
SEM_BUILDING = (0, 1, 25, 48)             # wall, building, house, skyscraper
SEM_NONE = (2, 4, 9, 17, 29,              # sky, tree, grass, plant, field
            21, 26, 60,                   # water, sea, river
            12, 20, 80, 83, 90, 102, 116, 127)  # person, car, bus, truck, ... (moving things)
SURF_RGB = ((0, 0, 0), (150, 130, 110), (10, 132, 255), (255, 149, 0), (160, 230, 150))
NAMES = ("none", "ground", "flat_roof", "pitched_roof", "wall")


RADIUS_FRAC = 0.01     # stencil half-width as a share of the image width (see the sweep in the docstring)


def _radius(gw: int) -> int:
    return int(min(24, max(1, round(gw * RADIUS_FRAC))))


def _window(r: int) -> int:
    """Edge-aware mean half-width for stencil ``r``: half of it, 1..4 (cost grows with its square)."""
    return int(min(4, max(1, r // 2)))


def classify_full(z, cam: dict, labels=None, ground_h=None, radius: int | None = None,
                  window: int | None = None) -> dict:
    """:func:`orient.classify` at the resolution of ``z`` with wide normals, then :func:`refine` when
    ``labels`` (same shape as ``z``) is given. ``radius`` is the difference stencil half-width (px),
    ``window`` the half-width of the edge-aware mean (default :func:`_window`). Same dict as classify."""
    z = np.asarray(z, dtype=np.float64)
    r = radius or _radius(z.shape[1])
    P = orient.backproject(z, cam)
    res = orient.classify(orient.normals(P, radius=window or _window(r), step=r), P, cam, ground_h)
    if labels is not None:
        res["cls"] = refine(res["cls"], labels)
        res["pitch"] = np.where(np.isin(res["cls"], (2, 3)), res["pitch"], np.nan).astype(np.float32)
        res["az"] = np.where(np.isin(res["cls"], (3, 4)), res["az"], np.nan).astype(np.float32)
    return res


MIN_ROOF_PX = 12      # a footprint needs this many roof pixels to vote


def vote(cls, blabels, ids=None):
    """Per-building roof vote (module docstring) on class map ``cls`` (same shape as ``blabels``, the
    :func:`orient.rasterize` label grid, -1 none). Returns a new uint8 map."""
    out = np.asarray(cls, np.uint8).copy()
    for k in np.unique(blabels[blabels >= 0]):
        m = blabels == k
        roof = m & ((cls == orient.FLAT) | (cls == orient.PITCHED))
        nf, npi = int((cls[roof] == orient.FLAT).sum()), int((cls[roof] == orient.PITCHED).sum())
        if nf + npi >= MIN_ROOF_PX:
            out[roof] = orient.FLAT if nf >= npi else orient.PITCHED
    return out


def load_buildings(cam: dict, region: str = "granada"):
    """Footprints seen by ``cam`` as the route's ``buildings`` list [{id, rings, dist}] (photo pixels), via the
    region's DEM + OSM (read-only; no key, no network). Raises when the region has no DEM."""
    import regions as rg
    import solve
    import terrain_solve as ts
    cfg = rg.Regions(rg.load_regions(), region, api_key="").get(region)
    dem = cfg.dem()
    if dem is None:
        raise RuntimeError("no DEM for region " + region)
    r = ts.project_buildings(dem, cfg.footprints(), solve._camera(cam, int(cam["width"]), int(cam["height"])))
    return [{"id": b["id"], "rings": b["contours"], "dist": b["dist_m"]} for b in r["buildings"]]


def refine(cls, labels, fill_passes: int = 2):
    """Apply the SegFormer rules (module docstring) to a depth class map; returns a new uint8 map."""
    cls = np.asarray(cls, np.uint8).copy()
    lab = np.asarray(labels)
    bld = np.isin(lab, SEM_BUILDING)
    gone = np.isin(lab, SEM_NONE)
    cls[gone] = 0
    cls[bld & (cls == orient.GROUND)] = orient.FLAT
    cls[~bld & ~gone & (cls > 0)] = orient.GROUND
    # the unclassified (edges, missing depth): majority of same-group neighbours
    for _ in range(fill_passes):
        todo = (cls == 0) & ~gone
        if not todo.any():
            break
        votes = np.zeros((5,) + cls.shape, np.int16)
        for sc, sb in zip(orient._shifts(cls.astype(np.float32), 0.0), orient._shifts(bld, False)):
            same = sb == bld
            for c in (1, 2, 3, 4):
                votes[c] += same & (sc == c)
        best = votes[1:].argmax(0) + 1
        has = votes[1:].max(0) >= 3
        best = np.where(bld, best, orient.GROUND)     # non-building pixels may only become ground
        has &= bld | (votes[1] >= 3)
        sel = todo & has
        cls[sel] = best[sel].astype(np.uint8)
    return cls


def _resolve(p) -> tuple[Path, str]:
    p = Path(p)
    if p.is_dir():
        return p, "photo"
    name = p.name
    for suf in ("_depth.npy", "_labels.npy", "_orient.json", ".png", ".webp", ".jpg"):
        name = name.removesuffix(suf)
    return p.parent, name


def _block_mean(a, w: int, h: int):
    """Mean of ``a`` over a w x h block grid, ignoring NaN."""
    gh, gw = a.shape
    ys = np.linspace(0, gh, h + 1).astype(int)[:-1]
    xs = np.linspace(0, gw, w + 1).astype(int)[:-1]
    ok = np.isfinite(a)
    v = np.where(ok, a, 0.0)
    sv = np.add.reduceat(np.add.reduceat(v, ys, axis=0), xs, axis=1)
    sn = np.add.reduceat(np.add.reduceat(ok.astype(float), ys, axis=0), xs, axis=1)
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(sn > 0, sv / sn, np.nan)


def roofwall_mask(photo_dir_or_stem, max_w: int = 2000, region: str | None = "granada", radius: int | None = None):
    """(cls uint8 HxW, stats) of one photo folder (stem ``photo``) or of ``<dir>/<stem>`` given a path to any
    of its files / the stem. Needs ``<stem>_depth.npy`` and ``<stem>_orient.json`` (camera, a, b);
    ``<stem>_labels.npy`` is used when present. Width is the depth map's, capped at ``max_w`` by block mean.
    With a ``region`` (None skips it) the footprints vote per building and ``stats["buildings"]`` holds
    their summaries (None when the region's DEM / OSM cannot be read)."""
    d, stem = _resolve(photo_dir_or_stem)
    disp = np.load(d / f"{stem}_depth.npy").astype(np.float64)
    meta = json.loads((d / f"{stem}_orient.json").read_text(encoding="utf-8"))
    cam = dict(meta["camera"])
    a, b = float(meta["a"]), float(meta["b"])
    ph, pw = disp.shape
    if pw > max_w:
        disp = _block_mean(disp, max_w, max(1, round(ph * max_w / pw)))
    with np.errstate(invalid="ignore", divide="ignore"):
        z = a / (disp - b)
    z[~np.isfinite(z) | (z <= 0) | (z > 5000)] = np.nan
    lab = None
    lp = d / f"{stem}_labels.npy"
    if lp.is_file():
        lab = np.load(lp)
        if lab.shape != z.shape:
            yi = np.minimum(np.arange(z.shape[0]) * lab.shape[0] // z.shape[0], lab.shape[0] - 1)
            xi = np.minimum(np.arange(z.shape[1]) * lab.shape[1] // z.shape[1], lab.shape[1] - 1)
            lab = lab[np.ix_(yi, xi)]
    res = classify_full(z, cam, lab, radius=radius)
    cls, bsum, berr = res["cls"], None, None
    if region:
        try:
            blds = load_buildings(cam, region)
            bl, ids = orient.rasterize(blds, z.shape[1], z.shape[0], cam["width"], cam["height"])
            cls = vote(cls, bl)
            bsum = {str(ids[k]): v for k, v in orient.building_summary(cls, res["pitch"], res["az"], bl).items()}
        except Exception as e:  # noqa: BLE001 - a missing DEM / OSM must not lose the per-pixel result
            berr = f"{type(e).__name__}: {e}"
    counts = np.bincount(cls.ravel(), minlength=5)
    stats = {"width": int(z.shape[1]), "height": int(z.shape[0]), "radius_px": _radius(z.shape[1]),
             "semantic": lab is not None, "buildings": bsum, "buildings_error": berr,
             "pixels": {k: int(counts[i]) for i, k in enumerate(NAMES)},
             "share": {k: round(float(counts[i]) / cls.size, 4) for i, k in enumerate(NAMES)}}
    return cls, stats


def write_outputs(photo_dir_or_stem, max_w: int = 2000, region: str | None = "granada") -> dict:
    """Run :func:`roofwall_mask`, write ``<stem>_roofwall.png`` and ``.json`` beside the inputs; returns stats."""
    from PIL import Image
    cls, stats = roofwall_mask(photo_dir_or_stem, max_w, region)
    d, stem = _resolve(photo_dir_or_stem)
    im = Image.fromarray(cls, "P")
    im.putpalette([c for rgb in SURF_RGB for c in rgb])
    im.save(d / f"{stem}_roofwall.png")
    (d / f"{stem}_roofwall.json").write_text(json.dumps(stats, indent=1), encoding="utf-8")
    return stats


if __name__ == "__main__":
    from city2stl.resources import scratch_guard
    scratch_guard(gpu_gb=0, max_workers=2)
    args = [a for a in sys.argv[1:] if not a.startswith("--region=")]
    reg = next((a[9:] for a in sys.argv[1:] if a.startswith("--region=")), "granada") or None   # --region= : no footprints
    for arg in args:
        s = write_outputs(arg, region=reg)
        print(arg, s["width"], "x", s["height"], {k: f"{v * 100:.1f}%" for k, v in s["share"].items()},
              "buildings:", len(s["buildings"]) if s["buildings"] is not None else s["buildings_error"])
