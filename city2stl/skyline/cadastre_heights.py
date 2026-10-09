"""Cadastre floor counts in the skyline run: the adapter from ``city2stl.height.providers.co_catastro``
to what publishing and the verification tiers take. Opt-in per site: ``use_cadastre_heights`` in
``sites/<region>.json`` (off by default; decision 2026-10-08 in docs/decisions/building-heights.md).

- ``load_region(region, records)``: ``{feature_id: CadastreReading}`` for the run's footprints
  (largest-overlap / parts match, IoU >= 0.3). Registers the tier rules below.
- ``tier_readings(c)``: the ``tiers.reading`` dicts a row's tiers see: ``[cadastre]`` for any
  non-PH match (towers included, as evidence), ``[]`` for a propiedad-horizontal predio, whose
  ``total_piso`` is often one unit's (Hotel Estelar, 52 floors, reads 1).
- ``TIER_RULES`` (merged into ``tiers.INDEPENDENT`` by ``register_tier_rules``): the cadastre plus
  one agreeing image reading is ``corroborated`` (the user, 2026-10-08): a drone reading from any
  seed, a satellite lean or multiview, a stereo reading of ``tiers.SAT_MIN_M`` or more. Never a
  shadow (a lower bound), facade floors or Street View, as for ``tiers.tag_witness``.
- ``prior_for(prior, c, sat_rs)``: what an untagged row falls back to. A publishable match (1 to
  ``TOWER_FLOORS`` floors, not PH; confidence 0.7 for 1-5, 0.6 for 6-10) replaces the T41 prior
  (source ``cadastre``), so the existing order holds: a drone reading, then a publishable
  satellite height, then the high-rise floors height, then the cadastre; and the 2x rule
  (``tiers.single_withheld``) now withholds a single reading over twice the *cadastre* height.
  A satellite reading of ``SAT_MIN_M`` or more (conf >= ``SAT_DISPUTE_CONF``) that disagrees
  vetoes it: the footprint is a tower whose cadastre polygon is its podium (13 of 249 stereo
  pairs on Cartagena). Towers over ``TOWER_FLOORS`` floors never replace the prior: drone,
  satellite and floors evidence decide.
- ``row_fields(c)``: the ``cadastre`` block a row carries for the reports.

Licence: CC BY-SA 4.0. A report showing a height that rests on the cadastre prints
``tier_display.CADASTRE_ATTRIBUTION`` (``height_attributions``).

Wiring (the hook; ``_core/height.py`` and ``region_pdf.py`` belong to the F-SKY26 v10 work, so
this lands with their owner). ``region_pdf``, after the satellite block::

    cadastre = None
    if _load_site_use_cadastre_heights(region_name):
        from .cadastre_heights import load_region as load_cadastre  # noqa: PLC0415
        cadastre = load_cadastre(region_name, building_records) or None

and ``withhold_untagged_street_view(..., cadastre=cadastre)``. In
``_core/height.withhold_untagged_street_view(rows, records, ..., floors=None, cadastre=None)``::

    if cadastre:
        from ..cadastre_heights import prior_for, row_fields  # noqa: PLC0415
        from ..cadastre_heights import tier_readings as cadastre_readings  # noqa: PLC0415
    ...  # per row, after sat_rs:
    cad = (cadastre or {}).get(row.get("feature_id"))
    cad_rs = cadastre_readings(cad) if cad else []
    if cad:
        row["cadastre"] = row_fields(cad)
    ...  # untagged branch, right after ``prior = fallback(rec) ...``:
    if cad:
        prior = prior_for(prior, cad, sat_rs)
    ...  # the published readings:
    published = drone + street + sat if h is None else drone + (
        sat if (tagged or drone or sat_pub or src == "withheld:cadastre") else [])
    published = published + cad_rs
    if src == "withheld:high_rise":
        published = [reading("floors", h)] + cad_rs

Nothing else changes: the prior path publishes ``withheld:cadastre``, and the 2x rule compares
against it.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import asdict, dataclass

from ._core import tiers
from ._core.tiers import reading as tier_reading

log = logging.getLogger(__name__)

#: The tiers reading kind and the prior / source name.
KIND = "cadastre"
#: A satellite reading this confident, at ``tiers.SAT_MIN_M`` or more, that disagrees with a
#: low cadastre height vetoes it (the cadastre polygon is a tower's podium or a neighbour).
SAT_DISPUTE_CONF = 0.5
_SAT_KINDS = ("lean", "stereo", "multiview")


@dataclass(frozen=True)
class CadastreReading:
    """One footprint's cadastre match (``co_catastro.CadastreMatch``) as the run uses it."""
    floors: int
    height_m: float
    conf: float
    iou: float
    zone: str | None
    ph: bool
    publishable: bool
    defers: bool
    codigo: str = ""
    dataset: str = ""


