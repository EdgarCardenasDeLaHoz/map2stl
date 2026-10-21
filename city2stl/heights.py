"""
city2stl/heights.py — OSM building height parsing and raster enhancement.

Provides pure-computation GeoDataFrame helpers for building height data.
No HTTP, cache, or server dependencies.

Server callers (``app.server.core.city_data`` and the city routers) import this module directly.

``height_from_tags`` is the one rule for OSM tag heights (``height`` with
units, else ``building:levels`` x 3.2 m plus the roof); ``_fill_heights``
applies it to a GeoDataFrame with clip bounds and a ``height_source`` column
so downstream code can identify default-height buildings. Untagged ruins and
fortress enclosures default to 3 m, not 10 m, and a ruin's levels tag is
ignored (``low_height_override``;
``apply_low_default_heights`` does the same for payloads cached before it).
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


def has_pitched_roof_shape(props) -> bool:
    """True when ``roof:shape`` is tagged and is anything but ``flat``.

    Only a pitched roof stands a floor-equivalent above the last storey; a flat
    roof (or no roof tag, which on a block is nearly always flat) adds nothing,
    so a levels-only block is ``levels`` x :data:`METRES_PER_LEVEL`.
    """
    shape = _tag(props, "roof:shape")
    return shape not in ("", "flat")


def height_from_tags(props) -> tuple[float | None, str]:
    """Building height in metres from OSM tags, and where it came from.

    The single rule for tag heights:

    1. ``height`` (or ``building:height``), units parsed (``"12 m"``,
       ``"40 ft"``) → source ``"osm_tag"``.
    2. ``building:levels`` (or ``levels``) × :data:`METRES_PER_LEVEL`, plus
       ``roof:levels`` × :data:`METRES_PER_LEVEL` when tagged, else one
       level-equivalent for the roof only when ``roof:shape`` is pitched
       (:func:`has_pitched_roof_shape`; a flat or untagged roof adds none)
       → source ``"osm_levels"``.
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
            roof_levels = 1.0 if has_pitched_roof_shape(props) else 0.0
        # A building:part's levels start at its building:min_level.
        below = _parse_count(props.get("building:min_level")) or 0.0
        return (below + levels + roof_levels) * METRES_PER_LEVEL, "osm_levels"

    return None, "default"

def min_height_from_tags(props) -> float:
    """Where a ``building:part`` starts above the ground, in metres: ``min_height``,
    else ``building:min_level`` x :data:`METRES_PER_LEVEL`, else 0."""
    h = parse_length_m(props.get("min_height"))
    if h is None:
        levels = _parse_count(props.get("building:min_level"))
        h = levels * METRES_PER_LEVEL if levels is not None else 0.0
    return max(h, 0.0)


#: Fallback height (m) of an untagged ruin or fortress enclosure instead of the
#: flat building default (10 m), and of a ruin whose height comes only from a
#: levels tag. Equal to the building clamp's lower bound.
LOW_DEFAULT_HEIGHT_M = 3.0
_ENCLOSURE_HISTORIC = frozenset({"castle", "fort", "fortification", "citywalls"})
#: ``castle_type`` values that mean a roofed building, which keeps the flat default.
_ROOFED_CASTLE_TYPES = frozenset({"palace", "stately", "manor", "schloss", "château", "chateau"})


def _tag(props, key: str) -> str:
    v = props.get(key)
    return "" if _is_missing(v) else str(v).strip().lower()


def is_ruin(props) -> bool:
    """``building=ruins``, ``historic=ruins`` or ``ruins=yes``."""
    return ("ruins" in (_tag(props, "building"), _tag(props, "historic"))
            or _tag(props, "ruins") == "yes")


def is_low_default_footprint(props) -> bool:
    """True for a ruin (:func:`is_ruin`) or an open fortress enclosure / courtyard.

    Enclosure: ``building=courtyard`` or ``historic`` in castle / fort /
    fortification / citywalls, unless ``castle_type`` marks a roofed building
    (palace, stately, manor, schloss, chateau).
    """
    if is_ruin(props) or _tag(props, "building") == "courtyard":
        return True
    historic = _tag(props, "historic")
    return historic in _ENCLOSURE_HISTORIC and _tag(props, "castle_type") not in _ROOFED_CASTLE_TYPES


def default_height_for(props, default_m: float) -> float:
    """Fallback height for a building with no height / levels tag:
    :data:`LOW_DEFAULT_HEIGHT_M` (never above *default_m*) for ruins and fortress
    enclosures (:func:`is_low_default_footprint`), else *default_m*. Granada
    Alhambra study: lidar reads the Alcazaba at about 0.2 m and tagged ruins at
    0.1-2.5 m, where the 10 m default stood them up as tall blocks. A ruin's
    levels tag is ignored too (:func:`low_height_override`)."""
    if is_low_default_footprint(props):
        return min(LOW_DEFAULT_HEIGHT_M, float(default_m))
    return float(default_m)


def low_height_override(props, h: float, src: str, default_m: float) -> tuple[float, str]:
    """``(height, source)`` after the low-default rule, for a building whose tag
    rule gave ``(h, src)`` (``h`` already the fallback when ``src == "default"``).

    - ``"default"``: :func:`default_height_for` (ruins and enclosures at 3 m).
    - ``"osm_levels"`` on a ruin (:func:`is_ruin`): a ruin's levels describe the
      building it was, not what stands, so it gets :data:`LOW_DEFAULT_HEIGHT_M`
      and source ``"default"``, which lets height providers (lidar fill) override
      it. An explicit ``height`` tag (``"osm_tag"``) still wins.
    - Anything else unchanged. Never raises a height.
    """
    if src == "default":
        return min(h, default_height_for(props, default_m)), src
    if src == "osm_levels" and is_ruin(props):
        return min(h, LOW_DEFAULT_HEIGHT_M), "default"
    return h, src


