"""city2stl.registration — register a city STL against OpenStreetMap.

numpy2stl's ``register_city_stl`` is geo-free: it takes a reference source and
never fetches.  This package supplies the OSM side:

* ``OSMReference`` — a ``numpy2stl.registration.ReferenceSource`` over
  ``city2stl.osm_raster`` (building heightmap, vegetation / water / bridge masks,
  city scale anchor and centre-search candidates) and the 3DEP EPT nDSM
  (``city2stl.height.providers.lidar_3dep_ept``).
* ``register_city_stl(stl_file, city_name, ...)`` — the city-name / bbox entry point
  (same arguments as before the split), building an ``OSMReference`` and calling
  numpy2stl.

CLIs: ``python -m city2stl.registration.scripts.run_registration`` (one city),
``...benchmark_micropolitan`` and ``...robustness_test``.

Plate placement and scoring, promoted from ``tools/align_tool`` on 2026-09-27 so the
web app can import them (the tool scripts now import from here):

* ``street_place`` — place a plate pack by its streets (with ``osm_model`` and
  ``osm_water`` for the map side, ``align_paths`` for where packs and caches live);
* ``consensus`` — the tile-consensus verdict (pass / review / fail);
* ``correlate`` — shared NCC / peak / height-channel primitives;
* ``critic`` — score a generated model against a plate or lidar nDSM
  (per-building median absolute error, footprint IoU, roof summary).

Import those submodules directly; this package ``__init__`` does not pull them in.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
from numpy2stl.registration import register_city_stl as _register
from numpy2stl.registration.types import CityRegistrationReport

from city2stl import osm_raster

logger = logging.getLogger(__name__)

__all__ = ["OSMReference", "register_city_stl"]


class OSMReference:
    """OSM reference source for ``numpy2stl.registration.register_city_stl``.

    ``city`` is a city name (e.g. "Philadelphia, PA, USA") or an (N, S, E, W)
    bbox.  For a name, the STL scale anchor (``scale_m_per_unit``, else
    ``tallest_m`` / STL height, else the built-in city table) sizes a tight bbox
    of ``margin`` x the STL footprint around ``center`` (else the city table, else
    the geocoded centroid); without an anchor the whole city is fetched.  A bbox
    is fetched as given.
    """

    def __init__(self, city: str | tuple, *, default_height: float = 10.0,
                 levels_to_meters: float = 3.5, center: tuple[float, float] | None = None,
                 tallest_m: float | None = None, scale_m_per_unit: float | None = None,
                 search_radius_km: float = 4.0, search_step_km: float = 1.0):
        self.city = city if isinstance(city, str) else tuple(float(v) for v in city)
        self.default_height = default_height
        self.levels_to_meters = levels_to_meters
        self.center = center
        self.tallest_m = tallest_m
        self.scale_m_per_unit = scale_m_per_unit
        self.search_radius_km = search_radius_km
        self.search_step_km = search_step_km
        self.name = self.city if isinstance(self.city, str) else f"bbox {self.city}"

    def _tight_bbox(self, stl_z_max, stl_xy_extent, margin, center=None):
        return osm_raster.estimate_bbox_from_stl(
            self.city, stl_z_max, stl_xy_extent, osm_margin=margin,
            center=center if center is not None else self.center,
            tallest_m=self.tallest_m, scale_m_per_unit=self.scale_m_per_unit)

    def resolve_target(self, stl_z_max: float, stl_xy_extent: float,
                       margin: float) -> tuple[Any, bool]:
        if isinstance(self.city, str):
            tight = self._tight_bbox(stl_z_max, stl_xy_extent, margin)
            if tight is not None:
                return tight, True
        return self.city, False

    def building_heightmap(self, target: Any, resolution: int) -> dict:
        return osm_raster.get_osm_building_heightmap(
            target, resolution=resolution, default_height=self.default_height,
            levels_to_meters=self.levels_to_meters)

    def semantic_masks(self, target: Any, resolution: int) -> dict:
        return osm_raster.get_osm_semantic_masks(target, resolution=resolution)

    def ndsm(self, target: Any, resolution: int) -> np.ndarray | None:
        from city2stl.height.providers.lidar_3dep_ept import get_ndsm
        return get_ndsm(osm_raster.resolve_bbox(target), resolution=resolution)

    def m_per_unit(self, stl_z_max: float) -> float | None:
        return osm_raster.derive_scale_m_per_unit(
            self.city, stl_z_max, tallest_m=self.tallest_m,
            scale_m_per_unit=self.scale_m_per_unit)

    def candidate_targets(self, stl_z_max: float, stl_xy_extent: float,
                          margin: float) -> list[tuple[tuple[float, float], tuple]]:
        """Tight bboxes around the initial centre plus rings of 8 compass offsets.

        Rings at ``search_step_km`` .. ``search_radius_km`` (33 candidates at the
        defaults).  km offsets use the initial centre's latitude for the longitude
        cosine (1 deg ~ 111 km).  Returns [((lat, lon), bbox), ...], initial first;
        empty when no centre can be geocoded or no scale anchor exists.
        """
        if not isinstance(self.city, str):
            return []
        initial = self.center or osm_raster.get_city_center_point(self.city)
        if initial is None:
            logger.warning("candidate_targets(%r): no centre and geocoding failed.", self.city)
            return []
        base_lat, base_lon = float(initial[0]), float(initial[1])
        m_per_deg_lat = 111_000.0
        m_per_deg_lon = 111_000.0 * math.cos(math.radians(base_lat))
        centres = [(base_lat, base_lon)]
        for r_km in np.arange(self.search_step_km, self.search_radius_km + 1e-9,
                              self.search_step_km):
            r_m = float(r_km) * 1000.0
            for k in range(8):
                theta = 2.0 * math.pi * k / 8
                centres.append((base_lat + r_m * math.cos(theta) / m_per_deg_lat,
                                base_lon + r_m * math.sin(theta) / m_per_deg_lon))
        out = []
        for c in centres:
            bbox = self._tight_bbox(stl_z_max, stl_xy_extent, margin, center=c)
            if bbox is not None:
                out.append((c, bbox))
        return out


def register_city_stl(
    stl_file: str,
    city_name: str | tuple,
    resolution: int = 1024,
    default_height: float = 10.0,
    levels_to_meters: float = 3.5,
    *,
    center: tuple[float, float] | None = None,
    tallest_m: float | None = None,
    scale_m_per_unit: float | None = None,
    **kwargs,
) -> CityRegistrationReport:
    """Register ``stl_file`` against OSM buildings for ``city_name`` (name or bbox).

    OSM arguments build the ``OSMReference``; every other keyword
    (``out_dir``, ``simplify_mode``, ``height_source``, ``center_search``,
    ``free_scale``, ...) goes to ``numpy2stl.registration.register_city_stl``.
    """
    ref = OSMReference(city_name, default_height=default_height,
                       levels_to_meters=levels_to_meters, center=center,
                       tallest_m=tallest_m, scale_m_per_unit=scale_m_per_unit)
    return _register(stl_file, ref, resolution=resolution, **kwargs)
