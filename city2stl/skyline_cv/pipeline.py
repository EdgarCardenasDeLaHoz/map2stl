"""Skyline-based building height estimation — core CV and math primitives.

Public surface used by region_pdf.py:
  Dataclasses  : Viewpoint, BuildingRecord, CapturedView, RegisteredBuildingEstimate
  Functions    : detect_skyline_contour, detect_building_silhouettes,
                 match_segments_to_buildings, register_view_to_osm,
                 estimate_heights_from_registration, aggregate_building_heights
  Helpers      : _building_height_from_tags, _polygon_area_m2,
                 _load_env_file_if_present
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# ── SegFormer-b0 (ADE20K) — lazy-loaded neural sky/building segmentation ─────
# ADE20K 0-based label indices:
#   1 = building;edifice
#   2 = sky
#  21 = water (lake/sea/ocean)
#  26 = sea
#  60 = river
# We treat any of {21, 26, 60} as "water" for the mIoU water co-objective.
_ADE20K_BUILDING: int = 1
_ADE20K_SKY: int = 2
_ADE20K_WATER_CLASSES: tuple[int, ...] = (21, 26, 60)
_SEGFORMER_MODEL_ID: str = "nvidia/segformer-b0-finetuned-ade-512-512"
_SEGFORMER_LOADED: bool = False
_SEGFORMER_OK: bool = False
_segformer_processor = None
_segformer_model = None
# LRU cache keyed by id(image_array): avoids double inference when
# detect_skyline_contour, detect_building_silhouettes, register_view_to_osm,
# and the joint optimizer all want the same image's masks. Capacity sized
# for one seed's spin views (12) + a few headroom slots.
from collections import OrderedDict as _OrderedDict
_NEURAL_CACHE_CAPACITY = 16
_neural_cache: "_OrderedDict[int, dict]" = _OrderedDict()


@dataclass(frozen=True)
class Viewpoint:
    name: str
    query: str
    lat: float
    lon: float
    heading: float
    pitch: float
    fov: float
    image_width: int
    image_height: int


@dataclass(frozen=True)
class BuildingRecord:
    feature_id: str
    name: str
    geometry: object
    centroid_lat: float
    centroid_lon: float
    height_tag_m: float | None
    height_source: str
    area_m2: float
    terrain_elev_m: float = 0.0


@dataclass(frozen=True)
class CapturedView:
    viewpoint: Viewpoint
    image_path: Path
    metadata_path: Path
    image: np.ndarray


@dataclass(frozen=True)
class RegisteredBuildingEstimate:
    feature_id: str
    name: str
    view_name: str
    heading_offset_deg: float
    x_px: float
    y_px: float
    forward_m: float
    estimated_height_m: float
    confidence: float


def _load_env_file_if_present() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return


def _building_height_from_tags(properties: dict) -> tuple[float | None, str]:
    raw_height = properties.get("height")
    if raw_height is not None:
        try:
            digits = "".join(ch for ch in str(raw_height)
                             if (ch.isdigit() or ch == "."))
            if digits:
                return float(digits), "osm_tag"
        except Exception:
            pass

    raw_levels = properties.get("building:levels") or properties.get("levels")
    if raw_levels is not None:
        try:
            levels = float(str(raw_levels).split(";")[0])
            return max(3.0, levels * 3.4), "osm_levels"
        except Exception:
            pass

    return None, "default"


def _polygon_area_m2(coords: list[tuple[float, float]]) -> float:
    """Approximate polygon area in m^2 from lon/lat coordinates.

    Uses a local equirectangular projection around the polygon centroid.
    """
    if len(coords) < 4:
        return 0.0
    lons = np.asarray([p[0] for p in coords], dtype=np.float64)
    lats = np.asarray([p[1] for p in coords], dtype=np.float64)
    lon0 = float(np.mean(lons))
    lat0 = float(np.mean(lats))
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    x = (lons - lon0) * m_per_deg_lon
    y = (lats - lat0) * m_per_deg_lat
    area = 0.5 * abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))
    return float(area)


# ---------------------------------------------------------------------------
# Camera / projection helpers
# ---------------------------------------------------------------------------

def _lonlat_to_local_m(lon: float, lat: float, lon0: float, lat0: float) -> tuple[float, float]:
    meters_per_deg_lat = 110_540.0
    meters_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    dx = (lon - lon0) * meters_per_deg_lon
    dy = (lat - lat0) * meters_per_deg_lat
    return dx, dy


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


def _ensure_segformer() -> bool:
    """Lazily load SegFormer-b0 (ADE20K). Returns True if the model is ready."""
    global _SEGFORMER_LOADED, _SEGFORMER_OK, _segformer_processor, _segformer_model
    if _SEGFORMER_LOADED:
        return _SEGFORMER_OK
    _SEGFORMER_LOADED = True
    try:
        from transformers import (  # noqa: PLC0415
            SegformerForSemanticSegmentation,
            SegformerImageProcessor,
        )
        _segformer_processor = SegformerImageProcessor.from_pretrained(_SEGFORMER_MODEL_ID)
        _segformer_model = SegformerForSemanticSegmentation.from_pretrained(_SEGFORMER_MODEL_ID)
        _segformer_model.eval()  # type: ignore[union-attr]
        _SEGFORMER_OK = True
    except Exception:
        _SEGFORMER_OK = False
    return _SEGFORMER_OK


def _neural_cache_put(img_id: int, entry: dict, image_rgb: np.ndarray) -> None:
    """LRU put with bounded capacity.

    CRITICAL: stores a strong reference to ``image_rgb`` inside ``entry``
    (key ``_anchor``). This prevents Python's garbage collector from freeing
    the image array while its id() is still in the cache. Without this
    anchor, freed memory addresses get reused for subsequent images and
    produce SILENT false cache hits — returning a different image's masks.
    The pre-LRU 1-slot cache was incidentally safe because it always held
    just one entry; the LRU's stale-id window made the bug observable.
    """
    entry = dict(entry)
    entry["_anchor"] = image_rgb
    if img_id in _neural_cache:
        _neural_cache.move_to_end(img_id)
        _neural_cache[img_id] = entry
        return
    _neural_cache[img_id] = entry
    while len(_neural_cache) > _NEURAL_CACHE_CAPACITY:
        _neural_cache.popitem(last=False)


def _neural_sky_and_building_masks(
    image_rgb: np.ndarray,
) -> tuple["np.ndarray | None", "np.ndarray | None"]:
    """Run SegFormer-b0 (ADE20K) inference and return boolean (H, W) masks.

    Returns (sky_mask, building_mask) where each pixel is True for that class,
    or (None, None) if the model is unavailable or inference fails.

    Results are cached by id(image_rgb). The cache pins the image array
    (see _neural_cache_put) so id() collisions from GC reuse cannot happen.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    # Defence-in-depth: verify the cached entry's anchor IS the array we were
    # called with. If anything ever bypasses the anchor (e.g. someone clears
    # _neural_cache externally without resetting anchors), the identity check
    # catches stale-id collisions before they corrupt results.
    if entry is not None and entry.get("_anchor") is image_rgb:
        _neural_cache.move_to_end(img_id)
        return entry["sky"], entry["building"]

    if not _ensure_segformer():
        _neural_cache_put(
            img_id, {"sky": None, "building": None, "water": None},
            image_rgb)
        return None, None

    try:
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415

        h, w = image_rgb.shape[:2]
        pil = PILImage.fromarray(image_rgb)
        inputs = _segformer_processor(images=pil, return_tensors="pt")  # type: ignore[misc]
        with torch.no_grad():
            outputs = _segformer_model(**inputs)  # type: ignore[misc]
        # logits: (1, num_classes, H/4, W/4) → upsample to original resolution
        upsampled = F.interpolate(
            outputs.logits, size=(h, w), mode="bilinear", align_corners=False
        )
        label_map = upsampled.squeeze(0).argmax(dim=0).numpy()  # (H, W) int64
        sky_mask = label_map == _ADE20K_SKY
        building_mask = label_map == _ADE20K_BUILDING
        # Water is the union of "water", "sea", "river" classes.
        water_mask = np.isin(label_map, _ADE20K_WATER_CLASSES)
        _neural_cache_put(img_id, {
            "sky": sky_mask, "building": building_mask, "water": water_mask},
            image_rgb)
        return sky_mask, building_mask
    except Exception:
        _neural_cache_put(
            img_id, {"sky": None, "building": None, "water": None},
            image_rgb)
        return None, None


