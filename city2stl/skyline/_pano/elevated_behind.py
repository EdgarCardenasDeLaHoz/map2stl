"""Part of ``city2stl.skyline._pano.elevated`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
import math
from dataclasses import replace

import numpy as np

from .. import footprint_detect as fd
from .._core import tiers as _tiers
from .._core.types import BuildingRecord
from .elevated_ground import trusted
from .elevated_pano import ElevatedSeed, footprints_from_records

logger = logging.getLogger('city2stl.skyline._pano.elevated')


#: A reading's top row within this many pixels (of the default spin pano, scaled by
#: ``footprint_detect.px_scale``) of where a farther OSM-tagged footprint over its columns puts
#: its tagged top is that footprint's top (:func:`_tag_behind`); None turns the test off.
BEHIND_TOL_PX: float | None = 4.0


#: ... a run counts only when its top-row MobileSAM instance differs from its base-row one in at
#: least this share of its core columns (the run crossed from the low building onto another).
BEHIND_INST_DIFF = 0.3


#: ... and roof-fit fills are tested too. Off: on Miami (2026-10-07, LiDAR truth) the test
#: dropped 6 wrong fills and 5 right ones (One Tequesta Point 106/95 m, b6312 151/152 m): a fill's
#: top often lines up with a tagged tower behind by chance, and fills have no instance change to
#: confirm the crossing.
BEHIND_FILLS = False


#: Farther footprints must be this much farther than the reading's (nearest vertex).
BEHIND_DIST_RATIO = 1.15


def _tag_behind(pano: fd.Pano, pose: fd.PanoPose, footprints):
    """``explained(m, pano) -> bool``: does a farther OSM-tagged footprint over at least 30 % of
    ``m``'s columns (of the narrower span) put its tagged top on ``m``'s top row (within
    :data:`BEHIND_TOL_PX`)? Then ``m`` read that tower over a lower building in front."""
    from .roof_fit import _candidates

    if BEHIND_TOL_PX is None:
        return lambda m, p: False
    h = pose.camera_h_m
    tags = [(c.dn, c.x0, c.x1, float(fd._row_of(pano, pose, math.degrees(math.atan2(
                float(footprints[c.i].osm_height_m) - h, c.dn)))))
            for c in _candidates(pano, pose, footprints, 3500.0, 1)
            if footprints[c.i].osm_height_m]
    if not tags:
        return lambda m, p: False
    arr = np.array(tags, float)
    tol = BEHIND_TOL_PX * fd.px_scale(pano)

    def explained(m, p) -> bool:
        far = arr[:, 0] >= BEHIND_DIST_RATIO * float(m.dist_m)
        ov = (np.minimum(arr[:, 2], m.x1) - np.maximum(arr[:, 1], m.x0)) / np.maximum(
            1.0, np.minimum(arr[:, 2] - arr[:, 1], m.x1 - m.x0))
        hit = far & (ov >= 0.3) & (np.abs(arr[:, 3] - float(m.top_row)) <= tol)
        return bool(hit.any())
    return explained


def _inst_change(m, instances, core: float = 0.6) -> float:
    """Share of ``m``'s core columns whose MobileSAM instance at the top row differs from the one
    at the bottom row (both labelled)."""
    if instances is None:
        return 0.0
    Hh, W = instances.shape
    xs = np.arange(m.x0, m.x1 + 1)
    cut = int(len(xs) * (1 - core) / 2)
    xs = xs[cut:len(xs) - cut]
    xs = xs[(xs >= 0) & (xs < W)]
    if not len(xs):
        return 0.0
    it = instances[min(Hh - 1, int(round(m.top_row)) + 1), xs]
    ib = instances[max(0, int(round(m.bottom_row)) - 1), xs]
    return float(np.mean((it > 0) & (ib > 0) & (it != ib)))


def _untrust_behind(m, explained, instances, pano):
    """A trusted sky-topped run whose top is a farther tagged tower's (:func:`_tag_behind`) and
    whose top instance is not its base's becomes ``top_edge="behind"`` (not :func:`trusted`).
    2026-10-07, Miami seed_3 sphere: Kaseya Center (LiDAR 43 m) read 138 m, its run climbing from
    the arena (instance 50) onto the towers of Museum Park behind it (instances 25, 20)."""
    if (m.top_edge != "sky" or not trusted(m) or not explained(m, pano)
            or _inst_change(m, instances) < BEHIND_INST_DIFF):
        return m
    return replace(m, top_edge="behind")


#: F-SKY26 step 6, the tower behind without a tag. A trusted reading on footprint F is untrusted
#: (:func:`tower_behind`) when both hold:
#: - F's satellite says low: it has readings of :data:`SAT_LOW_METHODS` with confidence >=
#:   :data:`SAT_LOW_CONF`, all under :data:`SAT_LOW_M`, and the reading is over
#:   :data:`SAT_LOW_FACTOR` x their largest (at least :data:`SAT_LOW_FLOOR_M`);
#: - a footprint G at least ``BEHIND_DIST_RATIO`` x farther over its columns
#:   (:func:`behind_map`) would be :data:`CREDIT_MIN_M` or taller with the reading's top row as
#:   its top, and G's own evidence agrees within :data:`CREDIT_TOL`: its OSM tag, any seed's
#:   trusted drone reading of G, or a satellite reading of G (same methods and confidence) of
#:   ``SAT_LOW_M`` or more.
#: Neither alone is safe. 2026-10-07, saved states of Cartagena seeds 1/4/5/6/7 and Miami seeds
#: 2/3/4 (+ spheres), every trusted reading:
#: - the geometric test alone (a G with evidence explains the top) flagged 6 of 14 Cartagena
#:   readings within 25 % of their tag and 20 of 37 Miami readings within 25 % of LiDAR, against
#:   3 / 63 / 24 wrong ones: adjacent towers explain each other's tops (the shared-top-edge check
#:   refused 2026-10-06);
#: - the satellite test alone is unsafe on 40-90 m towers whose stereo failed (b0289, tag 90 m:
#:   stereo 2 m at conf 1.0, multiview 2 m at 0.86; 9 of 16 tagged 40-80 m footprints have
#:   every confident reading under 40 m);
#: - together (with SAT_MIN_M and the shadow block): 65 readings on 61 footprints, none within
#:   25 % of a tag; 3 of 4 tag-wrong readings (b0634 69/10 m, b0628 105/42 m, b0622 74/6 m) and 51
#:   of 141 readings over 2x every satellite reading; 0 of 16 near a confident satellite reading.
#: Floors (the plan's cue: an accepted floor instance on the top matched to a farther plot)
#: matched 0 wrong readings and 3 right ones: too few instances pass the floor checks that far
#: out (0-38 a seed). Miami has no satellite readings, so nothing changes there.
SAT_LOW_M = 40.0


SAT_LOW_CONF = 0.5


#: A lean under SAT_LOW_M counts as low for the veto when its uncapped peak confidence is >= this.
SAT_LOW_PEAK_CONF = 0.7


SAT_LOW_FACTOR = 2.0


SAT_LOW_FLOOR_M = 5.0


SAT_LOW_METHODS = ("ls", "lean", "stereo", "multiview")


#: Readings under this are failures (ground matched), not a low roof: b0289 (tag 90 m) stereo
#: 2 m, b0075 (drone 111-118 m from two seeds) stereo 1.2 m.
SAT_MIN_M = 3.0


CREDIT_TOL = 0.25


CREDIT_MIN_M = 30.0


def behind_map(pano: fd.Pano, pose: fd.PanoPose, footprints, feature_ids, ms) -> dict:
    """For each trusted reading in ``ms``: the footprints at least ``BEHIND_DIST_RATIO`` x
    farther (nearest vertex) over >= 30 % of its columns (of the narrower span), each with the
    height its top would have on the reading's top row (camera height + range x tan(elevation)):
    ``{m.footprint: ((feature_id, height_m, osm_height_m | None), ...)}``."""
    from .roof_fit import _candidates

    ms = [m for m in ms if trusted(m)]
    if not ms:
        return {}
    cands = _candidates(pano, pose, footprints, 3500.0, 1)
    if not cands:
        return {}
    arr = np.array([(c.dn, c.i, c.x0, c.x1) for c in cands], float)
    h = float(pose.camera_h_m)
    out = {}
    for m in ms:
        far = arr[:, 0] >= BEHIND_DIST_RATIO * float(m.dist_m)
        ov = (np.minimum(arr[:, 3], m.x1) - np.maximum(arr[:, 2], m.x0)) / np.maximum(
            1.0, np.minimum(arr[:, 3] - arr[:, 2], m.x1 - m.x0))
        t = math.tan(math.radians(float(fd._elev_of(pano, pose, m.top_row))))
        got = []
        for dn, i, _x0, _x1 in arr[far & (ov >= 0.3)]:
            hg = h + float(dn) * t
            if hg >= CREDIT_MIN_M:
                f = footprints[int(i)]
                got.append((feature_ids[int(i)], hg, float(f.osm_height_m) if f.osm_height_m
                            else None))
        if got:
            out[m.footprint] = tuple(got)
    return out


def seed_ownership(pano: fd.Pano, pose: fd.PanoPose, buildings: list[BuildingRecord], ms: list,
                   depth=None, instances=None, seed_name: str = ""):
    """``ownership.seed_ownership`` of a seed's trusted readings (review 2026-10-09, item 3), or
    None when off (``ownership.enabled``) or when it fails (the run goes on without it).
    Computed after the cached measurement (its key unchanged), while the pano and the instance
    map are still in memory."""
    from . import ownership

    if not ownership.enabled():
        return None
    try:
        got = ownership.seed_ownership(pano, pose, buildings, ms, instances=instances,
                                       depth=depth, trusted=trusted)
    except Exception as exc:                          # never lose the seed to the check
        logger.warning("[elevated] %s: ownership failed (%r)", seed_name, exc)
        return None
    if got.reasons:
        from collections import Counter

        fids = footprints_from_records(buildings)[1]
        logger.info("[elevated] %s: ownership: %d of %d trusted readings not their footprint's "
                    "(%s), %d re-credited to a farther owner%s", seed_name, len(got.reasons),
                    len(got.verdicts), dict(Counter(got.reasons.values())), len(got.recredits),
                    "".join(f"; {fids[a]} -> {fids[m.footprint]} {m.height_m:.0f} m"
                            for a, m in got.recredits))
    return got


def not_owned(seeds) -> dict:
    """``{(seed_name, feature_id): reason}`` of the trusted readings whose top is not their
    footprint's (:func:`seed_ownership`)."""
    out = {}
    for s in seeds:
        own = getattr(s, "ownership", None)
        for i, why in (getattr(own, "reasons", None) or {}).items():
            out[(s.seed_name, s.feature_ids[i])] = why
    return out


