"""Microsoft Global ML Building Footprints fetcher + OSM merger (F-SKY8).

The strm2stl pipeline relies on OSM building polygons as the structural
anchor for the skyline-CV matcher. In sparse-OSM regions (Cartagena's
Bocagrande waterfront is the canonical example) entire rows of visible
towers have no OSM polygon — the matcher sees them in the SegFormer
mask but has nothing to assign. Microsoft's Global ML Building Footprints
(open data, ODbL) is derived from Bing aerial imagery and covers those
buildings.

Two-step flow:
  1. Fetch the dataset-links CSV (cached) to find which quadkey-9 tile(s)
     overlap a region's bbox and where to download them from.
  2. Fetch those tiles (cached, ~10-50 MB each), parse the GeoJSONL
     features, and filter to the bbox.

Then the merge step turns the surviving polygons into
``BuildingRecord`` entries and de-dups against existing OSM polygons
by IoU. OSM wins ties (it has height tags and stable feature IDs).

Cache layout (``runs/satellite_footprints_cache/``):
  dataset-links.csv          — index of all available tiles, ~7 MB
  qk_<quadkey>.geojsonl      — decompressed tile content, ~50 MB / tile

All filesystem I/O is gitignored via the existing ``runs/`` exclusion.

See ``docs/plans/F-SKY8-satellite-footprints.md``.
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import json
import math
import os
from pathlib import Path
from typing import Iterable

import requests
from shapely.geometry import box, shape
from shapely.strtree import STRtree

from .pipeline import BuildingRecord, _polygon_area_m2


_MS_DATASET_LINKS_URL = (
    "https://minedbuildings.z5.web.core.windows.net/global-buildings/dataset-links.csv"
)
_CACHE_DIR = Path(__file__).parent / "runs" / "satellite_footprints_cache"
_QUADKEY_ZOOM = 9


def _lonlat_to_quadkey(lon: float, lat: float, zoom: int = _QUADKEY_ZOOM) -> str:
    """Compute the Bing/Microsoft Buildings quadkey for a lon/lat.

    Quadkey is the Bing-style tile encoding: at zoom z the world is a
    2^z × 2^z grid; each tile gets a base-4 quadkey from the
    interleaved (x, y) bits, root-first.
    """
    n = 1 << zoom
    x = int(math.floor((lon + 180.0) / 360.0 * n))
    sinlat = math.sin(math.radians(lat))
    y = int(math.floor(
        (0.5 - math.log((1 + sinlat) / (1 - sinlat)) / (4 * math.pi)) * n
    ))
    x = max(0, min(n - 1, x))
    y = max(0, min(n - 1, y))
    chars: list[str] = []
    for i in range(zoom, 0, -1):
        digit = 0
        mask = 1 << (i - 1)
        if x & mask:
            digit += 1
        if y & mask:
            digit += 2
        chars.append(str(digit))
    return "".join(chars)


def _bbox_quadkeys(
    bbox: tuple[float, float, float, float],
    zoom: int = _QUADKEY_ZOOM,
) -> set[str]:
    """Quadkey tiles that overlap a bbox (S, W, N, E). For small bboxes
    (< 50 km) this is usually 1–4 tiles."""
    south, west, north, east = bbox
    qks: set[str] = set()
    for lat in (south, north, (south + north) * 0.5):
        for lon in (west, east, (west + east) * 0.5):
            qks.add(_lonlat_to_quadkey(lon, lat, zoom))
    return qks


def _load_dataset_links() -> dict[str, str]:
    """Fetch (cached) the Microsoft Buildings dataset-links CSV; return
    a dict mapping quadkey → tile download URL. Multiple tiles share a
    quadkey across countries; we keep the largest (most-populated)
    entry per quadkey since that's the one that matters for skyline
    work in metro areas."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    csv_path = _CACHE_DIR / "dataset-links.csv"
    if not csv_path.exists():
        r = requests.get(_MS_DATASET_LINKS_URL, timeout=120)
        r.raise_for_status()
        csv_path.write_bytes(r.content)

    qk_to: dict[str, tuple[int, str]] = {}
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            qk = (row.get("QuadKey") or "").strip()
            url = (row.get("Url") or "").strip()
            size_str = (row.get("Size") or "").strip()
            if not qk or not url:
                continue
            size_bytes = _parse_size(size_str)
            prev = qk_to.get(qk)
            if prev is None or size_bytes > prev[0]:
                qk_to[qk] = (size_bytes, url)
    return {qk: url for qk, (_, url) in qk_to.items()}


def _parse_size(size_str: str) -> int:
    """Parse Microsoft's human-readable size strings ("12.2MB", "70.1KB",
    "570B") to bytes. Forgiving; returns 0 on unknown shapes."""
    size_str = size_str.strip()
    if not size_str:
        return 0
    units = {"B": 1, "KB": 1024, "MB": 1024 ** 2, "GB": 1024 ** 3}
    for suffix, mult in units.items():
        if size_str.endswith(suffix):
            try:
                return int(float(size_str[: -len(suffix)]) * mult)
            except ValueError:
                return 0
    try:
        return int(size_str)
    except ValueError:
        return 0


