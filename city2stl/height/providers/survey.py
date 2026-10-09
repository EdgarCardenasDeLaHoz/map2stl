"""Registry of the surveyed nDSM providers (F-LANDMARK §4).

One interface over every lidar-derived height-above-ground source, for the
landmark nDSM override (``city2stl.landmarks``)::

    ndsm_for_bbox(name, bbox, resolution_m=None) -> (array row0=north, lon/lat Affine) | None
    available_for_bbox(bbox) -> [{name, label, resolution_m, available, note}, ...]
    years_for_bbox(name, bbox) -> (first, last) survey year | None   (``YEARS``)

Contract and caching: ``_survey``. Providers (one module each):

=================  ===========================================================
``ign_lidarhd``    France, IGN LiDAR HD MNH, 0.5 m (WMS-R float GeoTIFF)
``rediam_mdhn``    Andalucía, REDIAM MDHN, 1 m, 2020-21 (regional COG)
``cnig_mdsn``      Spain, CNIG MDSn edificación, 2.5 m, 2008-15 (IDEE WCS)
``cuzk_dmp``       Czechia, ČÚZK DMP 1G − DMR 5G, ~1 m (ArcGIS ImageServer)
``usgs_3dep``      USA, 3DEP point cloud: COPC (laspy), then EPT (laspy), then EPT (PDAL)
``usgs_3dep_ept``  USA, 3DEP point cloud, newest USGS EPT project only (laspy)
=================  ===========================================================

Documented gaps (``docs/reference/survey-sources.md``): Spain's 0.5 m PNOA 2nd/3rd-coverage
surfaces (only through an undocumented download form), Lisbon (DGT, account
required), Barcelona ICGC (WMS returns pictures; point cloud only), Salzburg
BEV (50 km tiles; not wired), Cartagena de Indias (no open surface model).
"""

from __future__ import annotations

from types import SimpleNamespace

from . import (
    cnig_mdsn,
    cuzk_dmp,
    ign_lidarhd,
    lidar_3dep_copc,
    lidar_3dep_ept,
    lidar_3dep_ept_laspy,
    rediam_mdhn,
)
from ._survey import SurveyError, as_nsew

__all__ = ["PROVIDERS", "YEARS", "SurveyError", "available_for_bbox", "ndsm_for_bbox",
           "years_for_bbox"]


def _usgs_ndsm(bbox, resolution_m: float = 1.0):
    """COPC (laspy, per-request HTTP ranges) first; then USGS's own EPT octree read with
    laspy (Planetary Computer's COPC collection has no tiles over central Miami or
    Seattle); the PDAL/EPT path if laspy is missing."""
    if lidar_3dep_copc.available():
        got = lidar_3dep_copc.ndsm_for_bbox(bbox, resolution_m)
        if got is not None:
            return got
        return lidar_3dep_ept_laspy.ndsm_for_bbox(bbox, resolution_m)
    return lidar_3dep_ept.ndsm_for_bbox(bbox, resolution_m)


usgs_3dep = SimpleNamespace(
    name="usgs_3dep",
    label="USGS 3DEP lidar point cloud (USA, ~1 m)",
    resolution_m=1.0,
    covers=lidar_3dep_copc.covers,
    ndsm_for_bbox=_usgs_ndsm,
)

usgs_3dep_ept = SimpleNamespace(
    name="usgs_3dep_ept",
    label="USGS 3DEP lidar, newest EPT project (USA, ~1 m)",
    resolution_m=1.0,
    covers=lidar_3dep_ept_laspy.covers,
    ndsm_for_bbox=lidar_3dep_ept_laspy.ndsm_for_bbox,
)

#: name -> provider (module-like: name, label, resolution_m, covers, ndsm_for_bbox).
#: Order is preference where two overlap (Andalucía: REDIAM 1 m before CNIG 2.5 m).
PROVIDERS = {p.name: p for p in (ign_lidarhd, rediam_mdhn, cnig_mdsn, cuzk_dmp, usgs_3dep,
                                  usgs_3dep_ept)}


#: name -> ``(first, last)`` survey year of the flights behind the provider's surface, or None
#: when it depends on the place (USGS: one project per area; ``years_for_bbox`` looks it up).
#: Used to suspect a survey height of being stale (F-SKY26 2d: an OSM ``start_date`` at or
#: after the survey year). IGN LiDAR HD: flown department by department from 2021.
YEARS: dict[str, tuple[int, int] | None] = {
    "ign_lidarhd": (2021, 2025),
    "rediam_mdhn": (2020, 2021),
    "cnig_mdsn": (2008, 2015),
    "cuzk_dmp": (2009, 2013),
    "usgs_3dep": None,
    "usgs_3dep_ept": None,
}


def years_for_bbox(name: str, bbox) -> tuple[int, int] | None:
    """``(first, last)`` survey year of provider ``name`` over ``bbox``, or None if unknown.

    ``usgs_3dep_ept`` reads the newest EPT project meeting the bbox, so its year is that
    project's (``lidar_3dep_ept_laspy._project_year``: a measured flight year, else the year
    in its name or work-unit suffix; the boundary index is cached for 30 days). ``usgs_3dep`` may
    serve an older COPC copy first, so its year stays unknown.
    """
    if name not in PROVIDERS:
        raise KeyError(f"unknown nDSM provider {name!r}; known: {', '.join(PROVIDERS)}")
    if name == "usgs_3dep_ept":
        projects = [p for p in lidar_3dep_ept_laspy.projects_for_bbox(as_nsew(bbox)) if p["year"]]
        return (projects[0]["year"], projects[0]["year"]) if projects else None
    return YEARS.get(name)


def available_for_bbox(bbox) -> list[dict]:
    """Every provider with whether its extent covers ``bbox`` (cheap: no network)."""
    out = []
    for p in PROVIDERS.values():
        note = ""
        if p is usgs_3dep and not (lidar_3dep_copc.available() or lidar_3dep_ept._deps()):
            note = "needs laspy[lazrs] (or PDAL + py3dep)"
        if p is usgs_3dep_ept and not lidar_3dep_ept_laspy.available():
            note = "needs laspy[lazrs]"
        out.append({"name": p.name, "label": p.label, "resolution_m": p.resolution_m,
                    "available": bool(p.covers(bbox)) and not note, "note": note})
    return out


def ndsm_for_bbox(name: str, bbox, resolution_m: float | None = None):
    """nDSM from provider ``name`` (native resolution by default); None = not covered.

    Raises ``KeyError`` for an unknown provider and :class:`SurveyError` when the
    endpoint fails.
    """
    if name not in PROVIDERS:
        raise KeyError(f"unknown nDSM provider {name!r}; known: {', '.join(PROVIDERS)}")
    p = PROVIDERS[name]
    return p.ndsm_for_bbox(as_nsew(bbox), resolution_m or p.resolution_m)
