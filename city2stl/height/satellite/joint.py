"""Joint lean + shadow height of one footprint (``sat_joint``; F-SKY26 step 7b, 2026-10-10).

The single-method readings pick one peak each (the roof-edge shift of the lean, the tip of the
dark run of the shadow) and the tiers vote on them (``ls``: lean and shadow within 25 % of each
other, 40 m or more). Here both are scored at every candidate height instead, with the scene's own
vectors, and the height is where the two agree:

- ``lean_evidence``: a scene's lean profile (``measure.lean_height(profile=True)``) read at the
  roof shift ``bsh + kappa_lean * k * H`` a height ``H`` would give: the gradient gain over the
  profile's median, scaled to 0..1, times the visible share.
- ``shadow_evidence``: a scene's shadow profile (``measure.shadow_height(profile=True)``) read at
  the tip ``redge + kappa_shadow * DEN * H`` (``DEN = cot(el) - lean . shadow_dir``; ``cot(el) H``
  when no roof edge was found): the brightness step at the tip times the dark purity of the run
  that leads to it times the visible share.
- scenes are averaged within a kind (shadows of one building are cut by the same podium in every
  scene: one source, decision 2026-10-07), ``J(H) = Zl(H) + Zs(H)``; the reading is the argmax,
  made only when both kinds have evidence and each supports it (``Z_SUPPORT``).

``kappa_*`` are the per-scene scale fits on anchor buildings of known height (:func:`fit_kappas`):
the vectors ``fit_lean`` / ``solve_sun`` give from the anchors set the directions and the nominal
lengths; the kappa corrects the length per metre of height a scene really shows.

Pure numpy; the profiles are the dicts ``measure`` returns.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

import numpy as np

#: Candidate heights, metres.
H_GRID = np.arange(2.5, 330.01, 0.5)
#: A kind supports the joint height when its mean evidence there is at least this.
Z_SUPPORT = 0.3
#: Lean gain over the profile median at which the evidence reaches 1 (``lean_height``'s ``gain`` term).
LEAN_GAIN_FULL = 1.6
#: Shadow tip contrast (grey levels) at which the evidence reaches 1 (``shadow_height``'s ``ct / 60``).
SHADOW_CONTRAST_FULL = 60.0
#: Scale search range and step of :func:`fit_kappas`.
KAPPA_RANGE = (0.8, 1.25)
KAPPA_STEP = 0.01
#: Fewer anchors with evidence than this leave a scene's kappa at 1.
KAPPA_MIN_ANCHORS = 5


def _f(a) -> np.ndarray:
    return np.asarray(a, dtype=np.float64)


def lean_evidence(p: Mapping | None, kappa: float = 1.0, H: np.ndarray = H_GRID) -> np.ndarray | None:
    """Evidence 0..1 of each height in ``H`` from one scene's lean profile; None without one."""
    if not p or not p.get("valid", True):
        return None
    med = float(p["med"])
    if not med > 0:
        return None
    Pr, va = _f(p["Pr"]), _f(p["va"])
    n = len(Pr)
    s = float(p["bsh"]) + kappa * float(p["k"]) * H
    idx = (s - float(p["sh0"])) / float(p["step"])
    ok = (s >= float(p["bsh"]) + float(p["dmin"])) & (idx >= 0) & (idx <= n - 1)
    out = np.zeros(len(H))
    if not ok.any():
        return out
    i0 = np.floor(idx[ok]).astype(int)
    i1 = np.minimum(i0 + 1, n - 1)
    f = idx[ok] - i0
    a, b = Pr[i0], Pr[i1]
    pr = np.where(np.isnan(a), b, np.where(np.isnan(b), a, a * (1 - f) + b * f))
    v = np.nan_to_num(np.where(np.isnan(va[i0]), va[i1], va[i0]))
    gain = np.nan_to_num(pr / med, nan=0.0)
    z = np.clip((gain - 1.0) / (LEAN_GAIN_FULL - 1.0), 0, 1) * np.clip(v / 0.7, 0, 1)
    out[ok] = z
    return out


def shadow_evidence(p: Mapping | None, kappa: float = 1.0, H: np.ndarray = H_GRID) -> np.ndarray | None:
    """Evidence 0..1 of each height in ``H`` from one scene's shadow profile; None without one
    (no dark run found, or one refused for a merged shadow or vegetation)."""
    if not p or not p.get("valid", True):
        return None
    D, Bd, vf = _f(p["D"]), _f(p["Bd"]), _f(p["vf"])
    n = len(D)
    start = int(p["start"])
    t = (float(p["redge"]) + kappa * float(p["den"]) * H) if p.get("roof_ok") else kappa * float(p["cot"]) * H
    idx = np.rint((t - float(p["ss0"])) / float(p["step"])).astype(int)
    ok = (idx > start + 1) & (idx < n - 1)
    out = np.zeros(len(H))
    if not ok.any():
        return out
    cs = np.concatenate([[0.0], np.cumsum(np.nan_to_num(Bd))])
    cnt = np.concatenate([[0.0], np.cumsum(~np.isnan(Bd))])
    ii = idx[ok]
    m = cnt[ii + 1] - cnt[start]
    purity = np.where(m >= 3, (cs[ii + 1] - cs[start]) / np.maximum(m, 1), 0.0)
    contrast = np.nanmax(np.stack([D[ii - 1], D[ii], D[ii + 1]]), axis=0) if np.isfinite(D[ii]).any() \
        else np.zeros(len(ii))
    contrast = np.nan_to_num(contrast, nan=0.0)
    v = np.nan_to_num(vf[ii])
    out[ok] = np.clip(contrast / SHADOW_CONTRAST_FULL, 0, 1) * purity * np.clip(v / 0.7, 0, 1)
    return out


