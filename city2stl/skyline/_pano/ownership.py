"""skyline._pano.ownership — which footprint owns a drone reading's top (review 2026-10-09, item 3).

``footprint_detect.measure_footprints`` climbs each column from a footprint's base to the first
sky or depth step. A low building in front of a tower then reads the tower (tower-behind), and a
building hidden behind a nearer roof reads that roof. Depth does not separate them (depth study
2026-10-09: calibrated Depth Anything V2 and MoGe-2 caught 0 of the 4 known Miami cases), so
ownership is decided here by geometry, independent of any height evidence:

1. **Upper plausible height** per footprint (:func:`upper_heights`): as an occluder its OSM tag;
   else, with the cadastre occluders on, the non-PH cadastre floors x 1.5 x 4.13 m; else the
   untagged prior's 90th percentile (:func:`prior_upper`). As the owner of a top, the tag plus
   :data:`TAG_MARGIN`. Never a measured top.
2. **Claims, near to far** (:meth:`Scene.owners`). Every footprint, measured or not, claims its
   silhouette at that height (:func:`claim_top`: the near roof edge from below, the far one from
   above). Along each core column of a reading, the first footprint whose claim holds the top
   pixel owns it: a nearer one (the line of sight to F's facade crosses it: the isovist fails),
   F itself, a farther one (the ray clears F's claim) or none. F keeps the top with a third of
   the columns (:data:`SELF_MIN`).
3. **Only hard bounds take a top away** (:func:`not_owned`): the rule's walk counts, besides F,
   only footprints with a tag or a cadastre count. A prior p90 claim names the re-credit target
   but never removes a reading: with it, Cartagena's untagged mid-rises in front of untagged
   towers lost corroborated readings.
4. **Re-credit** (:meth:`Scene.recredit`): a reading whose top an untagged farther footprint G
   owns (every claim counted) becomes G's reading, kept when it passes ``elevated.trusted`` with
   G's base hidden (the share seen above the nearer claims). Line of sight picks G, not agreeing
   height evidence (the 2026-10-07 refusal of re-credit applied to the latter); a tagged G
   publishes its tag anyway.

Measured on the Miami drone states (LiDAR, 2026-10-09; ``docs/decisions/building-heights.md``):
trusted readings within 25 % 0.56 -> 0.81 kept (n 64; 20 of 28 wrong caught, 1 of 36 right
flagged), on the older reading set 0.43 -> 0.81 (42 of 51, 1 of 39), the 4 known tower-behind
cases caught. Two parts of the review's design were measured and left out:

- **Own corners** (:meth:`Scene.corner_edges`, computed with ``diagnose``): a vertical edge at F's
  projected corner columns. At 1-3 km OSM footprints include low wings, MobileSAM merges a block
  into one instance and a tower behind can share a corner's column: no edge at 10 of 36 right
  readings, an edge at 18 of 28 wrong ones.
- **Claims as the run's start** (``occ[]`` in ``measure_footprints`` from these claims instead of
  measured tops): more trusted readings but worse ones (within 25 % 0.56 -> 0.45; kept after the
  rule 0.74).

Cadastre occluders (Cartagena, :func:`cadastre_occluders`, :data:`CADASTRE_OCCLUDERS`): the AMB
``Construccion`` polygons add geometry only (OSM holds ~28k buildings, the cadastre ~174k); their
heights are never published.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass, field

import numpy as np

from .. import footprint_detect as fd
from .roof_fit import ray_intervals

#: Quantile of the untagged prior used as the upper plausible height of an untagged footprint.
UPPER_PRIOR_Q = 0.9
#: Cadastre bound: non-PH floors x this many storeys per floor x the tower storey height (m).
CAD_STOREY_FACTOR = 1.5
CAD_STOREY_M = 4.13
#: A tag is the roof height; a reading within the fusion agreement (25 %) of it is the tag's.
TAG_MARGIN = 0.25
#: Footprints this far (m, nearest vertex) take part; nearer than ``MIN_DIST_M`` never (the
#: camera stands on or next to them).
MAX_DIST_M = 3500.0
MIN_DIST_M = 20.0
#: Pixel tolerances in pixels of the default spin pano (``fd.px_scale`` scales them): the top
#: must be this far inside a nearer claim to count as that footprint's, and this far above F's
#: own claim to count as not F's (pose and footprint misplacement, ~0.5 deg).
CLAIM_TOL_PX = 4.0
#: Own-corner test: the edge column within this many pixels of the projected corner, the row
#: window this many pixels about the predicted roof row, rows below it, gap from the edge.
CORNER_COL_TOL_PX = 3.0
CORNER_ROW_TOL_PX = 3.0
CORNER_RUN_PX = 6.0
CORNER_MIN_DIFF = 0.6
#: Inverse-depth ratio that counts as a depth step across a corner.
CORNER_DEPTH_STEP = 1.15
#: Same-building test: another polygon overlapping F by this share of the smaller one is F's
#: part (podium, annex), not an occluder or an owner.
SAME_BUILDING_OVERLAP = 0.3
#: Core share of a reading's columns tested (as ``measure_footprints``).
CORE = 0.6
#: F owns a reading's top when at least this share of the tested columns is F's
#: (``measure_footprints`` keeps a footprint with a third of its core columns).
SELF_MIN = 1.0 / 3.0


# --------------------------------------------------------------------------- upper heights


@functools.lru_cache(maxsize=4)
def _upper_model(exclude_city: str | None = None, q: float = UPPER_PRIOR_Q):
    """Quantile gradient boosting of log height on the untagged prior's features and training
    set (``untagged_prior``), ``exclude_city`` left out."""
    import pandas as pd
    from sklearn.ensemble import HistGradientBoostingRegressor

    from .. import untagged_prior as up

    df = pd.read_csv(up.TRAIN_CSV)
    X = np.full((len(df), len(up.FEATURES)), np.nan)
    for _city, g in df.groupby("city"):
        X[g.index] = up.features(g.lat, g.lon, g.area_m2, g.perimeter_m, g.n_vertices,
                                 g.osm_tag_m, g.height_source)
    untag = ((df.height_source == "default") & (df.city != (exclude_city or ""))).to_numpy()
    m = HistGradientBoostingRegressor(loss="quantile", quantile=q, max_depth=3,
                                      learning_rate=0.05, max_iter=200, min_samples_leaf=20,
                                      random_state=0)
    m.fit(X[untag], np.log(df.truth_m.to_numpy()[untag]))
    return m


def prior_upper(records, exclude_city: str | None = None, q: float = UPPER_PRIOR_Q) -> np.ndarray:
    """The untagged prior's ``q`` quantile (m) per ``BuildingRecord`` (neighbours = these
    records, as ``untagged_prior.fallback_for``)."""
    from .. import untagged_prior as up

    rows = up.rows_from_records(records)
    if not rows:
        return np.zeros(0)
    X = up.features([r["lat"] for r in rows], [r["lon"] for r in rows],
                    [r["area_m2"] for r in rows], [r["perimeter_m"] for r in rows],
                    [r["n_vertices"] for r in rows],
                    [np.nan if r.get("osm_tag_m") is None else r["osm_tag_m"] for r in rows],
                    [r.get("height_source") for r in rows])
    return np.exp(_upper_model((exclude_city or "").lower() or None, q).predict(X))


@dataclass(frozen=True)
class Upper:
    """Upper plausible height of one footprint: as an occluder (``height_m``: the tag itself, the
    cadastre bound or the prior's p90) and as the owner of a top (``own_m``: the tag plus
    :data:`TAG_MARGIN`, else the same), its source (``tag``, ``cadastre``, ``prior_p90``) and
    whether a top above ``own_m`` is not the footprint's (``hard``: a tag or a cadastre count)."""
    height_m: float
    source: str
    hard: bool
    own_m: float = math.nan

    @property
    def own(self) -> float:
        return self.height_m if math.isnan(self.own_m) else self.own_m


