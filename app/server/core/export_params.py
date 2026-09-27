"""
core/export_params.py — Export parameter parsing and DEM cache resolution.

Extracts, validates, and type-casts export parameters from client requests.
Supports both inline DEM arrays and cache-based DEM resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


def resolve_dem(data: dict) -> tuple[list, int, int] | None:
    """Find the DEM an export refers to, without the client resending the array.

    Preferred: ``dem_id``, the handle /api/terrain/dem returned; it names the
    exact grid the user saw (see core/dem_store.py) and raises DemGone if it
    has expired. Fallback while clients migrate: rebuild the disk-cache key from
    bbox + DEM settings, which misses if any setting disagrees.

    Returns ``(dem_values_list, height, width)``, or ``None`` on a cache miss.
    """
    from app.server.core.cache import read_array_cache
    from app.server.core.dem_cache import dem_cache_key
    from app.server.core.dem_store import DemGone, dem_store

    gone = None
    if data.get("dem_id"):
        try:
            grid, _ = dem_store.get(data["dem_id"])
            h, w = grid.shape
            return grid.ravel().tolist(), h, w
        except DemGone as exc:
            # Handles do not survive a restart; the disk cache does. Try it
            # before telling the user to reload.
            gone = exc

    bbox = data.get("bbox") or data
    north = bbox.get("north")
    south = bbox.get("south")
    east  = bbox.get("east")
    west  = bbox.get("west")
    if None in (north, south, east, west):
        if gone is not None:
            raise gone
        return None

    # The key and its defaults live in core/dem_cache.py, which the terrain
    # router also calls when it writes the entry. Reconstructing the key here
    # by hand is what made this path fail twice before; do not reintroduce a
    # local copy of the field list or the defaults.
    dem = data.get("dem") or data
    cache_key = dem_cache_key(north, south, east, west, dem)

    cached = read_array_cache("dem", cache_key)
    if cached is None or cached[0].get("dem") is None:
        logger.debug("DEM cache miss for export (key %s)", cache_key[:8])
        if gone is not None:
            raise gone
        return None

    dem_arr = cached[0]["dem"]  # np.ndarray (H, W), raw Plate Carree

    # The cache holds the grid before projection (projection is deliberately
    # not in the key, see core/dem_cache.py), and /api/terrain/dem projects it
    # on the way out. Returning it as-is meant the mesh and every export were
    # built from the unprojected grid - a different shape from the map the user
    # was looking at whenever a projection was selected, which is the default.
    projection = dem.get("projection") or "none"
    if projection != "none":
        from geo2stl.projections import project_grid
        clip = dem.get("clip_valid_region", dem.get("clip_nans", True))
        dem_arr = project_grid(
            dem_arr.astype("float32"), north, south, east, west, projection,
            bool(clip), categorical=False,
            maintain_dimensions=bool(dem.get("maintain_dimensions", False)),
        )

    h, w = dem_arr.shape
    logger.info("DEM resolved from cache for export (key %s, %dx%d, %s)",
                cache_key[:8], w, h, projection)
    return dem_arr.ravel().tolist(), h, w


# Composite sources that rasterise OSM features. Meshes get those features from
# the vector stage (city2stl.city_model); baked into the terrain as well, every
# building would print twice, once as a pixel block. They stay available for
# the 2D composite preview and ML rasters.
FEATURE_SOURCES = frozenset({"osm_buildings", "osm_roads", "osm_waterways", "osm_walls"})


def mesh_composite_layers(layers: list | None) -> list | None:
    """The terrain-stage part of a composite spec (feature channels removed)."""
    kept = [spec for spec in (layers or [])
            if (spec.get("source") if isinstance(spec, dict) else getattr(spec, "source", None))
            not in FEATURE_SOURCES]
    return kept or None


@dataclass
class ExportContext:
    """Typed container for parsed export parameters.

    Replaces the raw dict returned by _parse_export_params, giving IDE
    autocompletion and catching typos at attribute-access time.
    """
    dem_values: list[float]
    height: int
    width: int
    model_height: float = 20.0
    base_height: float = 5.0
    exaggeration: float = 1.0
    sea_level_cap: bool = False
    name: str = "terrain"
    # Horizontal scale: 1 DEM pixel → mm_per_pixel mm in the printed model.
    # Default 1.0 means "1 px = 1 mm" — i.e. an N×M DEM produces an N×M mm STL.
    mm_per_pixel: float = 1.0
    # Optional composite layer spec — when present the server runs the merge
    # pipeline before scaling/extrusion so the 3D model matches what the user
    # configured in the Composite tab.
    composite_layers: list | None = None
    composite_dim: int | None = None
    bbox: dict | None = None
    # Set when a composite spec was sent but could not be built. The request
    # still succeeds on the plain DEM, so callers must surface this; a model
    # that silently lacks the configured layers looks like a correct one.
    composite_error: str | None = None
    # Vertical scale: "auto" (true scale under 20 km diagonal, fit above), "true", "fit".
    z_mode: str = "auto"
    median_size: int = 3
    # Terrain-relative carve (rivers, lakes; negative m, same grid as dem_values)
    # from the composite spec. Kept out of dem_values so the terrain stage can
    # add it after the median filter, which would erase a one-pixel channel.
    carve_m: Any = None

    @classmethod
    def from_request(cls, data: dict) -> ExportContext:
        """Construct from an incoming request dict.

        The DEM comes from, in priority order: a composite spec, an inline
        ``dem_values`` array (edited grids), or :func:`resolve_dem` (``dem_id``
        handle, else bbox + settings).
        """
        dem_values = data.get("dem_values", [])
        height = data.get("height", 0)
        width = data.get("width", 0)

        # Composite mode (highest priority): rebuild the DEM from the merge spec
        # so the 3D output reflects the user's Composite-tab configuration.
        composite_layers = mesh_composite_layers(data.get("composite_layers"))
        composite_error = None
        carve_m = None
        if composite_layers and data.get("bbox"):
            try:
                # Lazy import — avoids circular deps with the composite router.
                from app.server.routers.composite import compute_composite_dem
                dem_settings = data.get("dem") or {}
                dim = int(data.get("composite_dim") or dem_settings.get("dim") or 600)
                composite, carve = compute_composite_dem(
                    data["bbox"], dim, composite_layers,
                    projection=dem_settings.get("projection") or "none",
                    clip_nans=bool(dem_settings.get("clip_nans", True)),
                    maintain_dimensions=bool(
                        dem_settings.get("maintain_dimensions", False)),
                    split_carve=True,
                )
                dem_values = composite.flatten().tolist()
                height, width = composite.shape
                carve_m = carve if carve.any() else None
            except Exception as exc:
                logger.exception("Composite resolve failed; falling back: %s", exc)
                composite_error = f"{type(exc).__name__}: {exc}"[:300]

        # Settings-only mode: resolve DEM from cache
        if not dem_values:
            resolved = resolve_dem(data)
            if resolved is not None:
                dem_values, height, width = resolved

        bbox = data.get("bbox") or None
        if not bbox and data.get("dem_id"):
            from app.server.core.dem_store import DemGone, dem_store
            try:
                bbox = dem_store.get(data["dem_id"])[1]
            except DemGone:
                bbox = None

        return cls(
            dem_values=dem_values,
            height=height,
            width=width,
            model_height=float(data.get("model_height", 20)),
            base_height=float(data.get("base_height", 5)),
            exaggeration=float(data.get("exaggeration", 1.0)),
            sea_level_cap=bool(data.get("sea_level_cap", False)),
            name=data.get("name", "terrain"),
            mm_per_pixel=float(data.get("mm_per_pixel", 1.0)),
            composite_layers=composite_layers,
            composite_dim=int(data["composite_dim"]) if data.get("composite_dim") else None,
            bbox=bbox,
            composite_error=composite_error,
            z_mode=str(data.get("z_mode", "auto")),
            median_size=int(data.get("median_size", 3)),
            carve_m=carve_m,
        )


def _parse_export_params(data: dict) -> ExportContext:
    """Extract and type-cast the common export parameters from a request dict."""
    return ExportContext.from_request(data)