def scene_evidence(profiles: Mapping[str, Mapping], kappas: Mapping[str, Sequence[float]] | None = None,
                   H: np.ndarray = H_GRID) -> tuple[np.ndarray | None, np.ndarray | None, int, int]:
    """``(Zl, Zs, n_lean, n_shadow)``: the scene-averaged lean and shadow evidence of one footprint
    (None for a kind no scene has a profile of). ``profiles``: ``{scene: {"lean", "shadow"}}``;
    ``kappas``: ``{scene: (kappa_lean, kappa_shadow)}`` (default 1, 1)."""
    zl, zs = [], []
    for scene, pr in profiles.items():
        kl, ks = (kappas or {}).get(scene, (1.0, 1.0))
        a = lean_evidence(pr.get("lean"), kl, H)
        b = shadow_evidence(pr.get("shadow"), ks, H)
        if a is not None:
            zl.append(a)
        if b is not None:
            zs.append(b)
    return (np.mean(zl, 0) if zl else None, np.mean(zs, 0) if zs else None, len(zl), len(zs))


def joint_reading(profiles: Mapping[str, Mapping], kappas: Mapping[str, Sequence[float]] | None = None,
                  H: np.ndarray = H_GRID) -> dict | None:
    """The joint height of one footprint: ``{height_m, J, zl, zs, j2, n_lean, n_shadow}`` or None.
    None unless a lean and a shadow profile exist and each kind's mean evidence at the argmax is at
    least :data:`Z_SUPPORT`. ``j2``: the best J at a height more than max(3 m, 15 %) from the argmax
    (a second peak: how ambiguous the reading is)."""
    Zl, Zs, nl, ns = scene_evidence(profiles, kappas, H)
    if Zl is None or Zs is None:
        return None
    J = Zl + Zs
    k = int(np.argmax(J))
    h = float(H[k])
    if J[k] <= 0 or Zl[k] < Z_SUPPORT or Zs[k] < Z_SUPPORT:
        return None
    far = np.abs(H - h) > max(3.0, 0.15 * h)
    return dict(height_m=h, J=float(J[k]), zl=float(Zl[k]), zs=float(Zs[k]),
                j2=float(J[far].max()) if far.any() else 0.0, n_lean=nl, n_shadow=ns)


def fit_kappas(anchors: Sequence[tuple[Mapping[str, Mapping], float]], scenes: Sequence[str],
               grid: np.ndarray | None = None) -> dict:
    """Per-scene scale fits on anchors: ``anchors`` = ``[(profiles, truth_m)]``. For each scene and
    kind, the kappa in :data:`KAPPA_RANGE` that maximises the mean evidence at the anchors' truth
    heights. Returns ``{scene: {"lean": k, "shadow": k, "n_lean": n, "n_shadow": n,
    "gain_lean": g, "gain_shadow": g}}`` (``gain``: mean evidence at the fitted kappa less that at 1);
    a kind with under :data:`KAPPA_MIN_ANCHORS` anchors with evidence keeps 1.0."""
    ks = grid if grid is not None else np.arange(KAPPA_RANGE[0], KAPPA_RANGE[1] + 1e-9, KAPPA_STEP)
    out = {}
    for sc in scenes:
        rec = {}
        for kind, fn in (("lean", lean_evidence), ("shadow", shadow_evidence)):
            rows = [(pr[sc][kind], t) for pr, t in anchors if sc in pr and pr[sc].get(kind)
                    and pr[sc][kind].get("valid", True)]
            best, base, n = 1.0, 0.0, len(rows)
            if n >= KAPPA_MIN_ANCHORS:
                tab = np.array([[float(np.interp(t, H_GRID, fn(p, k, H_GRID))) for p, t in rows] for k in ks])
                score = tab.mean(1)
                base = float(score[int(np.argmin(np.abs(ks - 1.0)))])
                kb = float(ks[int(np.argmax(score))])
                best = kb if score.max() > base + 1e-6 else 1.0
                rec[f"gain_{kind}"] = float(score.max() - base)
            rec[kind], rec[f"n_{kind}"] = best, n
        out[sc] = rec
    return out


