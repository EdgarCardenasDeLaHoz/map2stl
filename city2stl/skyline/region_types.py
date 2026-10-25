"""city2stl.skyline.region_types - frozen dataclasses shared across the
skyline orchestration modules.

Split out of ``region_pdf.py`` (F-CLEAN14, 2026-06-07) so region_data,
streetview_io, seed_selection, ``_pano/`` and ``_region_render/`` can all
import the shared result/structure types from one place without importing the
heavyweight orchestrator.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np  # noqa: F401  (referenced by lazy string annotations below)

# --- compact pixel storage (T21) -------------------------------------------------------
# A region run keeps every view's pixels until the report, so the result types hold them
# packed and give back the original array on access (same dtype, shape and values).

def pack_mask(arr):
    """``(bits, shape, dtype)`` for a 0/1 mask (1 bit per pixel); any other array unchanged."""
    if not isinstance(arr, np.ndarray) or arr.size == 0:
        return arr
    if arr.dtype != np.bool_:
        if arr.dtype.kind not in "ui" or int(arr.min()) < 0 or int(arr.max()) > 1:
            return arr
    return (np.packbits(arr.ravel().astype(np.uint8, copy=False)), arr.shape, arr.dtype)


def unpack_mask(packed):
    """Inverse of :func:`pack_mask` (a fresh, writable array)."""
    if not isinstance(packed, tuple):
        return packed
    bits, shape, dtype = packed
    return np.unpackbits(bits, count=int(np.prod(shape))).reshape(shape).astype(dtype, copy=False)


def encode_png(arr):
    """Lossless PNG bytes for a uint8 image (an array of any other kind is returned as is)."""
    if not (isinstance(arr, np.ndarray) and arr.dtype == np.uint8 and arr.ndim in (2, 3)
            and arr.size and (arr.ndim == 2 or arr.shape[2] in (1, 3, 4))):
        return arr
    import cv2  # noqa: PLC0415

    ok, buf = cv2.imencode(".png", arr, [cv2.IMWRITE_PNG_COMPRESSION, 1])
    return (buf.tobytes(), arr.shape) if ok else arr


def decode_png(blob):
    """Inverse of :func:`encode_png`."""
    if not isinstance(blob, tuple):
        return blob
    import cv2  # noqa: PLC0415

    data, shape = blob
    out = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_UNCHANGED)
    return out.reshape(shape)


def _packed_property(name, pack, unpack):
    slot = "_packed_" + name

    def _get(self):
        return unpack(self.__dict__.get(slot))

    def _set(self, value):
        self.__dict__[slot] = pack(value)

    return property(_get, _set, doc=f"``{name}``, stored compactly; reads give the original array.")


@dataclass(frozen=True)
class RegionBBox:
    name: str
    north: float
    south: float
    east: float
    west: float


@dataclass(frozen=True)
class SkylinePoint:
    name: str
    lat: float
    lon: float
    heading: float
    source: str
    score: float
    fov: float = 80.0
    pitch: float = 0.0
    # if set, requests target this exact pano (no snapping)
    pano_id: str | None = None


@dataclass(frozen=True)
class StitchedPanoResult:
    """One pano-level pipeline result per seed — the alternative path to the
    per-view aggregation. Used to build the comparison page in the PDF."""
    seed_name: str
    seed_lat: float
    seed_lon: float
    pano_image: np.ndarray
    band_y: tuple[int, int] | None
    matched_segments: list  # list[dict]
    n_segments: int
    n_matched: int
    n_buildings_in_view: int
    anchor_offset_deg: float = 0.0  # joint-optimized pano-to-geo offset
    # Per-column compass heading (matches pano_image.shape[1]). Used by the
    # PDF page to overlay yellow heading ticks so the user can verify the
    # column-to-heading mapping visually.
    headings_per_col: np.ndarray | None = None
    # Per-class pano-stitched masks (same view set + sort as
    # ``pano_image`` so the column geometry agrees). Populated by
    # ``_build_and_detect_pano`` for the HTML report's SegFormer-mask
    # pano layer; ``None`` when unavailable.
    pano_building_mask: np.ndarray | None = None
    pano_water_mask: np.ndarray | None = None
    # Deprecated: never stored, reads give None (nothing read it; T21).
    pano_sky_mask: np.ndarray | None = None
    pano_vegetation_mask: np.ndarray | None = None
    # Building instances (``building_instances``, MobileSAM): int label per pixel, 0 = none,
    # on the ``pano_image`` grid; the seg overlay colours each instance. None when unavailable.
    pano_instances: np.ndarray | None = None
    # F-SKY24: pano-wide depth map (Depth Anything V2 inverse depth,
    # [0, 1] scaled). Computed once in ``_build_and_detect_pano`` so the
    # splitter + downstream renderers (depth pano, reconstruction polar
    # plot) all share one inference instead of recomputing per-tab.
    pano_depth: np.ndarray | None = None
    # OSM-anchored depth→distance scale: median(osm_dist / sqrt(d_inv))
    # over matched towers, so ``dist_m = sqrt(1 - depth_rel) * depth_scale``
    # is calibrated to real metres. Computed once in the pipeline and
    # shared by BOTH the Reconstruction polar plot and the Distance scan
    # so they agree (previously the scan used a hardcoded 1450 while the
    # recon used this anchor, giving the ~600 vs ~1000 m mismatch).
    # Falls back to 1450.0 when no OSM anchor is derivable.
    depth_scale: float = 1450.0
    # F-SKY25 horizon-pitch geometric distance model:
    #   dist_m = geom_K / (base_row - geom_horizon_row)
    # Fitted (2 params) from matched towers' base rows vs OSM distance.
    # Unlike the saturated depth value, the base-row position tracks real
    # near/far variation, so this gives a non-flat distance estimate for
    # every column. ``None`` when the fit wasn't possible (<3 towers or
    # degenerate base-row spread).
    geom_K: float | None = None
    geom_horizon_row: float | None = None
    # Bearing-recovery shift actually APPLIED to pano_headings (deg); 0.0
    # when the gate skipped (anchor kept). Surfaced in the index summary.
    bearing_shift_deg: float = 0.0
    # F-DET A7 (Type 2 diagnosis): metres from the seed's proposed position to the Street View
    # pano it resolved to (None when unknown), and the median column IoU of each matched
    # segment with its matched footprint's projection (None when nothing matched).
    snap_m: float | None = None
    footprint_overlap: float | None = None


@dataclass(frozen=True)
class SeedViewRegistration:
    seed_name: str
    seed_lat: float
    seed_lon: float
    heading: float
    fov: float
    registration_score: float
    best_offset: float
    estimates_count: int
    # The registration overlay. Not stored when ``overlay_spec`` is set: reading it redraws
    # the overlay from ``raw_image`` and the spec (T21). Pass None then.
    image: np.ndarray | None
    matched_segments: list  # list[dict] from match_segments_to_buildings
    is_aerial: bool = False
    iou: float = 0.0  # semantic mIoU at best offset (heading-quality signal)
    # Vertical building band (y_top, y_bot) computed from the SegFormer
    # building mask. Used to vertically crop the displayed image so views
    # with lots of water/sky don't waste page area. None = no crop, show
    # full frame.
    band_y: tuple[int, int] | None = None
    # True for seeds declared in sites/<region>.json `negative_seeds`. The
    # view is still rendered (so the user can verify the pipeline isn't
    # producing spurious estimates) but no heights are aggregated.
    is_negative: bool = False
    # Why this seed was marked bad (e.g. "low building coverage 3%") when
    # auto-rejected by the pano coverage screen; None for config-declared
    # negatives. Surfaced in the report so bad panos are labelled.
    negative_reason: str | None = None
    # F-SKY4: the SegFormer building mask for this view. Persisted into the
    # registration so the PDF renderer can overlay it without depending on
    # the bounded in-memory neural cache (which evicts after 16 entries and
    # otherwise misses by PDF-render time on multi-seed runs).
    building_mask: np.ndarray | None = None
    # Additional SegFormer class masks captured at the same time as
    # ``building_mask``. Free piggy-back on the already-cached label_map
    # forward pass. Used by the HTML report to render a "all
    # segmentations on grayscale" diagnostic image so reviewers can see
    # what every class label looked like, not just buildings.
    sky_mask: np.ndarray | None = None
    water_mask: np.ndarray | None = None
    vegetation_mask: np.ndarray | None = None
    # Raw RGB frame (pre-overlay) used as the grayscale base for the
    # multi-class mask diagnostic. Kept separately from ``image`` because
    # ``image`` is overwritten with the registration-overlay drawing.
    raw_image: np.ndarray | None = None
    # F-SKY13 Phase B: pano↔OSM-coastline registration score at the
    # recovered (or manually overridden) heading offset. Per-seed, not
    # per-view — populated only when pano-recovery is enabled and the
    # seed has OSM coastline within the 1 km window. None elsewhere.
    pano_osm_iou: float | None = None
    pano_osm_n_keypoints: int | None = None
    # F-SKY11.1 pano-coastline heading recovery diagnostics. The sweep
    # over candidate heading offsets returns the argmax (recovered_offset),
    # the peak score at that offset, and the score-curve sigma (a
    # confidence proxy — flat curves get high sigma). These already drive
    # the drive_anchor decision; surfacing them lets the HTML reader see
    # WHY a seed's registration succeeded or failed.
    pano_recovered_offset_deg: float | None = None
    pano_recovered_peak: float | None = None
    pano_recovered_sigma: float | None = None
    pano_water_frac: float | None = None
    # F-SKY13 Phase B: pano-derived coastline projected back to lon/lat
    # (one point per pano column where water was detected, sampled at a
    # coarse stride). Drawn as a dashed orange polyline on the minimap so
    # the user can see where the pano "thinks" the coast is. None when
    # pano-recovery is disabled or there's no water in the pano.
    pano_projected_coastline: list[tuple[float, float]] | None = None
    # F-SKY18: depth-snapped pano vegetation base points (lon/lat). Second
    # bearing-landmark class alongside coastline; drawn as green dots on the
    # minimap and (Phase 3) fed into heading registration.
    pano_projected_vegetation: list[tuple[float, float]] | None = None
    # F-SKY15: per-view list of ``RegisteredBuildingEstimate`` records
    # (with F-SKY12 depth fields when SKYLINE_CV_F_SKY12=1). Persisted so
    # the HTML diagnostic report can render the depth diagnostics today
    # without waiting for the PDF rendering path. None elsewhere.
    view_estimates: list | None = None
    # T21: what ``_region_render._draw.overlay_spec`` copied when the overlay was drawn.
    overlay_spec: dict | None = None

    def set_overlay_spec(self, spec: dict | None) -> None:
        """Replace the overlay (frozen dataclass: the one sanctioned mutation); drops any stored image."""
        object.__setattr__(self, "overlay_spec", spec)
        self.__dict__["_image_arr"] = None
        self.__dict__["_image_is_raw"] = False

    @property
    def stored_bytes(self) -> int:
        """Bytes of pixel data this row holds (the T21 measure)."""
        return _stored_bytes(self.__dict__)


def _stored_bytes(d: dict) -> int:
    total = 0
    for k, v in d.items():
        if not (k.startswith("_packed_") or k == "_image_arr"):
            continue
        if isinstance(v, tuple) and v and isinstance(v[0], (bytes, np.ndarray)):
            total += v[0].nbytes if isinstance(v[0], np.ndarray) else len(v[0])
        elif isinstance(v, np.ndarray):
            total += v.nbytes
    return total


def _view_image_get(self):
    arr = self.__dict__.get("_image_arr")
    if arr is not None:
        return arr
    raw = self.raw_image
    if raw is None:
        return None
    if self.__dict__.get("_image_is_raw"):
        return raw
    if self.overlay_spec is not None:
        from ._region_render._draw import render_overlay  # noqa: PLC0415

        return render_overlay(raw, self.overlay_spec)
    return None


def _view_image_set(self, value):
    self.__dict__["_image_arr"] = value
    self.__dict__["_image_is_raw"] = False


def _view_raw_set(self, value):
    # the overlay-less rows pass one array as both ``image`` and ``raw_image``: keep one copy
    if value is not None and value is self.__dict__.get("_image_arr"):
        self.__dict__["_image_arr"] = None
        self.__dict__["_image_is_raw"] = True
    self.__dict__["_packed_raw_image"] = encode_png(value)


for _n in ("building_mask", "sky_mask", "water_mask", "vegetation_mask"):
    setattr(SeedViewRegistration, _n, _packed_property(_n, pack_mask, unpack_mask))
SeedViewRegistration.raw_image = property(
    lambda self: decode_png(self.__dict__.get("_packed_raw_image")), _view_raw_set,
    doc="Raw frame, stored as PNG bytes; reads decode it.")
SeedViewRegistration.image = property(
    _view_image_get, _view_image_set,
    doc="Overlay image: stored as given, else redrawn from ``raw_image`` + ``overlay_spec``.")
for _n in ("pano_building_mask", "pano_water_mask", "pano_vegetation_mask"):
    setattr(StitchedPanoResult, _n, _packed_property(_n, pack_mask, unpack_mask))
StitchedPanoResult.pano_sky_mask = property(lambda self: None, lambda self, value: None,
                                            doc="Never stored (nothing reads it).")
StitchedPanoResult.stored_bytes = property(lambda self: _stored_bytes(self.__dict__))
