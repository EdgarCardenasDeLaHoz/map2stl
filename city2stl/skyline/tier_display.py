"""How verification tiers look in the reports (F-SKY26 step 2f). Pure: no I/O, no numpy.

One place for the labels, colours, hover text and survey attribution lines that the region HTML
report (``html_report``), the PDF heights page (``_region_render._pages``) and the app
(``app/server/routers/reports.py``) show for a ``heights.json`` row's ``tier``.

Colours: Okabe-Ito, safe for the common colour-vision deficiencies; ``single`` is the warm
"check this" colour, ``prior`` a neutral grey. Tiers come from ``_core.tiers.TIERS``.

Survey rows (2d) name their provider in ``survey_provider`` (also read: ``survey_source`` and an
``effective_height_source`` of ``survey:<provider>``). Every provider a report shows heights from
needs its licence line printed with it (``survey_attributions``). A height resting on a cadastre
(``tier_methods`` or the published source names ``cadastre``; ``cadastre_heights``) needs the
cadastre's attribution and share-alike line (``height_attributions`` prints both kinds).

Measured vs tag (review item 6, 2026-10-09): a tagged row whose agreeing readings sit more than
10 % from its published tag (``tag_disagrees``) shows both values and the reason (``tag_note``, in
``tier_hover``; ``tag_disagreements`` / ``tag_disagreement_lines`` for lists). The hover also says
why the value was published (``selection_reason``).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ._core.tiers import (
    TAG_DISAGREE_REL,
    TIERS,
    VERIFIED_TIERS,
    canonical_tier,
    selection_reason,
    upgrade_tiers,
)

#: tier -> short label shown in tables and legends.
TIER_LABELS = {
    "survey": "Survey lidar",
    "corroborated": "Corroborated",
    "tag": "OSM tag",
    "single": "Single reading",
    "prior": "Prior estimate",
    "unlabelled": "No tier",
}
#: tier -> one word, for tight columns (the PDF's monospace list).
TIER_SHORT = {"survey": "survey", "corroborated": "corrob.", "tag": "tag", "single": "single",
              "prior": "prior", "unlabelled": "-"}
#: tier -> one plain line saying what it means (legends, hover).
TIER_HINTS = {
    "survey": "measured by an airborne lidar survey",
    "corroborated": "two independent readings agree within 25 % of the smaller",
    "tag": "the building's OpenStreetMap height or levels tag",
    "single": "one kind of image reading, not confirmed by a second",
    "prior": "no usable reading; the height model's estimate",
    "unlabelled": "written before tiers existed",
}
#: tier -> colour (Okabe-Ito).
TIER_COLORS = {
    "survey": "#0072B2",      # blue
    "corroborated": "#009E73",  # bluish green
    "tag": "#56B4E9",         # sky blue
    "single": "#D55E00",      # vermillion
    "prior": "#999999",       # grey
    "unlabelled": "#CCCCCC",
}
#: Tiers whose height nothing else confirmed: the reports mark them "unverified".
UNVERIFIED_TIERS = ("tag", "single", "prior")

#: survey provider -> the attribution its licence asks for.
SURVEY_ATTRIBUTIONS = {
    "usgs_3dep": "Survey heights: USGS 3D Elevation Program (3DEP) lidar. Public domain.",
    "usgs_3dep_ept": "Survey heights: USGS 3D Elevation Program (3DEP) lidar. Public domain.",
    "ign_lidarhd": ("Survey heights: IGN, LiDAR HD (MNH). Licence Ouverte / Open Licence "
                    "Etalab 2.0."),
    "rediam_mdhn": ("Survey heights: Junta de Andalucía, REDIAM, Modelo Digital de Alturas "
                    "(MDHN). CC BY 4.0."),
    "cnig_mdsn": ("Survey heights: © Instituto Geográfico Nacional (CNIG), PNOA-LiDAR MDSn. "
                  "CC BY 4.0."),
    "cuzk_dmp": "Survey heights: © ČÚZK, DMP 1G and DMR 5G. CC BY 4.0.",
}


#: ``effective_height_source`` (after a ``withheld:`` prefix) -> plain words.
SOURCE_LABELS = {
    "osm_tag": "OSM height tag",
    "osm_levels": "OSM levels tag",
    "elevated": "drone (elevated seed)",
    "satellite": "satellite",
    "prior_gbm": "height model",
    "prior": "height model",
    "constant": "default height",
    "street_view": "Street View",
    "survey": "survey lidar",
    "cadastre": "cadastre floors",
}


def source_label(row: dict) -> str:
    """The row's published-height source in plain words."""
    src = str(row.get("effective_height_source") or "")
    if src.startswith("withheld:"):
        src = src.split(":", 1)[1]
    if src.startswith("survey"):
        return SOURCE_LABELS["survey"]
    return SOURCE_LABELS.get(src, src.replace("_", " ") or "unknown")


