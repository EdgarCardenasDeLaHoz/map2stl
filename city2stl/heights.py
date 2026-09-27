"""
city2stl/heights.py — OSM building height parsing and raster enhancement.

Provides pure-computation GeoDataFrame helpers for building height data.
No HTTP, cache, or server dependencies.

Server entry point: app.server.core.osm re-exports all public symbols.

``height_from_tags`` is the one rule for OSM tag heights (``height`` with
units, else ``building:levels`` x 3.2 m plus the roof); ``_fill_heights``
applies it to a GeoDataFrame with clip bounds and a ``height_source`` column
so downstream code can identify default-height buildings.
"""

from __future__ import annotations

import logging
import math
import re

import numpy as np

logger = logging.getLogger(__name__)

# Metres of building height per OSM ``building:levels``. 3.2 m is the middle
# of the residential/commercial range and is the same figure the skyline
# facade-periodicity estimator assumes (``_floor_period_for_building``'s
# ``floor_height_m``). The two used to disagree — 4.0 here against 3.2 there —
# which made a floor-count height and a levels-tag height for the same
# building differ by 25 % for no physical reason.
METRES_PER_LEVEL = 3.2

_FEET_TO_M = 0.3048
_NUMBER_RE = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


def _is_missing(value) -> bool:
    """True for None, NaN and blank strings (pandas hands NaN for absent tags)."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return isinstance(value, str) and not value.strip()


def parse_length_m(value) -> float | None:
    """Parse an OSM length tag (``"12"``, ``"12 m"``, ``"40 ft"``, ``"40'"``) to metres.

    Only the first value of a ``;``-separated list is used. Returns None when
    the tag is missing or holds no number.
    """
    if _is_missing(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).split(";")[0].strip().lower()
    m = _NUMBER_RE.search(text)
    if not m:
        return None
    number = float(m.group(0).replace(",", "."))
    unit = text[m.end():].strip()
    if unit.startswith(("ft", "feet", "foot", "'")):
        number *= _FEET_TO_M
    return number


def _parse_count(value) -> float | None:
    """Parse a ``building:levels`` / ``roof:levels`` count, or None."""
    if _is_missing(value):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    m = _NUMBER_RE.search(str(value).split(";")[0])
    return float(m.group(0).replace(",", ".")) if m else None


def height_from_tags(props) -> tuple[float | None, str]:
    """Building height in metres from OSM tags, and where it came from.

    The single rule for tag heights:

    1. ``height`` (or ``building:height``), units parsed (``"12 m"``,
       ``"40 ft"``) → source ``"osm_tag"``.
    2. ``building:levels`` (or ``levels``) × :data:`METRES_PER_LEVEL`, plus
       ``roof:levels`` × :data:`METRES_PER_LEVEL` when tagged, else one
       level-equivalent for the roof → source ``"osm_levels"``.
    3. Otherwise ``(None, "default")``; callers apply their own default
       height (usually 10 m) and clamps.

    Args:
        props: Mapping of OSM tags (GeoJSON ``properties``, a dict, or a
            pandas row).
    """
    for key in ("height", "building:height"):
        h = parse_length_m(props.get(key))
        if h is not None:
            return h, "osm_tag"

    levels = _parse_count(props.get("building:levels"))
    if levels is None:
        levels = _parse_count(props.get("levels"))
    if levels is not None:
        roof_levels = _parse_count(props.get("roof:levels"))
        if roof_levels is None:
            roof_levels = 1.0
        return (levels + roof_levels) * METRES_PER_LEVEL, "osm_levels"

    return None, "default"

# Roof geometry tags preserved through the dissolve step so that
# _build_building_meshes() can generate shaped roofs.
_ROOF_COLS = [
    "roof:shape", "roof:height", "roof:levels",
    "roof:direction", "roof:orientation",
    "roof:colour", "roof:material",
    "building:levels", "min_height",
]


def _reduce_buildings(gdf):
    """
    Reduce building polygon count by merging only buildings that physically
    touch or overlap and share the same rounded height.

    Uses a spatial-graph approach: build an adjacency graph from intersecting
    pairs, find connected components, and dissolve each component separately.
    This avoids the unary_union-per-height-group mistake that previously merged
    ALL buildings of the same height into one blob regardless of distance.

    Roof tag columns (_ROOF_COLS) and height_source are preserved: for each
    merged group the tags from the largest-area member building are used.

    Falls back to the original gdf on any error.
    """
    try:

        original_crs = gdf.crs
        gdf = gdf.copy().to_crs(epsg=3857)
        gdf['height_m'] = gdf['height_m'].round(0)
        gdf = gdf.reset_index(drop=True)

        n = len(gdf)
        if n == 0:
            return gdf.to_crs(original_crs)

        # Build adjacency: find pairs that touch/overlap using spatial index
        sindex = gdf.sindex
        parent = list(range(n))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        def union(i, j):
            pi, pj = find(i), find(j)
            if pi != pj:
                parent[pi] = pj

        for i, geom in enumerate(gdf.geometry):
            candidates = list(sindex.query(geom, predicate='intersects'))
            for j in candidates:
                if j <= i:
                    continue
                if gdf.at[i, 'height_m'] == gdf.at[j, 'height_m']:
                    union(i, j)

        # Assign component labels
        gdf['_comp'] = [find(i) for i in range(n)]

        # Build a lookup: comp_id -> tag dict from the largest-area building.
        # This ensures that, e.g., a cathedral's bell-tower tags win over an
        # adjacent tiny annexe when both are merged into one component.
        extra_cols = ["height_source"] + [c for c in _ROOF_COLS if c in gdf.columns]
        if extra_cols:
            gdf['_area_m2'] = gdf.geometry.area
            # Sort descending so the first row per group is the largest building
            rep = (
                gdf.sort_values('_area_m2', ascending=False)
                   .drop_duplicates(subset=['_comp'])
                   .set_index('_comp')
            )
            # Build {comp_id: {col: val, ...}} lookup
            tag_lookup: dict = {}
            for comp_id in rep.index:
                tag_lookup[comp_id] = {
                    col: rep.at[comp_id, col]
                    for col in extra_cols
                    if col in rep.columns
                }
            gdf = gdf.drop(columns=['_area_m2'])

        # Dissolve each component into a single (multi)polygon
        gdf_dissolved = gdf.dissolve(by='_comp')   # _comp is now the index
        gdf_dissolved['geometry'] = gdf_dissolved.geometry.make_valid()

        gdf_out = gdf_dissolved.explode(index_parts=False)
        gdf_out = gdf_out[
            gdf_out.geometry.notna() &
            gdf_out.geometry.geom_type.isin(['Polygon', 'MultiPolygon'])
        ]

        # Restore extra columns from the largest-building representative
        if extra_cols and tag_lookup:
            for col in extra_cols:
                gdf_out[col] = gdf_out.index.map(
                    lambda cid: tag_lookup.get(cid, {}).get(col)  # noqa: B023
                )

        gdf_out = gdf_out.reset_index(drop=True)

        keep = ['geometry', 'height_m'] + [c for c in extra_cols if c in gdf_out.columns]
        return gdf_out[keep].to_crs(original_crs)
    except Exception as exc:
        logger.warning(f"_reduce_buildings failed, using raw geometries: {exc}")
        return gdf


def _fill_heights(
    gdf,
    default_m: float,
    lo: float = 2.0,
    hi: float = 300.0,
    levels_col: str | None = None,
):
    """Fill height_m for OSM features with :func:`height_from_tags`.

    Also sets ``height_source`` to one of ``"osm_tag"``, ``"osm_levels"``,
    or ``"default"`` so downstream code can identify buildings that only
    have a fallback height (candidates for raster enhancement).

    Args:
        default_m:  Fallback height when no tag gives one.
        lo, hi:     Clip bounds in metres.
        levels_col: If set, the level-count column (with ``roof:levels``) is
                    a secondary fallback before *default_m* (buildings only).
                    Without it only the ``height`` tags are read.
    """
    tag_cols = [c for c in ("height", "building:height") if c in gdf.columns]
    level_cols: dict[str, str] = {}
    if levels_col and levels_col in gdf.columns:
        level_cols["building:levels"] = levels_col
        if "roof:levels" in gdf.columns:
            level_cols["roof:levels"] = "roof:levels"

    heights: list[float] = []
    sources: list[str] = []
    tag_rows = gdf[tag_cols].to_dict("records") if tag_cols else [{}] * len(gdf)
    level_rows = (gdf[list(level_cols.values())].to_dict("records")
                  if level_cols else [{}] * len(gdf))
    for tags, lv in zip(tag_rows, level_rows, strict=True):
        props = dict(tags)
        for key, col in level_cols.items():
            props[key] = lv.get(col)
        h, src = height_from_tags(props)
        if h is None:
            h = float(default_m)
        heights.append(round(min(max(h, lo), hi), 1))
        sources.append(src)

    gdf = gdf.copy()
    gdf['height_m'] = np.asarray(heights, dtype=float)
    gdf['height_source'] = sources
    return gdf


def enhance_buildings_with_raster(
    buildings_geojson: dict,
    raster: np.ndarray,
    bbox: tuple,
    confidence_raster: np.ndarray | None = None,
    min_confidence: float = 0.3,
    source_name: str = "raster",
) -> dict:
    """Enhance building heights by sampling a height raster at each centroid.

    Only overwrites buildings whose ``height_source`` is ``"default"`` (i.e.
    those that fell through to the 10 m fallback because OSM had no tag).

    Args:
        buildings_geojson: GeoJSON FeatureCollection with ``height_m`` and
            ``height_source`` in each feature's properties.
        raster: (H, W) float32 array of building heights in metres above
            ground.  NaN means no data.
        bbox: (north, south, east, west) geographic bounds matching *raster*.
        confidence_raster: Optional (H, W) float32 [0, 1] array.
        min_confidence: Minimum confidence to accept a raster sample.
        source_name: Value written to ``height_source`` for enhanced buildings.
            Defaults to ``"raster"``; callers should pass the provider name
            (e.g. ``"google3d"``, ``"ghsl"``, ``"shadow"``) so the origin of
            each height value is traceable.

    Returns:
        ``{"buildings": <modified GeoJSON>, "stats": {...}}``
    """
    north, south, east, west = bbox
    h, w = raster.shape

    features = buildings_geojson.get("features", [])
    total = len(features)
    enhanced = 0
    no_data = 0
    unchanged = 0

    for feat in features:
        props = feat.get("properties") or {}
        if props.get("height_source") != "default":
            unchanged += 1
            continue

        # Compute centroid from exterior ring
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates")
        if not coords:
            unchanged += 1
            continue

        # Get the exterior ring (first ring of first polygon)
        ring = coords
        gtype = geom.get("type", "")
        if gtype == "MultiPolygon":
            ring = coords[0][0] if coords and coords[0] else None
        elif gtype == "Polygon":
            ring = coords[0] if coords else None
        else:
            unchanged += 1
            continue

        if not ring or len(ring) < 3:
            unchanged += 1
            continue

        # Mean of exterior ring as centroid approximation
        cx = sum(p[0] for p in ring) / len(ring)  # longitude
        cy = sum(p[1] for p in ring) / len(ring)  # latitude

        # Map to raster pixel
        col = int((cx - west) / (east - west) * w)
        row = int((north - cy) / (north - south) * h)

        if row < 0 or row >= h or col < 0 or col >= w:
            no_data += 1
            continue

        val = float(raster[row, col])
        if np.isnan(val) or val <= 0:
            no_data += 1
            continue

        if confidence_raster is not None:
            conf = float(confidence_raster[row, col])
            if conf < min_confidence:
                no_data += 1
                continue

        # Clamp to reasonable range
        val = max(3.0, min(300.0, round(val, 1)))
        props["height_m"] = val
        props["height_source"] = source_name
        enhanced += 1

    stats = {
        "total": total,
        "enhanced": enhanced,
        "unchanged": unchanged,
        "no_data": no_data,
    }
    logger.info(f"[enhance] {enhanced}/{total} buildings enhanced with raster heights "
                f"({unchanged} had OSM data, {no_data} no raster coverage)")

    return {"buildings": buildings_geojson, "stats": stats}
