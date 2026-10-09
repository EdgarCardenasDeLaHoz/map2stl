"""Verification tiers for published heights (F-SKY26 step 2a). Pure: no I/O, no numpy.

Every published row says how far its height can be trusted, as one tier, best first:

- ``survey``: the per-footprint survey nDSM height (published only with 2d, ``use_survey_heights``);
- ``verified_2``: two *independent* readings agree (``independent`` + ``agree``);
- ``tag``: the OSM height tag;
- ``single``: image readings, but no independent agreeing pair: published, labelled unverified;
- ``prior``: no reading; the T41 height prior (or the constant fallback).

A reading is a dict ``{"kind", "value_m", "seed"}`` (``seed`` optional). Kinds: ``drone``
(an elevated seed's roof reading), ``street`` (Street View), ``floors`` (floor count x storey),
satellite ``lean`` / ``multiview`` / ``stereo``, and ``shadow`` (a lower bound).

Independence (``INDEPENDENT``) is an allow-list of kind pairs, each with its condition; a pair
not listed never verifies. Listed (decision 2026-10-07, F-SKY26 addendum):

- drone + drone, from different seeds;
- drone + satellite (lean, multiview or stereo) when the satellite reads ``SAT_MIN_M`` or more;
- lean + shadow on a building over ``LEAN_SHADOW_MIN_M`` (the lean reading);
- floors + drone from different seeds.

Not listed, so never verified: shadow + shadow and stereo + stereo (one podium or one scene
pair cuts every reading the same way), floors + drone from the same seed (one image),
street + street (the roof-to-building assignment errs the same way from every street seed).

Tag + one reading (the user's rule, 2026-10-08, ``tag_witness``): an OSM ``height`` tag
(``osm_tag``; never ``osm_levels``) that agrees within ``AGREE_REL`` with one independent image
reading is ``verified_2`` (methods ``["osm_tag", <reading>]``). The reading can be a (trusted)
drone reading, a satellite lean or multiview, or a stereo reading of ``SAT_MIN_M`` or more; never
a shadow (a lower bound), floors alone, or Street View.

``prior_disagrees``: a ``single`` row whose published height is more than
``PRIOR_DISAGREE_FACTOR`` from the prior.

Publish rule (the user's choice, 2026-10-08, ``single_withheld``): a ``single`` reading more than
``SINGLE_WITHHOLD_FACTOR`` x the prior publishes the prior instead (tier ``prior``); the reading
stays in the row as unverified evidence (``single_reading_m``, ``single_source``,
``withheld_reason``). On Cartagena v9 the tower-behind check showed such readings are often a
farther tower's top, and the 164 singles over 2x the prior had a median of ~99 m.

Refinement (the user's choice, 2026-10-09, ``single_support``): only an *unsupported* single is
withheld. It stays ``single`` (``single_support`` names why) when

- a validated satellite reading of ``SUPPORT_MIN_M`` or more agrees with it within ``AGREE_REL``
  (or is the single itself): lean at conf >= 0.7, lean + shadow (``ls``), multiview at conf
  >= 0.3 (``SUPPORT_MIN_CONF``; Chicago LiDAR: lean over 100 m within 25 % 94 %, multiview over
  40 m 84 %; plain stereo at 40-100 m only ~59 %, so stereo alone never supports), or
- floor counts flagged the plot a high-rise (``floor_bands.high_rise_plots``; the flag already
  carries the satellite veto): ``HIGH_RISE_SUPPORT``.

Neither counts when the satellite says the plot is low (its largest confident reading is under
40 m, the tower-behind test's rule): such a reading is withheld. Cartagena v10 withheld 302
singles (median 70 m), most of Bocagrande's towers among them.

Agreement (``agree``; review item 5, 2026-10-09): two readings agree when the larger is at most
``1 + tol`` times the smaller (``|ln a/b| <= ln(1 + tol)``, i.e. ``tol`` of the *smaller*), ``tol``
from ``AGREE_TOL`` by the smaller value's band (<15 / 15-40 / 40-100 / >100 m), fitted on labelled
pairs; plus a metre floor (``AGREE_FLOOR_M``) below ``AGREE_FLOOR_BELOW_M``, off until low-rise
labels can accept it. Until 2026-10-09 it was 25 % of the larger (a ratio up to 1.33).

Measured vs tag (review item 6, ``tag_measurement``): a tagged row whose corroborating pair agrees
with itself but sits more than ``TAG_DISAGREE_REL`` from the tag keeps publishing the tag and
carries ``measured_m``, ``measured_methods`` and ``tag_disagrees`` (Palmetto: lean 136 + shadow
135 against the tag 156). The user decides any switch.

``selection_reason``: why a published row's value was chosen, on every row (as ``withheld_reason``
says why a reading was not).
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

#: Tiers, best first.
TIERS = ("survey", "verified_2", "tag", "single", "prior")
#: Tiers whose height is checked by something other than the image that produced it.
VERIFIED_TIERS = ("survey", "verified_2")
#: The base agreement tolerance: two readings agree when the larger is at most ``1 + AGREE_REL``
#: times the smaller (25 % of the smaller; the benchmark's "within 25 %" yardstick). Bands may
#: tighten it (``AGREE_TOL``). Until 2026-10-09: 25 % of the larger.
AGREE_REL = 0.25
#: Bands of the agreement table, by the smaller of the two values: ``(upper bound m, name)``.
AGREE_BANDS = ((15.0, "<15"), (40.0, "15-40"), (100.0, "40-100"), (math.inf, ">100"))
#: band -> tolerance (review item 5, fitted 2026-10-09 by ``S/tiers/fit.py``): the loosest of
#: 5-25 % at which P(both readings within max(25 %, 2 m) of truth | they agree) >= 0.85, on 528
#: labelled pairs (the satellite validation's 7 LiDAR cities + Miami drone harness; tag + witness,
#: drone + drone, drone / tag + satellite, lean + shadow). 40-100 m: 25 % gives 0.89 (n agree 54,
#: held out by city 0.93); > 100 m: 0.99 (n 216, held out 0.99). < 15 m and 15-40 m: no tolerance
#: reaches 0.85 (2 and 7 agreeing pairs, about half right), so they keep 25 %.
AGREE_TOL = {"<15": 0.25, "15-40": 0.25, "40-100": 0.25, ">100": 0.25}
#: Metre floor: below ``AGREE_FLOOR_BELOW_M`` two readings also agree within this many metres
#: (the review's max(25 %, 2 m): 25 % of a 4 m house is 1 m, under LiDAR-vs-LiDAR noise). Off
#: (0) since 2026-10-09: on the labelled pairs its only new corroboration was wrong (tag 3 m +
#: lean 4.2 m, truth 12.5 m), and the < 15 m band has 2 corroborated buildings, short of the
#: review's 30 (acceptance rule §4.5). ``REVIEW_FLOOR_M`` is the value to switch it on with.
AGREE_FLOOR_M = 0.0
REVIEW_FLOOR_M = 2.0
AGREE_FLOOR_BELOW_M = 15.0
#: Satellite readings verify a drone reading only at or above this height: below 40 m, lean
#: and stereo are dropped and shadows are 0.05-weight (decision 2026-10-07).
SAT_MIN_M = 40.0
#: Lean + shadow verify only on a building over this height: 26 of 27 agreeing pairs right.
LEAN_SHADOW_MIN_M = 100.0
#: A ``single`` height more than this factor from the prior is flagged ``prior_disagrees``.
PRIOR_DISAGREE_FACTOR = 2.0
SATELLITE_KINDS = ("lean", "multiview", "stereo")
#: A ``single`` height more than this factor *over* the prior publishes the prior instead
#: (``single_withheld``; the user's rule, 2026-10-08). Singles under the prior are kept.
SINGLE_WITHHOLD_FACTOR = 2.0
#: ``withheld_reason`` of a row whose single reading was withheld.
SINGLE_WITHHELD_REASON = "single over 2x prior"


def reading(kind: str, value_m: float, seed: str | None = None) -> dict:
    """A reading dict for ``verification_tier``."""
    return {"kind": kind, "value_m": float(value_m), "seed": seed}


def label(r: dict) -> str:
    """``kind:seed`` (or ``kind``): how a reading is named in ``tier_methods``."""
    return f"{r['kind']}:{r['seed']}" if r.get("seed") else r["kind"]


def agree_band(h: float) -> str:
    """The ``AGREE_BANDS`` name of a height (the smaller of two readings)."""
    for top, name in AGREE_BANDS:
        if h < top:
            return name
    return AGREE_BANDS[-1][1]


def agree_tol(a: float, b: float) -> float:
    """The tolerance two readings are held to: ``AGREE_TOL`` of the smaller one's band."""
    return AGREE_TOL.get(agree_band(min(abs(a), abs(b))), AGREE_REL)


