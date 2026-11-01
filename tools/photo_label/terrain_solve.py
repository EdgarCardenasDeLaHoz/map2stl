"""Compatibility shim: the photo-label camera fit on the terrain now lives in the package.

- `solve_terrain`, `label_priors`, `is_named` and the fit constants:
  `city2stl.skyline.camera_solver` (beside the column-only `solve_pose` that seeds it).
- `project_buildings`, `building_height` and the visibility-raster constants:
  `city2stl.skyline.photo_view` (beside `PinholeCamera`).

Moved unchanged in F-UNIFY Phase 3 so the app and the tool share one camera-solve API;
this module only keeps `import terrain_solve as ts` working for the tool and its tests.
"""
from __future__ import annotations

from city2stl.skyline import photo_view as pv  # noqa: F401
from city2stl.skyline.camera_solver import (  # noqa: F401
    MAX_H,
    PRIOR_SIGMA_M,
    PRIOR_WEIGHT,
    ROLL_GAIN,
    ROLL_MIN_LABELS,
    _cam,
    is_named,
    label_priors,
    solve_terrain,
)
from city2stl.skyline.camera_solver import _solve_terrain_once as _solve  # noqa: F401
from city2stl.skyline.photo_view import (  # noqa: F401
    _CLAMP,
    _NUM,
    DEFAULT_BUILDING_H,
    LEVEL_H,
    M_PER_DEG_LAT,
    MAX_VISIBLE,
    MIN_DIST_M,
    MIN_VISIBLE_PX,
    NEAR_M,
    PROJECT_RADIUS_M,
    _clip_near,
    _num,
    _prism_faces,
    building_height,
    project_buildings,
)
