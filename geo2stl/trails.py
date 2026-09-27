"""geo2stl/trails.py — Ski and hiking trail fetching and rasterization.

Fetches ski pistes and hiking paths for a bounding box and rasterizes each category
to its own relief grid, so the two can be toggled independently downstream without
another fetch. Mirrors the provider/service shape of ``geo2stl.hydrology``.

Source choice: OpenStreetMap is the primary and the only global source. OpenSkiMap and
OpenSnowMap look like independent ski sources but are both rendered *from* OSM and
publish tiles or whole-planet dumps rather than bounding-box vector queries, so adding
them would re-fetch the same geometry at far higher cost. The one genuinely independent
source wired in here is the USDA Forest Service EDW trails layer, which is hiking-only
and covers US national forest land; ``source="all"`` unions it with OSM.

Trails are engraved by default (negative relief). A groove of a few metres survives
slicing at model scale; a raised ridge of the same size usually does not.
"""

from __future__ import annotations

import logging

import numpy as np
from numpy2stl.raster import burn_polygons

from geo2stl.geo import M_PER_DEG_LAT, M_PER_DEG_LON_EQ
from geo2stl.osm import use_overpass_endpoint

logger = logging.getLogger(__name__)

# Categories are fixed: the UI toggles exactly these two.
CATEGORIES = ("ski", "hiking")

# OSM tag sets per category. Values are osmnx `features_from_bbox` tag filters.
OSM_SKI_TAGS = {
    # piste:type is the canonical piste tag and covers downhill, nordic, skitour,
    # sled and snow_park. `route=piste` catches relation-tagged pistes.
    "piste:type": True,
    "route": ["piste", "ski"],
}
OSM_HIKING_TAGS = {
    "highway": ["path", "footway", "track", "bridleway", "steps"],
    "route": ["hiking", "foot"],
}

# OSM `piste:difficulty` values, ordered easiest to hardest. A feature's class is
# its index in this tuple plus one, so 0 is free to mean "no usable difficulty
# tag" in the uint8 difficulty grid. The order is also the paint order: harder
# pistes are burned last, so where two runs overlap the harder one shows, which
# is the conservative reading for a skier looking at the map.
SKI_DIFFICULTY_CLASSES = (
    "novice", "easy", "intermediate", "advanced", "expert", "freeride", "extreme",
)
_DIFFICULTY_INDEX = {name: i + 1 for i, name in enumerate(SKI_DIFFICULTY_CLASSES)}

# USDA Forest Service "Trails (NFS Publish)" — public ArcGIS MapServer, no API key.
USFS_TRAILS_URL = (
    "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_TrailNFSPublish_01"
    "/MapServer/0/query"
)

# Continental-scale sanity bound for the USFS provider. Outside it the query is a
# guaranteed empty round trip, so skip the request instead of paying the timeout.
_USFS_BBOX = (-179.5, 17.0, -64.0, 72.0)  # west, south, east, north

# Per-request budget for the Overpass queries. A resort-sized trails query on a
# healthy mirror answers in under 10 s; 180 s leaves room for a loaded one while
# still failing over to the next mirror in bounded time rather than hanging.
_OVERPASS_REQUEST_TIMEOUT_S = 180


class TrailsUpstreamError(RuntimeError):
    """Every trail provider failed to answer.

    Distinct from a region that genuinely has no trails in it. The two used to
    be indistinguishable to the caller - an Overpass outage produced the same
    empty grids and the same "no trails found" message as a stretch of open
    desert - which is a wrong answer rather than a missing one.
    """


class TrailsLayerBase:
    """Base interface for trail providers.

    A provider returns a GeoJSON-style FeatureCollection per requested category, or
    ``None`` for a category it does not serve. Rasterization is shared and happens in
    :class:`TrailsService`, so providers only fetch geometry.
    """

    name: str = "trails-provider"

    #: Categories this provider can serve.
    categories: tuple[str, ...] = CATEGORIES

    def fetch(
        self,
        north: float,
        south: float,
        east: float,
        west: float,
        categories: tuple[str, ...] = CATEGORIES,
    ) -> dict[str, dict]:
        raise NotImplementedError()


# ---------------------------------------------------------------------------
# Rasterization (shared by every provider)
# ---------------------------------------------------------------------------

