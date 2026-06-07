"""Skyline-based building-height estimation — pure CV / geometry primitives.

This module is the "math layer" of skyline_cv. It is intentionally free of
HTTP I/O, matplotlib, and Street View concerns so its functions can be
unit-tested without API keys. The orchestration that ties these primitives
to a real region run lives in ``region_pdf.py``.

Public surface (in dependency order):

  Dataclasses
    Viewpoint                       — camera pose (lat/lon/heading/pitch/fov)
    BuildingRecord                  — OSM building + tagged/derived height proxy
    CapturedView                    — RGB image + Viewpoint
    RegisteredBuildingEstimate      — per-view per-building height estimate

  SegFormer integration
    _neural_sky_and_building_masks  — anchored-LRU-cached forward pass
    _neural_water_mask              — water-class accessor (same cache)

  Skyline detection
    detect_skyline_contour          — top-of-non-sky row per column
    detect_building_silhouettes     — contour-peak silhouettes (per-tower)
    detect_buildings_from_mask      — connected-component silhouettes from mask
    compute_building_band           — vertical band where buildings exist
    _merge_silhouette_sources       — IoU-based de-dup of two silhouette lists

  OSM projection + registration
    _project_building               — single-building rectilinear projection
    _project_all_buildings_vectorized — batched numpy projection (the perf win)
    _projected_building_x_ranges    — per-building (x_min, x_max) for IoU scoring
    register_view_to_osm            — find pano-to-geo heading offset per view
    _score_offset_semantic_iou      — the offset objective
                                       (per-building IoU − water − miss penalty)

  Matching + culling
    _cull_occluded_projections      — keep only the closest-per-bin building
    osm_anchor_silhouettes          — F-SKY2 anchored split of merged segments
    match_segments_to_buildings     — interval-IoU + width-ratio scorer
    (F-SKY3 / osm_marker_voronoi_silhouettes removed 2026-05-18 after a
     measured MAE regression; see docs/plans/F-SKY3-osm-marker-instances.md)
    osm_sam_instance_silhouettes    — F-SKY5 MobileSAM instance head
                                       (optional; no-op if MobileSAM not installed)

  Pano helpers
    stitch_pano_views               — stitch RGB strip from spin views
    stitch_pano_masks               — stitch per-view masks (faster, seam-free)
    project_buildings_to_pano       — bearing-to-column projection for pano

  Height extraction
    estimate_heights_from_registration — per-view pinhole-y → height_m
    aggregate_building_heights      — per-building median + outlier downweighting
    _floor_period_for_building      — F-SKY1 facade-period diagnostic
                                       (OSM-independent height + distance check)

See STATUS.md for what currently works and what doesn't. Hard dependency on
SegFormer-b0 (ADE20K) via the `transformers` library — the registration
objective and per-building mask sampling both rely on it; no fallback path
is intended to be functional without the model.
"""

from __future__ import annotations
from collections import OrderedDict as _OrderedDict

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

# ── SegFormer-b0 (ADE20K) — lazy-loaded neural sky/building segmentation ─────
# ADE20K 0-based label indices:
#   1 = building;edifice
#   2 = sky
#  21 = water (lake/sea/ocean)
#  26 = sea
#  60 = river
# We treat any of {21, 26, 60} as "water" for the mIoU water co-objective.
_ADE20K_SKY: int = 2
# Building classes: 1 (building/edifice), 25 (house), 48 (skyscraper).
# Tall glass towers on Cartagena's Bocagrande get labelled by SegFormer-b3
# as "skyscraper" (48), NOT "building" (1) — so the unioned mask above
# captures them instead of leaving black holes in the per-view overlay.
# 25 (house) added for the short residential blocks south of the bay.
_ADE20K_BUILDING_CLASSES: tuple[int, ...] = (1, 25, 48)
# Backwards-compat alias: existing call sites that read _ADE20K_BUILDING as
# a scalar still see the primary class. Treat the tuple as the source of
# truth in mask construction.
_ADE20K_BUILDING: int = _ADE20K_BUILDING_CLASSES[0]
_ADE20K_WATER_CLASSES: tuple[int, ...] = (21, 26, 60)
# Vegetation classes (F-SKY18 bearing landmarks): tree=4, grass=9, plant=17,
# field=29. Field added because SegFormer routinely labels low coastal
# grass / esplanade plantings as "field" instead of "grass".
_ADE20K_VEGETATION_CLASSES: tuple[int, ...] = (4, 9, 17, 29)
def _segformer_model_id() -> str:
    """Resolve the SegFormer model ID at first inference.

    Default is ``b3`` (flipped from ``b0`` 2026-05-16 after measurement —
    on Cartagena b3 doubled the matched-tagged-building count (n=8 → 17)
    and dropped MAE 22.13 → 13.73, with cross-seed bias collapsing from
    +20.30 m to +0.87 m). Set env var ``SKYLINE_CV_SEGFORMER_SIZE`` to
    any of ``b0`` … ``b5`` to override. ``b0`` is still useful for fast
    iteration (~3 min vs b3's ~5–6 min on Cartagena). Larger variants
    improve building/sky mask quality on reflective-glass spires and
    shadowed tower tops that smaller models classify as sky. The first
    call downloads the weights to HuggingFace's local cache; subsequent
    runs reuse them.
    """
    size = os.environ.get("SKYLINE_CV_SEGFORMER_SIZE", "b3").strip().lower()
    if size not in {"b0", "b1", "b2", "b3", "b4", "b5"}:
        size = "b3"
    return f"nvidia/segformer-{size}-finetuned-ade-512-512"


def _segformer_input_size() -> int:
    """Resolve the SegFormer processor input resolution (square).

    Inference cost on a transformer scales roughly with the *pixel count*
    of the input, so dropping from 512² to 384² is ~44% fewer pixels and
    ~1.8× speedup; 320² is ~61% fewer pixels and ~2.5× speedup. The
    accuracy floor is the inter-tower gap width: at 320 px, a 3-pixel-wide
    sky strip between two close towers becomes ~2 px and may merge.
    Default 512 (the size the model was finetuned at). Override with
    ``SKYLINE_CV_SEGFORMER_INPUT_SIZE`` (integer ≥ 128).
    """
    raw = os.environ.get("SKYLINE_CV_SEGFORMER_INPUT_SIZE", "512").strip()
    try:
        n = int(raw)
    except ValueError:
        return 512
    return max(128, n)


_SEGFORMER_MODEL_ID: str = _segformer_model_id()
_SEGFORMER_LOADED: bool = False
_SEGFORMER_OK: bool = False
_segformer_processor = None
_segformer_model = None
# LRU cache keyed by id(image_array): avoids double inference when
# detect_skyline_contour, detect_building_silhouettes, register_view_to_osm,
# and the joint optimizer all want the same image's masks. Capacity sized
# for one seed's spin views (12) + a few headroom slots.
_NEURAL_CACHE_CAPACITY = 64
_neural_cache: "_OrderedDict[int, dict]" = _OrderedDict()

# ── MobileSAM (optional) — instance head for merged-blob splitting (F-SKY5) ───
# Checkpoint path: env var MOBILESAM_CHECKPOINT_PATH or ~/.cache/mobile_sam/vit_t.pth
# Install: pip install git+https://github.com/ChaoningZhang/MobileSAM.git
# Graceful no-op when package or checkpoint is absent.
_MOBILESAM_LOADED: bool = False
_MOBILESAM_OK: bool = False
_mobilesam_predictor = None


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
    # F-SKY1 floor-period diagnostics. All optional; populated only when the
    # building's facade has detectable horizontal floor banding (a clean
    # autocorrelation peak in the mask-band row-mean luminance). These are
    # OSM-independent: floor_period_px lets us back out distance via the
    # inverse pinhole using an assumed 3.2 m floor height, and floor count
    # × floor height gives an independent height estimate. See
    # docs/plans/F-SKY1-floor-periodicity.md.
    floor_period_px: float | None = None
    floor_confidence: float | None = None
    inferred_distance_m: float | None = None
    inferred_height_m: float | None = None
    # F-SKY12 depth-from-pano diagnostics. Populated when
    # ``augment_estimates_with_depth`` is called on a view's estimates.
    # ``depth_height_m`` is an independent height estimate from Depth
    # Anything V2 at the silhouette-top pixel, calibrated to metres using
    # the matched OSM building distances as anchors. ``depth_disagreement``
    # is True when |estimated_height_m - depth_height_m| / max(...) > 0.4.
    # Phase A: pure diagnostic, no impact on aggregated heights. See
    # docs/plans/F-SKY12-depth-from-panos.md.
    depth_height_m: float | None = None
    depth_disagreement: bool | None = None


def augment_estimates_with_depth(
    image_rgb: np.ndarray,
    estimates: list[RegisteredBuildingEstimate],
    viewpoint: "Viewpoint",
    camera_height_m: float = 1.7,
) -> list[RegisteredBuildingEstimate]:
    """F-SKY12: augment per-view estimates with depth-derived heights.

    Runs Depth Anything V2 once on the view image, calibrates relative
    depth to metres using each estimate's ``forward_m`` as an anchor at
    its ``(x_px, y_px)``, and computes a second height estimate at the
    silhouette-top pixel via pinhole geometry. Sets ``depth_height_m``
    and ``depth_disagreement`` on each returned estimate.

    Heavy: DA2 inference is ~1–2 s on CPU. Callers should only invoke
    this when the per-view PDF page needs the diagnostic. Returns a new
    list (RegisteredBuildingEstimate is frozen); on failure returns the
    input unchanged so the rest of the pipeline is untouched.
    """
    if not estimates:
        return estimates

    try:
        from city2stl.skyline_cv.depth_estimation import (  # noqa: PLC0415
            calibrate_pano_depth,
            compare_heights,
            depth_height_from_segment,
            predict_pano_depth,
        )
    except ImportError as exc:
        logger.warning("F-SKY12 depth module unavailable: %s", exc)
        return estimates

    try:
        depth_rel = predict_pano_depth(image_rgb)
    except Exception as exc:
        logger.warning("F-SKY12 DA2 inference failed: %s", exc)
        return estimates

    # Build anchors from the estimates: (row, col, distance_m). Use the
    # silhouette-top pixel as the sample location and forward_m as the
    # known distance to the matched building.
    anchors: list[tuple[int, int, float]] = []
    for est in estimates:
        if est.forward_m > 0.0 and 0.0 <= est.y_px and 0.0 <= est.x_px:
            anchors.append(
                (int(round(est.y_px)), int(round(est.x_px)), float(est.forward_m))
            )
    if not anchors:
        return estimates

    try:
        depth_m = calibrate_pano_depth(depth_rel, anchors)
    except ValueError as exc:
        logger.warning("F-SKY12 depth calibration failed: %s", exc)
        return estimates

    fy = _focal_length_px(viewpoint)
    cy = float(viewpoint.image_height) / 2.0

    out: list[RegisteredBuildingEstimate] = []
    for est in estimates:
        h_depth = depth_height_from_segment(
            depth_m,
            (int(round(est.x_px)), int(round(est.y_px))),
            fy=fy,
            cy=cy,
            camera_height_m=camera_height_m,
        )
        disagree = compare_heights(est.estimated_height_m, h_depth)
        out.append(
            replace(est, depth_height_m=float(h_depth), depth_disagreement=bool(disagree))
        )
    return out


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


# Public alias for the equirectangular local-metres projection. Equirectangular
# is exact enough for ≤ a few km from the origin and is used uniformly across
# skyline_cv (minimap rendering, registration sweeps, OSM coastline clipping).
# Prefer this over reimplementing the projection in new modules.
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


_segformer_device: str = "cpu"