def upper_heights(records, exclude_city: str | None = None,
                  cadastre: dict | None = None) -> dict:
    """``{feature_id: Upper}`` for ``records``: the tag (as owner x (1 + :data:`TAG_MARGIN`));
    else the cadastre bound ``cadastre[fid]`` (m, from :func:`cadastre_bounds`); else the
    prior's p90."""
    records = list(records)
    p90 = prior_upper(records, exclude_city)
    out = {}
    for r, p in zip(records, p90, strict=True):
        fid = str(r.feature_id)
        if r.height_tag_m:
            t = float(r.height_tag_m)
            out[fid] = Upper(t, "tag", True, t * (1.0 + TAG_MARGIN))
        elif cadastre and cadastre.get(fid):
            out[fid] = Upper(float(cadastre[fid]), "cadastre", True)
        else:
            out[fid] = Upper(float(p), "prior_p90", False)
    return out


def cadastre_bound(floors: float) -> float:
    """Upper height (m) of a non-PH cadastre count: floors x 1.5 x 4.13 m."""
    return float(floors) * CAD_STOREY_FACTOR * CAD_STOREY_M


def cadastre_bounds(records, layer=None) -> dict:
    """``{feature_id: m}``: :func:`cadastre_bound` of each record's non-PH cadastre match
    (``co_catastro.match_footprints``); PH plots and unmatched records are left out."""
    from city2stl.height.providers import co_catastro as cc

    polys = {str(r.feature_id): r.geometry for r in records if r.geometry is not None}
    out = {}
    for fid, m in cc.match_footprints(polys, layer=layer).items():
        if not m.ph and m.floors and math.isfinite(m.floors) and m.floors > 0:
            out[fid] = cadastre_bound(m.floors)
    return out