# ------------------------------------------------------------------ confidence calibration
#: Height bands of the confidence table, by the joint height: (upper bound m, name).
CONF_BANDS = ((15.0, "<15"), (40.0, "15-40"), (math.inf, ">=40"))
#: J buckets (lower edges) of the confidence table.
J_EDGES = (0.0, 0.8, 1.0, 1.2, 1.4, 1.6)


def conf_cell(height_m: float, J: float) -> tuple[str, int]:
    band = next(name for top, name in CONF_BANDS if height_m < top)
    return band, max(i for i, e in enumerate(J_EDGES) if J >= e)


def fit_conf_table(rows: Sequence[tuple], min_n: float = 8) -> dict:
    """``{(band, J bucket): share within tol}`` from ``[(height_m, J, within_tol[, weight])]``; a cell
    with under ``min_n`` weight borrows the pooled share of its band and the buckets above it (so
    the table is monotone in J)."""
    cells: dict = {}
    for r in rows:
        h, J, ok = r[:3]
        w = float(r[3]) if len(r) > 3 else 1.0
        cells.setdefault(conf_cell(h, J), []).append((bool(ok), w))
    table = {}

    def share(vs):
        n = sum(w for _, w in vs)
        return (sum(w for ok, w in vs if ok) / n if n else 0.0), n

    for _, band in CONF_BANDS:
        for b in range(len(J_EDGES)):
            s, n = share(cells.get((band, b), []))
            if n >= min_n:
                table[(band, b)] = s
            else:
                pool = [v for (bd, bb), vs in cells.items() if bd == band and bb >= b for v in vs]
                s, n = share(pool)
                table[(band, b)] = s if n >= min_n else 0.0
    return table


#: The confidence table: the share within max(25 %, 2 m) of LiDAR truth of the joint readings of
#: each (height band, J bucket) cell, fitted on Honolulu (1,805 footprints), San Juan (4,058) and
#: Fort Lauderdale (1,844) with the scenes' kappas fitted on each city's anchors (2026-10-10;
#: low-truth footprints weighted back from their hash sample). No cell reaches 0.8 (the best,
#: 15-40 m with J 1.4-1.6, is 0.61 on a few dozen readings), so...
CONF_TABLE: dict = {
    ("<15", 0): 0.47,
    ("<15", 1): 0.46,
    ("<15", 2): 0.55,
    ("<15", 3): 0.57,
    ("<15", 4): 0.5,
    ("<15", 5): 0.67,
    ("15-40", 0): 0.11,
    ("15-40", 1): 0.23,
    ("15-40", 2): 0.3,
    ("15-40", 3): 0.5,
    ("15-40", 4): 0.61,
    ("15-40", 5): 0.0,
    (">=40", 0): 0.39,
    (">=40", 1): 0.34,
    (">=40", 2): 0.25,
    (">=40", 3): 0.34,
    (">=40", 4): 0.0,
    (">=40", 5): 0.0,
}
#: ...no reading is published: ``C_PUB`` (the confidence from which a sat_joint reading counts: the
#: smallest at which the training share within tol is 0.8 or more) does not exist, and stays above 1.
#: The marks of the anchors test failed (decisions/building-heights.md, 2026-10-10).
C_PUB = 1.01


def conf_of(table: Mapping, height_m: float, J: float) -> float:
    """The calibrated confidence of a joint reading: the table's share within tol (0 if unknown)."""
    return float(table.get(conf_cell(height_m, J), 0.0))


@dataclass
class JointModel:
    """What turns a footprint's profiles into a ``sat_joint`` reading: the per-scene kappas
    (``{scene: (kappa_lean, kappa_shadow)}``, :func:`fit_kappas` on the region's anchors), the
    confidence ``table`` (:func:`fit_conf_table`; empty: every confidence 0) and ``c_pub``, the
    confidence from which the reading counts (the readings below it are still stored)."""
    kappas: dict
    table: dict
    c_pub: float = 0.0


def make_reading(profiles: Mapping[str, Mapping], model: JointModel) -> dict | None:
    """``{height_m, conf, extra}`` of one footprint under ``model``, or None."""
    jr = joint_reading(profiles, model.kappas)
    if jr is None:
        return None
    return dict(height_m=jr["height_m"], conf=conf_of(model.table, jr["height_m"], jr["J"]),
                extra={k: round(jr[k], 3) for k in ("J", "zl", "zs", "j2")} | {
                    "n_lean": jr["n_lean"], "n_shadow": jr["n_shadow"]})


__all__ = ["H_GRID", "Z_SUPPORT", "lean_evidence", "shadow_evidence", "scene_evidence", "joint_reading",
           "fit_kappas", "conf_cell", "fit_conf_table", "conf_of", "JointModel", "make_reading", "CONF_BANDS", "J_EDGES",
           "KAPPA_RANGE", "KAPPA_MIN_ANCHORS"]