def _mark_not_owner(seeds, reasons: dict) -> None:
    """``trusted`` False and ``untrusted_reason`` = the ownership reason on the report segments
    of the readings :func:`not_owned` lists (a re-credit segment keeps its own status)."""
    for s in seeds:
        for seg in getattr(s.pano_result, "matched_segments", None) or ():
            fid = (seg.get("matched_projection") or {}).get("feature_id")
            why = reasons.get((s.seed_name, fid))
            if why and seg.get("height_src") != "recredit":
                seg["trusted"] = False
                seg["untrusted_reason"] = why


def recredited(seeds, skip: dict | None = None) -> dict:
    """``{seed_name: [(feature_id, Measured)]}``: each seed's re-credited readings, at most one
    per footprint (the best weighted), and none for a footprint the seed already reads itself
    with a trusted reading of its own that is kept (not in ``skip``)."""
    skip = skip or {}
    out = {}
    for s in seeds:
        own = getattr(s, "ownership", None)
        if own is None or not own.recredits:
            continue
        have = {s.feature_ids[m.footprint] for m in s.measured if trusted(m)
                and (s.seed_name, s.feature_ids[m.footprint]) not in skip}
        best: dict = {}
        for _src, m in own.recredits:
            fid = s.feature_ids[m.footprint]
            if fid in have:
                continue
            if fid not in best or fd.measurement_weight(m) > fd.measurement_weight(best[fid]):
                best[fid] = m
        if best:
            out[s.seed_name] = sorted(best.items())
    return out