def cadastre_occluders(lat: float, lon: float, layer=None, max_dist_m: float = MAX_DIST_M):
    """``[(ring lon/lat, upper m)]``: the non-PH cadastre polygons within ``max_dist_m`` of the
    camera, as occluders only (geometry and :func:`cadastre_bound`; never published)."""
    import shapely

    from city2stl.height.providers import co_catastro as cc

    layer = layer if layer is not None else cc.fetch_layer("cartagena")
    kx = fd.M_PER_DEG_LAT * math.cos(math.radians(lat))
    box = shapely.box(lon - max_dist_m / kx, lat - max_dist_m / fd.M_PER_DEG_LAT,
                      lon + max_dist_m / kx, lat + max_dist_m / fd.M_PER_DEG_LAT)
    geoms = layer.geometry
    hit = np.flatnonzero(shapely.intersects(geoms, box))
    out = []
    for i in hit:
        fl = float(layer.floors[i])
        if not math.isfinite(fl) or fl <= 0 or cc.is_ph(cc._codigo(layer, int(i))):
            continue
        g = geoms[i]
        if g.geom_type == "MultiPolygon":
            g = max(g.geoms, key=lambda q: q.area)
        if g.geom_type != "Polygon":
            continue
        out.append((np.asarray(g.exterior.coords, float)[:, :2], cadastre_bound(fl)))
    return out


# --------------------------------------------------------------------------- geometry


def claim_top(h: float, U, d_in, d_out):
    """Elevation (deg) of the top of a footprint's claimed silhouette at height ``U`` (see
    ``roof_fit.top_elev``): the near edge when ``U >= h``, the far roof edge from above."""
    U = np.asarray(U, float)
    return np.degrees(np.arctan((U - h) / np.where(U < h, d_out, d_in)))


def base_elev(h: float, d_in):
    return -np.degrees(np.arctan(h / np.asarray(d_in, float)))


def _overlaps(a, b) -> bool:
    """One building: the overlap is at least SAME_BUILDING_OVERLAP of the smaller polygon."""
    import shapely

    m = min(a.area, b.area)
    try:
        return m > 0 and a.intersection(b).area >= SAME_BUILDING_OVERLAP * m
    except shapely.errors.GEOSException:
        return False


def _owner(shares: dict) -> str:
    """``self`` with :data:`SELF_MIN` of the columns, else the most common other owner."""
    if shares.get("self", 0.0) >= SELF_MIN - 1e-9:
        return "self"
    return max(("nearer", "farther", "none"), key=lambda kd: shares.get(kd, 0.0))


@dataclass
class _Poly:
    xy: np.ndarray
    dn: float
    c0: float
    c1: float
    v0: int            # vertex at the left (lowest-bearing) extreme
    v1: int            # ... and at the right one
    upper: float       # as an occluder
    own: float         # as the owner of a top
    hard: bool
    fp: int            # index into the footprints, -1 for an occluder only (cadastre)
    tagged: bool = False
    shape: object = None


@dataclass
class Verdict:
    """Ownership of one reading's top (see the module docstring)."""
    footprint: int
    owner: str                    # self, nearer, farther, none, unknown (the rule's walk)
    shares: dict = field(default_factory=dict)   # owner kind -> share of tested columns
    shares_raw: dict = field(default_factory=dict)  # ... without the sideways tolerance
    shares_hard: dict = field(default_factory=dict)  # ... the rule's walk (hard bounds only)
    owner_all: str = ""           # the owner by every claim, the prior p90 ones included
    far_fp: int | None = None     # the farther owner's footprint index (-1: an occluder only)
    far_share: float = 0.0
    far_hard: bool = False
    far_poly: int | None = None   # the farther owner's polygon in the Scene
    near_fp: int | None = None
    corners: tuple = (None, None)  # left, right: True edge, False none, None not testable
    upper_m: float = math.nan
    upper_source: str = ""
    upper_hard: bool = False
    n_cols: int = 0
    base_claimed: float = 0.0     # share of columns whose base row a nearer claim covers
    claim_frac: float = math.nan  # visible share above the nearer claims (base to top)

    @property
    def corner_ok(self) -> bool | None:
        c = [x for x in self.corners if x is not None]
        return None if not c else any(c)


