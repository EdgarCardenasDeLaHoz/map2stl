"""Part of ``city2stl.skyline._pano.elevated`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging

import numpy as np

from .. import footprint_detect as fd
from .._core import tiers as _tiers
from .._core.types import BuildingRecord, RegisteredBuildingEstimate
from .elevated_behind import (
    SAT_LOW_M,
    _mark_not_owner,
    _sat_max,
    not_owned,
    recredited,
    tower_behind,
)
from .elevated_ground import _mark_tower_behind, estimate_confidence, trusted
from .elevated_pano import ElevatedSeed, footprints_from_records

logger = logging.getLogger('city2stl.skyline._pano.elevated')


#: Footprints within this range of the camera are candidates for a floor instance's plot (the
#: harness's ``--floors-range``; floors need >= 5 px a floor, ~770 m at hi-res anyway).
FLOORS_RANGE_M = 3000.0


#: MobileSAM instances smaller than this many pixels (at 1194 px focal length, scaled by f^2) are
#: not counted (the review's size cut).
FLOORS_MIN_PX = 4000


#: Floor counts join the fusion as weighted readings (F-SKY26 step 5) when the city's storey
#: calibration is reliable (``floor_bands.StoreyCalibration.reliable``).
FLOORS_IN_FUSION = True


#: A footprint only floors read (no drone, no satellite reading kept) publishes no height from
#: them: off by the score of 2026-10-08 (Cartagena: 47 floors-only footprints, the 2 with truth
#: both lower bounds reading 37 % low; no floors-only count with its base seen had truth).
FLOORS_PUBLISH_ALONE = False   # True needs an estimate builder for floors-only footprints


def seed_floors(pano: fd.Pano, pose: fd.PanoPose, depth, instances,
                buildings: list[BuildingRecord], max_range_m: float = FLOORS_RANGE_M) -> list:
    """``floor_bands.pano_floors`` on a measured pano: one dict per accepted instance matched to
    a footprint within ``max_range_m`` (``fid``, ``floors``, ``base_seen``, ``spread``,
    ``n_strips``, ``instance``, ``name``, ``tag_m``: the OSM height tag, ``osm_tag`` source only,
    for the storey calibration). Pose-dependent only through the plot match; the counts are not."""
    from .. import floor_bands as fl

    if instances is None:
        return []
    fps, fids = footprints_from_records(buildings)
    recs = {str(b.feature_id): b for b in buildings}
    sel, rings = [], []
    for i, f in enumerate(fps):
        r = fd._local(pano.lat, pano.lon, np.asarray(f.ring, float)[:, :2])
        if float(np.hypot(*r.mean(0))) <= max_range_m:
            sel.append(i)
            rings.append(r)
    if not rings:
        return []
    gray = pano.rgb.mean(-1).astype(np.float32)
    ids, cnt = np.unique(instances[instances > 0], return_counts=True)
    min_px = int(FLOORS_MIN_PX * (pano.f_px / 1194.0) ** 2)
    out = []
    for e, hit in fl.pano_floors(pano, pose, gray, instances, [int(i) for i in ids[cnt > min_px]],
                                 depth=depth, rings_xy=rings):
        if not hit:
            continue
        fid = fids[sel[hit[0]]]
        rec = recs.get(fid)
        tag = (float(rec.height_tag_m) if rec is not None and rec.height_tag_m
               and rec.height_source == "osm_tag" else None)
        # the storey at the matched wall's range, for floor_bands.pose_check (step 8)
        rng = float(hit[1])
        storey = (float(e.storey_m) if e.plot is not None
                  else fl.NOMINAL_FLOOR_M * rng / float(e.dist_m))
        out.append({"fid": fid, "floors": float(e.floors_visible), "base_seen": bool(e.base_seen),
                    "spread": float(e.spread), "n_strips": int(e.n_strips),
                    "instance": int(e.instance), "name": fps[sel[hit[0]]].name or fid,
                    "tag_m": tag, "storey_m": storey, "plot_dist_m": rng,
                    "bearing_deg": float(e.bearing_deg)})
    return out


def floor_entries(seeds: list[ElevatedSeed]) -> list[dict]:
    """Every seed's :attr:`ElevatedSeed.floors` as ``floor_bands`` entries (``seed`` added).
    ``lower_bound`` unless the base is in view: ground under the instance's mask
    (``InstanceFloors.base_seen``) or the same seed's reading of that footprint saw its base.
    With the mask test alone 140 of Cartagena's 153 counts were lower bounds and the storey
    calibration had 2 samples; with either, 85 and 6 (storey 4.13 m, sigma_log 0.13)."""
    out = []
    for s in seeds:
        base = {s.feature_ids[m.footprint] for m in s.measured if m.base_visible}
        for e in s.floors or ():
            out.append(dict(e, seed=s.seed_name,
                            lower_bound=not (e.get("base_seen") or e["fid"] in base)))
    return out


class ElevatedEstimates(list):
    """:func:`elevated_estimates`' list of estimates, plus ``floors``: ``{fid: {high_rise_seen,
    floors, lower_bound, storey_m, storey_sigma_log, storey_reliable}}`` for every footprint a
    seed counted floors on, and ``storey`` (``floor_bands.StoreyCalibration``). The publisher's
    hook is ``floor_bands.high_rise_height(prior_m, est.floors.get(fid))``."""

    floors: dict
    storey: object

    def __init__(self, items=(), floors=None, storey=None):
        super().__init__(items)
        self.floors = floors or {}
        self.storey = storey


#: A floors reading agrees with the same seed's base-visible reading of its plot within this
#: share (:func:`drop_contradicted_floors`).
FLOORS_SAME_SEED_AGREE = 0.25


def drop_contradicted_floors(seeds: list[ElevatedSeed], readings: dict) -> tuple[dict, list]:
    """``(readings kept, dropped)``: :func:`floor_bands.floor_readings` without the counts that
    the same seed's own base-visible reading of the plot (trusted or not) contradicts by more
    than :data:`FLOORS_SAME_SEED_AGREE`. One image cannot overrule itself: the count is an
    instance matched to the plot by range (and usually takes its "base in view" from that very
    reading), so a count far from the reading is another building's facade.

    Cartagena v10 (2026-10-09): such counts anchored the fusion over trusted drone readings that
    satellite readings confirmed, and four plots lost their drone height: b0582 Hotel Cartagena
    Plaza (seed_1 66 m, shadow 66, stereo 61; seed_1's count 35 floors = 148 m), b0426 (seed_4 72,
    stereo 74.5; seed_5's count 98.5 m against its own 72 m), b0635 (seed_4 44, lean 53.5 / 1.0,
    stereo 44; seed_6's count 73 m against its own 49 m), b0075 (seed_4 67; seed_5's count 35 m
    against its own 118 m). 20 of 145 counts drop; the b1158 catch (drone 183 m) and every
    tagged plot are unchanged. ``dropped``: ``(fid, seed, floors height, own heights)``."""
    own: dict = {}
    for s in seeds:
        for m in s.measured:
            if m.base_visible:
                own.setdefault((s.seed_name, s.feature_ids[m.footprint]), []).append(
                    float(m.height_m))
    kept, dropped = {}, []
    tol = FLOORS_SAME_SEED_AGREE
    for key, rs in readings.items():
        out = []
        for d in rs:
            hs = own.get((d.get("seed"), d["footprint"]), ())
            h = float(d["height_m"])
            if hs and not any(_tiers.agree(h, x, rel=tol) for x in hs):
                dropped.append((d["footprint"], d.get("seed"), h, list(hs)))
            else:
                out.append(d)
        if out:
            kept[key] = out
    return kept, dropped


def floors_info(seeds: list[ElevatedSeed], satellite_raw: dict | None = None):
    """``(floors info per fid, storey calibration, fusion readings)`` of the seeds' floor counts
    (F-SKY26 steps 4/5): the city's storey from the counts with their base in view on
    ``osm_tag`` plots (``floor_bands.calibrate_storey``), the high-rise flag
    (``floor_bands.high_rise_plots``, >= 10 floors, a lower bound counts, never on a plot whose
    confident satellite readings are all under 40 m), and ``floor_bands.floor_readings`` less the
    counts the same seed's own reading contradicts (:func:`drop_contradicted_floors`)."""
    from .. import floor_bands as fl

    ents = floor_entries(seeds)
    cal = fl.calibrate_storey([(e["floors"], e["tag_m"]) for e in ents
                               if e.get("tag_m") and not e["lower_bound"]])
    low = set()
    for f, rs in (satellite_raw or {}).items():
        m = _sat_max(rs)
        if m is not None and m < SAT_LOW_M:
            low.add(f)
    hr = fl.high_rise_plots(ents, low=low)
    storey = cal.storey_m if cal.reliable else fl.NOMINAL_FLOOR_M
    info: dict = {}
    for e in ents:
        f = e["fid"]
        cur = info.get(f)
        if cur is None or e["floors"] > cur["floors"]:
            info[f] = {"floors": e["floors"], "lower_bound": e["lower_bound"],
                       "seeds": sorted({x["seed"] for x in ents if x["fid"] == f})}
    for f, d in info.items():
        if f in hr:
            d["floors"], d["lower_bound"] = hr[f]
        d.update(high_rise_seen=f in hr, storey_m=round(storey, 3),
                 storey_sigma_log=None if cal.sigma_log is None else round(cal.sigma_log, 4),
                 storey_reliable=cal.reliable, sat_low=f in low,
                 floors_m=round(fl.floors_height(d["floors"], storey), 1))
    readings = fl.floor_readings(ents, cal) if FLOORS_IN_FUSION else {}
    if readings:
        readings, dropped = drop_contradicted_floors(seeds, readings)
        if dropped:
            logger.info("[elevated] floors: %d readings left out of fusion (the same seed's own "
                        "reading of the plot disagrees by more than %.0f %%)", len(dropped),
                        100 * FLOORS_SAME_SEED_AGREE)
    return info, cal, readings


