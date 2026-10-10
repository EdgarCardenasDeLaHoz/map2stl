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

Plausibility band (``load_band``, ``CadastreBand``; site flag ``use_cadastre_band``, 2026-10-10):
the cadastre never publishes a height there. Its floor count only bounds what a single image
reading may be, in place of the "2x the prior" rule where a match exists: a non-PH plot reads
``floors x storey(use, area band, floors)`` within x0.5 .. x1.5 (``STOREY_M``: Puerto Rico LiDAR p95 /
OSM ``building:levels``, S/storey/results.md model B); a propiedad-horizontal plot reads
(``floors`` +- ``PH_FLOORS_SLACK``) x ``PH_STOREY_M``. ``_core/height.withhold_untagged_street_view``
(``cadastre_band=``) withholds a ``single`` outside it (``withheld_reason`` ``cadastre_band``) and
keeps one inside it without satellite support. Corroboration is unchanged (no cadastre reading
enters the tiers).

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

import json
import logging
import math
import time
from collections import Counter
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

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


# --------------------------------------------------------------------------- plausibility band
#: Band edges (areas, m2 of the OSM footprint) and names of ``STOREY_M``.
AREA_BANDS = (("<100", 0.0, 100.0), ("100-300", 100.0, 300.0), ("300-1000", 300.0, 1000.0),
              (">1000", 1000.0, math.inf))
#: Metres per floor by (use class, area band) for 1, 2 and 3+ floors: the median of LiDAR p95 /
#: ``building:levels`` on 3,546 Puerto Rico footprints (San Juan, 2018 USGS survey), a cell under
#: 20 footprints falling back to its area band over all classes (``any``), then to the global value
#: (S/storey/results.md, model B, 2026-10-09). A 1-level building is one tall storey: 4.2-4.8 m for
#: a small house, 6.7 m at 300-1000 m2. The 1-floor ``>1000`` cells use the 14 footprints there
#: (12.83 m, IQR 10.7-31.7), not the global 5.4 m of the model: big one-floor sheds and shopping
#: boxes are the tall ones, and Cartagena's 1-floor industrial / commercial plots over 1000 m2 read
#: 10-11 m on Google 2.5D.
STOREY_M = {
    ("house", "<100"): (4.24, 3.21, 3.27),
    ("house", "100-300"): (4.78, 3.6, 3.31),
    ("house", "300-1000"): (6.66, 3.8, 3.93),
    ("house", ">1000"): (12.83, 6.43, 4.8),
    ("apartments", "<100"): (4.51, 3.16, 3.27),
    ("apartments", "100-300"): (5.37, 3.6, 3.5),
    ("apartments", "300-1000"): (6.66, 4.84, 3.51),
    ("apartments", ">1000"): (12.83, 6.43, 4.8),
    ("commercial", "<100"): (4.51, 3.31, 3.27),
    ("commercial", "100-300"): (5.37, 3.94, 3.61),
    ("commercial", "300-1000"): (6.66, 5.15, 3.93),
    ("commercial", ">1000"): (12.83, 6.43, 4.8),
    ("industrial", "<100"): (4.51, 3.31, 3.27),
    ("industrial", "100-300"): (5.37, 3.69, 3.61),
    ("industrial", "300-1000"): (6.66, 4.84, 3.93),
    ("industrial", ">1000"): (12.83, 6.43, 4.8),
    ("civic", "<100"): (4.51, 3.31, 3.27),
    ("civic", "100-300"): (5.37, 3.69, 3.61),
    ("civic", "300-1000"): (6.66, 4.84, 3.93),
    ("civic", ">1000"): (12.83, 6.43, 4.8),
    ("annex", "<100"): (4.51, 3.31, 3.27),
    ("annex", "100-300"): (5.37, 3.69, 3.61),
    ("annex", "300-1000"): (6.66, 4.84, 3.93),
    ("annex", ">1000"): (12.83, 6.43, 4.8),
    ("any", "<100"): (4.51, 3.31, 3.27),
    ("any", "100-300"): (5.37, 3.69, 3.61),
    ("any", "300-1000"): (6.66, 4.84, 3.93),
    ("any", ">1000"): (12.83, 6.43, 4.8),
}
#: A reading is plausible from ``BAND_LOWER`` x to ``BAND_UPPER`` x the cadastre height.
BAND_LOWER = 0.5
BAND_UPPER = 1.5
#: A propiedad-horizontal plot's count is a unit's, so its band is that many floors either side of
#: it, at a flat storey.
PH_FLOORS_SLACK = 2
PH_STOREY_M = 3.5
#: The cadastre's R2 table (Socrata ``yk9q-cw89``): the use of each predio (``uso1-3``), which the
#: ``Construccion`` layer lacks. ``cad_class`` maps the use to ``STOREY_M``'s classes.
R2_DOMAIN = "www.datos.gov.co"
R2_DATASET = "yk9q-cw89"
R2_BATCH = 80
R2_CACHE = Path(__file__).resolve().parent / "runs" / "cadastre"


