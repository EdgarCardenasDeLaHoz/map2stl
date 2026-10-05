"""T37 (blind ML review): which photo tower readings are wrong?

Data: docs/research/ml-blind-2026-10-05/photo_readings.csv (2,791 readings with confirmed
truth, Miami and Chicago). A reading is wrong when ``|photo_m - truth_m| > 0.25 * truth_m``.
Train on one city, test on the other (both directions); report AUC, precision and recall of
"wrong", and the MAE of the readings kept against all readings. Small CPU models only. Run:

    python tools/ml/t37_photo_reading_errors.py [--out results.json]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import roc_auc_score
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

DATA = Path(__file__).resolve().parents[2] / "docs/research/ml-blind-2026-10-05/photo_readings.csv"
WRONG_REL = 0.25


def features(d: pd.DataFrame) -> pd.DataFrame:
    """Per reading; never uses truth. The photo-level terms leave the reading itself out."""
    f = pd.DataFrame(index=d.index)
    f["log_ratio_osm"] = np.log(d.photo_m.clip(lower=1) / d.osm_m.clip(lower=1))
    f["abs_log_ratio_osm"] = f.log_ratio_osm.abs()
    other = d.other_photos_median_m
    f["abs_log_ratio_other"] = np.log(d.photo_m.clip(lower=1) / other.clip(lower=1)).abs()
    f["has_other"] = other.notna().astype(float)
    f["abs_log_ratio_other"] = f.abs_log_ratio_other.fillna(0.0)
    f["n_other_photos"] = d.n_other_photos
    f["log_dist"] = np.log(d.dist_m.clip(lower=1))
    f["width_deg"] = d.width_deg
    f["osm_angle_deg"] = d.osm_angle_deg
    f["prominence_deg"] = d.prominence_deg
    # photo quality: how far the photo's *other* towers sit from their tags (leave one out)
    g = f.abs_log_ratio_osm.groupby([d.city, d.photo])
    n = g.transform("size")
    s = g.transform("sum")
    f["photo_dev_others"] = ((s - f.abs_log_ratio_osm) / (n - 1).clip(lower=1)).where(n > 1, 0.0)
    f["photo_n_towers"] = n
    return f


def _metrics(wrong_pred, p, d, y) -> dict:
    err = (d.photo_m - d.truth_m).abs().to_numpy()
    keep = ~wrong_pred
    tp = int((wrong_pred & y).sum())
    return {
        "auc": None if p is None else round(float(roc_auc_score(y, p)), 3),
        "precision_wrong": round(tp / max(1, int(wrong_pred.sum())), 3),
        "recall_wrong": round(tp / max(1, int(y.sum())), 3),
        "flagged_share": round(float(wrong_pred.mean()), 3),
        "mae_all": round(float(err.mean()), 2),
        "mae_kept": round(float(err[keep].mean()), 2) if keep.any() else None,
        "wrong_share_kept": round(float(y[keep].mean()), 3) if keep.any() else None,
    }


def _threshold(p_train: np.ndarray, y_train: np.ndarray) -> float:
    """The cut that maximises F1 of "wrong" on the training city."""
    best, cut = -1.0, 0.5
    for c in np.unique(np.round(p_train, 3)):
        pred = p_train >= c
        tp = (pred & y_train).sum()
        f1 = 2 * tp / max(1, pred.sum() + y_train.sum())
        if f1 > best:
            best, cut = f1, float(c)
    return cut


def run() -> dict:
    d = pd.read_csv(DATA)
    y_all = ((d.photo_m - d.truth_m).abs() > WRONG_REL * d.truth_m).to_numpy()
    f = features(d)
    cols_all = list(f.columns)
    cols_geom = ["log_dist", "width_deg", "osm_angle_deg", "prominence_deg", "n_other_photos",
                 "photo_n_towers"]
    res: dict = {"n": {c: int((d.city == c).sum()) for c in d.city.unique()},
                 "wrong_share": {c: round(float(y_all[d.city == c].mean()), 3)
                                 for c in d.city.unique()},
                 "directions": {}}
    for train_c, test_c in (("chicago", "miami"), ("miami", "chicago")):
        tr = (d.city == train_c).to_numpy()
        te = (d.city == test_c).to_numpy()
        dt, yt, yr = d[te], y_all[te], y_all[tr]
        out = {}
        # rule: the reading disagrees with the tower's own OSM tag by more than 25 %
        rule = f.abs_log_ratio_osm[te].to_numpy() > np.log(1.25)
        out["rule_osm_25pct"] = _metrics(rule, f.abs_log_ratio_osm[te].to_numpy(), dt, yt)
        rule2 = rule | ((f.has_other[te] > 0) & (f.abs_log_ratio_other[te] > np.log(1.25))).to_numpy()
        out["rule_osm_or_other_25pct"] = _metrics(rule2, None, dt, yt)
        models = {
            "logistic_all": (make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000)),
                             cols_all),
            "gbm_all": (HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05,
                                                       max_iter=200, min_samples_leaf=20,
                                                       random_state=0), cols_all),
            "gbm_no_tag": (HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05,
                                                          max_iter=200, min_samples_leaf=20,
                                                          random_state=0),
                           [c for c in cols_all if c not in ("log_ratio_osm",
                                                             "abs_log_ratio_osm",
                                                             "photo_dev_others")]),
            "gbm_geometry_only": (HistGradientBoostingClassifier(
                max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=20,
                random_state=0), cols_geom),
        }
        for name, (m, cols) in models.items():
            m.fit(f.loc[tr, cols], yr)
            p_tr = m.predict_proba(f.loc[tr, cols])[:, 1]
            p_te = m.predict_proba(f.loc[te, cols])[:, 1]
            cut = _threshold(p_tr, yr)
            out[name] = {**_metrics(p_te >= cut, p_te, dt, yt), "threshold": round(cut, 3)}
        # what replacing the reading by the OSM tag would give (the tag is an input here)
        err_tag = (dt.osm_m - dt.truth_m).abs()
        out["osm_tag_instead"] = {"mae": round(float(err_tag.mean()), 2),
                                  "wrong_share": round(float(
                                      (err_tag > WRONG_REL * dt.truth_m).mean()), 3)}
        res["directions"][f"{train_c}->{test_c}"] = out
    return res


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    res = run()
    print("wrong share:", res["wrong_share"], "n:", res["n"])
    for direction, out in res["directions"].items():
        print(f"\n== train {direction}")
        print(f"{'method':26s} {'AUC':>6s} {'prec':>6s} {'rec':>6s} {'flag':>6s} "
              f"{'MAEall':>7s} {'MAEkept':>8s} {'wrongKept':>9s}")
        for name, m in out.items():
            if name == "osm_tag_instead":
                print(f"{name:26s} MAE {m['mae']}  wrong {m['wrong_share']}")
                continue
            print(f"{name:26s} {m['auc'] if m['auc'] is not None else '-':>6} "
                  f"{m['precision_wrong']:>6} {m['recall_wrong']:>6} {m['flagged_share']:>6} "
                  f"{m['mae_all']:>7} {m['mae_kept']:>8} {m['wrong_share_kept']:>9}")
    if args.out:
        args.out.write_text(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