def _sat_fields(r) -> tuple[str, float, float]:
    if isinstance(r, dict):
        return str(r.get("method")), float(r.get("height_m")), float(r.get("conf"))
    return str(r.method), float(r.height_m), float(r.conf)


def _lean_peak_conf(r) -> float:
    """A lean's peak-shape confidence before ``calibrate`` capped it (``extra["conf_peak"]``)."""
    ex = r.get("extra") if isinstance(r, dict) else getattr(r, "extra", None)
    try:
        return float((ex or {}).get("conf_peak", 0.0))
    except (TypeError, ValueError):
        return 0.0


def _sat_max(rs, *, peak_lean: bool = False) -> float | None:
    """Largest confident satellite reading (:data:`SAT_LOW_METHODS`, conf >= SAT_LOW_CONF, at
    least :data:`SAT_MIN_M`); a confident shadow (a lower bound) at or over :data:`SAT_LOW_M`
    counts too, so it keeps the footprint from looking low. ``peak_lean`` (the tower-behind veto
    only): a lean under :data:`SAT_LOW_M` whose uncapped peak confidence was >= SAT_LOW_PEAK_CONF counts
    too, although fusion and the tiers keep its calibrated 0.3 (2026-10-09)."""
    v = []
    for r in rs or ():
        k, h, c = _sat_fields(r)
        if peak_lean and k == "lean" and h < SAT_LOW_M:
            if _lean_peak_conf(r) >= SAT_LOW_PEAK_CONF:
                c = max(c, SAT_LOW_CONF)
        if c >= SAT_LOW_CONF and ((k in SAT_LOW_METHODS and h >= SAT_MIN_M)
                                  or (k == "shadow" and h >= SAT_LOW_M)):
            v.append(h)
    return max(v) if v else None


