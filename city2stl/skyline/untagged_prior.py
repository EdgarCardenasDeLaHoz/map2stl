"""Height prior for buildings without an OSM height tag (T41, from the T38 blind review).

T28 withholds Street View heights on untagged buildings (they read 65-113 m too tall). This is
the replacement: a small gradient-boosting model of log height on the footprint's shape and
the OSM height tags of its tagged neighbours, trained on the benchmark cities' confirmed
untagged buildings (``data/untagged_prior_train.csv``, exported from
``docs/research/ml-blind-2026-10-05/buildings.csv``). Leave-one-city-out it scores MAE 9.5 m
against 11.2 m for a 12 m constant and 91 m for Street View
(``docs/research/ml-blind-2026-10-05/T38-results.md``,
``tools/ml/t38_untagged_prior.py``).

Features (``features``), all known at run time, never truth:

- log area, log perimeter, compactness (4 pi A / P^2), vertex count;
- median OSM height tag of the *tagged* buildings within 150 m and within 400 m (NaN when
  there are none). Neighbours come from the set of buildings passed in: the buildings the
  Street View run estimated, as in training. Neighbour counts (T38's other features) are
  left out: they depend on how many buildings that set holds.

    predict(rows, exclude_city)     # rows: dicts with lat, lon, area_m2, perimeter_m,
                                    #       n_vertices, osm_tag_m, height_source
    fallback_for(records, region)   # BuildingRecord -> (height, "prior_gbm") for T28
    report_rows(buildings)          # heights.json rows -> predict rows (benchmark re-score)

A benchmark city is left out of the training when it is the region being built or scored
(``exclude_city``), so its scores stay out-of-sample.
"""

from __future__ import annotations

import functools
import math
from pathlib import Path

import numpy as np

TRAIN_CSV = Path(__file__).resolve().parent / "data" / "untagged_prior_train.csv"
TAGGED_SOURCES = ("osm_tag", "osm_levels")
RADII_M = (150.0, 400.0)
FEATURES = ["log_area", "log_perim", "compact", "n_vertices",
            "tag_median_150", "tag_median_400"]
M_PER_DEG = 111_320.0


def features(lat, lon, area_m2, perimeter_m, n_vertices, tag_m, source) -> np.ndarray:
    """``(n, len(FEATURES))`` array for buildings given as parallel sequences. ``tag_m`` and
    ``source`` give each building's OSM height and its provenance; a building counts as a
    tagged neighbour when its source is in ``TAGGED_SOURCES`` and its tag is finite."""
    lat, lon = np.asarray(lat, float), np.asarray(lon, float)
    area = np.clip(np.asarray(area_m2, float), 1.0, None)
    perim = np.clip(np.asarray(perimeter_m, float), 1.0, None)
    tag = np.asarray(tag_m, float)
    tagged = np.isin(np.asarray(source, object), TAGGED_SOURCES) & np.isfinite(tag)
    n = lat.size
    out = np.full((n, len(FEATURES)), np.nan)
    out[:, 0], out[:, 1] = np.log(area), np.log(perim)
    out[:, 2] = 4 * math.pi * area / perim ** 2
    out[:, 3] = np.asarray(n_vertices, float)
    if n:
        kx = M_PER_DEG * math.cos(math.radians(float(np.mean(lat))))
        x, y = (lon - lon.mean()) * kx, (lat - lat.mean()) * M_PER_DEG
        tx, ty, tv = x[tagged], y[tagged], tag[tagged]
        for i in range(n):
            if not tv.size:
                break
            d = np.hypot(tx - x[i], ty - y[i])
            d[(tx == x[i]) & (ty == y[i])] = np.inf      # not its own tag
            for k, r in enumerate(RADII_M):
                near = tv[d <= r]
                if near.size:
                    out[i, 4 + k] = float(np.median(near))
    return out