class Scene:
    """One pano's footprints and occluders, their claims and the label/instance/depth maps."""

    def __init__(self, pano: fd.Pano, pose: fd.PanoPose, footprints: list, upper: list,
                 instances: np.ndarray | None = None, depth: np.ndarray | None = None,
                 occluders: list | None = None, max_dist_m: float = MAX_DIST_M,
                 diagnose: bool = False):
        import shapely

        self.diagnose = diagnose
        self.footprints = footprints
        self.pano, self.pose = pano, pose
        self.h = float(pose.camera_h_m)
        self.H, self.W = pano.labels.shape
        self.k_px = fd.px_scale(pano)
        self.bu, self.b0 = fd.bearing_columns(pano, pose)
        self.bear = self.bu % 360.0
        self.labels = pano.labels
        self.inst = instances
        # the label, instance and depth maps serve only the corner diagnostic: the rule needs
        # geometry alone, so a run builds no full-pano arrays here
        self.isb = self.dz = None
        if diagnose:
            self.isb = np.isin(pano.labels, fd.BUILDING_CLASSES)
            if depth is not None:
                from scipy.ndimage import median_filter

                self.dz = median_filter(np.asarray(depth, np.float32),
                                        size=(fd._scaled(5, self.k_px) | 1, 1))
        self.tol_deg = math.degrees(CLAIM_TOL_PX * self.k_px / pano.f_px)
        self.polys: list[_Poly] = []
        self.by_fp: dict[int, int] = {}
        items = [(fp.ring, u, i) for i, (fp, u) in enumerate(zip(footprints, upper, strict=True))]
        items += [(ring, Upper(float(u), "cadastre", True), -1) for ring, u in (occluders or ())]
        for ring, u, i in items:
            ring = np.asarray(ring, float)
            if ring.ndim != 2 or len(ring) < 4 or not np.isfinite(ring[:, :2]).all():
                continue                              # no polygon (or NaN): never handed to GEOS
            xy = fd._local(pano.lat, pano.lon, ring[:, :2])
            d = np.hypot(xy[:, 0], xy[:, 1])
            dn = float(d.min())
            if dn < MIN_DIST_M or dn > max_dist_m:
                continue
            bear = np.degrees(np.arctan2(xy[:, 0], xy[:, 1])) % 360.0
            rel = (bear - bear[0] + 180.0) % 360.0 - 180.0
            c0 = float(fd.column_of((bear[0] + rel.min()) % 360.0, self.bu, self.b0))
            c1 = float(fd.column_of((bear[0] + rel.max()) % 360.0, self.bu, self.b0))
            if not (np.isfinite(c0) and np.isfinite(c1)) or c1 < c0:
                continue
            p = _Poly(xy, dn, c0, c1, int(np.argmin(rel)), int(np.argmax(rel)),
                      float(u.height_m), float(u.own), bool(u.hard), i, u.source == "tag")
            if i >= 0:
                self.by_fp[i] = len(self.polys)
            self.polys.append(p)
        good = []
        for p in self.polys:
            try:
                s = shapely.Polygon(p.xy)
                s = s if s.is_valid else s.buffer(0)
            except (shapely.errors.GEOSException, ValueError):
                continue
            if s.is_empty or not s.area > 0:
                continue                              # degenerate ring: no area to claim
            p.shape = s
            good.append(p)
        if len(good) < len(self.polys):
            self.polys = good
            self.by_fp = {p.fp: n for n, p in enumerate(self.polys) if p.fp >= 0}
        # an occluder-only polygon (cadastre) overlapping an OSM footprint is that building,
        # already there with its own upper height: dropped, so it never takes a top from it
        osm = [p for p in self.polys if p.fp >= 0]
        if len(osm) < len(self.polys) and osm:
            t = shapely.STRtree([p.shape for p in osm])
            keep = []
            for p in self.polys:
                if p.fp < 0 and any(_overlaps(p.shape, osm[int(j)].shape)
                                    for j in t.query(p.shape, predicate="intersects")):
                    continue
                keep.append(p)
            self.polys = keep
            self.by_fp = {p.fp: n for n, p in enumerate(self.polys) if p.fp >= 0}
        self.c0 = np.array([p.c0 for p in self.polys])
        self.c1 = np.array([p.c1 for p in self.polys])
        self.tree = shapely.STRtree([p.shape for p in self.polys]) if self.polys else None

    # ------------------------------------------------------------------ helpers
    def elev(self, row):
        return fd._elev_of(self.pano, self.pose, row)

    def row(self, elev):
        return fd._row_of(self.pano, self.pose, elev)

    def _same_building(self, k: int) -> set:
        """Polygons that are F's own parts (overlap >= SAME_BUILDING_OVERLAP of the smaller)."""
        p = self.polys[k]
        out = {k}
        for j in self.tree.query(p.shape, predicate="intersects"):
            if j != k and _overlaps(p.shape, self.polys[j].shape):
                out.add(int(j))
        return out

    def _cols(self, x0: int, x1: int) -> np.ndarray:
        n = x1 - x0 + 1
        cut = int(n * (1 - CORE) / 2)
        xs = np.arange(x0 + cut, x1 - cut + 1)
        return xs[(xs >= 0) & (xs < self.W)]

    def _column_polys(self, xs: np.ndarray, skip: set) -> list[int]:
        lo, hi = float(xs.min()), float(xs.max())
        ks = np.flatnonzero((self.c1 >= lo) & (self.c0 <= hi))
        return [int(k) for k in ks if int(k) not in skip]

    # ------------------------------------------------------------------ ownership
    def owners(self, k: int, xs: np.ndarray, e_t: float, erode: bool = True,
               hard_only: bool = False):
        """Per column of ``xs``: the owner kind of the pixel at elevation ``e_t`` for polygon
        ``k`` (``self``/``nearer``/``farther``/``none``, ``miss`` when the column misses ``k``),
        the owner's polygon index (-1 when none), F's ``(d_in, d_out)`` and the claim row of
        the nearer polygons (the lowest row they leave free; ``H`` when none).

        Walk near to far: a nearer polygon owns the pixel when the pixel lies inside its claim
        by the tolerance (``CLAIM_TOL_PX`` below its claimed top and, with ``erode``, as far
        inside its left and right edges: heading and footprint misplacement shift a claim
        sideways as much as up); F when inside its own claim (``Upper.own``) plus the
        tolerance; else the first farther polygon whose own claim plus the tolerance holds it.
        ``hard_only``: the other polygons are those with a hard bound (a tag, a cadastre count);
        F keeps its own claim whatever its bound."""
        p = self.polys[k]
        b = self.bear[xs]
        ivF = ray_intervals(p.xy, b)
        others = self._column_polys(xs, self._same_building(k))
        if hard_only:
            others = [j for j in others if self.polys[j].hard]
        n = len(xs)
        tol = self.tol_deg
        okF = np.isfinite(ivF[:, 0])
        dF = np.where(okF, ivF[:, 0], np.inf)
        # (columns x others): entry distance (inf: missed), eroded hit, claims and base
        m = len(others)
        D = np.full((n, m), np.inf)
        core = np.zeros((n, m), bool)
        top = np.full((n, m), -np.inf)
        own_top = np.full((n, m), -np.inf)
        base = np.full((n, m), np.inf)
        for c, j in enumerate(others):
            q = self.polys[j]
            iv = ray_intervals(q.xy, b)
            ok = np.isfinite(iv[:, 0])
            if not ok.any():
                continue
            D[ok, c] = iv[ok, 0]
            core[:, c] = ok
            if erode:
                core[:, c] &= np.isfinite(ray_intervals(q.xy, b - tol)[:, 0]) & np.isfinite(
                    ray_intervals(q.xy, b + tol)[:, 0])
            top[ok, c] = claim_top(self.h, q.upper, iv[ok, 0], iv[ok, 1])
            own_top[ok, c] = claim_top(self.h, q.own, iv[ok, 0], iv[ok, 1])
            base[ok, c] = base_elev(self.h, iv[ok, 0])
        idx = np.array(others, int)
        nearer = D < dF[:, None]
        farther = np.isfinite(D) & (D > dF[:, None])
        hit_n = nearer & core & (base - tol <= e_t) & (e_t <= top - tol)
        hit_f = farther & (base - tol <= e_t) & (e_t <= own_top + tol)
        topF = claim_top(self.h, p.own, ivF[:, 0], ivF[:, 1])
        hit_s = okF & (base_elev(self.h, dF) - tol <= e_t) & (e_t <= topF + tol)
        kind = np.full(n, "none", object)
        own = np.full(n, -1)
        any_f = hit_f.any(axis=1)
        if m:
            jf = np.argmin(np.where(hit_f, D, np.inf), axis=1)
            kind[any_f] = "farther"
            own[any_f] = idx[jf[any_f]]
        kind[hit_s] = "self"
        own[hit_s] = k
        any_n = hit_n.any(axis=1)
        if m:
            jn = np.argmin(np.where(hit_n, D, np.inf), axis=1)
            kind[any_n] = "nearer"
            own[any_n] = idx[jn[any_n]]
        kind[~okF] = "miss"
        own[~okF] = -1
        near_claim = np.max(np.where(nearer, top, -np.inf), axis=1) if m else np.full(n, -np.inf)
        claim_row = np.where(np.isfinite(near_claim), self.row(np.where(
            np.isfinite(near_claim), near_claim, 0.0)), float(self.H))
        return kind, own, ivF, claim_row

    def assess(self, m) -> Verdict | None:
        """Ownership of reading ``m`` (a ``Measured``); None when its footprint is not here."""
        k = self.by_fp.get(int(m.footprint))
        if k is None:
            return None
        p = self.polys[k]
        xs = self._cols(int(m.x0), int(m.x1))
        if not len(xs):
            return None
        e_t = float(self.elev(float(m.top_row)))
        kind, own, ivF, claim_row = self.owners(k, xs, e_t)
        tested = kind != "miss"
        n = int(tested.sum())
        v = Verdict(int(m.footprint), "unknown", upper_m=p.own, upper_hard=p.hard, n_cols=n)
        if not n:
            return v
        shares = {kd: float(np.mean(kind[tested] == kd))
                  for kd in ("self", "nearer", "farther", "none")}
        v.shares = shares
        # the rule's walk: only footprints with a hard bound (tag, cadastre count) besides F, so a
        # prior p90 (soft) claim never takes a top away; it only names the re-credit target
        kind_h, own_h = self.owners(k, xs, e_t, hard_only=True)[:2]
        v.shares_hard = {kd: float(np.mean(kind_h[tested] == kd))
                         for kd in ("self", "nearer", "farther", "none")}
        if self.diagnose:
            kraw = self.owners(k, xs, e_t, erode=False)[0]
            v.shares_raw = {kd: float(np.mean(kraw[tested] == kd))
                            for kd in ("self", "nearer", "farther", "none")}
        # F keeps the top with SELF_MIN of the tested columns (measure_footprints keeps a
        # footprint with a third of its core columns); else the most common other owner
        v.owner = _owner(v.shares_hard)
        v.owner_all = _owner(shares)
        far = own[tested & (kind == "farther")]
        if far.size:
            vals, cnt = np.unique(far, return_counts=True)
            j = int(vals[np.argmax(cnt)])
            v.far_fp, v.far_share = self.polys[j].fp, float(cnt.max() / n)
            v.far_hard = self.polys[j].hard
            v.far_poly = j
        near = own_h[tested & (kind_h == "nearer")]
        if near.size:
            vals, cnt = np.unique(near, return_counts=True)
            v.near_fp = self.polys[int(vals[np.argmax(cnt)])].fp
        # the claim, near to far: the base is seen only where no nearer claim covers its row
        dF = ivF[tested, 0]
        base_rows = self.row(base_elev(self.h, dF))
        cr = claim_row[tested]
        v.base_claimed = float(np.mean(cr < base_rows - CLAIM_TOL_PX * self.k_px))
        bottom = np.minimum(float(m.bottom_row), cr)
        v.claim_frac = float(np.median(np.clip((bottom - m.top_row) / np.maximum(
            1.0, base_rows - m.top_row), 0.0, 1.0)))
        if self.diagnose:                     # measured, not part of the rule (not_owned)
            v.corners = self.corner_edges(k, float(m.height_m))
        return v

    # ------------------------------------------------------------------ corners
    def corner_edges(self, k: int, height_m: float) -> tuple:
        """``(left, right)``: whether a vertical edge shows at polygon ``k``'s projected corner
        columns at the row its roof would have at ``height_m`` (None: outside the pano)."""
        p = self.polys[k]
        out = []
        for side, c, v in ((-1, p.c0, p.v0), (1, p.c1, p.v1)):
            d = float(np.hypot(*p.xy[v]))
            yc = float(self.row(claim_top(self.h, height_m, d, d)))
            out.append(self._edge_at(c, yc, side))
        return tuple(out)

    def _edge_at(self, xc: float, yc: float, side: int) -> bool | None:
        H, W = self.H, self.W
        k = self.k_px
        ctol = max(1, int(round(CORNER_COL_TOL_PX * k)))
        rtol = max(1, int(round(CORNER_ROW_TOL_PX * k)))
        run = max(2, int(round(CORNER_RUN_PX * k)))
        g = max(1, int(round(k)))
        if self.isb is None or not (np.isfinite(xc) and np.isfinite(yc)):
            return None
        y0, y1 = int(round(yc)) - rtol, int(round(yc)) + rtol + run
        rows = np.arange(max(0, y0), min(H, y1 + 1))
        if len(rows) < 3:
            return None
        tested = False
        for xe in range(int(round(xc)) - ctol, int(round(xc)) + ctol + 1):
            xi, xo = xe - side * g, xe + side * g
            if not (0 <= xi < W and 0 <= xo < W):
                continue
            bi = self.isb[rows, xi]
            if bi.sum() < 3:
                continue
            tested = True
            bo = self.isb[rows, xo]
            diff = ~bo
            if self.inst is not None:
                ii, io = self.inst[rows, xi], self.inst[rows, xo]
                diff |= bo & (ii > 0) & (io > 0) & (ii != io)
            if self.dz is not None:
                a, b = self.dz[rows, xi], self.dz[rows, xo]
                ok = (a > 0) & (b > 0)
                r = np.where(ok, np.maximum(a, b) / np.where(ok, np.minimum(a, b), 1.0), 1.0)
                diff |= bo & ok & (r >= CORNER_DEPTH_STEP)
            if float(np.mean(diff[bi])) >= CORNER_MIN_DIFF:
                return True
        return False if tested else None

    # ------------------------------------------------------------------ re-credit
    def recredit(self, m, v: Verdict):
        """``m`` as a reading of its farther geometric owner ``v.far_fp`` (a ``Measured`` with
        that footprint, its height and distance along the owned columns, base hidden and the
        visible share above the nearer claims), or None.

        Never to a tagged owner: its tag publishes anyway, and a re-credit kept only when it
        agrees with the tag would confirm the tag by construction (the 2026-10-07 refusal); one
        that disagrees reads a lower roof inside the tag's claim (Cartagena seed_1: low
        buildings in front of Allure, 190 m tag, re-credited 68 and 83 m)."""
        from dataclasses import replace

        j = v.far_poly
        if j is None or v.far_fp is None or v.far_fp < 0:
            return None
        q = self.polys[j]
        if q.tagged:
            return None
        xs = self._cols(int(m.x0), int(m.x1))
        e_t = float(self.elev(float(m.top_row)))
        kind, own, _ivF, _cr = self.owners(self.by_fp[int(m.footprint)], xs, e_t)
        sel = (kind == "farther") & (own == j)
        if not sel.any():
            return None
        iv = ray_intervals(q.xy, self.bear[xs[sel]])
        t = math.tan(math.radians(e_t))
        hs = self.h + np.where(t < 0.0, iv[:, 1], iv[:, 0]) * t
        d_in = iv[:, 0]
        hG, dG = float(np.median(hs)), float(np.median(d_in))
        # G's own walk over those columns (F's claim now a nearer one): the top must be G's
        kindG, _ownG, ivG, crG = self.owners(j, xs[sel], e_t)
        okG = np.isfinite(ivG[:, 0])
        if not okG.any() or float(np.mean(kindG[okG] == "self")) < SELF_MIN - 1e-9:
            return None
        base_rows = self.row(base_elev(self.h, ivG[:, 0]))
        frac = float(np.median(np.clip((crG[okG] - m.top_row) / np.maximum(
            1.0, base_rows[okG] - m.top_row), 0.0, 1.0))) if okG.any() else 0.0
        x0 = int(max(m.x0, math.ceil(q.c0)))
        x1 = int(min(m.x1, math.floor(q.c1)))
        base = float(np.median(base_rows[okG])) if okG.any() else float(m.base_row)
        fp = self.footprints[q.fp] if self.footprints is not None else None
        return replace(m, footprint=q.fp, x0=x0, x1=x1, base_row=base, dist_m=dG,
                       height_m=hG, base_visible=False, visible_frac=frac,
                       name=getattr(fp, "name", m.name),
                       osm_height_m=getattr(fp, "osm_height_m", None),
                       bottom_row=float(min(m.bottom_row, base)))


