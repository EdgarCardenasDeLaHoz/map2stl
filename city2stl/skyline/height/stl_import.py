"""
city2stl/height/stl_import -- Convert a georeferenced STL mesh to a 2-D heightmap.

The STL file format is unit-less and has no embedded coordinate system.
The caller provides the real-world bounding box the mesh should be mapped
onto so the heightmap can be overlaid on terrain and city data.

Public API
----------
stl_to_heightmap(stl_path, bbox, resolution_m=5.0, up_axis="z")
    -> (heightmap: np.ndarray, mask: np.ndarray)

    heightmap : (H, W) float32, mesh-Z values in mesh units (caller decides
                scale).  NaN where no mesh surface was found.
    mask      : (H, W) bool, True where heightmap has valid data.
"""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Tuple, Union

import numpy as np

logger = logging.getLogger(__name__)

# Axis rotation matrices for bringing non-Z "up" axes to Z-up.
# Each matrix R satisfies: R @ [up_axis_unit] ~ [0, 0, 1]
# Convention: Ry(-90) for X-up, Rx(+90) for Y-up, etc.
_UP_AXIS_ROTATIONS = {
    "x":  np.array([[0, 0, -1], [0, 1,  0], [1,  0,  0]], dtype=np.float64),  # Ry(-90 deg)
    "y":  np.array([[1, 0,  0], [0, 0, -1], [0,  1,  0]], dtype=np.float64),  # Rx(+90 deg)
    "z":  np.eye(3, dtype=np.float64),                                          # identity
    "-x": np.array([[0, 0,  1], [0, 1,  0], [-1, 0,  0]], dtype=np.float64),  # Ry(+90 deg)
    "-y": np.array([[1, 0,  0], [0, 0,  1], [0, -1,  0]], dtype=np.float64),  # Rx(-90 deg)
    "-z": np.array([[1, 0,  0], [0, -1, 0], [0,  0, -1]], dtype=np.float64),  # Rx(180 deg)
}


