"""skyline._core.projection — extracted from pipeline.py (A1 split)."""
from __future__ import annotations
from collections import OrderedDict as _OrderedDict

import logging
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

from .types import Viewpoint, BuildingRecord

def _lonlat_to_local_m(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    meters_per_deg_lat = 110_540.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    dx = (lon - lon0) * meters_per_deg_lon
    dy = (lat - lat0) * meters_per_deg_lat
    return dx, dy

lonlat_to_local_m = _lonlat_to_local_m

def _focal_length_px(viewpoint: Viewpoint) -> float:
    return 0.5 * viewpoint.image_width / math.tan(math.radians(viewpoint.fov) * 0.5)

def _camera_frame(dx: float, dy: float, heading_deg: float) -> tuple[float, float]:
    theta = math.radians(heading_deg)
    forward = dy * math.cos(theta) + dx * math.sin(theta)
    lateral = dx * math.cos(theta) - dy * math.sin(theta)
    return forward, lateral

def _building_vertices_lonlat(building: BuildingRecord) -> list[tuple[float, float]]:
    """Return the polygon's outer ring as (lon, lat) tuples, or empty if missing."""
    geom = getattr(building, "geometry", None)
    if geom is None:
        return []
    try:
        coords = list(geom.exterior.coords)
        return [(float(p[0]), float(p[1])) for p in coords]
    except Exception:
        return []

def _project_building(
    building: BuildingRecord,
    viewpoint: Viewpoint,
    heading_offset_deg: float,
    max_distance_m: float = 4000.0,
    min_forward_m: float = 5.0,
    fov_margin_deg: float = 1.0,
) -> dict | None:
    """Project a building polygon onto the camera image plane.

    Uses the near edge of the footprint (closest vertex along the camera
    bearing) for `forward_m` so distant tall buildings don't get inflated by
    the much-farther centroid distance. Pinhole (tan) projection — Street View
    Static API returns rectilinear images at the requested FOV.
    """
    heading_total_deg = viewpoint.heading + heading_offset_deg

    # Centroid frame for in-FOV gating
    cdx, cdy = _lonlat_to_local_m(
        building.centroid_lon, building.centroid_lat, viewpoint.lon, viewpoint.lat
    )
    c_forward, c_lateral = _camera_frame(cdx, cdy, heading_total_deg)
    if c_forward < min_forward_m:
        return None
    centroid_distance = math.hypot(c_forward, c_lateral)
    if centroid_distance > max_distance_m:
        return None

    half_fov_rad = math.radians(viewpoint.fov * 0.5 + fov_margin_deg)
    centroid_bearing_rad = math.atan2(c_lateral, c_forward)
    if abs(centroid_bearing_rad) > half_fov_rad:
        return None

    # Project every polygon vertex into camera frame. We need:
    #   - near_forward: distance from camera to the closest vertex (used for
    #     occlusion ranking and the per-building geometric height equation).
    #   - lateral midpoint: the visible silhouette of a building extends from
    #     its leftmost in-frustum vertex to its rightmost in-frustum vertex.
    #     The x_px should mark the CENTER of that silhouette, not the corner
    #     closest to the camera — otherwise badges land off-center on wide
    #     facades and the matcher gets a biased anchor.
    verts = _building_vertices_lonlat(building)
    near_forward = c_forward
    lateral_min = c_lateral
    lateral_max = c_lateral
    forward_at_min = c_forward
    forward_at_max = c_forward
    if verts:
        best_dist = float("inf")
        lat_lo = float("inf")
        lat_hi = float("-inf")
        for vlon, vlat in verts:
            vdx, vdy = _lonlat_to_local_m(
                vlon, vlat, viewpoint.lon, viewpoint.lat)
            v_forward, v_lateral = _camera_frame(vdx, vdy, heading_total_deg)
            if v_forward < min_forward_m:
                continue
            d = math.hypot(v_forward, v_lateral)
            if d < best_dist:
                best_dist = d
                near_forward = v_forward
            if v_lateral < lat_lo:
                lat_lo = v_lateral
                forward_at_min = v_forward
            if v_lateral > lat_hi:
                lat_hi = v_lateral
                forward_at_max = v_forward
        if lat_lo != float("inf") and lat_hi != float("-inf"):
            lateral_min = lat_lo
            lateral_max = lat_hi

    # Bearing to the lateral midpoint of the facing edge.
    mid_lateral = 0.5 * (lateral_min + lateral_max)
    mid_forward = 0.5 * (forward_at_min + forward_at_max)
    if mid_forward < min_forward_m:
        mid_forward = near_forward
    bearing_rad = math.atan2(mid_lateral, max(mid_forward, min_forward_m))
    if abs(bearing_rad) > half_fov_rad:
        return None

    # Pinhole projection: x_px = cx + f * tan(bearing). Street View Static API
    # at a given FOV returns a rectilinear image, so tan is the physically
    # correct projection (atan would be equirectangular).
    f_px = _focal_length_px(viewpoint)
    cx = viewpoint.image_width * 0.5
    x_px = cx + f_px * math.tan(bearing_rad)
    if x_px < 0.0 or x_px > viewpoint.image_width - 1.0:
        return None

    # Project the building's full footprint extent (x_left_px..x_right_px) for
    # the matcher and overlay. Use the extreme-lateral vertices found above.
    bearing_left = math.atan2(lateral_min, max(forward_at_min, min_forward_m))
    bearing_right = math.atan2(lateral_max, max(forward_at_max, min_forward_m))
    x_left_px = cx + f_px * math.tan(bearing_left)
    x_right_px = cx + f_px * math.tan(bearing_right)
    if x_left_px > x_right_px:
        x_left_px, x_right_px = x_right_px, x_left_px
    x_left_px = max(0.0, x_left_px)
    x_right_px = min(viewpoint.image_width - 1.0, x_right_px)

    return {
        "feature_id": building.feature_id,
        "name": building.name,
        "x_px": float(x_px),
        "x_left_px": float(x_left_px),
        "x_right_px": float(x_right_px),
        "forward_m": near_forward,
        "lateral_m": float(mid_lateral),
        "distance_m": math.hypot(near_forward, mid_lateral),
        "centroid_forward_m": c_forward,
    }

def _building_projected_x_range(
    building: BuildingRecord,
    viewpoint: Viewpoint,
    offset_deg: float,
    image_width: int,
) -> tuple[int, int] | None:
    """Project a building's polygon vertices and return (x_left, x_right) of
    its image-plane footprint. None if the building doesn't appear in frame.
    """
    verts = _building_vertices_lonlat(building)
    if not verts:
        return None
    heading_total = viewpoint.heading + offset_deg
    cos_t = math.cos(math.radians(heading_total))
    sin_t = math.sin(math.radians(heading_total))
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(viewpoint.lat))
    f_px = _focal_length_px(viewpoint)
    cx = viewpoint.image_width * 0.5
    half_fov_rad = math.radians(viewpoint.fov * 0.5)

    xs: list[int] = []
    for vlon, vlat in verts:
        dx = (vlon - viewpoint.lon) * mlon
        dy = (vlat - viewpoint.lat) * mlat
        forward = dy * cos_t + dx * sin_t
        if forward <= 1.0:
            continue
        lateral = dx * cos_t - dy * sin_t
        bearing_rad = math.atan2(lateral, forward)
        if abs(bearing_rad) > half_fov_rad:
            continue
        x = cx + f_px * math.tan(bearing_rad)
        xs.append(int(round(x)))
    if not xs:
        return None
    x_left = max(0, min(xs))
    x_right = min(image_width - 1, max(xs))
    if x_right < x_left:
        return None
    return x_left, x_right