def assess(pano: fd.Pano, pose: fd.PanoPose, footprints: list, upper: list, ms: list,
           instances=None, depth=None, occluders=None) -> dict:
    """``{m.footprint: Verdict}`` for the readings ``ms`` (see :class:`Scene`)."""
    sc = Scene(pano, pose, footprints, upper, instances=instances, depth=depth,
               occluders=occluders)
    out = {}
    for m in ms:
        v = sc.assess(m)
        if v is not None:
            out[int(m.footprint)] = v
    return out


# --------------------------------------------------------------------------- the rule


def not_owned(v: Verdict | None) -> str | None:
    """Why a reading's top is not its footprint's (None: it is), from its :class:`Verdict`. The
    walk counts only hard bounds besides F (``Verdict.owner``): a tag or a cadastre count.

    - ``owner_nearer``: a nearer tagged (or cadastre) footprint's claim holds the top (the
      isovist fails);
    - ``owner_farther``: the ray clears F's own claim and a farther tagged footprint's holds it;
    - ``owner_above_tag``: the top is above F's tag (+25 %) or cadastre bound and no tagged
      footprint explains it.

    A prior p90 never takes a top away: the p90 holds only 0-22 % of the untagged buildings
    over 60 m (training set, leave one city out). With it, Cartagena's untagged 15-21-floor
    plots (b0191 55 m, b0339 66, b0411 70, b0447 81, drone readings a stereo or lean reading
    corroborated) lost their readings to the p90 claims of untagged towers behind them; Miami,
    nearly all tagged, scored the same either way.
    The own-corner test (:meth:`Scene.corner_edges`) is not part of the rule: on the Miami
    states it found no edge at 10 of 36 right readings and at 10 of 28 wrong ones."""
    if v is None or v.owner in ("self", "unknown"):
        return None
    if v.owner == "nearer":
        return "owner_nearer"
    if v.owner == "farther":
        return "owner_farther"
    return "owner_above_tag" if v.upper_hard else None