def from_match(m, dataset: str = "") -> CadastreReading:
    return CadastreReading(floors=int(m.floors), height_m=float(m.height_m), conf=float(m.conf),
                           iou=float(m.iou), zone=m.zone, ph=bool(m.ph),
                           publishable=bool(m.publishable), defers=bool(m.defers),
                           codigo=str(m.codigo), dataset=dataset)


# --------------------------------------------------------------------------- tiers

def _image_witness(a: dict, b: dict) -> bool:
    """The non-cadastre reading of the pair can verify the cadastre on its own: drone (any seed),
    lean, multiview, or a stereo reading of ``tiers.SAT_MIN_M`` or more."""
    other = b if a["kind"] == KIND else a
    if other["kind"] == "stereo":
        return other["value_m"] >= tiers.SAT_MIN_M
    return other["kind"] in ("drone", "lean", "multiview")


#: Kind pairs with the cadastre that verify (merged into ``tiers.INDEPENDENT``).
TIER_RULES = {frozenset({KIND, k}): _image_witness
              for k in ("drone", "lean", "multiview", "stereo")}


def register_tier_rules() -> None:
    """Add ``TIER_RULES`` to ``tiers.INDEPENDENT`` (idempotent). Readings of kind ``cadastre``
    only exist on runs with ``use_cadastre_heights``, so the rules are inert elsewhere."""
    tiers.INDEPENDENT.update(TIER_RULES)


def tier_readings(c: CadastreReading | None) -> list[dict]:
    """The row's cadastre reading for the tiers: none for a PH predio (unreliable count)."""
    if c is None or c.ph or c.height_m is None:
        return []
    return [tier_reading(KIND, c.height_m)]


# --------------------------------------------------------------------------- publishing

def disputed_by_satellite(c: CadastreReading, sat_rs: Sequence = ()) -> bool:
    """A confident satellite reading of ``tiers.SAT_MIN_M`` or more that disagrees with the
    cadastre height (``SatReading``-like: ``method``, ``height_m``, ``conf``)."""
    for r in sat_rs or ():
        if (getattr(r, "method", None) in _SAT_KINDS and r.conf >= SAT_DISPUTE_CONF
                and r.height_m >= tiers.SAT_MIN_M and not tiers.agree(r.height_m, c.height_m)):
            return True
    return False


def prior_for(prior: tuple, c: CadastreReading | None, sat_rs: Sequence = ()) -> tuple:
    """``(height_m, source)`` an untagged row falls back to: the cadastre height when the match
    is publishable and no satellite reading disputes it, else *prior* unchanged."""
    if c is None or not c.publishable or disputed_by_satellite(c, sat_rs):
        return prior
    return (float(c.height_m), KIND)


def row_fields(c: CadastreReading) -> dict:
    """The ``cadastre`` block of a published row."""
    d = asdict(c)
    d["height_m"] = round(d["height_m"], 1)
    d["iou"] = round(d["iou"], 2)
    return d


# --------------------------------------------------------------------------- loading

def _bbox(records: Sequence) -> tuple[float, float, float, float]:
    lats = [r.centroid_lat for r in records]
    lons = [r.centroid_lon for r in records]
    return max(lats), min(lats), max(lons), min(lons)


def load_region(region: str, records: Sequence, dataset: str | None = None) -> dict:
    """``{feature_id: CadastreReading}`` for the run's ``records``; {} when no cadastre covers
    them or the layer cannot be fetched (a run never fails on it)."""
    from city2stl.height.providers import co_catastro as cc  # noqa: PLC0415

    recs = [r for r in records if getattr(r, "geometry", None) is not None]
    if not recs:
        return {}
    ds = cc.DATASETS.get(dataset) if dataset else cc.dataset_for(_bbox(recs))
    if ds is None:
        log.info("[cadastre] %s: no cadastre dataset covers the region", region)
        return {}
    try:
        matches = cc.match_footprints({r.feature_id: r.geometry for r in recs}, ds=ds)
    except Exception as exc:  # noqa: BLE001 - an opt-in source must never fail the run
        log.warning("[cadastre] %s: %s unavailable (%s)", region, ds.dataset_id, exc)
        return {}
    register_tier_rules()
    out = {fid: from_match(m, ds.name) for fid, m in matches.items()}
    log.info("[cadastre] %s: %d of %d footprints matched (%d publishable, %d PH, %d towers); %s",
             region, len(out), len(recs), sum(c.publishable for c in out.values()),
             sum(c.ph for c in out.values()),
             sum(c.defers and not c.ph for c in out.values()), ds.attribution)
    return out


__all__ = ["CadastreReading", "KIND", "TIER_RULES", "disputed_by_satellite", "from_match",
           "load_region", "prior_for", "register_tier_rules", "row_fields", "tier_readings"]