def rasterize_trails(
    geojson: dict,
    bbox: tuple[float, float, float, float],
    dim: int,
    relief_m: float = -2.0,
    width_m: float = 8.0,
    with_areas: bool = False,
    with_difficulty: bool = False,
):
    """Rasterize trail linework to a relief grid.

    Args:
        geojson: FeatureCollection of LineString/MultiLineString. Polygons are
            accepted too — piste areas are often tagged as closed ways — and are
            reduced to their boundary, so every feature engraves as linework of
            ``width_m`` rather than as a filled area.
        bbox: (west, south, east, north)
        dim: output grid resolution (pixels per side)
        relief_m: signed relief applied to trail pixels; negative engraves.
        width_m: rendered trail width in metres, floored at two pixels so a trail
            cannot alias away at coarse resolutions.
        with_areas: also return a mask of the polygon interiors. The mask is for
            display only — a client tints it to show ski-area extent — and is
            deliberately kept out of the relief grid, since engraving a filled
            area would cut the whole mountain face away.
        with_difficulty: also return a uint8 grid of ``piste:difficulty`` classes,
            indexed into :data:`SKI_DIFFICULTY_CLASSES` (0 = untagged). Display
            only, like the area mask — it carries no relief.

    Returns:
        Float32 array of shape (dim, dim), zero away from trails. Each optional
        flag appends one more grid, in this order::

            (relief,)
            (relief, area)              with_areas
            (relief, difficulty)        with_difficulty
            (relief, area, difficulty)  both

        The area mask is 1.0 inside an areal feature and 0.0 elsewhere. The
        difficulty grid is a class index rather than a quantity, so anything that
        resamples it must do so with nearest-neighbour.
    """
    try:
        from shapely.geometry import shape
    except ImportError:
        logger.warning("shapely not installed for trail rasterization")
        return _blank(dim, with_areas, with_difficulty)

    west, south, east, north = bbox

    # Approximate metres per pixel; good enough for a width floor.
    pixel_size_lon_m = (east - west) * M_PER_DEG_LON_EQ / max(dim, 1)
    pixel_size_lat_m = (north - south) * M_PER_DEG_LAT / max(dim, 1)
    pixel_size_m = (pixel_size_lon_m + pixel_size_lat_m) / 2.0

    half_width_m = max(width_m, pixel_size_m * 2.0) / 2.0
    buffer_deg = half_width_m / M_PER_DEG_LAT

    shapes = []
    area_shapes = []
    difficulty_shapes = []
    for feature in geojson.get("features", []):
        try:
            geom = shape(feature["geometry"])
        except Exception as e:  # malformed geometry in a 20k-feature response
            logger.debug(f"Skipping trail feature: {e}")
            continue
        if geom.is_empty:
            continue
        difficulty = _difficulty_class(feature) if with_difficulty else 0
        if geom.geom_type in ("Polygon", "MultiPolygon"):
            # A piste or path mapped as a closed way is an *area* whose edge is
            # the trail; buffering the polygon itself would engrave its entire
            # interior and turn a ski run into a solid blob the size of the
            # mountain face. The interior goes to the display-only area mask and
            # the boundary carries on as linework of the requested width.
            if with_areas:
                area_shapes.append(geom)
            geom = geom.boundary
        if geom.is_empty:
            continue
        if geom.geom_type in ("LineString", "MultiLineString",
                              "LinearRing", "GeometryCollection"):
            buffered = geom.buffer(buffer_deg)
            shapes.append(buffered)
            if difficulty:
                difficulty_shapes.append((difficulty, buffered))

    if not shapes and not area_shapes:
        return _blank(dim, with_areas, with_difficulty)

    bounds = (west, south, east, north)

    def _burn(items, value):
        # Every shape carries the same value, so overlapping features paint the
        # identical number ("set"); "sum" would stack relief where trails cross.
        if not items:
            return np.zeros((dim, dim), dtype=np.float32)
        try:
            return burn_polygons(items, (dim, dim), bounds=bounds, values=value,
                                 mode="set", dtype=np.float32)
        except Exception as e:
            logger.error(f"Trail rasterization failed: {e}")
            return np.zeros((dim, dim), dtype=np.float32)

    grid = _burn(shapes, relief_m)

    logger.info(f"Rasterized {len(shapes)} trail features to {dim}x{dim} "
                f"(width {half_width_m * 2:.0f} m)"
                + (f", {len(area_shapes)} areal" if area_shapes else "")
                + (f", {len(difficulty_shapes)} graded" if difficulty_shapes else ""))

    out = [grid]
    if with_areas:
        out.append(_burn(area_shapes, 1.0))
    if with_difficulty:
        out.append(_burn_difficulty(difficulty_shapes, dim, bounds))
    return out[0] if len(out) == 1 else tuple(out)


