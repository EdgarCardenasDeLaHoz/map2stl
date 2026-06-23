"""height_diagnostic_report.py — Building height diagnostic HTML report.

Exercises the full provider stack (3DEP, GHSL, nDSM, Copernicus, WSF3D,
OpenBuildings) for a region bbox, assigns per-building heights, runs
rule-based roof classification from nadir satellite imagery, and writes a
self-contained HTML report for visual assessment before integrating into the
main STL pipeline.

Usage
-----
    cd strm2stl
    python -m city2stl.skyline.scripts.height_diagnostic_report --region cartagena
    python -m city2stl.skyline.scripts.height_diagnostic_report --region boston
    python -m city2stl.skyline.scripts.height_diagnostic_report --bbox 42.4 42.3 -71.0 -71.1

Output: output/height_reports/<region>_height_report.html
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import logging
import math
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("height_report")

# ---------------------------------------------------------------------------
# Provider registry (ordered by confidence, highest first)
# ---------------------------------------------------------------------------

def _build_registry() -> list[dict]:
    """Import providers lazily so missing optional deps don't crash startup."""
    from city2stl.skyline.height.providers.lidar_3dep import LiDAR3DEPProvider
    from city2stl.skyline.height.providers.ndsm import NDSMProvider
    from city2stl.skyline.height.providers.copernicus import CopernicusProvider
    from city2stl.skyline.height.providers.open_buildings import OpenBuildingsProvider
    from city2stl.skyline.height.providers.wsf3d import WSF3DProvider
    from city2stl.skyline.height.providers.ghsl import GHSLProvider

    return [
        {"name": "3DEP LiDAR",     "instance": LiDAR3DEPProvider(),    "confidence": 0.95, "res_m": 1.0,   "color": "#4caf50"},
        {"name": "nDSM",           "instance": NDSMProvider(),           "confidence": 0.80, "res_m": 30.0,  "color": "#2196f3"},
        {"name": "Copernicus",     "instance": CopernicusProvider(),     "confidence": 0.70, "res_m": 10.0,  "color": "#9c27b0"},
        {"name": "OpenBuildings",  "instance": OpenBuildingsProvider(),  "confidence": 0.60, "res_m": 5.0,   "color": "#ff9800"},
        {"name": "WSF3D",          "instance": WSF3DProvider(),          "confidence": 0.50, "res_m": 90.0,  "color": "#00bcd4"},
        {"name": "GHSL",           "instance": GHSLProvider(),           "confidence": 0.40, "res_m": 100.0, "color": "#f44336"},
    ]


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclass
class ProviderResult:
    name: str
    color: str
    confidence: float
    res_m: float
    status: str          # "ok" | "no_coverage" | "failed" | "no_data"
    error: str = ""
    stats: dict = field(default_factory=dict)
    raster: np.ndarray | None = None


@dataclass
class BuildingEntry:
    osm_id: str
    centroid_lat: float
    centroid_lon: float
    area_m2: float
    osm_height_m: float | None
    osm_source: str
    assigned_height_m: float
    assigned_source: str
    roof_type: str
    roof_confidence: float


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _centroid(rings: list) -> tuple[float, float]:
    coords = rings[0] if rings else []
    if not coords:
        return 0.0, 0.0
    lons = [c[0] for c in coords]
    lats = [c[1] for c in coords]
    return sum(lats) / len(lats), sum(lons) / len(lons)


def _polygon_area_m2(coords: list) -> float:
    """Shoelace area for a list of [lon, lat] coords, converted to m²."""
    n = len(coords)
    if n < 3:
        return 0.0
    R = 6_371_000.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        lon1, lat1 = math.radians(coords[i][0]), math.radians(coords[i][1])
        lon2, lat2 = math.radians(coords[j][0]), math.radians(coords[j][1])
        area += (lon2 - lon1) * (2 + math.sin(lat1) + math.sin(lat2))
    return abs(area) * R * R / 2.0


def _sample_raster(raster: np.ndarray, lat: float, lon: float,
                   bbox: tuple[float, float, float, float]) -> float | None:
    """Sample a height raster at (lat, lon) using linear bbox mapping."""
    north, south, east, west = bbox
    if not (south <= lat <= north and west <= lon <= east):
        return None
    H, W = raster.shape
    row = int((north - lat) / (north - south) * H)
    col = int((lon - west) / (east - west) * W)
    row = max(0, min(H - 1, row))
    col = max(0, min(W - 1, col))
    v = float(raster[row, col])
    return None if math.isnan(v) else v


# ---------------------------------------------------------------------------
# OSM height tag parsing
# ---------------------------------------------------------------------------

def _osm_height(props: dict) -> tuple[float | None, str]:
    """Return (height_m, source) from OSM properties, or (None, 'none')."""
    h = props.get("height")
    if h is not None:
        try:
            return float(str(h).split()[0]), "osm_tag"
        except (ValueError, TypeError):
            pass
    lvl = props.get("building:levels")
    if lvl is not None:
        try:
            return float(lvl) * 3.0, "osm_levels"
        except (ValueError, TypeError):
            pass
    return None, "none"


