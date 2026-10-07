"""Satellite building heights (F-SKY26 step 7): shadow, lean, multi-date shadows and stereo on
free Esri World Imagery / Wayback scenes.

Submodules (import them directly):
  - scene     Scene / TileSource, Esri Wayback identify and tile fetch, sun position, lean and
              sun solves (``fit_lean``, ``solve_sun``)
  - measure   per-footprint shadow and lean (one scene), multi-scene shadows and plane sweep,
              pair-consensus stereo
  - readings  SatReading per footprint and method (shadow scenes merged to one reading),
              readings.json I/O
  - weights   sigma_log / weight_scale decision table (2026-10-07)

Offline producer: ``city2stl/skyline/scripts/20_satellite_heights.py`` writes
``runs/satellite/<region>/readings.json``; the region run only reads it
(``city2stl/skyline/satellite_fusion.py``, site flag ``use_satellite_heights``).
Not ``height/providers/shadow_height.py``, deprecated since 2026-08-28.
"""

from __future__ import annotations


def footprints_from_records(records, z: int = 18) -> list[dict]:
    """Measurement footprints from skyline ``BuildingRecord`` s (or any objects with
    ``feature_id``, ``geometry``, ``height_tag_m``, ``height_source``, ``centroid_lat/lon``), in
    their order: ``fid``, ``P`` (global z18 pixel exterior rings), ``bb``, ``tag`` (OSM tag or
    levels height, else None), ``src``, ``lat``, ``lon``."""
    import numpy as np  # noqa: PLC0415

    from geo2stl.imagery import lat_to_global_px, lon_to_global_px  # noqa: PLC0415

    out = []
    for r in records:
        g = r.geometry
        if g is None or g.is_empty or g.geom_type not in ("Polygon", "MultiPolygon"):
            continue
        gs = [g] if g.geom_type == "Polygon" else list(g.geoms)
        P = [np.array([[lon_to_global_px(x, z), lat_to_global_px(y, z)]
                       for x, y in np.asarray(q.exterior.coords)[:, :2]]) for q in gs]
        A = np.vstack(P)
        tagged = r.height_source in ("osm_tag", "osm_levels") and r.height_tag_m
        out.append(dict(fid=r.feature_id, P=P, bb=(A[:, 0].min(), A[:, 1].min(), A[:, 0].max(), A[:, 1].max()),
                        tag=float(r.height_tag_m) if tagged else None, src=r.height_source,
                        lat=float(r.centroid_lat), lon=float(r.centroid_lon)))
    return out