def _fetch_tile(qk: str, url: str) -> Path | None:
    """Download a quadkey tile to the cache and return the path to the
    decompressed GeoJSONL file. Returns None on fetch failure."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _CACHE_DIR / f"qk_{qk}.geojsonl"
    if out_path.exists():
        return out_path
    try:
        r = requests.get(url, timeout=300)
        r.raise_for_status()
    except Exception as e:
        print(f"[ms_buildings] tile fetch failed qk={qk}: {e}")
        return None
    content = r.content
    if content[:2] == b"\x1f\x8b":
        try:
            content = gzip.decompress(content)
        except Exception as e:
            print(f"[ms_buildings] gzip decompress failed qk={qk}: {e}")
            return None
    try:
        out_path.write_bytes(content)
    except Exception as e:
        print(f"[ms_buildings] cache write failed qk={qk}: {e}")
        return None
    return out_path


def fetch_microsoft_buildings_for_bbox(
    bbox: tuple[float, float, float, float],
) -> list[dict]:
    """Return Microsoft Building polygons whose footprint intersects the
    bbox ``(south, west, north, east)``.

    Each result is ``{"geometry": <shapely Polygon>, "source":
    "ms_buildings"}``. Returns an empty list (with a warning to stdout)
    on any fetch error — the pipeline keeps running with OSM-only.
    """
    qks = _bbox_quadkeys(bbox)
    if not qks:
        return []
    try:
        links = _load_dataset_links()
    except Exception as e:
        print(f"[ms_buildings] dataset-links fetch failed: {e}")
        return []

    south, west, north, east = bbox
    bbox_poly = box(west, south, east, north)

    out: list[dict] = []
    for qk in sorted(qks):
        url = links.get(qk)
        if url is None:
            continue
        tile_path = _fetch_tile(qk, url)
        if tile_path is None:
            continue
        with tile_path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    feat = json.loads(line)
                    geom = feat.get("geometry")
                    if not geom:
                        continue
                    poly = shape(geom)
                    if not poly.is_valid or poly.is_empty:
                        continue
                    if not poly.intersects(bbox_poly):
                        continue
                    # Microsoft's properties.height: -1 means "not
                    # computed" (most non-US regions); a positive value
                    # means a real ML-derived height in meters. Pass it
                    # through when valid; rare for South America but
                    # automatic for any US tile.
                    props = feat.get("properties") or {}
                    raw_h = props.get("height")
                    height_m: float | None = None
                    if isinstance(raw_h, (int, float)) and raw_h > 0.5:
                        height_m = float(raw_h)
                    out.append({
                        "geometry": poly,
                        "source": "ms_buildings",
                        "height_m": height_m,
                    })
                except Exception:
                    continue
    return out


def merge_satellite_into_osm(
    osm_buildings: list[BuildingRecord],
    sat_polygons: list[dict],
    *,
    iou_dedup_threshold: float = 0.5,
) -> list[BuildingRecord]:
    """Add satellite polygons as additional ``BuildingRecord`` entries,
    skipping ones that duplicate an existing OSM polygon (interval IoU
    over polygon areas ≥ ``iou_dedup_threshold``).

    Satellite-sourced records carry ``height_tag_m=None`` and
    ``height_source="ms_buildings"`` — the existing
    ``_height_proxy`` sqrt-area fallback then governs their downstream
    weighting. ``feature_id`` gets a stable ``ms_<hash>`` prefix so the
    matcher can use it like any OSM id.
    """
    if not sat_polygons:
        return osm_buildings
    osm_geoms = [b.geometry for b in osm_buildings if b.geometry is not None]
    # Spatial index over OSM polygons — without it the per-satellite-
    # polygon dedup loop is O(N × M) which is 45M shapely ops on a
    # Cartagena-scale region (≈ 12 hours). STRtree's bbox prefilter cuts
    # that to ~10 candidates per satellite polygon (a few seconds total).
    tree = STRtree(osm_geoms) if osm_geoms else None
    out: list[BuildingRecord] = list(osm_buildings)
    added = 0
    for s in sat_polygons:
        sg = s.get("geometry")
        if sg is None or sg.is_empty:
            continue
        is_dup = False
        if tree is not None:
            candidate_idxs = tree.query(sg)
            for idx in candidate_idxs:
                og = osm_geoms[int(idx)]
                try:
                    inter = og.intersection(sg).area
                    if inter <= 0.0:
                        continue
                    union = og.union(sg).area
                    if union > 0.0 and inter / union >= iou_dedup_threshold:
                        is_dup = True
                        break
                except Exception:
                    continue
        if is_dup:
            continue
        centroid = sg.centroid
        # Stable id derived from polygon WKT — re-running on the same
        # tile produces the same ids.
        fid = "ms_" + hashlib.md5(sg.wkt.encode("utf-8")).hexdigest()[:8]
        try:
            area_m2 = _polygon_area_m2(sg)
        except Exception:
            area_m2 = float(sg.area)
        # Surface a Microsoft-derived height when the source had one
        # (US tiles; -1 elsewhere → None here). Tagged satellite records
        # get the same downstream treatment as OSM-tagged ones.
        sat_height = s.get("height_m")
        out.append(BuildingRecord(
            feature_id=fid,
            name="",
            geometry=sg,
            centroid_lat=float(centroid.y),
            centroid_lon=float(centroid.x),
            height_tag_m=sat_height,
            height_source=(
                "ms_buildings_height" if sat_height is not None
                else "ms_buildings"),
            area_m2=area_m2,
        ))
        added += 1
    print(f"[ms_buildings] merged {added} satellite polygons "
          f"({len(sat_polygons) - added} de-duped) into "
          f"{len(osm_buildings)} OSM => {len(out)} total")
    return out
