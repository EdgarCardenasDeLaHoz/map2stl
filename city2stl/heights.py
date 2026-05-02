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
    "roof_height_m",  # set by _fill_heights; needed by rasterizer
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
    m_per_level: float = 3.5,
):
    """Fill height_m for OSM features from the ``height`` tag.

    Also sets ``height_source`` to one of ``"osm_tag"``, ``"osm_levels"``,
    or ``"default"`` so downstream code can identify buildings that only
    have a fallback height (candidates for raster enhancement).

    Args:
        default_m:   Fallback height when tag is absent or unparseable.
        lo, hi:      Clip bounds in metres.
        levels_col:  If set, use ``gdf[levels_col] * m_per_level`` as a secondary
                     fallback before *default_m* (buildings only).
        m_per_level: Floor-to-floor height in metres. Defaults to 3.5.
                     Use 3.0–3.5 for Southern Europe (e.g. 3.4 for Granada),
                     3.5–4.0 for Northern Europe/US.
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
        height_from_levels = levels.fillna(3.0) * m_per_level
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

    # Compute roof_height_m for the rasterizer / mesh extruder.
    # Priority: explicit OSM 'roof:height' tag → roof:levels × m_per_level
    #           → default 30% of height_m, capped at 50% of height_m, min 2 m.
    # The rasterizer (city2stl/rasterize.py:_paint_building_roof) reads this
    # to determine ridge height for gabled / hipped / skillion / dome roofs.
    if 'roof:height' in gdf.columns:
        roof_raw = gdf['roof:height'].astype(str).str.extract(r'([\d.]+)', expand=False)
        roof_explicit = pd.to_numeric(roof_raw, errors='coerce')
    else:
        roof_explicit = pd.Series(np.nan, index=gdf.index, dtype='float64')

    if 'roof:levels' in gdf.columns:
        roof_levels = pd.to_numeric(gdf['roof:levels'], errors='coerce')
        roof_from_levels = roof_levels * m_per_level
    else:
        roof_from_levels = pd.Series(np.nan, index=gdf.index, dtype='float64')

    # 30% of building height, with floor of 2 m. Cap at 50% of height_m so the
    # eaves don't go negative.
    roof_default = (0.30 * height_m).clip(lower=2.0)
    roof_height_m = (
        roof_explicit
        .fillna(roof_from_levels)
        .fillna(roof_default)
        .clip(lower=0.0)
    )
    roof_height_m = np.minimum(roof_height_m.values, 0.5 * height_m.values)
    gdf['roof_height_m'] = pd.Series(roof_height_m, index=gdf.index).round(2)
    return gdf


def enhance_buildings_with_raster(
    buildings_geojson: dict,
    raster: np.ndarray,
    bbox: tuple,
    confidence_raster: np.ndarray | None = None,
    min_confidence: float = 0.3,
    source_name: str = "raster",
    footprint_percentile: float = 90.0,
) -> dict:
    """Enhance building heights by sampling a height raster across each footprint.

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
        footprint_percentile: Percentile of valid footprint samples to use when
            multiple raster pixels overlap a building footprint.

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

    # Derive a local height cap from OSM-sourced buildings in this collection.
    # Raster-derived heights (e.g. from shadow estimation) can include terrain
    # artefacts; capping at local_osm_max * 1.2 prevents wild outliers.
    _osm_sources = {"osm_tag", "osm_levels", "osm_parts"}
    osm_heights = [
        (feat.get("properties") or {}).get("height_m") or 0
        for feat in features
        if (feat.get("properties") or {}).get("height_source") in _osm_sources
    ]
    local_osm_max = max(osm_heights) if osm_heights else 0.0
    # Hard cap: at least 30 m (covers ~8-floor buildings in low-rise cities),
    # never more than 150 m even if OSM has tall spires/towers.
    # Shadow-based providers are additionally capped at 50 m in the provider
    # itself, so the 150 m ceiling only matters for LiDAR/photogrammetry sources.
    height_cap = max(30.0, min(150.0, local_osm_max * 1.2)) if local_osm_max > 0 else 30.0

    for feat in features:
        props = feat.get("properties") or {}
        if props.get("height_source") != "default":
            unchanged += 1
            continue

        geom = feat.get("geometry", {})
        if not geom:
            unchanged += 1
            continue

        val = _sample_raster_value_for_geometry(
            geom,
            raster,
            bbox,
            confidence_raster=confidence_raster,
            min_confidence=min_confidence,
            footprint_percentile=footprint_percentile,
        )
        if val is None or np.isnan(val) or val <= 0:
            no_data += 1
            continue

        # Clamp to reasonable range — use local OSM-derived cap to prevent
        # terrain/shadow artefacts from inflating heights in hilly cities.
        val = max(3.0, min(height_cap, round(val, 1)))
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
                f"({unchanged} had OSM data, {no_data} no raster coverage, cap={height_cap:.0f}m)")

    return {"buildings": buildings_geojson, "stats": stats}