def log_ratio(a: float, b: float) -> float:
    """``|ln(a / b)|`` of two heights (inf when one is not positive, 0 when both are equal)."""
    a, b = float(a), float(b)
    if a <= 0 or b <= 0:
        return 0.0 if a == b else math.inf
    return abs(math.log(a / b))


def agree(a: float, b: float, rel: float | None = None, floor_m: float | None = None) -> bool:
    """Whether two heights agree: the larger at most ``1 + tol`` times the smaller, ``tol`` =
    ``rel`` or, by default, the band tolerance (``agree_tol``); or, when the smaller is under
    ``AGREE_FLOOR_BELOW_M``, within ``floor_m`` metres (default ``AGREE_FLOOR_M``, 0 = off).
    Symmetric. ``tag_witness``, ``verified_pair``, the tag check, ``disputed_by`` and
    ``single_support`` all use it."""
    tol = agree_tol(a, b) if rel is None else rel
    if log_ratio(a, b) <= math.log1p(tol) + 1e-12:
        return True
    floor = AGREE_FLOOR_M if floor_m is None else floor_m
    return bool(floor) and min(abs(a), abs(b)) < AGREE_FLOOR_BELOW_M and abs(a - b) <= floor


def _different_seeds(a: dict, b: dict) -> bool:
    return bool(a.get("seed")) and bool(b.get("seed")) and a["seed"] != b["seed"]


