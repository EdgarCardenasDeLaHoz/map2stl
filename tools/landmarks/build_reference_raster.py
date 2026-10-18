"""Build the Alhambra reference raster and swap rule (F-DETAIL D4, ``city2stl.landmarks``).

Offline, run once (the output is stored under ``data/landmarks/``):

    ~/.venvs/map2stl/Scripts/python.exe -m tools.landmarks.build_reference_raster

1. Loads the Thingiverse STL (thing 5141408, mamutazos, CC BY-SA) and applies the stored 4x4
   (``thingiverse_to_geo.json``: STL mm -> local ENU metres, z scale in the z row) plus the
   residual shift of (-3 E, -4 N) m the Granada detail study found (exp4).
2. Takes the study's z-buffered, ground-subtracted grid (``thing_resid_mm``, STL frame through the
   stored 4x4) on its 1 m ENU grid (743 x 743), which is exactly a lon/lat grid: east = (lon-lon0) K cos(lat0), north = (lat-lat0) K.
3. Does this for both z scales (3.68 m/mm isotropic, 4.67 m/mm terrain fit), scores each against
   the REDIAM lidar nDSM on the swapped footprints (median |delta| over eroded, tree-free built
   cells), and keeps the scale with the lower median.
4. Writes ``data/landmarks/alhambra_reference.npz`` (height-above-ground raster + metadata) and
   ``data/landmarks/alhambra_reference_rule.json`` (the swap rule, as data).

Inputs live in ``map2stl/output/detail_study/`` (gitignored study outputs) and the OSM cache
of the study (``cache/osm/7a0d9bb04f7b975b73f9d6b8287e21a3``, Granada, polygon tolerance 3 m).
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from city2stl.resources import scratch_guard  # noqa: E402

scratch_guard(max_workers=1)

import argparse  # noqa: E402
import csv  # noqa: E402
import gzip  # noqa: E402
import json  # noqa: E402

import numpy as np  # noqa: E402

STUDY = ROOT / "output" / "detail_study"
OUT_DIR = ROOT / "data" / "landmarks"
STL = (ROOT.parent.parent / "Cities" / "granada_stl" / "Alhambra - 5141408" / "alhambra.stl")
OSM_CACHE = ROOT / "cache" / "osm" / "7a0d9bb04f7b975b73f9d6b8287e21a3.json.gz"
SHIFT_ENU_M = (-3.0, -4.0)         # residual registration shift (exp1/exp4), east, north
RENDER_CELL_MM = 0.25
SCALES = {"isotropic": None, "terrain_fit": 4.6711390306533485}   # None -> from the 4x4
LICENCE = ("Alhambra by mamutazos, Thingiverse thing 5141408, CC BY-SA "
           "(https://www.thingiverse.com/thing:5141408)")


def enu_to_affine(frame: dict, x0: float, y0: float, n_rows: int, cell_m: float = 1.0) -> list[float]:
    """Affine (a, b, c, d, e, f) of the lon/lat grid whose cell (row 0 = north) are the study's
    ENU cells of ``cell_m``: SW corner (x0, y0) metres east/north of (lon0, lat0)."""
    k = frame["K_m_per_deg"]
    kx = k * np.cos(np.radians(frame["lat0"]))
    return [cell_m / kx, 0.0, frame["lon0"] + x0 / kx,
            0.0, -cell_m / k, frame["lat0"] + (y0 + n_rows * cell_m) / k]


def render_height(grids, z_scale: float, shift=SHIFT_ENU_M) -> np.ndarray:
    """Height above ground (m) on the study's n x n 1 m ENU grid, row 0 = south, NaN = no data.

    The study already z-buffered the STL through the stored 4x4 and subtracted the ground in the
    STL frame (``thing_resid_mm``, scale independent, masked by ``thing_valid``); a z scale is
    just a multiplier. Re-deriving the ground here with ``estimate_dtm`` gave a 3.25 m median
    against the study's 2.37 m (the Alcazaba ground came out 0.8 mm low), so the study grid is
    the single source. The residual shift moves the grid by ``shift`` (east, north) metres.
    """
    resid = np.where(grids["thing_valid"], grids["thing_resid_mm"], np.nan).astype(np.float64)
    out = np.maximum(resid * z_scale, 0.0)
    de, dn = int(round(shift[0])), int(round(shift[1]))
    out = np.roll(out, (dn, de), axis=(0, 1))
    if dn > 0: out[:dn] = np.nan
    elif dn < 0: out[dn:] = np.nan
    if de > 0: out[:, :de] = np.nan
    elif de < 0: out[:, de:] = np.nan
    return out.astype(np.float32)


def rough_mask(lidar: np.ndarray) -> np.ndarray:
    """Lidar tree roughness as in exp1: 3 x 3 std over 1.5 m on cells over 2 m."""
    from scipy.ndimage import uniform_filter

    f = np.nan_to_num(lidar)
    sd = np.sqrt(np.maximum(uniform_filter(f * f, 3) - uniform_filter(f, 3) ** 2, 0))
    return (sd > 1.5) & (f > 2.0)


def read_rule_table() -> list[dict]:
    with open(STUDY / "exp4_merge" / "rule_table.csv", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def build_rule(grids, frame: dict) -> dict:
    """The swap rule as data: units (an OSM id, or a stored footprint for id-less towers/churches)."""
    from shapely.geometry import Point, mapping, shape

    rows = read_rule_table()
    labels = np.asarray(grids["building_labels"])
    x0, y0 = [float(v) for v in grids["enu_x0y0"]]
    kx = frame["K_m_per_deg"] * np.cos(np.radians(frame["lat0"]))
    with gzip.open(OSM_CACHE, "rt", encoding="utf-8") as fh:
        osm = json.load(fh)

    def centroid(label: int):
        r, c = np.nonzero(labels == label)
        if not len(r):
            return None
        e, n = x0 + c.mean() + 0.5, y0 + r.mean() + 0.5
        return [frame["lon0"] + e / kx, frame["lat0"] + n / frame["K_m_per_deg"]]

    units: dict[str, dict] = {}
    flagged_kept = []
    for row in rows:
        label = int(row["label"])
        swap = row["decision"] == "swap"
        flag = row["flag_lower"] == "True"
        if not swap:
            if flag:
                flagged_kept.append({"label": label, "name": row["name"] or None,
                                     "osm_id": row["osm_id"] or None,
                                     "ours_m": float(row["h_ours"]), "lidar_m": float(row["h_lidar"]),
                                     "decision": row["decision"]})
            continue
        osm_id = row["osm_id"] or None
        key = osm_id or f"ref:{label}"
        u = units.setdefault(key, {"key": key, "osm_id": osm_id, "name": row["name"] or None,
                                   "layer": row["layer"], "labels": [], "flag_lower": False})
        u["flag_lower"] |= flag
        u["labels"].append({"label": label, "cells": int(row["eval_cells"]),
                            "ours_m": float(row["h_ours"]), "thing_m": float(row["h_thing"]),
                            "lidar_m": float(row["h_lidar"]),
                            "median_abs_ours_m": float(row["mad_ours"]),
                            "median_abs_thing_m": float(row["mad_thing"])})
        if osm_id is None:
            c = centroid(label)
            feats = (osm.get(row["layer"]) or {}).get("features") or []
            pt = Point(c)
            hit = [f for f in feats if shape(f["geometry"]).buffer(2e-5).contains(pt)]
            if not hit:
                hit = sorted(feats, key=lambda f: shape(f["geometry"]).distance(pt))[:1]
            u["footprint"] = mapping(shape(hit[0]["geometry"]))["coordinates"] if hit else None
            u["footprint_source"] = f"OSM {row['layer']} layer, '{hit[0]['properties'].get('name')}'" \
                if hit else None
            u["centroid"] = c
    return {"units": list(units.values()), "flagged_kept": flagged_kept,
            "n_labels_swapped": sum(len(u["labels"]) for u in units.values()),
            "n_flagged": sum(u["flag_lower"] for u in units.values()) + len(flagged_kept)}


def score(raster: np.ndarray, grids, rule: dict) -> dict:
    """Median |thing - lidar| over the swapped labels' eroded, tree-free cells (exp4's method B metric)."""
    from scipy.ndimage import binary_erosion

    labels = np.asarray(grids["building_labels"])
    lidar = np.asarray(grids["lidar"], float)
    ok = np.isfinite(lidar) & ~rough_mask(lidar)
    per_label, diffs = {}, []
    for u in rule["units"]:
        for lab in u["labels"]:
            m = binary_erosion(labels == lab["label"], iterations=1) & ok
            r = np.where(np.isfinite(raster), raster, 0.0)   # NaN = no building = ground
            d = np.abs(r[m] - lidar[m])
            if d.size:
                per_label[lab["label"]] = float(np.median(d))
                diffs.append(d)
    allv = np.concatenate(diffs)
    improved = sum(per_label[lab["label"]] < lab["median_abs_ours_m"]
                   for u in rule["units"] for lab in u["labels"] if lab["label"] in per_label)
    return {"median_abs_m": float(np.median(allv)), "cells": int(allv.size),
            "improved": int(improved), "of": len(per_label), "per_label": per_label}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = ap.parse_args()

    reg = json.load(open(STUDY / "shared" / "thingiverse_to_geo.json", encoding="utf-8"))
    grids = np.load(STUDY / "shared" / "alhambra_grids.npz", allow_pickle=True)
    frame = reg["enu_frame"]
    x0, y0 = [float(v) for v in grids["enu_x0y0"]]
    n = int(grids["lidar"].shape[0])

    rule = build_rule(grids, frame)
    results = {}
    for name, zs in SCALES.items():
        zs = reg["scale_m_per_mm"] if zs is None else zs
        ras = render_height(grids, zs)
        s = score(ras, grids, rule)
        results[name] = {"z_scale_m_per_mm": zs,
                         "raster": ras, **s}
        print(f"z scale {name:12s} {results[name]['z_scale_m_per_mm']:.3f} m/mm: "
              f"median |delta| {s['median_abs_m']:.3f} m over {s['cells']} cells, "
              f"improved {s['improved']} of {s['of']}")
    best = min(results, key=lambda k: results[k]["median_abs_m"])
    print(f"kept: {best}")

    keep = results[best]
    ras = keep["raster"]
    # Metres in 1/100 m steps, 65535 = no data (about 130 KB compressed).
    q = np.where(np.isfinite(ras), np.clip(np.round(ras * 100), 0, 65534), 65535).astype(np.uint16)
    q = np.flipud(q)                       # row 0 north, as a rasterio affine expects
    meta = {
        "description": "Alhambra reference building heights above ground (m), derived from the "
                       "Thingiverse model",
        "source": LICENCE, "licence": "CC BY-SA", "crs": "EPSG:4326",
        "transform_affine": enu_to_affine(frame, x0, y0, n),
        "scale_chosen": best, "z_scale_m_per_mm": keep["z_scale_m_per_mm"],
        "scores_median_abs_m": {k: v["median_abs_m"] for k, v in results.items()},
        "scores_improved": {k: f"{v['improved']} of {v['of']}" for k, v in results.items()},
        "scored_against": "REDIAM MDHN lidar nDSM (1 m) on the swapped footprints",
        "shift_enu_m": list(SHIFT_ENU_M), "units": "m, uint16 x 0.01, 65535 = no data",
        "bbox_nsew": [float(v) for v in grids["bbox"]],
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    out = args.out_dir / "alhambra_reference.npz"
    np.savez_compressed(out, height_cm=q, meta=json.dumps(meta))
    rule_out = {"source": LICENCE, "licence": "CC BY-SA",
                "rule": "exp4_merge/rule_table.csv: swap where the Thingiverse model covers >=90% of "
                        "the footprint, is >=1 m tall, and its median |delta| to lidar is lower than "
                        "OSM's; flag (not delete) lidar<2 m and OSM>=5 m",
                "z_scale_m_per_mm": keep["z_scale_m_per_mm"], **rule}
    with open(args.out_dir / "alhambra_reference_rule.json", "w", encoding="utf-8") as fh:
        json.dump(rule_out, fh, indent=1, ensure_ascii=False)
    print(f"wrote {out} ({out.stat().st_size / 1e6:.2f} MB) and the rule json")


if __name__ == "__main__":
    main()