def _difficulty_class(feature: dict) -> int:
    """Map a feature's ``piste:difficulty`` tag to its class index, 0 if absent."""
    props = feature.get("properties") or {}
    raw = props.get("piste:difficulty")
    if not isinstance(raw, str):
        return 0
    return _DIFFICULTY_INDEX.get(raw.strip().lower(), 0)


def _burn_difficulty(items, dim: int, bounds) -> np.ndarray:
    """Rasterize difficulty class indices; the hardest wins any overlap ("max").

    Kept apart from ``_burn`` because this grid is categorical: it is burned as
    uint8 and must never be interpolated, whereas the relief and area grids are
    float32 and resample normally.
    """
    if not items:
        return np.zeros((dim, dim), dtype=np.uint8)
    try:
        return burn_polygons([geom for _, geom in items], (dim, dim), bounds=bounds,
                             values=[cls for cls, _ in items], mode="max", dtype=np.uint8)
    except Exception as e:
        logger.error(f"Trail difficulty rasterization failed: {e}")
        return np.zeros((dim, dim), dtype=np.uint8)


def _blank(dim: int, with_areas: bool, with_difficulty: bool = False):
    """Zero grids, shaped to match whichever return form the caller asked for."""
    out = [np.zeros((dim, dim), dtype=np.float32)]
    if with_areas:
        out.append(np.zeros((dim, dim), dtype=np.float32))
    if with_difficulty:
        out.append(np.zeros((dim, dim), dtype=np.uint8))
    return out[0] if len(out) == 1 else tuple(out)


def _empty_fc() -> dict:
    return {"type": "FeatureCollection", "features": []}


def _line_features(gdf, keep_cols: list[str]) -> dict:
    """Convert a GeoDataFrame to GeoJSON, keeping only linear/areal geometry."""
    import json as _json

    supported = {"LineString", "MultiLineString", "Polygon", "MultiPolygon"}
    gdf = gdf[gdf.geometry.notna()]
    gdf = gdf[gdf.geometry.geom_type.isin(supported)].reset_index(drop=True)
    if not len(gdf):
        return _empty_fc()
    cols = ["geometry"] + [c for c in keep_cols if c in gdf.columns]
    return _json.loads(gdf[cols].to_json())


# ---------------------------------------------------------------------------
# OpenStreetMap provider
# ---------------------------------------------------------------------------

class OsmTrailsLayer(TrailsLayerBase):
    """OSM/Overpass provider — global, serves both categories."""

    name = "osm"
    categories = CATEGORIES

    def fetch(self, north, south, east, west, categories=CATEGORIES):
        try:
            import osmnx as ox
        except ImportError:
            logger.warning("osmnx is not installed; OSM trails unavailable")
            return {}

        endpoints = self._endpoints()

        # osmnx 2.x bbox order: (west, south, east, north)
        bbox = (west, south, east, north)
        out: dict[str, dict] = {}
        failures: list[str] = []

        # A mirror that answers its status probe can still 502 every query, so
        # selecting one is not the same as it working. Run the whole set against
        # each mirror in turn and only accept the result when nothing failed at
        # the transport level.
        for attempt, endpoint in enumerate(endpoints):
            if endpoint is not None:
                use_overpass_endpoint(ox, endpoint, _OVERPASS_REQUEST_TIMEOUT_S)

            out = {}
            failures = []
            if "ski" in categories:
                out["ski"], err = self._fetch_tags(
                    ox, bbox, OSM_SKI_TAGS,
                    ["piste:type", "piste:difficulty", "name", "route"], "ski")
                if err:
                    failures.append(err)
            if "hiking" in categories:
                out["hiking"], err = self._fetch_tags(
                    ox, bbox, OSM_HIKING_TAGS,
                    ["highway", "sac_scale", "trail_visibility", "name", "route"],
                    "hiking")
                if err:
                    failures.append(err)

            if not failures:
                return out
            remaining = len(endpoints) - attempt - 1
            logger.warning(
                "Overpass %s could not serve trails (%s); %d mirror(s) left",
                endpoint, "; ".join(failures), remaining)

        raise TrailsUpstreamError(
            f"Overpass is unreachable (tried {len(endpoints):d} mirror(s)): "
            f"{'; '.join(failures) or 'no healthy mirror'}")

    @staticmethod
    def _endpoints() -> list[str | None]:
        """Overpass mirrors to try, in preference order.

        Uses the shared health probe (``geo2stl.osm``), which requests
        ``/status`` and checks the status code. The probe this replaced was a bare
        ``requests.head`` on the base URL that accepted anything which did not
        raise - and ``head`` does not raise on a 502, so a mirror that was up but
        broken was selected while healthy ones sat untried. It also assigned
        ``overpass_url`` before testing, leaving it pointed at the last dead
        mirror once the loop ran out.

        Returns ``[None]`` when the probe is unavailable or every mirror is down,
        which means "use whatever osmnx is configured with and try once".
        """
        from geo2stl.osm import healthy_overpass_endpoints
        healthy = healthy_overpass_endpoints()
        return list(healthy) if healthy else [None]

    @staticmethod
    def _fetch_tags(ox, bbox, tags, keep_cols,
                    label) -> tuple[dict, str | None]:
        """Fetch one category. Returns ``(feature_collection, error_or_None)``.

        An empty result and a failed request are different answers and must not
        share a return value. osmnx raises ``InsufficientResponseError`` when the
        query matched nothing, which is genuinely empty; anything else - a 502
        from the mirror, a connect timeout - is a failure the caller has to be
        able to retry on another mirror.
        """
        try:
            from osmnx._errors import InsufficientResponseError
        except Exception:  # pragma: no cover - osmnx layout change
            InsufficientResponseError = ()

        try:
            gdf = ox.features_from_bbox(bbox, tags=tags)
        except InsufficientResponseError as e:
            logger.info(f"OSM {label} trails: none in region ({e})")
            return _empty_fc(), None
        except Exception as e:
            logger.warning(f"OSM {label} trails: request failed ({e})")
            return _empty_fc(), f"{label}: {e}"
        return _line_features(gdf, keep_cols), None


