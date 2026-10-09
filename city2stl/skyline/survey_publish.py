"""Publish survey LiDAR heights as the ``survey`` tier of a region report (F-SKY26 step 2d).

Opt-in per site (``use_survey_heights`` in ``sites/<region>.json``; env ``SKYLINE_SURVEY_HEIGHTS``
overrides). The run only reads the cached survey records (``survey_heights.load_cache``, filled
offline by ``scripts/19_refresh_survey_truth.py``): it never reads the survey, never 3D Tiles.

    build_overlay(region, rows, records, osm_data=None, drone_seeds=()) -> SurveyOverlay | None
    SurveyOverlay.apply(row)      # publishes (or withholds) the survey value on one heights row
    set_active(overlay) / active()  # the overlay the report pages use for tag-only rows
    summarize(rows) -> dict       # counts for ``heights.json``

A footprint with a cached p95 roof height (``stat`` 2) publishes it: ``tier`` ``survey``,
``effective_height_m`` the survey p95, ``selection_reason`` saying so, and on the row

- ``survey_provider`` (the lidar project, e.g. ``USGS_LPC_PR_PRVI_E_2018``; else the provider),
  ``survey_source`` (the provider key), ``survey_year`` (the last flight year), ``survey_years``,
  ``survey_stat`` (``"p95"``), ``survey_m``, ``survey_roof_m`` (the stored p50 / p70 / p90 / p95 /
  p99 / max), ``survey_ground_p5_m``, ``survey_cells``;
- the image readings and the pre-survey answer stay for comparison: ``per_seed_median_m``,
  ``satellite``, ``no_survey_height_m`` / ``_source`` / ``_tier`` (the survey-blind answer the
  benchmark scores: a survey row is half of its own truth, so it is never scored on the survey).

Survey truth outranks tags and image readings, except where the survey may be stale. Those rows
keep tier ``survey`` with ``survey_stale`` True, ``survey_stale_codes`` and ``survey_stale_reason``:

- ``start_date``: the OSM ``start_date`` is in or after the survey's last year (the benchmark's
  ``temporal_flag`` rule: the survey may show a construction site or an empty lot);
- ``readings_2x``: the image readings agree with each other (``tiers.agree``; or the row was
  ``corroborated``) but sit more than 2x from the survey: a likely new building (or a demolished
  one).

A **nested** footprint (``benchmark.nested_keys``: half or more of it under a taller footprint of
the report) does not publish the survey value: the survey sees only the top of the stack. It keeps
its pre-survey tier and height, and carries ``survey_withheld_reason`` / ``survey_withheld_m``.
"""

from __future__ import annotations

import logging
import math
import os
from collections import Counter
from collections.abc import Iterable, Sequence

from ._core.tiers import SATELLITE_KINDS, agree, log_ratio, selection_reason

logger = logging.getLogger(__name__)

#: The statistic published: the survey p95 (``benchmark.STAT_VERSION`` 2), the truth's own.
STAT = "p95"
#: Image readings this far (factor, either way) from the survey flag it stale.
STALE_FACTOR = 2.0
CODE_START_DATE = "start_date"
CODE_READINGS = "readings_2x"
NESTED_REASON = "nested part: the survey sees only the top of the stack"
_ENV = "SKYLINE_SURVEY_HEIGHTS"


def enabled(site_flag: bool) -> bool:
    """Whether survey heights are published: the env var ``SKYLINE_SURVEY_HEIGHTS`` (1 / 0) when
    set, else the site flag ``use_survey_heights``."""
    env = os.environ.get(_ENV, "").strip().lower()
    if env in ("1", "true", "yes", "on"):
        return True
    if env in ("0", "false", "no", "off"):
        return False
    return bool(site_flag)


def _ring_of(rec) -> list | None:
    """The record's exterior ring as ``heights.json`` writes it (lon/lat, 6 decimals), so the
    key matches the benchmark's."""
    try:
        return [[round(x, 6), round(y, 6)] for x, y in rec.geometry.exterior.coords]
    except Exception:  # noqa: BLE001 - a multipolygon or an empty geometry has no ring
        return None


