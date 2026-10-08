"""Per-footprint satellite readings: what fusion and the verification tiers consume.

One footprint gets at most one reading per method (``SatReading``):

- ``lean``: the reference scene's roof shift (``measure.lean_height``);
- ``shadow``: every scene's shadow merged to **one** reading (the conf-weighted mean of the
  largest group within 25 %): shadows of one building are cut by the same podium in every scene,
  so they are one source (decision 2026-10-07). A shadow is a ``lower_bound``;
- ``stereo``: the pair consensus across dated scenes (``measure.consensus``);
- ``multiview``: the plane-sweep NCC peak over all scene pairs (``stereo_multiview``);
- ``ls``: lean and shadow agreeing (within 25 %): the conf-weighted mean, conf the larger.

Readings under ``weights.MIN_CONF`` (0.15) are not made. The rules are those of the F-SKY26
harness ``Code/claude/scripts/seed_experiment.py::load_sat`` (2026-10-07), which the satellite
weights were scored with; ``tests/test_satellite_heights.py`` checks they match. Since
2026-10-08 :func:`calibrate` then corrects two confidences the measurement's peak shape gets
wrong (LiDAR-checked; ``calibrate=False`` gives the 2026-10-07 readings):

- a lean under ``weights.TALL_M`` is capped at :data:`LOW_LEAN_MAX_CONF`;
- a stereo or multiview reading a confident tall lean is :data:`UNDER_LEAN_FACTOR` x or more
  above is dropped: the sweep matched a lower level of the footprint (podium, setback). Its
  height stays in the lean's ``extra`` (``stereo_under_lean``, ``multiview_under_lean``).

``readings.json`` (``runs/satellite/<region>/readings.json``) holds ``{"_meta": {...}, fid:
{"lat", "lon", "osm_id", "readings": [SatReading as dict]}}``; ``_meta.scenes`` lists the scene
names and dates the readings come from (the cache key).
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from .weights import MIN_CONF, MULTIVIEW_MIN_CONF, TALL_M

#: Two readings agree within this share (the benchmark's 25 % yardstick).
AGREE = 0.25
#: Methods a footprint can have, in the order fusion considers them.
METHODS = ("ls", "lean", "shadow", "stereo", "multiview")
READINGS_VERSION = 1
#: A lean under ``TALL_M`` is not a roof reading whatever its peak shape. LiDAR (2026-10-08,
#: S/satellite_cities/val): Chicago lean 15-40 m at conf >= 0.7 within 25 % of truth 1 of 61
#: (44 % under 0.6x truth), 3-15 m 1 of 16; Cartagena Ravello (published 144 m) lean 38 m at conf
#: 1.0. Capped at this, nothing reads it as confident (``elevated.tower_behind`` takes >= 0.5);
#: the peak-shape value stays in ``extra["conf_peak"]``. Fusion already drops it (weights).
LOW_LEAN_MAX_CONF = 0.3
#: A lean of at least ``TALL_M`` and :data:`LEAN_TRUST_CONF` this many times a stereo or
#: multiview reading drops that reading. Chicago (2026-10-08): stereo >= 40 m conf >= 0.6 with
#: such a lean 1.25x or more above it: stereo within 25 % of LiDAR 1 of 20, the lean 16 of 20;
#: within 1.25x both are about as good (66 % / 59 %, n 98). Cartagena Allure (180 m): lean 168 m,
#: stereo 77 m at conf 0.83.
UNDER_LEAN_FACTOR = 1.25
LEAN_TRUST_CONF = 0.7


@dataclass
class SatReading:
    """One satellite height reading of one footprint."""

    method: str                       # lean | shadow | stereo | multiview | ls
    height_m: float
    conf: float
    scene: str | None = None          # scene(s) it comes from ("+"-joined for merged shadows)
    lower_bound: bool = False         # a shadow: "at least"
    n_scenes: int = 1                 # shadow scenes in the merged group / scenes in the sweep
    extra: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        d = asdict(self)
        d["height_m"] = round(float(d["height_m"]), 2)
        d["conf"] = round(float(d["conf"]), 3)
        if not d["extra"]:
            d.pop("extra")
        return d

    @classmethod
    def from_json(cls, d: dict) -> SatReading:
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def _agree(a: float, b: float) -> bool:
    return abs(a - b) <= AGREE * max(1.0, max(a, b))


def group(vals: list[tuple[float, float]]) -> tuple[float, float, int, list]:
    """The conf-weighted mean of the largest-conf group within 25 % of one member:
    ``(height, max conf, n, members)`` (``seed_experiment._group``)."""
    best = None
    for h0, _ in vals:
        g = [(h, c) for h, c in vals if abs(h - h0) <= AGREE * max(1.0, h0)]
        s = sum(c for _, c in g)
        if best is None or s > best[0] or (s == best[0] and len(g) > len(best[1])):
            best = (s, g)
    g = best[1]
    w = np.array([c for _, c in g])
    return float(np.average([h for h, _ in g], weights=w)), float(w.max()), len(g), g


def calibrate(rs: list[SatReading]) -> list[SatReading]:
    """One footprint's readings with the 2026-10-08 confidence corrections (module docstring):
    lean under ``TALL_M`` capped at :data:`LOW_LEAN_MAX_CONF`; stereo / multiview under a
    confident tall lean (:data:`UNDER_LEAN_FACTOR`) dropped. Idempotent; the input is not
    changed."""
    by = {r.method: r for r in rs}
    lean = by.get("lean")
    if lean is None:
        return list(rs)
    if lean.height_m < TALL_M and lean.conf > LOW_LEAN_MAX_CONF:
        lean = SatReading(**{**lean.__dict__, "conf": LOW_LEAN_MAX_CONF,
                             "extra": {**lean.extra, "conf_peak": lean.conf}})
    elif lean.height_m >= TALL_M and lean.conf >= LEAN_TRUST_CONF:
        drop = {m: by[m].height_m for m in ("stereo", "multiview")
                if m in by and lean.height_m >= UNDER_LEAN_FACTOR * by[m].height_m}
        if drop:
            lean = SatReading(**{**lean.__dict__, "extra": {
                **lean.extra, **{f"{m}_under_lean": round(h, 1) for m, h in drop.items()}}})
            rs = [r for r in rs if r.method not in drop]
    return [lean if r.method == "lean" else r for r in rs]


def footprint_readings(*, lean: dict | None = None, shadows: dict | None = None,
                       stereo: dict | None = None, multiview: dict | None = None,
                       ref_scene: str | None = None, calibrate: bool = True) -> list[SatReading]:
    """One footprint's readings from its raw measurements.

    ``lean``: ``{height_m, conf}`` of the reference scene (key ``qc`` accepted for ``conf``);
    ``shadows``: ``{scene: {height_m, conf}}``; ``stereo``: the consensus; ``multiview``: the
    plane-sweep peak. ``calibrate``: apply :func:`calibrate` (False: the 2026-10-07 rules).
    """
    out: list[SatReading] = []
    by: dict[str, SatReading] = {}
    lean = lean or {}
    lc = lean.get("conf", lean.get("qc")) or 0
    if lean.get("height_m") and lc >= MIN_CONF:
        by["lean"] = SatReading("lean", float(lean["height_m"]), float(lc), scene=ref_scene)
    sc = [(n, float(s["height_m"]), float(s["conf"])) for n, s in (shadows or {}).items()
          if s and s.get("height_m") and (s.get("conf") or 0) >= MIN_CONF]
    if sc:
        h, c, n, g = group([(h, c) for _, h, c in sc])
        names = [nm for nm, hh, cc in sc if (hh, cc) in g]
        by["shadow"] = SatReading("shadow", h, c, scene="+".join(names), lower_bound=True,
                                  n_scenes=n, extra={"n_scenes_measured": len(sc)})
    st = stereo or {}
    if st.get("height_m") and (st.get("conf") or 0) >= MIN_CONF:
        by["stereo"] = SatReading("stereo", float(st["height_m"]), float(st["conf"]),
                                  n_scenes=int(st.get("n_pairs") or 0),
                                  extra={"n_agree": st.get("n_agree")})
    mv = multiview or {}
    if mv.get("height_m") and (mv.get("conf") or 0) >= MULTIVIEW_MIN_CONF:
        by["multiview"] = SatReading("multiview", float(mv["height_m"]), float(mv["conf"]),
                                     n_scenes=int(mv.get("n_pairs") or 0))
    if "lean" in by and "shadow" in by and by["lean"].height_m >= 40.0 \
            and _agree(by["lean"].height_m, by["shadow"].height_m):
        a, b = by["lean"], by["shadow"]
        by["ls"] = SatReading("ls", (a.height_m * a.conf + b.height_m * b.conf) / (a.conf + b.conf),
                              max(a.conf, b.conf), scene=a.scene)
    for m in METHODS:
        if m in by:
            out.append(by[m])
    return _calibrate(out) if calibrate else out


_calibrate = calibrate


def from_measurements(single: dict | None, multi: dict | None, ref_scene: str) -> dict:
    """``{fid: [SatReading]}`` from ``measure.measure_single`` (lean; its shadow too when no
    multi-scene run covers the footprint) and ``measure.finish_multi`` rows."""
    out = {}
    for fid in set(single or {}) | set(multi or {}):
        s = (single or {}).get(fid) or {}
        m = (multi or {}).get(fid)
        shadows = dict(m["shadow"]) if m else {}
        if not shadows and s.get("shadow"):
            shadows = {ref_scene: s["shadow"]}
        lean = s.get("lean") if (s.get("lean") or {}).get("height_m", 0) and s["lean"]["height_m"] >= 2.5 else None
        rs = footprint_readings(lean=lean, shadows=shadows, stereo=(m or {}).get("stereo"),
                                multiview=(m or {}).get("stereo_multiview"), ref_scene=ref_scene)
        if rs:
            out[fid] = rs
    return out


def save(path: str | os.PathLike, readings: dict, meta: dict, where: dict | None = None) -> None:
    """Write ``readings.json``; ``where``: fid -> {lat, lon, osm_id} (for matching later runs)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    body = {"_meta": dict(meta, version=READINGS_VERSION)}
    for fid in sorted(readings):
        body[fid] = dict((where or {}).get(fid) or {},
                         readings=[r.to_json() for r in readings[fid]])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=0), encoding="utf-8")
    os.replace(tmp, p)


def load(path: str | os.PathLike) -> tuple[dict, dict]:
    """``(meta, {fid: {"lat", "lon", "osm_id", "readings": [SatReading]}})``; ({}, {}) when the
    file is missing."""
    p = Path(path)
    if not p.exists():
        return {}, {}
    body = json.loads(p.read_text(encoding="utf-8"))
    meta = body.pop("_meta", {})
    out = {}
    for fid, v in body.items():
        out[fid] = dict(v, readings=[SatReading.from_json(r) for r in v.get("readings") or []])
    return meta, out


__all__ = ["SatReading", "footprint_readings", "from_measurements", "calibrate", "group", "save",
           "load", "METHODS", "AGREE", "READINGS_VERSION", "LOW_LEAN_MAX_CONF", "UNDER_LEAN_FACTOR",
           "LEAN_TRUST_CONF"]
