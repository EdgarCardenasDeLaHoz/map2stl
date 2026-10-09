"""Per-footprint satellite height measurements: shadow and lean on one scene, and multi-scene
shadows plus the multiview plane sweep across dated scenes.

Ported line for line from the validated scratch scripts (2026-10-07):
``S/satellite_cities/val/v1_single.py`` (``shadow``, ``lean``, the single-scene block loop) and
``val/wb/w4_multi.py`` (per-scene shadows, local ground offsets, plane sweep, pair consensus,
cluster combine). Generalises Cartagena ``S/satellite/s13w_measure.py`` (lean), ``s18`` (all-buildings
shadow) and ``s28``/``s29`` (Wayback multi-scene). ``scripts/check`` in the F-SKY26 progress notes
records the reproduction check: identical per-building values to the scratch scripts on Cartagena.

Footprints are dicts: ``fid`` (id), ``P`` (list of global z18 pixel rings at the OSM position),
``bb`` (pixel bbox), ``tag`` (OSM height tag or None: it only sizes search windows and the
occlusion labels; truth heights are never read), ``lat``, ``lon``; optional ``hint`` (the highest
drone or floors reading of an untagged footprint, from a region run: it only widens the search
window, :func:`search_cap`).

Shadow (``shadow_height``): slide the footprint's shadow-facing outline along the shadow bearing;
the dark run start (roof edge) to its tip gives ``H_roof = (tip - roof_edge) / (cot(el) - p)``,
``p = lean . shadow_dir`` (registration cancels); ``H_base = tip / cot(el)`` when the roof edge is
weak. Lean (``lean_height``): facade-bottom edges facing the satellite give the base offset along
the lean axis, roof edges the roof shift; ``H = (s - b) / lean_tan``.
"""

from __future__ import annotations

import itertools
import logging
import math
import time
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .scene import Scene, area_m2, outline, uv

log = logging.getLogger(__name__)


def _ram(min_gb: float | None) -> None:
    if min_gb:
        from city2stl.resources import wait_for_ram  # noqa: PLC0415
        wait_for_ram(min_gb)


#: Search windows reach this many times a footprint's tag (or, untagged, its ``hint``).
CAP_FACTOR = 1.6
#: An untagged footprint a seed reads over this (its ``hint``) is searched up to at least
#: :data:`HINT_TALL_CAP_M` (review 2026-10-09 item 2: without a tag the windows stopped at 60 m
#: (shadow, lean) and 80 m (sweep), so an untagged tower could never be read tall). Refused for
#: publishing the same day: the wide window finds spurious tall leans on low buildings (Honolulu
#: LiDAR, untagged: 35.6 % of footprints under 30 m vs 11.1 % at 60 m); hints are opt-in only.
HINT_TALL_M = 80.0
HINT_TALL_CAP_M = 220.0


def search_cap(f: dict, floor_m: float, top_m: float) -> float:
    """How high one footprint's shadow, lean or sweep search goes, in metres:
    ``CAP_FACTOR`` x its tag, at least ``floor_m`` (60 m shadow and lean, 80 m sweep), at most
    ``top_m``. Untagged: also ``CAP_FACTOR`` x its ``hint`` (the highest drone or floors reading),
    and at least :data:`HINT_TALL_CAP_M` when the hint is over :data:`HINT_TALL_M`."""
    tag = float(f.get("tag") or 0.0)
    cap = max(floor_m, CAP_FACTOR * tag)
    if not tag:
        hint = float(f.get("hint") or 0.0)
        cap = max(cap, CAP_FACTOR * hint)
        if hint > HINT_TALL_M:
            cap = max(cap, HINT_TALL_CAP_M)
    return min(top_m, cap)


def _margin_h(f: dict) -> float:
    """The height a :func:`measure_multi` block margin is sized for (``1.2 x h`` of lean or
    shadow): the tag; untagged with a ``hint``, the sweep's reach (``search_cap / 1.2``); else
    30 m."""
    if f.get("tag"):
        return float(f["tag"])
    if f.get("hint"):
        return search_cap(f, 80.0, 330.0) / 1.2
    return 30.0


@dataclass
class ShadowCtx:
    """One block of one scene, as the scratch scripts' globals: grey image, occupancy labels
    (0 ground, -1 no tile, i+1 footprint i), water and vegetation masks, footprints (block-local
    pixels), and the scene's sun and lean."""

    gray: np.ndarray
    lab: np.ndarray
    water: np.ndarray
    veg: np.ndarray
    B: dict
    ub: np.ndarray
    COT: float
    p_ls: float
    DEN_ROOF: float
    DARK: float
    M: float
    gxx: np.ndarray | None = None
    gyy: np.ndarray | None = None
    k_lean: float = 0.0
    ul: np.ndarray | None = None

    @property
    def Hh(self) -> int:
        return self.gray.shape[0]

    @property
    def Ww(self) -> int:
        return self.gray.shape[1]


def _at(c: ShadowCtx, A, Q):
    x = np.clip(Q[..., 0].round().astype(int), 0, c.Ww - 1)
    y = np.clip(Q[..., 1].round().astype(int), 0, c.Hh - 1)
    return A[y, x]


def _lead_samples(P, u, thr=0.6):
    S, N = [], []
    for pts in P:
        s, n = outline(pts)
        if s is None:
            continue
        m = (n @ u) >= thr
        S.append(s[m])
        N.append(n[m])
    S = np.vstack(S) if S else np.zeros((0, 2))
    N = np.vstack(N) if N else np.zeros((0, 2))
    return S, N