def _building_vertex_arrays(
    buildings: Sequence[BuildingRecord],
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Pack all building polygon vertices into flat (lon, lat, building_idx)
    arrays for vectorised projection. Returns None if no building has vertices.

    The packed form lets us project ALL vertices of ALL buildings in one
    numpy call instead of N per-building Python loops — turning the per-offset
    cost from O(B × V) Python ops into one O(B·V) numpy op.
    """
    lons: list[float] = []
    lats: list[float] = []
    bidx: list[int] = []
    for i, b in enumerate(buildings):
        verts = _building_vertices_lonlat(b)
        if not verts:
            continue
        for vlon, vlat in verts:
            lons.append(vlon)
            lats.append(vlat)
            bidx.append(i)
    if not lons:
        return None
    return (
        np.asarray(lons, dtype=np.float64),
        np.asarray(lats, dtype=np.float64),
        np.asarray(bidx, dtype=np.int32),
    )

_VERT_CACHE: dict[tuple[int, int], tuple[np.ndarray, np.ndarray, np.ndarray]] = {}

def _get_vertex_arrays_cached(
    buildings: Sequence[BuildingRecord],
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    key = (id(buildings), len(buildings))
    cached = _VERT_CACHE.get(key)
    if cached is not None:
        return cached
    packed = _building_vertex_arrays(buildings)
    if packed is None:
        return None
    _VERT_CACHE[key] = packed
    # Keep cache small to avoid leaks across calls.
    if len(_VERT_CACHE) > 4:
        # Drop the oldest entry (insertion order is preserved in py3.7+).
        first_key = next(iter(_VERT_CACHE))
        if first_key != key:
            _VERT_CACHE.pop(first_key, None)
    return packed

_GROUP_CACHE: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}

def _get_group_arrays_cached(
    buildings: Sequence[BuildingRecord],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (group_starts, building_idx_for_group) for the packed vertex
    arrays. Cached per (id, len) of the buildings list — see _VERT_CACHE
    docstring above for why both components are needed.
    """
    key = (id(buildings), len(buildings))
    cached = _GROUP_CACHE.get(key)
    if cached is not None:
        return cached
    packed = _get_vertex_arrays_cached(buildings)
    if packed is None:
        return None
    _, _, bidx = packed
    if bidx.size == 0:
        return None
    group_starts = np.concatenate(
        [[0], np.where(np.diff(bidx) != 0)[0] + 1]).astype(np.int64)
    building_idx_for_group = bidx[group_starts]
    _GROUP_CACHE[key] = (group_starts, building_idx_for_group)
    if len(_GROUP_CACHE) > 4:
        first_key = next(iter(_GROUP_CACHE))
        if first_key != key:
            _GROUP_CACHE.pop(first_key, None)
    return _GROUP_CACHE[key]

def _project_all_buildings_vectorized(
    buildings: Sequence[BuildingRecord],
    viewpoint: Viewpoint,
    offset_deg: float,
    max_distance_m: float = 4000.0,
    min_forward_m: float = 5.0,
    fov_margin_deg: float = 1.0,
) -> list[dict]:
    """Vectorised projection of ALL buildings at a single offset.

    Numpy-batch equivalent of looping ``_project_building`` over the building
    list — same output shape (list of projection dicts) but uses pre-cached
    packed vertex arrays and per-building group reductions. This is the inner
    loop of ``register_view_to_osm._score_offset`` and previously dominated
    runtime (629 s of 1033 s total in profiling).

    Buildings without geometry are silently skipped; the per-building
    ``_project_building`` path is retained for synthetic-geometry tests.
    """
    packed = _get_vertex_arrays_cached(buildings)
    groups = _get_group_arrays_cached(buildings)
    if packed is None or groups is None:
        return []
    lons, lats, bidx = packed
    group_starts, building_idx_for_group = groups

    heading_total = viewpoint.heading + offset_deg
    cos_t = math.cos(math.radians(heading_total))
    sin_t = math.sin(math.radians(heading_total))
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(viewpoint.lat))
    f_px = _focal_length_px(viewpoint)
    cx = viewpoint.image_width * 0.5
    half_fov_rad = math.radians(viewpoint.fov * 0.5 + fov_margin_deg)
    image_width = viewpoint.image_width

    dx = (lons - viewpoint.lon) * mlon
    dy = (lats - viewpoint.lat) * mlat
    forward = dy * cos_t + dx * sin_t
    lateral = dx * cos_t - dy * sin_t
    bearing = np.arctan2(lateral, np.maximum(forward, 1e-3))

    # In-frustum gating per vertex; out-of-frustum vertices are excluded from
    # the per-building reductions by being set to ±inf sentinels.
    distance = np.hypot(forward, lateral)
    valid = (forward > min_forward_m) & (distance <= max_distance_m)
    forward_for_min = np.where(valid, forward, np.inf)
    bearing_for_min = np.where(valid, bearing, np.inf)
    bearing_for_max = np.where(valid, bearing, -np.inf)

    near_forward = np.minimum.reduceat(forward_for_min, group_starts)
    bearing_min = np.minimum.reduceat(bearing_for_min, group_starts)
    bearing_max = np.maximum.reduceat(bearing_for_max, group_starts)

    in_view = (
        np.isfinite(near_forward)
        & (bearing_min < half_fov_rad)
        & (bearing_max > -half_fov_rad)
    )
    keep_indices = np.where(in_view)[0]
    if keep_indices.size == 0:
        return []

    # Subset to in-view buildings BEFORE the tan/clip math: out-of-frustum
    # buildings have bearing_min=+inf / bearing_max=-inf, which produce NaN
    # in (min+max) and then RuntimeWarning in tan. Operate only on valid rows.
    bmin_v = np.maximum(bearing_min[keep_indices], -half_fov_rad)
    bmax_v = np.minimum(bearing_max[keep_indices], half_fov_rad)
    bmid_v = 0.5 * (bmin_v + bmax_v)
    nfwd_v = near_forward[keep_indices]

    x_left_px = np.clip(cx + f_px * np.tan(bmin_v), 0.0, image_width - 1.0)
    x_right_px = np.clip(cx + f_px * np.tan(bmax_v), 0.0, image_width - 1.0)
    x_px = np.clip(cx + f_px * np.tan(bmid_v), 0.0, image_width - 1.0)
    lateral_m_v = f_px * np.tan(bmid_v)

    out: list[dict] = []
    for j, i in enumerate(keep_indices):
        bi = int(building_idx_for_group[i])
        b = buildings[bi]
        nf = float(nfwd_v[j])
        out.append({
            "feature_id": b.feature_id,
            "name": b.name,
            "x_px": float(x_px[j]),
            "x_left_px": float(x_left_px[j]),
            "x_right_px": float(x_right_px[j]),
            "forward_m": nf,
            "lateral_m": float(lateral_m_v[j]),
            "distance_m": nf,
            "centroid_forward_m": nf,
        })
    return out