# ---------------------------------------------------------------------------
# Rule-based roof classification
# ---------------------------------------------------------------------------

def _classify_roof(crop: np.ndarray) -> tuple[str, float]:
    """Classify roof type from a nadir satellite crop (H×W×3 RGB uint8).

    Rule-based approach — no ML model needed.

    Returns (roof_type, confidence) where roof_type is one of:
      flat     — uniform grey/dark surface, low variance (commercial towers)
      pitched  — strong bilateral brightness gradient (residential)
      stepped  — multiple distinct brightness bands (terraced/podium)
      complex  — high edge density (irregular or multi-section roof)
      unknown  — crop too small or featureless

    Why rule-based:
      Pitched roofs cast a shadow on one half and reflect directly on the
      other, creating a strong asymmetric brightness gradient. Flat roofs
      have uniform reflectance. Stepped roofs produce multiple parallel
      brightness bands. Complex roofs have many small edges.

    SAM would give more accurate polygon boundaries but runs ~500MB and
    2-5s per image — a future upgrade path when accuracy warrants it.
    """
    if crop is None or crop.size == 0 or min(crop.shape[:2]) < 8:
        return "unknown", 0.0

    gray = crop.astype(np.float32).mean(axis=2) if crop.ndim == 3 else crop.astype(np.float32)
    g_min, g_max = gray.min(), gray.max()
    if g_max - g_min < 5:
        return "unknown", 0.3  # all-black or all-white tile — nodata

    gray = (gray - g_min) / (g_max - g_min + 1e-6)

    variance = float(gray.var())
    if variance < 0.005:
        return "flat", 0.85

    H, W = gray.shape
    # Bilateral brightness split: compare quadrants
    top   = gray[:H // 2, :].mean()
    bot   = gray[H // 2:, :].mean()
    left  = gray[:, :W // 2].mean()
    right = gray[:, W // 2:].mean()
    asymmetry = max(abs(top - bot), abs(left - right))

    if asymmetry > 0.22:
        conf = min(0.55 + asymmetry, 0.90)
        return "pitched", round(conf, 2)

    # Edge density via simple gradient magnitude
    dy = np.diff(gray, axis=0)
    dx = np.diff(gray, axis=1)
    if dy.shape[0] > 1 and dx.shape[1] > 1:
        grad = np.sqrt(dy[:, :-1] ** 2 + dx[:-1, :] ** 2)
        edge_density = float((grad > 0.08).mean())
    else:
        edge_density = 0.0

    if edge_density > 0.18:
        return "complex", round(0.5 + edge_density * 0.5, 2)

    # Stepped: multiple bands in row-mean profile
    row_means = gray.mean(axis=1)
    d = np.diff(row_means)
    sign_changes = int(np.sum(np.diff(np.sign(d)) != 0))
    if sign_changes >= 6:
        return "stepped", 0.60

    return "flat", round(0.65 - variance * 2, 2)


def _crop_footprint(sat_img: np.ndarray,
                    projector: Callable[[float, float], tuple[float, float]],
                    coords: list,
                    pad_px: int = 4) -> np.ndarray | None:
    """Crop a building footprint from the satellite image."""
    try:
        pts = [projector(c[0], c[1]) for c in coords]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        x0 = max(0, int(min(xs)) - pad_px)
        x1 = min(sat_img.shape[1] - 1, int(max(xs)) + pad_px)
        y0 = max(0, int(min(ys)) - pad_px)
        y1 = min(sat_img.shape[0] - 1, int(max(ys)) + pad_px)
        if x1 <= x0 or y1 <= y0:
            return None
        return sat_img[y0:y1, x0:x1]
    except Exception:
        return None


# ---------------------------------------------------------------------------
# PNG helpers
# ---------------------------------------------------------------------------

_COLORMAPS = {
    "plasma": [
        (0.050, 0.030, 0.527),
        (0.494, 0.012, 0.657),
        (0.799, 0.278, 0.469),
        (0.974, 0.562, 0.254),
        (0.940, 0.975, 0.131),
    ],
    "rdylgn": [
        (0.647, 0.000, 0.149),
        (0.843, 0.188, 0.153),
        (0.957, 0.647, 0.302),
        (0.663, 0.851, 0.416),
        (0.102, 0.596, 0.314),
    ],
}


def _apply_colormap(arr: np.ndarray, cmap: str = "plasma") -> np.ndarray:
    """Map float array [0,1] to RGB uint8 using a 5-stop gradient."""
    stops = _COLORMAPS.get(cmap, _COLORMAPS["plasma"])
    n = len(stops) - 1
    out = np.zeros((*arr.shape, 3), dtype=np.uint8)
    for i in range(n):
        lo, hi = i / n, (i + 1) / n
        mask = (arr >= lo) & (arr < hi if i < n - 1 else arr <= 1.0)
        t = (arr[mask] - lo) / (hi - lo + 1e-9)
        for ch in range(3):
            out[mask, ch] = np.clip(
                (stops[i][ch] * (1 - t) + stops[i + 1][ch] * t) * 255, 0, 255
            ).astype(np.uint8)
    return out


def _raster_to_png_b64(raster: np.ndarray, vmin: float | None = None,
                        vmax: float | None = None, cmap: str = "plasma",
                        width_px: int = 400, height_px: int = 300) -> str:
    """Convert a float raster to a base64-encoded PNG with colormap."""
    from PIL import Image

    valid = raster[~np.isnan(raster)]
    if valid.size == 0:
        img = Image.new("RGB", (width_px, height_px), (30, 30, 30))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        return base64.b64encode(buf.getvalue()).decode()

    lo = float(vmin) if vmin is not None else float(np.nanpercentile(raster, 2))
    hi = float(vmax) if vmax is not None else float(np.nanpercentile(raster, 98))
    if hi <= lo:
        hi = lo + 1.0

    norm = np.clip((raster - lo) / (hi - lo), 0.0, 1.0)
    norm = np.where(np.isnan(raster), 0.0, norm)
    rgb = _apply_colormap(norm.astype(np.float32), cmap)

    # Mask NaN pixels as dark grey
    nan_mask = np.isnan(raster)
    rgb[nan_mask] = (20, 20, 20)

    img = Image.fromarray(rgb, "RGB").resize((width_px, height_px), Image.NEAREST)
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _sat_overlay_png_b64(sat_img: np.ndarray,
                          buildings: list[BuildingEntry],
                          projector: Callable[[float, float], tuple[float, float]],
                          osm_data: dict,
                          mode: str = "source",
                          width_px: int = 700,
                          height_px: int = 500) -> str:
    """Satellite image with footprints colored by source or height or roof."""
    from PIL import Image, ImageDraw

    _SOURCE_COLORS = {
        "3DEP LiDAR":    (76,  175, 80,  160),
        "nDSM":          (33,  150, 243, 160),
        "Copernicus":    (156, 39,  176, 160),
        "OpenBuildings": (255, 152, 0,   160),
        "WSF3D":         (0,   188, 212, 160),
        "GHSL":          (244, 67,  54,  160),
        "OSM Tags":      (255, 235, 59,  200),
        "osm_tag":       (255, 235, 59,  200),
        "osm_levels":    (255, 193, 7,   180),
        "default":       (100, 100, 100, 80),
    }
    _ROOF_COLORS = {
        "flat":    (200, 200, 200, 140),
        "pitched": (255, 140, 0,   140),
        "stepped": (100, 200, 255, 140),
        "complex": (200, 100, 255, 140),
        "unknown": (80,  80,  80,  80),
    }

    img_h, img_w = sat_img.shape[:2]
    base = Image.fromarray(sat_img, "RGB").resize((width_px, height_px), Image.BILINEAR)
    overlay = Image.new("RGBA", (width_px, height_px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    sx = width_px / img_w
    sy = height_px / img_h

    features = (osm_data.get("buildings") or {}).get("features") or []
    bld_map = {b.osm_id: b for b in buildings}

    for feat in features[:5000]:
        rings = (feat.get("geometry") or {}).get("coordinates") or []
        if not rings:
            continue
        coords = rings[0]
        fid = str((feat.get("properties") or {}).get("@id", ""))
        bld = bld_map.get(fid)
        if bld is None:
            continue

        if mode == "source":
            color = _SOURCE_COLORS.get(bld.assigned_source, _SOURCE_COLORS["default"])
        elif mode == "roof":
            color = _ROOF_COLORS.get(bld.roof_type, _ROOF_COLORS["unknown"])
        else:
            color = _SOURCE_COLORS["default"]

        try:
            pts_raw = [projector(c[0], c[1]) for c in coords]
            pts = [(x * sx, y * sy) for x, y in pts_raw]
            if len(pts) >= 3:
                draw.polygon(pts, fill=color, outline=(255, 255, 255, 60))
        except Exception:
            pass

    base_rgba = base.convert("RGBA")
    combined = Image.alpha_composite(base_rgba, overlay).convert("RGB")
    buf = io.BytesIO()
    combined.save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _colorbar_png_b64(vmin: float, vmax: float, cmap: str = "plasma",
                       label: str = "Height (m)", width_px: int = 300,
                       height_px: int = 28) -> str:
    """Horizontal colorbar PNG."""
    from PIL import Image

    arr = np.linspace(0.0, 1.0, width_px, dtype=np.float32)
    bar_row = _apply_colormap(arr[np.newaxis, :], cmap)[0]
    img_arr = np.tile(bar_row, (height_px, 1, 1)).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(img_arr, "RGB").save(buf, "PNG")
    return base64.b64encode(buf.getvalue()).decode()


# ---------------------------------------------------------------------------
# HTML generation
# ---------------------------------------------------------------------------

_CSS = """
body { margin: 0; background: #111; color: #ddd; font: 14px/1.5 'Inter', system-ui, sans-serif; }
h1   { margin: 0 0 4px; font-size: 1.6rem; color: #fff; }
h2   { margin: 20px 0 8px; font-size: 1.1rem; color: #aef; border-bottom: 1px solid #333; padding-bottom: 4px; }
.meta { color: #888; font-size: 0.82rem; margin-bottom: 18px; }
.wrap { max-width: 1200px; margin: 0 auto; padding: 24px 18px; }
.cards { display: flex; flex-wrap: wrap; gap: 14px; margin-bottom: 18px; }
.card { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; padding: 12px 14px; }
.card.ok   { border-left: 3px solid #4caf50; }
.card.fail { border-left: 3px solid #f44336; }
.card.none { border-left: 3px solid #888; }
.card .title { font-weight: 600; font-size: 0.9rem; color: #eee; }
.card .sub   { font-size: 0.78rem; color: #888; margin-top: 2px; }
.card .stat  { font-size: 0.82rem; color: #ccc; margin-top: 4px; }
.panel { background: #1a1a1a; border: 1px solid #2a2a2a; border-radius: 6px; padding: 14px; margin-bottom: 14px; }
.panel img { display: block; border-radius: 4px; max-width: 100%; }
.panel .cap { font-size: 0.78rem; color: #888; margin-top: 6px; }
.grid2 { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; }
.grid3 { display: grid; grid-template-columns: 1fr 1fr 1fr; gap: 14px; }
table { border-collapse: collapse; width: 100%; font-size: 0.82rem; }
th { background: #222; color: #aaa; padding: 6px 10px; text-align: left; font-weight: 500; }
td { padding: 5px 10px; border-bottom: 1px solid #222; color: #ccc; }
tr:hover td { background: #1d1d1d; }
.src-3dep  { color: #4caf50; }
.src-ndsm  { color: #2196f3; }
.src-cop   { color: #ce93d8; }
.src-ob    { color: #ff9800; }
.src-wsf   { color: #00bcd4; }
.src-ghsl  { color: #f44336; }
.src-osm   { color: #ffeb3b; }
.src-def   { color: #888; }
.roof-flat    { color: #bbb; }
.roof-pitched { color: #ff9800; }
.roof-stepped { color: #00bcd4; }
.roof-complex { color: #ce93d8; }
.roof-unknown { color: #666; }
.legend { display: flex; flex-wrap: wrap; gap: 10px; font-size: 0.78rem; margin-bottom: 10px; }
.legend span { display: flex; align-items: center; gap: 5px; }
.swatch { width: 12px; height: 12px; border-radius: 2px; display: inline-block; }
"""

_SOURCE_CSS = {
    "3DEP LiDAR": "src-3dep", "nDSM": "src-ndsm", "Copernicus": "src-cop",
    "OpenBuildings": "src-ob", "WSF3D": "src-wsf", "GHSL": "src-ghsl",
    "OSM Tags": "src-osm", "osm_tag": "src-osm", "osm_levels": "src-osm",
    "default": "src-def",
}

_ROOF_CSS = {"flat": "roof-flat", "pitched": "roof-pitched",
             "stepped": "roof-stepped", "complex": "roof-complex",
             "unknown": "roof-unknown"}


def _fmt(v: float | None, decimals: int = 1, suffix: str = "") -> str:
    if v is None:
        return "—"
    return f"{v:.{decimals}f}{suffix}"


def _source_class(src: str) -> str:
    return _SOURCE_CSS.get(src, "src-def")


def _generate_html(
    region_name: str,
    bbox: tuple[float, float, float, float],
    provider_results: list[ProviderResult],
    buildings: list[BuildingEntry],
    sat_source_b64: str | None,
    sat_roof_b64: str | None,
    raster_panels: list[tuple[str, str, str, str]],  # (name, img_b64, colorbar_b64, caption)
    merged_b64: str | None,
    elapsed_s: float,
) -> str:
    north, south, east, west = bbox
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ok_providers = [p for p in provider_results if p.status == "ok"]
    fail_providers = [p for p in provider_results if p.status not in ("ok", "no_coverage")]
    skip_providers = [p for p in provider_results if p.status == "no_coverage"]

    # --- provider summary cards ---
    cards_html = ""
    for p in provider_results:
        css = "ok" if p.status == "ok" else ("fail" if p.status == "failed" else "none")
        stat_html = ""
        if p.status == "ok" and p.stats:
            s = p.stats
            bldg_n   = s.get("bldg_n", 0)
            bldg_pct = s.get("bldg_pct", 0.0)
            if p.name == "OSM Tags":
                stat_html = (
                    f"<div class='stat'>"
                    f"<b>{bldg_n} bldgs ({bldg_pct:.1f}%)</b> &nbsp;"
                    f"mean {_fmt(s.get('mean_m'))} m &nbsp;"
                    f"max {_fmt(s.get('max_m'))} m &nbsp;"
                    f"p95 {_fmt(s.get('p95_m'))} m"
                    f"</div>"
                    f"<div class='sub'>Ground-truth — overrides all raster providers</div>"
                )
            else:
                above1m = s.get("above1m_pct")
                raster_note = (
                    f" &nbsp;<span style='color:#666;font-size:0.75rem'>"
                    f"(raster: {_fmt(s.get('coverage_pct'))}% non-NaN, "
                    f"{_fmt(above1m)}% &gt;1 m)</span>"
                ) if above1m is not None else (
                    f" &nbsp;<span style='color:#666;font-size:0.75rem'>"
                    f"(raster: {_fmt(s.get('coverage_pct'))}% non-NaN)</span>"
                )
                stat_html = (
                    f"<div class='stat'>"
                    f"<b>{bldg_n} bldgs ({bldg_pct:.1f}%)</b>{raster_note} &nbsp;"
                    f"mean {_fmt(s.get('mean_m'))} m &nbsp;"
                    f"max {_fmt(s.get('max_m'))} m &nbsp;"
                    f"p95 {_fmt(s.get('p95_m'))} m"
                    f"</div>"
                )
        elif p.status == "failed":
            stat_html = f"<div class='stat' style='color:#f44'>Error: {p.error[:80]}</div>"
        elif p.status == "no_coverage":
            stat_html = "<div class='sub'>No coverage for this region</div>"
        elif p.status == "no_data":
            stat_html = "<div class='sub'>Returned empty raster</div>"
        cards_html += (
            f"<div class='card {css}'>"
            f"<div class='title' style='color:{p.color}'>{p.name}</div>"
            f"<div class='sub'>{p.res_m:.0f} m resolution &nbsp;·&nbsp; "
            f"confidence {p.confidence:.0%}</div>"
            f"{stat_html}"
            f"</div>\n"
        )

    # --- raster panels ---
    panels_html = ""
    if raster_panels:
        panels_html += "<div class='grid3'>\n"
        for name, img_b64, cbar_b64, caption in raster_panels:
            panels_html += (
                f"<div class='panel'>"
                f"<img src='data:image/png;base64,{img_b64}' width='100%'>"
                f"<img src='data:image/png;base64,{cbar_b64}' width='100%' style='margin-top:4px'>"
                f"<div class='cap'>{caption}</div>"
                f"</div>\n"
            )
        panels_html += "</div>\n"

    # --- satellite overlays ---
    sat_html = ""
    legend_source = "".join(
        f"<span><span class='swatch' style='background:{c}'></span>{n}</span>"
        for n, c in [
            ("OSM tag", "#ffeb3b"), ("OSM levels", "#ffc107"),
            ("3DEP", "#4caf50"), ("nDSM", "#2196f3"), ("Copernicus", "#9c27b0"),
            ("OpenBuildings", "#ff9800"), ("WSF3D", "#00bcd4"), ("GHSL", "#f44336"),
            ("Default", "#555"),
        ]
    )
    legend_roof = "".join(
        f"<span><span class='swatch' style='background:{c}'></span>{n}</span>"
        for n, c in [("Flat", "#888"), ("Pitched", "#ff9800"),
                     ("Stepped", "#00bcd4"), ("Complex", "#ce93d8"), ("Unknown", "#444")]
    )

    if sat_source_b64:
        sat_html += (
            f"<div class='panel'>"
            f"<div class='legend'>{legend_source}</div>"
            f"<img src='data:image/png;base64,{sat_source_b64}' width='100%'>"
            f"<div class='cap'>Satellite imagery with building footprints coloured "
            f"by assigned height source.</div>"
            f"</div>\n"
        )
    if sat_roof_b64:
        sat_html += (
            f"<div class='panel'>"
            f"<div class='legend'>{legend_roof}</div>"
            f"<img src='data:image/png;base64,{sat_roof_b64}' width='100%'>"
            f"<div class='cap'>Satellite imagery with footprints coloured by "
            f"rule-based roof classification (flat / pitched / stepped / complex). "
            f"Rule logic: variance → flat; bilateral brightness gradient → pitched; "
            f"row-mean oscillation → stepped; edge density → complex. "
            f"SAM planned as a future upgrade for finer polygon accuracy.</div>"
            f"</div>\n"
        )

    # --- building table (top 80 by height) ---
    top_blds = sorted(buildings, key=lambda b: b.assigned_height_m, reverse=True)[:80]
    rows_html = ""
    for b in top_blds:
        sc = _source_class(b.assigned_source)
        rc = _ROOF_CSS.get(b.roof_type, "roof-unknown")
        osm_h = _fmt(b.osm_height_m) + " m" if b.osm_height_m else "—"
        rows_html += (
            f"<tr>"
            f"<td style='font-size:0.73rem;color:#666'>{b.osm_id[-12:]}</td>"
            f"<td>{b.assigned_height_m:.1f} m</td>"
            f"<td class='{sc}'>{b.assigned_source}</td>"
            f"<td>{osm_h}</td>"
            f"<td class='{rc}'>{b.roof_type} ({b.roof_confidence:.0%})</td>"
            f"<td>{b.area_m2:.0f}</td>"
            f"</tr>\n"
        )

    # roof distribution
    roof_counts: dict[str, int] = {}
    for b in buildings:
        roof_counts[b.roof_type] = roof_counts.get(b.roof_type, 0) + 1
    source_counts: dict[str, int] = {}
    for b in buildings:
        source_counts[b.assigned_source] = source_counts.get(b.assigned_source, 0) + 1

    def _pct(n: int) -> str:
        return f"{n / max(len(buildings), 1) * 100:.0f}%"

    _rk = "roof-unknown"
    roof_dist = " &nbsp; ".join(
        f"<span class='{_ROOF_CSS.get(k, _rk)}'>{k}: {v} ({_pct(v)})</span>"
        for k, v in sorted(roof_counts.items(), key=lambda x: -x[1])
    )
    src_dist = " &nbsp; ".join(
        f"<span class='{_source_class(k)}'>{k}: {v} ({_pct(v)})</span>"
        for k, v in sorted(source_counts.items(), key=lambda x: -x[1])
    )

    merged_panel = ""
    if merged_b64:
        merged_panel = (
            f"<div class='panel'>"
            f"<img src='data:image/png;base64,{merged_b64}' width='100%'>"
            f"<div class='cap'>Merged height raster (highest-confidence pixel wins per location).</div>"
            f"</div>"
        )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Height Diagnostic — {region_name}</title>
<style>{_CSS}</style>
</head>
<body>
<div class="wrap">
  <h1>Height Diagnostic Report — {region_name.replace("_", " ").title()}</h1>
  <div class="meta">
    bbox N{north:.4f} S{south:.4f} E{east:.4f} W{west:.4f} &nbsp;·&nbsp;
    {len(buildings)} buildings &nbsp;·&nbsp;
    {len(ok_providers)}/{len(provider_results)} providers OK &nbsp;·&nbsp;
    {elapsed_s:.0f} s &nbsp;·&nbsp; {ts}
  </div>

  <h2>Provider Status</h2>
  <div class="cards">{cards_html}</div>

  <h2>Height Source Distribution</h2>
  <div class="panel"><div class="stat">{src_dist}</div></div>

  <h2>Per-Provider Height Maps</h2>
  {panels_html}

  <h2>Merged Height Map</h2>
  {merged_panel}

  <h2>Satellite Overlays</h2>
  {sat_html}

  <h2>Roof Classification Distribution</h2>
  <div class="panel"><div class="stat">{roof_dist}</div></div>

  <h2>Building Detail (top {len(top_blds)} by height)</h2>
  <table>
    <tr>
      <th>OSM ID (suffix)</th><th>Assigned height</th><th>Source</th>
      <th>OSM height</th><th>Roof type</th><th>Area m²</th>
    </tr>
    {rows_html}
  </table>
</div>
</body>
</html>"""


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def _run(region_name: str, bbox: tuple[float, float, float, float],
         out_dir: Path) -> None:
    from city2stl.skyline.height import merge_height_rasters, provider_stats
    from city2stl.skyline.satellite_image import fetch_region_satellite
    from city2stl.skyline.region_data import _load_osm_for_region, _load_region_bbox
    from city2stl.skyline.region_types import RegionBBox

    north, south, east, west = bbox
    rbb = RegionBBox(name=region_name, north=north, south=south, east=east, west=west)

    t0 = time.time()
    print(f"[height_report] Region: {region_name}  bbox N{north:.4f} S{south:.4f} E{east:.4f} W{west:.4f}")

    # -- OSM buildings --
    print("[height_report] Fetching OSM data …")
    try:
        osm_data, osm_src = _load_osm_for_region(rbb)
        print(f"[height_report] OSM: {osm_src}")
    except Exception as exc:
        print(f"[height_report] OSM fetch failed: {exc}")
        osm_data = {}
    features = (osm_data.get("buildings") or {}).get("features") or []
    print(f"[height_report] {len(features)} OSM building features")

    # -- Satellite image --
    print("[height_report] Fetching satellite image …")
    sat_img: np.ndarray | None = None
    sat_proj: Callable | None = None
    try:
        north, south, east, west = bbox
        sat_img, sat_proj, sat_meta = fetch_region_satellite(
            (south, west, north, east), target_m_per_px=2.0)
        print(f"[height_report] satellite: {sat_img.shape} "
              f"zoom={sat_meta.get('zoom')} tiles={sat_meta.get('n_tiles')}")
    except Exception as exc:
        print(f"[height_report] satellite fetch failed: {exc}")

    # -- Height providers --
    registry = _build_registry()
    dim = (256, 256)
    provider_results: list[ProviderResult] = []
    ok_rasters: list = []

    for reg in registry:
        pr = ProviderResult(
            name=reg["name"], color=reg["color"],
            confidence=reg["confidence"], res_m=reg["res_m"],
            status="no_coverage",
        )
        if not reg["instance"].covers(bbox):
            provider_results.append(pr)
            print(f"[height_report] {reg['name']}: no coverage")
            continue
        print(f"[height_report] {reg['name']}: fetching …", end=" ", flush=True)
        try:
            hr = reg["instance"].fetch_heights(bbox, dim)
            valid = int(np.count_nonzero(~np.isnan(hr.raster)))
            if valid == 0:
                pr.status = "no_data"
                print("no data")
            else:
                pr.status = "ok"
                pr.raster = hr.raster
                pr.stats = provider_stats(hr)
                ok_rasters.append(hr)
                above1m = int(np.count_nonzero(hr.raster > 1.0))
                above1m_pct = above1m / hr.raster.size * 100
                print(f"ok  non-NaN={pr.stats.get('coverage_pct'):.0f}%  "
                      f">1m={above1m_pct:.0f}%  "
                      f"mean={pr.stats.get('mean_m'):.1f}m  "
                      f"max={pr.stats.get('max_m'):.1f}m")
        except Exception as exc:
            pr.status = "failed"
            pr.error = str(exc)
            print(f"FAILED: {exc}")
        provider_results.append(pr)

    # -- OSM height raster --
    # Stamp each building's OSM-tagged height (or levels*3m) at its centroid pixel.
    # Treated as confidence=1.0 since OSM explicit tags are surveyed ground truth.
    osm_raster = np.full(dim, np.nan, dtype=np.float32)
    osm_tagged_n = 0
    osm_levels_n = 0
    for feat in features:
        props = feat.get("properties") or {}
        rings = (feat.get("geometry") or {}).get("coordinates") or []
        if not rings:
            continue
        clat, clon = _centroid(rings)
        h, src = _osm_height(props)
        if h is not None and h > 0:
            row = max(0, min(dim[0] - 1,
                             int((north - clat) / (north - south) * dim[0])))
            col = max(0, min(dim[1] - 1,
                             int((clon - west) / (east - west) * dim[1])))
            existing = osm_raster[row, col]
            if np.isnan(existing) or h > existing:
                osm_raster[row, col] = h
            if src == "osm_tag":
                osm_tagged_n += 1
            else:
                osm_levels_n += 1

    osm_total = osm_tagged_n + osm_levels_n
    osm_valid_px = int(np.count_nonzero(~np.isnan(osm_raster)))
    osm_pr = ProviderResult(
        name="OSM Tags",
        color="#ffeb3b",
        confidence=1.0,
        res_m=0.0,
        status="ok" if osm_valid_px > 0 else "no_data",
    )
    if osm_valid_px > 0:
        osm_vals = osm_raster[~np.isnan(osm_raster)]
        osm_pr.raster = osm_raster
        osm_pr.stats = {
            "coverage_pct": osm_total / max(len(features), 1) * 100,
            "mean_m": float(np.nanmean(osm_raster)),
            "max_m": float(np.nanmax(osm_raster)),
            "p95_m": float(np.nanpercentile(osm_vals, 95)),
        }
        # Add to merge as confidence=1.0 so OSM beats all raster providers
        from city2stl.skyline.height import HeightResult as _HR  # noqa: PLC0415
        osm_conf_r = np.where(np.isnan(osm_raster), 0.0, 1.0).astype(np.float32)
        ok_rasters.insert(0, _HR(osm_raster, osm_conf_r, "osm_tags", 0.0))
        print(f"[height_report] OSM: {osm_tagged_n} height tags + "
              f"{osm_levels_n} level tags ({osm_total}/{len(features)} buildings "
              f"= {osm_total/max(len(features),1)*100:.1f}%)")
    else:
        print("[height_report] OSM: no height or level tags found")
    provider_results.insert(0, osm_pr)

    # -- Merge --
    merged_raster: np.ndarray | None = None
    if ok_rasters:
        from city2stl.skyline.height import merge_height_rasters
        merged_hr = merge_height_rasters(ok_rasters, target_shape=dim)
        merged_raster = merged_hr.raster
        valid_m = int(np.count_nonzero(~np.isnan(merged_raster)))
        print(f"[height_report] merged raster: {valid_m}/{merged_raster.size} valid pixels")

    # -- Per-building height assignment + roof classification --
    print(f"[height_report] Assigning heights to {len(features)} buildings …")
    buildings: list[BuildingEntry] = []

    # Build source priority order: first provider with data wins
    source_order = [(r.name, r.raster, r.confidence)
                    for r in provider_results if r.status == "ok" and r.raster is not None]

    for feat in features:
        props = (feat.get("properties") or {})
        rings = (feat.get("geometry") or {}).get("coordinates") or []
        if not rings:
            continue
        coords = rings[0]
        clat, clon = _centroid(rings)
        area = _polygon_area_m2(coords)
        fid = str(props.get("@id", ""))

        osm_h, osm_src = _osm_height(props)
        assigned_h = osm_h
        assigned_src = osm_src if osm_h is not None else "default"

        if assigned_h is None:
            for src_name, raster, _conf in source_order:
                v = _sample_raster(raster, clat, clon, bbox)
                if v is not None and v > 0:
                    assigned_h = v
                    assigned_src = src_name
                    break
        if assigned_h is None:
            assigned_h = 10.0
            assigned_src = "default"

        # Roof classification
        roof_type, roof_conf = "unknown", 0.0
        if sat_img is not None and sat_proj is not None:
            crop = _crop_footprint(sat_img, sat_proj, coords)
            if crop is not None and crop.size > 0:
                roof_type, roof_conf = _classify_roof(crop)

        buildings.append(BuildingEntry(
            osm_id=fid,
            centroid_lat=clat,
            centroid_lon=clon,
            area_m2=area,
            osm_height_m=osm_h,
            osm_source=osm_src,
            assigned_height_m=assigned_h,
            assigned_source=assigned_src,
            roof_type=roof_type,
            roof_confidence=roof_conf,
        ))

    print(f"[height_report] assigned heights for {len(buildings)} buildings")

    # -- Inject building-level coverage into each provider's stats --
    # "How many OSM buildings got their height from this provider?" is far more
    # meaningful than the raster pixel count, especially for coastal bboxes where
    # water pixels (nDSM = 0) inflate pixel coverage to 100% with no buildings.
    provider_bldg_counts: dict[str, int] = {}
    for b in buildings:
        key = b.assigned_source
        if key in ("osm_tag", "osm_levels"):
            key = "OSM Tags"
        provider_bldg_counts[key] = provider_bldg_counts.get(key, 0) + 1

    n_bldgs = max(len(buildings), 1)
    for pr in provider_results:
        n = provider_bldg_counts.get(pr.name, 0)
        if pr.stats is None:
            pr.stats = {}
        pr.stats["bldg_n"] = n
        pr.stats["bldg_pct"] = round(n / n_bldgs * 100, 1)
        # For raster providers also count pixels > 1 m (filters water / flat ground)
        if pr.raster is not None and pr.name != "OSM Tags":
            n_above = int(np.count_nonzero(pr.raster > 1.0))
            pr.stats["above1m_pct"] = round(n_above / pr.raster.size * 100, 1)

    # -- Render PNG panels --
    print("[height_report] Rendering images …")
    all_heights = [b.assigned_height_m for b in buildings]
    vmin_h = float(np.percentile(all_heights, 2)) if all_heights else 0.0
    vmax_h = float(np.percentile(all_heights, 98)) if all_heights else 100.0

    raster_panels: list[tuple[str, str, str, str]] = []
    for pr in provider_results:
        if pr.status != "ok" or pr.raster is None:
            continue
        s = pr.stats
        img_b64 = _raster_to_png_b64(pr.raster, vmin=0, vmax=vmax_h)
        cbar_b64 = _colorbar_png_b64(0, vmax_h)
        caption = (
            f"{pr.name} ({pr.res_m:.0f} m) — "
            f"cov {s.get('coverage_pct', 0):.0f}%  "
            f"mean {_fmt(s.get('mean_m'))} m  "
            f"max {_fmt(s.get('max_m'))} m"
        )
        raster_panels.append((pr.name, img_b64, cbar_b64, caption))

    merged_b64: str | None = None
    if merged_raster is not None:
        merged_b64 = _raster_to_png_b64(merged_raster, vmin=0, vmax=vmax_h)

    sat_source_b64: str | None = None
    sat_roof_b64: str | None = None
    if sat_img is not None and sat_proj is not None and buildings:
        sat_source_b64 = _sat_overlay_png_b64(
            sat_img, buildings, sat_proj, osm_data, mode="source")
        sat_roof_b64 = _sat_overlay_png_b64(
            sat_img, buildings, sat_proj, osm_data, mode="roof")

    # -- Write HTML --
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{region_name}_height_report.html"
    elapsed = time.time() - t0

    html = _generate_html(
        region_name=region_name,
        bbox=bbox,
        provider_results=provider_results,
        buildings=buildings,
        sat_source_b64=sat_source_b64,
        sat_roof_b64=sat_roof_b64,
        raster_panels=raster_panels,
        merged_b64=merged_b64,
        elapsed_s=elapsed,
    )
    out_path.write_text(html, encoding="utf-8")
    print(f"[height_report] Written -> {out_path}  ({elapsed:.1f}s)")


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Building height diagnostic report")
    ap.add_argument("--region", help="Region name (matches sites/<name>.json)")
    ap.add_argument("--bbox", nargs=4, type=float, metavar=("N", "S", "E", "W"),
                    help="Explicit bbox N S E W")
    ap.add_argument("--out", default="output/height_reports",
                    help="Output directory (default: output/height_reports)")
    args = ap.parse_args()

    if args.region:
        from city2stl.skyline.region_data import _load_region_bbox
        rbb = _load_region_bbox(args.region)
        bbox = (rbb.north, rbb.south, rbb.east, rbb.west)
        name = args.region
    elif args.bbox:
        bbox = tuple(args.bbox)
        name = f"bbox_{bbox[0]:.2f}_{bbox[2]:.2f}"
    else:
        ap.error("Provide --region or --bbox N S E W")

    _run(name, bbox, Path(args.out))


if __name__ == "__main__":
    main()
