"""city2stl.skyline.service: the skyline region run as a library call (F-UNIFY Phase 2).

``run_region(region_or_bbox, config, progress_cb)`` is the one entry point. The script
(``scripts/08_region_skyline_pdf.py``), ``region_pdf.run_region_pdf_report`` and the app's Heights
page job (``app/server/core/skyline_runs.py::run_region``) are thin wrappers over it.

One run:
- builds its :class:`~city2stl.skyline.config.SkylineConfig` (site file, then env, then the
  caller's overrides) unless the caller passes one, and makes it active (``config.use``) for the
  run's context, so two runs in one process do not share switches;
- runs inside ``google_usage.cache_only()`` when ``config.cache_only`` (a paid fetch is skipped and
  counted) and inside ``google_usage.tally()`` (paid requests and cache hits);
- writes the report sidecar ``report_summary.json`` (``report_index.SIDECAR``) beside
  ``heights.json``, read by the report listings in place of re-parsing ``index.html``.

Pool workers inside the run must be submitted through ``google_usage.bind`` so they see the
config, the scope and the tally (a thread does not inherit contextvars).
"""

from __future__ import annotations

import json
import logging
import time
from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from geo2stl import google_usage

from . import config as _config
from .config import SkylineConfig

logger = logging.getLogger(__name__)

#: Where a run writes when the caller gives no ``output_pdf``.
DEFAULT_REPORTS = Path(__file__).resolve().parent / "runs" / "region_reports"


@dataclass
class RegionResult:
    """What one region run produced.

    - ``summary``: the pipeline's summary dict (what ``run_region_pdf_report`` returned).
    - ``heights``: the published rows (``heights.json`` ``buildings``); [] when it was not written.
    - ``report_dir``: the report folder (``<output_pdf parent>/<output_pdf stem>``).
    - ``paths``: ``pdf`` / ``heights`` / ``index`` / ``sidecar``, each a path or None.
    - ``cost``: ``paid_images`` (Street View images sent), ``cached_hits`` (images answered from
      the cache), ``paid`` / ``cached`` (per ``google_usage`` kind) and ``skipped_paid`` (paid
      fetches skipped by cache-only, per kind).
    """
    region: str
    summary: dict
    heights: list[dict] = field(default_factory=list)
    report_dir: Path | None = None
    paths: dict[str, str | None] = field(default_factory=dict)
    cost: dict[str, Any] = field(default_factory=dict)
    config: dict[str, Any] = field(default_factory=dict)


def _cost(tally: dict, skipped: dict) -> dict:
    return {"paid_images": int(tally["paid"].get("streetview_image", 0)),
            "cached_hits": int(tally["cached"].get("streetview_image", 0)),
            "paid": dict(tally["paid"]), "cached": dict(tally["cached"]),
            "skipped_paid": dict(skipped)}


def _region_and_bbox(region_or_bbox) -> tuple[str, Any]:
    """``(region name, bbox or None)``: a name loads its bbox in the pipeline."""
    if isinstance(region_or_bbox, str):
        return region_or_bbox, None
    name = getattr(region_or_bbox, "name", None)
    if not name:
        raise TypeError("run_region needs a region name or a RegionBBox")
    return str(name), region_or_bbox


def _sidecar(region: str, report_dir: Path, summary: dict, cost: dict, cfg: SkylineConfig,
             output_pdf: Path) -> tuple[list[dict], dict[str, str | None]]:
    """Write ``report_summary.json``; returns (heights rows, paths)."""
    from . import report_index  # noqa: PLC0415

    heights_path, index_path = report_dir / "heights.json", report_dir / "index.html"
    doc: dict = {}
    try:
        doc = json.loads(heights_path.read_text(encoding="utf-8")) if heights_path.is_file() else {}
    except (OSError, ValueError) as exc:
        logger.warning(f"[sidecar] cannot read {heights_path}: {exc}")
    rows = doc.get("buildings") or []
    txt = ""
    if index_path.is_file():
        txt = index_path.read_text(encoding="utf-8", errors="replace")
    paths = {"pdf": str(output_pdf) if output_pdf.is_file() else None,
             "heights": str(heights_path) if heights_path.is_file() else None,
             "index": str(index_path) if index_path.is_file() else None,
             "sidecar": None}
    if not report_dir.is_dir():
        return rows, paths
    from .tier_display import tier_counts  # noqa: PLC0415
    try:
        side = report_index.write_sidecar(report_dir, {
            "region": doc.get("region", region),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "summary": summary,
            "n_buildings": len(rows),
            "tier_counts": tier_counts(rows, doc.get("tier_counts")),
            "bbox_nsew": doc.get("bbox_nsew"),
            "stats": report_index.sidecar_stats(txt) if txt else None,
            "panoramas": report_index.parse_pano_rows(txt) if txt else None,
            "paths": {k: (Path(v).name if v else None) for k, v in paths.items()},
            "cost": cost,
            "config": cfg.to_dict(),
        })
        paths["sidecar"] = str(side)
    except Exception as exc:          # a sidecar failure must not fail the run
        logger.warning(f"[sidecar] failed: {exc}")
    return rows, paths


def run_region(
    region_or_bbox,
    config: SkylineConfig | None = None,
    progress_cb=None,
    *,
    output_pdf: Path | str | None = None,
    seed_urls: list[str] | None = None,
    api_key: str | None = None,
    trace=None,
    skip_pdf: bool = False,
    pipeline=None,
) -> RegionResult:
    """Run the skyline pipeline for one region and return its :class:`RegionResult`.

    - ``region_or_bbox``: a region name (``sites/<name>.json`` / the regions table) or a
      ``RegionBBox`` (its ``name`` picks the site file).
    - ``config``: None builds ``SkylineConfig.build(region)`` (site file, then env).
    - ``progress_cb(label, done, total)``: progress; raising from it stops the run (the
      exception propagates, e.g. the app's ``RunCancelled``).
    - ``pipeline``: replaces ``region_pdf._run_pipeline`` (tests); called as
      ``pipeline(region, output_pdf, seed_urls=, explicit_api_key=, bbox=, trace=, skip_pdf=,
      progress_cb=)`` and returns the summary dict.
    """
    region, bbox = _region_and_bbox(region_or_bbox)
    cfg = config if config is not None else SkylineConfig.build(region)
    output_pdf = (Path(output_pdf) if output_pdf is not None
                  else DEFAULT_REPORTS / f"{region}_skyline_report.pdf")
    if pipeline is None:
        from . import region_pdf  # noqa: PLC0415
        pipeline = region_pdf._run_pipeline

    with _config.use(cfg), google_usage.tally() as tally, \
            (google_usage.cache_only() if cfg.cache_only else nullcontext({})) as skipped:
        summary = pipeline(region, output_pdf, seed_urls=seed_urls, explicit_api_key=api_key,
                           bbox=bbox, trace=trace, skip_pdf=skip_pdf, progress_cb=progress_cb)
    cost = _cost(tally, skipped)
    report_dir = output_pdf.parent / output_pdf.stem
    rows, paths = _sidecar(region, report_dir, summary or {}, cost, cfg, output_pdf)
    return RegionResult(region=region, summary=summary or {}, heights=rows,
                        report_dir=report_dir if report_dir.is_dir() else None,
                        paths=paths, cost=cost, config=cfg.to_dict())