def area_band(area_m2: float) -> str:
    return next(name for name, lo, hi in AREA_BANDS if lo <= float(area_m2) < hi)


def cad_class(use: str | None) -> str | None:
    """The ``STOREY_M`` class of a cadastre use (``Residencial.Vivienda_Hasta_3_Pisos``): house,
    apartments, commercial, industrial, civic or annex; None for no (or an unknown) use, which
    ``storey_m`` reads as the area band over all classes."""
    if not use:
        return None
    grp, _, name = use.partition(".")
    n = name.lower()
    if grp == "Residencial":
        return "annex" if n.startswith("garajes") else "apartments" if n.startswith("apartamentos") else "house"
    if grp == "Comercial":
        return "industrial" if n.startswith("bodegas") else "commercial"
    if grp == "Industrial":
        return "industrial"
    if grp == "Institucional":
        return "civic"
    if grp == "Anexo":
        return "annex"
    return None


def storey_m(use_class: str | None, area_m2: float, floors: int) -> float:
    """Metres per floor for a building of ``floors`` (1, 2, 3+) and ``area_m2`` of class ``use_class``
    (None / unknown: the area band over all classes)."""
    cls = use_class if (use_class, "<100") in STOREY_M else "any"
    return STOREY_M[(cls, area_band(area_m2))][0 if floors <= 1 else 1 if floors == 2 else 2]


@dataclass(frozen=True)
class CadastreBand:
    """What a footprint's cadastre match says a reading may be: ``lo_m`` .. ``hi_m``."""
    floors: int
    ph: bool
    use: str | None
    area_m2: float
    storey_m: float
    lo_m: float
    hi_m: float
    codigo: str = ""

    def contains(self, height_m: float) -> bool:
        return self.lo_m <= float(height_m) <= self.hi_m

    def row_fields(self) -> dict:
        d = asdict(self)
        for k in ("area_m2", "storey_m", "lo_m", "hi_m"):
            d[k] = round(d[k], 1)
        return d


def make_band(c: CadastreReading, area_m2: float, use: str | None = None) -> CadastreBand:
    """The band of one match. PH: ``(floors +- PH_FLOORS_SLACK) x PH_STOREY_M`` (the lower end not
    below 0). Otherwise ``floors x storey_m(use, area, floors)`` x ``BAND_LOWER`` .. ``BAND_UPPER``."""
    if c.ph:
        s = PH_STOREY_M
        lo, hi = max(0, c.floors - PH_FLOORS_SLACK) * s, (c.floors + PH_FLOORS_SLACK) * s
    else:
        s = storey_m(cad_class(use), area_m2, c.floors)
        lo, hi = BAND_LOWER * c.floors * s, BAND_UPPER * c.floors * s
    return CadastreBand(floors=int(c.floors), ph=bool(c.ph), use=use, area_m2=float(area_m2),
                        storey_m=float(s), lo_m=float(lo), hi_m=float(hi), codigo=c.codigo)


