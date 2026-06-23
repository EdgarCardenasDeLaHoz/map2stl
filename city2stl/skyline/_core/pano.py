"""skyline._core.pano — extracted from pipeline.py (A1 split)."""
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

from .types import BuildingRecord
from .projection import _building_vertices_lonlat

def stitch_pano_mask_channel(
    views: list[dict],
    fov_deg: float,
    step_deg: float,
    mask_key: str,
) -> "np.ndarray | None":
    """Stitch one boolean per-view mask channel into a 360° strip (F-SKY18).

    Generalises the building/water cropping in ``stitch_pano_masks`` to any
    mask key (e.g. ``"vegetation_mask"``) so additional landmark classes can
    be projected with the SAME pinhole geometry / column→heading mapping. The
    output aligns column-for-column with ``stitch_pano_masks`` /
    ``stitch_pano_views`` so ``headings_per_col`` applies unchanged.
    """
    if not views:
        return None
    sorted_views = sorted(views, key=lambda v: float(v["geo_heading"]))
    ref = next((v for v in sorted_views if v.get("building_mask") is not None), None)
    if ref is None:
        return None
    H_view, W_view = ref["building_mask"].shape[:2]
    f_px = 0.5 * W_view / math.tan(math.radians(fov_deg) * 0.5)
    crop_half_px = int(round(f_px * math.tan(math.radians(step_deg * 0.5))))
    crop_x0 = max(0, W_view // 2 - crop_half_px)
    crop_x1 = min(W_view, W_view // 2 + crop_half_px)
    if crop_x1 <= crop_x0:
        return None
    crops: list[np.ndarray] = []
    for v in sorted_views:
        bmask = v.get("building_mask")
        if bmask is None or bmask.shape[0] != H_view:
            continue
        m = v.get(mask_key)
        if m is not None and m.shape[:2] == bmask.shape[:2]:
            crops.append(np.asarray(m[:, crop_x0:crop_x1], dtype=bool))
        else:
            crops.append(np.zeros((H_view, crop_x1 - crop_x0), dtype=bool))
    if not crops:
        return None
    return np.concatenate(crops, axis=1)

def stitch_pano_masks(
    views: list[dict],
    fov_deg: float,
    step_deg: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Stitch the SegFormer building/water masks of a spin's views into one
    360° strip — same geometry as ``stitch_pano_views`` but operating on the
    already-cached per-view masks.

    Why this exists: running SegFormer with a sliding window on a stitched
    RGB strip introduced seam artefacts (each window saw a non-square aspect
    its training distribution didn't match) and re-paid 5+ inferences per
    seed. The model runs natively on per-view 640×640-ish images during
    Pass 2, so we already have high-quality masks; stitching them with the
    same pinhole geometry as the RGB stitcher is strictly better quality
    AND faster.

    Each input view dict must have keys:
        "image": HxWx3 (used only for shape inference)
        "geo_heading": float
        "building_mask": HxW boolean
        "water_mask": HxW boolean (may be None)

    Returns (building_mask_stitched, water_mask_stitched) where each is a
    boolean (H, W_stitched) array. The same headings_per_col coordinate that
    comes from stitch_pano_views applies here, so callers should call both
    in parallel.
    """
    if not views:
        return None
    sorted_views = sorted(views, key=lambda v: float(v["geo_heading"]))
    first = sorted_views[0]
    if first.get("building_mask") is None:
        return None
    H_view, W_view = first["building_mask"].shape[:2]
    f_px = 0.5 * W_view / math.tan(math.radians(fov_deg) * 0.5)
    half_step_rad = math.radians(step_deg * 0.5)
    crop_half_px = int(round(f_px * math.tan(half_step_rad)))
    crop_x0 = max(0, W_view // 2 - crop_half_px)
    crop_x1 = min(W_view, W_view // 2 + crop_half_px)
    if crop_x1 <= crop_x0:
        return None

    b_crops: list[np.ndarray] = []
    w_crops: list[np.ndarray] = []
    for v in sorted_views:
        bmask = v.get("building_mask")
        if bmask is None or bmask.shape[0] != H_view:
            continue
        b_crops.append(np.asarray(bmask[:, crop_x0:crop_x1], dtype=bool))
        wmask = v.get("water_mask")
        if wmask is not None and wmask.shape[:2] == bmask.shape[:2]:
            w_crops.append(np.asarray(wmask[:, crop_x0:crop_x1], dtype=bool))
        else:
            # Substitute False if water mask missing — water penalty becomes a no-op.
            w_crops.append(np.zeros((H_view, crop_x1 - crop_x0), dtype=bool))
    if not b_crops:
        return None
    return (
        np.concatenate(b_crops, axis=1),
        np.concatenate(w_crops, axis=1),
    )

def project_buildings_to_pano(
    buildings: Sequence[BuildingRecord],
    seed_lat: float,
    seed_lon: float,
    headings_per_col: np.ndarray,
    max_distance_m: float = 4000.0,
    min_forward_m: float = 5.0,
) -> list[dict]:
    """Project OSM buildings into stitched-pano column space.

    For each in-range building, compute its (forward, lateral) in a frame
    where geographic NORTH is forward; convert vertex extremes to
    geographic bearings; then look up which stitched column matches each
    bearing via ``headings_per_col`` (which gives the heading of every
    column in the pano).

    Returns the standard projection-dict shape used by the matcher:
        feature_id, name, x_px, x_left_px, x_right_px, forward_m,
        lateral_m, distance_m, centroid_forward_m
    """
    if headings_per_col.size == 0:
        return []
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    image_width = int(headings_per_col.size)

    def _bearing_to_col(bearing_deg: float) -> int | None:
        """Find the column whose recorded heading is closest to bearing_deg.
        Returns None if the bearing is more than half a step from any
        column (i.e. not actually represented in the pano)."""
        diffs = ((headings_per_col - bearing_deg + 180.0) % 360.0) - 180.0
        idx = int(np.argmin(np.abs(diffs)))
        if abs(float(diffs[idx])) > 5.0:  # outside the represented angles
            return None
        return idx

    out: list[dict] = []
    for b in buildings:
        verts = _building_vertices_lonlat(b)
        if not verts:
            continue
        # Compute each vertex's forward (north), lateral (east) in metres.
        # bearing = atan2(east, north), giving 0° at north and increasing
        # clockwise — same convention as headings_per_col.
        bearings: list[float] = []
        forwards: list[float] = []
        for vlon, vlat in verts:
            east = (vlon - seed_lon) * mlon
            north = (vlat - seed_lat) * mlat
            distance = math.hypot(east, north)
            if distance < min_forward_m or distance > max_distance_m:
                continue
            bearing = math.degrees(math.atan2(east, north)) % 360.0
            bearings.append(bearing)
            forwards.append(distance)
        if not bearings:
            continue
        # Find the bearing extremes. Handle the 0/360 wrap by working in the
        # ±180° range around the median bearing.
        med = bearings[len(bearings) // 2]
        rel = [((br - med + 540.0) % 360.0) - 180.0 for br in bearings]
        rel_min, rel_max = min(rel), max(rel)
        if rel_max - rel_min > 180.0:
            # Building straddles the wraparound from this seed — skip.
            continue
        bearing_min = (med + rel_min) % 360.0
        bearing_max = (med + rel_max) % 360.0
        bearing_mid = (med + (rel_min + rel_max) * 0.5) % 360.0

        col_left = _bearing_to_col(bearing_min)
        col_right = _bearing_to_col(bearing_max)
        col_mid = _bearing_to_col(bearing_mid)
        if col_left is None or col_right is None or col_mid is None:
            continue
        if col_left > col_right:
            # Building straddles a pano seam (unlikely with 30° step).
            col_left, col_right = col_right, col_left
        near_forward = min(forwards)
        out.append({
            "feature_id": b.feature_id,
            "name": b.name,
            "x_px": float(col_mid),
            "x_left_px": float(col_left),
            "x_right_px": float(col_right),
            "forward_m": float(near_forward),
            "lateral_m": 0.0,
            "distance_m": float(near_forward),
            "centroid_forward_m": float(near_forward),
        })
    return out

def stitch_pano_views(
    views: list[dict],
    fov_deg: float,
    step_deg: float,
) -> tuple[np.ndarray, np.ndarray] | None:
    """Stitch a list of FOV-rectilinear spin views into one 360° strip.

    Each view contributes its central ``step_deg`` portion (avoiding overlap
    at the rectilinear seams), concatenated left-to-right in heading order.

    Parameters
    ----------
    views : list of dicts each with keys "image" (np.ndarray HxWx3) and
            "geo_heading" (float, degrees) — the geographic heading the
            view's centre points at.
    fov_deg : per-view FOV (e.g. 75).
    step_deg : angular step between adjacent views (e.g. 30 for 12 views).

    Returns
    -------
    (stitched_image, headings_per_col) or None if input is empty.
        stitched_image: HxW_stitched x 3 RGB strip.
        headings_per_col: (W_stitched,) float array — the geographic heading
            corresponding to each column of the strip. Used to project OSM
            buildings onto stitched-x via bearing lookup.
    """
    if not views:
        return None
    sorted_views = sorted(views, key=lambda v: float(v["geo_heading"]))
    first_img = sorted_views[0]["image"]
    H_view, W_view = first_img.shape[:2]
    # Each view crops to its central step_deg portion. The angle-to-column
    # map under pinhole is x = cx + f_px * tan(angle), so the column range
    # corresponding to ±step_deg/2 from the centre is:
    f_px = 0.5 * W_view / math.tan(math.radians(fov_deg) * 0.5)
    half_step_rad = math.radians(step_deg * 0.5)
    crop_half_px = int(round(f_px * math.tan(half_step_rad)))
    crop_x0 = max(0, W_view // 2 - crop_half_px)
    crop_x1 = min(W_view, W_view // 2 + crop_half_px)
    if crop_x1 <= crop_x0:
        return None

    crops: list[np.ndarray] = []
    headings_chunks: list[np.ndarray] = []
    for v in sorted_views:
        img = v["image"]
        if img.shape[0] != H_view:
            continue
        crop = img[:, crop_x0:crop_x1, :]
        crops.append(crop)
        # Per-column heading via inverse pinhole.
        cols_in_view = np.arange(crop_x0, crop_x1, dtype=np.float32)
        rel_angle = np.degrees(np.arctan((cols_in_view - W_view * 0.5) / f_px))
        view_headings = (float(v["geo_heading"]) + rel_angle) % 360.0
        headings_chunks.append(view_headings.astype(np.float32))

    if not crops:
        return None

    # Normalise per-panel luminance so independently auto-exposed Street View
    # tiles blend smoothly.  Each crop is scaled to the median mean-luminance
    # across all crops.  We work in float32 to avoid clipping artefacts during
    # the scale, then round back to uint8.
    means = [float(c.astype(np.float32).mean()) for c in crops]
    ref = float(np.median(means))
    norm: list[np.ndarray] = []
    for c, m in zip(crops, means):
        if m > 1.0:
            scale = ref / m
            norm.append(np.clip(c.astype(np.float32) *
                        scale, 0, 255).astype(np.uint8))
        else:
            norm.append(c)

    stitched = np.concatenate(norm, axis=1)
    headings_per_col = np.concatenate(headings_chunks)
    return stitched, headings_per_col


__all__ = [
    'stitch_pano_mask_channel',
    'stitch_pano_masks',
    'project_buildings_to_pano',
    'stitch_pano_views',
]
