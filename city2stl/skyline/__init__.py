"""city2stl.skyline — Street-View → OSM building-height research module.

Estimates per-building heights for a city region by:
  1. Capturing 12-view spin panos at each seed location via Google Street
     View Static API.
  2. Running SegFormer-b0 (ADE20K) for building/sky/water segmentation.
  3. Jointly optimizing the pano-to-geographic heading offset across all
     spin views (width-weighted per-building IoU objective).
  4. Projecting OSM footprints into each registered view and extracting
     pinhole-y → height_m per building.
  5. Aggregating per-view estimates with outlier-seed downweighting.

Layout:
  - ``_core/``               — CV/geometry primitives, step timer (import the
                                defining submodule; there is no façade)
  - ``_pano/``               — per-seed capture, heading, detection
  - ``_region_render/``      — PDF drawing and pages
  - ``_report_plots/``       — HTML-report figures
  - ``region_pdf.py``        — region orchestration; ``region_config``,
    ``region_data`` and ``region_types`` hold its flags, I/O and dataclasses

Entry point: ``run_region`` (``service.py``; F-UNIFY Phase 2) with its ``SkylineConfig``
(``config.py``), returning a ``RegionResult``. ``scripts/08_region_skyline_pdf.py``, the app's
Heights page and ``region_pdf.run_region_pdf_report`` wrap it.

This is the cross-view research pipeline. The ML height stack and the external
height providers it builds on are general-purpose and live in
``city2stl/height/``. See ``README.md`` and ``docs/STATUS.md`` for
current strengths, weaknesses, and known failure modes.
"""

from ._core.height import aggregate_building_heights
from ._core.skyline import detect_skyline_contour

__all__ = ["RegionResult", "SkylineConfig", "aggregate_building_heights",
           "detect_skyline_contour", "run_region"]

_LAZY = {"run_region": "service", "RegionResult": "service", "SkylineConfig": "config"}


def __getattr__(name):
    # lazy: importing the package must not pull in the pipeline (torch, cv2) for a config read
    if name in _LAZY:
        import importlib  # noqa: PLC0415
        return getattr(importlib.import_module(f".{_LAZY[name]}", __name__), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