def _neural_water_mask(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Return the cached water-class mask for this image. Triggers a forward
    pass if not yet cached. Subsequent calls within the cache window are O(1).
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if entry is not None and entry.get("_anchor") is not image_rgb:
        # Stale id collision (impossible while anchors are honoured, but
        # defensive): force re-fetch by clearing the matched slot.
        _neural_cache.pop(img_id, None)
        entry = None
    if entry is None:
        _neural_sky_and_building_masks(image_rgb)
        entry = _neural_cache.get(img_id)
    if entry is None:
        return None
    _neural_cache.move_to_end(img_id)
    return entry.get("water")


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
    stitched = np.concatenate(crops, axis=1)
    headings_per_col = np.concatenate(headings_chunks)
    return stitched, headings_per_col


def segformer_sliding_window(
    image_rgb: np.ndarray,
    window: int = 512,
    stride: int = 256,
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    """Run SegFormer over a wide image using overlapping windows.

    Returns ``(sky_mask, building_mask, water_mask)`` boolean (H, W) arrays,
    or None if SegFormer is unavailable. Window logits are averaged in
    overlapping regions for smooth class boundaries at seams.

    This is the substrate for processing stitched panoramic strips that
    don't fit in a single SegFormer-b0 forward pass.
    """
    if not _ensure_segformer():
        return None
    try:
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415

        h, w = image_rgb.shape[:2]
        if w <= window:
            # Just one window — defer to the single-image path for parity.
            sky, building = _neural_sky_and_building_masks(image_rgb)
            water = _neural_water_mask(image_rgb)
            if sky is None or building is None or water is None:
                return None
            return sky, building, water

        # Determine window start positions covering [0, w].
        starts = list(range(0, max(1, w - window + 1), stride))
        if starts[-1] + window < w:
            starts.append(w - window)

        # Run one window to discover num_classes.
        first_crop = image_rgb[:, : window, :]
        pil = PILImage.fromarray(first_crop)
        inputs = _segformer_processor(images=pil, return_tensors="pt")  # type: ignore[misc]
        with torch.no_grad():
            outputs = _segformer_model(**inputs)  # type: ignore[misc]
        n_classes = int(outputs.logits.shape[1])

        # Accumulator at native image resolution.
        accum = np.zeros((n_classes, h, w), dtype=np.float32)
        counts = np.zeros((h, w), dtype=np.int32)
        # Triangular column weight to taper window edges (reduces seam
        # visibility in the merged label map).
        col_weights = np.minimum(
            np.arange(1, window + 1, dtype=np.float32),
            np.arange(window, 0, -1, dtype=np.float32),
        )
        col_weights = col_weights / float(col_weights.max())

        for x0 in starts:
            x1 = min(x0 + window, w)
            crop = image_rgb[:, x0:x1, :]
            if crop.shape[1] < window:
                pad = np.zeros(
                    (h, window - crop.shape[1], 3), dtype=image_rgb.dtype)
                crop_in = np.concatenate([crop, pad], axis=1)
            else:
                crop_in = crop
            pil = PILImage.fromarray(crop_in)
            inputs = _segformer_processor(images=pil, return_tensors="pt")  # type: ignore[misc]
            with torch.no_grad():
                outputs = _segformer_model(**inputs)  # type: ignore[misc]
            upsampled = F.interpolate(
                outputs.logits, size=(h, window),
                mode="bilinear", align_corners=False,
            )
            logits_np = upsampled.squeeze(0).numpy()  # (C, h, window)
            actual_w = x1 - x0
            weight_slice = col_weights[:actual_w][None, None, :]
            accum[:, :, x0:x1] += logits_np[:, :, :actual_w] * weight_slice
            counts[:, x0:x1] += int(weight_slice.shape[2] > 0)
            # counts tracks number of windows contributing — used only for
            # tracking; column weight already handles intensity scaling.

        # Argmax over class dimension to produce label map.
        label_map = accum.argmax(axis=0)
        sky_mask = label_map == _ADE20K_SKY
        building_mask = label_map == _ADE20K_BUILDING
        water_mask = np.isin(label_map, _ADE20K_WATER_CLASSES)
        return sky_mask, building_mask, water_mask
    except Exception:
        return None


def _make_sky_mask_from_bool(sky_bool: np.ndarray, h: int, w: int) -> np.ndarray:
    """Convert a boolean sky mask to uint8, keeping only pixels connected to top border."""
    raw = (sky_bool.astype(np.uint8)) * 255
    n_labels, labels, _stats, _centroids = cv2.connectedComponentsWithStats(raw, connectivity=8)
    top_labels = {int(v) for v in labels[0, :] if int(v) != 0}
    connected = np.zeros((h, w), dtype=np.uint8)
    for lab in top_labels:
        connected[labels == lab] = 255
    return connected if np.any(connected) else raw


def detect_skyline_contour(image_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return a top-down skyline contour and a binary sky mask.

    Attempts SegFormer-b0 neural segmentation first; falls back to HSV
    colour-threshold heuristics when the model is unavailable.
    """
    h, w = image_rgb.shape[:2]

    # ── Neural sky segmentation (preferred) ──────────────────────────────────
    sky_neural, _ = _neural_sky_and_building_masks(image_rgb)
    if sky_neural is not None:
        sky_mask = _make_sky_mask_from_bool(sky_neural, h, w)
    else:
        # ── Colour-threshold fallback ─────────────────────────────────────────
        hsv = cv2.cvtColor(image_rgb, cv2.COLOR_RGB2HSV)
        rgb = image_rgb.astype(np.float32)
        r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]

        sample_rows = max(24, h // 6)
        top = hsv[:sample_rows]
        v_thr = max(130, int(np.percentile(top[:, :, 2], 55)))
        s_thr = min(170, int(np.percentile(top[:, :, 1], 70) + 25))

        bright_low_sat = (hsv[:, :, 2] >= v_thr) & (hsv[:, :, 1] <= s_thr)
        blue_dominant = (b >= g * 0.96) & (b >= r * 1.03) & (hsv[:, :, 2] >= 70)
        coarse_sky = (bright_low_sat | blue_dominant).astype(np.uint8) * 255

        kernel = np.ones((3, 3), np.uint8)
        coarse_sky = cv2.morphologyEx(coarse_sky, cv2.MORPH_OPEN, kernel)
        coarse_sky = cv2.morphologyEx(coarse_sky, cv2.MORPH_CLOSE, kernel)

        n_labels, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
            coarse_sky, connectivity=8)
        top_labels = {int(v) for v in labels[0, :] if int(v) != 0}
        sky_mask = np.zeros_like(coarse_sky, dtype=np.uint8)
        for lab in top_labels:
            sky_mask[labels == lab] = 255

        if not np.any(sky_mask):
            sky_mask = coarse_sky.copy()

    # ── Contour extraction (shared path) ─────────────────────────────────────
    contour = np.full(w, np.nan, dtype=np.float32)
    for x in range(w):
        column = sky_mask[:, x] > 0
        non_sky = np.where(~column)[0]
        if non_sky.size:
            contour[x] = float(non_sky[0])

    # Reject implausible boundaries too close to image top/bottom.
    y_min = float(h * 0.06)
    y_max = float(h * 0.90)
    contour = np.where((contour >= y_min) & (contour <= y_max), contour, np.nan)

    if np.all(np.isnan(contour)):
        contour[:] = float(h * 0.5)
    else:
        valid_idx = np.where(~np.isnan(contour))[0]
        valid_vals = contour[valid_idx]
        if valid_idx.size >= 2:
            interp = np.interp(
                np.arange(w, dtype=np.float32),
                valid_idx.astype(np.float32),
                valid_vals.astype(np.float32),
            )
            contour = interp.astype(np.float32)
        else:
            fill = np.nanmedian(contour)
            contour = np.where(np.isnan(contour), fill, contour)
        contour = median_filter(contour, size=9, mode="nearest").astype(np.float32)

    return contour, sky_mask > 0


def _segment_has_structure(
    image: np.ndarray,
    x_left: int,
    x_right: int,
    top_y: int,
    base_y: int,
    building_mask: "np.ndarray | None" = None,
    peak_x: int | None = None,
) -> bool:
    """Return True if the image region plausibly contains a building facade.

    When *building_mask* (a boolean (H, W) array from SegFormer) is provided it
    is used as the primary signal. We test two cuts:

    1. Bounding-box fraction: ≥ 10 % accept, < 5 % reject when also weak at
       the peak column. Wide boxes around slim glass towers contain a lot of
       sky and easily fall below 10 % even though the tower itself is
       obviously a building.
    2. Peak-column fraction: a tight ±4 px column at ``peak_x`` (or the box
       midpoint) restricted to the lower 60 % of the box. This captures the
       facade core where buildings have ≥ 25 % mask coverage even when the
       wide bounding box is mostly sky.

    Colour/texture fallback rejects:
    - Blue-dominant regions (open water, sky bleed)
    - Regions with predominantly horizontal texture (water surface)
    - Structureless regions (open sky, flat beach sand)
    """
    h, w = image.shape[:2]
    y0 = max(0, top_y)
    y1 = min(h, base_y)
    x0 = max(0, x_left)
    x1 = min(w, x_right)
    if y1 <= y0 or x1 <= x0:
        return True  # degenerate region: don't reject

    region = image[y0:y1, x0:x1].astype(np.float32)
    if region.size < 100:
        return True

    # ── Neural building mask (authoritative when available) ──────────────────
    if building_mask is not None:
        bfrac = float(building_mask[y0:y1, x0:x1].mean())
        # Tight-column probe: ±4 px around the peak, lower 60 % of segment.
        # This is where a slim glass tower lives even when the bounding box
        # is wide and full of sky.
        px = int(peak_x) if peak_x is not None else (x0 + x1) // 2
        cx0 = max(0, px - 4)
        cx1 = min(w, px + 5)
        # Lower 60 % of segment vertically — the building "trunk", not the roof line
        trunk_top = y0 + (y1 - y0) * 2 // 5
        col_frac = float(building_mask[trunk_top:y1, cx0:cx1].mean()) if cx1 > cx0 else 0.0

        if bfrac >= 0.10 or col_frac >= 0.25:
            return True   # clearly a building facade
        if bfrac < 0.05 and col_frac < 0.10:
            return False  # no building pixels — water / sky / open ground
        # ambiguous mid-band: fall through to heuristics

    # ── Colour / texture heuristics ──────────────────────────────────────────
    r_mean = float(np.mean(region[:, :, 0]))
    g_mean = float(np.mean(region[:, :, 1]))
    b_mean = float(np.mean(region[:, :, 2]))

    # Reject: blue-dominant (water / sky)
    if b_mean > r_mean * 1.15 and b_mean > 100 and b_mean > g_mean * 1.05:
        return False

    gray = cv2.cvtColor(region.astype(np.uint8), cv2.COLOR_RGB2GRAY).astype(np.float32)
    sobel_v = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))  # vertical edges
    sobel_h = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3))  # horizontal edges

    mean_v = float(np.mean(sobel_v))
    mean_h = float(np.mean(sobel_h))

    # Reject: water surface (dominated by horizontal ripple texture)
    if mean_h > mean_v * 2.5 and mean_h > 4.0:
        return False

    # Reject: no structure at all (featureless sky/sand)
    if mean_v < 1.5 and mean_h < 1.5:
        return False

    return True


def _footprint_roof_y_from_mask(
    building_mask: "np.ndarray | None",
    x_left: int,
    x_right: int,
    min_avg_rows: float = 8.0,
    min_run_px: int = 3,
) -> tuple[int | None, float]:
    """Sample the building mask across the projected footprint x-range and
    return (roof_y, avg_building_rows_per_column).

    Previously this used a "fraction of FULL-HEIGHT box is building" gate
    (25 %) which systematically rejected short or distant buildings whose
    silhouette only occupies a fraction of the image height — even a 30-
    story tower at 1 km projects ~30 px tall (5 % of a 540 px frame) and
    would fail. The denominator was the whole image height; that's wrong.

    The new gate is **average building rows per column** — at least 8 rows
    of building pixels per column on average. This passes any real building
    silhouette and rejects spurious matches where the projected x-range
    lands on water/sky.
    """
    if building_mask is None:
        return None, 0.0
    h, w = building_mask.shape[:2]
    x0 = max(0, x_left)
    x1 = min(w - 1, x_right)
    if x1 < x0:
        return None, 0.0
    slab = building_mask[:, x0 : x1 + 1].astype(np.uint8)
    if slab.size == 0:
        return None, 0.0
    cols = x1 - x0 + 1
    rows = slab.sum(axis=1)
    avg_rows = float(slab.sum()) / float(cols)
    if avg_rows < min_avg_rows:
        return None, avg_rows
    threshold = max(1, cols // 2)
    in_run = 0
    for y in range(h):
        if rows[y] >= threshold:
            in_run += 1
            if in_run >= min_run_px:
                return int(y - min_run_px + 1), avg_rows
        else:
            in_run = 0
    return None, avg_rows


def _building_base_y_from_mask(
    building_mask: "np.ndarray | None",
    peak_x: int,
    top_y: int,
    half_width: int = 4,
    min_gap_px: int = 4,
) -> int | None:
    """Return the last row at column peak_x where the building mask is still
    majority-building (allowing small mask gaps).

    Walking the SegFormer building mask DOWN from top_y gives us the column
    where the facade actually ends — far more accurate than _estimate_building_base
    which gets confused by cloud edges, balcony rails, and window mullions.
    """
    if building_mask is None:
        return None
    h, w = building_mask.shape[:2]
    if peak_x < 0 or peak_x >= w:
        return None
    x0 = max(0, peak_x - half_width)
    x1 = min(w, peak_x + half_width + 1)
    if x1 <= x0:
        return None
    slab = building_mask[:, x0:x1].astype(np.uint8)
    row_counts = slab.sum(axis=1)
    threshold = max(1, (x1 - x0) // 2 + 1)

    last_building = -1
    consecutive_gap = 0
    for y in range(max(0, top_y), h):
        if row_counts[y] >= threshold:
            last_building = y
            consecutive_gap = 0
        else:
            consecutive_gap += 1
            if consecutive_gap >= min_gap_px and last_building >= 0:
                break
    return last_building if last_building >= 0 else None


def _estimate_building_base(
    image: np.ndarray,
    peak_x: int,
    top_y: int,
    h: int,
    building_mask: "np.ndarray | None" = None,
) -> int:
    """Estimate where the building base meets the ground/water below the rooftop.

    Strategy in order of preference:
    1. **Building mask** (preferred when available): walk the SegFormer mask
       down from top_y at this column and return the bottom of the building
       region. This is robust to cloud edges, balcony bands, and reflections
       that confuse the Sobel-based heuristic.
    2. Strong horizontal Sobel edge — building/ground color boundary.
    3. Brightness jump relative to the facade — original heuristic.

    Falls back to 75% frame height if nothing clear is found.
    """
    # Mask-based path first
    if building_mask is not None:
        mask_base = _building_base_y_from_mask(building_mask, peak_x, top_y)
        if mask_base is not None and mask_base > top_y + 10:
            return min(h - 1, mask_base)

    x0 = max(0, peak_x - 8)
    x1 = min(image.shape[1], peak_x + 9)
    strip = image[top_y:, x0:x1]

    if strip.shape[0] < 12:
        return h - 1

    gray_strip = cv2.cvtColor(strip, cv2.COLOR_RGB2GRAY).astype(np.float32)
    # Horizontal Sobel captures horizontal edges (building bottom against water/ground)
    sobel_h = np.abs(cv2.Sobel(gray_strip, cv2.CV_32F, 0, 1, ksize=3))
    edge_row = sobel_h.mean(axis=1)  # mean edge strength per row

    col_bright = np.mean(strip.astype(np.float32), axis=(1, 2))
    facade_ref = float(np.mean(col_bright[:10]))

    edge_thr = max(8.0, float(np.mean(edge_row[5:])) + float(np.std(edge_row[5:])) * 1.5)

    for i in range(8, strip.shape[0]):
        # Strong horizontal edge → likely building bottom
        if edge_row[i] >= edge_thr:
            return min(h - 1, top_y + i)
        # Significant brightness jump → likely open water/sky below
        if col_bright[i] > facade_ref * 1.25:
            return min(h - 1, top_y + i)

    # Conservative fallback: clip at 75% of frame height
    return min(h - 1, int(h * 0.75))


def detect_building_silhouettes(
    contour_y: np.ndarray,
    image: np.ndarray,
    min_width_px: int = 15,
    min_prominence_px: float = 8.0,
) -> list[dict]:
    """Identify **individual** building silhouettes in a skyline view.

    Strategy
    --------
    1. Find *building peaks* — local minima of the smoothed contour (low y =
       high in the image = tall building).  Each peak is one building.
    2. Find *valley boundaries* — local maxima of the contour (sky dipping
       between buildings) plus vertical Sobel edges.
    3. For each peak, assign the narrowest enclosing boundary segment.
    4. Filter by prominence and minimum width.
    5. Deduplicate: if two peaks fall in the same boundary segment, keep only
       the taller one.
    6. Estimate the building base row from image brightness below each rooftop.

    Returns a list of dicts compatible with ``match_segments_to_buildings``:
    ``{x_left, x_right, top_y, base_y, mid_x, peak_x}``.
    """
    from scipy.ndimage import uniform_filter1d, gaussian_filter1d  # already at module level
    c = np.asarray(contour_y, dtype=np.float32)
    h, w = image.shape[:2]

    finite = c[np.isfinite(c)]
    if finite.size == 0:
        return []

    global_min = float(np.nanmin(finite))
    global_max = float(np.nanmax(finite))
    global_median = float(np.nanmedian(finite))
    height_range = global_max - global_min

    if height_range < min_prominence_px:
        return []

    # Fill NaN for smoothing
    c_filled = np.where(np.isfinite(c), c, global_median)

    # Fine-scale smoothing to resolve individual buildings
    smooth = uniform_filter1d(c_filled, size=7)

    # ── Building peaks (individual rooftops) ─────────────────────────────
    # Invert: find_peaks on -smooth → local minima of contour → building tops.
    # Prominence floor is intentionally low: a 4-tower Bocagrande skyline has
    # ~20–50 px variation between towers, and a 0.06×range threshold (= 15 px
    # at range=250) drops most of them. 0.04×range with a 3 px floor still
    # rejects noise but keeps real tower-to-tower steps.
    peaks, _ = find_peaks(
        -smooth,
        distance=12,
        prominence=max(3.0, height_range * 0.04),
    )

    if peaks.size == 0:
        return []

    # ── Boundary signals ─────────────────────────────────────────────────
    # 1. Valleys in contour (sky between buildings)
    valleys, _ = find_peaks(
        smooth,
        distance=10,
        prominence=max(3.0, height_range * 0.04),
    )

    # 2. Vertical Sobel edges in sky-building transition zone
    gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image.astype(np.uint8)
    sobel_x = np.abs(cv2.Sobel(gray.astype(np.float32), cv2.CV_32F, 1, 0, ksize=3))
    zone_top = max(0, int(global_min) - 15)
    zone_bot = min(h, int(global_median) + 30)
    if zone_bot > zone_top:
        edge_col = sobel_x[zone_top:zone_bot, :].mean(axis=0).astype(np.float32)
        edge_smooth = gaussian_filter1d(edge_col, sigma=2.0)
        edge_thr = float(edge_smooth.mean()) + float(edge_smooth.std()) * 0.8
        edge_bounds, _ = find_peaks(edge_smooth, distance=12, height=edge_thr)
    else:
        edge_bounds = np.array([], dtype=np.int32)

    # All boundaries (include image edges)
    boundaries = np.sort(np.unique(
        np.concatenate([[0], valleys, edge_bounds, [w - 1]])
    ))

    # ── Assign each peak to its narrowest enclosing boundary segment ──────
    # Fetch neural building mask from cache (populated by detect_skyline_contour
    # if it was called with the same image object in this request cycle).
    _, _building_mask = _neural_sky_and_building_masks(image)
    raw: list[dict] = []
    for peak_x in peaks:
        top_y_raw = float(c[peak_x]) if np.isfinite(c[peak_x]) else float(smooth[peak_x])

        if global_median - top_y_raw < min_prominence_px:
            continue

        left_cands = boundaries[boundaries <= peak_x]
        right_cands = boundaries[boundaries >= peak_x]
        if left_cands.size == 0 or right_cands.size == 0:
            continue

        x_left = int(left_cands[-1])
        x_right = int(right_cands[0])

        if x_right - x_left < min_width_px:
            continue

        base_y = _estimate_building_base(
            image, int(peak_x), int(top_y_raw), h,
            building_mask=_building_mask,
        )

        # Validate image content — reject water, sky bleed, featureless regions.
        # Pass peak_x so the tight-column check evaluates at the actual rooftop
        # location, not the segment midpoint (which can be off-center on a slim
        # glass tower sitting at one end of a wide segment).
        if not _segment_has_structure(
            image, x_left, x_right, int(top_y_raw), base_y,
            building_mask=_building_mask, peak_x=int(peak_x),
        ):
            continue

        raw.append({
            "x_left": x_left,
            "x_right": x_right,
            "top_y": int(top_y_raw),
            "base_y": base_y,
            "mid_x": (x_left + x_right) // 2,
            "peak_x": int(peak_x),
        })

    # ── Deduplicate: same (x_left, x_right) → keep tallest ───────────────
    best: dict[tuple, dict] = {}
    for sil in raw:
        key = (sil["x_left"], sil["x_right"])
        if key not in best or sil["top_y"] < best[key]["top_y"]:
            best[key] = sil

    return sorted(best.values(), key=lambda s: s["mid_x"])


def compute_building_band(
    building_mask: "np.ndarray | None",
    min_col_frac: float = 0.01,
    slack_px: int = 5,
) -> tuple[int, int] | None:
    """Return (y_top, y_bot) — the vertical band where building pixels exist.

    A row is "building-active" if ≥ min_col_frac of its columns are building.
    The returned band spans from the topmost to bottommost active row,
    expanded by ``slack_px``. Returns None if no row passes the threshold.

    Used by detect_buildings_from_mask and the PDF renderer to crop away
    water/sky bands the building mask doesn't occupy — letting segments and
    visual displays focus on the actual skyline band.
    """
    if building_mask is None:
        return None
    bm = np.asarray(building_mask)
    if bm.size == 0:
        return None
    h, w = bm.shape[:2]
    if w == 0 or h == 0:
        return None
    row_frac = bm.sum(axis=1).astype(np.float32) / float(w)
    active = np.where(row_frac >= min_col_frac)[0]
    if active.size == 0:
        return None
    y_top = max(0, int(active[0]) - slack_px)
    y_bot = min(h - 1, int(active[-1]) + slack_px)
    if y_bot <= y_top + 5:
        return None
    return y_top, y_bot


def detect_buildings_from_mask(
    building_mask: "np.ndarray | None",
    min_width_px: int = 18,
    min_height_px: int = 25,
    split_wide_components: bool = True,
    contour: "np.ndarray | None" = None,
) -> list[dict]:
    """Identify individual buildings as connected components of the SegFormer
    building mask.

    This complements ``detect_building_silhouettes`` which depends on the
    skyline contour having a prominent valley between adjacent buildings.
    The mask-based approach catches every blob the segmenter labels as
    building, including:

    - towers that share a similar roof height (no contour valley between them)
    - buildings tucked behind a closer one whose roof barely peeks above
    - mid-rise rows where the skyline is nearly flat

    Wide components are common on long skyline panoramas (Bocagrande from
    across the bay, Old Town from Manga): adjacent towers share base pixels
    and merge into one big blob. When ``split_wide_components`` is True we
    look for local maxima of the per-column building height inside the blob
    and emit one silhouette per peak, so a single mask component covering
    "row of 10 towers" produces 10 silhouettes instead of one.

    Returns the same dict shape as ``detect_building_silhouettes`` so the
    downstream matcher can consume both sources uniformly.
    """
    if building_mask is None:
        return []
    mask = (building_mask.astype(np.uint8)) * 255
    if not mask.any():
        return []
    h, w = mask.shape[:2]

    # Pre-crop to the building band: zero out mask rows above/below the band
    # where building pixels actually exist. Stops connected components from
    # spanning down into water reflections / boats picked up by the SegFormer
    # mask, and gives downstream peak detection a tighter target. The band is
    # computed from the same mask, so this is a no-op when buildings cover
    # most of the frame and a big help when most of the frame is water/sky.
    band = compute_building_band(building_mask)
    if band is not None:
        y_top, y_bot = band
        # Outside the band, zero the mask so connected components and per-
        # column reductions don't include water/sky reflections.
        mask = mask.copy()
        if y_top > 0:
            mask[:y_top, :] = 0
        if y_bot < h - 1:
            mask[y_bot + 1 :, :] = 0

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    out: list[dict] = []
    for i in range(1, n_labels):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        if cw < min_width_px or ch < min_height_px:
            continue
        component = labels[y : y + ch, x : x + cw] == i
        # Per-column "building height in pixels" — the silhouette of this blob.
        col_counts = component.sum(axis=0).astype(np.float32)
        if col_counts.size == 0:
            continue

        # For each column find the topmost building row (within this component)
        # to use as the rooftop pixel of a per-peak silhouette below.
        top_rows = np.full(cw, -1, dtype=np.int32)
        for off in range(cw):
            rows = np.where(component[:, off])[0]
            if rows.size:
                top_rows[off] = int(rows[0])

        # Decide whether to emit a single silhouette for the whole component
        # or split it into multiple peaks. When the skyline ``contour`` is
        # provided we use IT for peak detection (the actual rooftop silhouette
        # captures tower-to-tower variation directly, even when adjacent towers
        # share base pixels in the mask). Otherwise we fall back to the mask's
        # per-column height profile.
        peaks: np.ndarray = np.empty(0, dtype=np.int64)
        if split_wide_components and cw >= int(1.5 * min_width_px):
            from scipy.signal import find_peaks  # scipy already a project dep
            # Union peaks from two signals to cover both failure modes:
            #   1. Contour-based — finds rooftop peaks even when adjacent
            #      towers share base pixels in the mask.
            #   2. Mask col-counts — finds tall thin spires that don't carve
            #      a deep valley in the contour (because the spire's top is
            #      still in the sky, not "between" anything).
            signals: list[np.ndarray] = []
            if contour is not None and contour.size >= x + cw:
                contour_slice = np.asarray(contour[x : x + cw], dtype=np.float32)
                if np.any(~np.isfinite(contour_slice)):
                    fill = float(np.nanmedian(contour_slice))
                    contour_slice = np.where(np.isfinite(contour_slice), contour_slice, fill)
                signals.append(-contour_slice)
            signals.append(col_counts.astype(np.float32))

            all_peaks: list[int] = []
            for signal in signals:
                sig_std = float(np.std(signal))
                # Aggressively low prominence — adjacent towers on a panorama
                # often have similar roof heights, so the contour valleys
                # between them are shallow. We require only a small
                # prominence relative to the signal variance.
                prom = max(1.5, sig_std * 0.15)
                p, _ = find_peaks(signal, distance=8, prominence=prom)
                all_peaks.extend(int(v) for v in p.tolist())
            # Dedup with a 10-px tolerance — peaks within that window from
            # different signals are the same tower.
            if all_peaks:
                all_peaks.sort()
                dedup: list[int] = [all_peaks[0]]
                for p in all_peaks[1:]:
                    if p - dedup[-1] >= 10:
                        dedup.append(p)
                peaks = np.asarray(dedup, dtype=np.int64)

        if peaks.size >= 2:
            # Split: one silhouette per peak. We bound each peak by walking left
            # and right from the peak column until col_counts drops below
            # 0.4 * peak height — that's the actual edge of THIS tower in the
            # mask. The valleys between peaks act as hard ceilings so we never
            # cross into the neighboring tower; the component edges bound the
            # outermost peaks. The previous valley-to-valley scheme used the
            # gap BETWEEN buildings as the box edge, producing boxes that
            # extended halfway to the next tower.
            valleys: list[int] = [0]
            for j in range(peaks.size - 1):
                a, b = int(peaks[j]), int(peaks[j + 1])
                if a >= b:
                    continue
                v = int(np.argmin(col_counts[a : b + 1])) + a
                valleys.append(v)
            valleys.append(cw - 1)
            edge_frac = 0.4
            for j, p in enumerate(peaks):
                p = int(p)
                peak_h = float(col_counts[p])
                if peak_h <= 0:
                    continue
                cutoff = peak_h * edge_frac
                left_bound = int(valleys[j])
                right_bound = int(valleys[j + 1])
                # Walk left from peak to first column < cutoff (or left_bound).
                xL_off = p
                k = p
                while k > left_bound and col_counts[k] >= cutoff:
                    xL_off = k
                    k -= 1
                # Walk right from peak to first column < cutoff (or right_bound).
                xR_off = p
                k = p
                while k < right_bound and col_counts[k] >= cutoff:
                    xR_off = k
                    k += 1
                xL = x + max(0, xL_off)
                xR = x + min(cw - 1, xR_off)
                if xR - xL < min_width_px:
                    continue
                # Top row at this peak's column
                top_off = int(top_rows[p]) if top_rows[p] >= 0 else 0
                top_y = int(y + top_off)
                # Tighten base_y by walking the ORIGINAL mask down from this
                # peak's top until building coverage drops out. Previously
                # base_y = y + ch - 1 (component bottom), which extended into
                # water when the SegFormer mask included reflections / boats.
                peak_x_abs = x + p
                base_walked = _building_base_y_from_mask(
                    building_mask, peak_x_abs, top_y)
                if base_walked is not None and base_walked > top_y + 5:
                    base_y = int(min(h - 1, base_walked))
                else:
                    base_y = int(min(h - 1, y + ch - 1))
                out.append({
                    "x_left": xL,
                    "x_right": xR,
                    "top_y": top_y,
                    "base_y": base_y,
                    "mid_x": (xL + xR) // 2,
                    "peak_x": peak_x_abs,
                })
        else:
            # Single-peak fallback: still tighten the segment to the actual
            # silhouette by walking left/right from the peak column until
            # col_counts drops below 0.4 * peak.
            peak_col_off = int(np.argmax(col_counts))
            peak_h = float(col_counts[peak_col_off])
            cutoff = peak_h * 0.4
            xL_off = peak_col_off
            k = peak_col_off
            while k > 0 and col_counts[k] >= cutoff:
                xL_off = k
                k -= 1
            xR_off = peak_col_off
            k = peak_col_off
            while k < cw - 1 and col_counts[k] >= cutoff:
                xR_off = k
                k += 1
            peak_x = x + peak_col_off
            top_off = int(top_rows[peak_col_off]) if top_rows[peak_col_off] >= 0 else 0
            top_y = int(y + top_off)
            base_walked = _building_base_y_from_mask(building_mask, peak_x, top_y)
            if base_walked is not None and base_walked > top_y + 5:
                base_y = int(min(h - 1, base_walked))
            else:
                base_y = int(min(h - 1, y + ch - 1))
            xL = x + xL_off
            xR = x + xR_off
            if xR - xL < min_width_px:
                xL = x
                xR = x + cw - 1
            out.append({
                "x_left": xL,
                "x_right": xR,
                "top_y": top_y,
                "base_y": base_y,
                "mid_x": (xL + xR) // 2,
                "peak_x": peak_x,
            })
    return out


def _merge_silhouette_sources(
    primary: list[dict],
    secondary: list[dict],
    iou_thresh: float = 0.3,
) -> list[dict]:
    """Merge two silhouette lists, deduplicating by horizontal IoU.

    A silhouette from ``secondary`` is kept only if it doesn't overlap any
    ``primary`` silhouette's x range by more than ``iou_thresh``. The peak
    column from each surviving silhouette is preserved for height extraction.
    """
    def _x_iou(a: dict, b: dict) -> float:
        ax0, ax1 = float(a["x_left"]), float(a["x_right"])
        bx0, bx1 = float(b["x_left"]), float(b["x_right"])
        inter = max(0.0, min(ax1, bx1) - max(ax0, bx0))
        union = max(ax1, bx1) - min(ax0, bx0)
        return inter / union if union > 0 else 0.0

    out = list(primary)
    for s in secondary:
        if all(_x_iou(s, p) < iou_thresh for p in out):
            out.append(s)
    return sorted(out, key=lambda s: s["mid_x"])


def match_segments_to_buildings(
    segments: list[dict],
    projections: list[dict],
    buildings_by_id: dict[str, BuildingRecord],
    min_interval_iou: float = 0.10,
) -> list[dict]:
    """For each skyline segment, pick the best-matching projected building.

    Ranking uses interval-IoU between the segment's x-range and the building's
    projected footprint x-range — width-aware, unlike the previous point-in-
    rect test. A building whose projected footprint covers most of the segment
    outranks one that merely has its centroid grazing the segment edge.

    Tie-breaking (within the same IoU bucket):
      - Nearest building wins (smallest forward_m). The closest building owns
        the skyline at that column unless...
      - ...the nearest candidate is very short (height_proxy < 5 m) AND a much
        taller building (>2× height_proxy) sits within 1.5× the nearest
        distance. Then prefer the taller one (kiosk-in-front-of-tower case).
    """
    def _seg_width(seg: dict) -> float:
        return max(1.0, float(seg["x_right"]) - float(seg["x_left"]))

    def _proj_width(proj: dict) -> float:
        pL = float(proj.get("x_left_px", proj["x_px"] - 10.0))
        pR = float(proj.get("x_right_px", proj["x_px"] + 10.0))
        return max(1.0, abs(pR - pL))

    def _interval_iou(seg: dict, proj: dict) -> float:
        sL = float(seg["x_left"]); sR = float(seg["x_right"])
        pL = float(proj.get("x_left_px", proj["x_px"] - 10.0))
        pR = float(proj.get("x_right_px", proj["x_px"] + 10.0))
        if pL > pR:
            pL, pR = pR, pL
        inter = max(0.0, min(sR, pR) - max(sL, pL))
        union = max(sR, pR) - min(sL, pL)
        return (inter / union) if union > 0 else 0.0

    def _width_ratio_score(seg: dict, proj: dict) -> float:
        """Score 0..1 for "is this building's projected width plausible for
        this image segment's width?" 1.0 when widths match, falling off as
        the ratio moves away from 1. The matched segment should visually
        cover roughly the building's projected footprint width — a 3× too-
        wide projected building isn't the right match even if x ranges
        nominally overlap (e.g. a long warehouse projected onto a single
        tower segment)."""
        ratio = _proj_width(proj) / _seg_width(seg)
        if ratio <= 0:
            return 0.0
        # log-symmetric falloff: ratio=1 → 1.0, ratio=2 or 0.5 → ~0.5,
        # ratio=4 or 0.25 → ~0.0
        log_r = abs(math.log2(ratio))
        return max(0.0, 1.0 - 0.5 * log_r)

    out: list[dict] = []
    for seg in segments:
        scored = []
        for p in projections:
            iou = _interval_iou(seg, p)
            if iou >= min_interval_iou:
                # Combine geometric overlap with width-plausibility. IoU is
                # weighted 0.7 (it's the stronger signal); width-ratio is 0.3
                # (a tiebreaker that prevents wildly-wrong-width matches
                # within a high-IoU pool — the Box 5 / b0739 failure mode
                # where the actually-narrow tower segment gets matched to a
                # wide warehouse footprint behind it).
                w_score = _width_ratio_score(seg, p)
                combined = 0.7 * iou + 0.3 * w_score
                scored.append((combined, iou, p))
        match: dict | None = None
        if scored:
            scored.sort(key=lambda t: t[0], reverse=True)
            best_combined = scored[0][0]
            bucket = [p for c, _iou, p in scored if c >= best_combined - 0.10]
            bucket.sort(key=lambda p: float(p.get("forward_m", 1e9)))
            nearest = bucket[0]
            nearest_fwd = float(nearest.get("forward_m", 1e9))
            b_near = buildings_by_id.get(nearest["feature_id"])
            nearest_h = _height_proxy(b_near) if b_near is not None else 0.0
            match = nearest
            if nearest_h < 5.0:
                max_fwd = nearest_fwd * 1.5
                for cand in bucket[1:]:
                    if float(cand.get("forward_m", 1e9)) > max_fwd:
                        break
                    b = buildings_by_id.get(cand["feature_id"])
                    h = _height_proxy(b) if b is not None else 0.0
                    if h > nearest_h * 2.0:
                        match = cand
                        break
        out.append({**seg, "matched_projection": match})
    return out


def _height_proxy(building: BuildingRecord) -> float:
    """Rank buildings as skyline-defining when no tagged height exists.

    Tagged height wins; otherwise sqrt(footprint area) as a coarse proxy.
    """
    if building.height_tag_m is not None:
        return float(building.height_tag_m)
    return float(min(40.0, 0.6 * math.sqrt(max(building.area_m2, 1.0))))


def _cull_occluded_projections(
    projections: list[dict],
    buildings_by_id: dict[str, BuildingRecord],
    image_width: int,
    bin_px: int = 24,
) -> list[dict]:
    """Per ~bin_px column window, keep only buildings that are actually visible.

    Physics: the CLOSEST building in each column bin occupies the visible
    foreground. A taller building BEHIND it is visible only if its roof
    sticks above the front building's roof from the camera's viewpoint.

    Approximated with height proxies (tagged height or sqrt-area fallback):
       front_roof_angle ≈ front_height / front_forward_m
       back_roof_angle  ≈ back_height  / back_forward_m
    A back building is kept iff its roof_angle exceeds the front's by a
    margin (20 %). Otherwise it's fully occluded and dropped.

    Without this rule, the matcher kept the "tallest in bin" — which often
    placed a far thin OSM building at the image column actually occupied by
    a nearer shorter building, producing nonsensical match overlays.
    """
    if not projections:
        return []
    bins: dict[int, list[dict]] = {}
    for proj in projections:
        b = int(round(proj["x_px"] / max(1, bin_px)))
        bins.setdefault(b, []).append(proj)

    def _angle(p: dict) -> float:
        b = buildings_by_id.get(p["feature_id"])
        h = _height_proxy(b) if b is not None else 10.0
        # Camera height ~1.7m; we want the rooftop angle above the camera.
        return float(max(h - 1.7, 0.5)) / max(float(p.get("forward_m", 1e9)), 1.0)

    kept: dict[str, dict] = {}
    for items in bins.values():
        if len(items) == 1:
            kept[items[0]["feature_id"]] = items[0]
            continue
        items_sorted = sorted(items, key=lambda p: float(p.get("forward_m", 1e9)))
        front = items_sorted[0]
        kept[front["feature_id"]] = front
        front_angle = _angle(front)
        for back in items_sorted[1:]:
            if _angle(back) > front_angle * 1.20:
                # Back building's roof sticks visibly above the front's:
                # contributes the top of the skyline, keep it.
                kept[back["feature_id"]] = back
            # else: fully occluded by the front building, drop.
    return list(kept.values())


def _match_projections_to_peaks(
    projections: list[dict],
    peaks: np.ndarray,
    max_match_px: float = 30.0,
) -> tuple[list[tuple[int, int]], float]:
    """Hungarian assignment between projected building x positions and skyline peaks.

    Returns (matches, mean_residual_px). matches is a list of (proj_idx, peak_idx)
    pairs; pairs whose cost exceeds max_match_px are dropped.
    """
    if not projections or peaks.size == 0:
        return [], float("inf")

    proj_x = np.asarray([p["x_px"] for p in projections], dtype=np.float32)
    peak_x = peaks.astype(np.float32)

    cost = np.abs(proj_x[:, None] - peak_x[None, :])
    row_idx, col_idx = linear_sum_assignment(cost)
    matches: list[tuple[int, int]] = []
    residuals: list[float] = []
    for r, c in zip(row_idx, col_idx):
        d = float(cost[r, c])
        if d <= max_match_px:
            matches.append((int(r), int(c)))
            residuals.append(d)
    if not matches:
        return [], float("inf")
    return matches, float(np.mean(residuals))


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


# Module-level cache keyed by id(buildings_list) — cleared automatically when
# the list is garbage-collected. For a single PDF run we project the same
# list across hundreds of offset candidates, so this saves a lot of work.
_VERT_CACHE: dict[int, tuple[np.ndarray, np.ndarray, np.ndarray]] = {}


def _get_vertex_arrays_cached(
    buildings: Sequence[BuildingRecord],
) -> tuple[np.ndarray, np.ndarray, np.ndarray] | None:
    key = id(buildings)
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


# Cache for per-buildings-list metadata (group_starts and building_idx_for_group)
# so the same grouping work isn't repeated for every offset candidate.
_GROUP_CACHE: dict[int, tuple[np.ndarray, np.ndarray]] = {}


def _get_group_arrays_cached(
    buildings: Sequence[BuildingRecord],
) -> tuple[np.ndarray, np.ndarray] | None:
    """Return (group_starts, building_idx_for_group) for the packed vertex
    arrays. Cached per buildings-list identity. group_starts gives the first
    vertex index of each building's contiguous run; building_idx_for_group
    gives the building index that each entry corresponds to.
    """
    key = id(buildings)
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
    in_frustum = (forward > 5.0) & (distance <= 4000.0) & (np.abs(bearing) <= half_fov_rad)
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
        predicted[xL : xR + 1] = True
    return predicted


def _score_offset_semantic_iou(
    buildings: Sequence[BuildingRecord],
    viewpoint: Viewpoint,
    offset_deg: float,
    building_mask: np.ndarray,
    water_mask: "np.ndarray | None" = None,
) -> float:
    """Width-weighted semantic-alignment score in [-1, 1] (clamped ≥ 0).

    The score is built from three terms aggregated across all in-frustum OSM
    buildings, each projected to its [x_min, x_max] image-column range:

      hit_cols   = Σ_b (predicted-cols ∩ building-dominant-mask-cols)
      water_cols = Σ_b (predicted-cols ∩ water-dominant-mask-cols)
      pred_cols  = Σ_b width_b
      placement  = (hit_cols - water_cols) / pred_cols
                 → fraction of "OSM ink" landing on building, net of water hits

      miss = unmatched mask-building-cols / total mask-building-cols
                 → fraction of observed skyline that the OSM cone DOESN'T cover

      score = placement - 0.3 * miss

    Why this formulation:
      - **Width-weighting** by per-building projected width replaces an
        equal-weight average; a 50-col Bocagrande tower properly outweighs a
        1-col distant warehouse, so the right offset doesn't get dragged down
        by misaligned tiny buildings.
      - **Net water-penalty**: putting OSM ink directly into observed water is
        the clearest single signal of a wrong heading on a maritime view.
        Subtracted in the same units as placement (column-counts).
      - **Miss penalty**: an offset that lands all predicted buildings in
        valid mask columns but rotates the cone such that 60% of the OBSERVED
        skyline is unexplained is still wrong. The miss term catches that.
        Weighted 0.3 because directional gross-miss is real but a smaller
        signal than placement; we don't want a half-occluded right offset to
        score worse than a fully-pointed-wrong offset that happens to cover
        the visible skyline by accident.
    """
    if building_mask is None or building_mask.size == 0:
        return 0.0
    bmask = np.asarray(building_mask)
    h_img, w = bmask.shape[:2]

    x_min, x_max = _projected_building_x_ranges(buildings, viewpoint, offset_deg, w)
    if x_min.size == 0:
        return 0.0

    build_per_col = bmask.sum(axis=0)
    if water_mask is not None and water_mask.size > 0:
        water_per_col = np.asarray(water_mask).sum(axis=0)
    else:
        water_per_col = np.zeros(w, dtype=np.int64)
    is_building_col = (build_per_col > water_per_col) & (build_per_col > 5)
    is_water_col = (water_per_col > build_per_col) & (water_per_col > 5)

    # Build a flat "predicted" mask once and accumulate hit/water/pred totals
    # by iterating the per-building ranges (preserving width-weighting).
    predicted = np.zeros(w, dtype=bool)
    hit_cols = 0
    water_cols = 0
    pred_cols = 0
    for xL, xR in zip(x_min.tolist(), x_max.tolist()):
        width = xR - xL + 1
        if width <= 0:
            continue
        pred_cols += width
        seg_b = is_building_col[xL : xR + 1]
        seg_w = is_water_col[xL : xR + 1]
        hit_cols += int(np.count_nonzero(seg_b))
        water_cols += int(np.count_nonzero(seg_w))
        predicted[xL : xR + 1] = True

    if pred_cols == 0:
        return 0.0

    placement = (hit_cols - water_cols) / float(pred_cols)

    # Miss term: fraction of observed-skyline-columns that the OSM cone fails
    # to cover. The mask itself can have spurious thin building columns; only
    # apply the miss term when there's a non-trivial amount of building in
    # view (≥ 5% of frame width).
    n_observed = int(np.count_nonzero(is_building_col))
    if n_observed >= max(20, int(0.05 * w)):
        unmatched_observed = int(np.count_nonzero(is_building_col & ~predicted))
        miss = unmatched_observed / float(n_observed)
    else:
        miss = 0.0

    score = placement - 0.3 * miss
    return max(0.0, min(1.0, score))


def register_view_to_osm(
    captured: CapturedView,
    buildings: Sequence[BuildingRecord],
    heading_search_deg: float,
    heading_step_deg: float,
    min_matches: int = 3,
    max_match_px: float = 30.0,
    coarse_search_deg: float | None = None,
    coarse_step_deg: float = 10.0,
    forced_center_deg: float | None = None,
    use_semantic_iou: bool = True,
) -> dict:
    """Register an image to OSM by finding the heading offset that best aligns
    projected building x positions with detected skyline peaks.

    Photo Sphere panos served by Google's Static API have an arbitrary internal
    coordinate frame — the API's ``heading`` parameter is NOT geographic for
    them — so the offset between API heading and geographic compass can be
    anything in [0, 360°). When ``coarse_search_deg`` is set we first sweep
    that wide range at ``coarse_step_deg`` to find the right ballpark, then
    refine within ±``heading_search_deg`` at ``heading_step_deg``.

    For road panos pass coarse_search_deg=None — they are already geographic-
    aligned and the cheap ±15° fine search is sufficient.
    """
    contour, sky_mask = detect_skyline_contour(captured.image)
    # Extract peaks inline (median-filtered inverted contour → local minima = rooftops)
    _c_inv = -median_filter(np.asarray(contour, dtype=np.float32), size=7, mode="nearest")
    observed_peaks, _ = find_peaks(
        _c_inv, distance=18, prominence=max(3.0, float(np.std(_c_inv)) * 0.15)
    )
    observed_peaks = observed_peaks.astype(np.int32)
    buildings_by_id = {b.feature_id: b for b in buildings}

    # Pull the neural masks once — they're the substrate for the semantic
    # mIoU objective. When the model is unavailable we fall back to the legacy
    # pixel-residual score (worse on panoramas, but functional).
    _, building_mask_neural = _neural_sky_and_building_masks(captured.image)
    water_mask_neural = _neural_water_mask(captured.image)
    has_mask = use_semantic_iou and building_mask_neural is not None

    def _score_offset(offset: float):
        """Return (combined_score, mean_resid_px, iou, culled, matches).

        Lower combined_score = better fit. When the neural building mask is
        available the objective is dominated by (1 - mIoU): a wrong offset
        puts predicted buildings into sky/water columns and the IoU tanks,
        whereas peak residuals are nearly always low because some peaks land
        within ±15 px of any projection on a panoramic skyline.

        Uses ``_project_all_buildings_vectorized`` for the projection step —
        ~100× faster than the per-building Python loop (which dominated
        runtime before vectorisation).
        """
        projected = _project_all_buildings_vectorized(
            buildings, captured.viewpoint, offset)
        if len(projected) < min_matches or observed_peaks.size < min_matches:
            return float("inf"), float("inf"), 0.0, [], []
        culled = _cull_occluded_projections(
            projected, buildings_by_id, captured.viewpoint.image_width)
        matches, mean_resid = _match_projections_to_peaks(
            culled, observed_peaks, max_match_px=max_match_px)
        if len(matches) < min_matches:
            return float("inf"), float("inf"), 0.0, [], []
        if has_mask:
            iou = _score_offset_semantic_iou(
                buildings, captured.viewpoint, offset,
                building_mask_neural, water_mask_neural)
            combined = (1.0 - iou) * 100.0 + mean_resid * 0.1
            return combined, mean_resid, iou, culled, matches
        return mean_resid, mean_resid, 0.0, culled, matches

    # When a seed-level anchor is supplied, skip the coarse pass entirely and
    # search only near the anchor. This forces all 12 spin views of a Photo
    # Sphere seed to share one consistent pano-to-geographic offset (the
    # matcher otherwise finds different "best" offsets per view because the
    # panorama has many similar towers and locally any offset can score okay).
    center_deg = 0.0
    if forced_center_deg is not None:
        center_deg = float(forced_center_deg)
    elif coarse_search_deg and coarse_search_deg > heading_search_deg:
        # Phase 1: coarse calibration sweep. Finds the ballpark pano-to-
        # geographic offset within ~coarse_step_deg.
        coarse_offsets = np.arange(
            -coarse_search_deg, coarse_search_deg + 0.001, coarse_step_deg)
        coarse_best_score = float("inf")
        for offset in coarse_offsets:
            cscore, *_ = _score_offset(float(offset))
            if cscore < coarse_best_score:
                coarse_best_score = cscore
                center_deg = float(offset)

    # Phase 2: fine refinement within ±heading_search_deg around the coarse best.
    fine_offsets = np.arange(
        center_deg - heading_search_deg,
        center_deg + heading_search_deg + 0.001,
        heading_step_deg,
    )
    best_offset = 0.0
    best_combined = float("inf")
    best_resid = float("inf")
    best_iou = 0.0
    best_projections: list[dict] = []
    best_matches: list[tuple[int, int]] = []
    for offset in fine_offsets:
        combined, resid, iou, culled, matches = _score_offset(float(offset))
        if combined < best_combined:
            best_combined = combined
            best_resid = resid
            best_iou = iou
            best_offset = float(offset)
            best_projections = culled
            best_matches = matches
    # Report reg_score as the pixel residual (interpretable, comparable across
    # views) but the offset selection uses the combined objective.
    best_score = best_resid

    matched_projections: list[dict] = []
    if best_matches:
        for r, c in best_matches:
            entry = dict(best_projections[r])
            entry["matched_peak_x"] = int(observed_peaks[c])
            entry["match_residual_px"] = abs(
                entry["x_px"] - float(observed_peaks[c]))
            matched_projections.append(entry)

    return {
        "contour": contour,
        "sky_mask": sky_mask,
        "observed_peaks": observed_peaks,
        "best_offset": best_offset,
        "best_score": best_score,           # mean pixel residual (legacy display)
        "best_iou": best_iou,                # semantic mIoU at best offset
        "best_combined_score": best_combined,
        "projections": matched_projections,
        "all_projections": best_projections,  # all culled-visible buildings at best offset
        "n_matches": len(matched_projections),
    }


def _building_roof_y_from_mask(
    building_mask: "np.ndarray | None",
    x_px: int,
    half_width: int = 4,
    min_run_px: int = 3,
) -> int | None:
    """Return the topmost row whose neighborhood around x_px is dominated by
    building-class pixels.

    Compared to reading the global skyline contour at ``x_px``, this is robust
    to clouds, lampposts, or anything else that ``detect_skyline_contour`` may
    classify as non-sky: only pixels labelled as ADE20K *building* count, so a
    cloudy bay view returns None (no roof here) instead of a cloud edge y.

    Returns the y of the first pixel in a vertical run of ``min_run_px``
    building-majority rows, or None when no building presence is found.
    """
    if building_mask is None:
        return None
    h, w = building_mask.shape[:2]
    if x_px < 0 or x_px >= w:
        return None
    x0 = max(0, x_px - half_width)
    x1 = min(w, x_px + half_width + 1)
    slab = building_mask[:, x0:x1].astype(np.uint8)
    if slab.size == 0:
        return None
    row_counts = slab.sum(axis=1)
    threshold = max(1, (x1 - x0) // 2 + 1)  # majority of sampled columns
    in_run = 0
    for y in range(h):
        if row_counts[y] >= threshold:
            in_run += 1
            if in_run >= min_run_px:
                return y - min_run_px + 1
        else:
            in_run = 0
    return None


def estimate_heights_from_registration(
    captured: CapturedView,
    registration: dict,
    buildings: Sequence[BuildingRecord],
    camera_height_m: float,
    camera_elev_m: float = 0.0,
) -> list[RegisteredBuildingEstimate]:
    contour = np.asarray(registration["contour"], dtype=np.float32)
    best_offset = float(registration["best_offset"])
    best_score = float(registration.get("best_score", float("inf")))
    # Use all culled-visible projections (not just peak-matched) to maximise yield.
    all_proj_list = registration.get("all_projections") or registration["projections"]
    projections = {item["feature_id"]: item for item in all_proj_list}
    # Track which buildings were peak-matched for confidence scoring.
    matched_ids = {item["feature_id"] for item in registration["projections"]}
    f_px = _focal_length_px(captured.viewpoint)
    cy = captured.viewpoint.image_height * 0.5
    cam_z = camera_elev_m + camera_height_m

    # Pull the cached neural building mask for this image. When available it's
    # the authoritative source for "is there a building at this column?": the
    # global skyline contour treats clouds, lampposts, and tree canopies as
    # not-sky and reports an inflated roof y, but those pixels are not in the
    # ADE20K building class. With the mask we can:
    #   - sample the roof y from the mask directly (per-column, robust to clouds)
    #   - drop the estimate when no building pixels exist in this column
    _, building_mask = _neural_sky_and_building_masks(captured.image)

    # Cap residual contribution to confidence: residuals well above ~25 px are
    # essentially noise.
    score_norm = float(np.clip(1.0 - min(best_score, 25.0) / 25.0, 0.05, 1.0))

    # Pre-compute (x_px, forward_m) arrays for the closest-in-column-bin check.
    # When several buildings project into the same image column, the visible
    # rooftop pixel belongs to the *closest* one — crediting a far building for
    # that pixel is the failure mode that produces b0151 143m→284m (tag→pred).
    if all_proj_list:
        _all_x = np.asarray([p["x_px"] for p in all_proj_list], dtype=np.float32)
        _all_fwd = np.asarray([p["forward_m"] for p in all_proj_list], dtype=np.float32)
    else:
        _all_x = np.empty(0, dtype=np.float32)
        _all_fwd = np.empty(0, dtype=np.float32)

    estimates: list[RegisteredBuildingEstimate] = []
    for building in buildings:
        proj = projections.get(building.feature_id)
        if not proj:
            continue

        x_px = int(round(proj["x_px"]))
        if x_px < 0 or x_px >= contour.size:
            continue

        # Closest-in-column-bin gate: if another projection within ±15 px of
        # this x is significantly closer (>200 m), the visible rooftop pixel
        # belongs to that closer building, not this one. Skip this estimate.
        # 200 m threshold preserves credible cross-distance distinctions (a
        # distant tall tower CAN still be credited if no near building shares
        # its column) while killing the "far thin building credited for a
        # near tall roof" pattern.
        forward = float(proj["forward_m"])
        if forward <= 1.0:
            continue
        if _all_x.size:
            nearby = np.abs(_all_x - float(proj["x_px"])) <= 15.0
            if nearby.any():
                closest_in_bin = float(_all_fwd[nearby].min())
                if forward > closest_in_bin + 200.0:
                    continue

        # Footprint-driven roof sampling: use the FULL projected x-range of
        # this building's footprint instead of just the centroid column. A
        # narrow building gets a tight column band; a wide one gets a wide
        # one. The footprint also gives us a coverage check — if < 25 % of
        # the projected range has building-mask pixels, this building is
        # occluded or off-frame, skip the estimate.
        x_range = _building_projected_x_range(
            building, captured.viewpoint, best_offset, contour.size)
        if x_range is not None:
            xL, xR = x_range
            roof_y_mask, coverage = _footprint_roof_y_from_mask(
                building_mask, xL, xR)
            if building_mask is not None and roof_y_mask is None:
                # No building pixels in the projected column range.  Could be
                # a glass/reflective tower (SegFormer labels the spire as sky),
                # a building that is too distant/thin to exceed the 8-row
                # threshold, or a genuinely occluded structure.  Fall through
                # to the contour-y path rather than discarding the building
                # entirely — the contour gives a reasonable rooftop estimate
                # and the closest-in-bin gate handles occlusion.
                pass
        else:
            # No polygon vertices in FOV — fall back to centroid sampling.
            roof_y_mask = _building_roof_y_from_mask(building_mask, x_px)
            if building_mask is not None and roof_y_mask is None:
                pass  # fall through to contour-y as above
            coverage = 0.0

        if roof_y_mask is not None:
            y_px = float(roof_y_mask)
            # Glass-facade override: SegFormer's building class often stops
            # 20-40 px below the actual rooftop on reflective glass/curtain-
            # wall spires because the top of the tower reflects sky and gets
            # labelled as sky. When the sky/non-sky CONTOUR is significantly
            # higher than the mask roof at this building's column, AND the
            # gap looks like a continuous tower silhouette (not a separate
            # foreground object), prefer the contour y.
            if x_range is not None:
                xL, xR = x_range
                cxL = max(0, int(xL))
                cxR = min(contour.size - 1, int(xR))
                if cxR >= cxL:
                    contour_slice = contour[cxL : cxR + 1]
                    finite = contour_slice[np.isfinite(contour_slice)]
                    if finite.size > 0:
                        # Use a representative high point — the 20th percentile
                        # of contour y over the building's projected column
                        # range (avoids being thrown by a single noise pixel
                        # above the spire).
                        contour_top_y = float(np.percentile(finite, 20))
                        gap = y_px - contour_top_y
                        if gap > 15.0:
                            # Sanity-check the gap: it should be plausible as
                            # additional tower height at this distance. A
                            # gap > what a 300 m tower could project is
                            # noise (cloud edge, lamppost), not glass roof.
                            pitch_rad = math.radians(captured.viewpoint.pitch)
                            angle_at_contour = math.atan(
                                (cy - contour_top_y) / f_px) + pitch_rad
                            top_at_contour = forward * math.tan(angle_at_contour)
                            implied_h = cam_z + top_at_contour - float(
                                building.terrain_elev_m)
                            if 0.0 <= implied_h <= 300.0:
                                y_px = contour_top_y
        else:
            y_px = float(contour[x_px])
            if not np.isfinite(y_px):
                continue

        pitch_rad = math.radians(captured.viewpoint.pitch)
        angle_rad = math.atan((cy - y_px) / f_px) + pitch_rad
        top_above_camera = forward * math.tan(angle_rad)
        # Height of the building above its own base (ground at footprint):
        # top_z = cam_z + forward*tan(angle); height_above_base = top_z - bld_terrain_z
        height_m = cam_z + top_above_camera - float(building.terrain_elev_m)
        if not np.isfinite(height_m):
            continue

        # Geometric y-consistency gate: even before invoking the per-building
        # height proxy, check that the sampled roof_y is geometrically
        # plausible for *any* building at this distance. The hard ceiling is
        # 300 m (Cartagena's tallest tower is ~206 m); the floor is 2 m. If
        # the column says the roof is higher in the image than a 300 m tower
        # could possibly project to this distance, the column actually
        # belongs to a CLOSER building — drop the credit. This catches the
        # "far thin OSM building credited for a near tall building's roof"
        # pattern that the closest-in-column-bin gate only partly catches
        # (it misses cases where the closer building is just outside the
        # ±15 px window because its centroid is far from the wide segment).
        max_plausible_top = 300.0  # global building height ceiling, metres
        min_plausible_top = 2.0
        max_top_angle = math.atan(
            (max_plausible_top - cam_z - float(building.terrain_elev_m)) / forward)
        min_top_angle = math.atan(
            (min_plausible_top - cam_z - float(building.terrain_elev_m)) / forward)
        # Convert angle bounds back to y_px bounds (inverted: smaller y = higher).
        min_y_for_building = cy - f_px * math.tan(max_top_angle - pitch_rad)
        max_y_for_building = cy - f_px * math.tan(min_top_angle - pitch_rad)
        # Allow ±5 px slack for rounding and mask quantisation.
        if y_px < min_y_for_building - 5 or y_px > max_y_for_building + 5:
            continue

        # Plausibility cap: only enforce the strict 5× window when we have a
        # TAGGED height (a strong constraint). For untagged buildings, the
        # area-derived proxy is at best loosely correlated with height (a tall
        # thin tower can have small footprint), so we rely on the geometric
        # y-consistency gate above (300 m absolute ceiling) and a lighter
        # sanity check (under 8 m on a proxy-meaningful footprint suggests a
        # missed match, e.g. far building credited for a closer tower's roof).
        pred_capped = max(height_m, 0.0)
        if building.height_tag_m is not None:
            tag_h = float(building.height_tag_m)
            if tag_h >= 3.0 and (pred_capped > tag_h * 5.0 or pred_capped < tag_h * 0.20):
                continue
        else:
            # Loose check: predictions under ~3 m for a >50 m² footprint
            # almost always indicate roof_y was sampled in water/sky rather
            # than on the building.
            if building.area_m2 > 50.0 and pred_capped < 3.0:
                continue

        # Unmatched buildings (not locked to a skyline peak): assign a
        # moderate default residual so their confidence is lower but nonzero.
        if building.feature_id in matched_ids:
            x_err = float(proj.get("match_residual_px",
                          abs(proj["x_px"] - float(x_px))))
        else:
            x_err = 15.0  # ~0.375 x_err_score for unmatched buildings
        x_err_score = max(0.1, 1.0 - x_err / 24.0)
        tag_score = 1.0 if building.height_tag_m is not None else 0.7
        forward_score = float(
            np.clip(1.0 - max(0.0, forward - 3000.0) / 3000.0, 0.3, 1.0))
        confidence = float(np.clip(score_norm * x_err_score *
                           tag_score * forward_score, 0.05, 1.0))

        estimates.append(
            RegisteredBuildingEstimate(
                feature_id=building.feature_id,
                name=building.name,
                view_name=captured.viewpoint.name,
                heading_offset_deg=best_offset,
                x_px=float(proj["x_px"]),
                y_px=y_px,
                forward_m=forward,
                estimated_height_m=float(max(0.0, height_m)),
                confidence=confidence,
            )
        )

    return estimates


def _seed_from_view_name(view_name: str) -> str:
    """Extract seed identifier from a view_name like 'seed_1_321' -> 'seed_1'."""
    parts = view_name.rsplit("_", 1)
    if len(parts) == 2 and parts[1].isdigit():
        return parts[0]
    return view_name


def aggregate_building_heights(estimates: Sequence[RegisteredBuildingEstimate]) -> list[dict]:
    grouped: dict[str, list[RegisteredBuildingEstimate]] = {}
    for estimate in estimates:
        grouped.setdefault(estimate.feature_id, []).append(estimate)

    output: list[dict] = []
    for feature_id, items in grouped.items():
        heights = np.asarray(
            [item.estimated_height_m for item in items], dtype=np.float32)
        confidences = np.asarray(
            [item.confidence for item in items], dtype=np.float32)
        if heights.size == 0:
            continue

        # Per-seed grouping for cross-seed agreement and outlier downweighting.
        per_seed_items: dict[str, list[RegisteredBuildingEstimate]] = {}
        for item in items:
            seed_name = _seed_from_view_name(item.view_name)
            per_seed_items.setdefault(seed_name, []).append(item)
        per_seed_median = {
            s: float(np.median([it.estimated_height_m for it in seed_its]))
            for s, seed_its in per_seed_items.items()}

        # Outlier-seed downweighting: when ≥ 3 seeds disagree, find seeds
        # whose per-seed median is > 1.5 × MAD from the overall seed-median
        # median, and zero their contribution. This rejects an outlier seed
        # entirely (e.g. one bad heading anchor) rather than letting it pull
        # the aggregate. For 2 seeds we can't tell which is the outlier, so
        # we leave both in.
        outlier_seeds: set[str] = set()
        if len(per_seed_median) >= 3:
            vals = np.asarray(list(per_seed_median.values()), dtype=np.float32)
            seed_median_overall = float(np.median(vals))
            seed_mad = float(np.median(np.abs(vals - seed_median_overall)))
            if seed_mad >= 1.0:  # only meaningful with non-trivial spread
                threshold = 1.5 * seed_mad
                for s, m in per_seed_median.items():
                    if abs(m - seed_median_overall) > threshold:
                        outlier_seeds.add(s)

        # Build the effective items/heights/confidences (excluding outliers)
        # for the aggregate. Keep the raw per-seed metadata for diagnostics.
        eff_items = [
            it for it in items
            if _seed_from_view_name(it.view_name) not in outlier_seeds
        ]
        if not eff_items:
            eff_items = list(items)  # all were outliers — fall back to raw
        eff_heights = np.asarray(
            [it.estimated_height_m for it in eff_items], dtype=np.float32)
        eff_confidences = np.asarray(
            [it.confidence for it in eff_items], dtype=np.float32)

        median = float(np.median(eff_heights))
        spread = float(np.median(np.abs(eff_heights - median)))
        weighted = float(np.average(
            eff_heights, weights=np.maximum(eff_confidences, 1e-3)))

        if len(per_seed_median) >= 2:
            vals = np.asarray(list(per_seed_median.values()), dtype=np.float32)
            seed_disagreement_m = float(np.max(vals) - np.min(vals))
            seed_std_m = float(np.std(vals))
        else:
            seed_disagreement_m = 0.0
            seed_std_m = 0.0

        output.append(
            {
                "feature_id": feature_id,
                "name": items[0].name,
                "n_views": int(heights.size),
                "n_seeds": len(per_seed_items),
                "n_outlier_seeds": len(outlier_seeds),
                "outlier_seeds": sorted(outlier_seeds),
                "median_height_m": median,
                "weighted_height_m": weighted,
                "mad_m": spread,
                "mean_confidence": float(np.mean(eff_confidences)),
                "per_seed_median_m": per_seed_median,
                "seed_disagreement_m": seed_disagreement_m,
                "seed_std_m": seed_std_m,
                "views": [
                    {
                        "view_name": item.view_name,
                        "height_m": item.estimated_height_m,
                        "confidence": item.confidence,
                        "heading_offset_deg": item.heading_offset_deg,
                    }
                    for item in items
                ],
            }
        )

    output.sort(
        key=lambda row: (-row["n_seeds"], -row["n_views"], -row["median_height_m"]))
    return output