def _satellite_tall_enough(a: dict, b: dict) -> bool:
    sat = a if a["kind"] in SATELLITE_KINDS else b
    return sat["value_m"] >= SAT_MIN_M


def _lean_over_100(a: dict, b: dict) -> bool:
    lean = a if a["kind"] == "lean" else b
    return lean["value_m"] > LEAN_SHADOW_MIN_M


#: Kind pair -> the condition under which two readings of those kinds are independent.
INDEPENDENT: dict[frozenset, Callable[[dict, dict], bool]] = {
    frozenset({"drone"}): _different_seeds,
    frozenset({"drone", "lean"}): _satellite_tall_enough,
    frozenset({"drone", "multiview"}): _satellite_tall_enough,
    frozenset({"drone", "stereo"}): _satellite_tall_enough,
    frozenset({"lean", "shadow"}): _lean_over_100,
    frozenset({"floors", "drone"}): _different_seeds,
}


def independent(a: dict, b: dict) -> bool:
    """Whether readings ``a`` and ``b`` are independent enough to verify each other."""
    rule = INDEPENDENT.get(frozenset({a["kind"], b["kind"]}))
    return rule is not None and rule(a, b)


def verified_pair(readings: Sequence[dict]) -> tuple[dict, dict] | None:
    """The independent, agreeing pair of ``readings`` that agrees most closely, or None."""
    best, best_d = None, None
    rs = [r for r in readings if r.get("value_m") is not None and r["value_m"] > 0]
    for i, a in enumerate(rs):
        for b in rs[i + 1:]:
            if not (independent(a, b) and agree(a["value_m"], b["value_m"])):
                continue
            d = log_ratio(a["value_m"], b["value_m"])
            if best_d is None or d < best_d:
                best, best_d = (a, b), d
    return best


