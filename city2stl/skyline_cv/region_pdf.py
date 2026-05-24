"""Region-driven skyline screening and PDF report generation — the
orchestration layer of skyline_cv. Calls into pipeline.py for the
math; this module handles I/O, seed selection, multi-pass registration,
and rendering.

Entry point
-----------
``run_region_pdf_report(region_name, output_pdf, seed_urls, ...)``
is invoked by ``scripts/08_region_skyline_pdf.py``. It produces one
PDF that doubles as the primary debug artefact for inspection.

Flow per region
---------------
1. Load region bbox from the strm2stl SQLite ``regions`` table.
2. Fetch OSM buildings + waterways via Overpass (cached if available).
3. Build ``BuildingRecord`` list with tagged heights, area, and DEM
   terrain elevation (open-meteo).
4. Resolve user-provided + auto-proposed seed locations:
   - ``_parse_streetview_url`` parses the URL's pano_id / lat / lon /
     heading / pitch / FOV
   - ``_propose_standoff_locations`` adds 8 dirs × 3 standoff radii
     positions weighted toward water-adjacent placements
5. Screen each candidate with a 1-image probe + a sky/contour quality
   gate (``_screen_score_from_image``).
6. ``_seed_multiview_registration`` is the core per-seed loop:
   - Resolve pano (pano_id-bound or location-fallback)
   - Capture 12-view spin (every 30°), SegFormer mask each one
   - Joint optimize the pano-to-geographic offset across all 12 views
     (3° coarse + 0.5° fine, view-weighted by observed-building cols)
   - Per-view register with ±8° around the seed anchor
   - ``estimate_heights_from_registration`` per view
   - Stitch per-view masks for the 360° pano-level result
7. ``aggregate_building_heights`` (in pipeline.py) groups by feature_id
   with outlier-seed downweighting.
8. ``_render_pdf`` writes the multi-page report.

Key state structures
--------------------
- ``RegionBBox``         — region polygon (N/S/E/W)
- ``SkylinePoint``       — candidate camera position + heading + source tag
- ``SeedViewRegistration`` — one frozen result per spin view (image overlay,
  registration metadata, matched-segment list, vertical band crop)
- ``StitchedPanoResult`` — one frozen result per seed for the pano pipeline

Hard dependencies beyond pipeline.py: ``requests`` (Street View Static API
+ Overpass + open-meteo elevations), ``matplotlib`` (PDF rendering),
``app.server.core.cache`` / ``app.server.core.db`` (OSM cache + region
table — production strm2stl integration points).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import cv2
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import requests
from matplotlib.backends.backend_pdf import PdfPages
from shapely.geometry import shape

from app.server.core.cache import osm_cache_key, read_osm_cache
from app.server.core.db import get_db, init_db
from city2stl.fetch import fetch_osm_data

from .pipeline import (
    BuildingRecord,
    CapturedView,
    Viewpoint,
    _building_height_from_tags,
    _load_env_file_if_present,
    _polygon_area_m2,
    aggregate_building_heights,
    augment_estimates_with_depth,
    _merge_silhouette_sources,
    _neural_sky_and_building_masks,
    detect_building_silhouettes,
    detect_buildings_from_mask,
    detect_skyline_contour,
    estimate_heights_from_registration,
    match_segments_to_buildings,
    osm_anchor_silhouettes,
    register_view_to_osm,
)

# F-SKY12: enable Depth Anything V2 verifier on each view. Off by default
# while Phase A is validated. Set env var SKYLINE_CV_F_SKY12=1 to turn on.
_F_SKY12_ENABLED = os.environ.get("SKYLINE_CV_F_SKY12", "0").strip().lower() in (
    "1", "true", "yes", "on"
)

# F-SKY13: draw OSM coastline + 1 km consideration window on per-view minimap.
# OSM is the primary coastline ground truth (see feedback memory
# feedback_satellite_coastline_hsv_unreliable). Default ON; set
# SKYLINE_CV_F_SKY13=0 to disable the overlay.
_F_SKY13_ENABLED = os.environ.get("SKYLINE_CV_F_SKY13", "1").strip().lower() in (
    "1", "true", "yes", "on"
)
_F_SKY13_RADIUS_M = 1000.0
# Optional: also render the ESRI satellite image as the minimap background.
# Costs one network fetch per seed on first run (then disk-cached). Off by
# default; enable with SKYLINE_CV_F_SKY13_SAT_BG=1. Independent of the
# F-SKY11.1 satellite HSV path — purely visual, no algorithmic role.
_F_SKY13_SAT_BG_ENABLED = os.environ.get(
    "SKYLINE_CV_F_SKY13_SAT_BG", "0"
).strip().lower() in ("1", "true", "yes", "on")

STREETVIEW_METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
STREETVIEW_IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"

# Disk cache for Street View images — avoids repeat API charges on re-runs.
# Key = SHA-1 of the request params *without* the API key.
_SV_IMAGE_CACHE_DIR = Path(__file__).parent / "runs" / "image_cache"


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
    headings_per_col: "np.ndarray | None" = None


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
    image: np.ndarray
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
    # F-SKY4: the SegFormer building mask for this view. Persisted into the
    # registration so the PDF renderer can overlay it without depending on
    # the bounded in-memory neural cache (which evicts after 16 entries and
    # otherwise misses by PDF-render time on multi-seed runs).
    building_mask: "np.ndarray | None" = None
    # F-SKY13 Phase B: pano↔OSM-coastline registration score at the
    # recovered (or manually overridden) heading offset. Per-seed, not
    # per-view — populated only when pano-recovery is enabled and the
    # seed has OSM coastline within the 1 km window. None elsewhere.
    pano_osm_iou: float | None = None
    pano_osm_n_keypoints: int | None = None
    # F-SKY13 Phase B: pano-derived coastline projected back to lon/lat
    # (one point per pano column where water was detected, sampled at a
    # coarse stride). Drawn as a dashed orange polyline on the minimap so
    # the user can see where the pano "thinks" the coast is. None when
    # pano-recovery is disabled or there's no water in the pano.
    pano_projected_coastline: "list[tuple[float, float]] | None" = None
    # F-SKY15: per-view list of ``RegisteredBuildingEstimate`` records
    # (with F-SKY12 depth fields when SKYLINE_CV_F_SKY12=1). Persisted so
    # the HTML diagnostic report can render the depth diagnostics today
    # without waiting for the PDF rendering path. None elsewhere.
    view_estimates: "list | None" = None


def _resolve_api_key(explicit_key: str | None = None) -> str:
    _load_env_file_if_present()
    key = explicit_key or os.environ.get(
        "GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_API_KEY")
    if not key:
        raise RuntimeError(
            "Google Maps API key not found. Set GOOGLE_MAPS_API_KEY or pass --api-key")
    return key


def _load_region_bbox(region_name: str) -> RegionBBox:
    """Load a RegionBBox from the SQLite regions table.

    Falls back to ``sites/<region_name>.json`` so that cities defined via the
    site JSON files work without requiring a DB entry.  Matching against the DB
    is case-sensitive; the JSON fallback is case-insensitive on the filename.
    """
    init_db()
    with get_db() as conn:
        row = conn.execute(
            "SELECT name, north, south, east, west FROM regions WHERE name = ?",
            (region_name,),
        ).fetchone()
    if row is not None:
        return RegionBBox(
            name=str(row["name"]),
            north=float(row["north"]),
            south=float(row["south"]),
            east=float(row["east"]),
            west=float(row["west"]),
        )

    # Fallback: load bbox from sites/<region_name>.json if it exists.
    site_json = (
        Path(__file__).resolve().parent /
        "sites" / f"{region_name.lower()}.json"
    )
    if site_json.exists():
        try:
            data = json.loads(site_json.read_text(encoding="utf-8"))
            return RegionBBox(
                name=data.get("name", region_name),
                north=float(data["north"]),
                south=float(data["south"]),
                east=float(data["east"]),
                west=float(data["west"]),
            )
        except Exception as exc:
            raise RuntimeError(
                f"Region {region_name!r} not in DB and sites JSON is malformed: {exc}"
            ) from exc

    raise RuntimeError(
        f"Region {region_name!r} not found in regions table and no matching "
        f"sites/{region_name.lower()}.json exists."
    )


def _load_osm_for_region(bbox: RegionBBox) -> tuple[dict, str]:
    key_params = [
        (0.5, 5.0),
        (2.0, 20.0),
        (0.5, 20.0),
        (2.0, 5.0),
    ]
    for tol, min_area in key_params:
        key = osm_cache_key(bbox.north, bbox.south,
                            bbox.east, bbox.west, tol, min_area)
        cached = read_osm_cache(key)
        if cached and (cached.get("buildings", {}).get("features") or []):
            return cached, f"cache:{key[:8]} tol={tol} area={min_area}"

    fetched = fetch_osm_data(
        bbox.north,
        bbox.south,
        bbox.east,
        bbox.west,
        ["buildings", "roads", "waterways"],
        simplify_tolerance=0.5,
        min_area=5.0,
    )
    return fetched, "live_fetch"


def _parse_height(props: dict) -> float:
    raw = props.get("height_m")
    if raw is not None:
        try:
            return float(raw)
        except Exception:
            pass

    raw_h = props.get("height")
    if raw_h is not None:
        try:
            digits = "".join(ch for ch in str(
                raw_h) if ch.isdigit() or ch == ".")
            if digits:
                return float(digits)
        except Exception:
            pass

    raw_levels = props.get("building:levels") or props.get("levels")
    if raw_levels is not None:
        try:
            return max(3.0, float(str(raw_levels).split(";")[0]) * 3.4)
        except Exception:
            pass

    return 10.0


def _feature_centroid(feature: dict) -> tuple[float, float] | None:
    geom = feature.get("geometry", {})
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return None

    ring = None
    if gtype == "Polygon":
        ring = coords[0] if coords else None
    elif gtype == "MultiPolygon":
        ring = coords[0][0] if coords and coords[0] else None
    if not ring:
        return None

    lon = float(sum(p[0] for p in ring) / len(ring))
    lat = float(sum(p[1] for p in ring) / len(ring))
    return lat, lon


def _extract_high_rises(osm_data: dict, min_height_m: float = 35.0) -> list[tuple[float, float, float]]:
    highs: list[tuple[float, float, float]] = []
    features = osm_data.get("buildings", {}).get("features") or []
    for feat in features:
        c = _feature_centroid(feat)
        if c is None:
            continue
        h = _parse_height(feat.get("properties") or {})
        if h >= min_height_m:
            highs.append((c[0], c[1], h))

    if highs:
        return highs

    # Fallback: top quantile if no explicit tall buildings are tagged
    rows = []
    for feat in features:
        c = _feature_centroid(feat)
        if c is None:
            continue
        h = _parse_height(feat.get("properties") or {})
        rows.append((c[0], c[1], h))
    if not rows:
        return []
    hs = np.array([r[2] for r in rows], dtype=np.float32)
    thr = float(np.percentile(hs, 85))
    return [r for r in rows if r[2] >= thr]


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians((lat1 + lat2) * 0.5))
    dx = (lon2 - lon1) * mlon
    dy = (lat2 - lat1) * mlat
    return float(math.hypot(dx, dy))


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians((lat1 + lat2) * 0.5))
    dx = (lon2 - lon1) * mlon
    dy = (lat2 - lat1) * mlat
    b = math.degrees(math.atan2(dx, dy))
    return (b + 360.0) % 360.0


def _extract_pano_id(url: str) -> str | None:
    """Pull the pano id out of a Google Maps Street View URL.

    Google encodes the pano id in the data segment as `1s<panoid>!2e10`
    (e.g. `1sCIHM0ogKEICAgIDagoD5Mg!2e10!3e11`). The pano id is the slug
    between `!1s` and the next `!`.
    """
    if not url:
        return None
    marker = "!1s"
    idx = url.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = url.find("!", start)
    if end < 0:
        end = len(url)
    pano = url[start:end].strip()
    return pano or None


def _parse_streetview_url(url: str) -> tuple[float, float, float, float, float, str | None] | None:
    """Parse a Google Maps Street View URL.

    Returns (lat, lon, heading, fov, pitch, pano_id) where pano_id may be None
    if the URL doesn't carry one.

    Google Street View URL format after `@`:
      lat,lon,3a,<FOV>y,<HEADING>h,<TILT>t
    where TILT is 90° at the horizon (>90 looks down). We expose `pitch` in the
    Street View Static API convention (positive = up), so pitch = 90 - tilt.
    """
    text = url.strip()
    if not text:
        return None

    pano_id = _extract_pano_id(text)

    if "@" in text:
        try:
            segment = text.split("@", 1)[1].split("/", 1)[0]
            parts = segment.split(",")
            lat = float(parts[0])
            lon = float(parts[1])
            heading = 0.0
            fov = 80.0
            pitch = 0.0
            for p in parts[2:]:
                if not p:
                    continue
                tag = p[-1]
                try:
                    val = float(p[:-1])
                except ValueError:
                    continue
                if tag == "h":
                    heading = val
                elif tag == "y":
                    fov = val
                elif tag == "t":
                    pitch = 90.0 - val
            return lat, lon, heading % 360.0, fov, pitch, pano_id
        except Exception:
            pass

    try:
        parsed = urlparse(text)
        qs = parse_qs(parsed.query)
        lat = float(qs.get("lat", [""])[0])
        lon = float(qs.get("lon", [""])[0])
        heading = float(qs.get("heading", ["0"])[0]) % 360.0
        fov = float(qs.get("fov", ["80"])[0])
        pitch = float(qs.get("pitch", ["0"])[0])
        return lat, lon, heading, fov, pitch, pano_id
    except Exception:
        return None


def _feature_rings(feature: dict) -> list[list[tuple[float, float]]]:
    geom = feature.get("geometry", {})
    gtype = geom.get("type")
    coords = geom.get("coordinates")
    if not coords:
        return []
    rings: list[list[tuple[float, float]]] = []
    if gtype == "Polygon":
        if coords and coords[0]:
            rings.append([(float(p[0]), float(p[1])) for p in coords[0]])
    elif gtype == "MultiPolygon":
        for poly in coords:
            if poly and poly[0]:
                rings.append([(float(p[0]), float(p[1])) for p in poly[0]])
    return rings


def _osm_to_building_records(osm_data: dict, min_area_m2: float = 8.0) -> list[BuildingRecord]:
    """Build BuildingRecord list with deterministic IDs across runs.

    Overpass returns features in non-deterministic order, so feat['id'] cannot
    be trusted for cross-run identity. We sort by (centroid_lat, centroid_lon)
    after polygon construction so the same physical building gets the same
    sequential id no matter which order Overpass served it.
    """
    raw: list[tuple[float, float, object, dict, float, float | None, str]] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        rings = _feature_rings(feat)
        if not rings:
            continue
        coords = rings[0]
        if len(coords) < 4:
            continue
        if coords[0] != coords[-1]:
            coords = coords + [coords[0]]
        area_m2 = _polygon_area_m2(coords)
        if area_m2 < min_area_m2:
            continue
        geom_dict = feat.get("geometry") or {}
        try:
            poly = shape(geom_dict)
        except Exception:
            continue
        if poly.is_empty:
            continue
        if not poly.is_valid:
            poly = poly.buffer(0)
        if poly.is_empty:
            continue
        c = poly.centroid
        props = feat.get("properties") or {}
        h, hs = _building_height_from_tags(props)
        raw.append((float(c.y), float(c.x), poly, props, area_m2, h, hs))

    # Stable ordering — sort south→north then west→east.
    raw.sort(key=lambda r: (round(r[0], 6), round(r[1], 6)))

    out: list[BuildingRecord] = []
    for idx, (lat, lon, poly, props, area_m2, h, hs) in enumerate(raw):
        fid = f"b{idx:04d}"
        name = str(props.get("name") or props.get("ref") or fid)
        out.append(
            BuildingRecord(
                feature_id=fid,
                name=name,
                geometry=poly,
                centroid_lat=lat,
                centroid_lon=lon,
                height_tag_m=h,
                height_source=hs,
                area_m2=area_m2,
            )
        )
    return out


def _fetch_elevations(points: list[tuple[float, float]], chunk_size: int = 80) -> list[float]:
    if not points:
        return []
    out: list[float] = []
    for start in range(0, len(points), chunk_size):
        chunk = points[start: start + chunk_size]
        try:
            lats = ",".join(f"{p[0]:.6f}" for p in chunk)
            lons = ",".join(f"{p[1]:.6f}" for p in chunk)
            r = requests.get(
                "https://api.open-meteo.com/v1/elevation",
                params={"latitude": lats, "longitude": lons},
                timeout=25,
            )
            r.raise_for_status()
            data = r.json()
            vals = data.get("elevation")
            if isinstance(vals, list) and len(vals) == len(chunk):
                out.extend(float(v) for v in vals)
                continue
        except Exception:
            pass
        out.extend(0.0 for _ in chunk)
    return out


def _attach_building_terrain(buildings: list[BuildingRecord]) -> list[BuildingRecord]:
    if not buildings:
        return buildings
    pts = [(b.centroid_lat, b.centroid_lon) for b in buildings]
    elevs = _fetch_elevations(pts)
    out: list[BuildingRecord] = []
    for b, e in zip(buildings, elevs):
        out.append(
            BuildingRecord(
                feature_id=b.feature_id,
                name=b.name,
                geometry=b.geometry,
                centroid_lat=b.centroid_lat,
                centroid_lon=b.centroid_lon,
                height_tag_m=b.height_tag_m,
                height_source=b.height_source,
                area_m2=b.area_m2,
                terrain_elev_m=float(e),
            )
        )
    return out


def _read_site_config(region_name: str) -> dict:
    """Load and parse sites/<region>.json. Returns {} if missing/invalid.
    All other `_load_site_*` helpers below pull keys from this dict; the
    file itself is read once per call here. (For a hot path you'd want a
    per-region memo; the loaders are only hit once per run.)
    """
    cfg = (
        Path(__file__).resolve().parent
        / "sites" / f"{region_name.strip().lower()}.json"
    )
    if not cfg.exists():
        return {}
    try:
        return json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _load_site_seed_urls(region_name: str) -> list[str]:
    raw = _read_site_config(region_name).get("seed_urls")
    if not isinstance(raw, list):
        return []
    return [str(x).strip() for x in raw if str(x).strip()]


def _load_site_negative_seeds(region_name: str) -> set[str]:
    """Seed names listed in ``negative_seeds`` are still processed (PDF page
    + per-view diagnostics) but their per-view height estimates are NOT
    contributed to the aggregate. Curated regression-suite of known-bad
    camera positions (gas stations, parking lots, under-bridge views).
    If the pipeline ever produces useful estimates from a negative seed,
    that signals a screening / matching defect.
    """
    raw = _read_site_config(region_name).get("negative_seeds")
    return {str(x) for x in raw} if isinstance(raw, list) else set()


def _load_site_drive_pano_recovery_anchor(region_name: str) -> bool:
    """Second-stage opt-in: even with ``use_pano_coastline_recovery``
    enabled, the recovered offset only LOGS by default. Set this True
    to ALSO use a sharp recovery as the seed anchor when no manual
    ``anchor_offsets_deg`` override is present.
    """
    return bool(_read_site_config(region_name).get(
        "drive_pano_recovery_anchor", False))


def _load_site_use_pano_coastline_recovery(region_name: str) -> bool:
    """Per-region opt-in for F-SKY11.1 / Path B pano-coastline heading
    recovery. True → during ``_seed_multiview_registration`` each seed
    stitches a raw (API-heading) pano water mask, runs the F-SKY11.1
    pano-keypoint offset sweep, and LOGS the recovered offset for the
    user to compare against any manual override. Set
    ``drive_pano_recovery_anchor`` to also USE the recovery as the seed.
    """
    return bool(_read_site_config(region_name).get(
        "use_pano_coastline_recovery", False))


def _load_site_use_cross_view_scoring(region_name: str) -> bool:
    """Per-region opt-in for F-SKY10 cross-view colour reranking. True →
    the run fetches an ESRI satellite composite for the bbox and passes
    a roof-colour-consistency scorer into ``match_segments_to_buildings``.
    First run on a region downloads ~10–20 MB of ESRI WMTS tiles
    (cached under ``runs/satellite_image_cache/``).
    """
    return bool(_read_site_config(region_name).get(
        "use_cross_view_scoring", False))


def _load_site_pano_only_pdf(region_name: str) -> bool:
    """Per-region rendering flag. True → the PDF report skips the per-spin-
    view registration pages (one per heading × seed, dominant page-count
    contribution) and only renders the per-seed 360° stitched pano page
    plus the summary / aggregate / validation pages.

    Set this on regions where the per-view pages have become noise and you
    just want a compact orientation/QA artefact. Cuts PDF size dramatically
    on Cartagena-scale runs (26 per-view pages × ~1 MB each → drops to
    just the few-seed pano summary + aggregate pages).
    """
    return bool(_read_site_config(region_name).get("pano_only_pdf", False))


def _load_site_use_satellite_footprints(region_name: str) -> bool:
    """Per-region opt-in for Microsoft Buildings polygons. True →
    ``fetch_microsoft_buildings_for_bbox`` runs and the de-duped
    satellite polygons are added to the BuildingRecord list. False
    (default) → OSM-only. See F-SKY8 plan.
    """
    return bool(_read_site_config(region_name).get(
        "use_satellite_footprints", False))


def _load_site_max_plausible_height_m(region_name: str) -> float:
    """Per-region building-height ceiling. Defaults to 300 m. Cartagena's
    tallest tower is ~206 m so its site sets 200 m to tighten the
    geometric y-consistency gate; Miami's peaks are ~240 m → 250 m.
    Bounds both the contour-override glass-facade implied-height sanity
    check and the per-building geometric y gate.
    """
    raw = _read_site_config(region_name).get("max_plausible_height_m")
    try:
        v = float(raw) if raw is not None else 300.0
    except (TypeError, ValueError):
        return 300.0
    if not math.isfinite(v) or v <= 0.0:
        return 300.0
    return v


def _load_site_anchor_overrides(region_name: str) -> dict[str, float]:
    """Optional manual anchor-offset overrides keyed by seed name (e.g.
    ``"seed_4": -180.0``). When present, the joint IoU optimization is
    skipped and the override is used directly. Lets a user fix
    orientations the algorithm gets wrong.
    """
    raw = _read_site_config(region_name).get("anchor_offsets_deg")
    if not isinstance(raw, dict):
        return {}
    out: dict[str, float] = {}
    for k, v in raw.items():
        try:
            out[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return out


def _streetview_signing_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_MAPS_SIGN_SECRET", "").strip())


def _default_streetview_image_size() -> tuple[int, int]:
    """Default (width, height) for spin-view fetches.

    Without URL signing, Google's Static API caps unsigned requests at
    640×640 — a request for 1280×720 silently delivers 640×540 (height
    honoured, width clamped). We keep the historical 960×540 default in
    the unsigned path to preserve the existing on-disk image cache:
    swapping to 640×540 would invalidate ~120 cached images per region.

    With URL signing enabled the cap rises to 2048×2048 and 1280×720
    becomes a meaningful resolution bump. Signed requests get their own
    cache keys (size is part of the cache hash), so they don't collide
    with the unsigned-default cache files.
    """
    if _streetview_signing_enabled():
        return 1280, 720
    return 960, 540


def _sign_streetview_url(url: str) -> str:
    """Append a Google Maps URL signature when ``GOOGLE_MAPS_SIGN_SECRET``
    is set. Returns the URL unchanged when the env var is missing.

    Unsigned Street View Static API requests are capped by Google at
    640×640 image dimensions, regardless of what we ask for — so a 960×540
    request silently delivers 640×540. Signed requests can go up to
    2048×2048, the actual leverage for clearer Cartagena imagery.

    The signing secret is the URL-safe base64 string from Google Cloud
    Console → APIs & Services → Credentials → URL signing secret. The
    signature itself is NOT part of the local cache key (see _do_get),
    so rotating secrets does not invalidate the on-disk image cache.
    """
    secret = os.environ.get("GOOGLE_MAPS_SIGN_SECRET", "").strip()
    if not secret:
        return url
    parsed = urlparse(url)
    path_and_query = parsed.path + ("?" + parsed.query if parsed.query else "")
    try:
        key = base64.urlsafe_b64decode(secret)
    except Exception:
        # Malformed secret — emit unsigned URL rather than crashing the run.
        return url
    sig = hmac.new(key, path_and_query.encode("utf-8"), hashlib.sha1)
    encoded_sig = base64.urlsafe_b64encode(sig.digest()).decode()
    sep = "&" if parsed.query else "?"
    return f"{url}{sep}signature={encoded_sig}"


def _streetview_metadata(
    api_key: str,
    lat: float,
    lon: float,
    heading: float,
    fov: float = 80.0,
    pitch: float = 0.0,
    width: int = 640,
    height: int = 360,
    pano_id: str | None = None,
    radius_m: int = 200,
) -> dict:
    """Fetch Street View metadata.

    When ``pano_id`` is given, try that exact pano first. User-contributed
    Photo Sphere pano IDs from interactive maps URLs are not in the Static
    API's database and return ``ZERO_RESULTS``; we then fall back to
    ``location=lat,lon`` with an enlarged ``radius_m`` so the API can snap
    to the nearest official road pano.
    """
    base = {
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "size": f"{width}x{height}",
        "key": api_key,
    }
    if pano_id:
        params = {**base, "pano": pano_id}
        url = f"{STREETVIEW_METADATA_URL}?{urlencode(params)}"
        r = requests.get(_sign_streetview_url(url), timeout=30)
        r.raise_for_status()
        meta = r.json()
        if str(meta.get("status")) == "OK":
            return meta
        # fall through to location-based lookup

    params = {**base, "location": f"{lat},{lon}",
              "radius": radius_m, "source": "outdoor"}
    url = f"{STREETVIEW_METADATA_URL}?{urlencode(params)}"
    r = requests.get(_sign_streetview_url(url), timeout=30)
    r.raise_for_status()
    return r.json()


def _is_no_imagery_placeholder(img: np.ndarray) -> bool:
    """Detect Google's gray 'Sorry, we have no imagery here' placeholder.

    Two known placeholder styles are caught:
    1. Classic near-uniform bright-gray frame (mean > 210, std < 20).
    2. Current-style placeholder: mean ≈ 187, std ≈ 26 but nearly perfectly
       grayscale (per-channel mean spread < 3.0).  Real Street View imagery
       always has meaningful colour variation (spread typically 8–40+).
       A std cap of < 32 prevents misclassifying real low-contrast dawn/dusk
       shots that happen to be near-monochrome.
    """
    if img is None or img.size == 0:
        return True
    s = float(img.std())
    m = float(img.mean())
    # Style 1: original near-uniform bright gray
    if s < 20.0 and m > 210.0:
        return True
    # Style 2: current placeholder — monochromatic but lower mean
    if img.ndim == 3 and img.shape[2] >= 3:
        ch_means = [float(img[:, :, c].mean()) for c in range(3)]
        ch_spread = float(np.std(ch_means))
        if ch_spread < 3.0 and s < 32.0:
            return True
    return False


def _streetview_image(
    api_key: str,
    lat: float,
    lon: float,
    heading: float,
    fov: float = 80.0,
    pitch: float = 0.0,
    width: int | None = None,
    height: int | None = None,
    pano_id: str | None = None,
    radius_m: int = 200,
    pano_only: bool = False,
) -> np.ndarray | None:
    """Capture a Street View image. Returns None when no real imagery is
    available (including when the API returns its gray no-imagery placeholder).

    When *pano_only* is True and a pano_id is supplied, only the pano-id-based
    fetch is attempted — the location fallback is skipped.  Use this to probe
    whether a specific pano actually renders without silently obtaining a
    nearby road pano instead.
    """
    if width is None or height is None:
        dw, dh = _default_streetview_image_size()
        if width is None:
            width = dw
        if height is None:
            height = dh
    base = {
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "size": f"{width}x{height}",
        "key": api_key,
    }

    def _do_get(params: dict) -> np.ndarray | None:
        # Build a stable cache key from params without the API key. The
        # signature (added by _sign_streetview_url) is deliberately NOT
        # part of the cache key — the same logical request signed with a
        # rotated secret returns the same image bytes.
        cache_params = {k: v for k, v in params.items() if k != "key"}
        cache_key = hashlib.sha1(
            json.dumps(cache_params, sort_keys=True).encode()
        ).hexdigest()
        _SV_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _SV_IMAGE_CACHE_DIR / f"{cache_key}.png"

        if cache_path.exists():
            img = cv2.imread(str(cache_path), cv2.IMREAD_COLOR)
            if img is not None:
                return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        url = f"{STREETVIEW_IMAGE_URL}?{urlencode(params)}"
        r = requests.get(_sign_streetview_url(url), timeout=40)
        if r.status_code != 200:
            return None
        arr = np.frombuffer(r.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if _is_no_imagery_placeholder(rgb):
            return None
        # Persist to disk — BGR for cv2.imwrite.
        cv2.imwrite(str(cache_path), img)
        return rgb

    if pano_id:
        img = _do_get({**base, "pano": pano_id})
        if img is not None:
            return img
        # pano_id didn't render — stop here when caller only wants the pano.
        # IMPORTANT: callers that have applied a Photo-Sphere heading offset
        # (api_heading = geo_heading - seed.heading) MUST pass pano_only=True.
        # Falling back to a road pano at the same lat/lon and applying that
        # offset on top is the source of the cone-vs-image mismatch the user
        # observed — the labeled heading and the actual image disagree by the
        # URL's `h` value.
        if pano_only:
            return None
        # Otherwise try progressively larger location-based radii.
    for r in (radius_m, max(radius_m, 500), max(radius_m, 1500)):
        img = _do_get({**base, "location": f"{lat},{lon}",
                      "radius": r, "source": "outdoor"})
        if img is not None:
            return img
    return None


def _meta_location(meta: dict) -> tuple[float, float] | None:
    """Extract (lat, lon) of the actual pano the API returned, if available."""
    loc = meta.get("location") if isinstance(meta, dict) else None
    if isinstance(loc, dict):
        try:
            return float(loc["lat"]), float(loc["lng"])
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Geometry-driven viewpoint proposal
# ---------------------------------------------------------------------------

def _propose_standoff_locations(
    bbox: RegionBBox,
    high_rises: list[tuple[float, float, float]],
    osm_data: dict,
    n_max: int = 6,
) -> list[SkylinePoint]:
    """Generate candidate viewpoints from OSM building geometry.

    Algorithm:
    1. Compute height-weighted centroid of the tall-building cluster.
    2. Sample 8 compass directions × 3 standoff radii from that centroid.
    3. Score each by: angular spread of visible high-rises in the FOV cone
       (more spread = better), foreground building density within 200 m
       (fewer obstructions = better), water adjacency bonus × 1.4.
    4. Deduplicate candidates within 200 m of each other.
    5. Return top ``n_max`` as SkylinePoint(source="auto").

    The camera heading is always aimed at the cluster centroid so the skyline
    is centred in the frame. This replicates the geometry that made seed_1
    in Cartagena the only valid seed: open water, 600 m standoff, towers
    spread across the full FOV width.
    """
    if not high_rises:
        return []

    # 1. Height-weighted centroid of the tall-building cluster.
    lats = np.array([r[0] for r in high_rises], dtype=np.float64)
    lons = np.array([r[1] for r in high_rises], dtype=np.float64)
    heights = np.array([r[2] for r in high_rises], dtype=np.float64)
    w = heights / heights.sum()
    clat = float(np.dot(w, lats))
    clon = float(np.dot(w, lons))

    # 2. Precompute building centroid list for the foreground-density check.
    all_centroids: list[tuple[float, float]] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        c = _feature_centroid(feat)
        if c is not None:
            all_centroids.append(c)

    # 3. Water-polygon vertex list for the adjacency bonus.
    water_verts: list[tuple[float, float]] = []
    for layer_key in ("waterways", "water", "waterway"):
        for feat in osm_data.get(layer_key, {}).get("features") or []:
            for ring in _feature_rings(feat):
                water_verts.extend((vlon, vlat) for (vlon, vlat) in ring)

    def _near_water(lat: float, lon: float, radius_m: float = 350.0) -> bool:
        return any(
            _distance_m(lat, lon, vlat, vlon) < radius_m
            for (vlon, vlat) in water_verts
        )

    def _foreground_density(
        lat: float, lon: float, heading: float,
        depth_m: float = 200.0, lat_width_m: float = 60.0,
    ) -> int:
        """Count building centroids in a forward rectangle."""
        count = 0
        for (blat, blon) in all_centroids:
            d = _distance_m(lat, lon, blat, blon)
            if d > depth_m:
                continue
            bear = _bearing_deg(lat, lon, blat, blon)
            delta = abs((bear - heading + 540.0) % 360.0 - 180.0)
            if d * math.sin(math.radians(delta)) < lat_width_m:
                count += 1
        return count

    def _angular_spread(
        lat: float, lon: float, heading: float, fov: float = 80.0,
    ) -> float:
        """Angular spread (degrees) of high-rises visible within ±fov/2."""
        bearings: list[float] = []
        for (hlat, hlon, _) in high_rises:
            d = _distance_m(lat, lon, hlat, hlon)
            if d < 50.0:
                continue
            bear = _bearing_deg(lat, lon, hlat, hlon)
            delta = abs((bear - heading + 540.0) % 360.0 - 180.0)
            if delta <= fov * 0.5:
                bearings.append(bear)
        if len(bearings) < 2:
            return 0.0
        bs = sorted(bearings)
        return float(bs[-1] - bs[0])

    # 4. Sample 8 directions × 3 standoff radii from the cluster centroid.
    # The previous range started at 400 m which put proposed positions INSIDE
    # the dense Bocagrande building cluster — auto-seeds at that distance
    # showed buildings only as the camera shooting between adjacent towers,
    # heavily occluded. Skylines need standoff: a tower's full silhouette
    # becomes visible at roughly 5–10 × its own height (a 200 m tower needs
    # ≥1 km standoff to clear the foreground).
    standoffs_m = [900.0, 1400.0, 2000.0]
    dirs_deg = list(np.arange(0.0, 360.0, 45.0))
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(clat))

    # Extend bbox tolerance by ~600 m on each side so coastal/causeway
    # positions outside the strict city boundary are still proposed.
    lat_margin = 600.0 / m_per_deg_lat
    lon_margin = 600.0 / m_per_deg_lon

    candidates: list[tuple[float, SkylinePoint]] = []
    for dist_m in standoffs_m:
        for dir_deg in dirs_deg:
            dlat = (dist_m * math.cos(math.radians(dir_deg))) / m_per_deg_lat
            dlon = (dist_m * math.sin(math.radians(dir_deg))) / m_per_deg_lon
            clat_c = clat + dlat
            clon_c = clon + dlon

            if not (
                (bbox.south - lat_margin) <= clat_c <= (bbox.north + lat_margin)
                and (bbox.west - lon_margin) <= clon_c <= (bbox.east + lon_margin)
            ):
                continue

            # Camera faces the centroid so the cluster fills the frame.
            heading = _bearing_deg(clat_c, clon_c, clat, clon)
            spread = _angular_spread(clat_c, clon_c, heading)
            fg_count = _foreground_density(clat_c, clon_c, heading)
            # Hard reject: more than 12 building centroids inside the 200 m
            # foreground rectangle means the camera is INSIDE the urban
            # cluster — guaranteed occluded shot. The soft penalty (-2 pts
            # per obstruction) wasn't enough to consistently drop these
            # below water-adjacent standoff positions in scoring.
            if fg_count > 12:
                continue
            # Water adjacency is strongly correlated with clear skyline:
            # Bocagrande/Old Town views from across the bay are the
            # canonical "good" pose. Boost from ×1.4 to ×2.0 so water-side
            # candidates outrank inland ones even when their angular spread
            # is similar.
            water_bonus = 2.0 if _near_water(clat_c, clon_c) else 1.0

            base = spread - fg_count * 2.0
            score = float(max(0.0, base) * water_bonus)

            name = f"auto_{int(dir_deg):03d}_{int(dist_m):04d}m"
            candidates.append(
                (score, SkylinePoint(
                    name=name,
                    lat=clat_c,
                    lon=clon_c,
                    heading=heading,
                    source="auto",
                    score=score,
                    fov=80.0,
                    pitch=0.0,
                ))
            )

    # 5. Sort by score, then deduplicate within 200 m proximity.
    candidates.sort(key=lambda t: -t[0])
    kept: list[SkylinePoint] = []
    for _score, pt in candidates:
        if not any(
            _distance_m(pt.lat, pt.lon, k.lat, k.lon) < 200.0
            for k in kept
        ):
            kept.append(pt)
        if len(kept) >= n_max:
            break
    return kept


def _screen_score_from_image(image: np.ndarray, pitch: float = 0.0) -> tuple[float, str, bool, bool]:
    contour, sky_mask = detect_skyline_contour(image)
    h, w = image.shape[:2]

    # Aerial detection: URL pitch below -8° indicates an elevated/drone camera.
    is_aerial = pitch < -8.0

    # Gate A: at least 35% of the top 20% of the frame must be sky.
    # Catches walls, close rooftops, and tree canopy pressed to the top.
    top_h = max(1, int(h * 0.20))
    sky_frac_top = float(sky_mask[:top_h, :].sum()) / max(top_h * w, 1)

    # Gate B: contour peak-to-trough must span >=5% of frame height.
    # Catches flat horizons (ocean, flat roofscapes).
    # Threshold was 10% when using the noisy HSV sky mask; the neural
    # SegFormer contour is much smoother so real building skylines that
    # previously produced inflated range due to mask noise now produce
    # accurate (smaller) range values.  Truly flat views (open water) sit
    # at < 2% range; building silhouettes start at ~5%.
    contour_valid = contour[np.isfinite(contour)]
    contour_range_frac = (
        float(np.nanmax(contour_valid) - np.nanmin(contour_valid)) / h
        if contour_valid.size > 0
        else 0.0
    )

    # Loosened thresholds — the previous gates rejected legitimate
    # building views where the camera was up-close to a tall facade.
    # Gate A: top 20 % needs at least 20 % sky (was 35 %).
    # Gate B: contour must span >= 3 % of frame (was 5 %).
    if sky_frac_top < 0.20 or contour_range_frac < 0.03:
        return 0.0, "rejected", is_aerial, False

    # Gate C / D / E — distant-skyline checks against the SegFormer masks.
    _, building_mask_bool = _neural_sky_and_building_masks(image)
    if building_mask_bool is not None:
        building_frac = float(building_mask_bool.sum()) / max(h * w, 1)
        sky_frac_total = float(sky_mask.sum()) / max(h * w, 1)
        # Gate C: full close-up facade — building fills 80%+ of frame
        # (was 60%, which rejected legitimate close-range tower views).
        if building_frac > 0.80:
            return 0.0, "rejected", is_aerial, False
        # Gate E: enclosed space / underpass — almost no sky visible.
        if sky_frac_total < 0.08:  # was 0.12
            return 0.0, "rejected", is_aerial, False
    # Gate D: skyline must reach the upper 60 % of the frame.
    # (was upper 50 %; raised cap lets close-up tower views pass.)
    if contour_valid.size > 0 and float(np.nanmedian(contour_valid)) / h > 0.60:
        return 0.0, "rejected", is_aerial, False

    # Top-clipping detection: building tops are being cut off and the
    # camera should tilt upward. Two complementary triggers:
    #   (i) classic: the highest point of the skyline contour is within
    #       the top 8 % of the frame.
    #   (ii) sustained-high: ≥ 25 % of the contour columns are within the
    #       top 12 % of the frame. Catches the case where many tower tops
    #       are *near* but not exactly at the frame top (Cartagena seed_5
    #       Bocagrande row at 75° FOV / 90° tilt — every tower sits ~5-10 %
    #       below y=0 yet the original 8 %/single-point trigger never
    #       fires, so the spin never gets the +12° correction it needs).
    top_clipped = False
    if contour_valid.size > 0:
        min_y = float(np.nanmin(contour_valid))
        if min_y / h < 0.08:
            top_clipped = True
        else:
            high_cols = float(np.sum(contour_valid / h < 0.12)) / contour_valid.size
            if high_cols >= 0.25:
                top_clipped = True

    var = float(np.std(contour))
    span = float(np.nanmax(contour) - np.nanmin(contour))
    # Heuristic score: non-flat skyline gets higher confidence.
    # The divisor 80 and the thresholds below were calibrated when HSV sky
    # detection was in use — HSV false-positives inflated contour std by
    # roughly 2× relative to the precise SegFormer neural contour.
    # Thresholds are recalibrated downward to match neural contour quality:
    #   "good"   was 0.45 → 0.30  (~same building density, smoother contour)
    #   "medium" was 0.25 → 0.15
    score = float(np.clip((0.6 * var + 0.4 * span) / 80.0, 0.0, 1.0))
    label = "good" if score >= 0.30 else (
        "medium" if score >= 0.15 else "weak")
    return score, label, is_aerial, top_clipped


def _screen_locations(
    points: list[SkylinePoint],
    api_key: str,
    max_snap_m: float = 200.0,
) -> list[dict]:
    """Screen each candidate location.

    For seeds with a pano_id, the API returns the exact pano (no snapping).
    For autos (and seeds without pano_id), the API snaps to the nearest pano;
    we read the snapped lat/lon from the metadata response and reject points
    where the snap is farther than ``max_snap_m`` from the requested location
    — those produce images that don't match the OSM cone we'd draw.

    The returned point in each row is rebound to the snapped location so the
    mini-map and any downstream projection see the actual camera position.
    """
    if points:
        elev_lookup = _fetch_elevations([(p.lat, p.lon) for p in points])
    else:
        elev_lookup = []

    screened: list[dict] = []
    for idx, point in enumerate(points):
        try:
            meta = _streetview_metadata(
                api_key,
                point.lat,
                point.lon,
                point.heading,
                fov=point.fov,
                pitch=point.pitch,
                pano_id=point.pano_id,
            )
            status = str(meta.get("status", "UNKNOWN"))
        except Exception as exc:
            screened.append({
                "point": point,
                "status": "ERROR",
                "screen_score": 0.0,
                "coverage": "weak",
                "image": None,
                "error": str(exc),
            })
            continue

        if status != "OK":
            screened.append({
                "point": point,
                "status": status,
                "screen_score": 0.0,
                "coverage": "weak",
                "image": None,
                "error": None,
            })
            continue

        # Rebind to the snapped location so the mini-map and bearing diagnostics
        # match where the camera actually is.
        snap = _meta_location(meta)
        snap_dist_m = (
            _distance_m(point.lat, point.lon,
                        snap[0], snap[1]) if snap else 0.0
        )
        effective_point = point
        if snap is not None:
            effective_point = SkylinePoint(
                name=point.name,
                lat=snap[0],
                lon=snap[1],
                heading=point.heading,
                source=point.source,
                score=point.score,
                fov=point.fov,
                pitch=point.pitch,
                pano_id=point.pano_id or str(
                    meta.get("pano_id") or "") or None,
            )

        # Drop autos that snapped too far — the captured image won't match the
        # heading/cone we picked, which is the bug behind unusable auto images.
        if effective_point.source == "auto" and snap_dist_m > max_snap_m:
            screened.append({
                "point": point,
                "effective_point": effective_point,
                "status": "SNAPPED_TOO_FAR",
                "snap_distance_m": snap_dist_m,
                "screen_score": 0.0,
                "coverage": "rejected",
                "image": None,
                "error": None,
            })
            continue

        image = _streetview_image(
            api_key,
            effective_point.lat,
            effective_point.lon,
            effective_point.heading,
            fov=effective_point.fov,
            pitch=effective_point.pitch,
            pano_id=effective_point.pano_id,
        )
        if image is None:
            screened.append({
                "point": effective_point,
                "status": "IMAGE_FAIL",
                "screen_score": 0.0,
                "coverage": "weak",
                "image": None,
                "error": None,
            })
            continue

        score, label, is_aerial, top_clipped = _screen_score_from_image(
            image, pitch=effective_point.pitch)

        # Pitch correction: if building tops are clipped and we're not already
        # looking steeply upward, re-request the same pano with +12° pitch.
        # This catches Photo Spheres captured with a slight downward tilt where
        # the tallest towers extend above the frame.
        if top_clipped and effective_point.pitch < 8.0 and not is_aerial:
            corrected_pitch = min(effective_point.pitch + 12.0, 15.0)
            corrected_image = _streetview_image(
                api_key,
                effective_point.lat,
                effective_point.lon,
                effective_point.heading,
                fov=effective_point.fov,
                pitch=corrected_pitch,
                pano_id=effective_point.pano_id,
                pano_only=effective_point.pano_id is not None,
            )
            if corrected_image is not None:
                c_score, c_label, c_aerial, _ = _screen_score_from_image(
                    corrected_image, pitch=corrected_pitch)
                if c_label != "rejected":
                    image = corrected_image
                    score, label, is_aerial = c_score, c_label, c_aerial
                    effective_point = SkylinePoint(
                        name=effective_point.name,
                        lat=effective_point.lat,
                        lon=effective_point.lon,
                        heading=effective_point.heading,
                        source=effective_point.source,
                        score=effective_point.score,
                        fov=effective_point.fov,
                        pitch=corrected_pitch,
                        pano_id=effective_point.pano_id,
                    )

        # Heading-rotation retry: the URL-provided heading often points the
        # camera at trees / walls / open water rather than the actual skyline.
        # When the initial heading is rejected, try ±90° and 180° offsets to
        # find an orientation that captures buildings. This is purely a
        # screening-recovery — the full 360° spin pass later doesn't depend
        # on this and runs every view regardless.
        if label == "rejected" and point.source == "seed" and not is_aerial:
            for offset_deg in (-90.0, 90.0, 180.0):
                retry_heading = (effective_point.heading + offset_deg) % 360.0
                retry_image = _streetview_image(
                    api_key,
                    effective_point.lat,
                    effective_point.lon,
                    retry_heading,
                    fov=effective_point.fov,
                    pitch=effective_point.pitch,
                    pano_id=effective_point.pano_id,
                    pano_only=effective_point.pano_id is not None,
                )
                if retry_image is None:
                    continue
                r_score, r_label, r_aerial, _ = _screen_score_from_image(
                    retry_image, pitch=effective_point.pitch)
                if r_label != "rejected":
                    image = retry_image
                    score, label, is_aerial = r_score, r_label, r_aerial
                    effective_point = SkylinePoint(
                        name=effective_point.name,
                        lat=effective_point.lat,
                        lon=effective_point.lon,
                        heading=retry_heading,
                        source=effective_point.source,
                        score=effective_point.score,
                        fov=effective_point.fov,
                        pitch=effective_point.pitch,
                        pano_id=effective_point.pano_id,
                    )
                    break

        elev_m = float(elev_lookup[idx]) if idx < len(elev_lookup) else 0.0
        screened.append(
            {
                "point": effective_point,
                "requested_point": point,
                "snap_distance_m": snap_dist_m,
                "status": "OK",
                "screen_score": score,
                "coverage": label,
                "is_aerial": is_aerial,
                "image": image,
                "elevation_m": elev_m,
                "error": None,
            }
        )

    screened.sort(key=lambda row: row["screen_score"], reverse=True)
    return screened


def _draw_location_map(ax, bbox: RegionBBox, screened: list[dict], osm_data: dict) -> None:
    ax.set_title("Skyline Request Location Screen")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.plot([bbox.west, bbox.east, bbox.east, bbox.west, bbox.west],
            [bbox.south, bbox.south, bbox.north, bbox.north, bbox.south],
            "k-", linewidth=1.2, label="Region")

    # Draw OSM building footprints for context — PatchCollection for speed.
    from matplotlib.collections import PatchCollection  # noqa: PLC0415
    _ctx_polys: list[mpatches.Polygon] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        for ring in _feature_rings(feat):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            _ctx_polys.append(mpatches.Polygon(
                np.column_stack([xs, ys]), closed=True))
    if _ctx_polys:
        ax.add_collection(PatchCollection(
            _ctx_polys,
            facecolor=(0.75, 0.75, 0.75, 0.18),
            edgecolor=(0.45, 0.45, 0.45, 0.25),
            linewidths=0.3,
            match_original=False,
        ))

    for row in screened:
        p: SkylinePoint = row["point"]
        color = "tab:green" if row["coverage"] == "good" else (
            "tab:orange" if row["coverage"] == "medium" else "tab:red")
        marker = "*" if p.source == "seed" else "o"
        size = 140 if p.source == "seed" else 60
        ax.scatter([p.lon], [p.lat], c=color, s=size, marker=marker, alpha=0.9)
        ax.text(p.lon, p.lat, f" {p.name}", fontsize=8)

        # Draw heading vector to show camera angle used for screening
        span = max(bbox.east - bbox.west, bbox.north - bbox.south)
        vec = span * 0.04
        theta = math.radians(p.heading)
        dlon = vec * math.sin(theta)
        dlat = vec * math.cos(theta)
        ax.arrow(
            p.lon,
            p.lat,
            dlon,
            dlat,
            width=span * 0.0008,
            head_width=span * 0.006,
            head_length=span * 0.008,
            length_includes_head=True,
            color=color,
            alpha=0.8,
        )
    ax.grid(alpha=0.25)


_SEGMENT_PALETTE = (
    (255, 80, 80),
    (80, 200, 255),
    (180, 255, 80),
    (255, 180, 60),
    (200, 120, 255),
    (255, 240, 100),
    (120, 255, 200),
    (255, 120, 200),
)


def _registration_overlay(
    image: np.ndarray,
    registration: dict,
    matched_segments: list[dict] | None = None,
) -> np.ndarray:
    out = image.copy()
    contour = np.asarray(registration.get("contour"), dtype=np.float32)
    if contour.size:
        for x in range(1, contour.size):
            y1 = int(contour[x - 1])
            y2 = int(contour[x])
            cv2.line(out, (x - 1, y1), (x, y2), (0, 220, 255), 2)

    if matched_segments:
        for i, seg in enumerate(matched_segments):
            color = _SEGMENT_PALETTE[i % len(_SEGMENT_PALETTE)]
            xL, xR = int(seg["x_left"]), int(seg["x_right"])
            top_y, base_y = int(seg["top_y"]), int(seg["base_y"])
            cv2.rectangle(out, (xL, top_y), (xR, base_y), color, 2)
            # Index badge in the upper-left corner of the box — mirrors the
            # numbered circle drawn on the mini-map footprint so the user can
            # pair image-box N with map-polygon N visually.
            badge_x = xL + 2
            badge_y = max(18, top_y + 2)
            cv2.circle(out, (badge_x + 9, badge_y + 7), 11, color, -1)
            cv2.circle(out, (badge_x + 9, badge_y + 7),
                       11, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.putText(
                out, str(i + 1), (badge_x + (3 if i +
                                             1 < 10 else 0), badge_y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )
            label = seg.get("matched_projection")
            if label:
                txt = str(label.get("name") or label.get(
                    "feature_id") or "")[:14]
                cv2.putText(
                    out,
                    txt,
                    (xL + 25, max(18, top_y - 4)),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.45,
                    color,
                    1,
                    cv2.LINE_AA,
                )

    for proj in registration.get("projections", []):
        x = int(round(float(proj.get("x_px", 0))))
        if 0 <= x < out.shape[1] and contour.size:
            y = int(round(float(contour[x])))
            cv2.circle(out, (x, y), 3, (255, 80, 80), -1)
    return out


def _seed_multiview_registration(
    seeds: list[SkylinePoint],
    buildings: list[BuildingRecord],
    api_key: str,
    spin_step_deg: float = 30.0,
    anchor_overrides: dict[str, float] | None = None,
    negative_seeds: set[str] | None = None,
    trace=None,
    max_plausible_height_m: float = 300.0,
    cross_view_state: dict | None = None,
    pano_recovery_state: dict | None = None,
) -> tuple[list[SeedViewRegistration], list[dict], list["StitchedPanoResult"]]:
    """Capture a full 360° spin (every spin_step_deg) at each seed location.

    Each successful registration contributes height estimates to the aggregate.
    The original seed.heading from the URL is ignored on purpose — we want
    coverage of every direction from a seed, not a narrow fan.

    For seeds with a pano_id (parsed from the URL) we hit that exact pano so
    the captured image actually corresponds to where the user pointed.
    Without pano_id, we resolve the snapped pano via metadata once per seed
    and rebind lat/lon/pano_id so projections, mini-map, and the image all
    agree on a single camera position.
    """
    # Resolve each seed to a concrete pano whose IMAGE actually renders (not
    # just metadata-OK). User-contributed Photo Sphere panos resolve in
    # metadata but the Static IMAGE API returns its gray "no imagery"
    # placeholder for them — we have to verify by actually fetching one image.
    # We probe a 0° image with progressively larger radii until we find a
    # pano that returns real pixels, then bind the seed to that pano's lat/lon.
    # Per-region cache of resolved seed positions to keep results
    # reproducible across runs. The Static API's location-based snap
    # routinely returns DIFFERENT panos on different runs (50–500 m drift)
    # because the metadata endpoint walks the visible-pano list slightly
    # differently each time it's called. Without caching, the user sees
    # the report change every run even when the seed_urls input is fixed.
    _resolve_cache_path = (
        Path(__file__).parent / "runs" / "seed_resolution_cache.json"
    )
    _resolve_cache: dict = {}
    if _resolve_cache_path.exists():
        try:
            _resolve_cache = json.loads(
                _resolve_cache_path.read_text(encoding="utf-8"))
        except Exception:
            _resolve_cache = {}

    def _cache_save() -> None:
        try:
            _resolve_cache_path.parent.mkdir(parents=True, exist_ok=True)
            _resolve_cache_path.write_text(
                json.dumps(_resolve_cache, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _seed_cache_key(s: SkylinePoint) -> str:
        # Key by the URL-derived identity: name + lat/lon (to 6 dp) +
        # pano_id. Changes only when the user edits sites/<region>.json.
        return f"{s.name}|{s.lat:.6f}|{s.lon:.6f}|{s.pano_id or ''}"

    resolved: list[tuple[SkylinePoint, float, bool]] = []
    seed_elevs = _fetch_elevations([(s.lat, s.lon) for s in seeds])
    for seed, seed_elev in zip(seeds, seed_elevs):
        bound: SkylinePoint | None = None
        cache_key = _seed_cache_key(seed)

        # 1. Try the cached resolved pano first.
        cached = _resolve_cache.get(cache_key)
        if cached is not None:
            try:
                cached_pano = cached.get("pano_id") or None
                cached_lat = float(cached["lat"])
                cached_lon = float(cached["lon"])
                probe = _streetview_image(
                    api_key, cached_lat, cached_lon, 0.0,
                    fov=seed.fov, pitch=seed.pitch,
                    pano_id=cached_pano, radius_m=50,
                    pano_only=cached_pano is not None,
                )
                if probe is not None:
                    bound = SkylinePoint(
                        name=seed.name, lat=cached_lat, lon=cached_lon,
                        heading=seed.heading, source=seed.source,
                        score=seed.score, fov=seed.fov,
                        pitch=seed.pitch, pano_id=cached_pano,
                    )
            except Exception:
                pass  # cache miss → fall through to fresh resolve

        # 2. If no cache hit, try the URL's pano_id directly.
        if bound is None and seed.pano_id:
            probe = _streetview_image(
                api_key, seed.lat, seed.lon, 0.0,
                fov=seed.fov, pitch=seed.pitch,
                pano_id=seed.pano_id, radius_m=200, pano_only=True,
            )
            if probe is not None:
                bound = seed
        # 3. Fall back to progressively larger location-based radii.
        if bound is None:
            for r in (200, 500, 1500, 3000):
                try:
                    meta = _streetview_metadata(
                        api_key, seed.lat, seed.lon, 0.0,
                        fov=seed.fov, pitch=seed.pitch,
                        pano_id=None, radius_m=r,
                    )
                except Exception:
                    continue
                if str(meta.get("status")) != "OK":
                    continue
                snap = _meta_location(meta)
                snap_pano = str(meta.get("pano_id") or "") or None
                if snap is None:
                    continue
                probe = _streetview_image(
                    api_key, snap[0], snap[1], 0.0,
                    fov=seed.fov, pitch=seed.pitch,
                    pano_id=snap_pano, radius_m=50,
                )
                if probe is None:
                    continue
                bound = SkylinePoint(
                    name=seed.name, lat=snap[0], lon=snap[1],
                    heading=seed.heading, source=seed.source,
                    score=seed.score, fov=seed.fov,
                    pitch=seed.pitch, pano_id=snap_pano,
                )
                break

        # Persist this seed's resolved position so subsequent runs hit the
        # same pano. Only cache successful resolutions.
        if bound is not None and cache_key not in _resolve_cache:
            _resolve_cache[cache_key] = {
                "lat": float(bound.lat),
                "lon": float(bound.lon),
                "pano_id": bound.pano_id,
            }
            _cache_save()
        if bound is None:
            # No usable imagery anywhere near this seed — skip it entirely.
            continue
        # is_photosphere: True when the bound is the URL's Photo Sphere pano.
        # Photo Sphere panos treat heading=0 as the native capture direction
        # (not geographic north), so the API heading must be pano-relative.
        # For road panos found by location-based fallback the heading IS
        # geographic and no offset is needed.
        is_photosphere = (
            seed.pano_id is not None and bound.pano_id == seed.pano_id)
        resolved.append((bound, float(seed_elev), is_photosphere))

    spin_headings = tuple(float(h)
                          for h in np.arange(0.0, 360.0, spin_step_deg))
    view_rows: list[SeedViewRegistration] = []
    pano_results: list[StitchedPanoResult] = []
    estimates = []

    # Spatial prefilter: only buildings within max_view_radius_m of the seed
    # can be in any of its 12 spin views (max_distance_m=4000 m inside the
    # projection plus a safety margin → 4500 m). Cuts ~3000 buildings to ~400
    # per seed for projection and IoU work, multiplies the speedup from
    # vectorisation by another ~7×.
    def _buildings_near_seed(seed_lat: float, seed_lon: float, radius_m: float = 4500.0):
        mlat = 110_540.0
        mlon = 111_320.0 * math.cos(math.radians(seed_lat))
        rad_sq = radius_m * radius_m
        out: list[BuildingRecord] = []
        for b in buildings:
            dx = (b.centroid_lon - seed_lon) * mlon
            dy = (b.centroid_lat - seed_lat) * mlat
            if dx * dx + dy * dy <= rad_sq:
                out.append(b)
        return out

    for seed, seed_elev, is_photosphere in resolved:
        # Per-seed building subset (recomputed each seed; cheap O(N) sweep).
        seed_buildings = _buildings_near_seed(seed.lat, seed.lon)
        # ── Pass 1: capture all spin images and screen them. We do NOT run
        # per-view registration here — the joint anchor optimization across
        # all views (next block) is authoritative. The per-view wide search
        # is run lazily in Pass 2 only as a fallback when the forced anchor
        # leaves a specific view with too few matches.
        #
        # This skip alone saves ~120 s of work per run: previously every
        # spin view ran register_view_to_osm with a coarse 180°/10° sweep
        # plus a fine ±15°/1° refinement, and the result was discarded by
        # the joint optimizer that ran right after.
        # Pass 1a: fetch every spin view at seed.pitch and screen it. We
        # defer building the CapturedViews until we know what *seed-level*
        # pitch the whole spin will commit to — splitting some views to the
        # original pitch and others to a corrected pitch produces visible
        # vertical seams in the stitched 360° pano because adjacent strips
        # then have different horizon y. The pano stitcher hstacks central
        # 30° crops assuming a uniform vertical center.
        prefetch: list[dict] = []
        any_needs_pitch_correction = False
        for geo_heading in spin_headings:
            image = _streetview_image(
                api_key,
                seed.lat,
                seed.lon,
                geo_heading,
                fov=seed.fov,
                pitch=seed.pitch,
                pano_id=seed.pano_id,
                pano_only=is_photosphere,
            )
            if image is None:
                prefetch.append({"geo_heading": geo_heading, "image": None})
                continue
            sv_score, sv_label, _, _tc = _screen_score_from_image(
                image, pitch=seed.pitch)
            if sv_label == "rejected":
                # Keep the image — it's still useful for pano-coastline
                # recovery, which needs ALL 12 directions to register
                # against the satellite (the open-water directions that
                # the screening rejects are exactly the ones the
                # coastline alignment depends on). cached_views filters
                # rejected entries below; only the pano-recovery block
                # reads through the screening.
                prefetch.append({
                    "geo_heading": geo_heading,
                    "image": image,
                    "sv_score": 0.0,
                    "sv_label": "rejected",
                })
                continue
            # Flag the SEED for pitch correction if ANY view's tops are
            # clipped or the seed itself is tilted noticeably down. The
            # decision is made once per seed, not per view.
            if seed.pitch < 8.0 and (seed.pitch < -8.0 or _tc):
                any_needs_pitch_correction = True
            prefetch.append({
                "geo_heading": geo_heading,
                "image": image,
                "sv_score": sv_score,
                "sv_label": sv_label,
            })

        # Pass 1b: if any view in the spin asked for pitch correction,
        # re-fetch ALL views at the same corrected pitch and use those
        # uniformly. This keeps the stitched pano horizon continuous and
        # lets height extraction benefit from the corrected pitch for
        # views that wouldn't have triggered the per-view rule on their
        # own. If a corrected fetch fails for some heading, that single
        # view is dropped (rather than mixed in at the wrong pitch).
        #
        # Fallback: if the corrected pitch retains noticeably fewer views
        # than the original pitch (more than half lost), the corrected
        # angle isn't physically renderable at most headings for this
        # pano. Accept the seam and keep the original-pitch versions —
        # more buildings extracted from a seamed pano beats few buildings
        # from a clean pano.
        effective_pitch = seed.pitch
        n_original = sum(1 for e in prefetch if e["image"] is not None)
        if any_needs_pitch_correction and n_original > 0:
            corrected_pitch = min(seed.pitch + 12.0, 15.0)
            corrected_prefetch: list[dict] = []
            n_kept = 0
            for entry in prefetch:
                gh = entry["geo_heading"]
                if entry["image"] is None:
                    corrected_prefetch.append(
                        {"geo_heading": gh, "image": None})
                    continue
                c_image = _streetview_image(
                    api_key, seed.lat, seed.lon, gh,
                    fov=seed.fov, pitch=corrected_pitch,
                    pano_id=seed.pano_id, pano_only=is_photosphere,
                )
                if c_image is None:
                    corrected_prefetch.append(
                        {"geo_heading": gh, "image": None})
                    continue
                c_score, c_label, _, _ = _screen_score_from_image(
                    c_image, pitch=corrected_pitch)
                if c_label == "rejected":
                    # Same logic as Pass 1a: retain the image for
                    # pano-coastline recovery even when the screening
                    # rejects the view as a per-view registration source.
                    corrected_prefetch.append({
                        "geo_heading": gh,
                        "image": c_image,
                        "sv_score": 0.0,
                        "sv_label": "rejected",
                    })
                    continue
                corrected_prefetch.append({
                    "geo_heading": gh,
                    "image": c_image,
                    "sv_score": c_score,
                    "sv_label": c_label,
                })
                n_kept += 1
            # Retain corrected only if it doesn't cost us more than half
            # the views the original pitch already had.
            if n_kept >= max(1, (n_original + 1) // 2):
                prefetch = corrected_prefetch
                effective_pitch = corrected_pitch
                print(
                    f"[pano pitch] {seed.name} corrected to {effective_pitch:+.1f}° "
                    f"({n_kept}/{n_original} views retained)")
            else:
                print(
                    f"[pano pitch] {seed.name} keeping original {seed.pitch:+.1f}° "
                    f"(corrected pitch retained only {n_kept}/{n_original})")

        cached_views: list[dict] = []
        for entry in prefetch:
            image = entry["image"]
            if image is None:
                continue
            # Rejected entries are kept in prefetch for pano-coastline
            # recovery only; per-view registration must still skip them
            # so today's matcher behavior is unchanged.
            if entry.get("sv_label") == "rejected":
                continue
            geo_heading = entry["geo_heading"]
            vp = Viewpoint(
                name=f"{seed.name}_{int(round(geo_heading))%360:03d}",
                query=seed.name,
                lat=seed.lat,
                lon=seed.lon,
                heading=geo_heading,
                pitch=effective_pitch,
                fov=seed.fov,
                image_width=image.shape[1],
                image_height=image.shape[0],
            )
            cap = CapturedView(
                viewpoint=vp, image_path=Path("."),
                metadata_path=Path("."), image=image,
            )
            cached_views.append({
                "geo_heading": geo_heading,
                "image": image,
                "cap": cap,
                "sv_score": entry["sv_score"],
                "sv_label": entry["sv_label"],
                "wide_reg": None,        # lazily computed in Pass 2 fallback
                "wide_score": float("inf"),
            })

        if not cached_views:
            continue

        # ── Anchor selection: JOINT optimization across all views.
        # A Photo Sphere is a single rigid pano with one intrinsic rotation
        # vs. geographic compass. The correct offset is the one that aligns
        # ALL 12 views with their respective masks simultaneously. Picking
        # by single best view's mIoU is unreliable when several views have
        # ambiguous local optima.
        # We sweep a coarse grid of candidate offsets and pick the one that
        # maximizes the SUM of mIoU across all views. This is the standard
        # SOTA approach (bundle-adjustment-lite for pose estimation).
        from .pipeline import _score_offset_semantic_iou, _neural_sky_and_building_masks, _neural_water_mask
        # Pre-compute mask references per view (cached by SegFormer).
        masks_per_view: list[tuple] = []
        for cv in cached_views:
            _, bmask = _neural_sky_and_building_masks(cv["image"])
            wmask = _neural_water_mask(cv["image"])
            masks_per_view.append((bmask, wmask, cv["cap"].viewpoint))

        # Two-stage coarse-to-fine joint search. Stage 1 sweeps 360° at 3°
        # steps (denser than the previous 5° step — at FOV=75° the right
        # anchor can sit between 5° gridpoints and miss a sharp local optimum)
        # to find the right ballpark; stage 2 refines ±5° at 0.5° steps around
        # the ballpark.
        #
        # Per-view weighting: a view that sees a busy skyline discriminates
        # offsets far better than a view pointed at open water (which scores
        # near zero at EVERY offset). We weight each view's contribution by
        # the count of observed-building columns in its mask, so non-
        # informative views can't dilute the right anchor.
        def _view_weight(bmask):
            if bmask is None:
                return 0.0
            obs = np.asarray(bmask).sum(axis=0)
            # observed building cols ≥ 5 rows (same gate as the score itself)
            return float(np.count_nonzero(obs > 5))

        weights = [_view_weight(b) for (b, _w, _v) in masks_per_view]
        total_weight = float(sum(weights))

        # ── F-SKY11.1 Path B: pano-coastline heading recovery ──────────────
        # Stitch a pano water mask in API-heading frame (NOT yet rotated
        # by anchor_offset) and run the F-SKY11.1 keypoint sweep. The
        # recovered value is always logged so it can be compared to a
        # manual override; whether it gets used to seed the joint
        # optimizer is governed by the sharpness gate + the absence of a
        # manual override (manual always wins).
        pano_recovered_offset: float | None = None
        pano_recovered_sigma: float | None = None
        pano_recovered_peak: float | None = None
        # F-SKY13 Phase B diagnostics — populated alongside pano-recovery
        # when OSM coastline is available within 1 km of the seed.
        pano_osm_iou: float | None = None
        pano_osm_n_keypoints: int | None = None
        pano_projected_coastline: list[tuple[float, float]] | None = None
        if pano_recovery_state is not None:
            try:
                from .pipeline import (
                    stitch_pano_masks as _stitch_masks,
                    stitch_pano_views as _stitch_rgb,
                )
                from .coastline_registration import (
                    detect_coastline_keypoints, sweep_pano_heading_offset,
                )
                # Build the pano from EVERY successfully-fetched view
                # in prefetch, including the ones the per-view screening
                # rejected. The water-mask geometry the recovery needs
                # is preserved across all directions even when the view
                # contains no skyline structure (the rejection reason).
                _spin_views_raw = []
                for entry in prefetch:
                    _img = entry.get("image")
                    if _img is None:
                        continue
                    _bm = None
                    _wm = None
                    # Reuse the cached_views mask when available (avoids
                    # a redundant SegFormer forward pass for unrejected
                    # views), otherwise run SegFormer on the rejected
                    # view's image now.
                    for cv_i, cv in enumerate(cached_views):
                        if abs(cv["geo_heading"]
                               - entry["geo_heading"]) < 0.5:
                            _bm = masks_per_view[cv_i][0]
                            _wm = masks_per_view[cv_i][1]
                            break
                    if _wm is None:
                        from .pipeline import _neural_water_mask
                        _wm = _neural_water_mask(_img)
                    if _bm is None:
                        from .pipeline import _neural_sky_and_building_masks
                        _, _bm = _neural_sky_and_building_masks(_img)
                    _spin_views_raw.append({
                        "image": _img,
                        "geo_heading": float(entry["geo_heading"]),
                        "building_mask": _bm,
                        "water_mask": _wm,
                    })
                _rgb_stitched = _stitch_rgb(
                    _spin_views_raw, seed.fov, spin_step_deg)
                _stitched = _stitch_masks(
                    _spin_views_raw, seed.fov, spin_step_deg)
                if _rgb_stitched is not None and _stitched is not None:
                    _pano_img_unused, _headings_per_col = _rgb_stitched
                    _pb, _pw = _stitched
                    # Compute keypoints per-seed (they are inherently
                    # seed-centric — each seed sees the surrounding
                    # coastline at its own bearings/distances).
                    keypoints = detect_coastline_keypoints(
                        pano_recovery_state["sat_water"],
                        pano_recovery_state["sat_project"],
                        seed.lat, seed.lon,
                        n_bearings=24, max_range_m=2500.0,
                        step_m=5.0, min_distance_m=30.0,
                    )
                    if keypoints and _pw is not None:
                        best, _cand, _scores = sweep_pano_heading_offset(
                            keypoints, _pw, _headings_per_col,
                            seed.lat, seed.lon,
                            pitch_deg=effective_pitch, step_deg=1.0,
                            tolerance_px=25,
                        )
                        pano_recovered_offset = float(best)
                        pano_recovered_peak = float(_scores.max())
                        pano_recovered_sigma = float(_scores.std())
                        print(
                            f"[pano_recovery] seed={seed.name}  "
                            f"keypoints={len(keypoints)}  "
                            f"pano_views={len(_spin_views_raw)}/12  "
                            f"pano_water_frac={float(_pw.mean()):.3f}  "
                            f"recovered={pano_recovered_offset:.1f}deg  "
                            f"peak={pano_recovered_peak:.3f}  "
                            f"sigma={pano_recovered_sigma:.3f}"
                        )

                        # F-SKY13 Phase B: also score against OSM coastline
                        # (the trusted ground truth — see feedback memory).
                        # Reuses the same scoring function via the
                        # osm_keypoints_for_scoring adapter; no new scoring
                        # path. Also invert the pano water-top to lon/lat
                        # so the minimap can draw what the pano "sees" as
                        # the coastline.
                        try:
                            from .osm_water import (  # noqa: PLC0415
                                clip_to_radius,
                                osm_keypoints_for_scoring,
                            )
                            from .coastline_registration import (  # noqa: PLC0415
                                pano_water_top_to_lonlat,
                                score_pano_offset_keypoints,
                            )
                            # OSM coastline features are pre-extracted at
                            # region scope and stashed in pano_recovery_state
                            # (osm_data isn't in this function's scope).
                            _osm_coast = clip_to_radius(
                                pano_recovery_state.get("osm_coastline_features") or [],
                                (seed.lon, seed.lat),
                                radius_m=1000.0,
                            )
                            _osm_kps = osm_keypoints_for_scoring(
                                _osm_coast, (seed.lon, seed.lat),
                                spacing_m=20.0,
                            )
                            if _osm_kps:
                                pano_osm_iou = float(
                                    score_pano_offset_keypoints(
                                        _osm_kps, _pw, _headings_per_col,
                                        seed.lat, seed.lon,
                                        candidate_offset_deg=pano_recovered_offset,
                                        pitch_deg=effective_pitch,
                                        tolerance_px=25,
                                    )
                                )
                                pano_osm_n_keypoints = len(_osm_kps)
                            # Project pano water-top back to lon/lat for the
                            # dashed-orange minimap overlay. Independent of
                            # whether OSM coastline data was found.
                            pano_projected_coastline = (
                                pano_water_top_to_lonlat(
                                    _pw, _headings_per_col,
                                    seed.lat, seed.lon,
                                    column_stride=8,
                                    pitch_deg=effective_pitch,
                                )
                            )
                            print(
                                f"[pano_recovery] seed={seed.name}  "
                                f"osm_kp={pano_osm_n_keypoints}  "
                                f"osm_iou={pano_osm_iou}  "
                                f"projected_pts={len(pano_projected_coastline) if pano_projected_coastline else 0}"
                            )
                        except Exception as _e_osm:
                            print(
                                f"[pano_recovery] seed={seed.name} "
                                f"OSM diagnostic failed: {_e_osm}"
                            )
                    else:
                        print(f"[pano_recovery] seed={seed.name}  "
                              f"keypoints={len(keypoints)} — no recovery "
                              "attempted")
            except Exception as _e:
                print(f"[pano_recovery] seed={seed.name} failed: {_e}")

        # Manual override: if sites/<region>.json provides an
        # `anchor_offsets_deg` entry for this seed, skip the IoU
        # optimization entirely. This gives the user a deterministic
        # escape hatch when the algorithm picks a wrong local maximum
        # (degenerate score landscape for seeds with buildings in
        # multiple directions).
        override = (anchor_overrides or {}).get(seed.name)
        if override is not None:
            anchor_offset = float(override)
            if pano_recovered_offset is not None:
                _delta = (pano_recovered_offset - anchor_offset + 540.0) % 360.0 - 180.0
                print(f"[pano_recovery] seed={seed.name}  "
                      f"manual={anchor_offset:.1f}deg  "
                      f"delta_to_recovered={_delta:+.1f}deg")
        elif total_weight <= 0:
            anchor_offset = 0.0
        else:
            def _joint_score(cand: float) -> float:
                total = 0.0
                for (bmask, wmask, vp), wt in zip(masks_per_view, weights):
                    if bmask is None or wt <= 0:
                        continue
                    s = _score_offset_semantic_iou(
                        seed_buildings, vp, cand, bmask, wmask)
                    total += s * wt
                return total / total_weight

            # Initial-guess strategy: seed URLs carry an `h=X°` token that's
            # the compass heading the original Street View viewer pointed at.
            # For api_heading=0 to render the same world content, the
            # pano-to-geographic offset should be ≈ h. We use this as the
            # initial guess and search ±25° around it. Falls back to a full-
            # circle sweep when no h-token is available (i.e. the seed was
            # auto-proposed without a URL).
            #
            # This eliminates 180°-symmetric local maxima — the wrong-by-180°
            # offset would score reasonably on per-building IoU because OSM
            # building columns happen to land near the predicted columns, but
            # the depicted scene then points the wrong way and the user sees
            # geographically nonsensical orientations (e.g. seed_5 with
            # anchor=+101° showed Bocagrande towers at "effective_heading=65°
            # ENE" when the scene clearly depicts NORTH up the peninsula).
            # Full 360° coarse sweep at 3° resolution. The h-token-constrained
            # search was tried but produced edge-of-range optima (true value
            # outside the constraint), so we go global. The stronger
            # miss-penalty in _score_offset_semantic_iou (0.6 instead of
            # 0.3) is what excludes 180°-symmetric local maxima.
            # F-SKY11.1 Path B: skip the 360° coarse sweep and refine
            # directly around the pano-recovered offset only when ALL
            # gates clear:
            #   - sigma  <= 0.10  (sharp peak)
            #   - peak   >  0.15  (real signal — pure-zero curves can
            #                      still have sigma=0)
            #   - opt-in `use_pano_recovery_to_drive_anchor` site flag
            #
            # Calibration (2026-05-24, Cartagena 5-seed run):
            #   seed_1 peak=0.417 sigma=0.041 → correct  (Δ≈+1°)
            #   seed_4 peak=0.350 sigma=0.023 → wrong by 76°
            #   seed_5 peak=0.328 sigma=0.056 → wrong by 34°
            # Sigma alone can't discriminate (seed_4 has the SHARPEST
            # sigma but the worst answer). Peak does: the correct
            # recovery sits notably above the wrong ones. A 0.40 floor
            # passes seed_1 and rejects seeds 4/5, restoring 8/2/1
            # coverage when the manual overrides are also restored.
            # The previous 0.15 floor passed every recovery and dropped
            # coverage to 2/1/7 because drive_anchor sent seed_4/5 to
            # the wrong basins.
            _PANO_RECOVERY_SHARP_SIGMA = 0.10
            _PANO_RECOVERY_MIN_PEAK = 0.40
            allow_drive = bool(
                (pano_recovery_state or {}).get("drive_anchor", False))
            use_pano_seed = (
                allow_drive
                and pano_recovered_offset is not None
                and pano_recovered_sigma is not None
                and pano_recovered_peak is not None
                and pano_recovered_sigma <= _PANO_RECOVERY_SHARP_SIGMA
                and pano_recovered_peak > _PANO_RECOVERY_MIN_PEAK
            )
            if use_pano_seed:
                # Recovered offset is in [0, 360); normalise to the
                # same [-180, 180) range the coarse sweep uses so the
                # fine step's centre is consistent.
                recovered = pano_recovered_offset
                if recovered > 180.0:
                    recovered -= 360.0
                fine_offsets = np.arange(
                    recovered - 15.0, recovered + 15.0 + 0.001, 1.0)
                best_anchor_offset = recovered
                best_sum_iou = _joint_score(float(recovered))
                for cand in fine_offsets:
                    s = _joint_score(float(cand))
                    if s > best_sum_iou:
                        best_sum_iou = s
                        best_anchor_offset = float(cand)
                anchor_offset = best_anchor_offset
                print(f"[pano_recovery] seed={seed.name}  "
                      f"USED pano seed -> anchor={anchor_offset:.1f}deg  "
                      f"(joint_iou={best_sum_iou:.3f})")
            else:
                coarse_offsets = np.arange(-180.0, 180.0, 3.0)
                h_token = float(seed.heading) if seed.heading is not None else None
                coarse_best = -float("inf")
                coarse_best_offset = h_token if h_token is not None else 0.0
                for cand in coarse_offsets:
                    s = _joint_score(float(cand))
                    if s > coarse_best:
                        coarse_best = s
                        coarse_best_offset = float(cand)
                # Stage 2: fine 0.5° sweep within ±5° of coarse best
                fine_offsets = np.arange(
                    coarse_best_offset - 5.0, coarse_best_offset + 5.0 + 0.001, 0.5)
                best_anchor_offset = coarse_best_offset
                best_sum_iou = coarse_best
                for cand in fine_offsets:
                    s = _joint_score(float(cand))
                    if s > best_sum_iou:
                        best_sum_iou = s
                        best_anchor_offset = float(cand)
                anchor_offset = best_anchor_offset

        # ── Pass 2: re-register each view with a tight search around the
        # seed-level anchor. Allow ±8° local adjustment to absorb small
        # per-view noise without letting any single view jump to a wildly
        # different offset.
        for cv in cached_views:
            geo_heading = cv["geo_heading"]
            image = cv["image"]
            cap = cv["cap"]
            sv_label = cv["sv_label"]
            reg = register_view_to_osm(
                cap, seed_buildings,
                heading_search_deg=8.0, heading_step_deg=1.0,
                forced_center_deg=anchor_offset,
            )
            score = float(reg.get("best_score", float("inf")))
            n_matches = int(reg.get("n_matches", 0))
            if not math.isfinite(score) or n_matches < 3:
                # Forced anchor failed — DROP this view. The previous wide-
                # search fallback let individual views adopt completely
                # different offsets (e.g. seed_1 had three views with
                # offsets +37.5°, -80°, +42.5° — geometrically impossible
                # for a single rigid pano). All 12 spin views of one Photo
                # Sphere MUST share the same pano-to-geographic offset; the
                # joint optimizer's anchor is the right value for all of
                # them or none. Per-view escape breaks that invariant and
                # produces misleading minimaps.
                continue

            # Aerial detection: use the *effective* pitch stored in the
            # Viewpoint, not seed.pitch. If pitch correction fired in the
            # fetch loop above, cap.viewpoint.pitch reflects the corrected
            # angle and the aerial flag should be cleared.
            is_aerial = cap.viewpoint.pitch < -8.0

            est_for_view: list = []
            is_negative_seed = bool(
                negative_seeds and seed.name in negative_seeds)
            # Only "good" and "medium" views contribute height estimates.
            # "weak" views pass the flat-horizon gate but have low skyline
            # structure — their estimates are unreliable (e.g. ocean horizon
            # with minimal buildings). They still appear in the PDF for review.
            # Negative-example seeds (declared in sites/<region>.json as
            # ``negative_seeds``) are processed end-to-end so the PDF can
            # show their views, but contribute NO height estimates to the
            # aggregate. They serve as a regression suite for screening +
            # matching quality: the pipeline should reject these as non-
            # skyline; any time it doesn't, that's a defect to fix.
            if (not is_aerial
                    and sv_label not in ("weak", "rejected")
                    and not is_negative_seed):
                est_for_view = estimate_heights_from_registration(
                    cap,
                    reg,
                    seed_buildings,
                    camera_height_m=1.7,
                    camera_elev_m=float(seed_elev),
                    trace=trace,
                    max_plausible_height_m=max_plausible_height_m,
                )
                # F-SKY12: augment estimates with depth-derived heights as
                # an independent verifier. Pure diagnostic in Phase A — does
                # not alter ``estimated_height_m``. Off unless
                # SKYLINE_CV_F_SKY12=1 because DA2 inference is ~1–2 s/view.
                if _F_SKY12_ENABLED and est_for_view:
                    est_for_view = augment_estimates_with_depth(
                        image, est_for_view, cap.viewpoint, camera_height_m=1.7,
                    )
                estimates.extend(est_for_view)

            buildings_by_id = {b.feature_id: b for b in seed_buildings}
            # Two complementary segmentation sources:
            #  - contour-peak (good for distinct spires with sky valleys)
            #  - mask-component (catches buildings the contour merges or
            #    misses, e.g. mid-rise rows or near-flat skylines)
            contour_segs = detect_building_silhouettes(
                reg.get("contour"), image)
            _, _bmask = _neural_sky_and_building_masks(image)
            mask_segs = detect_buildings_from_mask(
                _bmask, contour=reg.get("contour"), image=image,
            )
            segments = _merge_silhouette_sources(contour_segs, mask_segs)
            all_proj_list = reg.get(
                "all_projections") or reg.get("projections", [])
            # F-SKY2: when registration is confident (≥ 3 OSM matches in the
            # primary peak), use the OSM projections as structural anchors
            # to split silhouettes that SegFormer merged across adjacent
            # towers. Skipped when registration is weak — applying anchors
            # from a wrong-offset registration would split correct segments
            # at wrong places. Originally gated at ≥ 5 but lowered to ≥ 3
            # (F-SKY2.1) after observing that the high gate excluded the
            # exact failure case it was meant to fix: merged-mask views
            # naturally produce fewer peaks → fewer matches → blocked from
            # the anchored splitting that would recover the missing peaks.
            if int(reg.get("n_matches", 0)) >= 3:
                # F-SKY2: split at clear mask gaps between adjacent OSM
                # projections (snaps to actual mask valley).
                segments = osm_anchor_silhouettes(
                    segments, all_proj_list, building_mask=_bmask)
                # F-SKY3 (Voronoi marker splitting) was removed 2026-05-18.
                # Regressed Cartagena MAE 17.28 → 22.13 / tagged-building
                # count 13 → 8. See docs/plans/F-SKY3-osm-marker-instances.md
                # for the post-mortem; the right fix is a small instance-
                # segmentation model, not heuristic Voronoi.
            # Negative seeds (sites/<region>.json `negative_seeds`) are
            # known-bad camera positions — gas stations, parking lots,
            # under-bridge views with no actual skyline. Their per-view
            # estimates were already excluded from the aggregate; ALSO
            # skip the matcher so the PDF page doesn't paint numbered
            # overlays and minimap footprint dots that imply legitimate
            # matches when none should exist. The page still shows the
            # photo, SegFormer mask overlay, and the [NEGATIVE EXAMPLE]
            # banner — that's the audit signal we want without spurious
            # match annotations.
            if is_negative_seed:
                matched_segments = []
            else:
                # F-SKY10: build a per-view cross-view scorer when the
                # region opted in. The satellite image + projection were
                # fetched once at the top of run_region_pdf_report; per-
                # view cost is the closure setup + per-candidate ndarray
                # crop inside the matcher.
                _cv_scorer = None
                if cross_view_state is not None:
                    try:
                        from .cross_view import make_cross_view_scorer
                        _cv_scorer = make_cross_view_scorer(
                            cross_view_state["sat_image"],
                            cross_view_state["sat_project"],
                            image,
                        )
                    except Exception as _e:
                        print(f"[cross_view] scorer build failed: {_e}")
                        _cv_scorer = None
                matched_segments = match_segments_to_buildings(
                    segments, all_proj_list, buildings_by_id,
                    cross_view_scorer=_cv_scorer,
                )
            # Diagnostic: annotate each matched segment with the matched building's
            # true bearing/distance from the seed camera, FOV-cone flag, the
            # height_proxy (tagged height or area-derived), the implied predicted
            # height from the sampled contour pixel, and whether the match is the
            # closest projected building in its column bin (a far building credited
            # for a closer roof is the dominant failure mode).
            effective_heading = (
                geo_heading + float(reg.get("best_offset", 0.0))) % 360.0
            half_fov = seed.fov * 0.5
            contour_arr = np.asarray(reg.get("contour", []), dtype=np.float32)
            f_px = 0.5 * image.shape[1] / \
                math.tan(math.radians(seed.fov) * 0.5)
            cy = image.shape[0] * 0.5
            pitch_rad = math.radians(cap.viewpoint.pitch)
            cam_z = float(seed_elev) + 1.7
            from .pipeline import _height_proxy as _hp

            def _annotate_match_diagnostics(seg: dict) -> None:
                """Populate seg with bearing / distance / predicted-height /
                closest-in-bin diagnostics derived from `matched_projection`.
                Called twice: once after the matcher, and again after any
                post-match correction so the displayed flags reflect the
                final choice.
                """
                m = seg.get("matched_projection")
                if not m:
                    return
                b = buildings_by_id.get(m["feature_id"])
                if b is None:
                    return
                true_bearing = _bearing_deg(
                    seed.lat, seed.lon, b.centroid_lat, b.centroid_lon)
                true_dist = _distance_m(
                    seed.lat, seed.lon, b.centroid_lat, b.centroid_lon)
                delta = (true_bearing - effective_heading +
                         540.0) % 360.0 - 180.0
                proxy_h = float(_hp(b))
                x_px = int(round(float(m.get("x_px", 0))))
                pred_h = float("nan")
                if 0 <= x_px < contour_arr.size:
                    y_px = float(contour_arr[x_px])
                    if np.isfinite(y_px):
                        ang = math.atan((cy - y_px) / f_px) + pitch_rad
                        pred_h = max(
                            0.0,
                            cam_z + float(m.get("forward_m", 0.0)
                                          ) * math.tan(ang)
                            - float(getattr(b, "terrain_elev_m", 0.0))
                        )
                near = [
                    p for p in all_proj_list
                    if abs(float(p.get("x_px", 0)) - float(m.get("x_px", 0))) <= 15.0
                ]
                is_closest = bool(near) and min(
                    float(p.get("forward_m", 1e9)) for p in near
                ) >= float(m.get("forward_m", 0.0)) - 1.0
                seg["true_bearing_deg"] = true_bearing
                seg["true_distance_m"] = true_dist
                seg["bearing_delta_deg"] = delta
                seg["bearing_in_fov"] = abs(delta) <= half_fov
                seg["height_proxy_m"] = proxy_h
                seg["predicted_height_m"] = pred_h
                seg["is_closest_in_bin"] = is_closest
                seg["height_tag_m"] = (
                    float(b.height_tag_m) if b.height_tag_m is not None else None)

            for seg in matched_segments:
                _annotate_match_diagnostics(seg)

            # ----------------------------------------------------------------
            # Post-match cross-verification (2026-05-19): re-rank when the
            # diagnostic flags reveal the matcher picked a worse candidate.
            # The original matcher already tries "nearest in bucket wins",
            # but its bucket is gated to candidates whose combined score is
            # within 0.10 of the best — a closer-but-lower-IoU candidate
            # can fall outside that band and lose. We catch that here.
            #
            # Inputs already computed above:
            #   seg.is_closest_in_bin  — False ⇒ a closer projection exists
            #     in the same column bin
            #   seg.bearing_in_fov     — False ⇒ matched building's true
            #     bearing is outside the view's FOV (geometry says it
            #     can't be visible)
            #   seg.predicted_height_m — implied height from contour-y
            #     under the matched building's distance
            #   seg.height_proxy_m     — OSM tagged height or area-based
            #     fallback
            #   seg.match_diagnostics  — top-3 candidates from the matcher
            #
            # Swap rule: if (B-flag fired OR predicted_height_m is wildly
            # implausible OR bearing_in_fov is False) AND the top-3
            # diagnostics contain a candidate that is closer, plausible
            # height (≥ 5 m), and within FOV, swap to it. Re-annotate so
            # downstream rendering / height extraction see the corrected
            # match.
            #
            # Conservative thresholds chosen so the swap can only IMPROVE
            # geometry consistency; a closer candidate with worse IoU is
            # still rejected if its bearing is out of FOV.
            # ----------------------------------------------------------------
            n_swapped = 0
            for seg in matched_segments:
                m = seg.get("matched_projection")
                if m is None:
                    continue
                diags = seg.get("match_diagnostics") or []
                if len(diags) < 2:
                    continue
                cur_fwd = float(m.get("forward_m", 1e9))
                cur_pred_h = float(seg.get("predicted_height_m", float("nan")))
                cur_in_fov = bool(seg.get("bearing_in_fov", True))
                cur_is_closest = bool(seg.get("is_closest_in_bin", True))
                cur_height_implausible = (
                    not np.isfinite(cur_pred_h)
                    or cur_pred_h > max_plausible_height_m * 1.25
                    or cur_pred_h < 1.5
                )
                # Trigger the rescue search whenever ANY geometric red flag
                # fires. The quality bar on the swap TARGET below decides
                # whether the swap actually happens — a B flag without a
                # better alternative still leaves the current match alone.
                needs_swap = (
                    (not cur_in_fov)
                    or cur_height_implausible
                    or (not cur_is_closest)
                )
                if not needs_swap:
                    continue
                # Best alternative: search the seg's top-3 diagnostics and
                # the full all_proj_list for a closer-and-plausible match.
                cur_fid = str(m.get("feature_id", ""))
                best_alt = None
                best_alt_fid: str | None = None
                for d in diags:
                    alt_fid = str(d.get("feature_id", ""))
                    if alt_fid == cur_fid:
                        continue
                    alt_p = next(
                        (p for p in all_proj_list
                         if str(p.get("feature_id", "")) == alt_fid),
                        None,
                    )
                    if alt_p is None:
                        continue
                    alt_fwd = float(alt_p.get("forward_m", 1e9))
                    if alt_fwd >= cur_fwd - 1.0:
                        continue  # not actually closer
                    alt_b = buildings_by_id.get(alt_fid)
                    if alt_b is None:
                        continue
                    alt_proxy_h = float(_hp(alt_b))
                    if alt_proxy_h < 5.0:
                        continue  # ignore kiosks as rescue candidates
                    # Bearing-in-FOV check for the alternative
                    alt_true_bearing = _bearing_deg(
                        seed.lat, seed.lon,
                        alt_b.centroid_lat, alt_b.centroid_lon)
                    alt_delta = (alt_true_bearing - effective_heading +
                                 540.0) % 360.0 - 180.0
                    if abs(alt_delta) > half_fov:
                        continue
                    # Implied height under alternative's forward_m must be
                    # plausible (otherwise no improvement over the current).
                    alt_x_px = int(round(float(alt_p.get("x_px", 0))))
                    alt_pred_h = float("nan")
                    if 0 <= alt_x_px < contour_arr.size:
                        y_px = float(contour_arr[alt_x_px])
                        if np.isfinite(y_px):
                            ang = math.atan((cy - y_px) / f_px) + pitch_rad
                            alt_pred_h = max(
                                0.0,
                                cam_z + alt_fwd * math.tan(ang)
                                - float(getattr(alt_b, "terrain_elev_m", 0.0))
                            )
                    if (not np.isfinite(alt_pred_h)
                            or alt_pred_h > max_plausible_height_m
                            or alt_pred_h < 1.5):
                        continue
                    if best_alt is None or alt_fwd < float(
                            best_alt.get("forward_m", 1e9)):
                        best_alt = alt_p
                        best_alt_fid = alt_fid
                if best_alt is not None and best_alt_fid != cur_fid:
                    # Honour F-SKY6 1:1 constraint: don't steal a building
                    # already claimed by a higher-scoring sibling segment.
                    already_claimed = any(
                        s is not seg
                        and s.get("matched_projection") is not None
                        and str(s["matched_projection"].get(
                            "feature_id", "")) == best_alt_fid
                        for s in matched_segments
                    )
                    if not already_claimed:
                        seg["matched_projection_pre_correction"] = m
                        seg["matched_projection"] = best_alt
                        seg["match_corrected"] = True
                        _annotate_match_diagnostics(seg)
                        n_swapped += 1
            if n_swapped:
                print(
                    f"[cross_verify] {cap.viewpoint.name}: "
                    f"corrected {n_swapped} match(es) via post-hoc rescue"
                )

            # ----------------------------------------------------------------
            # View-level cross-checks (2026-05-19): two consensus signals
            # that surface failures the per-match rescue can't catch.
            #
            # (1) Heading-consistency: each match has a `bearing_delta_deg`
            #     (matched building's true bearing minus the camera's
            #     effective heading). If the camera heading was right, the
            #     deltas would cluster around segment offsets (each segment
            #     sits at some α inside the FOV, so its bearing_delta is α).
            #     The MEDIAN delta across all matches measures the *common*
            #     bias — a clean centred view should have median ≈ 0. A
            #     non-trivial median means every match is shifted in the
            #     same direction, which is the fingerprint of a wrong
            #     heading offset (or wrong projection).
            #
            # (2) Multi-building segment: a segment that's much wider than
            #     its matched building's projected width is masking several
            #     buildings as one. Flag width_ratio = seg_width / proj_width
            #     and also count how many *other* projections fall inside
            #     the segment's x-range — the user's "segment 7 is 5
            #     buildings" case.
            # ----------------------------------------------------------------
            deltas: list[float] = []
            wide_segs: list[dict] = []
            for seg in matched_segments:
                m = seg.get("matched_projection")
                if m is None:
                    continue
                bd = seg.get("bearing_delta_deg")
                if bd is not None and np.isfinite(bd):
                    deltas.append(float(bd))
                seg_w = float(seg["x_right"]) - float(seg["x_left"])
                proj_w = max(1.0,
                             float(m.get("x_right_px", m.get("x_px", 0))) -
                             float(m.get("x_left_px", m.get("x_px", 0))))
                width_ratio = seg_w / proj_w
                # Count OTHER projections whose centre x_px falls inside
                # this segment — these are candidate sibling buildings the
                # segment is masking.
                others = []
                for p in all_proj_list:
                    if str(p.get("feature_id", "")) == str(m.get("feature_id", "")):
                        continue
                    px = float(p.get("x_px", -1))
                    if float(seg["x_left"]) <= px <= float(seg["x_right"]):
                        others.append(p)
                seg["seg_width_px"] = seg_w
                seg["proj_width_px"] = proj_w
                seg["width_ratio"] = width_ratio
                seg["covered_other_projs"] = len(others)
                if width_ratio >= 2.5 or len(others) >= 2:
                    seg["multi_building_candidate"] = True
                    wide_segs.append(seg)
            if len(deltas) >= 3:
                med = float(np.median(deltas))
                # Match-residuals around the consensus: how spread out are
                # they? Tight cluster around `med` means heading-offset bias
                # is the dominant story (and `med` is the bias). Broad
                # spread means matches are individually noisy too.
                res = [abs(d - med) for d in deltas]
                mad = float(np.median(res))
                # Side-channel print: don't alter behaviour, just surface
                # the diagnostic where a session log can spot it.
                if abs(med) > 5.0 or mad > 15.0:
                    print(
                        f"[heading_consistency] {cap.viewpoint.name}: "
                        f"median bearing_delta={med:+.1f}° "
                        f"MAD={mad:.1f}° n={len(deltas)} "
                        f"— {'heading offset may be biased' if abs(med) > 5.0 else 'matches scattered'}"
                    )
            if wide_segs:
                ratios = [f"{int(s.get('width_ratio', 0))}×" for s in wide_segs]
                covered = [str(s.get('covered_other_projs', 0)) for s in wide_segs]
                print(
                    f"[multi_building] {cap.viewpoint.name}: "
                    f"{len(wide_segs)} wide segment(s) "
                    f"(width_ratios={','.join(ratios)} "
                    f"others_inside={','.join(covered)})"
                )

            overlay = _registration_overlay(
                image, reg, matched_segments=matched_segments)
            # Compute building band on the original image's mask for display
            # cropping. compute_building_band returns None if too little of
            # the frame is building (e.g. ocean view) — then we render full
            # frame as a fallback.
            from .pipeline import compute_building_band  # noqa: PLC0415
            band = compute_building_band(_bmask, slack_px=20)
            view_rows.append(
                SeedViewRegistration(
                    seed_name=seed.name,
                    seed_lat=seed.lat,
                    seed_lon=seed.lon,
                    heading=geo_heading,
                    fov=seed.fov,
                    registration_score=score,
                    best_offset=float(reg.get("best_offset", 0.0)),
                    estimates_count=len(est_for_view),
                    matched_segments=matched_segments,
                    image=overlay,
                    is_aerial=is_aerial,
                    iou=float(reg.get("best_iou", 0.0)),
                    band_y=band,
                    is_negative=is_negative_seed,
                    building_mask=_bmask,
                    pano_osm_iou=pano_osm_iou,
                    pano_osm_n_keypoints=pano_osm_n_keypoints,
                    pano_projected_coastline=pano_projected_coastline,
                    view_estimates=list(est_for_view) if est_for_view else None,
                )
            )

        # ── Pass 3 (per seed): stitched-pano detection ───────────────────────
        # Negative seeds (declared in sites/<region>.json as ``negative_seeds``)
        # are non-skyline locations used as regression examples.  They already
        # contributed no height estimates in Pass 2.  Skipping Pass 3 entirely
        # means they don't generate a pano page, which keeps the report clean
        # and avoids wasting SegFormer inference + building-matching time on
        # scenes that will never yield useful annotations.
        is_negative_seed_for_pano = bool(
            negative_seeds and seed.name in negative_seeds)
        if is_negative_seed_for_pano:
            continue

        # Stitch per-view masks (already computed during Pass 2) into one
        # 360° strip. Building silhouettes are then detected on the unified
        # mask, projected OSM footprints are mapped to stitched columns by
        # bearing lookup, and segments are matched to buildings.
        #
        # Previously this re-ran SegFormer with a sliding window on the
        # stitched RGB strip. That introduced seam artefacts (each window
        # had a non-square aspect that didn't match SegFormer's training
        # distribution) and re-paid 5+ inferences per seed for no quality
        # benefit. The model already saw each tile at native size during
        # Pass 2; stitching the resulting masks with the same geometry as
        # the RGB stitcher is strictly higher quality AND faster.
        try:
            from .pipeline import (  # noqa: PLC0415
                stitch_pano_views,
                stitch_pano_masks,
                project_buildings_to_pano,
                detect_buildings_from_mask as _det_pano,
                match_segments_to_buildings as _match_pano,
                compute_building_band as _cbb,
                _neural_sky_and_building_masks,
                _neural_water_mask,
            )
            spin_views_for_pano: list[dict] = []
            for cv in cached_views:
                _sky, bmask = _neural_sky_and_building_masks(cv["image"])
                wmask = _neural_water_mask(cv["image"])
                spin_views_for_pano.append({
                    "image": cv["image"],
                    "geo_heading": (cv["geo_heading"] + anchor_offset) % 360.0,
                    "building_mask": bmask,
                    "water_mask": wmask,
                })
            stitch_out = stitch_pano_views(
                spin_views_for_pano, seed.fov, spin_step_deg)
            mask_out = stitch_pano_masks(
                spin_views_for_pano, seed.fov, spin_step_deg)
            if stitch_out is not None and mask_out is not None:
                pano_img, pano_headings = stitch_out
                pano_bmask, pano_wmask = mask_out
                if pano_bmask.shape[1] != pano_img.shape[1]:
                    raise ValueError(
                        f"pano image/mask width mismatch: "
                        f"img={pano_img.shape[1]} mask={pano_bmask.shape[1]}")
                pano_band = _cbb(pano_bmask, slack_px=20)
                pano_segs = _det_pano(pano_bmask)
                pano_projs = project_buildings_to_pano(
                    seed_buildings, seed.lat, seed.lon, pano_headings)
                bbid = {b.feature_id: b for b in seed_buildings}
                pano_matched = _match_pano(pano_segs, pano_projs, bbid)
                # Stamp each matched segment with its geographic bearing so
                # the minimap can draw an accurate dashed ray. The bearing
                # is looked up from headings_per_col at the segment's peak
                # column (or midpoint as fallback).
                for seg in pano_matched:
                    px = int(seg.get("peak_x", seg.get("mid_x", 0)))
                    px = max(0, min(pano_headings.size - 1, px))
                    seg["true_bearing_deg"] = float(pano_headings[px])
                n_matched = sum(
                    1 for s in pano_matched if s.get("matched_projection"))
                pano_results.append(StitchedPanoResult(
                    seed_name=seed.name,
                    seed_lat=seed.lat,
                    seed_lon=seed.lon,
                    pano_image=pano_img,
                    band_y=pano_band,
                    matched_segments=pano_matched,
                    n_segments=len(pano_segs),
                    n_matched=n_matched,
                    n_buildings_in_view=len(pano_projs),
                    anchor_offset_deg=float(anchor_offset),
                    headings_per_col=pano_headings,
                ))
        except Exception as e:
            # Pano path is supplementary — failure should never break the
            # primary per-view pipeline. Log to stderr only.
            import sys as _sys
            print(f"[pano] seed={seed.name} failed: {e}",
                  file=_sys.stderr)

    agg = aggregate_building_heights(estimates) if estimates else []
    return view_rows, agg, pano_results


def _draw_osm_coastline_overlay(
    ax,
    seed_lon: float,
    seed_lat: float,
    mlon: float,
    mlat: float,
    osm_data: dict,
    radius_m: float = 1000.0,
    *,
    satellite_bg: bool = False,
    pano_projected_coastline: "list[tuple[float, float]] | None" = None,
    pano_osm_iou: float | None = None,
    pano_osm_n_keypoints: int | None = None,
) -> None:
    """F-SKY13: draw OSM coastline + 1 km consideration window on a minimap.

    OSM ``natural=coastline`` linestrings within ``radius_m`` of the seed are
    drawn as a solid blue line (the primary coastline ground truth). A dashed
    grey circle marks the consideration window. Optional water polygons (bay/
    ocean/sea) get a faint blue fill so the user can immediately see which
    side of the coastline is wet.

    When ``satellite_bg=True`` an ESRI satellite tile covering the 1 km
    window is fetched (or loaded from the on-disk cache populated by
    ``city2stl.skyline_cv.satellite_image.fetch_region_satellite``) and
    rendered as a faint underlay so the OSM features sit on top of real
    imagery. Reuses the existing satellite-tile primitive — does not add a
    second fetch path. Network failures degrade gracefully (no background
    drawn).

    No-op when the OSM ``waterways`` layer is empty or contains no features
    within the radius — keeps inland seeds clean.
    """
    try:
        from city2stl.skyline_cv.osm_water import (  # noqa: PLC0415
            clip_to_radius,
            extract_coastline_features,
            extract_water_features,
        )
    except ImportError as exc:
        logger.warning("F-SKY13 osm_water module unavailable: %s", exc)
        return

    # ── Optional satellite-image background (under everything) ─────────────
    # Computed before the no-op short-circuit so the background shows even
    # on inland seeds (where there's no coastline but the imagery is still
    # informative).
    if satellite_bg:
        try:
            from city2stl.skyline_cv.satellite_image import (  # noqa: PLC0415
                fetch_region_satellite,
            )
            # Build a 1 km bbox around the seed. mlon/mlat are lon/lat→m
            # scale factors; invert to get degrees per metre.
            dlon = radius_m / mlon
            dlat = radius_m / mlat
            sat_bbox = (
                seed_lat - dlat,  # south
                seed_lon - dlon,  # west
                seed_lat + dlat,  # north
                seed_lon + dlon,  # east
            )
            sat_img, _proj, _meta = fetch_region_satellite(
                sat_bbox, target_m_per_px=2.0,
            )
            ax.imshow(
                sat_img,
                extent=(sat_bbox[1], sat_bbox[3], sat_bbox[0], sat_bbox[2]),
                origin="upper",
                alpha=0.55,
                zorder=0,
                interpolation="bilinear",
            )
        except Exception as exc:
            logger.info(
                "F-SKY13 satellite background unavailable: %s", exc
            )

    seed_lonlat = (seed_lon, seed_lat)
    coastline = clip_to_radius(
        extract_coastline_features(osm_data), seed_lonlat, radius_m
    )
    water = clip_to_radius(
        extract_water_features(osm_data), seed_lonlat, radius_m
    )

    if not coastline and not water:
        return  # inland seed — nothing to draw

    # ── Water-area fill (faint blue, sits between grey footprints and coastline)
    if water:
        from matplotlib.collections import PatchCollection  # noqa: PLC0415
        water_polys: list[mpatches.Polygon] = []
        for feat in water:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if geom.get("type") == "Polygon":
                rings = [coords[0]] if coords else []
            elif geom.get("type") == "MultiPolygon":
                rings = [poly[0] for poly in coords if poly]
            else:
                rings = []
            for ring in rings:
                if len(ring) < 3:
                    continue
                xs = [float(p[0]) for p in ring]
                ys = [float(p[1]) for p in ring]
                water_polys.append(mpatches.Polygon(
                    np.column_stack([xs, ys]), closed=True))
        if water_polys:
            ax.add_collection(PatchCollection(
                water_polys,
                facecolor=(0.45, 0.65, 0.85, 0.18),
                edgecolor=(0.20, 0.40, 0.65, 0.30),
                linewidths=0.4,
                zorder=1.5,
                match_original=False,
            ))

    # ── Coastline linestrings — solid blue, the trusted reference
    for feat in coastline:
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "LineString":
            lines = [coords]
        elif gtype == "MultiLineString":
            lines = coords
        else:
            continue
        for line in lines:
            if len(line) < 2:
                continue
            xs = [float(p[0]) for p in line]
            ys = [float(p[1]) for p in line]
            ax.plot(
                xs, ys,
                color=(0.10, 0.30, 0.70),
                linewidth=1.6,
                alpha=0.85,
                zorder=3.0,
                label="OSM coastline" if "_osm_coastline_label" not in ax.__dict__ else None,
            )
            ax.__dict__["_osm_coastline_label"] = True

    # ── 1 km consideration window (dashed grey circle around the seed) ──
    # Convert metres to degree offsets via the cached lat-scaled factors.
    # 100-point polyline closes the loop visually without using matplotlib's
    # Circle patch (which doesn't respect equal-aspect well at these scales).
    n_pts = 100
    angles = np.linspace(0.0, 2.0 * math.pi, n_pts, dtype=np.float64)
    radius_lon = radius_m / mlon
    radius_lat = radius_m / mlat
    circle_x = seed_lon + radius_lon * np.cos(angles)
    circle_y = seed_lat + radius_lat * np.sin(angles)
    ax.plot(
        circle_x, circle_y,
        color=(0.35, 0.35, 0.35, 0.65),
        linewidth=0.9,
        linestyle="--",
        zorder=2.5,
    )

    # ── F-SKY13 Phase B: pano-projected coastline (where the pano "sees"
    # water meeting land, projected back to lon/lat). Each entry is a
    # discrete sea-level point at the apparent water-top of one pano column.
    # Drawn as scattered dots rather than a connected polyline because
    # adjacent columns may sample different distances (e.g. when a pier
    # juts out into the bay) and a polyline would zig-zag awkwardly.
    if pano_projected_coastline:
        xs = [p[0] for p in pano_projected_coastline]
        ys = [p[1] for p in pano_projected_coastline]
        ax.scatter(
            xs, ys,
            s=6,
            color=(0.95, 0.55, 0.10),
            alpha=0.85,
            zorder=3.5,
            edgecolors="none",
            label="pano-projected coastline",
        )

    # ── F-SKY13 Phase B: pano↔OSM IoU score as a small annotation in the
    # top-left corner of the minimap. Lets the user see the registration
    # confidence number alongside the visual coastline agreement check.
    if pano_osm_iou is not None:
        n_kp = pano_osm_n_keypoints or 0
        score_txt = f"pano↔OSM IoU: {pano_osm_iou:.2f}   ({n_kp} keypoints)"
        ax.text(
            0.02, 0.98, score_txt,
            transform=ax.transAxes,
            fontsize=8,
            fontweight="bold",
            verticalalignment="top",
            color=(0.10, 0.30, 0.70),
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor=(1.0, 1.0, 1.0, 0.85),
                edgecolor=(0.10, 0.30, 0.70, 0.6),
                linewidth=0.6,
            ),
            zorder=10,
        )


def _draw_view_minimap(
    ax,
    seed_lat: float,
    seed_lon: float,
    heading_deg: float,
    fov_deg: float,
    osm_data: dict,
    matched_segments: list[dict],
    buildings_by_id: dict | None = None,
    image_width: int = 960,
    radius_m: float = 1500.0,
    *,
    pano_osm_iou: float | None = None,
    pano_osm_n_keypoints: int | None = None,
    pano_projected_coastline: "list[tuple[float, float]] | None" = None,
) -> None:
    """Draw an OSM-footprint mini-map centered on the seed, with matched
    buildings colored to match their skyline segment.

    Each detected segment gets:
    - its **footprint polygon** filled in the segment color (so the user can
      visually verify "image-box N corresponds to the building at footprint N
      on the map");
    - a dashed bearing line from the seed toward that footprint;
    - a numbered label at the footprint centroid matching the legend.

    ``buildings_by_id`` maps the deterministic feature_id (e.g. "b0142") to
    the matched BuildingRecord — used to draw the polygon in the segment
    colour, since the raw OSM feature list uses a different id encoding.
    """
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    dlat = radius_m / mlat
    dlon = radius_m / mlon

    # Map deterministic feature_id → segment_index
    matched_ids = {
        seg["matched_projection"]["feature_id"]: i
        for i, seg in enumerate(matched_segments)
        if seg.get("matched_projection")
    }

    # ── Pass 1: all OSM footprints in light grey (context) ──────────────────
    # PatchCollection is ~50× faster than per-polygon add_patch when drawing
    # 3000+ context polygons per view; matplotlib batches the path drawing.
    from matplotlib.collections import PatchCollection  # noqa: PLC0415
    _grey_polys: list[mpatches.Polygon] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        for ring in _feature_rings(feat):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            _grey_polys.append(mpatches.Polygon(
                np.column_stack([xs, ys]), closed=True))
    if _grey_polys:
        ax.add_collection(PatchCollection(
            _grey_polys,
            facecolor=(0.7, 0.7, 0.7, 0.25),
            edgecolor=(0.4, 0.4, 0.4, 0.4),
            linewidths=0.3,
            zorder=1,
            match_original=False,
        ))

    # ── Pass 1b: F-SKY8 satellite (ms_buildings) polygons as dashed grey ────
    # The grey-context layer above only renders the raw OSM features. After
    # F-SKY8 the matcher works against a *merged* record set; without a
    # second context pass the satellite-only polygons (~half the inventory on
    # Cartagena) are invisible until they get matched. Drawing them with a
    # dashed edge keeps the OSM/MS distinction visible at a glance.
    if buildings_by_id is not None:
        _sat_polys: list[mpatches.Polygon] = []
        for fid, b in buildings_by_id.items():
            if not str(fid).startswith("ms_"):
                continue
            geom = getattr(b, "geometry", None)
            if geom is None:
                continue
            try:
                xs_b, ys_b = geom.exterior.xy
            except Exception:
                continue
            _sat_polys.append(mpatches.Polygon(
                np.column_stack([list(xs_b), list(ys_b)]), closed=True))
        if _sat_polys:
            ax.add_collection(PatchCollection(
                _sat_polys,
                facecolor=(0.85, 0.78, 0.55, 0.20),
                edgecolor=(0.55, 0.40, 0.10, 0.55),
                linewidths=0.4,
                linestyle="--",
                zorder=1.2,
                match_original=False,
            ))

    # ── Pass 2: matched footprints in segment colours (drawn on top) ───────
    # We use buildings_by_id which keys polygons by the same deterministic id
    # that the segment matches reference; the raw OSM feature list uses a
    # different id encoding (raw index pre-sort) and cannot be matched here.
    matched_footprint_centroids: dict[int, tuple[float, float]] = {}
    if buildings_by_id is not None:
        for fid, seg_idx in matched_ids.items():
            b = buildings_by_id.get(fid)
            if b is None:
                continue
            geom = getattr(b, "geometry", None)
            if geom is None:
                continue
            try:
                xs, ys = geom.exterior.xy
            except Exception:
                continue
            color = tuple(
                c / 255.0 for c in _SEGMENT_PALETTE[seg_idx % len(_SEGMENT_PALETTE)])
            # Satellite-sourced matches get a dashed edge so the user can
            # spot at a glance which matches are F-SKY8 fill-ins (OSM gap
            # candidates) vs OSM-anchored matches. Fill stays the segment
            # colour either way.
            is_sat = str(fid).startswith("ms_")
            poly = mpatches.Polygon(
                np.column_stack([list(xs), list(ys)]),
                closed=True,
                facecolor=(*color, 0.85),
                edgecolor=(*color, 1.0),
                linewidth=1.4,
                linestyle="--" if is_sat else "-",
                zorder=3,
            )
            ax.add_patch(poly)
            matched_footprint_centroids[seg_idx] = (
                float(b.centroid_lon), float(b.centroid_lat))

    # F-SKY6 Part 2: "considered but lost" candidates. The matcher's
    # match_diagnostics field records the top-3 candidates per segment.
    # Drawing the ones that didn't win as faint orange dots makes it
    # immediately visible whether OSM has buildings in an unmatched
    # x-band (orange dot present → matcher rejection issue) versus an
    # OSM data gap (no orange dots → no OSM building to match).
    if buildings_by_id is not None:
        considered_fids: set[str] = set()
        for seg in matched_segments:
            for d in seg.get("match_diagnostics", []):
                fid = d.get("feature_id")
                if fid and fid not in matched_ids:
                    considered_fids.add(fid)
        if considered_fids:
            xs_c: list[float] = []
            ys_c: list[float] = []
            for fid in considered_fids:
                b = buildings_by_id.get(fid)
                if b is None:
                    continue
                xs_c.append(float(b.centroid_lon))
                ys_c.append(float(b.centroid_lat))
            if xs_c:
                ax.scatter(
                    xs_c, ys_c,
                    s=14, c="orange", marker="o",
                    edgecolors=(0.7, 0.4, 0.0), linewidth=0.5,
                    alpha=0.7, zorder=2.5,
                    label="considered but lost",
                )

    # ── Per-segment bearing lines ─────────────────────────────────────────
    # Draw a dashed colored line from the seed toward each detected building
    # segment so the image boxes can be cross-referenced with the map.
    # Bearing source preference:
    #   1. seg["true_bearing_deg"]  (set by pano matcher — already geographic)
    #   2. heading + angle_off via inverse pinhole on peak_x (per-view path)
    # The fallback path uses the per-view image's intrinsic FOV; pano callers
    # avoid it by supplying true_bearing_deg directly.
    half_fov_tan = (
        math.tan(math.radians(fov_deg / 2.0))
        if 0.0 < fov_deg < 180.0 else None
    )
    line_len = radius_m * 0.70
    for i, seg in enumerate(matched_segments):
        color_rgb = _SEGMENT_PALETTE[i % len(_SEGMENT_PALETTE)]
        color = tuple(cv / 255.0 for cv in color_rgb)

        # Pano matcher supplies a precomputed bearing; respect it if present.
        explicit_bearing = seg.get("true_bearing_deg")
        if explicit_bearing is not None:
            seg_bearing = float(explicit_bearing) % 360.0
        elif half_fov_tan is not None:
            peak_x = seg.get("peak_x", seg.get("mid_x", image_width // 2))
            norm_x = (peak_x - image_width / 2.0) / (image_width / 2.0)
            angle_off = math.degrees(math.atan(norm_x * half_fov_tan))
            seg_bearing = (heading_deg + angle_off) % 360.0
        else:
            # No bearing source — skip the ray; still draw the centroid label.
            seg_bearing = None

        if seg_bearing is not None:
            seg_theta = math.radians(seg_bearing)
            ldx = line_len * math.sin(seg_theta) / mlon
            ldy = line_len * math.cos(seg_theta) / mlat
            ax.plot(
                [seed_lon, seed_lon + ldx],
                [seed_lat, seed_lat + ldy],
                color=color,
                linewidth=1.2,
                alpha=0.7,
                linestyle="--",
                zorder=2,
            )
        else:
            ldx = ldy = 0.0
        centroid = matched_footprint_centroids.get(i)
        if centroid is not None:
            lx, ly = centroid
        else:
            lx = seed_lon + ldx * 0.65
            ly = seed_lat + ldy * 0.65
        ax.annotate(
            str(i + 1),
            xy=(lx, ly),
            color="black",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="center",
            bbox=dict(boxstyle="circle,pad=0.15", facecolor=color,
                      edgecolor="black", linewidth=0.6, alpha=0.95),
            zorder=6,
        )

    # F-SKY13: OSM-coastline + 1 km consideration window overlay. OSM is the
    # primary coastline ground truth (the satellite HSV detector in
    # coastline_registration.py is unreliable — see feedback memory). Drawn
    # underneath the camera marker but on top of the building polygons, so
    # the user can verify pano↔OSM coastline agreement visually.
    if _F_SKY13_ENABLED:
        _draw_osm_coastline_overlay(
            ax,
            seed_lon=seed_lon,
            seed_lat=seed_lat,
            mlon=mlon,
            mlat=mlat,
            osm_data=osm_data,
            radius_m=_F_SKY13_RADIUS_M,
            satellite_bg=_F_SKY13_SAT_BG_ENABLED,
            pano_projected_coastline=pano_projected_coastline,
            pano_osm_iou=pano_osm_iou,
            pano_osm_n_keypoints=pano_osm_n_keypoints,
        )

    # Camera marker drawn now; the FOV cone is drawn AFTER auto-zoom so
    # we can scale it to the actual axis span (it previously used the
    # default radius_m=1500 which overshot tight auto-zoomed panels by
    # 5×, dominating the visual).
    ax.scatter([seed_lon], [seed_lat], c="red", s=80, marker="*", zorder=5)

    # Auto-zoom: prefer the bbox of matched-footprint polygons + the seed
    # position, expanded by a 100 m margin. Falls back to the fixed ±radius_m
    # window when no footprints are matched. Previously the fixed 1500 m
    # window left most of the minimap empty when matches clustered tightly,
    # making the badge numbers and polygons hard to read.
    bbox_lons: list[float] = [seed_lon]
    bbox_lats: list[float] = [seed_lat]
    if buildings_by_id is not None:
        for fid in matched_ids.keys():
            b = buildings_by_id.get(fid)
            if b is None:
                continue
            geom = getattr(b, "geometry", None)
            if geom is None:
                continue
            try:
                xs, ys = geom.exterior.xy
                bbox_lons.extend(float(x) for x in xs)
                bbox_lats.extend(float(y) for y in ys)
            except Exception:
                continue

    use_auto_zoom = len(bbox_lons) > 1  # more than just the seed
    if use_auto_zoom:
        margin_lon = 100.0 / mlon
        margin_lat = 100.0 / mlat
        x0 = min(bbox_lons) - margin_lon
        x1 = max(bbox_lons) + margin_lon
        y0 = min(bbox_lats) - margin_lat
        y1 = max(bbox_lats) + margin_lat
        # Enforce a minimum window (~300 m) so a single tight cluster doesn't
        # zoom in to a degenerate few-metre frame.
        min_span_lon = 300.0 / mlon
        min_span_lat = 300.0 / mlat
        if x1 - x0 < min_span_lon:
            cx = (x0 + x1) * 0.5
            x0, x1 = cx - min_span_lon * 0.5, cx + min_span_lon * 0.5
        if y1 - y0 < min_span_lat:
            cy = (y0 + y1) * 0.5
            y0, y1 = cy - min_span_lat * 0.5, cy + min_span_lat * 0.5
        # Equal aspect: enlarge the smaller dimension to match the larger.
        # Avoids matplotlib stretching the figure.
        dx = x1 - x0
        dy = y1 - y0
        # Both in metres for fair comparison
        dx_m = dx * mlon
        dy_m = dy * mlat
        if dx_m > dy_m:
            extra_lat = (dx_m - dy_m) / mlat * 0.5
            y0 -= extra_lat
            y1 += extra_lat
        else:
            extra_lon = (dy_m - dx_m) / mlon * 0.5
            x0 -= extra_lon
            x1 += extra_lon
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        radius_label = (
            f"~{int(max((x1-x0)*mlon, (y1-y0)*mlat) * 0.5)} m radius (auto)"
        )
    else:
        ax.set_xlim(seed_lon - dlon, seed_lon + dlon)
        ax.set_ylim(seed_lat - dlat, seed_lat + dlat)
        radius_label = f"radius {int(radius_m)}m"

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(alpha=0.2)
    ax.set_title(f"Footprints in view ({radius_label})")

    # Draw FOV cone AFTER axes are set so we can size it to the actual
    # axis span (max half-span in metres × 0.7 so the cone reaches but
    # doesn't escape the panel). Pano callers pass fov_deg=360 to mean
    # "no single direction" — skip the cone there. Also skip when the
    # view has fewer than 3 matched segments: the cone implies a
    # confident orientation, but with 0–2 matches the heading is
    # essentially unverified by the registration, and a prominent cone
    # is misleading — just the camera star is honest.
    if 0.0 < fov_deg < 180.0 and len(matched_segments) >= 3:
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        half_span_m = max(
            (xlim[1] - xlim[0]) * mlon,
            (ylim[1] - ylim[0]) * mlat,
        ) * 0.5
        cone_len = max(50.0, half_span_m * 0.7)
        half = math.radians(fov_deg * 0.5)
        theta = math.radians(heading_deg)
        for sign in (-1.0, 1.0):
            angle = theta + sign * half
            dx = cone_len * math.sin(angle) / mlon
            dy = cone_len * math.cos(angle) / mlat
            ax.plot(
                [seed_lon, seed_lon + dx], [seed_lat, seed_lat + dy],
                "r-", linewidth=0.8, alpha=0.6,
            )
        arrow_dx = cone_len * 0.5 * math.sin(theta) / mlon
        arrow_dy = cone_len * 0.5 * math.cos(theta) / mlat
        # Arrow head + body sized off the actual axis span. The default
        # `width` in matplotlib's ax.arrow is 0.001 (in DATA UNITS) which
        # at this lat/lon is ~100 m — produced a huge red bar across the
        # minimap. We size everything relative to the actual axis span.
        head_w = (xlim[1] - xlim[0]) * 0.025
        head_h = (ylim[1] - ylim[0]) * 0.03
        body_w = (xlim[1] - xlim[0]) * 0.005  # thin body, ~half the head
        ax.arrow(
            seed_lon, seed_lat, arrow_dx, arrow_dy,
            width=body_w,
            head_width=head_w, head_length=head_h,
            length_includes_head=True, color="red", alpha=0.7, zorder=4,
        )
        # Restore the axis limits — arrow drawing can expand them.
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)


def _render_stitched_pano_page(
    pdf,
    pr: "StitchedPanoResult",
    osm_data: dict,
    buildings_by_id: dict | None = None,
    seed_views: list[SeedViewRegistration] | None = None,
) -> None:
    """Render one comparison page per seed: stitched 360° pano with segment
    overlays on top, summary stats below (per-view vs pano numbers), and a
    minimap with all pano-matched footprints colored.

    The pano image is full-width with all 12 stitched crops laid horizontally.
    Each detected segment gets a colored bounding box + numbered badge so the
    user can pair segments to footprints on the minimap (same convention as
    the per-view pages).
    """
    fig = plt.figure(figsize=(14, 8.5))
    fig.suptitle(
        f"Stitched 360° pano — {pr.seed_name}  "
        f"anchor_offset={pr.anchor_offset_deg:+.1f}°  "
        f"segments={pr.n_segments}  matched={pr.n_matched}  "
        f"buildings_in_view={pr.n_buildings_in_view}",
        fontsize=12,
    )

    # Top half: pano image with segment overlays. Crop vertically to building
    # band if available so we don't waste space on water/sky.
    pano_img = pr.pano_image
    band = pr.band_y
    if band is not None:
        y0, y1 = band
        H = pano_img.shape[0]
        y0 = max(0, min(H - 1, int(y0)))
        y1 = max(y0 + 30, min(H, int(y1) + 1))
        if y1 - y0 > 60:
            pano_img = pano_img[y0:y1, :, ...]
    # Burn segments onto a copy so we don't mutate the cached array.
    overlay = pano_img.copy()
    for i, seg in enumerate(pr.matched_segments):
        if seg.get("matched_projection") is None:
            continue
        color = _SEGMENT_PALETTE[i % len(_SEGMENT_PALETTE)]
        xL, xR = int(seg["x_left"]), int(seg["x_right"])
        top_y, base_y = int(seg["top_y"]), int(seg["base_y"])
        if band is not None:
            top_y -= band[0]
            base_y -= band[0]
        top_y = max(0, top_y)
        base_y = min(overlay.shape[0] - 1, base_y)
        if base_y <= top_y or xR <= xL:
            continue
        cv2.rectangle(overlay, (xL, top_y), (xR, base_y), color, 2)
        cv2.circle(overlay, (xL + 11, max(15, top_y + 7)), 11, color, -1)
        cv2.circle(overlay, (xL + 11, max(15, top_y + 7)), 11, (0, 0, 0), 1)
        cv2.putText(
            overlay, str(i + 1),
            (xL + (4 if i + 1 < 10 else 1), max(20, top_y + 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )

    ax_pano = fig.add_axes([0.03, 0.50, 0.94, 0.38])
    ax_pano.imshow(overlay)
    # Heading-tick overlay along the bottom: every 30° draw a vertical
    # marker + compass label so we can verify visually that the stitching's
    # column-to-heading map matches the depicted scene. (If e.g. column
    # marked "180°" doesn't show buildings the user expects in the south
    # direction, that pinpoints a heading-mapping bug.)
    headings_per_col = getattr(pr, "headings_per_col", None)
    if headings_per_col is not None and headings_per_col.size > 0:
        h_img = overlay.shape[0]
        labeled_cardinals = [
            (0.0, "N"), (45.0, "NE"), (90.0, "E"), (135.0, "SE"),
            (180.0, "S"), (225.0, "SW"), (270.0, "W"), (315.0, "NW"),
        ]
        for hdg, name in labeled_cardinals:
            # Find the column closest to this compass heading
            diffs = np.abs(((headings_per_col - hdg + 180.0) % 360.0) - 180.0)
            if diffs.min() > 8.0:
                continue
            col = int(np.argmin(diffs))
            ax_pano.axvline(col, color="yellow", linewidth=0.5, alpha=0.7)
            ax_pano.text(
                col, h_img * 0.97, f"{name} {hdg:.0f}°",
                color="black", fontsize=8, ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.15", fc="yellow",
                          ec="black", alpha=0.85, lw=0.5),
            )
    ax_pano.axis("off")
    ax_pano.set_title(
        "Stitched pano — mask-stitched per-view masks (yellow ticks = compass headings)",
        fontsize=10,
    )

    # Bottom-left: pano-matched-footprint minimap.
    ax_map = fig.add_axes([0.04, 0.06, 0.45, 0.36])
    _draw_view_minimap(
        ax_map,
        pr.seed_lat,
        pr.seed_lon,
        # Use 0° heading and 360° FOV stand-in: we just want all matched
        # footprints visible. The minimap's per-segment bearing rays will
        # still go to the right places because they're computed from the
        # actual matched-footprint centroids when available.
        heading_deg=0.0,
        fov_deg=360.0,
        osm_data=osm_data,
        matched_segments=pr.matched_segments,
        buildings_by_id=buildings_by_id,
        image_width=pr.pano_image.shape[1],
    )

    # Bottom-right: side-by-side comparison vs per-view aggregate.
    ax_cmp = fig.add_axes([0.52, 0.06, 0.45, 0.36])
    ax_cmp.axis("off")
    per_view_for_seed = (
        [sv for sv in (seed_views or []) if sv.seed_name == pr.seed_name]
    )
    n_view_segments = sum(len(sv.matched_segments) for sv in per_view_for_seed)
    n_view_matched = sum(
        1 for sv in per_view_for_seed
        for seg in sv.matched_segments
        if seg.get("matched_projection")
    )
    matched_pano_ids = {
        seg["matched_projection"]["feature_id"]
        for seg in pr.matched_segments
        if seg.get("matched_projection")
    }
    matched_view_ids = {
        seg["matched_projection"]["feature_id"]
        for sv in per_view_for_seed
        for seg in sv.matched_segments
        if seg.get("matched_projection")
    }
    overlap_ids = matched_pano_ids & matched_view_ids
    pano_only = matched_pano_ids - matched_view_ids
    view_only = matched_view_ids - matched_pano_ids
    cmp_lines = [
        f"PANO vs PER-VIEW (this seed only)",
        "",
        f"{'metric':<28}{'pano':>10}{'per-view':>12}",
        f"{'-'*52}",
        f"{'segments detected':<28}{pr.n_segments:>10d}{n_view_segments:>12d}",
        f"{'segments with OSM match':<28}{pr.n_matched:>10d}{n_view_matched:>12d}",
        f"{'distinct buildings matched':<28}{len(matched_pano_ids):>10d}{len(matched_view_ids):>12d}",
        "",
        f"  overlap (both methods agree):  {len(overlap_ids):>4d}",
        f"  pano only:                     {len(pano_only):>4d}",
        f"  per-view only:                 {len(view_only):>4d}",
    ]
    ax_cmp.text(
        0.02, 0.98, "\n".join(cmp_lines),
        family="monospace", fontsize=9, va="top", transform=ax_cmp.transAxes,
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _count_seg_flags(matched_segments: list[dict]) -> tuple[int, int, int]:
    """Tally (F=outside_FOV, B=not_closest, P=implausible_vs_proxy) flags
    across a view's matched segments. Used by the per-view PDF header.
    Extracted so the rendering path doesn't carry an inline 20-line
    loop just to compute three counts.
    """
    n_fov_fail = n_not_closest = n_implausible = 0
    for seg in matched_segments:
        if seg.get("matched_projection") is None:
            continue
        if not bool(seg.get("bearing_in_fov", True)):
            n_fov_fail += 1
        if not bool(seg.get("is_closest_in_bin", True)):
            n_not_closest += 1
        proxy_h = float(seg.get("height_proxy_m", 0.0))
        pred_h = seg.get("predicted_height_m", float("nan"))
        if (
            proxy_h >= 3.0
            and isinstance(pred_h, float)
            and np.isfinite(pred_h)
            and (pred_h > proxy_h * 5.0 or pred_h < proxy_h * 0.2)
        ):
            n_implausible += 1
    return n_fov_fail, n_not_closest, n_implausible


def _render_seed_view_page(
    pdf,
    sv: SeedViewRegistration,
    osm_data: dict,
    buildings_by_id: dict | None = None,
) -> None:
    """Render one per-view PDF page.

    F-SKY7 layout (replaces F-SKY4 cyan-overlay + bottom legend table):
      - Top-left: Street View image with skyline-segment overlays.
      - Bottom-left: SegFormer building mask in its own panel (faint
        photo background + cyan mask) — direct side-by-side comparison
        with the segment panel above lets you spot "mask present but
        no segment" failures at a glance.
      - Right: minimap (full height) — taller than before since the
        bottom legend table was removed.

    Flag counts (F / B / P) that used to live in the legend table now
    appear in the figure title.
    """
    fig = plt.figure(figsize=(14, 8.5))

    # ── Title: pose + per-flag counts ─────────────────────────────────────
    aerial_tag = "  [AERIAL — height skipped]" if sv.is_aerial else ""
    if getattr(sv, "is_negative", False):
        aerial_tag += "  [NEGATIVE EXAMPLE — estimates excluded]"
    effective_heading = (sv.heading + sv.best_offset) % 360.0
    n_fov_fail, n_not_closest, n_implausible = _count_seg_flags(
        sv.matched_segments)
    n_total = len(sv.matched_segments)
    flags_str = (
        f"  flags={n_fov_fail}F/{n_not_closest}B/{n_implausible}P of {n_total}"
        if n_total else ""
    )
    # View-level cross-checks (set by _seed_multiview_registration):
    #   bearing_delta_deg per match → median + MAD across the view
    #   multi_building_candidate per match → count of wide/cluster segments
    deltas = [
        float(s["bearing_delta_deg"]) for s in sv.matched_segments
        if s.get("matched_projection") is not None
        and s.get("bearing_delta_deg") is not None
        and np.isfinite(s.get("bearing_delta_deg", float("nan")))
    ]
    heading_str = ""
    if len(deltas) >= 3:
        med = float(np.median(deltas))
        mad = float(np.median([abs(d - med) for d in deltas]))
        heading_str = f"  Δh̃={med:+.1f}°(MAD={mad:.0f}°)"
    n_wide = sum(
        1 for s in sv.matched_segments
        if s.get("matched_projection") is not None
        and s.get("multi_building_candidate")
    )
    wide_str = f"  multi-bldg={n_wide}" if n_wide else ""
    n_corrected = sum(
        1 for s in sv.matched_segments
        if s.get("match_corrected")
    )
    corr_str = f"  ✎{n_corrected}" if n_corrected else ""
    # F-CLEAN5: surface the F-SKY10 cross-view score in the per-view header.
    # Each matched segment's `match_diagnostics[0]` carries the per-candidate
    # scoring breakdown including `cv` when the cross-view scorer ran.
    # We aggregate as mean+min across matched segments — the mean gives a
    # whole-view "how well does the cross-view signal AGREE with this match
    # set" indicator; the min surfaces single suspect matches without
    # forcing the user to dig into per-segment metadata.
    cv_vals = []
    for _s in sv.matched_segments:
        if _s.get("matched_projection") is None:
            continue
        _d = (_s.get("match_diagnostics") or [{}])[0]
        if "cv" in _d:
            try:
                cv_vals.append(float(_d["cv"]))
            except (TypeError, ValueError):
                continue
    cv_str = (
        f"  cv̄={float(np.mean(cv_vals)):.2f}/min={float(np.min(cv_vals)):.2f}"
        if cv_vals else ""
    )
    fig.suptitle(
        f"Seed view — {sv.seed_name}  "
        f"effective_heading={effective_heading:.1f}°  "
        f"(api={sv.heading:.0f}° + offset={sv.best_offset:+.1f}°)  "
        f"reg_score={sv.registration_score:.2f}px  iou={sv.iou:.2f}  "
        f"segments={n_total}  "
        f"estimates={sv.estimates_count}"
        f"{flags_str}{heading_str}{wide_str}{corr_str}{cv_str}"
        f"{aerial_tag}",
        fontsize=11,
    )
    # Decoder for the F/B/P + new diagnostics shorthand in the title.
    if n_total and (
        n_fov_fail or n_not_closest or n_implausible
        or len(deltas) >= 3 or n_wide or n_corrected
    ):
        fig.text(
            0.5, 0.955,
            "F = matched bearing outside FOV   "
            "B = matched building not closest in column bin   "
            "P = predicted height implausible vs sqrt-area proxy   "
            "Δh̃ = median(true-bearing − effective-heading) across matches  "
            "(non-zero ⇒ heading bias)   "
            "multi-bldg = wide segment that spans multiple OSM polygons   "
            "✎N = N matches corrected by post-hoc rescue   "
            "cv̄/min = mean/min F-SKY10 cross-view score across matched segments",
            ha="center", va="top", fontsize=7, color="0.35",
        )

    # ── Crop the displayed image to the building band ────────────────────
    # Views looking across water otherwise devote 60-70% of the panel to
    # empty sea/sky. Segment x_left/x_right/top_y/base_y stay in original-
    # image coords so overlays still register against the crop.
    display_img = sv.image
    crop_y0 = crop_y1 = None
    if sv.band_y is not None:
        y0, y1 = sv.band_y
        h_img = sv.image.shape[0]
        y0 = max(0, min(h_img - 1, int(y0)))
        y1 = max(y0 + 30, min(h_img, int(y1) + 1))
        if y1 - y0 > 60:
            display_img = sv.image[y0:y1, :, ...]
            crop_y0, crop_y1 = y0, y1

    # ── Top-left: image + skyline segment overlays ────────────────────────
    ax_img = fig.add_axes([0.03, 0.56, 0.55, 0.34])
    ax_img.imshow(display_img)
    ax_img.axis("off")
    img_title = "Street View + skyline segments"
    if crop_y0 is not None:
        img_title += f"  (cropped to band y={crop_y0}..{crop_y1})"
    ax_img.set_title(img_title, fontsize=10)

    # ── Mid-left: SegFormer building mask on its own ─────────────────────
    ax_mask = fig.add_axes([0.03, 0.21, 0.55, 0.32])
    if sv.building_mask is not None:
        mask_disp = sv.building_mask
        if crop_y0 is not None:
            mask_disp = mask_disp[crop_y0:crop_y1, :]
        # Faint greyscale photo as background context so you can see what
        # the mask is covering, then the mask as a cyan layer on top.
        ax_mask.imshow(display_img, alpha=0.35)
        rgba = np.zeros((*mask_disp.shape, 4), dtype=np.float32)
        rgba[..., 1] = 0.85
        rgba[..., 2] = 1.0
        rgba[..., 3] = np.where(mask_disp, 0.65, 0.0)
        ax_mask.imshow(rgba)
        ax_mask.set_title(
            "SegFormer building mask (cyan = building class)",
            fontsize=10,
        )
    else:
        ax_mask.text(
            0.5, 0.5, "(no SegFormer mask available)",
            ha="center", va="center", transform=ax_mask.transAxes,
            fontsize=10, color="grey",
        )
        ax_mask.set_title("SegFormer building mask", fontsize=10)
    ax_mask.axis("off")

    # ── Bottom-left: column-coverage strip with peak markers ──────────────
    # The 1-D signal `detect_building_silhouettes` works on. Visible peaks
    # (kept = match; unmatched = no match) plotted as vertical bars so the
    # user can tell apart "silhouette detector missed the tower" (no bar
    # above a tall mask column) from "matcher rejected the peak" (bar
    # present but unmatched). Critical for diagnosing seed_5-style cases.
    ax_strip = fig.add_axes([0.03, 0.05, 0.55, 0.13])
    if sv.building_mask is not None:
        coverage = sv.building_mask.astype(np.float32).mean(axis=0)
        xs = np.arange(coverage.size)
        ax_strip.fill_between(xs, 0.0, coverage,
                              color=(0.20, 0.55, 0.75, 0.45), linewidth=0)
        ax_strip.plot(xs, coverage, color=(0.10, 0.30, 0.55, 0.9),
                      linewidth=0.7)
        for seg in sv.matched_segments:
            try:
                peak_x = int(seg.get(
                    "peak_x",
                    0.5 * (float(seg.get("x_left", 0))
                           + float(seg.get("x_right", 0))),
                ))
            except Exception:
                continue
            if peak_x < 0 or peak_x >= coverage.size:
                continue
            matched = seg.get("matched_projection") is not None
            color = (0.0, 0.65, 0.0, 0.95) if matched else (
                0.85, 0.40, 0.0, 0.95)
            ax_strip.axvline(peak_x, color=color, linewidth=1.0)
        ax_strip.set_xlim(0, max(1, coverage.size - 1))
        ax_strip.set_ylim(0, 1.0)
        ax_strip.set_xlabel("column x (px)", fontsize=8)
        ax_strip.set_ylabel("mask coverage", fontsize=8)
        ax_strip.tick_params(labelsize=7)
        ax_strip.grid(alpha=0.20)
        ax_strip.set_title(
            "Per-column building mask coverage  "
            "(green = matched peak, orange = unmatched peak)",
            fontsize=9,
        )
    else:
        ax_strip.axis("off")

    # ── Right: minimap, full page height ─────────────────────────────────
    ax_map = fig.add_axes([0.62, 0.05, 0.35, 0.85])
    # Use heading + best_offset for the minimap arrow because building
    # projections were computed with this effective heading during
    # registration.
    _draw_view_minimap(
        ax_map,
        sv.seed_lat,
        sv.seed_lon,
        sv.heading + sv.best_offset,
        sv.fov,
        osm_data,
        sv.matched_segments,
        buildings_by_id=buildings_by_id,
        image_width=sv.image.shape[1],
        pano_osm_iou=sv.pano_osm_iou,
        pano_osm_n_keypoints=sv.pano_osm_n_keypoints,
        pano_projected_coastline=sv.pano_projected_coastline,
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)


def _load_known_heights(
    region_name: str,
    buildings: list[BuildingRecord],
    match_radius_m: float = 60.0,
) -> list[dict]:
    """Load surveyed building heights from sites/<region>.json.

    Reads the ``known_heights_m`` dict (keyed by building name, values with
    lat/lon/height_m/floors) and matches each entry to the closest
    BuildingRecord within ``match_radius_m``.  Used for CTBUH-style
    ground-truth validation separate from crowd-sourced OSM tags.

    Returns a list of dicts with keys:
      name, ctbuh_m, floors, lat, lon, matched_id, matched_dist_m.
    """
    cfg = Path(__file__).resolve().parent / \
        "sites" / f"{region_name.lower()}.json"
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw = data.get("known_heights_m")
    if not isinstance(raw, dict):
        return []

    out: list[dict] = []
    for bname, info in raw.items():
        if bname.startswith("_"):
            continue
        try:
            klat = float(info["lat"])
            klon = float(info["lon"])
            ctbuh_m = float(info["height_m"])
            floors = int(info.get("floors", 0))
        except Exception:
            continue
        best_id: str | None = None
        best_dist = float("inf")
        for b in buildings:
            d = _distance_m(klat, klon, b.centroid_lat, b.centroid_lon)
            if d < best_dist:
                best_dist = d
                best_id = b.feature_id
        out.append({
            "name": bname,
            "ctbuh_m": ctbuh_m,
            "floors": floors,
            "lat": klat,
            "lon": klon,
            "matched_id": best_id if best_dist <= match_radius_m else None,
            "matched_dist_m": best_dist,
        })
    return out


def _render_pdf(
    out_pdf: Path,
    bbox: RegionBBox,
    osm_source: str,
    osm_data: dict,
    buildings_count: int,
    high_rise_count: int,
    screened: list[dict],
    seed_views: list[SeedViewRegistration],
    building_heights: list[dict],
    building_records: list[BuildingRecord],
    known_heights: list[dict] | None = None,
    pano_results: list["StitchedPanoResult"] | None = None,
    pano_only: bool = False,
) -> None:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        # Page 1: summary
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle(
            f"Skyline Region Report — {bbox.name}", fontsize=18, fontweight="bold")
        ax = fig.add_subplot(111)
        ax.axis("off")
        n_aerial_views = sum(1 for sv in seed_views if sv.is_aerial)
        lines = [
            f"Region: {bbox.name}",
            f"BBox: N {bbox.north:.5f}  S {bbox.south:.5f}  E {bbox.east:.5f}  W {bbox.west:.5f}",
            f"OSM source: {osm_source}",
            f"Buildings in dataset: {buildings_count}",
            f"High-rise candidates: {high_rise_count}",
            f"StreetView-screened points: {len(screened)}",
            f"Seed multiview registrations: {len(seed_views)}  (aerial/skipped: {n_aerial_views})",
            f"Extracted buildings from seed registrations: {len(building_heights)}",
            "",
            "Coverage screening rule:",
            "- good: skyline score >= 0.30",
            "- medium: 0.15 <= score < 0.30",
            "- weak: score < 0.15",
            "- rejected: failed sky-fraction or contour-range gate",
            "- aerial: elevated camera detected, height estimation skipped",
        ]
        top = [r for r in screened if r["status"] == "OK"][:8]
        lines.append("")
        lines.append("Top screened viewpoints:")
        for row in top:
            p: SkylinePoint = row["point"]
            lines.append(
                f"- {p.name} ({p.source})  lat={p.lat:.6f}, lon={p.lon:.6f}, heading={p.heading:.1f}  "
                f"score={row['screen_score']:.2f}  coverage={row['coverage']}"
            )
        ax.text(0.03, 0.96, "\n".join(lines), va="top",
                ha="left", family="monospace", fontsize=10)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Page 2: map screen
        fig, ax = plt.subplots(figsize=(11, 8.5))
        _draw_location_map(ax, bbox, screened, osm_data)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Point screenshots — consolidated into one (or a few) montage pages.
        # Previously rendered one full-page screenshot per screened location,
        # which inflated the PDF and added ~2 s/page of matplotlib draw time.
        # The same images appear in detail on the seed-view registration
        # pages later, so the screenshots are only useful as a quick at-a-
        # glance "which locations got chosen" overview.
        ok_rows = [r for r in screened
                   if r["status"] == "OK" and r["image"] is not None]
        if ok_rows:
            tiles_per_page = 6  # 2 rows × 3 cols
            for page_start in range(0, len(ok_rows), tiles_per_page):
                page_rows = ok_rows[page_start: page_start + tiles_per_page]
                fig = plt.figure(figsize=(11, 8.5))
                fig.suptitle(
                    f"Screened locations  "
                    f"({page_start + 1}–{page_start + len(page_rows)} of {len(ok_rows)})",
                    fontsize=14,
                )
                for tile_idx, row in enumerate(page_rows):
                    p: SkylinePoint = row["point"]
                    ax = fig.add_subplot(2, 3, tile_idx + 1)
                    ax.imshow(row["image"])
                    ax.axis("off")
                    ax.set_title(
                        f"{p.name}\n"
                        f"{p.source}  cov={row['coverage']}  "
                        f"score={row['screen_score']:.2f}\n"
                        f"hdg={p.heading:.0f}°  lat={p.lat:.4f}  lon={p.lon:.4f}",
                        fontsize=7,
                    )
                fig.tight_layout(rect=[0, 0, 1, 0.95])
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)

        # Seed registration pages: image with segments + matched-footprint mini-map
        # buildings_by_id lets the mini-map draw the actual matched footprint
        # polygons in the segment colours (rather than just bearing rays).
        # Skipped entirely under `pano_only_pdf` — these are the dominant
        # contributor to PDF file size (~26 pages × ~1 MB each on Cartagena),
        # and the per-seed stitched pano page below carries the same info
        # at lower fidelity for compact-orientation reviews.
        buildings_by_id = {b.feature_id: b for b in building_records}
        if not pano_only:
            for sv in seed_views:
                _render_seed_view_page(
                    pdf, sv, osm_data, buildings_by_id=buildings_by_id)

        # Stitched-pano comparison pages — one per seed that produced a
        # stitched result. Lets you compare the 360° pano-level segmentation
        # against the per-view results on the preceding pages. Skip seeds
        # whose stitched pano detected no segments and matched nothing:
        # the page is pure noise (an empty pano image + zero-rows table)
        # and just inflates the PDF page count.
        if pano_results:
            for pr in pano_results:
                if pr.n_segments == 0 and pr.n_matched == 0:
                    continue
                _render_stitched_pano_page(
                    pdf, pr, osm_data, buildings_by_id=buildings_by_id,
                    seed_views=seed_views)

        # Extracted heights summary page — text-only per-building dump.
        # Skipped under `pano_only`: the same data lives in the HTML
        # report (html_report.py) where tables are first-class; the PDF
        # is the orientation / visual artefact, not the data store.
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle("Seed-Derived Building Heights",
                     fontsize=16, fontweight="bold")
        ax = fig.add_subplot(111)
        ax.axis("off")
        cross_seed = [r for r in building_heights if r.get("n_seeds", 1) >= 2]
        single_seed = [r for r in building_heights if r.get("n_seeds", 1) < 2]

        # Cross-seed disagreement: the same OSM building seen from multiple
        # seeds should produce similar heights. Large spread = matcher is
        # picking different physical structures in different seeds (or one
        # seed's heading is off). This is our headline "are matches even
        # pointing at the same building?" sanity metric.
        cs_disagreements = np.asarray(
            [r.get("seed_disagreement_m", 0.0) for r in cross_seed], dtype=np.float32)
        cs_lines = [
            "Cross-seed agreement metric (lower is better — same building seen from different",
            "seeds should produce similar heights):",
        ]
        if cs_disagreements.size:
            cs_lines.extend([
                f"  n cross-seed buildings : {cs_disagreements.size}",
                f"  median disagreement    : {float(np.median(cs_disagreements)):6.1f} m",
                f"  p75 disagreement       : {float(np.percentile(cs_disagreements, 75)):6.1f} m",
                f"  p90 disagreement       : {float(np.percentile(cs_disagreements, 90)):6.1f} m",
                f"  max disagreement       : {float(np.max(cs_disagreements)):6.1f} m",
            ])
        else:
            cs_lines.append("  (no buildings seen from >= 2 seeds yet)")

        lines = [
            f"Buildings with aggregated estimates: {len(building_heights)}",
            f"  - cross-seed (>=2 distinct seeds): {len(cross_seed)}",
            f"  - single-seed only: {len(single_seed)}",
            "",
            *cs_lines,
            "",
            "Top cross-seed extracted heights (sorted by lowest disagreement first):",
        ]

        # Sort cross-seed buildings by disagreement (best matches first)
        cross_seed_sorted = sorted(
            cross_seed,
            key=lambda r: (r.get("seed_disagreement_m",
                           0.0), -r.get("n_seeds", 0)),
        )
        for row in cross_seed_sorted[:30]:
            per_seed = row.get("per_seed_median_m", {})
            per_seed_str = ", ".join(
                f"{s}:{h:.1f}" for s, h in sorted(per_seed.items())
            )
            lines.append(
                f"- {row['name']}: med={row['median_height_m']:5.1f}m "
                f"weighted={row['weighted_height_m']:5.1f}m "
                f"n_seeds={row['n_seeds']} n_views={row['n_views']:2d} "
                f"disagree={row.get('seed_disagreement_m', 0.0):5.1f}m "
                f"[{per_seed_str}]"
            )
        ax.text(0.03, 0.96, "\n".join(lines), va="top",
                ha="left", family="monospace", fontsize=8)
        if not pano_only:
            pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Residuals page: predicted vs OSM-tagged height
        tagged_by_id = {
            b.feature_id: b for b in building_records if b.height_tag_m is not None
        }
        pairs: list[tuple[str, float, float, int, int, float]] = []
        for row in building_heights:
            tag = tagged_by_id.get(row["feature_id"])
            if tag is None:
                continue
            pairs.append(
                (
                    row["name"],
                    float(tag.height_tag_m or 0.0),
                    float(row["weighted_height_m"]),
                    int(row.get("n_seeds", 0)),
                    int(row.get("n_views", 0)),
                    float(row.get("mean_confidence", 0.0)),
                )
            )

        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle("Validation: Predicted vs OSM-tagged Heights",
                     fontsize=16, fontweight="bold")
        if pairs:
            tagged_h = np.array([p[1] for p in pairs], dtype=np.float32)
            pred_h = np.array([p[2] for p in pairs], dtype=np.float32)
            n_seeds_arr = np.array([p[3] for p in pairs], dtype=np.int32)
            resid = pred_h - tagged_h
            mae = float(np.mean(np.abs(resid)))
            rmse = float(np.sqrt(np.mean(resid * resid)))
            bias = float(np.median(resid))

            ax_scatter = fig.add_axes([0.08, 0.42, 0.55, 0.45])
            colors = np.where(n_seeds_arr >= 2, "tab:blue", "tab:orange")
            ax_scatter.scatter(tagged_h, pred_h, c=colors, alpha=0.7, s=24)
            lim = float(max(tagged_h.max(), pred_h.max()) * 1.05)
            ax_scatter.plot([0, lim], [0, lim], "k--",
                            linewidth=0.8, alpha=0.5)
            ax_scatter.set_xlabel("OSM-tagged height (m)")
            ax_scatter.set_ylabel("Predicted weighted height (m)")
            ax_scatter.set_xlim(0, lim)
            ax_scatter.set_ylim(0, lim)
            ax_scatter.set_title(
                f"n={len(pairs)} tagged buildings (blue=cross-seed, orange=single-seed)")
            ax_scatter.grid(alpha=0.25)

            ax_hist = fig.add_axes([0.70, 0.42, 0.25, 0.45])
            ax_hist.hist(resid, bins=20, color="tab:gray", edgecolor="black")
            ax_hist.axvline(0.0, color="black", linewidth=0.8)
            ax_hist.set_title("Residual (pred − tag)")
            ax_hist.set_xlabel("m")

            cs_mask = n_seeds_arr >= 2
            ss_mask = ~cs_mask

            def _stats(mask):
                if not mask.any():
                    return None
                r = resid[mask]
                return (
                    float(np.mean(np.abs(r))),
                    float(np.sqrt(np.mean(r * r))),
                    float(np.median(r)),
                    int(mask.sum()),
                )

            cs = _stats(cs_mask)
            ss = _stats(ss_mask)
            stats_lines = [
                f"All           n={len(pairs):3d}  MAE={mae:6.2f}m  RMSE={rmse:6.2f}m  bias={bias:+6.2f}m",
            ]
            if cs is not None:
                stats_lines.append(
                    f"Cross-seed    n={cs[3]:3d}  MAE={cs[0]:6.2f}m  RMSE={cs[1]:6.2f}m  bias={cs[2]:+6.2f}m   <-- trust this"
                )
            if ss is not None:
                stats_lines.append(
                    f"Single-seed   n={ss[3]:3d}  MAE={ss[0]:6.2f}m  RMSE={ss[1]:6.2f}m  bias={ss[2]:+6.2f}m   (unreliable)"
                )
            if not pano_only:
                # Per-building "worst residuals" listing — table content
                # belongs in the HTML report, not the orientation PDF.
                stats_lines += ["", "Worst residuals:"]
                order = np.argsort(-np.abs(resid))[:10]
                for i in order:
                    name, t, p, ns, nv, _ = pairs[int(i)]
                    stats_lines.append(
                        f"  {name[:24]:<24}  tag={t:5.1f}  pred={p:5.1f}  Δ={p-t:+5.1f}  seeds={ns}"
                    )
            fig.text(
                0.08,
                0.34,
                "\n".join(stats_lines),
                family="monospace",
                fontsize=9,
                va="top",
            )
        else:
            fig.text(
                0.5,
                0.5,
                "No buildings with both OSM-tagged height and a prediction.",
                ha="center",
                va="center",
                fontsize=12,
            )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Diagnostic: matched-segment width histogram ──────────────────────
        # A bimodal distribution of matched-segment widths is the headline
        # signal that SegFormer is over-merging adjacent towers into one
        # silhouette (one wide segment that wins a match instead of N
        # narrow segments matching N OSM polygons). The per-seed bar to the
        # right shows matched count vs OSM markers projected into the FOV
        # cone — a seed leaving many markers unmatched is a candidate for
        # an `anchor_offsets_deg` override or extra Voronoi splitting.
        widths_px: list[float] = []
        ms_widths_px: list[float] = []
        per_seed_matched: dict[str, int] = {}
        per_seed_in_cone: dict[str, int] = {}
        for sv in seed_views:
            if getattr(sv, "is_negative", False) or sv.is_aerial:
                continue
            seed = sv.seed_name
            per_seed_matched.setdefault(seed, 0)
            per_seed_in_cone.setdefault(seed, 0)
            for seg in sv.matched_segments:
                m = seg.get("matched_projection")
                if m is None:
                    continue
                try:
                    w = float(seg.get("x_right", 0.0)) - float(seg.get("x_left", 0.0))
                except Exception:
                    w = 0.0
                if w > 0:
                    widths_px.append(w)
                    if str(m.get("feature_id", "")).startswith("ms_"):
                        ms_widths_px.append(w)
                per_seed_matched[seed] += 1
            # Markers projected into the FOV cone — match_diagnostics holds
            # every candidate the matcher considered; that's the OSM/MS pool
            # for this view. Use a set of feature_ids to avoid double-counting
            # the same building across segments.
            in_cone_ids: set[str] = set()
            for seg in sv.matched_segments:
                for d in seg.get("match_diagnostics", []) or []:
                    fid = d.get("feature_id")
                    if fid:
                        in_cone_ids.add(str(fid))
                m = seg.get("matched_projection")
                if m and m.get("feature_id"):
                    in_cone_ids.add(str(m["feature_id"]))
            per_seed_in_cone[seed] = max(per_seed_in_cone[seed], len(in_cone_ids))

        if widths_px:
            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(
                "Matcher Coverage Diagnostic — segment widths + per-seed yield",
                fontsize=14, fontweight="bold",
            )

            ax_h = fig.add_axes([0.07, 0.12, 0.55, 0.72])
            arr = np.asarray(widths_px, dtype=np.float32)
            ax_h.hist(arr, bins=24, color=(0.45, 0.55, 0.75, 0.85),
                      edgecolor="black", label="OSM-matched")
            if ms_widths_px:
                ax_h.hist(np.asarray(ms_widths_px, dtype=np.float32),
                          bins=24, color=(0.85, 0.65, 0.20, 0.6),
                          edgecolor="black", label="ms_buildings-matched")
            p50 = float(np.median(arr))
            p75 = float(np.percentile(arr, 75))
            over_thr = 2.0 * p75
            ax_h.axvline(p50, color="green", linestyle="--",
                         linewidth=1.0, label=f"median={p50:.0f}px")
            ax_h.axvline(p75, color="orange", linestyle=":",
                         linewidth=1.0, label=f"p75={p75:.0f}px")
            ax_h.axvline(over_thr, color="red", linestyle="--",
                         linewidth=1.0, label=f"over-merge ≥{over_thr:.0f}px")
            ax_h.set_xlabel("Matched segment width (px)")
            ax_h.set_ylabel("Count")
            ax_h.set_title(
                f"n={arr.size} matched segments  "
                f"({int((arr >= over_thr).sum())} suspected over-merges)",
                fontsize=10,
            )
            ax_h.legend(fontsize=8, loc="upper right")
            ax_h.grid(alpha=0.25)

            ax_b = fig.add_axes([0.68, 0.12, 0.28, 0.72])
            seeds = sorted(per_seed_matched.keys())
            x = np.arange(len(seeds))
            matched_vals = [per_seed_matched[s] for s in seeds]
            cone_vals = [per_seed_in_cone[s] for s in seeds]
            bar_w = 0.4
            ax_b.bar(x - bar_w / 2, cone_vals, width=bar_w,
                     color=(0.65, 0.65, 0.65, 0.85),
                     edgecolor="black", label="in FOV cone")
            ax_b.bar(x + bar_w / 2, matched_vals, width=bar_w,
                     color=(0.30, 0.50, 0.75, 0.95),
                     edgecolor="black", label="matched")
            ax_b.set_xticks(x)
            ax_b.set_xticklabels(seeds, rotation=45, ha="right", fontsize=8)
            ax_b.set_ylabel("Count")
            ax_b.set_title("Per-seed matcher yield", fontsize=10)
            ax_b.legend(fontsize=8)
            ax_b.grid(axis="y", alpha=0.25)

            fig.text(
                0.07, 0.04,
                "Read: a bimodal width histogram = SegFormer over-merging adjacent towers "
                "(one wide segment that wins one match instead of N narrow segments matching "
                "N buildings). A low matched/in-cone ratio for a seed flags that seed as a "
                "candidate for an anchor_offsets_deg override or extra Voronoi splitting.",
                fontsize=8, color="0.30", wrap=True,
            )
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

        # ── CTBUH / ground-truth validation page ─────────────────────────────
        # Only rendered when the sites/<region>.json contains known_heights_m.
        if known_heights:
            brec_by_id = {b.feature_id: b for b in building_records}
            pred_by_id = {
                row["feature_id"]: float(row["weighted_height_m"])
                for row in building_heights
            }

            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(
                "Ground-Truth Validation — Surveyed Building Heights",
                fontsize=16, fontweight="bold",
            )

            matched = [
                kh for kh in known_heights if kh["matched_id"] is not None]
            unmatched = [
                kh for kh in known_heights if kh["matched_id"] is None]

            hdr = (
                f"{'Building':<32} {'Survey m':>9} {'Floors':>7} "
                f"{'OSM tag':>8} {'Pred m':>8} {chr(0x394)+' m':>8} {'Dist m':>8}"
            )
            sep = "-" * 86
            lines = [
                f"Known heights loaded: {len(known_heights)}  "
                f"(OSM-matched: {len(matched)}, unmatched: {len(unmatched)})",
                "",
                hdr,
                sep,
            ]
            # (ctbuh_m, pred_m, name)
            pairs: list[tuple[float, float, str]] = []
            for kh in known_heights:
                fid = kh["matched_id"]
                ctbuh_m = kh["ctbuh_m"]
                floors_s = str(kh["floors"]) if kh["floors"] else "-"
                dist_m = kh["matched_dist_m"]
                osm_h_s = "-"
                pred_s = "-"
                delta_s = "-"
                if fid is not None:
                    brec = brec_by_id.get(fid)
                    if brec and brec.height_tag_m is not None:
                        osm_h_s = f"{brec.height_tag_m:.1f}"
                    if fid in pred_by_id:
                        pred_m = pred_by_id[fid]
                        pred_s = f"{pred_m:.1f}"
                        delta_s = f"{pred_m - ctbuh_m:+.1f}"
                        pairs.append((ctbuh_m, pred_m, kh["name"]))
                no_match = "  ⚠ no OSM footprint" if fid is None else ""
                lines.append(
                    f"{kh['name'][:32]:<32} {ctbuh_m:>9.1f} {floors_s:>7} "
                    f"{osm_h_s:>8} {pred_s:>8} {delta_s:>8} {dist_m:>8.0f}{no_match}"
                )

            if not pano_only:
                # Per-building survey-vs-pred table — dropped under
                # pano_only since the same info is rendered as a sortable
                # HTML table by html_report.py.
                ax_t = fig.add_axes([0.03, 0.40, 0.94, 0.52])
                ax_t.axis("off")
                ax_t.text(
                    0.0, 1.0, "\n".join(lines),
                    va="top", ha="left", family="monospace", fontsize=8.5,
                )

            if len(pairs) >= 2:
                ctbuh_arr = np.array([p[0] for p in pairs])
                pred_arr = np.array([p[1] for p in pairs])
                mae = float(np.mean(np.abs(pred_arr - ctbuh_arr)))
                rmse = float(np.sqrt(np.mean((pred_arr - ctbuh_arr) ** 2)))
                bias = float(np.median(pred_arr - ctbuh_arr))

                ax_s = fig.add_axes([0.06, 0.05, 0.46, 0.32])
                ax_s.scatter(ctbuh_arr, pred_arr, c="tab:blue",
                             s=60, alpha=0.85, zorder=3)
                lim = float(max(ctbuh_arr.max(), pred_arr.max()) * 1.10)
                ax_s.plot([0, lim], [0, lim], "k--", linewidth=0.8, alpha=0.5)
                for (ct, pr, bname) in pairs:
                    ax_s.annotate(
                        bname[:14], (ct, pr), fontsize=7,
                        xytext=(3, 3), textcoords="offset points",
                    )
                ax_s.set_xlabel("Surveyed height (m)")
                ax_s.set_ylabel("Predicted height (m)")
                ax_s.set_xlim(0, lim)
                ax_s.set_ylim(0, lim)
                ax_s.set_title(f"Surveyed vs Predicted (n={len(pairs)})")
                ax_s.grid(alpha=0.25)

                ax_m = fig.add_axes([0.58, 0.05, 0.38, 0.32])
                ax_m.axis("off")
                ax_m.text(
                    0.02, 0.95,
                    f"n = {len(pairs)}\n"
                    f"MAE  = {mae:.1f} m\n"
                    f"RMSE = {rmse:.1f} m\n"
                    f"bias = {bias:+.1f} m\n\n"
                    "Source: surveyed / CTBUH verified heights\n"
                    "Unmatched = building not found in OSM\n"
                    "Pred = pipeline weighted estimate",
                    va="top", ha="left", family="monospace", fontsize=10,
                    transform=ax_m.transAxes,
                )
            else:
                fig.text(
                    0.5, 0.22,
                    "No predictions yet for surveyed buildings.\n"
                    "Run with seed URLs from good vantage points to populate.",
                    ha="center", va="center", fontsize=12,
                )

            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    # Post-process: stamp "Page X / N" on every page. Done after PdfPages
    # closes the file so the total count is exact. PyMuPDF is the only
    # cleanly-available editor in this stack; falls back silently if not
    # installed.
    try:
        import fitz  # noqa: PLC0415
    except Exception:
        return
    try:
        doc = fitz.open(out_pdf)
        total = doc.page_count
        for i, page in enumerate(doc, start=1):
            rect = page.rect
            text = f"Page {i} / {total}"
            # Bottom-right corner, small monospace, with a thin white
            # background box so it's legible over any underlying plot.
            page.insert_text(
                fitz.Point(rect.width - 80, rect.height - 12),
                text,
                fontsize=8, fontname="helv",
                color=(0.25, 0.25, 0.25),
                render_mode=0,
            )
        doc.saveIncr()
        doc.close()
    except Exception:
        # Page numbering is cosmetic; never let it break the report.
        pass


def run_region_pdf_report(
    region_name: str,
    output_pdf: Path,
    seed_urls: list[str] | None = None,
    explicit_api_key: str | None = None,
    *,
    trace=None,
    skip_pdf: bool = False,
) -> dict:
    api_key = _resolve_api_key(explicit_api_key)
    bbox = _load_region_bbox(region_name)
    osm_data, osm_source = _load_osm_for_region(bbox)

    buildings = osm_data.get("buildings", {}).get("features") or []
    high_rises = _extract_high_rises(osm_data)
    building_records = _osm_to_building_records(osm_data)
    # F-SKY8: optionally enrich with Microsoft Buildings (satellite-derived)
    # polygons. Gated on per-site flag because the first run downloads
    # ~10–50 MB per quadkey tile; opt-in keeps default behaviour
    # unchanged. Runs BEFORE terrain attachment so satellite-sourced
    # records get DEM elevations along with OSM ones.
    if _load_site_use_satellite_footprints(region_name):
        from .satellite_footprints import (
            fetch_microsoft_buildings_for_bbox,
            merge_satellite_into_osm,
        )
        sat_polys = fetch_microsoft_buildings_for_bbox(
            (bbox.south, bbox.west, bbox.north, bbox.east))
        building_records = merge_satellite_into_osm(
            building_records, sat_polys)
    building_records = _attach_building_terrain(building_records)

    # F-SKY10: fetch the per-region satellite image once and stash it for
    # the per-view matcher closure. Failures degrade gracefully — the
    # matcher just runs without the cross-view rerank.
    cross_view_state: dict | None = None
    if _load_site_use_cross_view_scoring(region_name):
        try:
            from .satellite_image import fetch_region_satellite
            sat_image, sat_project, sat_meta = fetch_region_satellite(
                (bbox.south, bbox.west, bbox.north, bbox.east),
                target_m_per_px=1.0,
            )
            print(
                f"[cross_view] satellite image loaded: zoom={sat_meta['zoom']} "
                f"shape={sat_meta['shape']} tiles={sat_meta['tiles_loaded']}/{sat_meta['tiles_total']}"
            )
            cross_view_state = {
                "sat_image": sat_image,
                "sat_project": sat_project,
                "sat_meta": sat_meta,
            }
        except Exception as e:
            print(f"[cross_view] satellite image fetch failed: {e}")
            cross_view_state = None

    # F-SKY11.1 Path B: optionally precompute coastline keypoints once
    # per region. The per-seed pano recovery sweep uses these to seed the
    # joint anchor optimizer. Reuses cross_view_state's satellite image
    # when present to avoid a duplicate fetch.
    pano_recovery_state: dict | None = None
    if _load_site_use_pano_coastline_recovery(region_name):
        try:
            from .satellite_image import fetch_region_satellite
            from .coastline_registration import (
                detect_sat_water_mask, detect_coastline_keypoints,
            )
            if cross_view_state is not None:
                sat_image = cross_view_state["sat_image"]
                sat_project = cross_view_state["sat_project"]
            else:
                sat_image, sat_project, _meta = fetch_region_satellite(
                    (bbox.south, bbox.west, bbox.north, bbox.east),
                    target_m_per_px=2.0,
                )
            sat_water = detect_sat_water_mask(sat_image)
            water_frac = float(sat_water.mean())
            if water_frac < 0.02:
                print(f"[pano_recovery] region has <2% water "
                      f"({water_frac:.1%}) — coastline recovery skipped")
            else:
                # Per-seed keypoints get computed inside the seed loop
                # since each seed has its own lat/lon centre. Stash the
                # raw sat-water + project closure here.
                # F-SKY13 Phase B: extract OSM coastline + water polygons
                # once per region. The seed loop doesn't have osm_data in
                # scope, so we stash the extracted features here. Each
                # seed clips to its own 1 km window inside the loop.
                try:
                    from .osm_water import (  # noqa: PLC0415
                        extract_coastline_features,
                        extract_water_features,
                    )
                    osm_coastline_features = extract_coastline_features(osm_data)
                    osm_water_features = extract_water_features(osm_data)
                except Exception as _e_osm_extract:
                    print(f"[pano_recovery] OSM extraction failed: {_e_osm_extract}")
                    osm_coastline_features = []
                    osm_water_features = []
                pano_recovery_state = {
                    "sat_water": sat_water,
                    "sat_project": sat_project,
                    "water_frac": water_frac,
                    "osm_coastline_features": osm_coastline_features,
                    "osm_water_features": osm_water_features,
                    # Optional sub-flag: when True, a sharp recovery
                    # can REPLACE the joint-anchor coarse sweep for
                    # seeds with no manual override. Default False —
                    # the feature ships as a measurement tool only,
                    # since on Cartagena the recovery is dead-on for
                    # some seeds and wildly wrong for others with no
                    # automatic way to tell them apart.
                    "drive_anchor": bool(
                        _load_site_drive_pano_recovery_anchor(region_name)),
                }
                print(f"[pano_recovery] region satellite water "
                      f"{water_frac:.1%} — keypoints will be computed "
                      "per-seed inside _seed_multiview_registration")
        except Exception as e:
            print(f"[pano_recovery] region precomputation failed: {e}")
            pano_recovery_state = None

    merged_seed_urls = _load_site_seed_urls(region_name)
    for extra in seed_urls or []:
        extra_text = str(extra).strip()
        if extra_text and extra_text not in merged_seed_urls:
            merged_seed_urls.append(extra_text)

    seeds: list[SkylinePoint] = []
    for idx, url in enumerate(merged_seed_urls):
        parsed = _parse_streetview_url(url)
        if parsed is None:
            continue
        lat, lon, heading, fov, pitch, pano_id = parsed
        seeds.append(
            SkylinePoint(
                name=f"seed_{idx+1}",
                lat=lat,
                lon=lon,
                heading=heading,
                source="seed",
                score=1.0,
                fov=fov,
                pitch=pitch,
                pano_id=pano_id,
            )
        )

    # Generate geometry-driven auto-proposals from OSM tall-building cluster.
    # These are screened via Street View but NOT fed into multiview registration
    # unless the user explicitly promotes them to seed_urls in the sites JSON.
    auto_points = _propose_standoff_locations(bbox, high_rises, osm_data)

    # If no seeds were provided, use the top 3 auto-proposals as provisional
    # seeds so that cities without a sites/<region>.json still run end-to-end.
    if not seeds:
        provisional = auto_points[:3]
        seeds = [
            SkylinePoint(
                p.name, p.lat, p.lon, p.heading, "seed", 1.0, p.fov, p.pitch
            )
            for p in provisional
        ]
        # Remove from auto_points so they don't appear twice in screened.
        auto_points = auto_points[3:]

    # Screen seeds + auto-proposals together so the PDF map shows all candidates.
    all_points = seeds + auto_points
    screened = _screen_locations(all_points, api_key)
    anchor_overrides = _load_site_anchor_overrides(region_name)
    negative_seeds = _load_site_negative_seeds(region_name)
    max_plausible_height_m = _load_site_max_plausible_height_m(region_name)
    if anchor_overrides:
        print(f"[anchor_overrides] {anchor_overrides}")
    if negative_seeds:
        print(f"[negative_seeds] {sorted(negative_seeds)}")
    print(f"[max_plausible_height_m] {max_plausible_height_m:.0f}")
    seed_views, building_heights, pano_results = _seed_multiview_registration(
        seeds, building_records, api_key,
        anchor_overrides=anchor_overrides,
        negative_seeds=negative_seeds,
        trace=trace,
        max_plausible_height_m=max_plausible_height_m,
        cross_view_state=cross_view_state,
        pano_recovery_state=pano_recovery_state,
    )

    # Load surveyed ground-truth heights from sites/<region>.json if present.
    known_heights = _load_known_heights(region_name, building_records)

    if not skip_pdf:
        _render_pdf(
            output_pdf,
            bbox,
            osm_source,
            osm_data,
            buildings_count=len(buildings),
            high_rise_count=len(high_rises),
            screened=screened,
            seed_views=seed_views,
            building_heights=building_heights,
            building_records=building_records,
            known_heights=known_heights or None,
            pano_results=pano_results,
            pano_only=_load_site_pano_only_pdf(region_name),
        )

    # F-SKY15: parallel HTML diagnostic report. Default ON because the
    # cost is small (one PNG per seed via the existing minimap renderer);
    # set SKYLINE_CV_HTML_REPORT=0 to disable. Failures are swallowed
    # rather than allowed to break the PDF path — HTML is a diagnostic
    # tool, not the canonical output.
    if os.environ.get("SKYLINE_CV_HTML_REPORT", "1").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        try:
            from .html_report import write_region_report  # noqa: PLC0415
            html_out_dir = output_pdf.parent / output_pdf.stem
            write_region_report(
                html_out_dir,
                region_name=bbox.name,
                seed_views=seed_views,
                osm_data=osm_data,
                buildings_by_id={b.feature_id: b for b in building_records},
                building_heights=building_heights,
            )
        except Exception as _html_e:
            print(f"[F-SKY15] HTML report failed: {_html_e}")

    good = len([r for r in screened if r["coverage"] == "good"])
    medium = len([r for r in screened if r["coverage"] == "medium"])
    weak = len([r for r in screened if r["coverage"] == "weak"])
    return {
        "region": bbox.name,
        "output_pdf": str(output_pdf),
        "osm_source": osm_source,
        "seed_urls_used": len(merged_seed_urls),
        "auto_proposals": len(auto_points),
        "buildings": len(buildings),
        "high_rises": len(high_rises),
        "building_records": len(building_records),
        "locations_screened": len(screened),
        "seed_registration_views": len(seed_views),
        "seed_extracted_buildings": len(building_heights),
        "known_heights_loaded": len(known_heights),
        "coverage": {"good": good, "medium": medium, "weak": weak},
    }
