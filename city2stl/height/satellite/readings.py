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
weights were scored with; ``tests/test_satellite_heights.py`` checks they match.

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

from .weights import MIN_CONF, MULTIVIEW_MIN_CONF

#: Two readings agree within this share (the benchmark's 25 % yardstick).
AGREE = 0.25
#: Methods a footprint can have, in the order fusion considers them.
METHODS = ("ls", "lean", "shadow", "stereo", "multiview")
READINGS_VERSION = 1


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


def footprint_readings(*, lean: dict | None = None, shadows: dict | None = None,
                       stereo: dict | None = None, multiview: dict | None = None,
                       ref_scene: str | None = None) -> list[SatReading]:
    """One footprint's readings from its raw measurements.

    ``lean``: ``{height_m, conf}`` of the reference scene (key ``qc`` accepted for ``conf``);
    ``shadows``: ``{scene: {height_m, conf}}``; ``stereo``: the consensus; ``multiview``: the
    plane-sweep peak.
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
    return out


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


__all__ = ["SatReading", "footprint_readings", "from_measurements", "group", "save", "load",
           "METHODS", "AGREE", "READINGS_VERSION"]
