"""Score a city export's building heights against survey truth (F-PANOVIEW).

    python tools/eval/score_export_heights.py --polygons <key prefix | file.json.gz> \
        --npz cache/city_models/<key>.npz --out runs/panoview/granada/score_before.json

A finished city model cache (``.npz``) holds only meshes, so the buildings' ids, footprints and
height sources come from the ``city_polygons`` cache entry of the same build (model mm,
properties ``osm_id`` / ``height_m`` / ``height_source``). Two readings are scored:

* ``intended``: the ``height_m`` property of the polygon (what the export meant to build);
* ``mesh`` (with ``--npz``): what the mesh holds, i.e. highest building vertex inside the
  footprint minus the highest terrain under it, divided by the vertical print scale
  (``z_mm_per_m``, true scale x exaggeration; derived from ``--bbox`` for true scale).

Matching: the truth file (``benchmark.footprint_truth``) is keyed by a footprint hash and
stores no geometry, only ``osm_id``, so buildings pair by OSM id (a footprint IoU >= 0.5 match
has nothing to overlap: no truth record carries a ring).
Reports MAE, bias (estimate - truth), share within 25 % of truth, n, overall and per
``height_source``.

``--exclude-source`` (default ``survey``; repeat or pass nothing to keep none) also scores the
buildings *without* those height sources, as ``intended_excluding`` / ``mesh_excluding``: a CNIG
truth is not independent of a CNIG survey fill, so rank providers on that held-out score.
"""

from __future__ import annotations

import argparse
import glob
import gzip
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np  # noqa: E402
import shapely  # noqa: E402

from geo2stl.geo import M_PER_DEG_LON_EQ  # noqa: E402

TRUTH_DEFAULT = REPO / "city2stl" / "skyline" / "runs" / "benchmark" / "truth" / "granada.json"
#: Granada export bbox (south, west, north, east) of the 999 x 721 mm model, fitted on 4.5k
#: buildings by osm_id (OSM node centroids vs model-mm polygon centroids; 0.4 mm median error).
GRANADA_BBOX = (37.16931, -3.60929, 37.18729, -3.57801)
GRANADA_MODEL_MM = (999.0, 721.0)
TOL_REL = 0.25


class Georef:
    """lon/lat <-> model mm for an export of ``bbox`` = (south, west, north, east), size ``mm``."""

    def __init__(self, bbox=GRANADA_BBOX, mm=GRANADA_MODEL_MM):
        self.s, self.w, self.n, self.e = bbox
        self.wmm, self.hmm = mm
        lat0 = np.radians((self.s + self.n) / 2)
        self.width_m = (self.e - self.w) * M_PER_DEG_LON_EQ * np.cos(lat0)
        self.height_m = (self.n - self.s) * 110950.0

    @property
    def z_mm_per_m(self) -> float:
        """True-scale vertical (= horizontal) mm per metre."""
        return self.wmm / self.width_m

    def to_mm(self, lon, lat):
        return ((np.asarray(lon) - self.w) / (self.e - self.w) * self.wmm,
                (np.asarray(lat) - self.s) / (self.n - self.s) * self.hmm)

    def to_lonlat(self, x, y):
        return (self.w + np.asarray(x) / self.wmm * (self.e - self.w),
                self.s + np.asarray(y) / self.hmm * (self.n - self.s))


def load_polygons(spec: str):
    """(polygons in model mm, props) from a ``city_polygons`` cache file or key prefix."""
    p = Path(spec)
    if not p.is_file():
        hits = glob.glob(str(REPO / "cache" / "city_polygons" / f"{spec}*.json.gz"))
        if len(hits) != 1:
            raise FileNotFoundError(f"{len(hits)} city_polygons entries match {spec!r}")
        p = Path(hits[0])
    with gzip.open(p, "rt", encoding="utf-8") as fh:
        d = json.load(fh)
    polys = np.array(shapely.from_wkb(d["wkb"]), dtype=object) if d["wkb"] else np.zeros(0, object)
    return polys, d["props"]


def load_truth(path: Path = TRUTH_DEFAULT) -> dict[str, float]:
    """``{osm_id: truth_m}`` of the usable truth records (one per id; the tallest survey wins)."""
    out: dict[str, float] = {}
    for rec in json.loads(Path(path).read_text(encoding="utf-8")).values():
        t, oid = rec.get("truth_m"), rec.get("osm_id")
        if oid and t is not None and t > 0:
            out[oid] = max(float(t), out.get(oid, 0.0))
    return out


def load_model_meshes(npz_path) -> dict[str, tuple[np.ndarray, np.ndarray]]:
    """``{part name: (vertices, faces)}`` of a ``city_models`` cache entry (``__merged__`` too)."""
    z = np.load(npz_path)
    names = json.loads(Path(npz_path).with_suffix(".json").read_text(encoding="utf-8"))["names"]
    return {n: (z[f"{i}_v"], z[f"{i}_f"]) for i, n in enumerate(names)}