@dataclass
class SeedOwnership:
    """One seed's ownership: ``verdicts`` ``{Measured.footprint: Verdict}`` (trusted readings),
    ``reasons`` ``{Measured.footprint: not_owned reason}`` for the readings that are not their
    footprint's, and ``recredits`` ``[(from footprint, Measured)]``: those readings as readings
    of their farther geometric owner (footprint indices as in the seed's feature ids)."""
    verdicts: dict = field(default_factory=dict)
    reasons: dict = field(default_factory=dict)
    recredits: list = field(default_factory=list)


#: Geometric ownership in the region run (``elevated.measure_elevated_seed`` computes it,
#: ``elevated.elevated_estimates`` applies it). Environment ``SKYLINE_OWNERSHIP`` (0/1)
#: overrides. Off until accepted: it passes the Miami marks, but review 2026-10-09 §4.5 asks for
#: a second truth city, and on Cartagena it meets the low-rise gate only with the cadastre.
OWNERSHIP = False
#: The AMB cadastre's ``Construccion`` polygons as occluders and its non-PH counts as upper
#: bounds (``co_catastro``; Cartagena). Off: the user decides (review 2026-10-09, item 3).
#: Environment ``SKYLINE_CADASTRE_OCCLUDERS`` (0/1) overrides.
CADASTRE_OCCLUDERS = False