def _projected_building_x_ranges(
    buildings: Sequence[BuildingRecord],
    viewpoint: Viewpoint,
    offset_deg: float,
    image_width: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Return per-building (x_min, x_max) arrays for in-frustum buildings only.

    The output preserves identity (one row per visible building), which lets
    callers compute per-building objectives instead of a flat column union.
    Buildings with no in-frustum vertices are omitted from both arrays.
    """
    packed = _get_vertex_arrays_cached(buildings)
    empty = (np.empty(0, dtype=np.int32), np.empty(0, dtype=np.int32))
    if packed is None:
        return empty
    lons, lats, bidx = packed

    heading_total = viewpoint.heading + offset_deg
    f_px = _focal_length_px(viewpoint)
    cx = viewpoint.image_width * 0.5
    half_fov_rad = math.radians(viewpoint.fov * 0.5)
    cos_t = math.cos(math.radians(heading_total))
    sin_t = math.sin(math.radians(heading_total))
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(viewpoint.lat))

    dx = (lons - viewpoint.lon) * mlon
    dy = (lats - viewpoint.lat) * mlat
    forward = dy * cos_t + dx * sin_t
    lateral = dx * cos_t - dy * sin_t
    bearing = np.arctan2(lateral, np.maximum(forward, 1e-3))
    distance = np.hypot(forward, lateral)
    in_frustum = (forward > 5.0) & (distance <= 4000.0) & (
        np.abs(bearing) <= half_fov_rad)
    if not np.any(in_frustum):
        return empty

    xs_all = cx + f_px * np.tan(bearing)
    xs_all = np.clip(xs_all, 0.0, image_width - 1.0).astype(np.int32)

    valid_b = bidx[in_frustum]
    valid_x = xs_all[in_frustum]
    n_buildings = int(bidx.max()) + 1
    x_min = np.full(n_buildings, image_width, dtype=np.int32)
    x_max = np.full(n_buildings, -1, dtype=np.int32)
    np.minimum.at(x_min, valid_b, valid_x)
    np.maximum.at(x_max, valid_b, valid_x)
    has_proj = x_max >= 0
    return x_min[has_proj], x_max[has_proj]

def _projected_building_column_mask(
    buildings: Sequence[BuildingRecord],
    viewpoint: Viewpoint,
    offset_deg: float,
    image_width: int,
) -> np.ndarray:
    """For a candidate heading offset, return a (W,) boolean array where True
    means "at least one OSM building footprint projects into this column."
    """
    predicted = np.zeros(image_width, dtype=bool)
    x_min, x_max = _projected_building_x_ranges(
        buildings, viewpoint, offset_deg, image_width)
    for xL, xR in zip(x_min.tolist(), x_max.tolist()):
        predicted[xL: xR + 1] = True
    return predicted


__all__ = [
    '_lonlat_to_local_m',
    'lonlat_to_local_m',
    '_focal_length_px',
    '_camera_frame',
    '_building_vertices_lonlat',
    '_project_building',
    '_building_projected_x_range',
    '_building_vertex_arrays',
    '_get_vertex_arrays_cached',
    '_get_group_arrays_cached',
    '_project_all_buildings_vectorized',
    '_projected_building_x_ranges',
    '_projected_building_column_mask',
]