# ---------------------------------------------------------------------------
# USDA Forest Service provider
# ---------------------------------------------------------------------------

class UsfsTrailsLayer(TrailsLayerBase):
    """USDA Forest Service EDW trails — hiking only, US national forest land.

    Independent of OSM (surveyed by the agency), so it fills in trails that were
    never mapped by contributors. Returns nothing outside the US bbox.
    """

    name = "usfs"
    categories = ("hiking",)

    def fetch(self, north, south, east, west, categories=CATEGORIES):
        if "hiking" not in categories:
            return {}

        w, s, e, n = _USFS_BBOX
        if east < w or west > e or north < s or south > n:
            return {"hiking": _empty_fc()}

        try:
            import requests
        except ImportError:
            return {"hiking": _empty_fc()}

        params = {
            "f": "geojson",
            "geometry": f"{west},{south},{east},{north}",
            "geometryType": "esriGeometryEnvelope",
            "spatialRel": "esriSpatialRelIntersects",
            "inSR": "4326",
            "outSR": "4326",
            "outFields": "TRAIL_NAME,TRAIL_TYPE",
            "returnGeometry": "true",
            "where": "1=1",
        }
        try:
            resp = requests.get(USFS_TRAILS_URL, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.info(f"USFS trails unavailable: {e}")
            return {"hiking": _empty_fc()}

        if not isinstance(data, dict) or "features" not in data:
            # ArcGIS reports errors as 200 + {"error": {...}}.
            logger.info(f"USFS trails returned no features: {str(data)[:200]}")
            return {"hiking": _empty_fc()}
        return {"hiking": data}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class TrailsService:
    """Trails orchestrator: fetch from one or all providers, then rasterize."""

    name = "trails"

    def __init__(self):
        self.providers = {
            "osm": OsmTrailsLayer(),
            "usfs": UsfsTrailsLayer(),
        }

    def _providers_for(self, source: str) -> list[TrailsLayerBase]:
        if source == "all":
            return list(self.providers.values())
        provider = self.providers.get(source)
        return [provider] if provider else [self.providers["osm"]]

    def fetch_and_rasterize(
        self,
        north: float,
        south: float,
        east: float,
        west: float,
        dim: int,
        relief_m: float = -2.0,
        width_m: float = 8.0,
        source: str = "all",
        categories: tuple[str, ...] = CATEGORIES,
    ) -> dict | None:
        """Fetch every requested category and rasterize each to its own grid.

        Returns a dict with ``ski_grid``, ``hiking_grid``, the display-only
        ``*_area_grid`` masks, a ``ski_difficulty_grid`` of piste grades,
        per-category feature counts, and the provider names that contributed, or
        ``None`` when no provider returned a single feature.
        """
        categories = tuple(c for c in categories if c in CATEGORIES) or CATEGORIES
        collected: dict[str, list[dict]] = {c: [] for c in categories}
        used: list[str] = []
        upstream_errors: list[str] = []

        for provider in self._providers_for(source):
            wanted = tuple(c for c in categories if c in provider.categories)
            if not wanted:
                continue
            try:
                result = provider.fetch(north, south, east, west, wanted)
            except TrailsUpstreamError as e:
                # Kept, not swallowed: if no provider ends up returning a single
                # feature, this is why, and the caller has to be able to say so
                # rather than reporting an empty region.
                logger.warning(f"Trails provider {provider.name} unreachable: {e}")
                upstream_errors.append(f"{provider.name}: {e}")
                continue
            except Exception as e:
                logger.warning(f"Trails provider {provider.name} failed: {e}")
                upstream_errors.append(f"{provider.name}: {e}")
                continue
            got = 0
            for category, fc in (result or {}).items():
                feats = (fc or {}).get("features") or []
                if feats:
                    collected.setdefault(category, []).append(fc)
                    got += len(feats)
            if got:
                used.append(provider.name)

        grids: dict[str, np.ndarray] = {}
        areas: dict[str, np.ndarray] = {}
        counts: dict[str, int] = {}
        # Only pistes carry a difficulty grade; hiking paths have no equivalent
        # tag, so asking for one would burn an all-zero grid for nothing.
        ski_difficulty = np.zeros((dim, dim), dtype=np.uint8)
        for category in CATEGORIES:
            features: list[dict] = []
            for fc in collected.get(category, []):
                features.extend(fc.get("features") or [])
            counts[category] = len(features)
            if not features:
                grids[category] = np.zeros((dim, dim), dtype=np.float32)
                areas[category] = np.zeros((dim, dim), dtype=np.float32)
                continue
            want_difficulty = category == "ski"
            rasterized = rasterize_trails(
                {"type": "FeatureCollection", "features": features},
                (west, south, east, north), dim,
                relief_m=relief_m, width_m=width_m, with_areas=True,
                with_difficulty=want_difficulty)
            if want_difficulty:
                grids[category], areas[category], ski_difficulty = rasterized
            else:
                grids[category], areas[category] = rasterized

        if not any(counts.values()):
            if upstream_errors:
                raise TrailsUpstreamError("; ".join(upstream_errors))
            logger.info("Trails: no features in region")
            return None

        return {
            "ski_grid": grids["ski"],
            "hiking_grid": grids["hiking"],
            # Display-only interior masks for areal features; never merged into
            # the DEM, so a ski-area polygon cannot engrave the terrain it spans.
            "ski_area_grid": areas["ski"],
            "hiking_area_grid": areas["hiking"],
            # Piste grade per pixel, indexed into SKI_DIFFICULTY_CLASSES.
            "ski_difficulty_grid": ski_difficulty,
            "ski_count": counts["ski"],
            "hiking_count": counts["hiking"],
            "feature_count": sum(counts.values()),
            "sources": used,
            "source": source,
        }


TRAILS_LAYER = TrailsService()


def fetch_and_rasterize_trails(
    north, south, east, west, dim,
    relief_m=-2.0, width_m=8.0, source="all", categories=CATEGORIES,
):
    """Fetch trails and rasterize per category. Sync — call via run_in_executor.

    source='osm':  OpenStreetMap, global, ski + hiking
    source='usfs': USDA Forest Service, US national forest land, hiking only
    source='all':  union of the above (default)

    Returns the dict described in :meth:`TrailsService.fetch_and_rasterize`, or
    None when no provider found any feature.
    """
    import time as _time

    t0 = _time.perf_counter()
    try:
        result = TRAILS_LAYER.fetch_and_rasterize(
            north, south, east, west, dim,
            relief_m=relief_m, width_m=width_m,
            source=source, categories=categories)
        if result is None:
            return None
        logger.info(
            "Trails total: %.2fs, %d ski + %d hiking via %s",
            _time.perf_counter() - t0,
            result["ski_count"], result["hiking_count"],
            ", ".join(result["sources"]) or source)
        return result
    except Exception as e:
        logger.error(f"Trails fetch/rasterize failed: {e}", exc_info=True)
        return None


def merge_trails_with_dem(dem: np.ndarray, trails: np.ndarray) -> np.ndarray:
    """Apply a trail relief grid to a DEM.

    Negative relief is engraved with an element-wise minimum (a groove cannot be
    filled in by a neighbouring trail), positive relief is added on top.
    """
    if dem is None or trails is None or dem.shape != trails.shape:
        return dem
    out = dem.astype(np.float32, copy=True)
    carve = trails < 0
    raise_ = trails > 0
    out[carve] = np.minimum(out[carve], out[carve] + trails[carve])
    out[raise_] = out[raise_] + trails[raise_]
    return out
