"""skyline report_plots — split (A3) (_plot_utils)."""
from __future__ import annotations
import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

POLAR_MAX_M = 1000.0

NO_BUILDING_M = 3000.0

def _fmt_optional_float(value: float | None, suffix: str = "", precision: int = 2) -> str:
    """Format an Optional[float] for display in a table cell.

    Returns ``"—"`` for None so missing diagnostics are visually obvious
    (rather than showing "0.00" which could be misread as a real value).
    """
    if value is None:
        return "—"
    try:
        return f"{value:.{precision}f}{suffix}"
    except (TypeError, ValueError):
        return html.escape(str(value))

def _fmt_optional_bool(value: bool | None) -> str:
    if value is None:
        return "—"
    return "yes" if value else "no"

def _osm_height_m(feat: dict) -> float | None:
    """Extract a metres-units height from an OSM building feature.

    Priority: explicit ``height`` / ``building:height`` (stripped of
    units), then ``building:levels`` × 3.0 m as a coarse fallback.
    Returns ``None`` when no usable tag is present.
    """
    props = feat.get("properties") or {}
    for key in ("height", "building:height"):
        v = props.get(key)
        if v is None:
            continue
        try:
            return float(str(v).split()[0].replace("m", "").strip())
        except (ValueError, TypeError):
            continue
    lv = props.get("building:levels")
    if lv is not None:
        try:
            return float(str(lv).split()[0]) * 3.0
        except (ValueError, TypeError):
            pass
    return None

def _osm_polygon_area_m2(ring: list, lat0: float) -> float:
    """Rough planar area in m² for a small lon/lat ring using a local
    equirectangular projection centred at ``lat0``. Accurate to <1% for
    polygons up to a few km wide — good enough to filter sheds / kiosks
    from real buildings.
    """
    import math  # noqa: PLC0415
    mlat = 110540.0
    mlon = 111320.0 * math.cos(math.radians(lat0))
    if len(ring) < 3:
        return 0.0
    s = 0.0
    n = len(ring)
    for i in range(n):
        x1 = ring[i][0] * mlon
        y1 = ring[i][1] * mlat
        x2 = ring[(i + 1) % n][0] * mlon
        y2 = ring[(i + 1) % n][1] * mlat
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5

def _build_osm_nearest_per_degree(
    osm_data: dict | None,
    seed_lat: float,
    seed_lon: float,
    *,
    min_area_m2: float = 150.0,
    require_height_tag: bool = False,
    median_kernel: int = 5,
) -> "np.ndarray":
    """Compute the per-degree nearest-OSM-building distance.

    Filters:
      - ``min_area_m2`` drops small structures (sheds, kiosks) that
        otherwise dominate the nearest signal at close range and read
        as noise.
      - ``require_height_tag`` restricts to polygons with an OSM
        ``height`` / ``building:levels`` tag (usually landmark
        buildings).

    Then applies a 1D median filter (wrap-aware) to the 360-bin signal
    to suppress single-degree spikes from polygon vertex sampling.
    Returns a length-360 array with NaN for bearings with no nearby
    qualifying building.
    """
    import math  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415
    from ..region_pdf import _bearing_deg, _distance_m  # noqa: PLC0415
    out = np.full(360, np.nan, dtype=np.float32)
    if osm_data is None:
        return out
    bucket = [float("inf")] * 360
    for feat in (osm_data.get("buildings", {}).get("features") or [])[:8000]:
        geom = feat.get("geometry") or {}
        if geom.get("type") not in ("Polygon", "MultiPolygon"):
            continue
        if require_height_tag:
            if _osm_height_m(feat) is None:
                continue
        if geom.get("type") == "Polygon":
            rings = [geom.get("coordinates", [[]])[0]]
        else:
            rings = [poly[0] for poly in geom.get("coordinates", [])]
        for ring in rings:
            if not ring or len(ring) < 3:
                continue
            if _osm_polygon_area_m2(ring, seed_lat) < min_area_m2:
                continue
            for (lon, lat) in ring:
                bb = _bearing_deg(seed_lat, seed_lon, lat, lon)
                dd = _distance_m(seed_lat, seed_lon, lat, lon)
                if dd <= POLAR_MAX_M:
                    bi = int(round(bb)) % 360
                    if dd < bucket[bi]:
                        bucket[bi] = dd
    for d in range(360):
        if bucket[d] < float("inf"):
            out[d] = float(bucket[d])
    # Wrap-aware median filter: pad with the same array's tail/head so
    # the filter handles the 0°/360° boundary correctly.
    if median_kernel >= 3 and median_kernel % 2 == 1:
        try:
            from scipy.signal import medfilt  # noqa: PLC0415
            pad = median_kernel // 2
            padded = np.concatenate(
                [out[-pad:], out, out[:pad]])
            # medfilt ignores NaN incorrectly; mask + nanmedian per
            # window so a single-degree spike collapses to its
            # neighbourhood median while NaN gaps stay NaN.
            filt = np.full(out.shape, np.nan, dtype=np.float32)
            for i in range(360):
                win = padded[i: i + median_kernel]
                if np.any(~np.isnan(win)):
                    filt[i] = float(np.nanmedian(win))
            out = filt
        except Exception:
            pass
    return out

def _bearing_xcorr_offset(
    silh: "np.ndarray", osm_nearest: "np.ndarray",
    *, improve_min: float = 0.45,
) -> "tuple[int, float, np.ndarray, bool]":
    """Cross-correlate two dense 360-bin signals over all rotations and
    return ``(best_offset_deg, best_mae, mae_curve, applied)``.

    Both inputs are expected to already be NO_BUILDING-filled (no NaN).
    ``applied`` mirrors the pipeline's gate: the offset is trusted only
    when rotating to it improves on the CURRENT anchor (offset 0) by
    ≥``improve_min`` MAE. Empirically this cleanly separates already-
    aligned seeds (improve <30%) from misaligned ones (improve >50%).

    ``mae_curve`` is the mean-absolute-error at each candidate offset
    (lower = better) for diagnostic plotting.
    """
    import numpy as np  # noqa: PLC0415
    scores = np.full(360, 1e9, dtype=np.float32)
    if silh.shape != (360,) or osm_nearest.shape != (360,):
        return 0, 1e9, scores, False, 0.0
    # Fill any residual NaN with the sentinel so the vectors are dense.
    s = np.where(np.isnan(silh), NO_BUILDING_M, silh)
    o = np.where(np.isnan(osm_nearest), NO_BUILDING_M, osm_nearest)
    for off in range(360):
        rs = np.roll(s, off)
        scores[off] = float(np.mean(np.abs(rs - o)))
    pre_mae = float(scores[0])
    best = int(np.argmin(scores))
    best_mae = float(scores[best])
    shift = best if best <= 180 else best - 360
    improve = (pre_mae - best_mae) / max(pre_mae, 1e-6)
    # Gate on the QUALITY of the destination alignment (best_mae), not
    # the shift magnitude or the possibly-random starting anchor: a low
    # best_mae means the recovered bearing genuinely aligns depth with
    # OSM. Mirrors the pipeline's apply gate.
    applied = (abs(shift) >= 1 and improve > 0.0 and best_mae <= 750.0)
    return shift, best_mae, scores, applied, improve


__all__ = [
    'POLAR_MAX_M',
    'NO_BUILDING_M',
    '_fmt_optional_float',
    '_fmt_optional_bool',
    '_osm_height_m',
    '_osm_polygon_area_m2',
    '_build_osm_nearest_per_degree',
    '_bearing_xcorr_offset',
]