def _pair_value(pair: tuple[dict, dict]) -> float:
    return (pair[0]["value_m"] + pair[1]["value_m"]) / 2


#: Reading kinds that can verify an OSM height tag on their own (``tag_witness``); stereo only
#: at ``SAT_MIN_M`` or more. Not shadow (a lower bound), floors, or street.
TAG_WITNESS_KINDS = ("drone", "lean", "multiview", "stereo")
#: The tag source a single reading can verify: a height tag, not a level count.
TAG_WITNESS_SOURCE = "osm_tag"


def _can_witness(r: dict) -> bool:
    if r["kind"] not in TAG_WITNESS_KINDS:
        return False
    return r["kind"] != "stereo" or r["value_m"] >= SAT_MIN_M


def tag_witness(readings: Sequence[dict], tag_m: float | None,
                tag_source: str | None) -> dict | None:
    """The image reading that verifies an OSM height tag on its own, or None: ``tag_source``
    must be ``osm_tag`` and the reading a ``TAG_WITNESS_KINDS`` kind within ``AGREE_REL`` of the
    tag (the closest one when several agree)."""
    if tag_m is None or tag_source != TAG_WITNESS_SOURCE:
        return None
    best, best_d = None, None
    for r in readings:
        v = r.get("value_m")
        if v is None or v <= 0 or not _can_witness(r) or not agree(v, float(tag_m)):
            continue
        d = log_ratio(v, float(tag_m))
        if best_d is None or d < best_d:
            best, best_d = r, d
    return best


#: A tagged row's agreeing pair more than this share from the tag (on the smaller value) is shown
#: beside it (``tag_measurement``; review item 6, 2026-10-09).
TAG_DISAGREE_REL = 0.10


def tag_measurement(readings: Sequence[dict], tag_m: float | None) -> dict:
    """What a tagged row's readings measured, when that disagrees with the published tag: the
    independent agreeing pair (``verified_pair``) whose mean is more than ``TAG_DISAGREE_REL``
    from ``tag_m`` gives ``{"measured_m", "measured_methods", "tag_disagrees": True}``; else {}.
    The tag stays published (the user decides any switch); one reading alone is not a
    measurement here (``tag_witness`` judges it). Palmetto (v11): lean 136.4 + shadow 135.3 =
    135.9 against the tag 156."""
    if tag_m is None or float(tag_m) <= 0:
        return {}
    pair = verified_pair(readings)
    if pair is None:
        return {}
    m = _pair_value(pair)
    if log_ratio(m, float(tag_m)) <= math.log1p(TAG_DISAGREE_REL):
        return {}
    return {"measured_m": round(m, 1), "measured_methods": sorted(label(r) for r in pair),
            "tag_disagrees": True}


def verification_tier(readings: Sequence[dict], tag_m: float | None = None,
                      survey: float | None = None,
                      tag_source: str | None = None) -> tuple[str, list[str]]:
    """``(tier, methods)`` for one building.

    ``readings``: the building's published-kind readings (see module docstring); ``tag_m``:
    its OSM height tag when that is what is published; ``tag_source``: that tag's source
    (``osm_tag`` / ``osm_levels``); ``survey``: its survey nDSM height when that is published.
    A verified pair must also agree with the tag when there is one. Otherwise an ``osm_tag``
    tag one reading agrees with (``tag_witness``) is ``verified_2`` too, unless an independent
    pair disputes it; else the tag stands (tier ``tag``). ``methods`` name what the tier rests on.
    """
    if survey is not None:
        return "survey", ["survey"]
    pair = verified_pair(readings)
    if pair is not None and (tag_m is None or agree(_pair_value(pair), float(tag_m))):
        return "verified_2", sorted(label(r) for r in pair)
    if tag_m is not None:
        w = tag_witness(readings, tag_m, tag_source) if pair is None else None
        if w is not None:
            return "verified_2", ["osm_tag", label(w)]
        return "tag", ["osm_tag"]
    rs = [r for r in readings if r.get("value_m") is not None]
    if rs:
        return "single", sorted(label(r) for r in rs)
    return "prior", []