def _env_flag(name: str, default: bool) -> bool:
    import os

    v = os.environ.get(name)
    return default if v is None or v == "" else v.strip().lower() not in ("0", "false", "no", "off")


def enabled() -> bool:
    return _env_flag("SKYLINE_OWNERSHIP", OWNERSHIP)


def cadastre_enabled() -> bool:
    return _env_flag("SKYLINE_CADASTRE_OCCLUDERS", CADASTRE_OCCLUDERS)


def _cadastre_for(pano: fd.Pano, records) -> tuple[dict | None, list | None]:
    """(bounds by feature id, occluders) from the cadastre covering the camera, or (None, None)."""
    from city2stl.height.providers import co_catastro as cc

    d = 0.03
    bbox = (pano.lat + d, pano.lat - d, pano.lon + d, pano.lon - d)    # north, south, east, west
    ds = cc.dataset_for(bbox)
    if ds is None:
        return None, None
    layer = cc.fetch_layer(ds)
    return cadastre_bounds(records, layer), cadastre_occluders(pano.lat, pano.lon, layer)


def seed_ownership(pano: fd.Pano, pose: fd.PanoPose, records, ms: list, instances=None,
                   depth=None, trusted=None, exclude_city: str | None = None,
                   cadastre: bool | None = None) -> SeedOwnership:
    """Ownership of one seed's readings ``ms`` (``Measured`` indexed into
    ``elevated.footprints_from_records(records)``): verdicts for the readings ``trusted``
    accepts (all when None), the reasons of those not owned (:func:`not_owned`) and the
    re-credits of the ones a farther footprint owns (kept when ``trusted`` accepts them: the
    re-credited reading has its base hidden, so it needs a sky-topped top over half its height
    seen above the nearer claims, or a confident roof fit). ``cadastre``: use the cadastre
    occluders (default :func:`cadastre_enabled`)."""
    from .elevated import footprints_from_records

    fps, fids = footprints_from_records(records)
    keep = {str(f) for f in fids}
    recs = [r for r in records if str(r.feature_id) in keep]
    use_cad = cadastre_enabled() if cadastre is None else cadastre
    bounds = occl = None
    if use_cad:
        bounds, occl = _cadastre_for(pano, recs)
    upd = upper_heights(recs, exclude_city, bounds)
    sc = Scene(pano, pose, fps, [upd[f] for f in fids], instances=instances, depth=depth,
               occluders=occl)
    out = SeedOwnership()
    for m in ms:
        if trusted is not None and not trusted(m):
            continue
        v = sc.assess(m)
        if v is None:
            continue
        out.verdicts[int(m.footprint)] = v
        why = not_owned(v)
        if why is None:
            continue
        out.reasons[int(m.footprint)] = why
        if why in ("owner_farther", "owner_above_tag"):
            rc = sc.recredit(m, v)
            if rc is not None and (trusted is None or trusted(rc)):
                out.recredits.append((int(m.footprint), rc))
    return out


__all__ = ["Upper", "Verdict", "Scene", "SeedOwnership", "assess", "not_owned", "seed_ownership",
           "upper_heights", "prior_upper", "cadastre_bounds", "cadastre_occluders",
           "cadastre_bound", "claim_top", "enabled", "cadastre_enabled"]