def _split_satellite(satellite: dict | None) -> tuple[dict | None, dict | None]:
    """(fusion readings, raw readings) from either form: ``satellite_fusion.fusion_readings``
    dicts (``kind`` keys) or the raw ``load_region`` readings (``method``)."""
    if not satellite:
        return None, None
    first = next((r for rs in satellite.values() for r in rs), None)
    if first is None:
        return None, None
    if (first.get("kind") if isinstance(first, dict) else None):
        return satellite, None
    from city2stl.height.satellite.readings import SatReading

    from ..satellite_fusion import fusion_readings

    raw = {f: [SatReading.from_json(r) if isinstance(r, dict) else r for r in rs]
           for f, rs in satellite.items()}
    return fusion_readings(raw), raw


def elevated_estimates(seeds: list[ElevatedSeed],
                       satellite: dict | None = None) -> ElevatedEstimates:
    """One estimate per footprint and seed, from :func:`trusted` readings only, for footprints
    whose seeds agree (``fuse_heights``, 25 %): a footprint the seeds disagree on gets no drone
    height at all, rather than the most reliable view's, since any of them may be the misread.

    ``satellite``: ``{feature_id: [fuse_heights dict]}`` (``satellite_fusion.fusion_readings``,
    site flag ``use_satellite_heights``). They join the fusion of the footprints a drone seed read,
    one pseudo-seed per kind (``sat_lean`` ...): weighted by sigma_log, they can outvote and so
    dispute a drone reading, or anchor which drone readings are kept. Only drone readings are
    emitted: a satellite key in ``used`` has no ``by_key`` entry and is skipped.

    Raw satellite readings (``satellite_fusion.load_region``, ``method`` keys) are converted
    here and also drive :func:`tower_behind`: the readings it flags are left out (F-SKY26 step 6;
    no re-credit to the tower behind: that tower is chosen by agreeing evidence).

    Geometric ownership (:attr:`ElevatedSeed.ownership`, review 2026-10-09 item 3): a reading
    whose top a nearer footprint's claim holds, or that clears its footprint's claim, leaves
    (:func:`not_owned`); one a farther footprint owns by line of sight joins as that footprint's
    reading (:func:`recredited`), since line of sight, not height evidence, picks it.

    Floor counts (:attr:`ElevatedSeed.floors`, F-SKY26 steps 4/5, :func:`floors_info`): with a
    reliable storey calibration they join the fusion as ``kind`` "floors" readings (they can
    dispute a drone reading, and verify one from another seed); they are never emitted, and a
    footprint only floors read publishes nothing (:data:`FLOORS_PUBLISH_ALONE`). The result's
    ``floors`` carries the per-footprint ``high_rise_seen`` / ``floors`` / ``storey_m`` for the
    publisher (``floor_bands.high_rise_height``)."""
    satellite, raw = _split_satellite(satellite)
    behind = tower_behind(seeds, raw)
    _mark_tower_behind(seeds, behind)
    if behind:
        logger.info("[elevated] tower behind: %d trusted readings on %d footprints untrusted "
                    "(satellite low, a farther footprint's evidence explains the top)",
                    len(behind), len({f for _s, f in behind}))
    # geometric ownership (review 2026-10-09, item 3): readings whose top is not their
    # footprint's leave; those a farther footprint owns by line of sight are its readings
    unowned = not_owned(seeds)
    _mark_not_owner(seeds, unowned)
    left = {**behind, **unowned}
    rcs = recredited(seeds, left)
    if unowned:
        logger.info("[elevated] ownership: %d trusted readings on %d footprints not their "
                    "footprint's; %d re-credited to %d farther footprints", len(unowned),
                    len({f for _s, f in unowned}), sum(map(len, rcs.values())),
                    len({f for v in rcs.values() for f, _m in v}))
    by_seed = {s.seed_name: [dict(m.__dict__, footprint=s.feature_ids[m.footprint])
                             for m in s.measured if trusted(m)
                             and (s.seed_name, s.feature_ids[m.footprint]) not in left]
                            + [dict(m.__dict__, footprint=f, recredit=True)
                               for f, m in rcs.get(s.seed_name, ())]
               for s in seeds}
    if satellite:
        drone_fids = {d["footprint"] for ms in by_seed.values() for d in ms}
        for fid in drone_fids:
            for d in satellite.get(fid, ()):
                by_seed.setdefault(d["kind"], []).append(d)
    finfo, cal, floor_rs = floors_info(seeds, raw)
    if finfo:
        logger.info("[elevated] floors: %d footprints counted, %d high-rise; storey %.2f m "
                    "(n %d, sigma_log %s%s); %d floors readings in fusion",
                    len(finfo), sum(d["high_rise_seen"] for d in finfo.values()), cal.storey_m,
                    cal.n, cal.sigma_log, "" if cal.reliable else ", not reliable",
                    sum(len(v) for v in floor_rs.values()))
    by_seed.update(floor_rs)
    fused = fd.fuse_heights(by_seed)
    by_key = {(s.seed_name, s.feature_ids[m.footprint]): (s, m) for s in seeds for m in s.measured}
    by_key.update({(s.seed_name, f): (s, m) for s in seeds for f, m in rcs.get(s.seed_name, ())})
    # the segments carry each footprint's true bearing (their columns are rolled north-centre)
    bearing = {(s.seed_name, seg["matched_projection"]["feature_id"]): seg["true_bearing_deg"]
               for s in seeds for seg in s.pano_result.matched_segments}
    out = ElevatedEstimates(floors=finfo, storey=cal)
    for fid, f in fused.items():
        if f["disputed"]:
            # seeds that saw it disagree (Cartagena 2026-10-06: seed pairs agreed within 25 % on
            # 5-36 % of shared footprints, the far or older view reading another building):
            # no drone height; the untagged prior or the tag takes over
            continue
        for seed_name in f["used"]:
            if (seed_name, fid) not in by_key:      # a satellite pseudo-seed
                continue
            s, m = by_key[(seed_name, fid)]
            b = int(round(bearing.get((seed_name, fid), 0.0))) % 360
            out.append(RegisteredBuildingEstimate(
                feature_id=fid, name=m.name, view_name=f"{seed_name}_{b:03d}",
                heading_offset_deg=float(s.position.pose.offset_deg), x_px=0.5 * (m.x0 + m.x1),
                y_px=float(m.top_row), forward_m=float(m.dist_m),
                estimated_height_m=float(m.height_m), confidence=estimate_confidence(m)))
    return out