def tier_fields(readings: Sequence[dict], *, published_m: float | None,
                tag_m: float | None = None, survey: float | None = None,
                prior_m: float | None = None, prior_source: str | None = None,
                tag_source: str | None = None) -> dict:
    """The tier fields of a published row: ``tier``, ``tier_methods``, ``verified``,
    ``disputed_by`` and ``prior_disagrees``; on a tagged row whose agreeing pair is more than
    ``TAG_DISAGREE_REL`` from the tag also ``measured_m``, ``measured_methods`` and
    ``tag_disagrees`` (``tag_measurement``).

    ``disputed_by``: the methods of an independent agreeing pair that disagrees with
    ``published_m`` (e.g. two drone seeds against the tag); such a pair never verifies the
    row, so an untagged row it disputes drops to ``single``. ``prior_m`` / ``prior_source``:
    the prior's height (for ``prior_disagrees``) and name (the ``prior`` tier's method).
    ``tag_source``: the published tag's source; ``osm_tag`` lets one reading verify it.
    """
    tier, methods = verification_tier(readings, tag_m, survey, tag_source)
    disputed_by: list[str] = []
    pair = verified_pair(readings)
    if pair is not None and published_m is not None \
            and not agree(_pair_value(pair), float(published_m)):
        disputed_by = sorted(label(r) for r in pair)
        if tier == "verified_2":
            tier, methods = "single", sorted(label(r) for r in readings)
    if tier == "prior" and prior_source:
        methods = [prior_source]
    prior_disagrees = bool(
        tier == "single" and prior_m and published_m
        and max(prior_m, published_m) > PRIOR_DISAGREE_FACTOR * min(prior_m, published_m))
    out = {"tier": tier, "tier_methods": methods, "verified": tier in VERIFIED_TIERS,
           "disputed_by": disputed_by, "prior_disagrees": prior_disagrees}
    if tag_m is not None and survey is None:
        out.update(tag_measurement(readings, tag_m))
    return out


def single_withheld(tier: str, published_m: float | None, prior_m: float | None,
                    factor: float = SINGLE_WITHHOLD_FACTOR) -> bool:
    """Whether a row's ``single`` height is withheld for the prior: tier ``single`` and the
    published height more than ``factor`` x the prior (a prior of None or 0 withholds nothing)."""
    return bool(tier == "single" and prior_m and published_m
                and float(published_m) > factor * float(prior_m))


#: A satellite reading supports a single over 2x the prior only at or above this height.
SUPPORT_MIN_M = 40.0
#: Validated satellite methods (``single_support``) and the confidence each needs: lean at 0.7
#: (Chicago LiDAR: lean over 100 m within 25 % 94 %), lean + shadow agreeing (``ls``) at any,
#: multiview at 0.3 (over 40 m within 25 % 84 %). Stereo is not listed (40-100 m: ~59 %).
SUPPORT_MIN_CONF = {"lean": 0.7, "ls": 0.0, "multiview": 0.3}
#: ``single_support`` entry of a single kept because floor counts flagged its plot a high-rise.
HIGH_RISE_SUPPORT = "high_rise_floors"


def validated_satellite(method: str, height_m: float | None, conf: float | None) -> bool:
    """Whether one satellite reading is validated enough to support a single on its own:
    a ``SUPPORT_MIN_CONF`` method at its confidence, ``SUPPORT_MIN_M`` or more."""
    need = SUPPORT_MIN_CONF.get(method)
    return (need is not None and height_m is not None and float(height_m) >= SUPPORT_MIN_M
            and float(conf or 0.0) >= need)


