"""city2stl.skyline.region_data - region/config/OSM data loading for skyline.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). Pure data-acquisition
helpers: region bbox from the strm2stl SQLite table, OSM fetch + BuildingRecord
construction, the water filter, DEM terrain attach, and the sites/<region>.json
config readers. No Street View I/O, no rendering. region_pdf re-imports these.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import requests
from shapely.geometry import shape

from app.server.core.cache import osm_cache_key, read_osm_cache, write_osm_cache
from app.server.core.db import get_db, init_db
from city2stl.fetch import fetch_osm_data

from .pipeline import BuildingRecord, _building_height_from_tags, _polygon_area_m2
from .region_types import RegionBBox


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
    def _ensure_green(data: dict, key: str) -> None:
        # F-SKY18: vegetation landmarks need OSM green polygons (parks/grass/
        # forest). The layer was added after the OSM cache format, so cached
        # data may lack it — fetch green-only once and merge + rewrite cache.
        if "green" in data:
            return
        try:
            extra = fetch_osm_data(
                bbox.north, bbox.south, bbox.east, bbox.west, ["green"])
            data["green"] = extra.get("green", {"type": "FeatureCollection", "features": []})
            write_osm_cache(key, data)
        except Exception as _e:
            print(f"[osm_cache] green supplemental fetch failed (non-fatal): {_e}")
            data["green"] = {"type": "FeatureCollection", "features": []}

    for tol, min_area in key_params:
        key = osm_cache_key(bbox.north, bbox.south,
                            bbox.east, bbox.west, tol, min_area)
        cached = read_osm_cache(key)
        if cached and (cached.get("buildings", {}).get("features") or []):
            _ensure_green(cached, key)
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
    # Persist under the (0.5, 5.0) key — matches the first key_params entry
    # the reader probes — so the next run hits the cache instead of re-querying
    # Overpass (~56 s on Cartagena). Previously this was never written, so
    # every run was a live fetch.
    try:
        write_osm_cache(
            osm_cache_key(bbox.north, bbox.south, bbox.east, bbox.west, 0.5, 5.0),
            fetched,
        )
    except Exception as _e:
        print(f"[osm_cache] write failed (non-fatal): {_e}")
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

def _drop_buildings_in_water(
    records: list["BuildingRecord"],
    osm_data: dict,
) -> list["BuildingRecord"]:
    """Filter out buildings whose centroid lies inside an OSM water polygon.

    Catches the failure mode where the OSM building layer (or, more often,
    the Microsoft satellite-derived footprints from F-SKY8) places
    polygons in the middle of bays, marinas, or open water. Those would
    otherwise project into seed views as "buildings", and the matcher
    has to be told they're not real candidates. Doing this once at
    record-construction time removes the noise everywhere downstream
    (projection, anchor sweep, registration, matching, height aggregation).

    No-op when ``osm_data`` has no water polygons (inland regions). Uses
    shapely's prepared-geometry contains test, fast for the ~30 polygon
    counts in a city OSM dump.
    """
    try:
        from .osm_water import extract_water_features  # noqa: PLC0415
        from shapely.geometry import shape, Point  # noqa: PLC0415
        from shapely.prepared import prep  # noqa: PLC0415
    except Exception:
        return records
    water_feats = extract_water_features(osm_data)
    if not water_feats:
        return records
    polys = []
    for feat in water_feats:
        try:
            poly = shape(feat.get("geometry") or {})
            if poly.is_empty:
                continue
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            polys.append(prep(poly))
        except Exception:
            continue
    if not polys:
        return records
    # Two-pass filter:
    #   1. Centroid inside any water polygon  → drop.
    #   2. Polygon overlap with water > 40 %  → drop (e.g. a footprint
    #      whose centroid lands on the shore but whose body extends out
    #      into the bay; Microsoft satellite footprints generate these
    #      around piers and slipways).
    # We need an unprepared geometry for area-intersection, but the
    # prepared geometry is much faster for the centroid contains call —
    # so keep both.
    raw_polys: list = []
    for feat in water_feats:
        try:
            poly = shape(feat.get("geometry") or {})
            if poly.is_empty:
                continue
            if not poly.is_valid:
                poly = poly.buffer(0)
            if poly.is_empty:
                continue
            raw_polys.append(poly)
        except Exception:
            continue
    kept: list["BuildingRecord"] = []
    dropped_centroid = 0
    dropped_overlap = 0
    for b in records:
        pt = Point(b.centroid_lon, b.centroid_lat)
        if any(pp.contains(pt) for pp in polys):
            dropped_centroid += 1
            continue
        b_geom = getattr(b, "geometry", None)
        if b_geom is not None and not b_geom.is_empty:
            try:
                b_area = b_geom.area
                if b_area > 0:
                    overlap_area = 0.0
                    for wp in raw_polys:
                        if b_geom.intersects(wp):
                            overlap_area += b_geom.intersection(wp).area
                            if overlap_area / b_area > 0.15:
                                break
                    if overlap_area / b_area > 0.15:
                        dropped_overlap += 1
                        continue
            except Exception:
                pass
        kept.append(b)
    if dropped_centroid or dropped_overlap:
        print(f"[water_filter] dropped {dropped_centroid} centroid-in-water "
              f"+ {dropped_overlap} polygon-mostly-in-water building(s) "
              f"of {len(records)}")
    return kept

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
    """Seed names listed in ``negative_seeds`` are known-bad camera positions
    (gas stations, parking lots, under-bridge views). Their pano frames are
    captured and kept as labelled ``is_negative`` examples (a curated
    bad-skyline set for future negative-mining), but ALL analysis is skipped
    — no registration, matching, or height estimation. See
    ``_negative_seed_views``.
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

def _load_site_render_pdf(region_name: str) -> bool:
    """Per-region opt-in for the PDF report. Default False — the HTML report
    is the canonical diagnostic output (it carries everything the PDF does
    plus side-by-side views, timing, and grep-able fields). Set
    ``render_pdf: true`` only when you specifically want the single-file PDF
    artefact for sharing/archival.
    """
    return bool(_read_site_config(region_name).get("render_pdf", False))

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