def with_unmeasured_tags(rows: Sequence[dict], records: Iterable) -> list[dict]:
    """``rows`` plus a ``tag`` row for every OSM-tagged record no row covers, as
    ``region_pdf._write_heights_json`` adds them, so the reports count the same tiers as
    ``heights.json``. ``records``: ``BuildingRecord``-like (``feature_id``, ``name``,
    ``height_source``, ``height_tag_m``, ``geometry``). When the run publishes survey heights
    (``survey_publish.active()``) a tag row whose footprint has one becomes a ``survey`` row."""
    from .survey_publish import active  # noqa: PLC0415

    survey = active()
    out = list(rows)
    seen = {r.get("feature_id") for r in rows}
    for rec in records or ():
        fid = getattr(rec, "feature_id", None)
        if (fid is None or fid in seen or getattr(rec, "height_source", None)
                not in ("osm_tag", "osm_levels") or not getattr(rec, "height_tag_m", None)):
            continue
        seen.add(fid)
        row = {"feature_id": fid, "name": getattr(rec, "name", fid), "measured": False,
               "effective_height_m": float(rec.height_tag_m),
               "effective_height_source": rec.height_source, "n_seeds": 0,
               "tier": "tag", "tier_methods": ["osm_tag"], "verified": False}
        row["selection_reason"] = selection_reason(row)
        if survey is not None:
            survey.apply(row)
        out.append(row)
    return out


def row_tier(row: dict) -> str:
    """A row's tier, or ``unlabelled`` (a schema 1 file, or a tier this module does not know)."""
    t = canonical_tier(row.get("tier"))
    return t if t in TIERS else "unlabelled"


def tier_counts(rows: Sequence[dict], stored: dict | None = None) -> dict[str, int]:
    """Rows per tier in ``TIERS`` order, ``unlabelled`` last only when non-zero. ``stored``: the
    file's own ``tier_counts``, used as is when given (it is what the run wrote)."""
    if stored:
        stored = upgrade_tiers({"tier_counts": stored})["tier_counts"]
        out = {t: int(stored.get(t, 0) or 0) for t in TIERS}
        extra = int(stored.get("unlabelled", 0) or 0)
    else:
        out = {t: 0 for t in TIERS}
        extra = 0
        for r in rows:
            t = row_tier(r)
            if t == "unlabelled":
                extra += 1
            else:
                out[t] += 1
    if extra:
        out["unlabelled"] = extra
    return out


def is_unverified(row: dict) -> bool:
    return row_tier(row) in UNVERIFIED_TIERS


def tier_hover(row: dict) -> str:
    """Hover text for a row's tier: what it means and the methods behind it."""
    t = row_tier(row)
    methods = row.get("tier_methods") or []
    text = f"{TIER_LABELS[t]}: {TIER_HINTS[t]}"
    if methods:
        text += ". Methods: " + ", ".join(str(m) for m in methods)
    disputed = row.get("disputed_by") or []
    if disputed:
        text += ". Disputed by: " + ", ".join(str(m) for m in disputed)
    if row.get("prior_disagrees") and t == "single":
        text += ". More than 2x away from the prior"
    if t == "survey" and row.get("survey_stale"):
        text += ". Survey flagged stale: " + str(row.get("survey_stale_reason") or "see the row")
    if row.get("survey_withheld_reason"):
        text += (f". Survey {float(row.get('survey_withheld_m') or 0):.0f} m not published: "
                 + str(row["survey_withheld_reason"]))
    note = tag_note(row)
    if note:
        text += ". " + note
    if row.get("selection_reason"):
        text += ". Published because: " + str(row["selection_reason"])
    return text


