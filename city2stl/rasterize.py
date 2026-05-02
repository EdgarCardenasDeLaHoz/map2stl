"""
city2stl/rasterize.py — Rasterize OSM vector features onto a height-map grid.

Provides pure-computation helpers that burn building, road, and waterway
GeoJSON features onto a float32 numpy grid. No HTTP, cache, or server deps.

Server entry point: app.server.core.osm re-exports all public symbols.
"""

from __future__ import annotations

import logging
import math

import numpy as np

logger = logging.getLogger(__name__)


def _count_verts(g) -> int:
    """Count total exterior vertices in a geometry (for simplification logging)."""
    if g.geom_type == "LineString":
        return len(g.coords)
    if g.geom_type == "MultiLineString":
        return sum(len(l.coords) for l in g.geoms)
    if g.geom_type == "Polygon":
        return len(g.exterior.coords)
    if g.geom_type == "MultiPolygon":
        return sum(len(p.exterior.coords) for p in g.geoms)
    return 0


def _empty_fc(error: str = "") -> dict:
    fc: dict = {"type": "FeatureCollection", "features": []}
    if error:
        fc["error"] = error
    return fc


# ─── Roof painters ────────────────────────────────────────────────────────────
#
# Each painter takes a boolean footprint mask in raster space and the
# eaves/ridge heights, and returns a float32 height array shaped like the
# mask. NaN where the mask is False; finite metres where True.
#
# The shapes follow the same definitions used by city2stl/mesh.py's
# _extrude_ring_with_roof, so the 2D heightmap and the 3D extrusion agree.

_MIN_ROOF_PIXELS = 9  # mask.sum() below this falls back to a flat top


def _principal_axis_deg(mask: np.ndarray) -> float:
    """PCA principal axis (degrees CCW from +x) of the True pixels in mask."""
    ys, xs = np.nonzero(mask)
    if xs.size < 2:
        return 0.0
    pts = np.column_stack([xs.astype(np.float32), ys.astype(np.float32)])
    pts -= pts.mean(axis=0)
    cov = np.cov(pts, rowvar=False)
    try:
        evals, evecs = np.linalg.eigh(cov)
    except np.linalg.LinAlgError:
        return 0.0
    v = evecs[:, np.argmax(evals)]
    return float(np.degrees(np.arctan2(v[1], v[0])))


def _direction_tag_to_deg(value) -> float | None:
    """Parse OSM ``roof:direction`` tag (compass: '90', 'N', 'NNE', ...) into deg CCW from +x."""
    if value is None:
        return None
    s = str(value).strip().upper()
    if not s:
        return None
    # Numeric compass bearing (clockwise from N)
    try:
        bearing = float(s)
    except ValueError:
        cardinals = {
            "N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5,
            "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
            "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5,
            "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5,
        }
        if s not in cardinals:
            return None
        bearing = cardinals[s]
    # Compass bearing → math angle (CCW from +x). Image y is flipped vs. north.
    # roof:direction points "up the slope" toward the ridge for skillion;
    # we treat it as the eaves-to-ridge direction in raster pixel coords.
    angle = 90.0 - bearing  # bearing=0 (N, up) → 90° (raster -y in image)
    return angle


def _flat_surface(mask: np.ndarray, eaves_h: float, roof_h: float) -> np.ndarray:
    out = np.full(mask.shape, np.nan, dtype=np.float32)
    out[mask] = eaves_h + roof_h
    return out