def _sample_raster_value_for_geometry(
    geom: dict,
    raster: np.ndarray,
    bbox: tuple,
    confidence_raster: np.ndarray | None = None,
    min_confidence: float = 0.3,
    footprint_percentile: float = 90.0,
) -> float | None:
    """Sample a footprint-aware raster height for a Polygon or MultiPolygon."""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        return None

    north, south, east, west = bbox
    h, w = raster.shape
    lon_span = east - west
    lat_span = north - south
    if lon_span <= 0 or lat_span <= 0 or h <= 0 or w <= 0:
        return None

    def _to_px(lon: float, lat: float) -> tuple[float, float]:
        x = (lon - west) / lon_span * w
        y = (north - lat) / lat_span * h
        return x, y

    gtype = geom.get("type", "")
    coords = geom.get("coordinates")
    if not coords or gtype not in {"Polygon", "MultiPolygon"}:
        return None

    polygons = coords if gtype == "MultiPolygon" else [coords]
    all_points: list[tuple[float, float]] = []
    for poly in polygons:
        for ring in poly or []:
            all_points.extend(_to_px(float(lon), float(lat)) for lon, lat in ring)
    if not all_points:
        return None

    xs = [pt[0] for pt in all_points]
    ys = [pt[1] for pt in all_points]
    c0 = max(0, int(np.floor(min(xs))))
    c1 = min(w, int(np.ceil(max(xs))) + 1)
    r0 = max(0, int(np.floor(min(ys))))
    r1 = min(h, int(np.ceil(max(ys))) + 1)
    if c0 >= c1 or r0 >= r1:
        return None

    mask_img = Image.new("1", (c1 - c0, r1 - r0), 0)
    draw = ImageDraw.Draw(mask_img)
    for poly in polygons:
        if not poly:
            continue
        outer = poly[0] if poly[0] else None
        if not outer:
            continue
        outer_px = [(x - c0, y - r0) for x, y in (_to_px(float(lon), float(lat)) for lon, lat in outer)]
        draw.polygon(outer_px, fill=1)
        for hole in poly[1:]:
            if not hole:
                continue
            hole_px = [(x - c0, y - r0) for x, y in (_to_px(float(lon), float(lat)) for lon, lat in hole)]
            draw.polygon(hole_px, fill=0)

    footprint_mask = np.array(mask_img, dtype=bool)
    if not np.any(footprint_mask):
        return None

    patch = raster[r0:r1, c0:c1]
    valid_mask = footprint_mask & np.isfinite(patch) & (patch > 0)
    if confidence_raster is not None:
        conf_patch = confidence_raster[r0:r1, c0:c1]
        valid_mask &= np.isfinite(conf_patch) & (conf_patch >= min_confidence)

    if not np.any(valid_mask):
        return None

    values = patch[valid_mask]
    if values.size <= 4:
        return float(np.nanmax(values))
    percentile = float(np.clip(footprint_percentile, 50.0, 100.0))
    return float(np.nanpercentile(values, percentile))