def shadow_height(c: ShadowCtx, i) -> dict:
    """Shadow height of footprint ``i`` of ``c.B`` (v1_single ``shadow``)."""
    from scipy import ndimage as ndi  # noqa: PLC0415

    M, gray, lab, water, veg, ub, COT, p_ls, DEN_ROOF, DARK = (
        c.M, c.gray, c.lab, c.water, c.veg, c.ub, c.COT, c.p_ls, c.DEN_ROOF, c.DARK)
    Ww, Hh = c.Ww, c.Hh
    OFF = 1.2 / M
    b = c.B[i]
    P = b["P"]
    me = i + 1
    AG = np.vstack(P)
    hcap = search_cap(b, 60.0, 320.0)
    S, N = _lead_samples(P, ub)
    if len(S) < 4:
        return dict(height_m=None, conf=0.0, reason="no edge facing the shadow")
    if len(S) > 400:
        j = np.linspace(0, len(S) - 1, 400).astype(int)
        S, N = S[j], N[j]
    lo = min(0.0, p_ls * hcap) - 10
    hi_start = max(0.0, p_ls * hcap) + 8
    SS = np.arange(lo, min(hcap * COT + hi_start + 20, 700.0), 0.5)
    q = S[None] + ub[None, None] * SS[:, None, None] / M
    if q[..., 0].min() < 2 or q[..., 1].min() < 2 or q[..., 0].max() > Ww - 3 or q[..., 1].max() > Hh - 3:
        # clip the search to the mosaic; long shadows leaving the core cannot be measured
        inside = ((q[..., 0].min(1) >= 2) & (q[..., 1].min(1) >= 2) & (q[..., 0].max(1) <= Ww - 3)
                  & (q[..., 1].max(1) <= Hh - 3))
        if not inside[:max(1, int((hi_start - lo) / 0.5))].all():
            return dict(height_m=None, conf=0.0, reason="near mosaic edge")
        n_in = int(np.argmin(inside)) if not inside.all() else len(SS)
        SS = SS[:n_in]
        q = q[:n_in]
    qi = q - N[None] * OFF
    qo = q + N[None] * OFF
    gi = ndi.map_coordinates(gray, [qi[..., 1].ravel(), qi[..., 0].ravel()], order=1).reshape(qi.shape[:2])
    go = ndi.map_coordinates(gray, [qo[..., 1].ravel(), qo[..., 0].ravel()], order=1).reshape(qo.shape[:2])
    li = _at(c, lab, qi)
    lo_ = _at(c, lab, qo)
    other_i = (li != 0) & (li != me)
    other_o = (lo_ != 0) & (lo_ != me)
    valid = ~other_i & ~other_o
    vf = valid.mean(1)
    vi = ~other_i
    nvi = vi.sum(1)
    Bd = np.where(nvi >= 3, ((gi < DARK) & vi).sum(1) / np.maximum(nvi, 1), np.nan)
    D = np.array([np.median((go - gi)[k][valid[k]]) if valid[k].sum() >= 3 else np.nan
                  for k in range(len(SS))])
    start = None
    for k, s_ in enumerate(SS):
        if s_ > hi_start:
            break
        if k + 2 < len(SS) and np.all(np.nan_to_num(Bd[k:k + 3], nan=0) >= 0.5):
            start = k
            break
    if start is None:
        w0 = (SS >= 0) & (SS <= 6)
        if np.nanmean(vf[w0]) < 0.3:
            return dict(height_m=None, conf=0.0, reason="shadow falls on another roof (adjacent building)")
        o = _at(c, water, qo[w0]).mean() + _at(c, veg, qo[w0]).mean()
        return dict(height_m=None, conf=0.0,
                    reason="no shadow found next to footprint" + (" (trees/water)" if o > 0.4 else ""))
    k = start
    gap = 0
    end = start
    unk_after = 0
    while k < len(SS) - 1:
        k += 1
        b_ = Bd[k]
        if np.isnan(b_):
            unk_after += 1
            continue
        if b_ >= 0.5:
            end = k
            gap = 0
            unk_after = 0
        else:
            gap += 1
            if gap > 3:
                break
    if end >= len(SS) - 2 or k >= len(SS) - 1:
        return dict(height_m=None, conf=0.0, reason="shadow longer than search window / merged / leaves core")
    if vf[min(end + 2, len(SS) - 1)] < 0.3 or unk_after > 2:
        return dict(height_m=None, conf=0.0, reason="shadow tip on another roof",
                    lower_bound_m=round(float((SS[end] - SS[start]) / DEN_ROOF), 1))
    w = (SS >= SS[end] - 2) & (SS <= SS[end] + 2.5)
    kt = np.where(w)[0][np.nanargmax(D[w])] if np.any(~np.isnan(D[w])) else end
    tip = SS[kt]
    ct = D[kt]
    qq = qo[kt]
    beyond_w = _at(c, water, qq)[valid[kt]].mean() if valid[kt].any() else 0
    beyond_v = _at(c, veg, qq)[valid[kt]].mean() if valid[kt].any() else 0
    wr = (SS >= SS[start] - 2.5) & (SS <= SS[start] + 2.5)
    kr = np.where(wr)[0][np.nanargmin(D[wr])] if np.any(~np.isnan(D[wr])) else start
    redge = SS[kr]
    cr = D[kr]
    H_base = tip / COT
    H_roof = (tip - redge) / DEN_ROOF
    roof_ok = (cr < -25) and H_roof > 2
    H = H_roof if roof_ok else H_base
    if H < 2.5:
        return dict(height_m=None, conf=0.0, reason="shadow too short to resolve")
    # lateral merge check
    up = np.array([-ub[1], ub[0]])
    allp = AG
    pr = allp @ up
    c0 = allp.mean(0)
    sides = []
    for sgn, ext in ((-1, pr.min()), (1, pr.max())):
        dd = []
        for fr in (0.3, 0.5, 0.7, 0.9):
            for extra in (2.0, 3.5):
                base = c0 + up * (ext - c0 @ up + sgn * extra / M)
                pp = base + ub * ((S @ ub).mean() - base @ ub) + ub * (SS[start] + fr * (tip - SS[start])) / M
                x, y = int(round(pp[0])), int(round(pp[1]))
                if 0 <= x < Ww and 0 <= y < Hh and not (lab[y, x] != 0 and lab[y, x] != me):
                    dd.append(gray[y, x] < DARK)
        sides.append(np.mean(dd) if len(dd) >= 3 else np.nan)
    merged = sum(1 for v in sides if v == v and v >= 0.6)
    if merged == 2 and H > 8:
        return dict(height_m=None, conf=0.0, reason="shadow merged with a neighbour's shadow",
                    upper_bound_m=round(float(H), 1))
    vfr = float(_at(c, veg, qi[start:end + 1]).mean()) if end >= start else 0.0
    if vfr > 0.35:
        return dict(height_m=None, conf=0.0, reason="shadow on trees/vegetation")
    purity = float(np.nanmean(Bd[start:end + 1]))
    conf = min(1, max(0, ct) / 60) * min(1, vf[kt] / 0.7) * purity
    conf *= 1.0 if roof_ok else 0.7
    if roof_ok and H_base > 0:
        conf *= max(0.3, 1 - abs(H_roof - H_base) / max(H_roof, H_base) * 0.8)
    reason = None
    conf *= max(0.0, 1 - 2 * vfr)
    if merged == 1:
        conf *= 0.6
        reason = "shadow touches a neighbour's shadow on one side"
    if beyond_w + beyond_v > 0.5:
        conf *= 0.4
        reason = "tip borders trees/water"
    if (tip - redge) < 4 and H < 6:
        conf *= 0.6
    sig = (math.hypot(1.5 * M, 1.5 * M) / DEN_ROOF) if roof_ok else math.hypot(1.5 * M, 3.0) / COT
    return dict(height_m=round(float(H), 1), conf=round(float(conf), 3),
                method="shadow_roof_edge" if roof_ok else "shadow_base", reason=reason,
                sigma_m=round(float(sig), 1), H_base_m=round(float(H_base), 1),
                H_roof_m=round(float(H_roof), 1), tip_m=float(tip), roof_edge_m=float(redge),
                contrast_tip=round(float(ct), 1), contrast_roof=round(float(cr), 1),
                valid_frac=round(float(vf[kt]), 2), merged_sides=merged)


