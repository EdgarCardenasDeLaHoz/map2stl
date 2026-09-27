"""Score a generated city model against a surveyed or registered height reference.

Promoted 2026-09-27 (F-LANDMARK §6, F-REGION §5) from the scratch "plate critic"
described in ``Code/docs/plate-critic.md``.  The scratch code (``critic/`` in an
earlier session's scratchpad: ``plate_pairs.py``, ``fresh_raster.py``, ...) did not
survive, so the metrics are re-implemented here from that page's definitions:

* both surfaces are **height above ground in metres on one grid** (row 0 = south);
* the plate is warped into the OSM window through the pack's accepted placement
  matrix and put in metres by its tallest-roof anchor (``street_place.metres_per_unit``);
* our model is either the building footprints the city model extrudes (polygon
  **fill**, never bounding boxes -- the page's correction section) or a rendered STL
  whose ground is removed by the same metre-sized top-hat the exporter uses;
* the headline number is the **per-building median absolute height error**, with
  footprint IoU and a roof-error summary beside it.

The learned critic from that page (a CNN telling plate from extrusion) is deliberately
*not* promoted: it was monotone in "more relief" and agreed with the truth argmin in
0 of 8 cities.  Only the measured-error side is kept.

What a roof number means at a given cell size is reported with it: at the 5-6 m cells
of the vendor plates a roof is below the registration noise (the page's second table),
so ``roofs.resolvable`` is False above ``ROOF_RESOLVABLE_CELL_M``.

Entry points
------------
``score_heights(model_h, ref_h, cell_m, ...)``        the metrics, on arrays
``pack_reference(slug, ...)``                          a registered plate pack as a Reference
``ndsm_reference(bbox_nsew, resolution)``              a 3DEP lidar nDSM as a Reference (network)
``buildings_raster(features, ref)``                    city-model buildings on a Reference grid
``stl_raster(stl_path, bbox, ref)``                    a rendered STL on a Reference grid
``score_model(model, ref)``                            dispatch over the two model kinds
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from city2stl.registration.align_paths import data_dir as _data_dir

logger = logging.getLogger(__name__)

__all__ = [
    "FLOOR_M", "ROOF_RESOLVABLE_CELL_M", "Reference", "buildings_raster", "ndsm_reference",
    "pack_reference", "resample_to_grid", "score_heights", "score_model", "stl_raster",
]

#: A cell at least this far above ground counts as built (street_place.FLOOR_M).
FLOOR_M = 2.0
#: Footprints smaller than this many cells are not scored as buildings.
MIN_BUILDING_CELLS = 4
#: A roof needs this many cells before its shape is compared at all.
MIN_ROOF_CELLS = 9
#: Above this cell size a roof is smaller than the grid can show (plate-critic.md).
ROOF_RESOLVABLE_CELL_M = 2.0
#: Ground-removal opening for a rendered STL: the exporter's 80 m residual opening.
GROUND_OPENING_M = 80.0


@dataclass
class Reference:
    """A height-above-ground surface in metres on a geographic grid (row 0 = south)."""

    heights_m: np.ndarray            # (rows, cols) float, 0 = ground, NaN = no data
    bbox_nsew: tuple                 # (N, S, E, W) of the grid
    cell_m: float                    # metres per cell (mean of the two axes)
    source: str                      # "pack:<slug>" | "ndsm:3dep" | ...
    valid: np.ndarray | None = None  # bool; defaults to isfinite(heights_m)
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        self.heights_m = np.asarray(self.heights_m, dtype=np.float64)
        if self.valid is None:
            self.valid = np.isfinite(self.heights_m)
        self.valid = np.asarray(self.valid, dtype=bool)

    @property
    def shape(self) -> tuple[int, int]:
        return self.heights_m.shape

    def describe(self) -> dict:
        n, s, e, w = self.bbox_nsew
        return {"source": self.source, "bbox": {"north": n, "south": s, "east": e, "west": w},
                "shape": list(self.shape), "cell_m": round(float(self.cell_m), 3),
                "valid_pct": round(100.0 * float(self.valid.mean()), 1), **self.meta}


# ---------------------------------------------------------------------------------------------
# Metrics

def _plain(v):
    """JSON-safe float: NaN / inf become None."""
    if v is None:
        return None
    v = float(v)
    return round(v, 4) if math.isfinite(v) else None


def _component_labels(mask: np.ndarray) -> tuple[np.ndarray, int]:
    import cv2
    n, labels = cv2.connectedComponents(mask.astype(np.uint8), connectivity=8)
    return labels, n - 1


def score_heights(model_h, ref_h, cell_m: float, *, model_valid=None, ref_valid=None,
                  labels=None, fit_scale: bool = False, floor_m: float = FLOOR_M,
                  min_cells: int = MIN_BUILDING_CELLS) -> dict:
    """Compare a model surface with a reference surface, both height above ground.

    ``model_h`` / ``ref_h``: same-shape arrays in metres (``model_h`` in model units when
    ``fit_scale``).  ``labels``: optional int array naming each model building (0 = none),
    e.g. one id per extruded footprint; without it the buildings are the connected
    components of the reference's built mask (a plate block, a lidar roof).

    ``fit_scale`` puts an unscaled model in metres by the median ratio reference/model over
    cells both call built -- the page's median-ratio fit.  It absorbs any uniform height
    bias, so a fitted score says how well the model's heights are *shaped*, not how tall.

    Returns a JSON-safe dict: ``cells`` (per-cell error on shared built cells),
    ``footprint`` (IoU, precision, recall), ``buildings`` (per-building median absolute
    error -- the headline), ``roofs`` (within-footprint shape error) and ``scale``.
    """
    model = np.asarray(model_h, dtype=np.float64)
    ref = np.asarray(ref_h, dtype=np.float64)
    if model.shape != ref.shape:
        raise ValueError(f"model {model.shape} and reference {ref.shape} are not on one grid")
    mv = np.isfinite(model) if model_valid is None else (np.asarray(model_valid, bool)
                                                         & np.isfinite(model))
    rv = np.isfinite(ref) if ref_valid is None else (np.asarray(ref_valid, bool)
                                                     & np.isfinite(ref))
    both = mv & rv
    model = np.where(both, model, np.nan)
    ref = np.where(both, ref, np.nan)

    scale = 1.0
    if fit_scale:
        pos = both & (ref > floor_m) & (model > 0)
        if pos.sum() >= min_cells:
            scale = float(np.median(ref[pos] / model[pos]))
        model = model * scale

    mb = both & (model > floor_m)
    rb = both & (ref > floor_m)
    inter = float((mb & rb).sum())
    union = float((mb | rb).sum())
    footprint = {
        "iou": _plain(inter / union) if union else None,
        "precision": _plain(inter / mb.sum()) if mb.any() else None,   # model built that ref confirms
        "recall": _plain(inter / rb.sum()) if rb.any() else None,      # ref built the model covers
        "model_built_pct": _plain(100.0 * mb.sum() / both.sum()) if both.any() else None,
        "ref_built_pct": _plain(100.0 * rb.sum() / both.sum()) if both.any() else None,
    }

    shared = mb & rb
    d = (model - ref)[shared]
    cells = {"n": int(shared.sum())}
    if d.size:
        cells.update({
            "mae_m": _plain(np.mean(np.abs(d))), "rmse_m": _plain(np.sqrt(np.mean(d * d))),
            "median_abs_m": _plain(np.median(np.abs(d))), "bias_m": _plain(np.mean(d)),
            "pearson_r": _plain(np.corrcoef(model[shared], ref[shared])[0, 1])
            if d.size > 2 and np.std(model[shared]) > 0 and np.std(ref[shared]) > 0 else None,
        })

    if labels is None:
        labels, _ = _component_labels(rb)
    labels = np.where(both, np.asarray(labels), 0)
    buildings, roofs = _per_building(model, ref, labels, min_cells)

    return {
        "cell_m": _plain(cell_m),
        "valid_cells": int(both.sum()),
        "scale": {"fitted": bool(fit_scale), "m_per_unit": _plain(scale)},
        "footprint": footprint,
        "cells": cells,
        "buildings": buildings,
        "roofs": dict(roofs, resolvable=bool(cell_m <= ROOF_RESOLVABLE_CELL_M),
                      note=None if cell_m <= ROOF_RESOLVABLE_CELL_M else
                      f"{cell_m:.1f} m cells: a roof is smaller than the grid and the "
                      f"registration noise (docs/plate-critic.md); read these as indicative"),
    }


def _per_building(model, ref, labels, min_cells):
    """Per-building median heights and within-footprint roof shape, one pass over labels."""
    flat = labels.ravel()
    order = np.argsort(flat, kind="stable")
    ids, starts = np.unique(flat[order], return_index=True)
    ends = np.append(starts[1:], flat.size)
    mflat, rflat = model.ravel(), ref.ravel()
    errs, roof_err, relief_ref, relief_model = [], [], [], []
    for lab, a, b in zip(ids, starts, ends, strict=True):
        if lab == 0 or b - a < min_cells:
            continue
        idx = order[a:b]
        m, r = mflat[idx], rflat[idx]
        keep = np.isfinite(m) & np.isfinite(r)
        if keep.sum() < min_cells:
            continue
        m, r = m[keep], r[keep]
        mm, rm = float(np.median(m)), float(np.median(r))
        errs.append(mm - rm)
        if m.size >= MIN_ROOF_CELLS and rm > FLOOR_M:
            roof_err.append(float(np.median(np.abs((m - mm) - (r - rm)))))
            relief_ref.append(float(np.percentile(r, 90) - np.percentile(r, 10)))
            relief_model.append(float(np.percentile(m, 90) - np.percentile(m, 10)))
    e = np.asarray(errs)
    buildings = {"n": int(e.size)}
    if e.size:
        ae = np.abs(e)
        buildings.update({
            "median_abs_error_m": _plain(np.median(ae)), "mean_error_m": _plain(np.mean(e)),
            "p90_abs_error_m": _plain(np.percentile(ae, 90)),
            "within_2m_pct": _plain(100.0 * np.mean(ae <= 2.0)),
            "within_5m_pct": _plain(100.0 * np.mean(ae <= 5.0)),
        })
    roofs = {"n": len(roof_err)}
    if roof_err:
        roofs.update({
            "median_shape_error_m": _plain(np.median(roof_err)),
            "median_relief_ref_m": _plain(np.median(relief_ref)),
            "median_relief_model_m": _plain(np.median(relief_model)),
        })
    return buildings, roofs


# ---------------------------------------------------------------------------------------------
# Grids

def _cell_m(bbox_nsew, shape) -> float:
    from geo2stl.geo import bbox_size_m
    n, s, e, w = bbox_nsew
    lon_m, lat_m = bbox_size_m({"north": n, "south": s, "east": e, "west": w})
    return 0.5 * (lat_m / shape[0] + lon_m / shape[1])


def resample_to_grid(src, src_bbox, dst_bbox, dst_shape, *, src_row0="south",
                     nearest: bool = False, fill=np.nan) -> np.ndarray:
    """Sample ``src`` (covering ``src_bbox``) at the cell centres of a ``dst_bbox`` grid.

    Both bboxes are (N, S, E, W) in degrees; the result is row 0 = south.  Cells outside
    ``src_bbox`` get ``fill``.  A plain linear lat/lon mapping: fine at city scale.
    """
    import cv2
    src = np.asarray(src, dtype=np.float32)
    if src_row0 == "north":
        src = src[::-1]
    sn, ss, se, sw = src_bbox
    dn, ds, de, dw = dst_bbox
    rows, cols = dst_shape
    lat = ds + (np.arange(rows) + 0.5) * (dn - ds) / rows
    lon = dw + (np.arange(cols) + 0.5) * (de - dw) / cols
    sy = (lat - ss) / (sn - ss) * src.shape[0] - 0.5
    sx = (lon - sw) / (se - sw) * src.shape[1] - 0.5
    map_x, map_y = np.meshgrid(sx.astype(np.float32), sy.astype(np.float32))
    interp = cv2.INTER_NEAREST if nearest else cv2.INTER_LINEAR
    fin = np.isfinite(src)
    out = cv2.remap(np.where(fin, src, 0.0).astype(np.float32), map_x, map_y, interp,
                    borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    ok = cv2.remap(fin.astype(np.float32), map_x, map_y, cv2.INTER_NEAREST,
                   borderMode=cv2.BORDER_CONSTANT, borderValue=0.0) > 0.5
    return np.where(ok, out, fill).astype(np.float64)


# ---------------------------------------------------------------------------------------------
# References

def _pack_meta(slug: str, data_dir: Path | None) -> tuple[Path, dict]:
    d = (data_dir if data_dir is not None else _data_dir()) / slug
    meta_path = d / "meta.json"
    if not meta_path.is_file():
        raise FileNotFoundError(f"No align pack {slug!r} (expected {meta_path})")
    return d, json.loads(meta_path.read_text(encoding="utf-8"))


def pack_reference(slug: str, *, data_dir: Path | None = None, matrix=None) -> Reference:
    """A registered plate pack (``tools/align_tool/data/<slug>/``) as a Reference.

    The plate's building raster (``stl_heightmap.npy``: height above local ground, NaN =
    none) is put in metres by the tallest-roof anchor and warped into the pack's OSM window
    through ``matrix`` (default: the accepted ``pipeline_guess``).  A surveyed pack with no
    placement (Old San Juan's lidar) already spans the window and is already in metres.
    """
    import cv2
    d, meta = _pack_meta(slug, data_dir)
    bbox = tuple(float(v) for v in meta["osm_bbox_nsew"])
    guess = meta.get("pipeline_guess")
    residual = np.load(d / "stl_residual.npy") if (d / "stl_residual.npy").exists() else None
    plate = np.load(d / "stl_heightmap.npy") if (d / "stl_heightmap.npy").exists() else residual
    if plate is None:
        raise FileNotFoundError(f"Pack {slug!r} has no stl_heightmap.npy / stl_residual.npy")
    relief = np.load(d / "stl_relief.npy") if (d / "stl_relief.npy").exists() else None
    footprint = np.isfinite(relief) if relief is not None else np.ones(plate.shape, bool)

    if guess is None and matrix is None:
        # Surveyed surface cut to the window itself, in metres (meta "note").
        heights = np.where(footprint, np.nan_to_num(plate, nan=0.0), np.nan)
        return Reference(heights, bbox, _cell_m(bbox, heights.shape), f"pack:{slug}",
                         meta={"pack": slug, "kind": "survey", "m_per_unit": 1.0,
                               "note": meta.get("note")})

    from city2stl.registration.street_place import metres_per_unit
    tallest = float(meta.get("tallest_m") or 0.0)
    mpu = (metres_per_unit(residual, relief, tallest)
           if residual is not None and relief is not None and tallest > 0
           else (tallest / float(np.nanmax(plate)) if tallest > 0 and np.nanmax(plate) > 0 else 1.0))
    res = int(meta.get("resolution") or 512)
    M = np.asarray(matrix if matrix is not None else guess["matrix"], dtype=np.float64)
    h = (np.nan_to_num(np.asarray(plate, np.float32), nan=0.0) * mpu).astype(np.float32)
    warped = cv2.warpAffine(h, M, (res, res), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    inside = cv2.warpAffine(footprint.astype(np.float32), M, (res, res),
                            flags=cv2.INTER_NEAREST, borderMode=cv2.BORDER_CONSTANT,
                            borderValue=0.0) > 0.5
    heights = np.where(inside, warped, np.nan)
    return Reference(heights, bbox, float(meta.get("cell_size_m") or _cell_m(bbox, (res, res))),
                     f"pack:{slug}", valid=inside,
                     meta={"pack": slug, "kind": "plate", "m_per_unit": round(mpu, 4),
                           "placement": (guess or {}).get("source") if matrix is None
                           else "explicit"})


def ndsm_reference(bbox_nsew, resolution: int = 512) -> Reference:
    """A USGS 3DEP lidar nDSM (``lidar_3dep_ept.get_ndsm``) as a Reference.

    US only, needs PDAL + py3dep and the network on first use (then cached).  Raises
    LookupError when no survey covers the bbox.
    """
    from city2stl.height.providers.lidar_3dep_ept import get_ndsm
    bbox = tuple(float(v) for v in bbox_nsew)
    ndsm = get_ndsm(bbox, resolution=resolution)
    if ndsm is None:
        raise LookupError("No 3DEP lidar nDSM for this bbox (outside US coverage, or PDAL "
                          "is not installed)")
    ndsm = np.asarray(ndsm, dtype=np.float64)
    return Reference(ndsm, bbox, _cell_m(bbox, ndsm.shape), "ndsm:3dep",
                     meta={"kind": "lidar"})


# ---------------------------------------------------------------------------------------------
# Models

def _feature_height(props: dict) -> float:
    """The height the city model extrudes (``city_model``: ``height_m``, default 10 m)."""
    try:
        return float(props.get("height_m") or props.get("height") or 10.0)
    except (TypeError, ValueError):
        return 10.0


def buildings_raster(features: list[dict], ref: Reference) -> tuple[np.ndarray, np.ndarray]:
    """City-model building footprints on the Reference grid: (heights_m, labels).

    GeoJSON features in lon/lat; each polygon is filled (not bbox'd) at its extruded
    ``height_m`` and labelled with its own id, so per-building error is per footprint.
    """
    from numpy2stl.raster import burn_polygons
    from shapely.geometry import shape as _shape
    n, s, e, w = ref.bbox_nsew
    geoms, heights = [], []
    for f in features or ():
        g = (f or {}).get("geometry")
        if not g or g.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        try:
            geom = _shape(g)
        except Exception:
            continue
        if geom.is_empty:
            continue
        geoms.append(geom)
        heights.append(_feature_height(f.get("properties") or {}))
    rows, cols = ref.shape
    if not geoms:
        return np.zeros(ref.shape), np.zeros(ref.shape, dtype=np.int32)
    h = burn_polygons(geoms, (rows, cols), bounds=(w, s, e, n), values=heights, mode="max",
                      dtype=np.float64)
    lab = burn_polygons(geoms, (rows, cols), bounds=(w, s, e, n),
                        values=list(range(1, len(geoms) + 1)), mode="set", dtype=np.float64)
    return np.flipud(h).copy(), np.flipud(lab).astype(np.int32)   # north-up -> row 0 = south


def stl_raster(stl_path, bbox_nsew, ref: Reference, *, up_axis: str = "z",
               m_per_unit: float | None = None) -> tuple[np.ndarray, bool]:
    """A rendered model STL on the Reference grid, as height above ground.

    The STL's XY extent is taken to span ``bbox_nsew`` exactly (true of the app's terrain
    and city exports, which cover the DEM bbox).  Ground is removed with the exporter's
    metre-sized top-hat (``numpy2stl.raster.terrain_residual``).  Returns (heights,
    needs_scale): without ``m_per_unit`` the heights are in model units and the caller
    should score with ``fit_scale=True``.
    """
    from numpy2stl.raster import terrain_residual

    from city2stl.height.stl_import import stl_to_heightmap
    n, s, e, w = (float(v) for v in bbox_nsew)
    bbox = {"north": n, "south": s, "east": e, "west": w}
    hm, _mask = stl_to_heightmap(stl_path, bbox, resolution_m=max(0.5, ref.cell_m),
                                 up_axis=up_axis)
    cell = _cell_m((n, s, e, w), hm.shape)
    # The opening is sized in metres of ground; the STL's z is not, but only x/y matter here.
    residual, valid = terrain_residual(hm, cell_size_m=cell, building_max_m=GROUND_OPENING_M)
    above = np.where(valid, residual, np.nan)
    if m_per_unit:
        above = above * float(m_per_unit)
    out = resample_to_grid(above, (n, s, e, w), ref.bbox_nsew, ref.shape, src_row0="north")
    return out, not m_per_unit


def score_model(model: dict, ref: Reference) -> dict:
    """Score one model against ``ref``.

    ``model`` is ``{"kind": "buildings", "features": [...]}`` (the current city-model
    build) or ``{"kind": "stl", "path": ..., "bbox": {...}, "up_axis": "z",
    "m_per_unit": None}`` (a rendered model).  The result carries the reference and model
    descriptions next to the metrics.
    """
    kind = model.get("kind")
    if kind == "buildings":
        heights, labels = buildings_raster(model.get("features") or [], ref)
        # Buildings are drawn on bare ground: every reference cell is also a model cell.
        result = score_heights(heights, ref.heights_m, ref.cell_m, ref_valid=ref.valid,
                               labels=labels)
        result["model"] = {"kind": "buildings", "features": int(labels.max())}
    elif kind == "stl":
        b = model["bbox"]
        bbox = (b["north"], b["south"], b["east"], b["west"])
        heights, needs_scale = stl_raster(model["path"], bbox, ref,
                                          up_axis=model.get("up_axis") or "z",
                                          m_per_unit=model.get("m_per_unit"))
        result = score_heights(heights, ref.heights_m, ref.cell_m, ref_valid=ref.valid,
                               fit_scale=needs_scale)
        result["model"] = {"kind": "stl", "name": Path(str(model["path"])).name}
    else:
        raise ValueError(f"Unknown model kind {kind!r} (expected 'buildings' or 'stl')")
    result["reference"] = ref.describe()
    return result