def tag_note(row: dict) -> str | None:
    """Both values and the reason, when a tagged row's agreeing readings disagree with its
    published tag (``tag_disagrees``, review item 6), else None: "Measured 136 m (lean, shadow)
    vs OSM tag 156 m (13 % lower); the tag is published until the user decides"."""
    if not row.get("tag_disagrees") or row.get("measured_m") is None:
        return None
    try:
        m = float(row["measured_m"])
        tag = float(row.get("height_tag_m") or row.get("effective_height_m"))
    except (TypeError, ValueError):
        return None
    methods = ", ".join(str(x) for x in row.get("measured_methods") or ()) or "readings"
    off = 100.0 * (m - tag) / tag if tag else 0.0
    return (f"Measured {m:.0f} m ({methods}) vs OSM tag {tag:.0f} m "
            f"({abs(off):.0f} % {'lower' if off < 0 else 'higher'}); the tag is published until "
            f"the user decides")


def tag_disagreements(rows: Iterable[dict]) -> list[dict]:
    """Every tagged row whose measurement disagrees with its published tag by more than
    ``TAG_DISAGREE_REL`` (``tag_disagrees``): ``{feature_id, name, tag_m, measured_m,
    measured_methods, off_pct, tier}``, largest disagreement first. For the report lists and
    ``heights.json``'s ``tag_disagreements``."""
    out = []
    for r in rows:
        if not r.get("tag_disagrees") or r.get("measured_m") is None:
            continue
        tag = r.get("height_tag_m") or r.get("effective_height_m")
        if not tag:
            continue
        m = float(r["measured_m"])
        out.append({"feature_id": r.get("feature_id"), "name": r.get("name"),
                    "tag_m": float(tag), "measured_m": m,
                    "measured_methods": list(r.get("measured_methods") or ()),
                    "off_pct": round(100.0 * (m - float(tag)) / float(tag), 1),
                    "tier": row_tier(r)})
    return sorted(out, key=lambda d: -abs(d["off_pct"]))


def tag_disagreement_lines(rows: Iterable[dict], limit: int = 12) -> list[str]:
    """Plain lines for a report page: one per ``tag_disagreements`` row (both values and the
    reason), at most ``limit``, with a heading; [] when there are none."""
    ds = tag_disagreements(rows)
    if not ds:
        return []
    lines = [f"Measured vs OSM tag (> {TAG_DISAGREE_REL:.0%} apart; tag published, the user decides):"]
    for d in ds[:limit]:
        lines.append(f"  {str(d['name'] or d['feature_id'])[:18]:<18} tag {d['tag_m']:4.0f} m  "
                     f"measured {d['measured_m']:4.0f} m ({', '.join(d['measured_methods'])})")
    if len(ds) > limit:
        lines.append(f"  ... and {len(ds) - limit} more")
    return lines


def withheld_note(row: dict) -> str | None:
    """A one-line note when the row's single reading was replaced by the prior, else None."""
    reading = row.get("single_reading_m")
    if reading is None and not row.get("withheld_reason"):
        return None
    reason = row.get("withheld_reason") or "withheld"
    if reading is None:
        return f"Reading withheld ({reason}); prior published"
    try:
        return f"Reading of {float(reading):.0f} m withheld ({reason}); prior published"
    except (TypeError, ValueError):
        return f"Reading withheld ({reason}); prior published"


def drone_seeds(rows: Iterable[dict], extra: Iterable[str] = ()) -> set[str]:
    """Seeds that gave drone (elevated) readings: named in a row's ``tier_methods`` as
    ``drone:<seed>``, plus ``extra`` (e.g. the run's elevated seeds)."""
    out = set(extra)
    for r in rows:
        for m in r.get("tier_methods") or ():
            if isinstance(m, str) and m.startswith("drone:"):
                out.add(m.split(":", 1)[1])
    return out


