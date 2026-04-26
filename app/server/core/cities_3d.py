"""
core/cities_3d.py -- shim: re-exports all symbols from city2stl.mesh.

All implementation has moved to city2stl/mesh.py (pure library, no server deps).
This file exists only so that existing router imports continue to work unchanged:
    from app.server.core.cities_3d import generate_city_3mf
"""

from city2stl.mesh import (  # noqa: F401
    _cross2,
    _point_in_triangle,
    _ear_clip,
    _extrude_ring,
    _build_building_meshes,
    _terrain_mesh,
    generate_city_3mf,
)

import logging  # noqa: F401  (kept so patch.object(cities_3d, 'logger', ...) still works)