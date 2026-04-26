"""
city2stl/heights.py — OSM building height parsing and raster enhancement.

Provides pure-computation GeoDataFrame helpers for building height data.
No HTTP, cache, or server dependencies.

Server entry point: app.server.core.osm re-exports all public symbols.

-- Legacy note --
city2stl/buildings.py had a building_heights() function that did similar
height parsing from GeoDataFrames using the old "building:height" column.
_fill_heights here is the modern replacement: it handles both "height" and
"building:levels" OSM tags, applies clip bounds, and tags each row with a
height_source so downstream code can identify default-height buildings.
"""

from __future__ import annotations

import logging

import numpy as np

logger = logging.getLogger(__name__)

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
        import numpy as np  # noqa: F811 (local re-import for clarity)

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
                    lambda cid: tag_lookup.get(cid, {}).get(col)
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
    """Fill height_m for OSM features from the ``height`` tag.

    Also sets ``height_source`` to one of ``"osm_tag"``, ``"osm_levels"``,
    or ``"default"`` so downstream code can identify buildings that only
    have a fallback height (candidates for raster enhancement).

    Args:
        default_m:  Fallback height when tag is absent or unparseable.
        lo, hi:     Clip bounds in metres.
        levels_col: If set, use ``gdf[levels_col] * 4.0`` as a secondary
                    fallback before *default_m* (buildings only).
    """
    try:
        import pandas as pd
    except ImportError:
        gdf = gdf.copy()
        gdf['height_m'] = float(default_m)
        gdf['height_source'] = 'default'
        return gdf

    n = len(gdf)  # noqa: F841 (kept for potential future use)
    source = pd.Series('default', index=gdf.index)

    # Levels-based estimate (buildings only)
    if levels_col and levels_col in gdf.columns:
        levels = pd.to_numeric(gdf[levels_col], errors='coerce')
        has_levels = levels.notna()
        height_from_levels = levels.fillna(3.0) * 4.0
        source = source.where(~has_levels, 'osm_levels')
    else:
        height_from_levels = pd.Series(float(default_m), index=gdf.index)

    # Explicit OSM height tag (strip trailing unit strings like " m" or "ft")
    if 'height' in gdf.columns:
        raw = gdf['height'].astype(str).str.extract(r'([\d.]+)', expand=False)
        explicit = pd.to_numeric(raw, errors='coerce')
        has_tag = explicit.notna()
        height_m = explicit.fillna(height_from_levels)
        source = source.where(~has_tag, 'osm_tag')
    else:
        height_m = height_from_levels

    height_m = (
        pd.to_numeric(height_m, errors='coerce')
        .fillna(float(default_m))
        .clip(lower=lo, upper=hi)
        .round(1)
    )
    gdf = gdf.copy()
    gdf['height_m'] = height_m
    gdf['height_source'] = source
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