def stl_to_heightmap(
    stl_path: Union[str, Path],
    bbox: dict,
    resolution_m: float = 5.0,
    up_axis: str = "z",
) -> Tuple[np.ndarray, np.ndarray]:
    """Convert a georeferenced STL mesh to a raster heightmap.

    The mesh's bounding box in X/Y is mapped linearly to the geographic
    bounding box.  The mesh Z values (after optional axis rotation) become
    the heightmap pixel values.  No unit conversion is applied -- the caller
    is responsible for knowing the mesh units and applying a scale factor
    before or after this function if needed.

    Parameters
    ----------
    stl_path : str or Path
        Path to an STL (or glTF/OBJ -- any format trimesh can open).
    bbox : dict
        Geographic extent: {"north", "south", "east", "west"} in decimal degrees.
    resolution_m : float
        Target pixel resolution in metres (default 5 m).
    up_axis : str
        Which mesh axis represents "up" (vertical height).
        One of: "x", "y", "z" (default), "-x", "-y", "-z".
        The mesh is rotated so this axis aligns with Z before ray-casting.

    Returns
    -------
    heightmap : (H, W) float32 ndarray
        Height at each grid cell in mesh units.  NaN where no surface.
    mask : (H, W) bool ndarray
        True where heightmap has a valid surface hit.

    Raises
    ------
    ImportError
        If trimesh is not installed.
    ValueError
        If up_axis is not one of the supported values, or the file cannot
        be loaded as a valid mesh.
    """
    try:
        import trimesh
    except ImportError as exc:
        raise ImportError(
            "trimesh is required for STL import. "
            "Install it with: pip install trimesh"
        ) from exc

    up_axis_lc = up_axis.lower().strip()
    if up_axis_lc not in _UP_AXIS_ROTATIONS:
        raise ValueError(
            f"up_axis {up_axis!r} is not supported. "
            f"Use one of: {sorted(_UP_AXIS_ROTATIONS)}"
        )

    # -- Load mesh -----------------------------------------------------------
    stl_path = Path(stl_path)
    logger.info(f"Loading mesh: {stl_path}")
    mesh = trimesh.load(str(stl_path), force="mesh")
    if not isinstance(mesh, trimesh.Trimesh) or len(mesh.faces) == 0:
        raise ValueError(
            f"Could not load a valid mesh from {stl_path}. "
            f"The file may be empty or in an unsupported format."
        )

    # -- Apply up-axis rotation ----------------------------------------------
    R = _UP_AXIS_ROTATIONS[up_axis_lc]
    if not np.allclose(R, np.eye(3)):
        # Transform all vertices
        mesh.vertices = (R @ mesh.vertices.T).T
        logger.debug(f"Rotated mesh to Z-up (from up_axis={up_axis!r})")

    bounds_min = mesh.bounds[0]   # [x_min, y_min, z_min]
    bounds_max = mesh.bounds[1]   # [x_max, y_max, z_max]
    logger.debug(f"Mesh bounds after rotation: {bounds_min} -> {bounds_max}")

    # -- Compute grid dimensions from geographic bbox + resolution -----------
    north = bbox["north"]
    south = bbox["south"]
    east = bbox["east"]
    west = bbox["west"]
    mid_lat = (north + south) / 2.0
    metres_per_deg_lat = 111_320.0
    metres_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))

    lat_m = abs(north - south) * metres_per_deg_lat
    lon_m = abs(east - west) * metres_per_deg_lon

    h = max(1, int(round(lat_m / resolution_m)))
    w = max(1, int(round(lon_m / resolution_m)))
    logger.debug(f"Target grid: {w}x{h} px at {resolution_m} m/px")

    # -- Build ray origins: grid of (x, y) mapped to mesh XY range ----------
    x_min, x_max = bounds_min[0], bounds_max[0]
    y_min, y_max = bounds_min[1], bounds_max[1]
    z_top = bounds_max[2] + 1.0   # ray start slightly above mesh top

    xs = np.linspace(x_min, x_max, w, dtype=np.float64)
    # North -> top of image (row 0 = north), so Y goes from y_max to y_min
    ys = np.linspace(y_max, y_min, h, dtype=np.float64)
    XX, YY = np.meshgrid(xs, ys)

    n_rays = h * w
    ray_origins = np.column_stack([
        XX.ravel(),
        YY.ravel(),
        np.full(n_rays, z_top),
    ])
    ray_dirs = np.tile([0.0, 0.0, -1.0], (n_rays, 1))

    # -- Ray-cast ------------------------------------------------------------
    logger.info(f"Ray-casting {n_rays} rays against mesh "
                f"({len(mesh.faces)} faces)...")
    locations, index_ray, _ = mesh.ray.intersects_location(
        ray_origins=ray_origins,
        ray_directions=ray_dirs,
        multiple_hits=True,
    )

    # -- Accumulate max Z per pixel (topmost surface = roof height) ---------
    # NOTE: np.maximum.at cannot start from a NaN-filled array — np.maximum(nan, x)
    # is always nan, so every hit pixel would stay nan. Start from -inf instead,
    # then convert cells with no hit (still -inf) back to nan.
    heightmap = np.full(n_rays, -np.inf, dtype=np.float32)
    if len(locations) > 0:
        # Vectorised: for each unique ray index, take the maximum z hit
        zvals = locations[:, 2].astype(np.float32)
        np.maximum.at(heightmap, index_ray, zvals)
    heightmap[np.isinf(heightmap)] = np.nan

    heightmap = heightmap.reshape(h, w)
    mask = ~np.isnan(heightmap)

    n_valid = int(mask.sum())
    pct = 100.0 * n_valid / n_rays if n_rays > 0 else 0.0
    logger.info(
        f"Ray-cast complete: {n_valid}/{n_rays} pixels with surface "
        f"({pct:.1f}%)"
    )

    return heightmap, mask
