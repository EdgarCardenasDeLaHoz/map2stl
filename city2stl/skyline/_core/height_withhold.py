"""Part of ``city2stl.skyline._core.height`` (split by responsibility, F-UNIFY Phase 2 part B); the names are re-exported there."""

from __future__ import annotations

import logging
from collections.abc import Sequence

import numpy as np

from .types import BuildingRecord  # noqa: E402


def _seed_from_view_name(view_name: str) -> str:
    """Extract seed identifier from a view_name like 'seed_1_321' -> 'seed_1'."""
    parts = view_name.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return view_name


#: OSM height sources that count as a tag; anything else ("default") is untagged.
TAGGED_SOURCES = ("osm_tag", "osm_levels")


#: The height a withheld untagged building gets when no better source is passed (the T41
#: prior, ``untagged_prior.py``, is passed by ``region_pdf``). 12 m: the median confirmed
#: untagged height of the benchmark cities is 11.5-13.1 m whichever city is left out, and
#: a 12 m constant scores MAE 11.2 m against 11.4 m for 10 m (T38).
UNTAGGED_FALLBACK_M = 12.0


def untagged_fallback(records: Sequence[BuildingRecord], region: str | None = None):
    """The fallback ``withhold_untagged_street_view`` uses: the T41 height prior
    (``untagged_prior.fallback_for``) unless ``SKYLINE_UNTAGGED_FALLBACK=constant``; the
    constant also when the prior cannot run (no scikit-learn). Returns None for the
    constant."""
    from ..config import current  # noqa: PLC0415
    if current().untagged_fallback == "constant":
        return None
    try:
        from ..untagged_prior import fallback_for  # noqa: PLC0415
        return fallback_for(records, exclude_city=region)
    except Exception as exc:  # noqa: BLE001 - a run must never fail on its fallback
        logging.getLogger(__name__).warning(
            "[withhold_untagged] height prior unavailable (%s); %.0f m constant", exc,
            UNTAGGED_FALLBACK_M)
        return None


def _prefer_tags_enabled() -> bool:
    """Whether a tagged building publishes its OSM height instead of the Street View value.

    On by default (2026-10-05): on Miami's 29 confirmed tagged buildings the tag is 9.2 m off
    the truth and Street View 26.6 m; and the blind review T37 found a photo reading more than
    25 % from its tag wrong far more often than the tag. ``SKYLINE_PREFER_TAGS=0`` publishes
    the measurement again.
    """
    from ..config import current  # noqa: PLC0415
    return current().prefer_tags


def _withhold_untagged_enabled() -> bool:
    """Whether Street View heights on untagged buildings are replaced by a fallback.

    On by default (T28, user-approved 2026-10-05): untagged buildings read 65-113 m too
    tall in every benchmark city, because a farther tower's roof is credited to the low
    building in front (roof-to-building assignment). Set ``SKYLINE_WITHHOLD_UNTAGGED=0``
    to publish the Street View value again, e.g. once assignment is fixed.
    """
    from ..config import current  # noqa: PLC0415
    return current().withhold_untagged


def _high_rise_height(prior_m, info) -> float | None:
    """``floor_bands.high_rise_height`` (F-SKY26 step 4), None when floors did not flag it."""
    if not info:
        return None
    from ..floor_bands import high_rise_height  # noqa: PLC0415
    return high_rise_height(prior_m, info)


def _satellite_low(sat_rs) -> bool:
    """Whether a plot's confident satellite readings are all under 40 m (``elevated._sat_max``,
    the tower-behind test's and the high-rise flag's rule)."""
    if not sat_rs:
        return False
    from .._pano.elevated import SAT_LOW_M, _sat_max  # noqa: PLC0415
    m = _sat_max(sat_rs)
    return m is not None and m < SAT_LOW_M


