"""Group skyline photos taken from the same spot (F-WEB2 step G).

User idea (2026-10-04): pre-group photos without a location by similarity, so a group is
located together; a located member passes its camera to the rest, and a group with none gets
one joint search.

Similarity is the skyline outline, not image features: two photos from the same spot show the
same tower outline up to a zoom ``s`` and a shift (columns ``x_b = s * x_a + t``, rows scaled by
the same ``s`` plus a tilt offset), whatever the year, light or camera. SIFT matched only copies
of one shot.

    sim = outline_similarity(ya, wa, yb, wb)      # best (misfit, s, t, overlap)
    groups = group_photos(profiles, max_misfit=0.15)

Outlines are resampled to ``N_COLS`` columns with rows in the same units, so a pair is scored
for every shift at once by FFT cross-correlation of the masked outlines (as in
``skyline_match``), over zooms from 1/3 to 3.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

N_COLS = 600
#: Zoom ratios tried between two photos (log-spaced, 1/3 to 3).
SCALES = np.exp(np.linspace(math.log(1 / 3), math.log(3), 31))


@dataclass(frozen=True)
class Similarity:
    misfit: float        # 1 - R^2 of B's outline against A's over the overlap; 0 identical
    scale: float         # resampling B by ``scale`` aligns it with A: B is zoomed 1/scale vs A
    shift: float         # where B's left edge lands on A, in A's widths
    overlap: float       # share of A's usable outline covered by B


def _resample(y_top: np.ndarray, width: int, scale: float = 1.0):
    """Outline on ``N_COLS * scale`` columns, rows in the same units (NaN = not usable)."""
    n = max(8, int(round(N_COLS * scale)))
    xs = (np.arange(n) + 0.5) * width / n
    src = np.floor(xs).astype(int).clip(0, width - 1)
    y = y_top[src] * (n / width)
    return y


def _xcorr(a, b, size):
    fa = np.fft.rfft(a, size)
    fb = np.fft.rfft(b, size)
    return np.fft.irfft(fa * np.conj(fb), size)


def outline_similarity(ya: np.ndarray, wa: int, yb: np.ndarray, wb: int,
                       min_overlap: float = 0.4, min_cols: int = 60) -> Similarity | None:
    """Best zoom and shift aligning photo B's outline to photo A's, and how well they agree.

    ``min_overlap``: share of A's usable columns that B must cover, so a sliver cannot match.
    """
    a = _resample(ya, wa)
    ma = np.isfinite(a)
    if ma.sum() < min_cols:
        return None
    a0 = np.where(ma, a, 0.0)
    best = None
    step = float(np.log(SCALES[1] / SCALES[0]))
    coarse = _scan(a0, ma, yb, wb, SCALES, min_overlap, min_cols)
    if coarse is None:
        return None
    # a 7 % zoom step leaves ~3 % error, i.e. up to ~18 of 600 columns off at the edges
    fine = np.exp(np.linspace(math.log(coarse.scale) - step, math.log(coarse.scale) + step, 13))
    best = _scan(a0, ma, yb, wb, fine, min_overlap, min_cols)
    return best if best is not None and best.misfit <= coarse.misfit else coarse


def _scan(a0, ma, yb, wb, scales, min_overlap, min_cols) -> Similarity | None:
    a = a0
    best = None
    for s in scales:
        b = _resample(yb, wb, s)
        mb = np.isfinite(b)
        if mb.sum() < min_cols:
            continue
        b0 = np.where(mb, b, 0.0)
        size = 1 << int(math.ceil(math.log2(len(a) + len(b))))
        wa_, wb_ = ma.astype(float), mb.astype(float)
        # sums over the overlap for every shift k (B placed at A column k)
        N = _xcorr(wa_, wb_, size)
        SA = _xcorr(wa_ * a0, wb_, size)
        SAA = _xcorr(wa_ * a0 * a0, wb_, size)
        SB = _xcorr(wa_, wb_ * b0, size)
        SBB = _xcorr(wa_, wb_ * b0 * b0, size)
        SAB = _xcorr(wa_ * a0, wb_ * b0, size)
        N = np.round(N)
        ok = N >= max(min_cols, min_overlap * ma.sum())
        if not ok.any():
            continue
        Nn = np.where(ok, N, 1.0)
        ssd = SAA + SBB - 2 * SAB - (SA - SB) ** 2 / Nn
        var_a = SAA - SA ** 2 / Nn
        mis = np.where(ok & (var_a > 1e-6), ssd / np.maximum(var_a, 1e-6), np.inf)
        k = int(np.argmin(mis))
        if not np.isfinite(mis[k]):
            continue
        shift = k if k < size // 2 else k - size
        if best is None or mis[k] < best.misfit:
            best = Similarity(float(mis[k]), float(s), float(shift) / N_COLS,
                              float(N[k] / ma.sum()))
    return best


def group_photos(keys: list[str], profiles: dict[str, tuple[np.ndarray, int]],
                 max_misfit: float = 0.15, **kw) -> tuple[list[list[str]], dict]:
    """Connected groups of photos whose outlines agree (either direction) below ``max_misfit``.

    Returns the groups (largest first) and the pairwise similarities that formed edges.
    """
    parent = {k: k for k in keys}

    def find(k):
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    edges = {}
    for i, ka in enumerate(keys):
        ya, wa = profiles[ka]
        for kb in keys[i + 1:]:
            yb, wb = profiles[kb]
            s1 = outline_similarity(ya, wa, yb, wb, **kw)
            s2 = outline_similarity(yb, wb, ya, wa, **kw)
            best = min((s for s in (s1, s2) if s is not None), key=lambda s: s.misfit, default=None)
            if best is not None and best.misfit <= max_misfit:
                edges[(ka, kb)] = best
                parent[find(ka)] = find(kb)
    groups: dict[str, list[str]] = {}
    for k in keys:
        groups.setdefault(find(k), []).append(k)
    return sorted(groups.values(), key=len, reverse=True), edges