def mesh_heights(npz_path, polys, z_mm_per_m: float) -> np.ndarray:
    """Per-polygon building height in metres read off the model mesh (NaN where it has none)."""
    from scipy.interpolate import LinearNDInterpolator
    parts = load_model_meshes(npz_path)
    bv, tv = parts["buildings"][0], parts["terrain"][0]
    tree = shapely.STRtree(polys)
    pt, pi = tree.query(shapely.points(bv[:, :2]), predicate="within")
    top = np.full(len(polys), -np.inf)
    np.maximum.at(top, pi, bv[pt, 2])
    terrain = LinearNDInterpolator(tv[:, :2], tv[:, 2])
    out = np.full(len(polys), np.nan)
    for i in np.flatnonzero(np.isfinite(top)):
        g = polys[i]
        pts = np.asarray(g.exterior.coords if g.geom_type == "Polygon" else g.representative_point().coords)
        gz = terrain(pts[:, 0], pts[:, 1])
        gz = gz[np.isfinite(gz)]
        if len(gz):
            out[i] = max(0.0, top[i] - gz.max()) / z_mm_per_m
    return out


def stats(est: np.ndarray, truth: np.ndarray) -> dict:
    err = est - truth
    n = len(err)
    if not n:
        return {"n": 0}
    return {"n": int(n), "mae_m": round(float(np.abs(err).mean()), 2), "bias_m": round(float(err.mean()), 2),
            "within_25pct": round(float((np.abs(err) <= TOL_REL * truth).mean() * 100), 1)}


def score(est: np.ndarray, sources: list[str], oids: list[str], truth: dict[str, float]) -> dict:
    """Overall and per-source stats of ``est`` against ``truth`` (paired by osm id)."""
    rows = [(e, s, truth[o]) for e, s, o in zip(est, sources, oids) if o in truth and np.isfinite(e)]
    if not rows:
        return {"overall": {"n": 0}, "by_source": {}}
    e = np.array([r[0] for r in rows])
    t = np.array([r[2] for r in rows])
    src = np.array([r[1] for r in rows])
    by = {s: stats(e[src == s], t[src == s]) for s in sorted(set(src))}
    return {"overall": stats(e, t), "by_source": by}


def exclude_sources(sources: list[str], drop) -> list[bool]:
    """Per building: kept (its ``height_source`` is not in ``drop``)."""
    drop = set(drop)
    return [s not in drop for s in sources]


def format_table(title: str, res: dict) -> str:
    lines = [title, f"{'height_source':<22}{'n':>7}{'MAE m':>8}{'bias m':>8}{'in 25%':>8}"]
    for name, s in [("ALL", res["overall"]), *res["by_source"].items()]:
        if s.get("n"):
            lines.append(f"{name:<22}{s['n']:>7}{s['mae_m']:>8}{s['bias_m']:>8}{s['within_25pct']:>7}%")
    return "\n".join(lines)


def run(polygons: str, truth_path: Path, npz: str | None, bbox, out: Path | None,
        exclude: tuple[str, ...] = ("survey",)) -> dict:
    polys, props = load_polygons(polygons)
    truth = load_truth(truth_path)
    oids = [p.get("osm_id") or "" for p in props]
    sources = [str(p.get("height_source") or "none") for p in props]
    result: dict = {"polygons": str(polygons), "npz": npz, "truth_records": len(truth),
                    "buildings": len(props), "match": "osm_id"}
    intended = np.array([p.get("height_m") if p.get("height_m") is not None else np.nan for p in props], float)
    result["intended"] = score(intended, sources, oids, truth)
    print(format_table("intended height_m vs truth", result["intended"]))
    keep = exclude_sources(sources, exclude) if exclude else None

    def held_out(est):
        k = np.array(keep)
        return score(est[k], [x for x, f in zip(sources, keep) if f],
                     [x for x, f in zip(oids, keep) if f], truth)

    if keep is not None:
        result["excluded_sources"] = list(exclude)
        result["intended_excluding"] = held_out(intended)
        print(format_table(f"intended, without {'/'.join(exclude)}", result["intended_excluding"]))
    if npz:
        zs = Georef(bbox).z_mm_per_m
        result["z_mm_per_m"] = round(float(zs), 5)
        mesh = mesh_heights(npz, polys, zs)
        result["mesh"] = score(mesh, sources, oids, truth)
        print(format_table(f"mesh-measured (z {zs:.4f} mm/m) vs truth", result["mesh"]))
        if keep is not None:
            result["mesh_excluding"] = held_out(mesh)
            print(format_table(f"mesh, without {'/'.join(exclude)}", result["mesh_excluding"]))
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--polygons", required=True, help="city_polygons cache key prefix or .json.gz path")
    ap.add_argument("--npz", help="city_models cache .npz (adds the mesh-measured reading)")
    ap.add_argument("--truth", type=Path, default=TRUTH_DEFAULT)
    ap.add_argument("--bbox", type=float, nargs=4, default=GRANADA_BBOX, metavar=("S", "W", "N", "E"))
    ap.add_argument("--out", type=Path)
    ap.add_argument("--exclude-source", nargs="*", default=["survey"], metavar="SRC",
                    help="height_source values held out of the second score (default: survey)")
    a = ap.parse_args()
    run(a.polygons, a.truth, a.npz, tuple(a.bbox), a.out, tuple(a.exclude_source))


if __name__ == "__main__":
    from city2stl.resources import scratch_guard
    scratch_guard(max_workers=2)
    main()
