"""Satellite heights in the skyline run (F-SKY26 step 7): the adapter from
``runs/satellite/<region>/readings.json`` (written offline by ``scripts/20_satellite_heights.py``,
``city2stl.height.satellite``) to what fusion, the tiers and publishing take.

Opt-in per site: ``use_satellite_heights`` in ``sites/<region>.json``. The run only reads the file;
it never measures or fetches imagery.

- ``load_region``: readings per region footprint id, calibrated on load (``readings.calibrate``;
  2026-10-09: a stored file written before calibration, or kept by ``--add``, is not used as
  stored); a reading is kept only when its stored centroid is within ``MATCH_M`` of the run's
  footprint (ids are the sorted-centroid order of the OSM dump, so another dump can renumber
  them); otherwise it is re-matched by centroid.
- ``fusion_readings``: ``footprint_detect.satellite_reading`` dicts per footprint for
  ``fuse_heights`` (``elevated_estimates``): drone-equivalent distance from sigma_log, shadows as
  lower bounds; ``ls`` (lean and shadow agreeing) replaces its lean and shadow.
- ``tier_readings``: ``tiers.reading`` dicts (lean, shadow, stereo, multiview): drone + satellite
  at 40 m or more and lean + shadow over 100 m verify (``tiers.INDEPENDENT``); two satellite
  readings of one kind never do.
- ``publishable``: the satellite-only height an untagged row without a drone reading may publish
  instead of the prior: the satellite readings fused alone, published only when the best kept
  reading's sigma_log is at most ``PUBLISH_MAX_SIGMA_LOG``.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from collections.abc import Sequence
from pathlib import Path

from city2stl.height.satellite import readings as sr
from city2stl.height.satellite.weights import sigma_log

from . import footprint_detect as fd
from ._core.tiers import reading as tier_reading

log = logging.getLogger(__name__)

RUNS = Path(__file__).resolve().parent / "runs"
#: A stored reading belongs to a run footprint whose centroid is within this distance.
MATCH_M = 3.0
#: A satellite-only height replaces the prior only when its best kept reading's sigma_log is at
#: most this: within the 25 % yardstick at 1 sigma (lean >= 40 m conf >= 0.5, shadow > 40 m,
#: stereo >= 40 m, multiview, shadow < 15 m at conf >= 0.7). Noisier readings still dispute and
#: verify through fusion and the tiers, but the prior stays published.
PUBLISH_MAX_SIGMA_LOG = 0.25
#: tiers kind of each satellite method (``ls`` is fusion-only: its lean and shadow are kept).
TIER_KIND = {"lean": "lean", "shadow": "shadow", "stereo": "stereo", "multiview": "multiview"}


def readings_path(region: str) -> Path:
    return RUNS / "satellite" / region / "readings.json"


def _dist_m(lat1, lon1, lat2, lon2) -> float:
    k = 111320.0
    return math.hypot((lat1 - lat2) * k, (lon1 - lon2) * k * math.cos(math.radians(lat1)))


def load_region(region: str, records: Sequence, path: str | Path | None = None) -> dict:
    """``{feature_id: [SatReading]}`` for the run's ``records``; {} when there is no file. The
    readings are calibrated on load (``readings.load``): an uncalibrated file is logged (run
    ``20_satellite_heights.py --recalibrate`` to rewrite it) but never used as stored."""
    meta, data = sr.load(path or readings_path(region), calibrate=True)
    if not data:
        log.info("[satellite] no readings for %s (%s)", region, path or readings_path(region))
        return {}
    if meta.get("n_recalibrated"):
        log.warning("[satellite] %s: %d footprints' stored readings were not calibrated; "
                    "calibrated on load (20_satellite_heights.py --recalibrate rewrites the file)",
                    region, meta["n_recalibrated"])
    by_id = {r.feature_id: r for r in records}
    out, moved = {}, 0
    loose = []
    for fid, v in data.items():
        r = by_id.get(fid)
        if r is not None and v.get("lat") is not None and \
                _dist_m(r.centroid_lat, r.centroid_lon, v["lat"], v["lon"]) <= MATCH_M:
            out[fid] = v["readings"]
        elif v.get("lat") is not None:
            loose.append(v)
    if loose:                                   # renumbered: match by centroid
        grid = defaultdict(list)
        for r in records:
            grid[(round(r.centroid_lat, 3), round(r.centroid_lon, 3))].append(r)
        for v in loose:
            cands = [r for dy in (-0.001, 0, 0.001) for dx in (-0.001, 0, 0.001)
                     for r in grid.get((round(v["lat"] + dy, 3), round(v["lon"] + dx, 3)), [])]
            best = min(cands, key=lambda r: _dist_m(r.centroid_lat, r.centroid_lon, v["lat"], v["lon"]),
                       default=None)
            if best is not None and best.feature_id not in out and \
                    _dist_m(best.centroid_lat, best.centroid_lon, v["lat"], v["lon"]) <= MATCH_M:
                out[best.feature_id] = v["readings"]
                moved += 1
    log.info("[satellite] %s: %d footprints with readings (%d re-matched by centroid; scenes %s)",
             region, len(out), moved, ", ".join(sorted(meta.get("scenes") or {})))
    return out


def fusion_readings(sat: dict) -> dict:
    """``{fid: [fuse_heights dict]}``: one per kept method, ``ls`` in place of its lean and
    shadow (as the F-SKY26 harness ``--sat-mode sigma``)."""
    out = {}
    for fid, rs in sat.items():
        by = {r.method: r for r in rs}
        ds = []
        for m in ("ls", "lean", "shadow", "stereo", "multiview"):
            if m not in by or (m in ("lean", "shadow") and "ls" in by):
                continue
            d = fd.satellite_reading(fid, m, by[m].height_m, by[m].conf)
            if d is not None:
                ds.append(d)
        if ds:
            out[fid] = ds
    return out


def tier_readings(rs: Sequence) -> list[dict]:
    """``tiers.reading`` dicts of one footprint's satellite readings."""
    return [tier_reading(TIER_KIND[r.method], r.height_m, None) for r in rs if r.method in TIER_KIND]


def publishable(rs: Sequence) -> dict | None:
    """The satellite-only height of one footprint, or None: ``{height_m, methods, sigma_log,
    lower_bound}``. The kept readings of ``fuse_heights`` over the satellite readings alone (each
    method its own key); published when the best kept sigma_log <= ``PUBLISH_MAX_SIGMA_LOG``."""
    ds = fusion_readings({"_": rs}).get("_")
    if not ds:
        return None
    f = fd.fuse_heights({d["kind"]: [d] for d in ds}).get("_")
    if f is None:
        return None
    kept = [d for d in ds if d["kind"] in f["used"]]
    sig = min((sigma_log(d["kind"][4:], d["height_m"], d["confidence"]) for d in kept), default=None)
    if sig is None or sig > PUBLISH_MAX_SIGMA_LOG:
        return None
    return dict(height_m=float(f["height_m"]), methods=sorted(d["kind"] for d in kept),
                sigma_log=float(sig), lower_bound=bool(f["lower_bound"]))


__all__ = ["load_region", "fusion_readings", "tier_readings", "publishable", "readings_path",
           "MATCH_M", "PUBLISH_MAX_SIGMA_LOG"]