def _osm_ids(osm_data: dict | None, records: Iterable) -> dict[str, str]:
    """``{feature_id: "way/123"}`` by matching each record's centroid to an OSM feature's (as
    ``19_refresh_survey_truth.pipeline_footprints`` does)."""
    if not osm_data:
        return {}
    from shapely.geometry import shape

    ids: dict[tuple, str] = {}
    for f in (osm_data.get("buildings") or {}).get("features") or []:
        oid = (f.get("properties") or {}).get("osm_id")
        if not oid:
            continue
        try:
            poly = shape(f.get("geometry") or {})
            if poly.is_empty:
                continue
            if not poly.is_valid:
                poly = poly.buffer(0)
            c = poly.centroid
        except Exception:  # noqa: BLE001
            continue
        ids[(round(c.y, 7), round(c.x, 7))] = str(oid)
    return {r.feature_id: ids[k] for r in records
            if (k := (round(r.centroid_lat, 7), round(r.centroid_lon, 7))) in ids}


def _start_dates(region: str) -> dict[str, str]:
    """The cached OSM ``start_date`` per OSM id (``19_refresh_survey_truth --temporal``), or {}."""
    import json

    from . import benchmark as bm

    path = bm.BENCHMARK_ROOT / "truth" / f"{bm.region_key(region)}.start_dates.json"
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("start_dates") or {}
    except (OSError, ValueError):
        return {}