def apply_low_default_heights(fc: dict) -> tuple[dict, int]:
    """Apply :func:`low_height_override` to a cached buildings FeatureCollection:
    ``(fc, n_changed)``.

    For payloads cached before the rule (their features went through
    ``_fill_heights`` with the flat 10 m and counted a ruin's levels). Uses
    whatever tags the features carry: ``"default"`` ruins / enclosures drop to
    3 m and keep that source; ``"osm_levels"`` ruins drop to 3 m and become
    ``"default"``, so height providers may still fill both. Never raises a height
    and is idempotent. Returns a new collection when anything changed; the input
    is not mutated.
    """
    feats = fc.get("features") if isinstance(fc, dict) else None
    if not feats:
        return fc, 0
    out, n = [], 0
    for f in feats:
        props = f.get("properties") or {}
        src = props.get("height_source")
        if src in ("default", "osm_levels"):
            try:
                h = float(props.get("height_m"))
            except (TypeError, ValueError):
                h = math.nan
            if not math.isfinite(h):
                h = 10.0   # city_model extrudes a missing height_m at 10 m
            new, new_src = low_height_override(props, h, src, h)
            if new < h or new_src != src:
                f = {**f, "properties": {**props, "height_m": new, "height_source": new_src}}
                n += 1
        out.append(f)
    return ({**fc, "features": out}, n) if n else (fc, 0)


# Roof geometry tags preserved through the dissolve step so that
# _build_building_meshes() can generate shaped roofs.
_ROOF_COLS = [
    "roof:shape", "roof:height", "roof:levels",
    "roof:direction", "roof:orientation",
    "roof:colour", "roof:material",
    "building:levels", "min_height",
]
# Identity and classification tags (F-LANDMARK §3/§5), preserved the same way:
# a merged group keeps the largest member's OSM id, so an override of that id
# replaces the whole merged footprint.
# ``ruins`` and ``castle_type`` feed :func:`is_low_default_footprint`.
LANDMARK_TAG_COLS = ["osm_id", "name", "building", "amenity", "historic", "tourism",
                     "ruins", "castle_type"]


def keeps_own_footprint(row) -> bool:
    """True for a building :func:`_reduce_buildings` must not merge into a neighbour.

    A named or ``historic`` building, or a tower (``tower:type``,
    ``man_made=tower``, ``building=tower``), is a landmark of its own: merged,
    it lost its osm_id, name and height to the largest member of its group
    (Granada: 26 touching 10 m halls and towers of the Nasrid palaces - Mexuar,
    Torre de Comares, Banos Reales - became "Palacio de Carlos V"; user,
    2026-10-14: "Keep named separate"). ``row`` is a mapping of tag columns;
    absent columns count as untagged.
    """
    get = row.get
    if not _is_missing(get("name")) or not _is_missing(get("historic")):
        return True
    if not _is_missing(get("tower:type")):
        return True
    return (str(get("man_made") or "").strip().lower() == "tower"
            or str(get("building") or "").strip().lower() == "tower")


def _reduce_buildings(gdf):
    """
    Reduce building polygon count by merging only buildings that physically
    touch or overlap and share the same rounded height. Landmarks (named,
    ``historic`` or towers: :func:`keeps_own_footprint`) are never merged and
    keep their own tags.

    Uses a spatial-graph approach: build an adjacency graph from intersecting
    pairs, find connected components, and dissolve each component separately.
    This avoids the unary_union-per-height-group mistake that previously merged
    ALL buildings of the same height into one blob regardless of distance.

    Roof tag columns (_ROOF_COLS), LANDMARK_TAG_COLS (osm_id, name, ...) and
    height_source are preserved: for each
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
        cols = [c for c in ("name", "historic", "tower:type", "man_made", "building")
                if c in gdf.columns]
        alone = ([keeps_own_footprint(r) for r in gdf[cols].to_dict("records")]
                 if cols else [False] * n)
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
            if alone[i]:
                continue
            candidates = list(sindex.query(geom, predicate='intersects'))
            for j in candidates:
                if j <= i or alone[j]:
                    continue
                if gdf.at[i, 'height_m'] == gdf.at[j, 'height_m']:
                    union(i, j)

        # Assign component labels
        gdf['_comp'] = [find(i) for i in range(n)]

        # Build a lookup: comp_id -> tag dict from the largest-area building.
        # This ensures that, e.g., a cathedral's bell-tower tags win over an
        # adjacent tiny annexe when both are merged into one component.
        extra_cols = ["height_source"] + [c for c in _ROOF_COLS + LANDMARK_TAG_COLS
                                          if c in gdf.columns]
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
    low_default: bool = False,
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
        low_default: Buildings: apply :func:`low_height_override` (untagged
                    ruins and fortress enclosures at 3 m instead of *default_m*;
                    a ruin's levels tag ignored, source ``"default"``).
    """
    tag_cols = [c for c in ("height", "building:height") if c in gdf.columns]
    if low_default:
        tag_cols += [c for c in ("building", "historic", "ruins", "castle_type")
                     if c in gdf.columns]
    level_cols: dict[str, str] = {}
    if levels_col and levels_col in gdf.columns:
        level_cols["building:levels"] = levels_col
        if "roof:levels" in gdf.columns:
            level_cols["roof:levels"] = "roof:levels"
        if "roof:shape" in gdf.columns:
            level_cols["roof:shape"] = "roof:shape"

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
        if low_default:
            h, src = low_height_override(props, h, src, default_m)
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
