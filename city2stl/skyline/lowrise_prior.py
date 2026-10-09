"""Low-rise height prior from Google Open Buildings 2.5D Temporal (review 2026-10-09 item 4).

The T41 prior (``untagged_prior``) was trained on 8 northern cities. On Cartagena's untagged
non-PH plots it reads +6.7 m (median) over the cadastre, and only 16 of 286 rows fall within
max(25 %, 2 m). This module puts the bias-corrected Google 2.5D height in its place for
low-rises: the 2023 layer, read per footprint by ``city2stl.height.providers.google_ob25d``.

- **Reading:** a statistic (``CORRECTION["stat"]``) of the in-footprint pixels with presence
  >= ``CORRECTION["tau"]``. It needs ``google_ob25d.MIN_PIXELS`` pixels and a
  ``google_ob25d.MIN_SHARE`` share, else no reading.
- **Correction:** an additive offset per band of the *raw* Google value: ``B1`` under 15 m,
  ``B2`` 15-40 m. Truth is unknown when it is applied, so the band comes from the raw value.
  - Fitted as the median of (survey p95 − Google) on untagged footprints of San Juan,
    Mayagüez and Charlotte Amalie: USGS 3DEP PR/VI 2018 LiDAR, inside Google's coverage.
  - Miami and Honolulu are outside it.
  - ``CORRECTION["offsets"]`` holds the fit on all three cities (``"all"``) and one fit per
    held-out city. ``exclude_city`` picks the fold, as ``untagged_prior`` does, so a truth city
    is never scored in-sample.
- **Use:** the corrected height replaces the T41 prior when it is under ``GATE_M`` (20 m).
  - Google heights are capped at 100 m and were never validated on Global South towers.
  - T41 keeps everything else, and the tower evidence (drone, satellite, floors) still ranks
    above any prior.
- **Cross-check:** ``cross_check`` flags a prior more than ``CROSS_CHECK_FACTOR`` (2x) away from
  a non-PH cadastre height of 1 to ``co_catastro.TOWER_FLOORS`` floors (``FLAG``). It is
  reported only. It never changes the published value, and the cadastre itself stays
  unpublished (the user, 2026-10-09).

Opt-in per site (``use_google_2p5d_prior`` in ``sites/<region>.json``, off by default). The
pre-registered test and its numbers are in ``docs/decisions/building-heights.md`` (2026-10-09,
Google 2.5D low-rise prior). ``STATUS`` records the outcome: **refused**, so the flag stays off
and nothing is wired. The wiring into ``_core/height.withhold_untagged_street_view`` /
``region_pdf`` is drafted for their owner, in case a later test admits it.

    load_region(region, records, fetch=False) -> {feature_id: google_ob25d.Reading}
    corrected(reading, exclude_city=None) -> float | None
    prior_for(prior, reading, exclude_city=None) -> (height_m, source)
    fallback_for(records, region, t41=None, readings=None) -> rec -> (height_m, source)
    cross_check(prior_m, cadastre) -> bool
    row_fields(reading, exclude_city=None) -> dict

Licence: Google 2.5D used under CC BY 4.0 (``google_ob25d.ATTRIBUTION``).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence

log = logging.getLogger(__name__)

SOURCE = "google_2p5d"
#: The corrected Google height replaces the prior only below this.
GATE_M = 20.0
#: Band edges of the raw Google value: B1 < 15 m <= B2 < 40 m; nothing is corrected above.
B1_TOP_M = 15.0
B2_TOP_M = 40.0
CROSS_CHECK_FACTOR = 2.0
FLAG = "prior_cadastre_2x"
YEAR = 2023

#: The fit (scratch ``ob25/scripts/analyze.py``, 2026-10-09). ``offsets[fold][band]``, in metres,
#: is added to the raw Google value. A band with None has no correction, so no Google prior.
#: ``stat`` and ``tau`` were picked on the training cities of every fold. All three folds and the
#: full fit chose p90 at presence >= 0.7.
#: Training rows per fold:
#: - B1: ~2,500-3,000;
#: - B2: 27-109, which is why the B2 offsets disagree (a 1.9 m span).
#: Fitted on 2,221 San Juan + 2,285 Mayaguez + 1,471 Charlotte Amalie untagged footprints with
#: survey truth.
CORRECTION: dict = {
    "stat": "p90",
    "tau": 0.7,
    "offsets": {
        "all": {"B1": 0.15, "B2": 0.74},
        "san_juan": {"B1": 0.27, "B2": 1.84},
        "mayaguez": {"B1": 0.09, "B2": -0.045},
        "charlotte_amalie": {"B1": 0.08, "B2": 0.78},
    },
    "truth": "USGS 3DEP PR/VI 2018 survey p95 (stat 2), untagged footprints",
}

#: Outcome of the pre-registered test: "refused" (2026-10-09; decisions doc). Held out, the
#: composite beats T41 in every city and is nearly unbiased under 15 m. It fails three marks:
#: - the 1 m margin in 2 of 3 cities (Mayaguez -0.55 m, Charlotte Amalie -0.12 m);
#: - the B2 offset stability (1.9 m span);
#: - the Cartagena gate (38 of 286 within tolerance of the non-PH cadastre, against the 143
#:   required).
#: Keep ``use_google_2p5d_prior`` off.
STATUS = "refused"


def _key(city: str | None) -> str:
    return (city or "").strip().lower().replace(" ", "_").replace("-", "_")


def band_of(g: float) -> str | None:
    """``"B1"`` / ``"B2"`` of a raw Google height, None at 40 m or more."""
    return "B1" if g < B1_TOP_M else ("B2" if g < B2_TOP_M else None)


def offsets_for(exclude_city: str | None = None) -> dict:
    """The offsets fitted without ``exclude_city`` when it is a truth city, else ``"all"``."""
    offs = CORRECTION["offsets"]
    return offs.get(_key(exclude_city)) or offs["all"]


def corrected(reading, exclude_city: str | None = None) -> float | None:
    """Bias-corrected Google height of a ``google_ob25d.Reading``, or None (no valid reading,
    raw value at 40 m or more, or no offset for its band)."""
    if reading is None or not getattr(reading, "valid", False):
        return None
    g = reading.stat(CORRECTION["stat"])
    if g is None:
        return None
    b = band_of(g)
    off = offsets_for(exclude_city).get(b) if b else None
    return None if off is None else float(g) + float(off)


def prior_for(prior: tuple, reading, exclude_city: str | None = None) -> tuple:
    """``(height_m, source)`` an untagged row falls back to: the corrected Google height under
    ``GATE_M``, else ``prior`` unchanged."""
    c = corrected(reading, exclude_city)
    if c is not None and 0.0 < c < GATE_M:
        return (round(c, 2), SOURCE)
    return prior


def cross_check(prior_m: float | None, cadastre) -> bool:
    """True when ``prior_m`` and a usable cadastre height differ by more than
    ``CROSS_CHECK_FACTOR``. The cadastre is a ``cadastre_heights.CadastreReading``-like
    object (``floors``, ``height_m``, ``ph``). PH predios and towers are never compared:
    their floor count is a unit's, or the podium's."""
    from city2stl.height.providers.co_catastro import TOWER_FLOORS  # noqa: PLC0415

    if cadastre is None or prior_m is None or getattr(cadastre, "ph", False):
        return False
    floors, h = getattr(cadastre, "floors", None), getattr(cadastre, "height_m", None)
    if not floors or not (1 <= floors <= TOWER_FLOORS) or not h or h <= 0 or prior_m <= 0:
        return False
    return max(prior_m, h) / min(prior_m, h) > CROSS_CHECK_FACTOR


def load_region(region: str, records: Sequence, fetch: bool = False) -> dict:
    """``{feature_id: Reading}`` for ``records`` (their ``geometry``, lon/lat). {} when no
    window covers them or the read fails: an opt-in source never fails a run."""
    from city2stl.height.providers import google_ob25d as gob  # noqa: PLC0415

    polys = {r.feature_id: r.geometry for r in records if getattr(r, "geometry", None) is not None}
    if not polys:
        return {}
    try:
        out = gob.footprint_heights(polys, year=YEAR, tau=CORRECTION["tau"], fetch=fetch)
    except Exception as exc:  # noqa: BLE001
        log.warning("[lowrise_prior] %s: Google 2.5D unavailable (%s)", region, exc)
        return {}
    log.info("[lowrise_prior] %s: %d of %d footprints read, %d valid; %s", region, len(out),
             len(polys), sum(r.valid for r in out.values()), gob.ATTRIBUTION)
    return out


def fallback_for(records: Sequence, region: str | None = None,
                 t41: Callable | None = None, readings: dict | None = None,
                 fetch: bool = False) -> Callable:
    """A ``withhold_untagged_street_view`` fallback: ``prior_for`` over ``t41`` (default
    ``untagged_prior.fallback_for(records, region)``), with ``readings`` from ``load_region``
    unless given."""
    records = list(records)
    if t41 is None:
        from .untagged_prior import fallback_for as t41_for  # noqa: PLC0415
        t41 = t41_for(records, exclude_city=region)
    if readings is None:
        readings = load_region(region or "", records, fetch=fetch)

    def fallback(rec):
        return prior_for(t41(rec), readings.get(rec.feature_id), region)

    return fallback


def row_fields(reading, exclude_city: str | None = None) -> dict:
    """The ``google_2p5d`` block of a row (None values when there is no valid reading)."""
    if reading is None:
        return {"google_2p5d": None}
    c = corrected(reading, exclude_city)
    return {"google_2p5d": {
        "raw_m": reading.stat(CORRECTION["stat"]) if reading.valid else None,
        "corrected_m": None if c is None else round(c, 2),
        "used": c is not None and 0.0 < c < GATE_M,
        "share": reading.share, "n_px": reading.n_px, "tau": reading.tau, "year": YEAR,
        "stat": CORRECTION["stat"]}}


__all__ = ["CORRECTION", "CROSS_CHECK_FACTOR", "FLAG", "GATE_M", "SOURCE", "STATUS", "band_of",
           "corrected", "cross_check", "fallback_for", "load_region", "offsets_for", "prior_for",
           "row_fields"]