def _ensure_segformer() -> bool:
    """Lazily load SegFormer-b0 (ADE20K). Returns True if the model is ready.

    Moves the model to CUDA when available (single forward pass is dominated
    by GPU transfer + transformer math; on Cartagena we measured ~2 s/image
    on CPU vs ~0.2-0.4 s/image on a consumer GPU). Override the device with
    the ``SKYLINE_CV_SEGFORMER_DEVICE`` env var (e.g. ``cpu`` to force CPU
    on a machine with a busy GPU).
    """
    global _SEGFORMER_LOADED, _SEGFORMER_OK, _segformer_processor, _segformer_model, _segformer_device
    if _SEGFORMER_LOADED:
        return _SEGFORMER_OK
    _SEGFORMER_LOADED = True
    try:
        import torch  # noqa: PLC0415
        from transformers import (  # noqa: PLC0415
            SegformerForSemanticSegmentation,
            SegformerImageProcessor,
        )
        _segformer_processor = SegformerImageProcessor.from_pretrained(
            _SEGFORMER_MODEL_ID)
        _segformer_model = SegformerForSemanticSegmentation.from_pretrained(
            _SEGFORMER_MODEL_ID)
        _segformer_model.eval()  # type: ignore[union-attr]
        # Override the processor's internal resize so a smaller input
        # square reduces the per-forward-pass cost. We touch both the
        # ``size`` dict (canonical for new transformers) AND ``do_resize``
        # so the processor actually performs the resize before it hands
        # tensors to the model.
        _input_n = _segformer_input_size()
        try:
            _segformer_processor.size = {"height": _input_n, "width": _input_n}
            _segformer_processor.do_resize = True
        except Exception as _e_size:
            print(f"[segformer] failed to set input size to {_input_n}: {_e_size}")
        # Device selection: respect the override env var, otherwise pick CUDA
        # if it's available, falling back to CPU.
        override = os.environ.get("SKYLINE_CV_SEGFORMER_DEVICE", "").strip().lower()
        if override:
            _segformer_device = override
        elif torch.cuda.is_available():
            _segformer_device = "cuda"
        else:
            _segformer_device = "cpu"
        try:
            _segformer_model.to(_segformer_device)  # type: ignore[union-attr]
            print(f"[segformer] device={_segformer_device}  model={_SEGFORMER_MODEL_ID.split('/')[-1]}  input={_input_n}px")
        except Exception as _e_dev:
            print(f"[segformer] failed to move to {_segformer_device}: {_e_dev} — falling back to CPU")
            _segformer_device = "cpu"
            _segformer_model.to("cpu")  # type: ignore[union-attr]
        _SEGFORMER_OK = True
    except Exception:
        _SEGFORMER_OK = False
    return _SEGFORMER_OK


def _ensure_mobilesam() -> bool:
    """Lazily load MobileSAM vit_t. Returns True if the predictor is ready."""
    global _MOBILESAM_LOADED, _MOBILESAM_OK, _mobilesam_predictor
    if _MOBILESAM_LOADED:
        return _MOBILESAM_OK
    _MOBILESAM_LOADED = True
    ckpt = os.environ.get(
        "MOBILESAM_CHECKPOINT_PATH",
        str(Path.home() / ".cache" / "mobile_sam" / "vit_t.pth"),
    )
    try:
        from mobile_sam import SamPredictor, sam_model_registry  # noqa: PLC0415
        if not Path(ckpt).is_file():
            return False
        model = sam_model_registry["vit_t"](checkpoint=ckpt)
        model.eval()
        _mobilesam_predictor = SamPredictor(model)
        _MOBILESAM_OK = True
    except Exception:
        _MOBILESAM_OK = False
    return _MOBILESAM_OK


def _mobilesam_available() -> bool:
    """Return True without triggering a load (for quick gate checks)."""
    if _MOBILESAM_LOADED:
        return _MOBILESAM_OK
    return _ensure_mobilesam()


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