def drone_disagreements(rows: Iterable[dict], seeds: set[str]) -> list[float]:
    """Per building seen by two or more drone seeds: the spread (max - min) of those seeds'
    medians in ``per_seed_median_m``. Only readings that reached the aggregate are there, i.e.
    the trusted ones not left out by the tower-behind check. Sorted ascending."""
    out = []
    for r in rows:
        vals = [float(v) for s, v in (r.get("per_seed_median_m") or {}).items()
                if s in seeds and isinstance(v, (int, float))]
        if len(vals) >= 2:
            out.append(max(vals) - min(vals))
    return sorted(out)


def survey_provider(row: dict) -> str | None:
    """The survey provider behind a ``survey`` row, or None."""
    if row_tier(row) != "survey":
        return None
    p = row.get("survey_provider") or row.get("survey_source")
    if not p:
        src = str(row.get("effective_height_source") or "")
        p = src.split(":", 1)[1] if src.startswith("survey:") else None
    return str(p) if p else "unknown"


def survey_providers(rows: Iterable[dict]) -> dict[str, int]:
    """Survey provider -> rows published from it."""
    out: dict[str, int] = {}
    for r in rows:
        p = survey_provider(r)
        if p is not None:
            out[p] = out.get(p, 0) + 1
    return out


#: Cadastre dataset -> the attribution and share-alike line CC BY-SA 4.0 asks for. Published
#: heights derived from it carry the same licence.
CADASTRE_ATTRIBUTIONS = {
    "cartagena": ("Building floors: Catastro Distrital de Cartagena de Indias, Área Metropolitana "
                  "de Barranquilla (AMB), datos.gov.co d7hk-qg8h. CC BY-SA 4.0; heights derived "
                  "from it are shared under the same licence."),
}


def uses_cadastre(row: dict) -> bool:
    """Whether the row's published height or its tier rests on a cadastre reading."""
    src = str(row.get("effective_height_source") or "")
    return src.endswith("cadastre") or "cadastre" in (row.get("tier_methods") or ())


def cadastre_attributions(rows: Iterable[dict]) -> list[str]:
    """The licence lines for the cadastres these rows' heights rest on."""
    names = sorted({str((r.get("cadastre") or {}).get("dataset") or "cartagena")
                    for r in rows if uses_cadastre(r)})
    return [CADASTRE_ATTRIBUTIONS.get(n, f"Building floors: {n} cadastre (CC BY-SA 4.0).")
            for n in names]


def height_attributions(rows: Sequence[dict]) -> list[str]:
    """Every licence line a report of these rows prints: survey providers, then cadastres."""
    return survey_attributions(survey_providers(rows)) + cadastre_attributions(rows)


def survey_attributions(providers: Iterable[str]) -> list[str]:
    """The licence lines for these providers, de-duplicated, in a stable order."""
    lines: list[str] = []
    for p in sorted(set(providers)):
        # a lidar project name (``survey_provider`` of a 2d row): USGS_LPC_* is a 3DEP project
        key = "usgs_3dep" if str(p).upper().startswith("USGS_") else p
        line = SURVEY_ATTRIBUTIONS.get(key, f"Survey heights: {p} (licence not recorded).")
        if line not in lines:
            lines.append(line)
    return lines


__all__ = ["CADASTRE_ATTRIBUTIONS", "SOURCE_LABELS", "SURVEY_ATTRIBUTIONS", "TIERS",
           "TIER_COLORS", "TIER_HINTS", "cadastre_attributions", "height_attributions",
           "uses_cadastre",
           "TIER_LABELS", "TIER_SHORT", "drone_disagreements", "drone_seeds", "UNVERIFIED_TIERS", "VERIFIED_TIERS", "is_unverified", "row_tier",
           "source_label", "survey_attributions", "survey_provider", "survey_providers",
           "tag_disagreement_lines", "tag_disagreements", "tag_note",
           "tier_counts", "tier_hover", "with_unmeasured_tags", "withheld_note"]