@functools.lru_cache(maxsize=4)
def _model(exclude_city: str | None = None):
    """The model fitted on ``TRAIN_CSV`` (about a second; cached per process). With
    ``exclude_city`` that city's buildings are left out, so a benchmark city is never
    scored in-sample (city names as in the CSV: ``miami``, ``la_defense``, ...)."""
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingRegressor

    df = pd.read_csv(TRAIN_CSV)
    X = np.full((len(df), len(FEATURES)), np.nan)
    for _city, g in df.groupby("city"):           # neighbours never cross cities
        X[g.index] = features(g.lat, g.lon, g.area_m2, g.perimeter_m, g.n_vertices,
                              g.osm_tag_m, g.height_source)
    untag = ((df.height_source == "default") & (df.city != (exclude_city or ""))).to_numpy()
    m = HistGradientBoostingRegressor(loss="absolute_error", max_depth=3, learning_rate=0.05,
                                      max_iter=200, min_samples_leaf=20, random_state=0)
    m.fit(X[untag], np.log(df.truth_m.to_numpy()[untag]))
    return m


def predict(rows: list[dict], exclude_city: str | None = None) -> np.ndarray:
    """Predicted height (m) per row; neighbours are the other rows. ``exclude_city``: see
    ``_model`` (pass the region being built or scored)."""
    if not rows:
        return np.zeros(0)
    X = features([r["lat"] for r in rows], [r["lon"] for r in rows],
                 [r["area_m2"] for r in rows], [r["perimeter_m"] for r in rows],
                 [r["n_vertices"] for r in rows],
                 [np.nan if r.get("osm_tag_m") is None else r["osm_tag_m"] for r in rows],
                 [r.get("height_source") for r in rows])
    return np.exp(_model((exclude_city or "").lower() or None).predict(X))


def ring_metrics(lonlat) -> tuple[float, float, int]:
    """``(area_m2, perimeter_m, n_vertices)`` of a lon/lat ring (closed or not), in a local
    equirectangular frame."""
    pts = np.asarray(lonlat, float)
    if len(pts) > 1 and np.allclose(pts[0], pts[-1]):
        pts = pts[:-1]
    kx = M_PER_DEG * math.cos(math.radians(float(pts[:, 1].mean())))
    x, y = pts[:, 0] * kx, pts[:, 1] * M_PER_DEG
    x, y = x - x.mean(), y - y.mean()
    area = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    perim = float(np.hypot(np.diff(np.r_[x, x[:1]]), np.diff(np.r_[y, y[:1]])).sum())
    return float(area), perim, int(len(pts))


def rows_from_records(records) -> list[dict]:
    """``predict`` rows for ``BuildingRecord``s (area from the record, perimeter and vertex
    count from its exterior ring)."""
    rows = []
    for r in records:
        try:
            ring = list(r.geometry.exterior.coords)
            _, perim, nv = ring_metrics(ring)
        except Exception:  # noqa: BLE001 - a record without a polygon gets a neutral shape
            perim, nv = 4.0 * math.sqrt(max(r.area_m2, 1.0)), 4
        rows.append({"lat": r.centroid_lat, "lon": r.centroid_lon, "area_m2": r.area_m2,
                     "perimeter_m": perim, "n_vertices": nv, "osm_tag_m": r.height_tag_m,
                     "height_source": r.height_source})
    return rows


def fallback_for(records, exclude_city: str | None = None):
    """A ``withhold_untagged_street_view`` fallback: the prior's height for each of
    ``records`` (computed once, neighbours = these records), source ``"prior_gbm"``."""
    records = list(records)
    pred = dict(zip((r.feature_id for r in records),
                    predict(rows_from_records(records), exclude_city), strict=True))
    return lambda rec: (float(pred[rec.feature_id]) if rec.feature_id in pred else None,
                        "prior_gbm")


def report_rows(buildings: list[dict]) -> list[dict]:
    """``predict`` rows for ``heights.json`` buildings (``load_report``): centroid, area,
    ``height_tag_m`` / ``height_source``, perimeter and vertices from ``footprint_lonlat``."""
    rows = []
    for b in buildings:
        area, perim, nv = ring_metrics(b["footprint_lonlat"])
        rows.append({"lat": b.get("centroid_lat"), "lon": b.get("centroid_lon"),
                     "area_m2": b.get("area_m2") or area, "perimeter_m": perim,
                     "n_vertices": nv, "osm_tag_m": b.get("height_tag_m"),
                     "height_source": b.get("height_source")})
    return rows