def _ensure_label_map(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Run SegFormer-b0 (ADE20K) and return the per-pixel argmax label map.

    Cached by id(image_rgb). All higher-level mask getters
    (``_neural_sky_and_building_masks``, ``_neural_water_mask``,
    ``_neural_vegetation_mask``) share this single forward pass — once the
    label map is cached, deriving any class mask is a ~1 ms boolean op.

    Returns None if the model is unavailable or inference fails. Cache
    entries store None for the failure case so we don't retry forever.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if entry is not None and entry.get("_anchor") is image_rgb:
        _neural_cache.move_to_end(img_id)
        return entry.get("label_map")

    if not _ensure_segformer():
        _neural_cache_put(img_id, {"label_map": None}, image_rgb)
        return None

    try:
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415

        h, w = image_rgb.shape[:2]
        pil = PILImage.fromarray(image_rgb)
        inputs = _segformer_processor(
            images=pil, return_tensors="pt")  # type: ignore[misc]
        # Move inputs to the model's device (CUDA when available).
        if _segformer_device != "cpu":
            inputs = {k: v.to(_segformer_device) for k, v in inputs.items()}
        with torch.no_grad():
            outputs = _segformer_model(**inputs)  # type: ignore[misc]
        # logits: (1, num_classes, H/4, W/4) → upsample to original resolution
        upsampled = F.interpolate(
            outputs.logits, size=(h, w), mode="bilinear", align_corners=False
        )
        # argmax on-device, then transfer the small int64 (H, W) result to CPU
        # for the downstream numpy work — cheaper than moving the full logits.
        label_map = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
        _neural_cache_put(img_id, {"label_map": label_map}, image_rgb)
        return label_map
    except Exception:
        _neural_cache_put(img_id, {"label_map": None}, image_rgb)
        return None


def _segformer_batch_size() -> int:
    """Resolve the prefetch forward-pass batch size (images per call).

    Batching the spin views into a single forward pass amortizes Python
    dispatch and (on GPU) kernel-launch overhead — the dominant per-image
    cost on the 12-view spin once the model itself is warm. Bounded so peak
    activation memory stays modest on CPU-only machines. Override with
    ``SKYLINE_CV_SEGFORMER_BATCH`` (integer ≥ 1; 1 disables batching).
    """
    raw = os.environ.get("SKYLINE_CV_SEGFORMER_BATCH", "12").strip()
    try:
        return max(1, int(raw))
    except ValueError:
        return 12


def prefetch_label_maps(images: "list[np.ndarray]") -> int:
    """Run SegFormer once on a *batch* of images, populating the neural cache.

    Every higher-level mask getter (``_ensure_label_map`` and the sky /
    building / water / vegetation masks built on it) is keyed by
    ``id(image_rgb)`` in ``_neural_cache``. Pre-running the whole spin as one
    (or a few) batched forward pass(es) means each later per-view call is a
    cache hit instead of its own forward pass — identical numerics, but the
    transformer's fixed per-call overhead is paid once for the batch rather
    than 12×.

    Images already cached (by id + anchor identity) are skipped, so this is
    idempotent and safe to call from multiple phases on overlapping image
    sets. Returns the number of images actually pushed through the model.
    A no-op returning 0 when the model is unavailable or the batch is empty;
    callers then fall back transparently to lazy per-image inference.
    """
    if not images:
        return 0
    # Filter to images not already cached (same id AND same array object, to
    # respect the anti-GC-reuse anchor invariant in _neural_cache_put).
    pending: list[np.ndarray] = []
    seen_ids: set[int] = set()
    for img in images:
        if img is None:
            continue
        img_id = id(img)
        if img_id in seen_ids:
            continue
        seen_ids.add(img_id)
        entry = _neural_cache.get(img_id)
        if (entry is not None and entry.get("_anchor") is img
                and "label_map" in entry):
            continue
        pending.append(img)
    if not pending:
        return 0
    if not _ensure_segformer():
        return 0

    try:
        import torch  # noqa: PLC0415
        import torch.nn.functional as F  # noqa: PLC0415
        from PIL import Image as PILImage  # noqa: PLC0415

        batch_n = _segformer_batch_size()
        ran = 0
        for start in range(0, len(pending), batch_n):
            chunk = pending[start:start + batch_n]
            pil_batch = [PILImage.fromarray(img) for img in chunk]
            inputs = _segformer_processor(  # type: ignore[misc]
                images=pil_batch, return_tensors="pt")
            if _segformer_device != "cpu":
                inputs = {k: v.to(_segformer_device) for k, v in inputs.items()}
            with torch.no_grad():
                outputs = _segformer_model(**inputs)  # type: ignore[misc]
            # logits: (N, num_classes, H/4, W/4). Upsample + argmax per image
            # at its own native resolution (the spin views are uniform size,
            # but this stays correct if a caller mixes sizes) — and avoids
            # one giant (N, C, H, W) full-res tensor.
            logits = outputs.logits
            for i, img in enumerate(chunk):
                h, w = img.shape[:2]
                upsampled = F.interpolate(
                    logits[i:i + 1], size=(h, w),
                    mode="bilinear", align_corners=False)
                label_map = upsampled.squeeze(0).argmax(dim=0).cpu().numpy()
                _neural_cache_put(id(img), {"label_map": label_map}, img)
                ran += 1
        return ran
    except Exception:
        # Leave the cache untouched; lazy per-image inference still works.
        return 0


def _neural_sky_and_building_masks(
    image_rgb: np.ndarray,
) -> tuple["np.ndarray | None", "np.ndarray | None"]:
    """Return cleaned-up boolean (sky_mask, building_mask) — morphology applied.

    The SegFormer forward pass is shared with the water/vegetation getters
    via ``_ensure_label_map``. This function adds the morphological cleanup
    (~50 ms/image) that the silhouette/registration code depends on:
    speckle removal, glass-tower top repair, and sky/building mutual
    exclusion. Callers who only need water or vegetation masks should use
    their respective helpers — those skip the morphology entirely.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if (entry is not None and entry.get("_anchor") is image_rgb
            and "sky" in entry and "building" in entry):
        _neural_cache.move_to_end(img_id)
        return entry["sky"], entry["building"]

    label_map = _ensure_label_map(image_rgb)
    if label_map is None:
        entry = _neural_cache.get(img_id)
        if entry is not None:
            entry["sky"] = None
            entry["building"] = None
        return None, None

    sky_mask = label_map == _ADE20K_SKY
    # Union all building-family classes (building, house, skyscraper) so a
    # dark-glass tower SegFormer prefers to label "skyscraper" still
    # contributes to the building mask the silhouette splitter sees.
    building_mask = np.isin(label_map, _ADE20K_BUILDING_CLASSES)
    # Mask cleanup: SegFormer's per-pixel argmax produces speckle —
    # small isolated sky pixels INSIDE a building (window reflections
    # of the sky on glass), and small isolated building pixels
    # OUTSIDE buildings (cloud edges, antenna tips). A 5×5 closing on
    # the building mask fills sub-window holes; a 3×3 opening on the
    # sky mask removes isolated-sky speckle. Then we reassert
    # mutual-exclusion (a pixel can't be both sky AND building post-
    # cleanup) by giving sky precedence near the top of the image
    # and building precedence below — that mirrors the physical
    # likelihood at each y. Net effect on Cartagena: the central
    # tower cluster's mask blob acquires sharper inter-building
    # gaps that F-SKY7's local-max peak detector can exploit.
    b_u8 = (building_mask.astype(np.uint8)) * 255
    s_u8 = (sky_mask.astype(np.uint8)) * 255
    close_k = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    open_k = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))
    b_u8 = cv2.morphologyEx(b_u8, cv2.MORPH_CLOSE, close_k)
    s_u8 = cv2.morphologyEx(s_u8, cv2.MORPH_OPEN, open_k)
    # Glass-tower top repair: a tall narrow vertical closing (1 col ×
    # 11 rows) bridges short sky strips that mirrored-sky reflections
    # carve into a glass facade — the canonical Cartagena Bocagrande
    # failure where the mask top has a wavy edge a row of grey-blue
    # pixels below the actual roofline. Vertical-only kernel preserves
    # the HORIZONTAL inter-tower gaps that F-SKY2 splitting depends
    # on (a 5×5 isotropic closing would erase those too). Capped at
    # 11 px so windows-and-cornice gaps two storeys tall don't get
    # mistakenly filled in.
    vert_k = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 25))
    b_u8 = cv2.morphologyEx(b_u8, cv2.MORPH_CLOSE, vert_k)
    # Glass-tower hole-fill: any non-building region NOT reachable from
    # the image border (i.e. fully enclosed by building) is almost
    # certainly a mis-classified dark glass facade. Flood-fill the
    # complement from (0,0); cells still untouched after the fill are
    # interior holes worth promoting to building.
    inv = (b_u8 == 0).astype(np.uint8)
    h_full, w_full = inv.shape[:2]
    ff_mask = np.zeros((h_full + 2, w_full + 2), dtype=np.uint8)
    flood = inv.copy()
    cv2.floodFill(flood, ff_mask, (0, 0), 2)
    interior_holes = (flood == 1)
    if interior_holes.any():
        b_u8[interior_holes] = 255
    # Beach/sand/earth-as-building cap (Cartagena coast): SegFormer-b1
    # routinely labels coastal sand strips as "building" (class 1), which
    # the hole-fill above then fuses with real tower bases — producing
    # per-view bboxes whose base lands at the waterline instead of the
    # actual building base. Two complementary clips run here:
    #
    #   A. Hard ground cap: in any column where SegFormer DID correctly
    #      label water/earth/sand at row Y, building above row Y is
    #      kept but anything at or below Y is zeroed.
    #
    #   B. Beach-band heuristic: in columns where the model is fooling
    #      itself (no ground class anywhere — just one tall "building"
    #      strip running into the waterline), use the column's water
    #      neighbours to estimate a "ground row" and clip the building
    #      mask at ~0.08 * H above the waterline. This catches the
    #      common case where the model labels the entire beach as
    #      building class 1 with no internal sand pixels.
    # Class-membership ground cap: in any column where SegFormer
    # explicitly labelled a non-building "ground" class (water, sea,
    # river, earth, sand) at row Y, clip the building mask at/below Y.
    # Catches the failure mode where the hole-fill fuses real towers
    # to a correctly-labelled patch of sand below them. Does NOT clip
    # at columns where the model itself mis-labels the beach as
    # building (that overcorrected on run #22 and cost legitimate
    # low-tower matches); the unconditional waterline band was
    # reverted in favour of trusting the model's own ground labels
    # where it provides them.
    # WATER ONLY for the bottom-up cap — deliberately excludes earth/sand
    # (ADE20K 13/46). Buildings stand ON sand/beach, so a sand region is
    # NOT a place a building "can't be": clipping at sand-top wrongly
    # removes the building's lower floors. Worse, Cartagena's bright
    # sandy-coloured towers get partially mislabelled as sand, so a
    # sand-inclusive run extends UP into the facade and chops it (the
    # seed_5 peninsula-tip cluster that went missing). Water is the only
    # class where a building genuinely cannot stand, so only water caps.
    _GROUND_CLASSES = _ADE20K_WATER_CLASSES
    ground_mask_local = np.isin(label_map, _GROUND_CLASSES)
    if ground_mask_local.any():
        # Waterline = top of the FOREGROUND water block, found bottom-up
        # per column — NOT the topmost water pixel. In a 360° pano the
        # far bay water is visible at the horizon ABOVE the foreground
        # building bases; a naive topmost-water argmax would treat that
        # distant strip as "ground" and zero the entire building beneath
        # it (the Cartagena seed_5 failure: buildings standing in front
        # of open bay get chopped). Instead we locate the bottom-most
        # contiguous water run in each column — the real foreground
        # waterline — and clip only at/below that. Horizon water above
        # the buildings no longer participates.
        gm = ground_mask_local
        rev = gm[::-1, :]  # row 0 = image bottom
        has_ground = rev.any(axis=0)
        # Bottom-most ground pixel (index in reversed coords).
        first_ground_rev = rev.argmax(axis=0)
        # First NON-ground row at/above that pixel (still reversed): the
        # top of the foreground ground run.
        row_idx_rev = np.arange(h_full)[:, None]
        non_ground_rev = (~rev) & (row_idx_rev >= first_ground_rev[None, :])
        has_break = non_ground_rev.any(axis=0)
        first_break_rev = np.where(
            has_break, non_ground_rev.argmax(axis=0), h_full)
        # Convert the run-top from reversed to original row coords.
        waterline_per_col = (h_full - first_break_rev).astype(np.int32)
        # Columns with no ground at all: cap below the image (no-op).
        waterline_per_col = np.where(
            has_ground, waterline_per_col, h_full).astype(np.int32)
        row_idx = np.arange(h_full)[:, None]
        margin_px = 4
        below_ground = row_idx >= (waterline_per_col[None, :] - margin_px)
        b_u8[below_ground] = 0
    building_mask = b_u8.astype(bool)
    sky_mask = s_u8.astype(bool)
    # Pixels claimed by both: building wins (sky-on-glass-reflection
    # is the dominant failure case; cyan-tower-edge cloud is rare).
    sky_mask &= ~building_mask
    entry = _neural_cache.get(img_id)
    if entry is not None:
        entry["sky"] = sky_mask
        entry["building"] = building_mask
    return sky_mask, building_mask


def _neural_water_mask(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Return the cached water-class boolean mask for this image.

    Shares the SegFormer forward pass with ``_neural_sky_and_building_masks``
    via the cached label_map but skips the (~50 ms/image) morphology pass —
    water consumers don't need it. Subsequent calls within the cache window
    are O(1).
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if (entry is not None and entry.get("_anchor") is image_rgb
            and "water" in entry):
        _neural_cache.move_to_end(img_id)
        return entry["water"]

    label_map = _ensure_label_map(image_rgb)
    entry = _neural_cache.get(img_id)
    if label_map is None or entry is None:
        if entry is not None:
            entry["water"] = None
        return None
    water_mask = np.isin(label_map, _ADE20K_WATER_CLASSES)
    entry["water"] = water_mask
    return water_mask


def _neural_vegetation_mask(image_rgb: np.ndarray) -> "np.ndarray | None":
    """Return the cached vegetation-class boolean mask (F-SKY18).

    Shares the SegFormer forward pass via the cached label_map; skips
    morphology like ``_neural_water_mask``. Consumers only use per-column
    extents, where speckle is averaged out.
    """
    img_id = id(image_rgb)
    entry = _neural_cache.get(img_id)
    if (entry is not None and entry.get("_anchor") is image_rgb
            and "vegetation" in entry):
        _neural_cache.move_to_end(img_id)
        return entry["vegetation"]

    label_map = _ensure_label_map(image_rgb)
    entry = _neural_cache.get(img_id)
    if label_map is None or entry is None:
        if entry is not None:
            entry["vegetation"] = None
        return None
    vegetation_mask = np.isin(label_map, _ADE20K_VEGETATION_CLASSES)
    entry["vegetation"] = vegetation_mask
    return vegetation_mask


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


def detect_skyline_contour(image_rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Return a top-down skyline contour and a binary sky mask.

    SegFormer-b0 (ADE20K) sky segmentation is a hard dependency. The
    downstream mIoU registration objective relies on the building/water
    masks from the same model, so a colour-threshold fallback wouldn't be
    enough to keep the pipeline functional.
    """
    h, w = image_rgb.shape[:2]

    sky_neural, _ = _neural_sky_and_building_masks(image_rgb)
    if sky_neural is None:
        raise RuntimeError(
            "SegFormer-b0 unavailable — required for sky/building segmentation. "
            "Ensure `transformers` + `torch` are installed and the model "
            "weights can be downloaded from HuggingFace.")
    # Keep only sky pixels connected to the top border (drop interior
    # "sky" holes that aren't actually sky).
    raw_sky = (sky_neural.astype(np.uint8)) * 255
    _n_labels, labels, _stats, _centroids = cv2.connectedComponentsWithStats(
        raw_sky, connectivity=8)
    top_labels = {int(v) for v in labels[0, :] if int(v) != 0}
    sky_mask = np.zeros((h, w), dtype=np.uint8)
    for lab in top_labels:
        sky_mask[labels == lab] = 255
    if not np.any(sky_mask):
        sky_mask = raw_sky

    # ── Contour extraction ───────────────────────────────────────────────────
    contour = np.full(w, np.nan, dtype=np.float32)
    for x in range(w):
        column = sky_mask[:, x] > 0
        non_sky = np.where(~column)[0]
        if non_sky.size:
            contour[x] = float(non_sky[0])

    # Reject implausible boundaries too close to image top/bottom.
    y_min = float(h * 0.06)
    y_max = float(h * 0.90)
    contour = np.where((contour >= y_min) & (
        contour <= y_max), contour, np.nan)

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
        contour = median_filter(
            contour, size=9, mode="nearest").astype(np.float32)

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

    Tests two cuts against the SegFormer building mask:
      1. Bounding-box fraction (≥ 10 % accept). Wide boxes around slim glass
         towers contain a lot of sky and easily fall below this even though
         the tower itself is obviously a building, so we also test...
      2. Peak-column fraction (≥ 25 % accept). A tight ±4 px column around
         ``peak_x`` restricted to the lower 60 % of the segment captures the
         facade core where the tower lives.

    A region passing either cut is kept. A region failing BOTH (bfrac < 5 %
    AND col_frac < 10 %) is rejected. Ambiguous middle band is accepted —
    the colour/texture heuristics that previously broke ties were removed
    because they almost never disagreed with the mask in practice.
    """
    h, w = image.shape[:2]
    y0 = max(0, top_y)
    y1 = min(h, base_y)
    x0 = max(0, x_left)
    x1 = min(w, x_right)
    if y1 <= y0 or x1 <= x0:
        return True  # degenerate region: don't reject

    if building_mask is None:
        # Without a mask we can't make an informed call; accept and let
        # downstream stages filter. SegFormer is a hard dependency in
        # production runs, so this branch should never fire.
        return True

    bfrac = float(building_mask[y0:y1, x0:x1].mean())
    px = int(peak_x) if peak_x is not None else (x0 + x1) // 2
    cx0 = max(0, px - 4)
    cx1 = min(w, px + 5)
    trunk_top = y0 + (y1 - y0) * 2 // 5
    col_frac = (
        float(building_mask[trunk_top:y1, cx0:cx1].mean())
        if cx1 > cx0 else 0.0
    )
    if bfrac >= 0.10 or col_frac >= 0.25:
        return True
    if bfrac < 0.05 and col_frac < 0.10:
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
    slab = building_mask[:, x0: x1 + 1].astype(np.uint8)
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


def _floor_period_for_building(
    image_rgb: np.ndarray,
    building_mask: "np.ndarray | None",
    x_range: tuple[int, int],
    y_top: float,
    y_base: float,
    *,
    f_px: float,
    floor_height_m: float = 3.2,
    min_lag_px: int = 4,
    max_lag_px: int = 80,
    min_confidence: float = 0.20,
    min_row_coverage: float = 0.30,
) -> dict | None:
    """Detect horizontal floor banding on a building facade and back out
    an OSM-independent height + distance estimate (F-SKY1).

    Idea: window rows, balconies, and slab edges produce regular horizontal
    stripes in a tall building's mask region. The dominant pixel period of
    those stripes is one floor. Given pinhole focal length f_px and an
    assumed physical floor height (3.2 m default — mid of residential
    2.8 m and commercial 3.6 m), distance is
    ``f_px × floor_height_m / period_px`` and total height is
    ``(facade_height_px / period_px) × floor_height_m``.

    Returns a dict with ``floor_period_px``, ``floor_confidence``,
    ``inferred_distance_m``, ``inferred_floors``, ``inferred_height_m``,
    or None when no usable period is detected (mask missing, region too
    short, autocorrelation peak below confidence threshold).

    See ``docs/plans/F-SKY1-floor-periodicity.md`` for the rationale and
    where this signal is meant to slot into the height aggregate.
    """
    if building_mask is None or image_rgb is None:
        return None
    H, W = building_mask.shape[:2]
    xL = max(0, int(x_range[0]))
    xR = min(W, int(x_range[1]) + 1)
    yT = max(0, int(round(y_top)))
    yB = min(H, int(round(y_base)))
    if xR - xL < 4 or yB - yT < max_lag_px * 2:
        return None
    if f_px <= 0.0 or floor_height_m <= 0.0:
        return None

    img_band = image_rgb[yT:yB, xL:xR]
    mask_band = building_mask[yT:yB, xL:xR]
    if img_band.size == 0 or not mask_band.any():
        return None

    # Per-row mean luminance over building-mask pixels only. Rows whose
    # mask coverage is below the threshold get filled with the band's
    # overall mean so they don't introduce a step into the FFT.
    luma = (
        0.299 * img_band[..., 0].astype(np.float32)
        + 0.587 * img_band[..., 1].astype(np.float32)
        + 0.114 * img_band[..., 2].astype(np.float32)
    )
    mask_f = mask_band.astype(np.float32)
    row_cov = mask_f.mean(axis=1)
    valid_rows = row_cov >= min_row_coverage
    if int(valid_rows.sum()) < max_lag_px * 2:
        return None
    row_sums = (luma * mask_f).sum(axis=1)
    row_counts = mask_f.sum(axis=1)
    safe_counts = np.where(row_counts > 0, row_counts, 1.0)
    profile = (row_sums / safe_counts).astype(np.float32)
    band_mean = float(profile[valid_rows].mean())
    profile = np.where(valid_rows, profile, band_mean).astype(np.float32)

    # High-pass: subtract a 12-row uniform mean to suppress whole-band
    # illumination gradients (top-of-tower in shadow, bottom lit by
    # waterfront sun). A 3-tap median then damps salt-and-pepper from
    # the SegFormer mask without flattening the floor period.
    smoothed = uniform_filter1d(profile, size=12, mode="nearest")
    high = profile - smoothed
    high = median_filter(high, size=3)
    high = high - float(high.mean())

    n = high.size
    full = np.correlate(high, high, mode="full")
    autocorr = full[n - 1:]
    norm = float(autocorr[0])
    if norm <= 1e-6:
        return None
    autocorr = autocorr / norm

    if max_lag_px >= autocorr.size:
        return None
    window = autocorr[min_lag_px: max_lag_px + 1]
    if window.size < 3:
        return None

    # Fundamental-period detection via sub-harmonic descent (harmonic
    # disambiguation). The autocorrelation of a periodic facade peaks
    # at the fundamental floor period P *and* every multiple 2P, 3P, …
    # The DOMINANT peak is frequently a multiple when the building has
    # strong coarse banding (mechanical floors, every-3rd-floor balcony
    # bands), so a naive argmax returns 2-3× the true period and the
    # back-derived distance comes out 2-3× too small.
    #
    # Strategy: take the dominant peak lag L_best, then test its integer
    # sub-divisors L_best/k (k=2..6). If a divisor lag d still shows a
    # strong autocorr peak (≥ ``sub_frac`` of the dominant), d is the
    # real fundamental and we descend to it. We keep the SMALLEST such
    # divisor that holds up — that's the fundamental floor period.
    best_abs = int(min_lag_px + int(np.argmax(window)))
    best_val = float(autocorr[best_abs])
    if best_val < min_confidence:
        return None

    def _peak_val_near(lag: int, tol: int = 1) -> tuple[int, float]:
        """Best autocorr value within ±tol of `lag` (periods aren't
        exact integers); returns (lag_of_max, value)."""
        lo = max(min_lag_px, lag - tol)
        hi = min(max_lag_px, lag + tol)
        if hi < lo:
            return lag, -1.0
        seg = autocorr[lo: hi + 1]
        j = int(np.argmax(seg))
        return lo + j, float(seg[j])

    sub_frac = 0.55
    fund_abs = best_abs
    for k in (6, 5, 4, 3, 2):  # try the deepest division first
        d = int(round(best_abs / k))
        if d < min_lag_px:
            continue
        d_lag, d_val = _peak_val_near(d)
        if d_val >= sub_frac * best_val and d_val >= min_confidence:
            fund_abs = d_lag  # descend; keep iterating toward smaller k
    peak_idx_rel = fund_abs - min_lag_px
    peak_val = float(autocorr[fund_abs])
    period_px = float(fund_abs)
    if period_px <= 0.0:
        return None

    inferred_distance_m = (f_px * floor_height_m) / period_px
    inferred_floors = float(yB - yT) / period_px
    inferred_height_m = inferred_floors * floor_height_m
    return {
        "floor_period_px": period_px,
        "floor_confidence": peak_val,
        "inferred_distance_m": float(inferred_distance_m),
        "inferred_floors": float(inferred_floors),
        "inferred_height_m": float(inferred_height_m),
    }


def _estimate_building_base(
    image: np.ndarray,
    peak_x: int,
    top_y: int,
    h: int,
    building_mask: "np.ndarray | None" = None,
) -> int:
    """Estimate the row where a building's base meets the ground/water.

    Walks the SegFormer building mask down from top_y at peak_x. The Sobel/
    brightness fallback was removed — it was only invoked when the mask was
    unavailable, which doesn't happen in practice (SegFormer is a hard
    dependency on the rest of the pipeline). Falls back to 75% frame height
    when no building pixels extend at least 10 rows below the rooftop
    (truncated building, weird mask).
    """
    if building_mask is not None:
        mask_base = _building_base_y_from_mask(building_mask, peak_x, top_y)
        if mask_base is not None and mask_base > top_y + 10:
            return min(h - 1, mask_base)
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

    # F-SKY7: also catch local-max peaks within continuous mask regions.
    # When SegFormer's building mask spans a row of glass towers without
    # sky valleys between them, the contour stays HIGH (low y) everywhere
    # and the global-prominence filter above rejects per-tower bumps.
    # Detect bumps relative to a smoothed regional baseline (40 px window
    # ≈ 6° of FOV at W=640) so monotone-but-bumpy rooflines still
    # produce one peak per tower. The absolute prominence floor (6 px)
    # is what stops contour noise from inventing spurious peaks.
    #
    # Two baseline widths run in parallel:
    #   - 40 px wide baseline catches well-spaced 25-50 m towers
    #   - 22 px tight baseline catches the dense Bocagrande-style row of
    #     similar-height glass towers (3-4° FOV/tower) that the wide
    #     baseline averages out (the bump signal flattens when peak
    #     spacing approaches the baseline width). Higher prominence
    #     floor (8 px) compensates for the noisier tight baseline.
    # Both peak sets merge via the same de-dup pass below.
    baseline = uniform_filter1d(c_filled, size=40, mode="nearest")
    bump = baseline - smooth
    local_peaks, _ = find_peaks(
        bump,
        distance=12,
        prominence=6.0,
    )
    baseline_tight = uniform_filter1d(c_filled, size=22, mode="nearest")
    bump_tight = baseline_tight - smooth
    tight_peaks, _ = find_peaks(
        bump_tight,
        distance=10,
        prominence=8.0,
    )
    if tight_peaks.size > 0:
        if local_peaks.size > 0:
            keep_t = np.array(
                [int(abs(local_peaks - tp).min()) > 8 for tp in tight_peaks],
                dtype=bool,
            )
            tight_peaks = tight_peaks[keep_t]
        if tight_peaks.size > 0:
            local_peaks = np.sort(np.concatenate([local_peaks, tight_peaks]))
    if local_peaks.size > 0:
        if peaks.size > 0:
            # De-dup: only keep local-max peaks > 8 px from any existing peak.
            keep = np.array(
                [int(abs(peaks - lp).min()) > 8 for lp in local_peaks],
                dtype=bool,
            )
            local_peaks = local_peaks[keep]
        if local_peaks.size > 0:
            peaks = np.sort(np.concatenate([peaks, local_peaks]))

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
    gray = cv2.cvtColor(
        image, cv2.COLOR_RGB2GRAY) if image.ndim == 3 else image.astype(np.uint8)
    sobel_x = np.abs(cv2.Sobel(gray.astype(
        np.float32), cv2.CV_32F, 1, 0, ksize=3))
    zone_top = max(0, int(global_min) - 15)
    zone_bot = min(h, int(global_median) + 30)
    if zone_bot > zone_top:
        edge_col = sobel_x[zone_top:zone_bot, :].mean(
            axis=0).astype(np.float32)
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
        top_y_raw = float(c[peak_x]) if np.isfinite(
            c[peak_x]) else float(smooth[peak_x])

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
    image: "np.ndarray | None" = None,
    max_splits_per_component: int = 24,
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

    # Pre-compute a per-column vertical-gradient signal across the full
    # frame (Phase A — Plan, STATUS.md). Captures facade edges between
    # adjacent towers that share a roofline. Computed once here, sliced
    # per-component in the peak loop below. Only used when an image is
    # supplied; if not, the existing contour + col-counts signals carry
    # the work.
    grad_col_signal: "np.ndarray | None" = None
    if image is not None and image.ndim == 3:
        try:
            gray = cv2.cvtColor(image, cv2.COLOR_RGB2GRAY).astype(np.float32)
            sx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3))
            # Integrate vertical edges within the BUILDING band only. Rows
            # outside the band would add noise from water/sky textures.
            band_for_grad = compute_building_band(building_mask, slack_px=8)
            if band_for_grad is not None:
                y0g, y1g = band_for_grad
                grad_col_signal = sx[y0g : y1g + 1].sum(axis=0)
            else:
                grad_col_signal = sx.sum(axis=0)
            # Mild smoothing — facade edges are thin but noise is thinner.
            grad_col_signal = gaussian_filter1d(
                grad_col_signal, sigma=1.5).astype(np.float32)
        except Exception:
            grad_col_signal = None

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
            mask[y_bot + 1:, :] = 0

    n_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
        mask, connectivity=8)
    out: list[dict] = []
    for i in range(1, n_labels):
        x = int(stats[i, cv2.CC_STAT_LEFT])
        y = int(stats[i, cv2.CC_STAT_TOP])
        cw = int(stats[i, cv2.CC_STAT_WIDTH])
        ch = int(stats[i, cv2.CC_STAT_HEIGHT])
        if cw < min_width_px or ch < min_height_px:
            continue
        component = labels[y: y + ch, x: x + cw] == i
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
            # Phase A rework: gradient peaks are facade BOUNDARIES (between
            # towers), not building centers. Previously we unioned them with
            # contour and col-counts peaks, then applied a mask-height
            # support threshold — which rejected the gradient peaks because
            # facade gaps have LOW mask coverage, not high. The result was
            # zero gradient contribution on the exact case it's needed for:
            # rows of similar-height towers with flat col_counts.
            #
            # New approach:
            #   1. Find gradient peaks → use them as BOUNDARIES that
            #      subdivide the component's column range.
            #   2. Within each subrange, pick ONE peak (col_counts argmax)
            #      as that subdivision's building center.
            #   3. Union with contour peaks (still useful for spires).
            #
            # This lets the gradient drive splitting even when col_counts
            # is flat — exactly the merged-row-of-towers case.
            grad_boundaries: list[int] = []
            if grad_col_signal is not None:
                grad_slice = grad_col_signal[x : x + cw].astype(np.float32)
                grad_std = float(np.std(grad_slice))
                grad_prom = max(1.5, grad_std * 0.20)
                gp, _ = find_peaks(
                    grad_slice, distance=10, prominence=grad_prom)
                grad_boundaries = [int(v) for v in gp.tolist()]

            # Build the subrange list using gradient boundaries.
            subrange_edges = [0, *sorted(grad_boundaries), cw - 1]
            # Per subrange, pick the col_counts argmax as the candidate
            # building center. Skip degenerate (too-narrow) subranges.
            grad_derived_peaks: list[int] = []
            for a, b in zip(subrange_edges[:-1], subrange_edges[1:]):
                if b - a < 6:
                    continue
                sub = col_counts[a : b + 1]
                if sub.size == 0 or sub.max() < 6:
                    continue
                grad_derived_peaks.append(int(np.argmax(sub)) + a)

            # Also find contour peaks (rooftop spires) — these still help
            # when towers have distinctive rooflines even within a flat row.
            contour_peaks: list[int] = []
            if contour is not None and contour.size >= x + cw:
                contour_slice = np.asarray(
                    contour[x: x + cw], dtype=np.float32)
                if np.any(~np.isfinite(contour_slice)):
                    fill = float(np.nanmedian(contour_slice))
                    contour_slice = np.where(np.isfinite(
                        contour_slice), contour_slice, fill)
                c_std = float(np.std(contour_slice))
                c_prom = max(1.5, c_std * 0.15)
                cp, _ = find_peaks(-contour_slice, distance=8, prominence=c_prom)
                contour_peaks = [int(v) for v in cp.tolist()]

            # And col_counts peaks (the original signal) — these catch
            # narrow spires that don't show up in the gradient subdivision
            # because they're entirely within one gap-bounded subrange.
            col_std = float(np.std(col_counts))
            col_prom = max(1.5, col_std * 0.15)
            cc_p, _ = find_peaks(col_counts, distance=8, prominence=col_prom)
            col_peaks = [int(v) for v in cc_p.tolist()]

            # Union all three sources.
            all_peaks = grad_derived_peaks + contour_peaks + col_peaks

            # Dedup with a 10-px tolerance — peaks within that window from
            # different signals are the same tower.
            if all_peaks:
                all_peaks.sort()
                dedup: list[int] = [all_peaks[0]]
                for p in all_peaks[1:]:
                    if p - dedup[-1] >= 10:
                        dedup.append(p)
                # Anti-over-split guard: require mask-height support for
                # each peak. With gradient now used to subdivide (not as a
                # peak source), the support threshold is purely a noise
                # filter on contour/col peaks. The gradient-derived peaks
                # already passed through the col_counts argmax, so they
                # implicitly have support.
                col_peak_h = float(col_counts.max())
                support_threshold = max(6.0, col_peak_h * 0.25)
                supported = [
                    p for p in dedup
                    if col_counts[p] >= support_threshold
                ]
                if supported:
                    dedup = supported
                # Cap the number of splits to avoid degenerate over-segmentation
                # of very wide components (a 1500-px-wide row of identical
                # towers shouldn't produce 30+ tiny segments).
                if len(dedup) > max_splits_per_component:
                    # Keep the top-N by col-height (tallest support wins).
                    dedup = sorted(
                        dedup,
                        key=lambda p: float(col_counts[p]),
                        reverse=True,
                    )[:max_splits_per_component]
                    dedup.sort()
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
                v = int(np.argmin(col_counts[a: b + 1])) + a
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
            top_off = int(top_rows[peak_col_off]
                          ) if top_rows[peak_col_off] >= 0 else 0
            top_y = int(y + top_off)
            base_walked = _building_base_y_from_mask(
                building_mask, peak_x, top_y)
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


def _proj_x_range(p: dict) -> tuple[float, float]:
    """Return (left, right) pixel x-bounds of a projection dict.

    Falls back to ±10 px around ``x_px`` when explicit ``x_left_px`` /
    ``x_right_px`` aren't set. Swaps if the caller emitted them out of
    order. Shared by ``osm_anchor_silhouettes`` and
    ``match_segments_to_buildings`` — they used to have private duplicates
    that drifted independently.
    """
    pL = float(p.get("x_left_px", float(p["x_px"]) - 10.0))
    pR = float(p.get("x_right_px", float(p["x_px"]) + 10.0))
    return (pL, pR) if pL <= pR else (pR, pL)


def _proj_containment_in_seg(seg_L: float, seg_R: float, p: dict) -> float:
    """Fraction of projection p's x-range that lies inside [seg_L, seg_R].

    Companion to ``_proj_x_range``. Used as the "is this projection a
    candidate for this segment" metric by F-SKY2, F-SKY3, and the
    matcher's containment fallback — they previously each inlined the
    same arithmetic.
    """
    pL, pR = _proj_x_range(p)
    inter = max(0.0, min(seg_R, pR) - max(seg_L, pL))
    proj_w = max(1.0, pR - pL)
    return inter / proj_w


def osm_anchor_silhouettes(
    segments: list[dict],
    projections: list[dict],
    *,
    building_mask: "np.ndarray | None" = None,
    min_proj_containment: float = 0.5,
    min_gap_px: int = 2,
    min_child_width_px: int = 4,
    min_center_separation_px: int = 14,
) -> list[dict]:
    """Split mask-merged silhouettes using OSM footprint projections as
    structural anchors (F-SKY2).

    SegFormer routinely merges adjacent buildings in dense skylines —
    three glass towers on Cartagena's Bocagrande share one contiguous
    building-mask blob and the OSM-blind silhouette detector emits a
    single wide segment. The matcher then has 1 segment to assign to
    3 buildings; two get silently dropped.

    For each input segment we find the OSM projections whose x-range
    is at least ``min_proj_containment`` fraction *inside* the segment.
    (Interval-IoU is the wrong metric here — N narrow projections
    inside one wide segment all have small IoU because the denominator
    is dominated by the segment width; containment captures "this
    projection mostly lives inside this segment" regardless of
    relative widths.) When 2+ projections qualify AND there is a
    ``min_gap_px``-wide gap between adjacent OSM x-ranges inside the
    segment, the segment is split at that gap. The split column is
    refined to the building-mask column with the lowest coverage
    inside the gap when ``building_mask`` is provided — this snaps the
    cut to where the mask itself is thinnest (the actual inter-building
    separator), not just the OSM midpoint.

    A segment that has 0 or 1 overlapping projections, or whose
    overlapping projections have no qualifying gap, passes through
    unchanged. Children inherit y bounds from the parent (refining
    per-child top/base is a follow-up).

    Calling with empty ``segments`` or empty ``projections`` returns
    the input unchanged — the function is a no-op when registration
    failed and there are no anchors to apply.

    See ``docs/plans/F-SKY2-osm-anchored-segments.md``.
    """
    if not segments or not projections:
        return list(segments)

    out: list[dict] = []
    for seg in segments:
        sL = float(seg["x_left"])
        sR = float(seg["x_right"])
        if sR <= sL:
            out.append(seg)
            continue

        overlapping: list[tuple[float, float, float, dict]] = []
        for p in projections:
            if _proj_containment_in_seg(sL, sR, p) >= min_proj_containment:
                pL, pR = _proj_x_range(p)
                overlapping.append((float(p["x_px"]), pL, pR, p))

        if len(overlapping) < 2:
            out.append(seg)
            continue

        overlapping.sort(key=lambda t: t[0])

        split_cols: list[float] = []
        for i in range(len(overlapping) - 1):
            this_xpx, _, this_R, _ = overlapping[i]
            next_xpx, next_L, _, _ = overlapping[i + 1]
            # Always cut between two distinct OSM building centres as long
            # as their projection centres are far enough apart to leave
            # workable children. The previous gap-only rule failed on
            # adjacent waterfront towers whose footprints just touched —
            # zero gap, zero cut, one mega-segment swallowing both. Now
            # the cut fires on centre separation; the mask-thinnest
            # refinement still snaps to a real valley when one exists.
            if next_xpx - this_xpx < min_center_separation_px:
                continue
            gap_w = next_L - this_R
            if gap_w >= min_gap_px:
                # Real valley between footprints — cut at gap midpoint.
                mid = 0.5 * (this_R + next_L)
            else:
                # Footprints abut or overlap; cut between the projection
                # centres themselves and let mask refinement find the
                # nearest valley.
                mid = 0.5 * (this_xpx + next_xpx)
            if mid <= sL + 2 or mid >= sR - 2:
                continue
            # Refine: snap split column to the building-mask column with
            # lowest coverage within a window around the candidate cut.
            # Generous window so we can find a thin valley even when the
            # initial midpoint sits inside a tall column.
            if building_mask is not None:
                search_half = max(8, int(0.5 * (next_xpx - this_xpx)))
                wL = max(int(mid - search_half), int(sL))
                wR = min(int(mid + search_half), int(sR))
                if wR > wL:
                    col_cov = building_mask[:, wL:wR + 1].sum(axis=0)
                    mid = float(wL + int(np.argmin(col_cov)))
            split_cols.append(mid)

        if not split_cols:
            out.append(seg)
            continue

        cuts = [sL] + sorted(split_cols) + [sR]
        for i in range(len(cuts) - 1):
            cL = cuts[i]
            cR = cuts[i + 1]
            if cR - cL < min_child_width_px:
                continue
            # peak_x: prefer the OSM x_px that falls inside this child.
            mid_x = int(round(0.5 * (cL + cR)))
            child_peak = mid_x
            for px, _, _, _ in overlapping:
                if cL <= px <= cR:
                    child_peak = int(round(px))
                    break
            child = dict(seg)
            child["x_left"] = float(cL)
            child["x_right"] = float(cR)
            child["mid_x"] = mid_x
            child["peak_x"] = child_peak
            child["osm_anchored"] = True
            out.append(child)

    return sorted(out, key=lambda s: s["mid_x"])


def osm_sam_instance_silhouettes(
    image_rgb: np.ndarray,
    segments: list[dict],
    projections: list[dict],
    *,
    building_mask: "np.ndarray | None" = None,
    min_proj_containment: float = 0.5,
    min_marker_separation_px: int = 20,
    confidence_floor: float = 0.65,
    min_child_width_px: int = 4,
) -> list[dict]:
    """Split merged segments using MobileSAM prompted by OSM centroids (F-SKY5).

    Replaces F-SKY3's heuristic Voronoi split with a learned instance-
    segmentation model. Fires only on segments that contain ≥ 2 OSM marker
    projections (the cases where F-SKY2 gap-based splitting and the matcher's
    containment fallback did not already resolve the merge). When MobileSAM is
    not installed or the checkpoint is missing, returns ``segments`` unchanged
    (transparent no-op — the pipeline continues with F-SKY2 segments).

    For each qualifying merged segment:
    - Gather the OSM-projected centroids (x_px, mid_y) as SAM point prompts.
    - Run MobileSAM; discard masks below ``confidence_floor``.
    - Intersect each surviving mask with the SegFormer ``building_mask`` (when
      provided) so SAM predictions that leak into sky / water are clipped.
    - Bound children to the parent segment's [x_left, x_right] column range.
    - Collapse sibling masks whose peaks are within ``min_marker_separation_px``
      pixels (avoids over-segmenting a single tower into body + spire).
    - If 0 masks survive the floor, leave the original segment unchanged.

    See ``docs/plans/F-SKY5-mobilesam-instance.md``.
    """
    if not _mobilesam_available():
        return list(segments)
    if not segments or not projections:
        return list(segments)

    predictor = _mobilesam_predictor
    h, w = image_rgb.shape[:2]
    mid_y = h // 2

    # Set image once for this call (MobileSAM caches the embedding internally).
    import torch  # noqa: PLC0415
    with torch.no_grad():
        predictor.set_image(image_rgb)

    out: list[dict] = []
    for seg in segments:
        sL = float(seg["x_left"])
        sR = float(seg["x_right"])

        # Collect OSM markers contained in this segment.
        markers: list[tuple[float, float]] = []
        for p in projections:
            if _proj_containment_in_seg(sL, sR, p) >= min_proj_containment:
                markers.append((float(p["x_px"]), float(mid_y)))

        if len(markers) < 2:
            out.append(seg)
            continue

        # Prompt SAM once per marker to get per-instance masks.
        # Batching all points together returns a single mask covering the union;
        # one point per call is the correct instance-segmentation pattern.
        surviving: list[tuple[float, np.ndarray]] = []
        for mx, my in markers:
            try:
                with torch.no_grad():
                    masks_arr, scores_arr, _ = predictor.predict(
                        point_coords=np.array([[mx, my]], dtype=np.float32),
                        point_labels=np.array([1], dtype=np.int32),
                        multimask_output=True,
                    )
            except Exception:
                continue
            # Pick the highest-confidence mask from the 3 multimask outputs.
            best_idx = int(np.argmax(scores_arr))
            best_score = float(scores_arr[best_idx])
            if best_score < confidence_floor:
                continue
            m = masks_arr[best_idx].astype(bool)
            if building_mask is not None:
                m = m & building_mask.astype(bool)
            m[:, :int(sL)] = False
            m[:, int(sR) + 1:] = False
            if not m.any():
                continue
            surviving.append((best_score, m))

        if not surviving:
            out.append(seg)
            continue

        # Sort by score descending; build non-overlapping child segments.
        surviving.sort(key=lambda t: t[0], reverse=True)

        children: list[dict] = []
        used_cols: set[int] = set()
        for _score, m in surviving:
            cols = np.where(m.any(axis=0))[0]
            if len(cols) == 0:
                continue
            cL, cR = float(cols[0]), float(cols[-1])
            if cR - cL < min_child_width_px:
                continue
            peak_x = int(round(0.5 * (cL + cR)))
            # Collapse: skip if a prior child's peak is within separation_px.
            if any(abs(peak_x - c["peak_x"]) < min_marker_separation_px
                   for c in children):
                continue
            if peak_x in used_cols:
                continue
            used_cols.update(range(int(cL), int(cR) + 1))
            child = dict(seg)
            child["x_left"] = cL
            child["x_right"] = cR
            child["mid_x"] = peak_x
            child["peak_x"] = peak_x
            child["sam_instance"] = True
            children.append(child)

        if not children:
            out.append(seg)
        else:
            out.extend(children)

    return sorted(out, key=lambda s: s["mid_x"])


def match_segments_to_buildings(
    segments: list[dict],
    projections: list[dict],
    buildings_by_id: dict[str, BuildingRecord],
    min_interval_iou: float = 0.10,
    cross_view_scorer: "callable | None" = None,
) -> list[dict]:
    """For each skyline segment, pick the best-matching projected building.

    Scoring (Phase B):
      base       = interval-IoU (geometric overlap, width-aware)
      width      = log-symmetric width-ratio score (0..1)
      occlusion  = penalty when a much-farther candidate's x-range is
                   contained in a much-nearer candidate's x-range — the
                   farther one is occluded and shouldn't win
      combined   = 0.55 * base + 0.30 * width + 0.15 * (1 - occlusion)

    Width penalty is now disqualifying when ratio > 4× (combined ×0.3)
    to prevent a long warehouse winning over a tower on weak IoU alone.

    Each output segment gets a `match_diagnostics` list with the top-3
    scored candidates so the user can audit flagged failures by reading
    one match against the alternatives the matcher considered.

    Tie-breaking (within the close-combined bucket):
      - Nearest building wins (smallest forward_m).
      - Kiosk-in-front-of-tower exception preserved: nearest < 5 m proxy
        + a tower within 1.5× the nearest distance → prefer the tower.

    F-SKY10 cross-view rerank
    -------------------------
    When ``cross_view_scorer`` is supplied (built by
    ``cross_view.make_cross_view_scorer``), each candidate's combined
    score is blended with a roof-colour-consistency score sampled from
    the per-region satellite image:

        final = 0.85 * combined_intra + 0.15 * cross_view_combined

    A wrong-building match (inland polygon credited for a waterfront
    silhouette) typically has poor cross-view colour agreement, so the
    rerank pushes the second-place candidate (the actually-visible
    building) into first place. The 0.15 weight is intentionally
    conservative — the plan calls for the intra-view IoU/width signal
    to remain dominant; cross-view is a tiebreaker, not an override.
    The per-candidate score lands in ``match_diagnostics[i]["cv"]`` so
    the audit page can show how it influenced the choice.
    """
    def _seg_width(seg: dict) -> float:
        return max(1.0, float(seg["x_right"]) - float(seg["x_left"]))

    def _proj_width(proj: dict) -> float:
        pL = float(proj.get("x_left_px", proj["x_px"] - 10.0))
        pR = float(proj.get("x_right_px", proj["x_px"] + 10.0))
        return max(1.0, abs(pR - pL))

    _proj_range = _proj_x_range  # module-level helper; alias for brevity

    def _interval_iou(seg: dict, proj: dict) -> float:
        sL = float(seg["x_left"])
        sR = float(seg["x_right"])
        pL, pR = _proj_range(proj)
        inter = max(0.0, min(sR, pR) - max(sL, pL))
        union = max(sR, pR) - min(sL, pL)
        return (inter / union) if union > 0 else 0.0

    def _proj_containment(seg: dict, proj: dict) -> float:
        """Thin adapter over ``_proj_containment_in_seg``. Second leg of
        the matcher's acceptance test (narrow projections in wide
        multi-building segments have low IoU but high containment).
        """
        return _proj_containment_in_seg(
            float(seg["x_left"]), float(seg["x_right"]), proj)

    def _width_ratio_score(seg: dict, proj: dict) -> float:
        ratio = _proj_width(proj) / _seg_width(seg)
        if ratio <= 0:
            return 0.0
        log_r = abs(math.log2(ratio))
        return max(0.0, 1.0 - 0.5 * log_r)

    def _occlusion_penalty(cand: dict, all_projs: list[dict]) -> float:
        """Return 0..1 penalty: how much of cand's x-range is covered by
        a MUCH-NEARER candidate. 0 = no occlusion (foreground or no closer
        building). 1 = fully occluded by something dramatically closer.
        """
        cL, cR = _proj_range(cand)
        cw = max(1.0, cR - cL)
        cand_fwd = float(cand.get("forward_m", 1e9))
        # "Much nearer" = at least 1.5× closer than this candidate. Avoids
        # penalizing similar-distance siblings (would over-cull dense rows
        # where all towers have similar forward_m).
        threshold = cand_fwd / 1.5
        max_overlap = 0.0
        for o in all_projs:
            if o is cand:
                continue
            o_fwd = float(o.get("forward_m", 1e9))
            if o_fwd >= threshold:
                continue
            oL, oR = _proj_range(o)
            inter = max(0.0, min(cR, oR) - max(cL, oL))
            cov = inter / cw
            if cov > max_overlap:
                max_overlap = cov
        return min(1.0, max_overlap)

    def _building_lonlat_ring(fid: str) -> list[tuple[float, float]]:
        """Pull a building's exterior ring as (lon, lat) tuples for the
        cross-view scorer. Returns [] when the polygon is missing or has
        no usable exterior (cross-view returns a neutral 0.5 in that
        case so the matcher is undisturbed)."""
        b = buildings_by_id.get(fid)
        if b is None:
            return []
        geom = getattr(b, "geometry", None)
        if geom is None:
            return []
        try:
            xs, ys = geom.exterior.xy
        except Exception:
            return []
        return [(float(x), float(y)) for x, y in zip(xs, ys)]

    out: list[dict] = []
    for seg in segments:
        scored: list[tuple] = []  # (combined, iou, w_score, occ, proj, cv)
        for p in projections:
            iou = _interval_iou(seg, p)
            # F-SKY2.1: accept by IoU OR by ≥ 50 % projection-containment.
            # The containment leg saves narrow projections inside wide
            # multi-building silhouettes that IoU alone discards because
            # the segment width dominates the denominator.
            if iou < min_interval_iou and _proj_containment(seg, p) < 0.5:
                continue
            w_score = _width_ratio_score(seg, p)
            occ = _occlusion_penalty(p, projections)
            combined_intra = 0.55 * iou + 0.30 * w_score + 0.15 * (1.0 - occ)
            # Hard width disqualifier: a building projected 4× wider or
            # narrower than the segment is implausible as a match.
            # EXCEPT when the segment was already cut by ``osm_anchor_silhouettes``
            # — that splitter trims the segment to one tower's width
            # exactly because the candidate footprint is wider than a
            # single tower's silhouette; the ratio > 4 there is a
            # signal of correctness, not implausibility.
            if not seg.get("osm_anchored"):
                ratio = _proj_width(p) / _seg_width(seg)
                if ratio > 4.0 or ratio < 0.25:
                    combined_intra *= 0.3
            # Distance bias: closer buildings score higher. f(d) softly
            # decays from ~1.0 at the camera to ~0.45 at 1500 m. The 0.7
            # baseline + 0.3 weighting on the bonus caps the effect at
            # ~22 % score swing between a 100 m and a 1500 m candidate,
            # enough to flip the bucket when IoU is within ~0.10 of each
            # other — the canonical "inland complex beats waterfront
            # tower" failure mode — without tipping cases where a closer
            # weak-IoU bush wins over the actual tower.
            fwd_m = float(p.get("forward_m", 1e9))
            depth_bonus = 1.0 / (1.0 + fwd_m / 500.0)
            combined_intra *= (0.70 + 0.30 * depth_bonus)
            # F-SKY10: optional cross-view rerank. Score lookup is the
            # one expensive call (satellite ndarray slice + median RGB)
            # so it's gated on the candidate having passed the
            # IoU/containment filter above — we never score buildings
            # the matcher would reject anyway.
            cv = None
            if cross_view_scorer is not None:
                ring = _building_lonlat_ring(p["feature_id"])
                if ring:
                    try:
                        cv = float(cross_view_scorer(seg, ring).get(
                            "combined", 0.5))
                    except Exception:
                        cv = None
            if cv is not None:
                combined = 0.85 * combined_intra + 0.15 * cv
            else:
                combined = combined_intra
            scored.append((combined, iou, w_score, occ, p, cv))

        match: dict | None = None
        diagnostics: list[dict] = []
        if scored:
            scored.sort(key=lambda t: t[0], reverse=True)
            # Capture top-3 diagnostics for the audit field.
            for c, iou, ws, occ, p, cv in scored[:3]:
                d = {
                    "feature_id": p["feature_id"],
                    "combined": round(c, 3),
                    "iou": round(iou, 3),
                    "width_score": round(ws, 3),
                    "occlusion": round(occ, 3),
                    "forward_m": round(float(p.get("forward_m", 0.0)), 1),
                }
                if cv is not None:
                    d["cv"] = round(cv, 3)
                diagnostics.append(d)
            best_combined = scored[0][0]
            bucket = [
                p for c, _iou, _w, _occ, p, _cv in scored
                if c >= best_combined - 0.10
            ]
            # Foreground-takes-precedence: rescue any credible candidate
            # whose x-range CONTAINS the segment's peak_x AND is strictly
            # closer than the bucket's nearest. The hard width
            # disqualifier (×0.3 when ratio > 4) routinely knocks
            # waterfront towers out of the bucket because a single tower
            # silhouette segment is narrower than the building's full
            # projected footprint width — but that nearer tower is the
            # actually-visible-foreground answer. We only rescue when
            # height proxy ≥ 8 m so 1-storey kiosks don't take over from
            # a tagged inland tower. "Strictly closer" (not 1.5×) is
            # required because cross-bay views like Cartagena's seed_5
            # see waterfront and inland rows at similar distances; the
            # difference can be as little as 10-20 %, and the rescue
            # needs to fire there.
            bucket_nearest_fwd = min(
                (float(p.get("forward_m", 1e9)) for p in bucket),
                default=1e9)
            peak_x = float(seg.get(
                "peak_x", (float(seg["x_left"]) + float(seg["x_right"])) * 0.5))
            # Image-derived plausibility: a segment whose silhouette spans
            # ≥ 100 px vertically is, in image space, a foreground building
            # — the closest-strict-rescue can accept untagged candidates
            # for it. Untagged condos / new construction in OSM have
            # ``_height_proxy = 0``; the old gate (≥ 8 m) discarded
            # the exact waterfront-tower rescue case that drives the
            # "inland complex matched, foreground tower ignored" failure.
            seg_pixel_height = (
                float(seg.get("base_y", 0)) - float(seg.get("top_y", 0)))
            seg_is_tall_in_image = seg_pixel_height >= 100.0
            for c, _iou, _w, _occ, p, _cv in scored:
                if p in bucket:
                    continue
                pL, pR = _proj_range(p)
                if not (pL <= peak_x <= pR):
                    continue
                p_fwd = float(p.get("forward_m", 1e9))
                if p_fwd >= bucket_nearest_fwd:
                    continue
                b = buildings_by_id.get(p["feature_id"])
                h_proxy = _height_proxy(b) if b is not None else 0.0
                # Accept rescue when the OSM tag indicates a real
                # building OR the image-space height says the segment
                # itself is a foreground tower. Either signal alone is
                # sufficient — a tagged 10 m+ candidate behind a short
                # mask is still a real building; a 100+ px-tall segment
                # in the image is unmistakably a tower regardless of tag.
                if h_proxy < 8.0 and not seg_is_tall_in_image:
                    continue
                bucket.append(p)
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
        # F-SKY6: record the combined score of the chosen match so the
        # post-pass deduplication can pick a winner when two segments
        # claimed the same OSM building.
        match_combined = 0.0
        if match is not None:
            for c, _iou, _w, _o, p, _cv in scored:
                if p["feature_id"] == match["feature_id"]:
                    match_combined = float(c)
                    break
        out.append({
            **seg,
            "matched_projection": match,
            "matched_combined": match_combined,
            "match_diagnostics": diagnostics,
        })

    # F-SKY6 part 1: enforce one-to-one segment ↔ building uniqueness.
    # The per-segment scoring above lets two adjacent segments pick the
    # same OSM building (observed on Cartagena seed_5 page 31 where
    # segments 1 and 2 both matched b0268). For each duplicated building,
    # keep the segment with the highest matched_combined and clear the
    # match on the losers. Losers become unmatched (no "next-preference"
    # fallback in this pass — that's a follow-up). match_diagnostics is
    # preserved on losers so the audit page still shows what they
    # considered.
    fid_claimants: dict[str, list[int]] = {}
    for i, o in enumerate(out):
        m = o.get("matched_projection")
        if m is None:
            continue
        fid_claimants.setdefault(m["feature_id"], []).append(i)
    for fid, idxs in fid_claimants.items():
        if len(idxs) <= 1:
            continue
        idxs.sort(key=lambda i: -float(out[i].get("matched_combined", 0.0)))
        for loser_i in idxs[1:]:
            out[loser_i]["matched_projection"] = None
            out[loser_i]["matched_combined"] = 0.0
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
        items_sorted = sorted(
            items, key=lambda p: float(p.get("forward_m", 1e9)))
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


# Module-level caches keyed by (id(buildings_list), len(buildings_list)).
# Pure id() keying is unsafe across runs / seed iterations: when Python GCs
# the old list and a NEW list of different size lands at the same memory
# address, an id-only cache returns stale metadata whose
# `building_idx_for_group` points past the end of the new list →
# IndexError downstream in _project_all_buildings_vectorized. Including
# `len(buildings)` in the key forces a recompute on any size change.
# (Two same-size lists at the same id can still collide in principle but
# that's a far narrower window than the size-change failure mode that
# blocked Cartagena runs on 2026-05-24.)
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


# Cache for per-buildings-list metadata (group_starts and building_idx_for_group)
# so the same grouping work isn't repeated for every offset candidate.
# Same (id, len) key for the same reason as _VERT_CACHE.
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

    x_min, x_max = _projected_building_x_ranges(
        buildings, viewpoint, offset_deg, w)
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
        seg_b = is_building_col[xL: xR + 1]
        seg_w = is_water_col[xL: xR + 1]
        hit_cols += int(np.count_nonzero(seg_b))
        water_cols += int(np.count_nonzero(seg_w))
        predicted[xL: xR + 1] = True

    if pred_cols == 0:
        return 0.0

    placement = (hit_cols - water_cols) / float(pred_cols)

    # Miss term: fraction of observed-skyline-columns that the OSM cone fails
    # to cover. The mask itself can have spurious thin building columns; only
    # apply the miss term when there's a non-trivial amount of building in
    # view (≥ 5% of frame width).
    n_observed = int(np.count_nonzero(is_building_col))
    if n_observed >= max(20, int(0.05 * w)):
        unmatched_observed = int(
            np.count_nonzero(is_building_col & ~predicted))
        miss = unmatched_observed / float(n_observed)
    else:
        miss = 0.0

    # Miss-penalty raised 0.3 → 0.6 to better discriminate 180°-symmetric
    # local maxima. At a wrong-by-180° offset, predicted buildings often
    # still land in some real mask-building columns (because there ARE
    # buildings in both directions from many seeds), so `placement` alone
    # gives a misleadingly-positive score. The miss term captures that
    # the wrong offset leaves most of the OBSERVED skyline unexplained.
    score = placement - 0.6 * miss
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
    _c_inv = -median_filter(np.asarray(contour,
                            dtype=np.float32), size=7, mode="nearest")
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

    # Compute a per-view "display IoU" — raw placement (hit ÷ pred_cols)
    # without the joint-optimization miss penalty. The miss penalty makes
    # sense at the joint anchor where we want to cover the full observed
    # skyline, but it over-clips at the per-view level: a view that
    # successfully matches half its buildings can still get
    # best_iou=0.0 because the other half isn't covered, which doesn't
    # reflect that the per-view registration is working. The display
    # value answers "how much of the projected OSM ink lands on
    # actual mask-building columns?" — meaningful per-view.
    if has_mask and best_projections:
        try:
            bmask = np.asarray(building_mask_neural)
            wmask = (
                np.asarray(water_mask_neural)
                if water_mask_neural is not None else None
            )
            w = bmask.shape[1]
            x_min, x_max = _projected_building_x_ranges(
                buildings, captured.viewpoint, best_offset, w)
            if x_min.size > 0:
                build_per_col = bmask.sum(axis=0)
                water_per_col = (
                    wmask.sum(axis=0) if wmask is not None
                    else np.zeros(w, dtype=np.int64)
                )
                is_b_col = (build_per_col > water_per_col) & (build_per_col > 5)
                hit = 0
                pred = 0
                for xL, xR in zip(x_min.tolist(), x_max.tolist()):
                    width = xR - xL + 1
                    if width <= 0:
                        continue
                    pred += width
                    hit += int(np.count_nonzero(is_b_col[xL : xR + 1]))
                if pred > 0:
                    best_iou = float(hit) / float(pred)
        except Exception:
            pass  # leave best_iou as found by the optimizer

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
        # mean pixel residual (legacy display)
        "best_score": best_score,
        "best_iou": best_iou,                # semantic mIoU at best offset
        "best_combined_score": best_combined,
        "projections": matched_projections,
        # all culled-visible buildings at best offset
        "all_projections": best_projections,
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
    *,
    trace=None,
    max_plausible_height_m: float = 300.0,
    compute_floor_period: bool = False,
) -> list[RegisteredBuildingEstimate]:
    # Optional `trace` (HeightTraceRecorder from height_trace.py): when set, the
    # function emits a row at every gate / decision point. Behaviour-neutral —
    # see docs/glass-roof-height-fix-plan.md Phase 1.
    #
    # `compute_floor_period` runs the F-SKY1 facade autocorrelation diagnostic
    # that fills the floor_period_px / floor_confidence / inferred_distance_m
    # / inferred_height_m fields on each estimate. Default OFF: the fields
    # are not rendered in the PDF and the cost (one autocorrelation per
    # building per view) is significant. Set True only for diagnostics.
    contour = np.asarray(registration["contour"], dtype=np.float32)
    best_offset = float(registration["best_offset"])
    best_score = float(registration.get("best_score", float("inf")))
    # Use all culled-visible projections (not just peak-matched) to maximise yield.
    all_proj_list = registration.get(
        "all_projections") or registration["projections"]
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
    sky_mask, building_mask = _neural_sky_and_building_masks(captured.image)

    # Cap residual contribution to confidence: residuals well above ~25 px are
    # essentially noise.
    score_norm = float(np.clip(1.0 - min(best_score, 25.0) / 25.0, 0.05, 1.0))

    # Pre-compute (x_px, forward_m) arrays for the closest-in-column-bin check.
    # When several buildings project into the same image column, the visible
    # rooftop pixel belongs to the *closest* one — crediting a far building for
    # that pixel is the failure mode that produces b0151 143m→284m (tag→pred).
    if all_proj_list:
        _all_x = np.asarray([p["x_px"]
                            for p in all_proj_list], dtype=np.float32)
        _all_fwd = np.asarray([p["forward_m"]
                              for p in all_proj_list], dtype=np.float32)
    else:
        _all_x = np.empty(0, dtype=np.float32)
        _all_fwd = np.empty(0, dtype=np.float32)

    estimates: list[RegisteredBuildingEstimate] = []
    view_name = captured.viewpoint.name
    if trace is not None:
        # Save view artefacts (RGB / contour / building mask) once per view so
        # the diagnostic renderer can draw them later. When `only_feature_id`
        # is set, skip views that don't contain that building — keeps the
        # in-memory artefact dict tight.
        only_fid = getattr(trace, "only_feature_id", None)
        if only_fid is None or only_fid in projections:
            saver = getattr(trace, "save_view", None)
            if saver is not None:
                saver(view_name, captured.image, contour, building_mask)
    for building in buildings:
        fid = building.feature_id
        if trace is not None:
            trace(
                "building_start",
                view_name=view_name,
                feature_id=fid,
                name=building.name,
                tag_h=building.height_tag_m,
                area_m2=building.area_m2,
                terrain_elev_m=float(building.terrain_elev_m),
            )
        proj = projections.get(fid)
        if not proj:
            if trace is not None:
                trace("drop_no_projection", view_name=view_name, feature_id=fid)
            continue

        x_px = int(round(proj["x_px"]))
        if x_px < 0 or x_px >= contour.size:
            if trace is not None:
                trace(
                    "drop_x_out_of_bounds",
                    view_name=view_name,
                    feature_id=fid,
                    x_px=int(x_px),
                    contour_size=int(contour.size),
                )
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
            if trace is not None:
                trace(
                    "drop_forward_too_close",
                    view_name=view_name,
                    feature_id=fid,
                    forward_m=forward,
                )
            continue
        if _all_x.size:
            nearby = np.abs(_all_x - float(proj["x_px"])) <= 15.0
            if nearby.any():
                closest_in_bin = float(_all_fwd[nearby].min())
                if trace is not None:
                    trace(
                        "closest_in_bin",
                        view_name=view_name,
                        feature_id=fid,
                        x_px=float(proj["x_px"]),
                        forward_m=forward,
                        closest_in_bin_m=closest_in_bin,
                        rivals_in_bin=int(nearby.sum()),
                    )
                if forward > closest_in_bin + 200.0:
                    if trace is not None:
                        trace(
                            "drop_closest_in_bin",
                            view_name=view_name,
                            feature_id=fid,
                            forward_m=forward,
                            closest_in_bin_m=closest_in_bin,
                            margin_m=forward - closest_in_bin,
                        )
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

        if trace is not None:
            trace(
                "roof_y_from_mask",
                view_name=view_name,
                feature_id=fid,
                x_range=list(x_range) if x_range is not None else None,
                roof_y_mask=float(roof_y_mask) if roof_y_mask is not None else None,
                coverage=float(coverage) if coverage is not None else None,
                mask_available=bool(building_mask is not None),
            )

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
                    contour_slice = contour[cxL: cxR + 1]
                    finite = contour_slice[np.isfinite(contour_slice)]
                    if finite.size > 0:
                        # Use a representative high point — the 20th percentile
                        # of contour y over the building's projected column
                        # range (avoids being thrown by a single noise pixel
                        # above the spire).
                        contour_top_y = float(np.percentile(finite, 20))
                        gap = y_px - contour_top_y
                        override_fired = False
                        implied_h_val: float | None = None
                        sky_above_ok = True
                        if gap > 15.0:
                            # Sanity-check the gap: it should be plausible as
                            # additional tower height at this distance. A
                            # gap > what the regional cap allows is noise
                            # (cloud edge, lamppost), not glass roof.
                            pitch_rad = math.radians(captured.viewpoint.pitch)
                            angle_at_contour = math.atan(
                                (cy - contour_top_y) / f_px) + pitch_rad
                            top_at_contour = forward * \
                                math.tan(angle_at_contour)
                            implied_h = cam_z + top_at_contour - float(
                                building.terrain_elev_m)
                            implied_h_val = float(implied_h)
                            # 1c: require SegFormer-sky pixels just above the
                            # contour roof. Without sky there, the contour is
                            # not a roof edge (it's a tree canopy or another
                            # building behind), so the glass-facade override
                            # would manufacture height where none exists.
                            if sky_mask is not None:
                                sky_probe_y = int(max(0, int(round(contour_top_y)) - 5))
                                sky_row = sky_mask[sky_probe_y, cxL: cxR + 1]
                                if sky_row.size == 0 or float(np.mean(sky_row)) < 0.5:
                                    sky_above_ok = False
                            if (sky_above_ok
                                    and 0.0 <= implied_h <= max_plausible_height_m):
                                y_px = contour_top_y
                                override_fired = True
                        if trace is not None:
                            trace(
                                "contour_override",
                                view_name=view_name,
                                feature_id=fid,
                                mask_roof_y=float(roof_y_mask),
                                contour_top_y=contour_top_y,
                                gap_px=float(gap),
                                implied_h_m=implied_h_val,
                                sky_above_ok=bool(sky_above_ok),
                                fired=override_fired,
                            )
        else:
            y_px = float(contour[x_px])
            if not np.isfinite(y_px):
                if trace is not None:
                    trace(
                        "drop_contour_nan",
                        view_name=view_name,
                        feature_id=fid,
                        x_px=int(x_px),
                    )
                continue

        pitch_rad = math.radians(captured.viewpoint.pitch)
        angle_rad = math.atan((cy - y_px) / f_px) + pitch_rad
        top_above_camera = forward * math.tan(angle_rad)
        # Height of the building above its own base (ground at footprint):
        # top_z = cam_z + forward*tan(angle); height_above_base = top_z - bld_terrain_z
        height_m = cam_z + top_above_camera - float(building.terrain_elev_m)
        if trace is not None:
            trace(
                "pinhole_math",
                view_name=view_name,
                feature_id=fid,
                y_px=float(y_px),
                cy=float(cy),
                f_px=float(f_px),
                pitch_rad=float(pitch_rad),
                angle_rad=float(angle_rad),
                forward_m=float(forward),
                top_above_camera_m=float(top_above_camera),
                cam_z_m=float(cam_z),
                terrain_elev_m=float(building.terrain_elev_m),
                height_m=float(height_m),
            )
        if not np.isfinite(height_m):
            if trace is not None:
                trace(
                    "drop_height_nan",
                    view_name=view_name,
                    feature_id=fid,
                    height_m=float(height_m) if math.isfinite(height_m) else None,
                )
            continue

        # Geometric y-consistency gate: even before invoking the per-building
        # height proxy, check that the sampled roof_y is geometrically
        # plausible for *any* building at this distance. The ceiling comes
        # from the region config (300 m default — Chicago Willis ≈ 442 m
        # needs a higher cap; Cartagena's tallest is ~206 m so 200 m is
        # appropriate). The floor is 2 m. If the column says the roof is
        # higher in the image than the regional cap could project to this
        # distance, the column actually belongs to a CLOSER building — drop
        # the credit. This catches the "far thin OSM building credited for a
        # near tall building's roof" pattern that the closest-in-column-bin
        # gate only partly catches (it misses cases where the closer
        # building is just outside the ±15 px window because its centroid
        # is far from the wide segment).
        max_plausible_top = float(max_plausible_height_m)
        min_plausible_top = 2.0
        max_top_angle = math.atan(
            (max_plausible_top - cam_z - float(building.terrain_elev_m)) / forward)
        min_top_angle = math.atan(
            (min_plausible_top - cam_z - float(building.terrain_elev_m)) / forward)
        # Convert angle bounds back to y_px bounds (inverted: smaller y = higher).
        min_y_for_building = cy - f_px * math.tan(max_top_angle - pitch_rad)
        max_y_for_building = cy - f_px * math.tan(min_top_angle - pitch_rad)
        if trace is not None:
            trace(
                "geometric_y_gate",
                view_name=view_name,
                feature_id=fid,
                y_px=float(y_px),
                min_y_for_building=float(min_y_for_building),
                max_y_for_building=float(max_y_for_building),
            )
        # Allow ±5 px slack for rounding and mask quantisation.
        if y_px < min_y_for_building - 5 or y_px > max_y_for_building + 5:
            if trace is not None:
                trace(
                    "drop_geometric_gate",
                    view_name=view_name,
                    feature_id=fid,
                    y_px=float(y_px),
                    min_y_for_building=float(min_y_for_building),
                    max_y_for_building=float(max_y_for_building),
                )
            continue

        # Tag-disagreement filter: when we have an OSM height tag, treat it
        # as ground truth and drop per-view estimates that disagree by more
        # than min(2 × tag, 50 m). The tag is the strongest constraint we
        # have on this building's height — predictions outside that band
        # signal a per-view geometry failure (wrong column matched, glass-
        # facade override misfired, projection error). Letting them into
        # the aggregate inflates the headline MAE; dropping them tightens
        # the cross-seed median AND validates the per-view extraction by
        # making the failure visible in the trace log.
        #
        # For untagged buildings we keep a light area-based floor — a
        # multi-square-metre footprint that predicts <3 m almost always
        # means roof_y was sampled in water/sky, not on the building.
        pred_capped = max(height_m, 0.0)
        if building.height_tag_m is not None:
            tag_h = float(building.height_tag_m)
            if tag_h >= 3.0:
                diff = abs(pred_capped - tag_h)
                disagreement_threshold = min(tag_h * 2.0, 50.0)
                if diff > disagreement_threshold:
                    if trace is not None:
                        trace(
                            "drop_tag_disagreement",
                            view_name=view_name,
                            feature_id=fid,
                            tag_h=tag_h,
                            pred_h=float(pred_capped),
                            diff_m=float(diff),
                            threshold_m=float(disagreement_threshold),
                        )
                    continue
        else:
            if building.area_m2 > 50.0 and pred_capped < 3.0:
                if trace is not None:
                    trace(
                        "drop_plausibility_area",
                        view_name=view_name,
                        feature_id=fid,
                        area_m2=float(building.area_m2),
                        pred_h=float(pred_capped),
                    )
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

        # F-SKY1: optional floor-period diagnostic. Skip on tilted views
        # (|pitch| > 4° distorts the per-floor pixel period as a function
        # of y) and on facades shorter than 80 px between roof and base
        # (the autocorrelation needs at least ~2 cycles to lock).
        # _floor_period_for_building returns None when the autocorrelation
        # peak is below the confidence floor, so noise / blank-wall
        # facades silently no-op without producing a fake estimate.
        floor_info: dict = {}
        proj_x_px = float(proj["x_px"])
        if (compute_floor_period
                and abs(captured.viewpoint.pitch) <= 4.0
                and building_mask is not None):
            x_left_px = float(proj.get("x_left_px", proj_x_px - 10.0))
            x_right_px = float(proj.get("x_right_px", proj_x_px + 10.0))
            if x_right_px < x_left_px:
                x_left_px, x_right_px = x_right_px, x_left_px
            y_px_int = int(round(y_px))
            y_base = _building_base_y_from_mask(
                building_mask, int(round(proj_x_px)), y_px_int)
            if y_base is not None and y_base - y_px_int > 80:
                floor_info = _floor_period_for_building(
                    captured.image,
                    building_mask,
                    (int(round(x_left_px)), int(round(x_right_px))),
                    y_px,
                    y_base,
                    f_px=f_px,
                ) or {}

        estimate = RegisteredBuildingEstimate(
            feature_id=building.feature_id,
            name=building.name,
            view_name=captured.viewpoint.name,
            heading_offset_deg=best_offset,
            x_px=proj_x_px,
            y_px=y_px,
            forward_m=forward,
            estimated_height_m=float(max(0.0, height_m)),
            confidence=confidence,
            floor_period_px=floor_info.get("floor_period_px"),
            floor_confidence=floor_info.get("floor_confidence"),
            inferred_distance_m=floor_info.get("inferred_distance_m"),
            inferred_height_m=floor_info.get("inferred_height_m"),
        )
        if trace is not None:
            trace(
                "emit",
                view_name=view_name,
                feature_id=fid,
                x_px=estimate.x_px,
                y_px=estimate.y_px,
                forward_m=estimate.forward_m,
                height_m=estimate.estimated_height_m,
                confidence=estimate.confidence,
                tag_h=building.height_tag_m,
                heading_offset_deg=float(best_offset),
                floor_period_px=estimate.floor_period_px,
                inferred_distance_m=estimate.inferred_distance_m,
                inferred_height_m=estimate.inferred_height_m,
            )
        estimates.append(estimate)

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

        # Per-view outlier rejection inside each seed: a single dissenting
        # view (rooftop occluded by a tree, mis-bounded segment, depth
        # blowup) can pull a seed's median 20+ m. When a seed has ≥3
        # views of the same building, drop any view that's more than
        # 2.5 MAD from the seed's per-view median before computing the
        # seed's authoritative height. Improves cross-seed agreement
        # numbers and aligns with the cross-view smoothing of matches
        # (no point trusting a view whose match was overridden because
        # the consensus disagreed).
        per_view_outliers_dropped = 0
        for seed_name, seed_its in list(per_seed_items.items()):
            if len(seed_its) < 3:
                continue
            view_heights = np.asarray(
                [it.estimated_height_m for it in seed_its], dtype=np.float32)
            v_med = float(np.median(view_heights))
            v_mad = float(np.median(np.abs(view_heights - v_med)))
            if v_mad < 1.0:
                continue
            v_threshold = 2.5 * v_mad
            kept = [
                it for it, h in zip(seed_its, view_heights)
                if abs(float(h) - v_med) <= v_threshold
            ]
            if kept and len(kept) < len(seed_its):
                per_view_outliers_dropped += (len(seed_its) - len(kept))
                per_seed_items[seed_name] = kept

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

        # Build the effective items/heights/confidences (excluding outliers).
        # ``items`` is the raw input; rebuild from the per-view-filtered
        # per_seed_items so per-view outliers are also removed.
        survivors_set = set()
        for seed_name, seed_its in per_seed_items.items():
            if seed_name in outlier_seeds:
                continue
            for it in seed_its:
                survivors_set.add(id(it))
        eff_items = [it for it in items if id(it) in survivors_set]
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
                "n_views_after_outlier_filter": int(eff_heights.size),
                "n_view_outliers_dropped": int(per_view_outliers_dropped),
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