def lean_height(c: ShadowCtx, i) -> dict:
    """Lean height of footprint ``i`` (v1_single ``lean``): needs ``gxx``, ``gyy``, ``k_lean``,
    ``ul``."""
    from scipy import ndimage as ndi  # noqa: PLC0415

    M, lab, k_lean, ul, gxx, gyy = c.M, c.lab, c.k_lean, c.ul, c.gxx, c.gyy
    Ww, Hh = c.Ww, c.Hh
    if k_lean < 0.08:
        return dict(height_m=None, conf=0.0, reason="no lean (true ortho / near nadir)")
    b = c.B[i]
    P = b["P"]
    me = i + 1
    hcap = search_cap(b, 60.0, 320.0)
    S, N = [], []
    for pts in P:
        s, n = outline(pts)
        if s is not None:
            S.append(s)
            N.append(n)
    if not S:
        return dict(height_m=None, conf=0.0, reason="no outline")
    S = np.vstack(S)
    N = np.vstack(N)
    if len(S) > 500:
        j = np.linspace(0, len(S) - 1, 500).astype(int)
        S, N = S[j], N[j]
    nu = N @ ul
    fac = nu < -0.3                       # facade-bottom edges (face the satellite)
    if fac.sum() < 4:
        return dict(height_m=None, conf=0.0, reason="no edge facing the satellite")
    sh = np.arange(-4.0, k_lean * hcap + 4, 0.5)            # shift along the lean axis, m
    q = S[None] + ul[None, None] * sh[:, None, None] / M
    if q[..., 0].min() < 3 or q[..., 1].min() < 3 or q[..., 0].max() > Ww - 4 or q[..., 1].max() > Hh - 4:
        return dict(height_m=None, conf=0.0, reason="near mosaic edge")
    g = (ndi.map_coordinates(gxx, [q[..., 1].ravel(), q[..., 0].ravel()], order=1).reshape(q.shape[:2]) * N[None, :, 0]
         + ndi.map_coordinates(gyy, [q[..., 1].ravel(), q[..., 0].ravel()], order=1).reshape(q.shape[:2]) * N[None, :, 1])
    g = np.abs(g)
    lq = _at(c, lab, q)
    occl = (lq != 0) & (lq != me)            # samples on other buildings' roofs
    okm = ~occl

    def prof(m):
        mm = okm & m[None]
        n_ = mm.sum(1)
        return np.where(n_ >= 4, (g * mm).sum(1) / np.maximum(n_, 1), np.nan), n_ / max(1, m.sum())

    Pf, _ = prof(fac)
    Pa, va = prof(np.ones(len(S), bool))
    wb = (sh >= -4) & (sh <= 4)
    if np.all(np.isnan(Pf[wb])):
        return dict(height_m=None, conf=0.0, reason="facade bottom hidden")
    kb = np.where(wb)[0][np.nanargmax(Pf[wb])]
    bsh = sh[kb]
    dmin = max(1.5, 2.0 * M)
    wr = sh >= bsh + dmin
    Pr = np.where(wr & (va >= 0.4), Pa, np.nan)
    if np.all(np.isnan(Pr)):
        return dict(height_m=None, conf=0.0, reason="roof outline hidden by neighbours")
    kr = int(np.nanargmax(Pr))
    s_roof = sh[kr]
    H = (s_roof - bsh) / k_lean
    sep = max(3.0, 0.15 * H) * k_lean
    far = np.abs(sh - s_roof) >= sep
    sec = np.nanmax(Pr[far]) if np.any(~np.isnan(Pr[far])) else 0.0
    med = np.nanmedian(Pr)
    pk = Pr[kr]
    ratio = float(pk / sec) if sec > 0 else 9.0
    gain = float(pk / med) if med > 0 else 9.0
    at_end = s_roof >= sh[-1] - 1.0 or kr == np.where(wr)[0][0]
    conf = float(np.clip((ratio - 1.0) / 0.4, 0, 1) * np.clip((gain - 1.0) / 0.6, 0, 1) * min(1, va[kr] / 0.7))
    reason = None
    if at_end:
        conf *= 0.3
        reason = "peak at search-window edge"
    sig = math.hypot(1.0 * M, 1.0 * M) / k_lean
    return dict(height_m=round(float(H), 1), conf=round(conf, 3), reason=reason, sigma_m=round(sig, 1),
                base_shift_m=float(bsh), roof_shift_m=float(s_roof), peak_ratio=round(ratio, 3),
                gain=round(gain, 3), visible_frac=round(float(va[kr]), 2))