class SurveyOverlay:
    """Survey records per feature id, and the rule that publishes them on a heights row."""

    def __init__(self, by_id: dict[str, dict], drone_seeds: Iterable[str] = ()):
        self.by_id = by_id
        self.drone_seeds = set(drone_seeds)

    def __len__(self) -> int:
        return len(self.by_id)

    # -- the image readings of a row, for the staleness check

    def _agreeing_value(self, row: dict) -> tuple[float, list[str]] | None:
        """``(value, names)`` when the row's image readings agree with each other, else None.
        Drone seeds' medians and satellite lean / multiview / stereo readings count; a row that
        was ``corroborated`` agrees by definition (its tag plus a witness counts too)."""
        readings: list[tuple[str, float]] = []
        for seed, v in (row.get("per_seed_median_m") or {}).items():
            if v is not None and seed in self.drone_seeds:
                readings.append((f"drone:{seed}", float(v)))
        for method, mv in (row.get("satellite") or {}).items():
            h = mv[0] if isinstance(mv, (list, tuple)) else mv
            if method in SATELLITE_KINDS and h is not None:
                readings.append((str(method), float(h)))
        prior_tier = row.get("no_survey_tier") or row.get("tier")
        pub = row.get("no_survey_height_m", row.get("effective_height_m"))
        if prior_tier in ("corroborated", "verified_2") and pub is not None:
            return float(pub), [str(m) for m in row.get("tier_methods") or ()] or ["corroborated"]
        if len(readings) >= 2:
            vals = [v for _, v in readings]
            if agree(min(vals), max(vals)):
                vals.sort()
                mid = vals[len(vals) // 2] if len(vals) % 2 else 0.5 * (
                    vals[len(vals) // 2 - 1] + vals[len(vals) // 2])
                return mid, [n for n, _ in readings]
        return None

    # -- publish

    def apply(self, row: dict) -> str | None:
        """Publish the survey height on ``row`` in place. Returns ``"survey"`` (published, fresh),
        ``"stale"`` (published, flagged), ``"nested"`` (withheld), or None (no survey record)."""
        info = self.by_id.get(row.get("feature_id"))
        if not info or row.get("tier") == "survey":
            return None
        survey_m = float(info["survey_m"])
        # the survey-blind answer the benchmark scores, kept before it is overwritten
        row.setdefault("no_survey_height_m", row.get("effective_height_m"))
        row.setdefault("no_survey_source", row.get("effective_height_source"))
        row.setdefault("no_survey_tier", row.get("tier"))
        if info.get("nested"):
            row["survey_withheld_reason"] = NESTED_REASON
            row["survey_withheld_m"] = survey_m
            return "nested"
        years = info.get("years") or None
        year = int(years[-1]) if years else None
        codes, reasons = [], []
        sd = info.get("start_date")
        if sd:
            from . import benchmark as bm

            if bm.temporal_flag(sd, years) == bm.TEMPORAL_MAY_POSTDATE:
                codes.append(CODE_START_DATE)
                reasons.append(f"survey year {year} is not after OSM start_date {sd}: the "
                               f"building may be newer than the survey")
        pub = row.get("no_survey_height_m")
        got = self._agreeing_value(row)
        if got is not None and pub is not None:
            value, names = got
            if log_ratio(value, survey_m) > math.log(STALE_FACTOR) + 1e-12:
                codes.append(CODE_READINGS)
                hi, lo = (value, survey_m) if value > survey_m else (survey_m, value)
                reasons.append(f"image readings agree ({', '.join(names)}: {value:.0f} m) but "
                               f"sit {hi / lo:.1f}x {'above' if value > survey_m else 'below'} "
                               f"the survey {survey_m:.0f} m: likely a new building")
        provider = info.get("project") or info.get("provider")
        row.update(
            effective_height_m=survey_m,
            effective_height_source=f"survey:{provider}",
            tier="survey", tier_methods=[f"survey:{STAT}"], verified=not codes,
            survey_provider=provider, survey_source=info.get("provider"),
            survey_year=year, survey_years=list(years) if years else None,
            survey_stat=STAT, survey_m=survey_m, survey_roof_m=info.get("roof_m"),
            survey_ground_p5_m=info.get("ground_p5_m"), survey_cells=info.get("survey_cells"),
            survey_stale=bool(codes), survey_stale_codes=codes,
            survey_stale_reason="; ".join(reasons) or None)
        if sd:
            row["survey_start_date"] = sd
        row["selection_reason"] = selection_reason(row)
        return "stale" if codes else "survey"


_ACTIVE: SurveyOverlay | None = None


def set_active(overlay: SurveyOverlay | None) -> None:
    """The overlay the report pages apply to their tag-only rows (``tier_display.
    with_unmeasured_tags``); None clears it. Set once per run by ``region_pdf``."""
    global _ACTIVE
    _ACTIVE = overlay


def active() -> SurveyOverlay | None:
    return _ACTIVE


def build_overlay(region: str, rows: Sequence[dict], records: Sequence, osm_data: dict | None = None,
                  drone_seeds: Iterable[str] = ()) -> SurveyOverlay | None:
    """The overlay for a run, from the cached survey of ``region`` (None when it has none).

    ``rows``: the run's estimated heights rows; ``records``: every ``BuildingRecord``. The
    published set (``rows`` plus OSM-tagged records no row covers, as ``heights.json`` lists it)
    decides which footprints are nested.
    """
    from . import benchmark as bm
    from . import survey_heights as sh

    cache = sh.load_cache(region)
    if not cache:
        logger.info("[survey_publish] %s: no cached survey heights (runs/survey)", region)
        return None
    by_rec = {r.feature_id: r for r in records}
    have = {row.get("feature_id") for row in rows}
    published_ids = [fid for fid in have if fid in by_rec]
    published_ids += [r.feature_id for r in records
                      if r.feature_id not in have and r.height_source in ("osm_tag", "osm_levels")
                      and r.height_tag_m and r.geometry is not None]
    keys: dict[str, str] = {}
    fps: list[dict] = []
    for fid in published_ids:
        ring = _ring_of(by_rec[fid])
        if not ring or len(ring) < 3:
            continue
        key = bm.footprint_key(ring)
        keys[fid] = key
        fps.append({"key": key, "footprint_lonlat": ring,
                    "height_tag_m": by_rec[fid].height_tag_m})
    nested = bm.nested_keys(fps) if fps else set()
    truth = bm.load_truth_cache(region)
    ids = _osm_ids(osm_data, (by_rec[f] for f in keys))
    dates = _start_dates(region) if ids else {}
    by_id: dict[str, dict] = {}
    for fid, key in keys.items():
        s = cache.get(key)
        if (not s or s.get("error") or s.get("survey_m") is None
                or s.get("stat") != bm.STAT_VERSION):
            continue
        project = s.get("project") or (truth.get(key) or {}).get("survey_project")
        by_id[fid] = {**s, "project": project, "nested": key in nested,
                      "start_date": dates.get(ids.get(fid, ""))}
    logger.info("[survey_publish] %s: %d of %d published footprints have a cached survey height "
                "(%d nested)", region, len(by_id), len(keys), sum(1 for v in by_id.values()
                                                                   if v["nested"]))
    return SurveyOverlay(by_id, drone_seeds) if by_id else None


def summarize(rows: Iterable[dict]) -> dict:
    """What ``heights.json`` records about the survey rows: counts by provider, how many are
    stale-flagged (and by which code), and how many nested footprints withheld a survey value."""
    rows = list(rows)
    survey = [r for r in rows if r.get("tier") == "survey"]
    stale = Counter(c for r in survey for c in (r.get("survey_stale_codes") or ()))
    return {"n_survey": len(survey),
            "n_stale": sum(1 for r in survey if r.get("survey_stale")),
            "stale_by_code": dict(stale),
            "n_nested_withheld": sum(1 for r in rows if r.get("survey_withheld_reason")),
            "providers": dict(Counter(str(r.get("survey_provider")) for r in survey))}
