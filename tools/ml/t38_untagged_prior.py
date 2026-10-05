"""T38 (blind ML review): a height prior for untagged buildings.

Data: docs/research/ml-blind-2026-10-05/buildings.csv (confirmed truth, 8 cities). Predicts
``truth_m`` for untagged buildings (``height_source == "default"``), leave-one-city-out,
against a constant (median of the training cities' untagged truth) and the Street View
height. Small CPU models only. Run:

    python tools/ml/t38_untagged_prior.py [--out results.json]

Prints per-city and pooled MAE, median absolute error and share within 25 %.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.linear_model import QuantileRegressor

DATA = Path(__file__).resolve().parents[2] / "docs/research/ml-blind-2026-10-05/buildings.csv"
M_PER_DEG = 111_320.0


def neighbour_features(df: pd.DataFrame, radii=(150.0, 400.0)) -> pd.DataFrame:
    """Per building, from the *other* buildings of its city: how many lie within each
    radius, and the median OSM height tag of the tagged ones (tags are known at run time;
    truth is never used)."""
    out = pd.DataFrame(index=df.index)
    for _city, g in df.groupby("city"):
        lat0 = g.lat.mean()
        xy = np.c_[(g.lon - g.lon.mean()) * M_PER_DEG * np.cos(np.radians(lat0)),
                   (g.lat - lat0) * M_PER_DEG]
        d = np.hypot(*(xy[:, None, :] - xy[None, :, :]).transpose(2, 0, 1))
        np.fill_diagonal(d, np.inf)
        tag = g.osm_tag_m.to_numpy()
        for r in radii:
            near = d <= r
            out.loc[g.index, f"n_within_{int(r)}"] = near.sum(1)
            med = [np.nanmedian(tag[row]) if np.isfinite(tag[row]).any() else np.nan
                   for row in near]
            out.loc[g.index, f"tag_median_{int(r)}"] = med
    return out


def features(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    f["log_area"] = np.log(df.area_m2.clip(lower=1))
    f["log_perim"] = np.log(df.perimeter_m.clip(lower=1))
    # 1 for a circle, lower for long or ragged footprints
    f["compact"] = 4 * np.pi * df.area_m2 / df.perimeter_m.clip(lower=1) ** 2
    f["n_vertices"] = df.n_vertices
    nb = neighbour_features(df)
    return f.join(nb)


SV_COLS = ["sv_height_m", "sv_n_views", "sv_n_seeds", "sv_mad_m", "sv_confidence"]


def scores(pred: np.ndarray, truth: np.ndarray) -> dict:
    err = np.abs(pred - truth)
    return {"n": int(truth.size), "mae": round(float(err.mean()), 2),
            "median_ae": round(float(np.median(err)), 2),
            "within_25": round(float(np.mean(err <= 0.25 * truth)), 3)}


def run() -> dict:
    df = pd.read_csv(DATA)
    feat = features(df)
    geo_cols = list(feat.columns)
    X_all = feat.join(df[SV_COLS])
    untag = df.height_source == "default"
    y = df.truth_m.to_numpy()
    shape_cols = ["log_area", "log_perim", "compact", "n_vertices"]
    preds: dict[str, np.ndarray] = {k: np.full(len(df), np.nan) for k in (
        "constant", "street_view", "loglinear_area", "gbm_shape_only", "gbm_geometry",
        "gbm_geometry_sv", "gbm_geometry_all_rows")}
    constants = {}
    for city in sorted(df.city.unique()):
        test = untag & (df.city == city)
        if not test.any():
            continue
        train_u = untag & (df.city != city)
        train_all = df.city != city
        t = test.to_numpy()
        constants[city] = round(float(np.median(y[train_u])), 2)
        preds["constant"][t] = constants[city]
        preds["street_view"][t] = df.sv_height_m[test]
        # median regression of log height on log area: one slope, one intercept
        qr = QuantileRegressor(quantile=0.5, alpha=0.0, solver="highs")
        qr.fit(feat.loc[train_u, ["log_area"]], np.log(y[train_u]))
        preds["loglinear_area"][t] = np.exp(qr.predict(feat.loc[test, ["log_area"]]))
        # gradient boosting on log height, absolute loss (median), small and shallow
        for name, cols, rows in (("gbm_shape_only", shape_cols, train_u),
                                 ("gbm_geometry", geo_cols, train_u),
                                 ("gbm_geometry_sv", geo_cols + SV_COLS, train_u),
                                 ("gbm_geometry_all_rows", geo_cols, train_all)):
            m = HistGradientBoostingRegressor(loss="absolute_error", max_depth=3,
                                              learning_rate=0.05, max_iter=200,
                                              min_samples_leaf=20, random_state=0)
            m.fit(X_all.loc[rows, cols], np.log(y[rows]))
            preds[name][t] = np.exp(m.predict(X_all.loc[test, cols]))
    res = {"per_city": {}, "pooled": {}, "bands": {}, "constants": constants}
    u = untag.to_numpy()
    for lo, hi in ((0, 30), (30, 60), (60, np.inf)):
        m = u & (y >= lo) & (y < hi)
        lab = f"{lo}-{hi:.0f}m" if np.isfinite(hi) else f"{lo}+m"
        res["bands"][lab] = {name: scores(p[m], y[m]) for name, p in preds.items()}
    for name, p in preds.items():
        res["pooled"][name] = scores(p[u], y[u])
        for city in sorted(df.city[untag].unique()):
            m = u & (df.city == city).to_numpy()
            res["per_city"].setdefault(city, {})[name] = scores(p[m], y[m])
    # macro average over cities with >= 20 untagged buildings (small cities are noise)
    big = [c for c in res["per_city"] if res["per_city"][c]["constant"]["n"] >= 20]
    res["macro_mae_cities_n20"] = {
        name: round(float(np.mean([res["per_city"][c][name]["mae"] for c in big])), 2)
        for name in preds}
    res["cities_n20"] = big
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    res = run()
    names = list(res["pooled"])
    print(f"{'city':22s}" + "".join(f"{n[:16]:>18s}" for n in names))
    for city, r in list(res["per_city"].items()) + [("POOLED", res["pooled"])]:
        print(f"{city[:22]:22s}" + "".join(
            f"{r[n]['mae']:>8.1f}/{r[n]['within_25']:<9.2f}" for n in names))
    print("macro MAE (cities with >= 20):", res["macro_mae_cities_n20"])
    for band, r in res["bands"].items():
        print(f"band {band:8s} n={r['constant']['n']:4d}  " + "  ".join(
            f"{n}={r[n]['mae']:.1f}" for n in names))
    print("constant per held-out city:", res["constants"])
    if args.out:
        args.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