def _water_veg(f32: np.ndarray, gray: np.ndarray):
    from scipy import ndimage as ndi  # noqa: PLC0415

    veg = (2 * f32[..., 1] - f32[..., 0] - f32[..., 2]) > 25
    loc_sd = np.sqrt(np.maximum(ndi.uniform_filter(gray ** 2, 9) - ndi.uniform_filter(gray, 9) ** 2, 0))
    water = ndi.binary_opening((loc_sd < 3.0) & (f32[..., 2] >= f32[..., 0]) & (gray < 110), iterations=3)
    return water, veg


# ------------------------------------------------------------------------ single scene (v1)
def measure_single(footprints: Sequence[dict], scene: Scene, M: float, *, dark: float,
                   only: set | None = None, tile_ok=None, ram_gb: float | None = 6.0,
                   progress=None) -> dict:
    """Shadow and lean of every footprint on one scene (v1_single ``main``).

    ``footprints`` in a fixed order (the occlusion labels are drawn in that order, tallest tag
    last); ``P`` at the OSM position (the scene registration ``reg_m`` is added here). Measures
    footprints whose centre tile is cached, of area >= 20 m^2, in ``only`` when given.
    ``dark``: the shadow grey threshold (v1: geometry ``dark_thr`` x 1.35). Returns
    ``{fid: {shadow, lean, area_m2, tag_m}}``.
    """
    from PIL import Image  # noqa: PLC0415
    from scipy import ndimage as ndi  # noqa: PLC0415
    from skimage.draw import polygon as skpoly  # noqa: PLC0415

    t0 = time.time()
    TILES = scene.tiles.tiles
    reg_px = np.asarray(scene.reg_m, float) / M
    allB = []
    for f in footprints:
        P = [p + reg_px for p in f["P"]]
        A = np.vstack(P)
        bb = (A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max())
        if (int(np.mean(bb[0::2]) // 256), int(np.mean(bb[1::2]) // 256)) not in TILES:
            continue
        allB.append(dict(f, P=P, bb=bb, area_m2=area_m2(P, M)))
    if not allB:
        return {}
    ub = uv(scene.shadow_bearing)
    COT = scene.cot
    if "lean_bearing" in scene.meta:            # tan + bearing as solved (v1 geometry.json)
        k_lean = float(scene.meta["lean_tan"])
        ul = uv(scene.meta["lean_bearing"])
        Lv = ul * k_lean
    else:
        Lv = np.asarray(scene.lean, float)
        k_lean = float(np.hypot(*Lv))
        ul = Lv / k_lean if k_lean > 0 else np.array([0.0, 1.0])
    p_ls = float(Lv @ ub)
    DEN_ROOF = COT - p_ls
    BS, MARG = 2048, 1024
    bbs = np.array([b["bb"] for b in allB])
    cxy = np.c_[(bbs[:, 0] + bbs[:, 2]) / 2, (bbs[:, 1] + bbs[:, 3]) / 2]
    sel = np.array([only is None or b["fid"] in only for b in allB])
    blocks = sorted({(int(x // BS), int(y // BS)) for x, y in cxy[sel]})
    res = {}
    for nb, (bx, by) in enumerate(blocks):
        X0, Y0 = bx * BS - MARG, by * BS - MARG
        X1, Y1 = (bx + 1) * BS + MARG, (by + 1) * BS + MARG
        ids_in = np.where((cxy[:, 0] >= bx * BS) & (cxy[:, 0] < (bx + 1) * BS) & (cxy[:, 1] >= by * BS)
                          & (cxy[:, 1] < (by + 1) * BS) & sel)[0]
        if not len(ids_in):
            continue
        _ram(ram_gb)
        Hh, Ww = Y1 - Y0, X1 - X0
        rgb = np.zeros((Hh, Ww, 3), np.uint8)
        have = np.zeros((Hh, Ww), bool)
        sok = np.zeros((Hh, Ww), bool)
        for tx in range(X0 // 256, X1 // 256):
            for ty in range(Y0 // 256, Y1 // 256):
                if (tx, ty) not in TILES:
                    continue
                ys, xs = ty * 256 - Y0, tx * 256 - X0
                rgb[ys:ys + 256, xs:xs + 256] = np.asarray(
                    Image.open(scene.tiles.dir / f"18_{tx}_{ty}.jpg").convert("RGB"))
                have[ys:ys + 256, xs:xs + 256] = True
                sok[ys:ys + 256, xs:xs + 256] = True if tile_ok is None else tile_ok(tx, ty)
        f32 = rgb.astype(np.float32)
        gray = f32.mean(2)
        del rgb
        water, veg = _water_veg(f32, gray)
        del f32
        gxx = ndi.gaussian_filter(gray, 1.0, order=(0, 1))
        gyy = ndi.gaussian_filter(gray, 1.0, order=(1, 0))
        near = np.where((bbs[:, 2] > X0) & (bbs[:, 0] < X1) & (bbs[:, 3] > Y0) & (bbs[:, 1] < Y1))[0]
        B = {}
        for i in near:
            b = dict(allB[i])
            b["P"] = [p - np.array([X0, Y0]) for p in allB[i]["P"]]
            x0_, y0_, x1_, y1_ = allB[i]["bb"]
            b["bb"] = (x0_ - X0, y0_ - Y0, x1_ - X0, y1_ - Y0)
            B[i] = b
        lab = np.zeros((Hh, Ww), np.int32)
        for i in sorted(near, key=lambda i: (allB[i]["tag"] or 10.0)):
            h = allB[i]["tag"] or 10.0
            for P in B[i]["P"]:
                for fr in np.linspace(0, 1, max(2, int(k_lean * h / M / 2) + 2)):
                    v = Lv * h * fr / M
                    rr, cc = skpoly(P[:, 1] + v[1], P[:, 0] + v[0], (Hh, Ww))
                    lab[rr, cc] = i + 1
        lab[~have] = -1
        ctx = ShadowCtx(gray=gray, lab=lab, water=water, veg=veg, B=B, ub=ub, COT=COT, p_ls=p_ls,
                        DEN_ROOF=DEN_ROOF, DARK=float(dark), M=M, gxx=gxx, gyy=gyy, k_lean=k_lean, ul=ul)
        for i in ids_in:
            b = B[i]
            x0, y0, x1, y1 = b["bb"]
            cyx = (int(min(Hh - 1, max(0, (y0 + y1) / 2))), int(min(Ww - 1, max(0, (x0 + x1) / 2))))
            if b["area_m2"] < 20:
                continue
            o = dict(fid=b["fid"], tag_m=b["tag"], area_m2=round(b["area_m2"], 1))
            if not sok[cyx]:
                o["shadow"] = dict(height_m=None, conf=0.0, reason="different scene (no sun/lean solution)")
                o["lean"] = dict(o["shadow"])
                res[b["fid"]] = o
                continue
            try:
                o["shadow"] = shadow_height(ctx, i)
            except Exception as e:  # noqa: BLE001 - one footprint never stops a block
                o["shadow"] = dict(height_m=None, conf=0.0, reason=f"error: {e}")
            try:
                o["lean"] = lean_height(ctx, i)
            except Exception as e:  # noqa: BLE001
                o["lean"] = dict(height_m=None, conf=0.0, reason=f"error: {e}")
            res[b["fid"]] = o
        if progress:
            progress(f"single {scene.name} block {nb + 1}/{len(blocks)} ({len(ids_in)}) {time.time() - t0:.0f}s")
        del gray, gxx, gyy, lab, have, veg, water, sok, ctx
    return res


# ------------------------------------------------------------------------- multi scene (w4)
def _ncc_rows(a, b):
    a = a - a.mean(-1, keepdims=True)
    b = b - b.mean(-1, keepdims=True)
    return (a * b).sum(-1) / np.sqrt((a * a).sum(-1) * (b * b).sum(-1) + 1e-6)


def _interior_pts(P, M, dil_m=1.5):
    from shapely import contains_xy  # noqa: PLC0415
    from shapely.geometry import Polygon  # noqa: PLC0415
    from shapely.ops import unary_union  # noqa: PLC0415

    poly = unary_union([Polygon(p).buffer(0) for p in P]).buffer(dil_m / M)
    x0, y0, x1, y1 = poly.bounds
    xs, ys = np.meshgrid(np.arange(math.floor(x0), math.ceil(x1) + 1), np.arange(math.floor(y0), math.ceil(y1) + 1))
    pts = np.c_[xs.ravel(), ys.ravel()].astype(float)
    pts = pts[contains_xy(poly, pts[:, 0], pts[:, 1])]
    if len(pts) > 500:
        pts = pts[np.random.default_rng(0).choice(len(pts), 500, replace=False)]
    return pts


#: Scene pairs whose lean vectors differ by less than this (m/m) are not used for stereo.
MIN_PAIR_DL = 0.15


def scene_pairs(scenes: Sequence[Scene]) -> list[tuple[str, str]]:
    """Scene-name pairs with |dL| >= ``MIN_PAIR_DL``, in scene order."""
    L = {s.name: np.asarray(s.lean, float) for s in scenes}
    return [(a.name, b.name) for a, b in itertools.combinations(scenes, 2)
            if np.hypot(*(L[a.name] - L[b.name])) >= MIN_PAIR_DL]


def measure_multi(footprints: Sequence[dict], scenes: Sequence[Scene], ref: str, fids: Sequence,
                  M: float, *, shadow_scenes: set | None = None, ram_gb: float | None = 6.0,
                  progress=None) -> dict:
    """Per-scene shadows and the multiview plane sweep for ``fids`` (w4_multi).

    ``scenes`` in a fixed order, ``ref`` one of them (the newest or current scene: the frame the
    footprints are registered to with its ``reg_m``); per block, each other scene's local ground
    offset is found by NCC on ground pixels (95 m cells, lazily). ``footprints``: every footprint
    (occlusion), with ``P`` at the OSM position. ``shadow_scenes``: limit per-scene shadows to
    these. Returns ``{fid: {shadow: {scene: {...}}, stereo_multiview: {...} | None}}`` (raw;
    :func:`consensus` and :func:`combine` follow).
    """
    from scipy import ndimage as ndi  # noqa: PLC0415
    from skimage.draw import polygon as skpoly  # noqa: PLC0415

    t0 = time.time()
    B = {}
    for k, f in enumerate(footprints):
        B[k] = f
    key_of = {f["fid"]: k for k, f in B.items()}
    IDS = [key_of[f] for f in fids if f in key_of]
    SCN = {s.name: s for s in scenes}
    NAMES = [s.name for s in scenes]
    REF = ref
    rREF = np.asarray(SCN[REF].reg_m, float)
    Ls = {n: np.asarray(SCN[n].lean, float) for n in NAMES}
    PAIRS = scene_pairs(scenes)
    allid = np.array(list(B))
    allbb = np.array([B[k]["bb"] for k in allid])
    cxy = {k: np.array([(B[k]["bb"][0] + B[k]["bb"][2]) / 2, (B[k]["bb"][1] + B[k]["bb"][3]) / 2]) for k in IDS}
    BS = 1024
    blocks = defaultdict(list)
    for k in IDS:
        blocks[(int(cxy[k][0] // BS), int(cxy[k][1] // BS))].append(k)
    res = {}
    nb = 0
    for (bx, by), ids in sorted(blocks.items()):
        nb += 1
        _ram(ram_gb)
        hmax = max(_margin_h(B[k]) for k in ids)
        MARG = int(min(450, max(80, 1.2 * hmax * max(max(SCN[n].cot for n in NAMES),
                                                      max(np.hypot(*Ls[n]) for n in NAMES)) + 40)) / M)
        x0, y0 = bx * BS - MARG, by * BS - MARG
        x1, y1 = (bx + 1) * BS + MARG, (by + 1) * BS + MARG
        off = np.array([x0, y0], float)
        Hh, Ww = y1 - y0, x1 - x0
        G, HV, VG, WT = {}, {}, {}, {}
        for n in NAMES:
            rgb, hv = SCN[n].tiles.crop(x0, y0, x1, y1, rgb=True)
            if not hv.any():
                continue
            f32 = rgb.astype(np.float32)
            del rgb
            gr = f32.mean(2)
            G[n] = gr
            HV[n] = hv
            VG[n] = (2 * f32[..., 1] - f32[..., 0] - f32[..., 2]) > 25
            sd = np.sqrt(np.maximum(ndi.uniform_filter(gr ** 2, 9) - ndi.uniform_filter(gr, 9) ** 2, 0))
            WT[n] = ndi.binary_opening((sd < 3.0) & (f32[..., 2] >= f32[..., 0]) & (gr < 110), iterations=3)
            del f32, sd
        if REF not in G:
            continue
        near = allid[(allbb[:, 2] > x0 - 50) & (allbb[:, 0] < x1 + 50) & (allbb[:, 3] > y0 - 50) & (allbb[:, 1] < y1 + 50)]
        occ = np.zeros((Hh, Ww), bool)
        for i in near:
            h = B[i]["tag"] or 10.0
            for pts in B[i]["P"]:
                for fr in np.linspace(0, 1, max(2, int(np.hypot(*Ls[REF]) * h / M / 2) + 2)):
                    v = (rREF + Ls[REF] * h * fr) / M
                    rr, cc = skpoly(pts[:, 1] + v[1] - y0, pts[:, 0] + v[0] - x0, (Hh, Ww))
                    occ[rr, cc] = True
        ground = ~ndi.binary_dilation(occ, iterations=3) & HV[REF]
        CELL, RW, RG = int(95 / M), int(70 / M), int(14 / M)
        GC_: dict = {}
        rng = np.random.default_rng(1)

        def g_at(n, p, ground=ground, GC_=GC_, rng=rng, G=G, HV=HV, Hh=Hh, Ww=Ww, CELL=CELL, RW=RW, RG=RG):
            """offset (px) ref -> scene n near block-local point p; (offset, ncc quality)"""
            if n == REF:
                return np.zeros(2), 1.0
            prior = (np.asarray(SCN[n].reg_m, float) - rREF) / M
            key = (n, int(p[0] // CELL), int(p[1] // CELL))
            if key in GC_:
                return GC_[key]
            cx_, cy_ = int(p[0]), int(p[1])
            ys, xs = np.nonzero(ground[max(0, cy_ - RW):cy_ + RW, max(0, cx_ - RW):cx_ + RW])
            ys = ys + max(0, cy_ - RW)
            xs = xs + max(0, cx_ - RW)
            out = (prior, 0.0)
            if len(ys) >= 300 and n in G:
                k = rng.choice(len(ys), min(2500, len(ys)), replace=False)
                P = np.c_[xs[k], ys[k]].astype(float)
                a = G[REF][ys[k], xs[k]]
                best = None
                for step, rad in ((2, RG), (0.5, 2)):
                    c0 = prior if best is None else best[1]
                    V = np.array([(dx, dy) for dy in np.arange(-rad, rad + 0.01, step)
                                  for dx in np.arange(-rad, rad + 0.01, step)]) + c0
                    q = P[None] + V[:, None]
                    b = ndi.map_coordinates(G[n], [q[..., 1].ravel(), q[..., 0].ravel()], order=1).reshape(q.shape[:2])
                    hvq = HV[n][np.clip(q[..., 1].astype(int), 0, Hh - 1), np.clip(q[..., 0].astype(int), 0, Ww - 1)].mean(1)
                    s = np.where(hvq > 0.95, _ncc_rows(np.broadcast_to(a, b.shape), b), -1)
                    j = int(np.argmax(s))
                    best = (float(s[j]), V[j])
                out = (best[1], best[0]) if best[0] > 0.3 else (prior, best[0])
            GC_[key] = out
            return out

        # ---- shadow per scene
        SH = {}
        for n in NAMES:
            if n not in G:
                continue
            if shadow_scenes is not None and n not in shadow_scenes:
                continue
            S_ = SCN[n]
            if S_.sun_el is None or S_.shadow_bearing is None:
                SH[n] = None
                continue
            ub = uv(S_.shadow_bearing)
            COT = S_.cot
            Lv = Ls[n]
            p_ls = float(Lv @ ub)
            DEN = COT - p_ls
            if DEN < 0.25:
                SH[n] = None
                continue
            dark = S_.dark or float(0.5 * (np.percentile(G[n][HV[n]], 3) + np.percentile(G[n][HV[n]], 50)))
            BB = {}
            for i in near:
                ctr = np.array([(B[i]["bb"][0] + B[i]["bb"][2]) / 2, (B[i]["bb"][1] + B[i]["bb"][3]) / 2]) - off
                gg, _ = g_at(n, ctr) if n != REF else (np.zeros(2), 1)
                BB[i] = dict(P=[p - off + rREF / M + gg for p in B[i]["P"]], tag=B[i]["tag"])
            lab = np.zeros((Hh, Ww), np.int32)
            for i in sorted(near, key=lambda i: (B[i]["tag"] or 10.0)):
                h = B[i]["tag"] or 10.0
                for P in BB[i]["P"]:
                    for fr in np.linspace(0, 1, max(2, int(np.hypot(*Lv) * h / M / 2) + 2)):
                        v = Lv * h * fr / M
                        rr, cc = skpoly(P[:, 1] + v[1], P[:, 0] + v[0], (Hh, Ww))
                        lab[rr, cc] = i + 1
            lab[~HV[n]] = -1
            ctx = ShadowCtx(gray=G[n], lab=lab, water=WT[n], veg=VG[n], B=BB, ub=ub, COT=COT,
                            p_ls=p_ls, DEN_ROOF=DEN, DARK=dark, M=M)
            SHn = {}
            for i in ids:
                if n != REF and S_.covered is not None and B[i]["fid"] not in S_.covered:
                    SHn[i] = dict(height_m=None, conf=0.0, reason="outside scene polygon")
                    continue
                try:
                    SHn[i] = shadow_height(ctx, i)
                except Exception as e:  # noqa: BLE001
                    SHn[i] = dict(height_m=None, conf=0.0, reason=f"error: {e}")
            SH[n] = SHn
            S_.meta.setdefault("dark_used", []).append(round(dark, 1))
            del lab, ctx
        # ---- multi-view plane sweep
        for i in ids:
            out = dict(shadow={}, stereo_multiview=None)
            for n in NAMES:
                s = SH.get(n)
                if n not in G:
                    out["shadow"][n] = dict(height_m=None, conf=0.0, reason="no tiles")
                elif s is None:
                    out["shadow"][n] = dict(height_m=None, conf=0.0,
                                            reason="roof hides shadow tip in this scene (lean along the shadow)")
                else:
                    out["shadow"][n] = {k: v for k, v in s[i].items()
                                        if k in ("height_m", "conf", "method", "reason", "sigma_m",
                                                 "lower_bound_m", "upper_bound_m")}
            P = [p - off + rREF / M for p in B[i]["P"]]
            pts = _interior_pts(P, M)
            hcap = search_cap(B[i], 80.0, 330.0)
            HS = np.arange(0.0, hcap, 0.5)
            if len(pts) >= 20:
                smp = {}
                for n in G:
                    if n != REF and SCN[n].covered is not None and B[i]["fid"] not in SCN[n].covered:
                        continue
                    gg, gq = g_at(n, pts.mean(0))
                    if n != REF and gq < 0.3:
                        continue
                    q = pts[None] + gg + Ls[n][None, None] * HS[:, None, None] / M
                    if q[..., 0].min() < 0 or q[..., 1].min() < 0 or q[..., 0].max() > Ww - 1 or q[..., 1].max() > Hh - 1:
                        continue
                    if not HV[n][np.clip(q[..., 1].astype(int), 0, Hh - 1), np.clip(q[..., 0].astype(int), 0, Ww - 1)].all():
                        continue
                    smp[n] = ndi.map_coordinates(G[n], [q[..., 1].ravel(), q[..., 0].ravel()], order=1).reshape(q.shape[:2])
                prs = [(a, b) for a, b in PAIRS if a in smp and b in smp]
                if prs:
                    Cm = np.array([_ncc_rows(smp[a], smp[b]) for a, b in prs])
                    mC = Cm.mean(0)
                    kb = int(np.argmax(mC))
                    Hb = float(HS[kb])
                    far = np.abs(HS - Hb) >= max(4.0, 0.08 * Hb)
                    sec = float(mC[far].max()) if far.any() else -1
                    per = [float(HS[int(np.argmax(cc))]) for cc in Cm]
                    dL = [float(np.hypot(*(Ls[a] - Ls[b]))) for a, b in prs]
                    conf = float(np.clip((mC[kb] - 0.25) / 0.4, 0, 1) * np.clip((mC[kb] - sec) / 0.12, 0, 1))
                    sc_scene = {n: float(np.mean([Cm[j, kb] for j, (a, b) in enumerate(prs) if n in (a, b)])) for n in smp}
                    out["stereo_multiview"] = dict(
                        height_m=round(Hb, 1), conf=round(conf, 2), ncc=round(float(mC[kb]), 3),
                        ncc_second=round(sec, 3), n_pairs=len(prs), pairs=[f"{a}|{b}" for a, b in prs],
                        per_pair_h=[round(x, 1) for x in per], pair_dL=[round(x, 2) for x in dL],
                        sigma_m=round(float(M / np.sqrt(np.sum(np.array(dL) ** 2)) * 2), 1),
                        scene_ncc={k: round(v, 2) for k, v in sc_scene.items()})
            res[B[i]["fid"]] = out
        if progress:
            progress(f"multi block {nb}/{len(blocks)} ({len(ids)}) {time.time() - t0:.0f}s")
        del G, HV, VG, WT, occ, ground
    return res


def _cons(ph):
    best = None
    for h0 in ph:
        tol = max(3.0, 0.07 * h0)
        m = np.abs(ph - h0) <= tol
        if best is None or m.sum() > best[0] or (m.sum() == best[0] and np.median(ph[m]) > best[1]):
            best = (int(m.sum()), float(np.median(ph[m])), m)
    return best


def consensus(st: dict | None) -> dict | None:
    """Pair-consensus stereo (w4): each pair's best height; the height most pairs support
    (tolerance max(3 m, 7 %)). None with fewer than 2 pairs."""
    if not st or len(st["per_pair_h"]) < 2:
        return None
    ph = np.array(st["per_pair_h"])
    n, H, m = _cons(ph)
    conf = float(np.clip((n - 1) / 3, 0, 1) * np.clip(n / len(ph) / 0.4, 0, 1))
    return dict(height_m=round(H, 1), conf=round(conf, 2), n_agree=n, n_pairs=len(ph),
                agreeing_pairs=[p for p, mm in zip(st["pairs"], m, strict=False) if mm],
                sigma_m=round(max(1.0, float(np.std(ph[m]))), 1))


#: Readings under this confidence stay out of the scratch cluster combine (w4 ``CMIN``).
COMBINE_CMIN = 0.15


def combine(o: dict) -> dict:
    """The w4 cluster combine of one footprint's multi-scene shadows and consensus stereo
    (largest group within 25 %, inverse-variance mean). Diagnostic only: fusion uses
    ``readings``, where shadow scenes count once."""
    est = []
    for sc, s in o["shadow"].items():
        if s.get("height_m") and s.get("conf", 0) >= COMBINE_CMIN:
            est.append(("shadow:" + sc, s["height_m"], max(s.get("sigma_m") or 1.5, 1.0), s["conf"]))
    st = o.get("stereo")
    if st and st["conf"] >= 0.25 and st["height_m"] >= 2.5:
        est.append(("stereo", st["height_m"], st["sigma_m"], st["conf"]))
    if not est:
        return dict(height_m=None, conf=0.0, sigma_m=None, n_independent=0, used=[])
    est = [(e[0], e[1], max(e[2], 1.5, 0.08 * e[1]), e[3]) for e in est]
    best = None
    for cnd in est:
        mem = [e for e in est if abs(e[1] - cnd[1]) <= 0.25 * max(cnd[1], e[1], 4)]
        key = (len(mem), sum(e[3] for e in mem))
        if best is None or key > best[0]:
            best = (key, mem)
    keep = best[1]
    kw = np.array([1 / e[2] ** 2 for e in keep])
    H = float((kw * np.array([e[1] for e in keep])).sum() / kw.sum())
    sig = float(max(1 / math.sqrt(kw.sum()), np.std([e[1] for e in keep]) if len(keep) > 1 else 0))
    n_ind = len(keep)
    conf = min(0.95, 0.45 + 0.15 * n_ind) if n_ind >= 2 else 0.8 * keep[0][3]
    if len(keep) < len(est):
        conf *= 0.85 if n_ind >= 2 else 0.5
    return dict(height_m=round(H, 1), conf=round(conf, 2), sigma_m=round(sig, 1),
                n_independent=n_ind, used=[e[0] for e in keep])


def finish_multi(res: dict) -> dict:
    """Adds ``stereo`` (consensus) and ``combined`` to each :func:`measure_multi` row."""
    for o in res.values():
        o["stereo"] = consensus(o.get("stereo_multiview"))
        o["combined"] = combine(o)
    return res


__all__ = ["ShadowCtx", "shadow_height", "lean_height", "measure_single", "measure_multi",
           "scene_pairs", "consensus", "combine", "finish_multi", "search_cap", "MIN_PAIR_DL",
           "COMBINE_CMIN", "CAP_FACTOR", "HINT_TALL_M", "HINT_TALL_CAP_M"]