def fetch_uses(codigos: Sequence[str], dataset: str = "cartagena", refresh: bool = False) -> dict:
    """``{codigo: most frequent use}`` for the predial numbers, from the R2 table. Cached in
    ``runs/cadastre/<dataset>_r2.json`` (``{codigo: {"uses": [...]}}``); numbers not cached are
    fetched (Socrata, ``R2_BATCH`` a request), and a failed fetch leaves them without a use (the
    band then reads the area band over all classes)."""
    path = R2_CACHE / f"{dataset}_r2.json"
    have: dict = {}
    if path.exists() and not refresh:
        try:
            have = {k: list((v.get("uses") if isinstance(v, dict) else v) or [])
                    for k, v in json.loads(path.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError):
            have = {}
    todo = sorted({c for c in codigos if c and c not in have})
    if todo:
        import requests  # noqa: PLC0415

        try:
            s = requests.Session()
            for i in range(0, len(todo), R2_BATCH):
                chunk = todo[i:i + R2_BATCH]
                where = "referenciacatastral in (" + ",".join(f"'{c}'" for c in chunk) + ")"
                r = s.get(f"https://{R2_DOMAIN}/resource/{R2_DATASET}.json",
                          params={"$where": where, "$select": "referenciacatastral,uso1,uso2,uso3",
                                  "$limit": 50000}, timeout=120)
                r.raise_for_status()
                for c in chunk:
                    have.setdefault(c, [])
                for row in r.json():
                    have[row["referenciacatastral"]] += [row[k] for k in ("uso1", "uso2", "uso3")
                                                         if row.get(k) and row[k] != "0"]
                time.sleep(0.2)
            R2_CACHE.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({k: {"uses": v} for k, v in have.items()}), encoding="utf-8")
        except Exception as exc:  # noqa: BLE001 - an opt-in source must never fail the run
            log.warning("[cadastre] R2 uses unavailable for %d predios (%s)", len(todo), exc)
    return {c: Counter(have[c]).most_common(1)[0][0] for c in codigos if have.get(c)}


def load_band(region: str, records: Sequence, dataset: str | None = None) -> dict:
    """``{feature_id: CadastreBand}`` for the run's ``records`` with a cadastre match (``load_region``
    matching; the use from the R2 table, ``fetch_uses``); {} when no cadastre covers them. Does not
    register the cadastre tier rules: nothing from the cadastre enters the tiers."""
    by = {r.feature_id: r for r in records if getattr(r, "geometry", None) is not None}
    matches = load_region(region, list(by.values()), dataset, register=False)
    if not matches:
        return {}
    uses = fetch_uses([c.codigo for c in matches.values() if not c.ph], dataset=dataset or "cartagena")
    out = {fid: make_band(c, by[fid].area_m2, uses.get(c.codigo)) for fid, c in matches.items()}
    log.info("[cadastre] %s: plausibility band for %d footprints (%d PH, %d with a use)",
             region, len(out), sum(b.ph for b in out.values()), sum(b.use is not None for b in out.values()))
    return out


# --------------------------------------------------------------------------- loading

def _bbox(records: Sequence) -> tuple[float, float, float, float]:
    lats = [r.centroid_lat for r in records]
    lons = [r.centroid_lon for r in records]
    return max(lats), min(lats), max(lons), min(lons)


def load_region(region: str, records: Sequence, dataset: str | None = None,
                register: bool = True) -> dict:
    """``{feature_id: CadastreReading}`` for the run's ``records``; {} when no cadastre covers
    them or the layer cannot be fetched (a run never fails on it). ``register``: add the cadastre
    tier rules (:func:`register_tier_rules`); off for the plausibility band."""
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
    if register:
        register_tier_rules()
    out = {fid: from_match(m, ds.name) for fid, m in matches.items()}
    log.info("[cadastre] %s: %d of %d footprints matched (%d publishable, %d PH, %d towers); %s",
             region, len(out), len(recs), sum(c.publishable for c in out.values()),
             sum(c.ph for c in out.values()),
             sum(c.defers and not c.ph for c in out.values()), ds.attribution)
    return out


__all__ = ["CadastreReading", "CadastreBand", "KIND", "STOREY_M", "TIER_RULES", "area_band",
           "cad_class", "disputed_by_satellite", "fetch_uses", "from_match", "load_band",
           "load_region", "make_band", "prior_for", "register_tier_rules", "row_fields",
           "storey_m", "tier_readings"]
