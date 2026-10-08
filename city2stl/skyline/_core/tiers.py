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
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

#: Tiers, best first.
TIERS = ("survey", "verified_2", "tag", "single", "prior")
#: Tiers whose height is checked by something other than the image that produced it.
VERIFIED_TIERS = ("survey", "verified_2")
#: Two readings agree when they differ by at most this share of the larger: the benchmark's
#: "within 25 %" yardstick.
AGREE_REL = 0.25
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


def agree(a: float, b: float, rel: float = AGREE_REL) -> bool:
    """Whether two heights differ by at most ``rel`` of the larger."""
    return abs(a - b) <= rel * max(abs(a), abs(b))


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
            d = abs(a["value_m"] - b["value_m"]) / max(a["value_m"], b["value_m"])
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
        d = abs(v - float(tag_m))
        if best_d is None or d < best_d:
            best, best_d = r, d
    return best


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
    ``disputed_by`` and ``prior_disagrees``.

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
    return {"tier": tier, "tier_methods": methods, "verified": tier in VERIFIED_TIERS,
            "disputed_by": disputed_by, "prior_disagrees": prior_disagrees}


def single_withheld(tier: str, published_m: float | None, prior_m: float | None,
                    factor: float = SINGLE_WITHHOLD_FACTOR) -> bool:
    """Whether a row's ``single`` height is withheld for the prior: tier ``single`` and the
    published height more than ``factor`` x the prior (a prior of None or 0 withholds nothing)."""
    return bool(tier == "single" and prior_m and published_m
                and float(published_m) > factor * float(prior_m))


def tier_counts(rows: Sequence[dict]) -> dict[str, int]:
    """Rows per tier, in ``TIERS`` order (``unlabelled`` for rows without one)."""
    counts = {t: 0 for t in TIERS}
    for row in rows:
        t = row.get("tier") or "unlabelled"
        counts[t] = counts.get(t, 0) + 1
    return {t: n for t, n in counts.items() if n or t in TIERS}
