"""How much a satellite height reading is worth: robust sigma of log(h / truth) by method, measured
height and confidence (decisions/building-heights.md, 2026-10-07 satellite weights and stereo).

Source: scratchpad ``satellite_cities/validation.md`` (LiDAR truth, 1,794 buildings in 7 cities;
Chicago multi-date WorldView stereo, 882 buildings). Bands are by the *measured* height, which is
what fusion sees. ``None``: the reading is dropped.

Pure: no I/O, no numpy. ``city2stl.skyline.footprint_detect.satellite_sigma_log`` delegates here.
"""

from __future__ import annotations

#: Reference sigma_log: a reading this good weighs 1 (``weight_scale``).
REF_SIGMA_LOG = 0.15
#: Readings under this confidence are not used at all (``load_sat`` / v4_agree ``cmin``).
MIN_CONF = 0.15
#: Satellite readings verify (tiers) or are weighted as full readings only from this height up:
#: lean, stereo and multiview under 40 m are dropped.
TALL_M = 40.0
#: Multiview (NCC plane-sweep peak) readings count only at this confidence or above.
MULTIVIEW_MIN_CONF = 0.3

KINDS = ("lean", "shadow", "stereo", "multiview", "ls")


def sigma_log(kind: str, h: float, conf: float) -> float | None:
    """sigma_log of one satellite reading.

    ``kind``: ``lean`` (current-scene roof shift), ``shadow`` (scenes merged to one reading),
    ``stereo`` (pair consensus across dated scenes), ``multiview`` (NCC plane-sweep peak over all
    scene pairs) or ``ls`` (lean and shadow agreeing). Lean, stereo and multiview under 40 m are
    dropped (sigma >= 0.5; stereo 0.61-0.84 on Chicago, multiview 0.59).
    """
    h, c = float(h), float(conf)
    if kind == "lean":
        if h < TALL_M:
            return None
        if h > 100.0:
            return 0.07 if c >= 0.7 else 0.15 if c >= 0.5 else 0.28
        return 0.16 if c >= 0.7 else 0.25 if c >= 0.5 else 0.43
    if kind == "shadow":
        if h > TALL_M:
            return 0.13
        if h >= 15.0:
            return 0.67
        return 0.21 if c >= 0.7 else 0.41 if c >= 0.3 else 0.85
    if kind == "stereo":                    # Chicago pair consensus: weight 0.6 / 0.4
        if h < TALL_M:
            return None
        return 0.19 if h <= 100.0 else 0.24
    if kind == "multiview":                 # Chicago NCC peak, conf >= 0.3: 84 % within 25 %
        if h < TALL_M or c < MULTIVIEW_MIN_CONF:
            return None
        return 0.08 if h <= 100.0 else 0.10
    if kind == "ls":
        lean, shadow = sigma_log("lean", h, c), sigma_log("shadow", h, c)
        return min(s for s in (lean, shadow, REF_SIGMA_LOG) if s is not None)
    raise ValueError(kind)


def weight_scale(kind: str, h: float, conf: float) -> float:
    """``min(1, (0.15 / sigma_log)^2)``; 0 for a dropped reading (the decision table's weights)."""
    s = sigma_log(kind, h, conf)
    return 0.0 if s is None else min(1.0, (REF_SIGMA_LOG / s) ** 2)


__all__ = ["sigma_log", "weight_scale", "REF_SIGMA_LOG", "MIN_CONF", "TALL_M",
           "MULTIVIEW_MIN_CONF", "KINDS"]