def tower_behind(seeds: list[ElevatedSeed], satellite_raw: dict | None) -> dict:
    """``{(seed_name, feature_id): (G feature_id, G height_m, evidence_m)}``: the trusted readings
    that read a farther tower (see :data:`SAT_LOW_M`). ``satellite_raw``: ``{feature_id:
    [SatReading or its dict]}`` (``satellite_fusion.load_region``); without it nothing is
    flagged."""
    if not satellite_raw:
        return {}
    drone: dict = {}
    for s in seeds:
        for m in s.measured:
            if trusted(m):
                drone.setdefault(s.feature_ids[m.footprint], []).append(float(m.height_m))
    smax = {f: _sat_max(rs, peak_lean=True) for f, rs in satellite_raw.items()}
    out = {}
    for s in seeds:
        for m in s.measured:
            fid = s.feature_ids[m.footprint]
            low = smax.get(fid)
            if (not trusted(m) or low is None or low >= SAT_LOW_M
                    or float(m.height_m) <= SAT_LOW_FACTOR * max(low, SAT_LOW_FLOOR_M)):
                continue
            for g, hg, tag in s.behind.get(m.footprint, ()):
                ev = ([tag] if tag else []) + drone.get(g, [])
                sg = smax.get(g)
                if sg is not None and sg >= SAT_LOW_M:
                    ev.append(sg)
                e = next((e for e in ev if e >= CREDIT_MIN_M and _tiers.agree(hg, e, rel=CREDIT_TOL)),
                         None)
                if e is not None:
                    out[(s.seed_name, fid)] = (g, float(hg), float(e))
                    break
    return out