def _gabled_surface(mask: np.ndarray, eaves_h: float, roof_h: float,
                    axis_deg: float) -> np.ndarray:
    """Symmetric gable: ridge along axis_deg, eaves at the perpendicular extremes."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.full(mask.shape, np.nan, dtype=np.float32)

    cx = float(xs.mean())
    cy = float(ys.mean())
    # Perpendicular to ridge: rotate axis by 90° → (-sin, cos)
    theta = math.radians(axis_deg)
    nx = -math.sin(theta)
    ny = math.cos(theta)

    # Project pixel offset onto perpendicular axis
    dx = xs.astype(np.float32) - cx
    dy = ys.astype(np.float32) - cy
    perp = dx * nx + dy * ny
    max_abs = float(np.max(np.abs(perp))) or 1.0
    # 1.0 at eaves edge, 0.0 at ridge
    d_norm = np.abs(perp) / max_abs
    z = eaves_h + roof_h * (1.0 - d_norm)

    out = np.full(mask.shape, np.nan, dtype=np.float32)
    out[ys, xs] = z.astype(np.float32)
    return out


def _pyramidal_surface(mask: np.ndarray, eaves_h: float, roof_h: float) -> np.ndarray:
    """Pyramid / square hip: peak at centroid, linear falloff to footprint edge."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.full(mask.shape, np.nan, dtype=np.float32)
    cx = float(xs.mean())
    cy = float(ys.mean())
    dx = xs.astype(np.float32) - cx
    dy = ys.astype(np.float32) - cy
    r = np.sqrt(dx * dx + dy * dy)
    rmax = float(r.max()) or 1.0
    z = eaves_h + roof_h * (1.0 - r / rmax)
    out = np.full(mask.shape, np.nan, dtype=np.float32)
    out[ys, xs] = z.astype(np.float32)
    return out