def _withhold_single_enabled() -> bool:
    """Whether an untagged ``single`` reading more than 2x the prior publishes the prior.

    On by default (the user's rule, 2026-10-07/08; ``tiers.single_withheld``): on Cartagena v9,
    164 single rows read over 2x their prior at a median of ~99 m, and the tower-behind check
    (``elevated.tower_behind``) showed such a reading is often a farther tower's top.
    Refined by the user on 2026-10-09 (``tiers.single_support``): only an unsupported single is
    withheld. ``SKYLINE_WITHHOLD_SINGLE=0`` publishes the single reading again.
    """
    from ..config import current  # noqa: PLC0415
    return current().withhold_single


def withhold_untagged_street_view(rows: list[dict], records: Sequence[BuildingRecord],
                                  fallback=None, measured_seeds=(), satellite=None,
                                  floors=None, cadastre_band=None) -> int:
    """Replace the Street View height of every untagged building in ``rows`` (the output of
    ``aggregate_building_heights``) by a fallback, and (``SKYLINE_PREFER_TAGS``, default on)
    that of every tagged building by its OSM height; returns how many were replaced.

    The Street View value is kept for scoring: ``street_view_m`` and
    ``street_view_source`` hold what ``effective_height_m`` / ``effective_height_source``
    were, ``effective_height_source`` becomes ``"withheld:<fallback source>"``.
    ``fallback(record) -> (height_m, source)`` picks the replacement (T29 decides the best
    source); by default ``UNTAGGED_FALLBACK_M``, source ``"default"``. A fallback height of
    None leaves the row as it was. Idempotent; replaces nothing when the flag is off.

    ``measured_seeds``: seeds that measure footprint first (the drone seeds, ``elevated_seeds``),
    not by Street View's roof assignment. An untagged building one of them read publishes the
    median of those readings (source ``"withheld:elevated"``) instead of the fallback:
    2026-10-05, Cartagena's Hotel Estelar (202 m published) read 185 m from seed_1 but 113 m
    from a street seed, and was published at the 10 m fallback.

    Every row with a record (flag on or off) also gets its verification tier (F-SKY26 2a,
    ``tiers.tier_fields``: ``tier``, ``tier_methods``, ``verified``, ``disputed_by``,
    ``prior_disagrees``) from the readings behind what it publishes, and the survey-blind
    answer the benchmark scores (2c): ``no_survey_height_m`` / ``_source`` / ``_tier``, equal to
    the published value until survey heights are published (2d).

    ``satellite``: ``{feature_id: [SatReading]}`` (``satellite_fusion.load_region``, site flag
    ``use_satellite_heights``, F-SKY26 step 7). The readings join the tier readings of every row
    (drone + satellite at 40 m or more, lean + shadow over 100 m verify; satellite pairs of one
    kind never do), and an untagged row with no drone reading publishes the satellite-only height
    (``satellite_fusion.publishable``, source ``"withheld:satellite"``) instead of the prior when
    its best reading's sigma_log is at most 0.25. Rows carry ``satellite`` ({method: [m, conf]}).

    A ``single`` row (drone median or satellite-only, unconfirmed, untagged) more than 2x its
    prior publishes the prior instead (``SKYLINE_WITHHOLD_SINGLE``, default on; the user's rule
    2026-10-08): tier ``prior``, ``prior_disagrees`` True, and the reading kept as unverified
    evidence in ``single_reading_m`` / ``single_source`` / ``single_methods`` with
    ``withheld_reason`` ``"single over 2x prior"``. Singles within 2x publish as before.
    Refinement (the user, 2026-10-09; ``tiers.single_support``): a single over 2x the prior that a
    validated satellite reading >= 40 m agrees with (lean conf >= 0.7, ``ls``, multiview conf >=
    0.3; or the single is that reading), or whose plot floors flagged a high-rise, stays
    ``single`` with ``single_support`` (``["lean", ...]`` / ``["high_rise_floors"]``), unless the
    plot's confident satellite readings are all under 40 m (a tower-behind case).

    ``floors``: ``{feature_id: info}`` (``elevated_estimates(...).floors``, kept by the
    orchestrator in ``elevated_state["floors"]``; F-SKY26 step 4). An untagged row with no drone
    reading and no publishable satellite height that floors flagged a high-rise publishes
    ``floor_bands.high_rise_height`` (max of the prior and floors x storey + 3 m; source
    ``"withheld:high_rise"``, tier ``single``, ``high_rise`` holds the count); the flag supports
    it under the 2x rule (the user, 2026-10-08; the flag never fires on a satellite-low plot).
    Every row floors counted carries ``high_rise_seen``, ``floors`` and ``storey_m``.

    ``cadastre_band``: ``{feature_id: CadastreBand}`` (``cadastre_heights.load_band``, site flag
    ``use_cadastre_band``; 2026-10-10). On a non-PH plot with a band it replaces the 2x rule: a
    ``single`` inside ``lo_m`` .. ``hi_m`` stays ``single`` (no satellite support needed), one
    outside it publishes the prior (tier ``prior``, ``withheld_reason`` ``cadastre_band``, the
    reading kept as evidence) unless a validated satellite reading supports it and it is within
    2x the band's upper end (``tiers.band_tolerated``): then it stays ``single`` with
    ``single_support`` and ``cadastre_band["reason"]`` ``cadastre_band_tolerated``. The row carries
    ``cadastre_band`` (the band and ``inside``). PH plots ignore their band (the count is often a
    unit's, the user, 2026-10-10) and keep the 2x rule, as do rows without a band. The cadastre
    height is never published and no cadastre reading enters the tiers.
    A tagged row whose ``osm_tag`` height one drone or satellite reading agrees with is
    ``corroborated`` (``tiers.tag_witness``; the user, 2026-10-08). A tagged row whose agreeing
    pair is more than 10 % from the tag keeps the tag and carries ``measured_m``,
    ``measured_methods`` and ``tag_disagrees`` (``tiers.tag_measurement``; review item 6,
    2026-10-09). Every such row says why its value was chosen: ``selection_reason``
    (``tiers.selection_reason``).
    """
    from .tiers import (  # noqa: PLC0415
        CADASTRE_BAND_REASON,
        CADASTRE_BAND_TOLERATED,
        SINGLE_WITHHELD_REASON,
        band_tolerated,
        reading,
        selection_reason,
        single_outside_band,
        single_support,
        single_withheld,
        tier_fields,
    )
    if satellite:
        from ..satellite_fusion import publishable, tier_readings  # noqa: PLC0415

    enabled = _withhold_untagged_enabled()
    by_id = {r.feature_id: r for r in records}
    n = 0
    for row in rows:
        rec = by_id.get(row.get("feature_id"))
        if rec is None or "street_view_m" in row:
            continue
        seeds = {k: v for k, v in (row.get("per_seed_median_m") or {}).items() if v is not None}
        drone = [reading("drone", v, k) for k, v in seeds.items() if k in measured_seeds]
        street = [reading("street", v, k) for k, v in seeds.items() if k not in measured_seeds]
        sat_rs = (satellite or {}).get(row.get("feature_id")) or []
        sat = tier_readings(sat_rs) if sat_rs else []
        if sat_rs:
            row["satellite"] = {r.method: [round(r.height_m, 1), round(r.conf, 2)] for r in sat_rs}
        tagged = rec.height_source in TAGGED_SOURCES
        fi = (floors or {}).get(row.get("feature_id"))
        if fi:
            # visible on every row floors counted (the refined 2x rule reads the flag)
            row["high_rise_seen"] = bool(fi.get("high_rise_seen"))
            row["floors"] = fi.get("floors")
            row["storey_m"] = fi.get("storey_m")
        h = src = prior = None
        sat_pub = None
        if enabled and tagged:
            if _prefer_tags_enabled() and rec.height_tag_m:
                h, src = float(rec.height_tag_m), rec.height_source
        elif enabled:
            prior = fallback(rec) if fallback is not None else (UNTAGGED_FALLBACK_M, "default")
            if drone:
                h, src = float(np.median([r["value_m"] for r in drone])), "withheld:elevated"
            elif sat_rs and (sat_pub := publishable(sat_rs)) is not None:
                h, src = sat_pub["height_m"], "withheld:satellite"
                row["satellite_methods"] = sat_pub["methods"]
                row["satellite_lower_bound"] = sat_pub["lower_bound"]
            elif (hr := _high_rise_height(prior[0], fi)) is not None:
                h, src = hr, "withheld:high_rise"
                row["high_rise"] = {k: fi.get(k) for k in ("floors", "lower_bound", "storey_m",
                                                           "floors_m", "seeds")}
            elif prior[0] is not None:
                h, src = float(prior[0]), f"withheld:{prior[1]}"
        if h is not None:
            row["street_view_m"] = row.get("effective_height_m")
            row["street_view_source"] = row.get("effective_height_source")
            row["effective_height_m"] = h
            row["effective_height_source"] = src
            n += 1
        # the readings behind what the row publishes: none for the prior, the drone seeds
        # for a tag or a drone median, and Street View too where it is still published
        published = drone + street + sat if h is None else drone + (
            sat if (tagged or drone or sat_pub) else [])
        if src == "withheld:high_rise":
            published = [reading("floors", h)]
        row.update(tier_fields(
            published, published_m=row.get("effective_height_m"),
            tag_m=h if (h is not None and tagged) else None,
            tag_source=rec.height_source if tagged else None,
            prior_m=prior[0] if prior else None, prior_source=prior[1] if prior else None))
        support = None
        band = (cadastre_band or {}).get(row.get("feature_id"))
        withheld_reason = SINGLE_WITHHELD_REASON
        if band is not None and not band.ph and prior is not None and row["tier"] == "single":
            # 2026-10-10: on a non-PH plot the cadastre plausibility band replaces the 2x rule
            # (a PH count is often a unit's, so PH plots keep the 2x rule below)
            outside = single_outside_band(row["tier"], h, band.lo_m, band.hi_m)
            row["cadastre_band"] = dict(band.row_fields(), inside=not outside)
            if outside and _withhold_single_enabled():
                # a validated satellite reading (no high-rise flag) keeps a single that is within
                # 2x the band's upper end; beyond that, or unsupported, the prior is published
                sat_support = single_support(
                    h, [(r.method, r.height_m, r.conf) for r in sat_rs],
                    satellite_low=_satellite_low(sat_rs))
                if band_tolerated(h, band.hi_m, sat_support):
                    row["single_support"] = sat_support
                    row["cadastre_band"]["reason"] = CADASTRE_BAND_TOLERATED
                else:
                    support = []
                    withheld_reason = CADASTRE_BAND_REASON
        elif (prior is not None and _withhold_single_enabled()
                and single_withheld(row["tier"], h, prior[0])):
            # refined by the user (2026-10-09): a validated satellite reading >= 40 m that agrees,
            # or the floors high-rise flag (which carries the satellite veto; it covers the
            # withheld:high_rise rows, exempt since 2026-10-08), keeps the single
            support = single_support(
                h, [(r.method, r.height_m, r.conf) for r in sat_rs],
                high_rise=bool(fi and fi.get("high_rise_seen")),
                satellite_low=_satellite_low(sat_rs))
            if support:
                row["single_support"] = support
        if support == []:
            # the user's rule (2026-10-08): one unsupported reading over 2x the prior is kept
            # as evidence only; the prior is published
            row["single_reading_m"] = h
            row["single_source"] = src
            row["single_methods"] = row["tier_methods"]
            row["withheld_reason"] = withheld_reason
            row["effective_height_m"] = float(prior[0])
            row["effective_height_source"] = f"withheld:{prior[1]}"
            row.update(tier="prior", tier_methods=[prior[1]], verified=False,
                       prior_disagrees=True)
        # the survey-blind answer the benchmark headline scores (2c); the published one
        # until survey heights are published (2d)
        row["no_survey_height_m"] = row.get("effective_height_m")
        row["no_survey_source"] = row.get("effective_height_source")
        row["no_survey_tier"] = row["tier"]
        row["selection_reason"] = selection_reason(row)
    return n