def single_support(value_m: float | None, satellite: Sequence[tuple] = (),
                   high_rise: bool = False, satellite_low: bool = False) -> list[str]:
    """Why a ``single`` reading over 2x the prior is kept (the user's refinement, 2026-10-09):
    the validated satellite methods (``validated_satellite``) that agree with ``value_m`` within
    ``AGREE_REL``, sorted, plus ``HIGH_RISE_SUPPORT`` when floor counts flagged the plot. Empty
    (withhold it) when nothing supports it, or when ``satellite_low`` (the plot's confident
    satellite readings are all under 40 m: a tower-behind case). ``satellite``: ``(method,
    height_m, conf)`` per reading. A satellite single whose value is such a reading agrees with
    itself, so it supports itself."""
    if value_m is None or satellite_low:
        return []
    out = sorted({str(m) for m, h, c in satellite
                  if validated_satellite(m, h, c) and agree(float(h), float(value_m))})
    if high_rise:
        out.append(HIGH_RISE_SUPPORT)
    return out


#: Why a published row's value was chosen (``selection_reason``), by its published source.
SELECTION_REASONS = {
    "survey": "survey lidar height",
    "osm_tag": "OSM height tag: tags are published over image readings",
    "osm_levels": "OSM levels tag: tags are published over image readings",
    "elevated": "median of the drone seeds' trusted readings",
    "satellite": "satellite-only height: its best reading has sigma_log <= 0.25",
    "high_rise": "floor counts flagged a high-rise: floors x storey + 3 m",
    "cadastre": "cadastre floors x storey",
    "prior": "no usable reading: the height prior",
    "street": "Street View reading (withholding off)",
}


def selection_reason(row: dict) -> str:
    """Why the row publishes its ``effective_height_m`` (the selection-reason counterpart of
    ``withheld_reason``, as 3DBAG records one): ``SELECTION_REASONS`` by the published source,
    plus what qualifies it: a measurement that disagrees with a published tag
    (``tag_disagrees``), the support that kept a single over 2x the prior (``single_support``),
    the withheld reading behind a published prior (``withheld_reason``)."""
    src = str(row.get("effective_height_source") or "")
    base = src.split(":", 1)[1] if src.startswith("withheld:") else src
    if row.get("tier") == "survey" or base.startswith("survey"):
        return SELECTION_REASONS["survey"]
    if base in ("osm_tag", "osm_levels"):
        out = SELECTION_REASONS[base]
        if row.get("tag_disagrees") and row.get("measured_m") is not None:
            out += (f"; measured {float(row['measured_m']):.0f} m "
                    f"({', '.join(map(str, row.get('measured_methods') or ()))}) disagrees by more "
                    f"than {TAG_DISAGREE_REL:.0%}: the tag stays published until the user decides")
        return out
    if base in ("elevated", "satellite", "high_rise"):
        out = SELECTION_REASONS[base]
        if row.get("single_support"):
            out += ("; over 2x the prior, kept: supported by "
                    + ", ".join(map(str, row["single_support"])))
        return out
    if base.endswith("cadastre"):
        return SELECTION_REASONS["cadastre"]
    if src.startswith("withheld:"):
        if row.get("withheld_reason"):
            return f"the height prior: the reading was withheld ({row['withheld_reason']})"
        if row.get("drone_seen"):
            return SELECTION_REASONS["prior"] + " (the drone seeds that saw it gave none)"
        return SELECTION_REASONS["prior"]
    return SELECTION_REASONS["street"] if src else "no published height"


def tier_counts(rows: Sequence[dict]) -> dict[str, int]:
    """Rows per tier, in ``TIERS`` order (``unlabelled`` for rows without one)."""
    counts = {t: 0 for t in TIERS}
    for row in rows:
        t = row.get("tier") or "unlabelled"
        counts[t] = counts.get(t, 0) + 1
    return {t: n for t, n in counts.items() if n or t in TIERS}