def _skillion_surface(mask: np.ndarray, eaves_h: float, roof_h: float,
                      axis_deg: float) -> np.ndarray:
    """Single-pitch ramp from eaves on one side to peak on the opposite side."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.full(mask.shape, np.nan, dtype=np.float32)
    cx = float(xs.mean())
    cy = float(ys.mean())
    theta = math.radians(axis_deg)
    ux = math.cos(theta)
    uy = math.sin(theta)
    dx = xs.astype(np.float32) - cx
    dy = ys.astype(np.float32) - cy
    proj = dx * ux + dy * uy
    pmin = float(proj.min())
    pmax = float(proj.max())
    span = max(pmax - pmin, 1.0)
    t = (proj - pmin) / span  # 0..1, low side = eaves, high side = peak
    z = eaves_h + roof_h * t
    out = np.full(mask.shape, np.nan, dtype=np.float32)
    out[ys, xs] = z.astype(np.float32)
    return out


def _hipped_surface(mask: np.ndarray, eaves_h: float, roof_h: float) -> np.ndarray:
    """Hipped roof via distance-transform: ridge runs along the medial axis.

    Result naturally produces hip lines at the corners. Falls back to gabled-like
    when the polygon is highly elongated (medial axis collapses to a line).
    """
    try:
        from scipy.ndimage import distance_transform_edt
    except Exception:
        # No scipy → emulate with simple distance-to-boundary using gabled fallback
        return _gabled_surface(mask, eaves_h, roof_h, _principal_axis_deg(mask))
    dt = distance_transform_edt(mask).astype(np.float32)
    if not np.any(mask):
        return np.full(mask.shape, np.nan, dtype=np.float32)
    dmax = float(dt[mask].max()) or 1.0
    # Normalised: 0 at eaves edge, 1 at ridge spine
    norm = np.clip(dt / dmax, 0.0, 1.0)
    z = eaves_h + roof_h * norm
    out = np.full(mask.shape, np.nan, dtype=np.float32)
    out[mask] = z[mask]
    return out


def _dome_surface(mask: np.ndarray, eaves_h: float, roof_h: float) -> np.ndarray:
    """Spherical-cap dome over footprint."""
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        return np.full(mask.shape, np.nan, dtype=np.float32)
    cx = float(xs.mean())
    cy = float(ys.mean())
    dx = xs.astype(np.float32) - cx
    dy = ys.astype(np.float32) - cy
    r = np.sqrt(dx * dx + dy * dy)
    rmax = float(r.max()) or 1.0
    rn = r / rmax
    z = eaves_h + roof_h * np.sqrt(np.clip(1.0 - rn * rn, 0.0, 1.0))
    out = np.full(mask.shape, np.nan, dtype=np.float32)
    out[ys, xs] = z.astype(np.float32)
    return out


def _paint_building_roof(
    mask: np.ndarray,
    eaves_h: float,
    roof_h: float,
    shape: str,
    axis_deg: float | None = None,
) -> np.ndarray:
    """Return a per-pixel height surface for a building footprint.

    Args:
        mask:     (H, W) boolean — True inside the building footprint.
        eaves_h:  Height (metres) at the eaves edge.
        roof_h:   Additional rise (metres) from eaves to ridge. May be 0.
        shape:    One of 'flat', 'gabled', 'hipped', 'pyramidal', 'skillion', 'dome'.
        axis_deg: Optional ridge-direction in degrees CCW from +x. None → PCA principal axis.

    Returns:
        (H, W) float32 array. NaN outside the footprint, finite metres inside.
        Buildings smaller than _MIN_ROOF_PIXELS pixels always fall back to flat.
    """
    if not np.any(mask):
        return np.full(mask.shape, np.nan, dtype=np.float32)
    if mask.sum() < _MIN_ROOF_PIXELS or roof_h <= 0:
        return _flat_surface(mask, eaves_h, roof_h)

    s = (shape or "flat").strip().lower()
    if s in ("flat", "skeleton", "raised", ""):
        return _flat_surface(mask, eaves_h, roof_h)
    if s == "pyramidal" or s == "pyramid":
        return _pyramidal_surface(mask, eaves_h, roof_h)
    if s == "dome":
        return _dome_surface(mask, eaves_h, roof_h)
    if s == "hipped" or s == "half-hipped":
        return _hipped_surface(mask, eaves_h, roof_h)

    # Gabled, skillion, mansard, gambrel — all benefit from a ridge axis
    if axis_deg is None:
        axis_deg = _principal_axis_deg(mask)

    if s == "skillion" or s == "mono-pitched" or s == "shed":
        return _skillion_surface(mask, eaves_h, roof_h, axis_deg)
    if s in ("gabled", "gable", "saltbox", "mansard", "gambrel", "round"):
        return _gabled_surface(mask, eaves_h, roof_h, axis_deg)

    # Unknown shape → flat
    return _flat_surface(mask, eaves_h, roof_h)


def rasterize_city_data(
    north: float, south: float, east: float, west: float,
    dim: int,
    buildings_geojson: dict,
    roads_geojson: dict,
    waterways_geojson: dict,
    building_scale: float = 1.0,
    road_depression_m: float = 0.0,
    water_depression_m: float = -2.0,
    roof_shapes: bool = False,
) -> dict:
    """
    Burn OSM vector features onto a dim x dim float32 height-map grid.

    Layer order (painter's algorithm -- later layers overwrite earlier ones):
      1. waterways  -- polygons/lines burned at water_depression_m
      2. roads      -- lines buffered to road_width_m, burned at road_depression_m
      3. buildings  -- polygons burned at height_m * building_scale (np.maximum so tall wins)

    Returns a dict compatible with the DEM response format:
      { values: [float, ...], width, height, vmin, vmax, bbox }
    """
    from rasterio.transform import from_bounds
    from rasterio.enums import MergeAlg
    from rasterio.features import rasterize as _rasterize
    from shapely.geometry import shape, mapping

    transform = from_bounds(west, south, east, north, dim, dim)
    grid = np.zeros((dim, dim), dtype=np.float32)

    # -- Waterways --------------------------------------------------------
    water_shapes = []
    for feat in (waterways_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            s = shape(geom)
            # Buffer lines to give them 1-pixel minimum width in degree units
            if s.geom_type in ("LineString", "MultiLineString"):
                pixel_deg = (north - south) / dim
                s = s.buffer(pixel_deg * 0.5)
            if not s.is_empty:
                water_shapes.append((mapping(s), water_depression_m))
        except Exception:
            continue
    if water_shapes:
        try:
            _rasterize(water_shapes, out=grid, transform=transform,
                       merge_alg=MergeAlg.replace, dtype="float32")
        except Exception as e:
            logger.warning(f"rasterize waterways failed: {e}")

    # -- Roads ------------------------------------------------------------
    road_shapes = []
    for feat in (roads_geojson.get("features") or []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            width_m = (feat.get("properties") or {}).get("road_width_m", 4.0)
            # Convert metres to degrees (approximate at this latitude)
            mid_lat = (north + south) / 2
            metres_per_deg_lon = 111_000.0 * math.cos(math.radians(mid_lat))
            buf_deg = (width_m / 2) / metres_per_deg_lon
            s = shape(geom).buffer(max(buf_deg, (north - south) / dim * 0.5))
            if not s.is_empty:
                road_shapes.append((mapping(s), road_depression_m))
        except Exception:
            continue
    if road_shapes:
        try:
            _rasterize(road_shapes, out=grid, transform=transform,
                       merge_alg=MergeAlg.replace, dtype="float32")
        except Exception as e:
            logger.warning(f"rasterize roads failed: {e}")

    # -- Buildings --------------------------------------------------------
    # Burn each building separately and take the maximum so tall buildings
    # win over adjacent shorter ones (can't batch because each has a different value).
    if not roof_shapes:
        # Original flat-top fast path: collect all (geom, height) pairs and
        # batch-rasterize, then merge with the existing grid via np.maximum.
        building_shapes = []
        for feat in (buildings_geojson.get("features") or []):
            geom = feat.get("geometry")
            if not geom:
                continue
            try:
                h = float((feat.get("properties") or {}).get("height_m", 10.0)) * building_scale
                building_shapes.append((mapping(shape(geom)), h))
            except Exception:
                continue
        if building_shapes:
            building_shapes.sort(key=lambda x: x[1])
            try:
                building_grid = _rasterize(
                    building_shapes, out_shape=(dim, dim), transform=transform,
                    fill=0, dtype="float32", merge_alg=MergeAlg.replace,
                )
                np.maximum(grid, building_grid, out=grid)
            except Exception:
                for feat_shape, h in building_shapes:
                    try:
                        tmp = _rasterize(
                            [(feat_shape, h)], out_shape=(dim, dim),
                            transform=transform, fill=0, dtype="float32",
                        )
                        np.maximum(grid, tmp, out=grid)
                    except Exception:
                        continue
    else:
        # Roof-shaped path: per-building rasterize footprint mask, then paint a
        # roof-shaped surface into it using OSM tags. ~Nx slower for N buildings
        # since we can't batch — each surface is per-building.
        n_painted = 0
        n_flat = 0
        for feat in (buildings_geojson.get("features") or []):
            geom = feat.get("geometry")
            if not geom:
                continue
            props = feat.get("properties") or {}
            try:
                height_m = float(props.get("height_m", 10.0)) * building_scale
            except Exception:
                continue
            roof_h_tag = props.get("roof_height_m")
            try:
                roof_h = float(roof_h_tag) if roof_h_tag is not None else max(2.0, 0.3 * height_m)
            except Exception:
                roof_h = max(2.0, 0.3 * height_m)
            roof_h = min(roof_h, 0.5 * height_m)  # cap so eaves don't go below ground
            roof_h *= building_scale
            eaves_h = max(0.0, height_m - roof_h)
            shape_tag = (props.get("roof:shape") or props.get("roof_shape") or "flat")
            axis_deg = _direction_tag_to_deg(
                props.get("roof:direction") or props.get("roof_direction")
                or props.get("roof:orientation") or props.get("roof_orientation")
            )

            try:
                feat_shape = mapping(shape(geom))
                # Footprint mask = 1 inside, 0 outside
                mask = _rasterize(
                    [(feat_shape, 1)], out_shape=(dim, dim), transform=transform,
                    fill=0, dtype="uint8",
                ).astype(bool)
                if not mask.any():
                    continue
                surface = _paint_building_roof(
                    mask, eaves_h, roof_h, shape_tag, axis_deg,
                )
                # Combine: take max with existing grid, ignoring NaN
                surface_filled = np.where(np.isnan(surface), -np.inf, surface)
                np.maximum(grid, surface_filled, out=grid)
                if shape_tag and shape_tag.lower() not in ("flat", "", "skeleton", "raised"):
                    n_painted += 1
                else:
                    n_flat += 1
            except Exception as e:
                logger.debug(f"roof-shape rasterize failed for one building: {e}")
                continue
        # Replace any -inf (artefact of np.maximum on NaN-bridged arrays) with 0
        np.maximum(grid, 0.0, out=grid)
        logger.info(
            f"[rasterize] roof_shapes ON: {n_painted} shaped + {n_flat} flat buildings"
        )

    vmin = float(grid.min())
    vmax = float(grid.max())
    return {
        "values": grid.flatten().tolist(),
        "width": dim,
        "height": dim,
        "vmin": vmin,
        "vmax": vmax,
        "bbox": {"north": north, "south": south, "east": east, "west": west},
    }
