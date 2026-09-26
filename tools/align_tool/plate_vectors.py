"""The plate as polygons with heights, and those polygons back as a plate.

A plate raster and an OSM building layer hold the same physical quantity -- height above local
ground -- but in different forms: the plate is a grid, OSM is a list of footprints each carrying
a height.  Everything downstream of the fetch takes the OSM form.  `city2stl.mesh` extrudes it,
`city2stl.rasterize` burns it, the height estimators annotate it.  A plate that can be put into
that form can be run through all of it; and vectors that can be burnt back to a grid can be
compared against the plate they came from, which is the only way to know what the conversion
cost.

So this is a pair, and the pair is the point:

    features = heightmap_to_polygons(heights, cell_size_m=1.7, bbox=bbox)
    grid     = polygons_to_heightmap(features, heights.shape, cell_size_m=1.7, bbox=bbox)

`grid` should resemble `heights`.  `roundtrip_report` says by how much.

Two conventions are inherited rather than chosen, because the rest of the alignment tool already
keeps them.  NaN means "no building here", not zero -- a plate cell at ground level is not a
building of height nothing.  And a raster's row 0 is its SOUTH edge, matching `locate._rasterize`,
so latitudes ascend with the row index rather than descending as an image viewer would assume.

The shape of the output is a GeoJSON FeatureCollection whose properties are the ones
`city2stl.mesh.generate_city_3mf` and `city2stl.rasterize.rasterize_city_data` already read:
``height_m`` on every feature, and ``terrain_z``.  The latter is zero unless a terrain surface
is supplied, because the segmented plate raster measures height above local ground and carries no
absolute datum of its own; pass ``terrain`` to recover one.
"""
from __future__ import annotations

import warnings

import numpy as np

# A component smaller than this is a rasterisation artefact rather than a building: at the
# 1.7 m/px the monument plates use it is about seven pixels.
MIN_AREA_M2 = 20.0

# Douglas-Peucker tolerance. Half a metre keeps a right angle square while dropping the
# staircase a contour tracer leaves along a diagonal wall.
SIMPLIFY_M = 0.5


def _contours(mask: np.ndarray):
    """Outer rings and their holes, as pixel-coordinate arrays.

    OpenCV's two-level hierarchy is what makes holes recoverable: with ``RETR_CCOMP`` every
    contour is either an outer boundary or the boundary of a hole inside the one before it, and
    the fourth hierarchy column names that parent.  A courtyard therefore survives as a hole
    rather than being filled in, which matters for a palace built round one.
    """
    import cv2

    cnts, hier = cv2.findContours(mask.astype(np.uint8), cv2.RETR_CCOMP,
                                  cv2.CHAIN_APPROX_SIMPLE)
    if hier is None:
        return []
    hier = hier[0]
    out = []
    for i, c in enumerate(cnts):
        if hier[i][3] != -1:            # a hole; collected with its parent below
            continue
        holes = [cnts[j][:, 0, :] for j in range(len(cnts)) if hier[j][3] == i]
        out.append((c[:, 0, :], holes))
    return out


def _ring_to_polygon(outer, holes, simplify_px: float):
    """One traced ring and its holes as a valid shapely Polygon, or None."""
    from shapely.geometry import Polygon

    if len(outer) < 3:
        return None
    poly = Polygon(outer, [h for h in holes if len(h) >= 3])
    if simplify_px > 0:
        poly = poly.simplify(simplify_px, preserve_topology=True)
    if not poly.is_valid:
        poly = poly.buffer(0)
    if poly.is_empty or poly.geom_type not in ("Polygon", "MultiPolygon"):
        return None
    return poly


def _to_lonlat(poly, bbox, grid_shape):
    """Move a polygon from pixel coordinates into degrees.

    Row 0 is the south edge, so the latitude of a row ascends with the row index.  Getting this
    backwards produces a mirrored building layer that still looks plausible, which is why it is
    stated here rather than left to the caller.
    """
    from shapely.affinity import affine_transform

    north, south, east, west = (float(v) for v in bbox)
    rows, cols = grid_shape
    sx = (east - west) / cols
    sy = (north - south) / rows
    # shapely takes [a, b, d, e, xoff, yoff] for x' = a*x + b*y + xoff, y' = d*x + e*y + yoff.
    return affine_transform(poly, [sx, 0.0, 0.0, sy, west, south])


def _feature(poly, height_m: float, source: str, terrain_z: float = 0.0) -> dict:
    """One GeoJSON feature in the schema city2stl already reads."""
    from shapely.geometry import mapping

    return {
        "type": "Feature",
        "geometry": mapping(poly),
        "properties": {
            "height_m": round(float(height_m), 2),
            "terrain_z": round(float(terrain_z), 2),
            "height_source": source,
        },
    }


def heightmap_to_polygons(
    heights: np.ndarray,
    *,
    cell_size_m: float,
    bbox=None,
    bands_m: float | None = None,
    terrain: np.ndarray | None = None,
    height_stat: str = "median",
    simplify_m: float | None = None,
    min_area_m2: float = MIN_AREA_M2,
) -> dict:
    """The plate's built cells as footprints carrying heights.

    Args:
        heights: height above local ground per cell, NaN where there is no building.  This is
            what ``stl_heightmap.npy`` and ``osm_buildings.npy`` both hold.
        cell_size_m: metres per pixel, which sets the physical meaning of every tolerance here.
        bbox: ``(north, south, east, west)`` in degrees to emit lon/lat coordinates.  Omit for
            pixel coordinates, which is the right choice when the result is going back through
            `polygons_to_heightmap` on the same grid.
        terrain: absolute ground elevation per cell, same shape as ``heights``.  Each footprint
            takes the median of this beneath it and reports it as ``terrain_z``, which is what
            sits the building on the hillside rather than on a common floor.  Omit it and every
            footprint reports zero, which is correct only for a plate that has no relief.
        bands_m: when set, the plate is cut into horizontal slices this tall and each slice
            contributes its own footprint.  A tower on a podium then comes out as two nested
            polygons rather than one blob at a compromise height, which is what makes the
            result resemble an OSM layer rather than a contour map.  When None, each connected
            component becomes a single footprint at one height.
        height_stat: how a single height is chosen for a component -- ``median`` (typical),
            ``max`` (the ridge), or ``p75``.  Ignored when ``bands_m`` is set, where the slice
            itself fixes the height.
        simplify_m: Douglas-Peucker tolerance in metres.  Defaults to half a cell, floored
            at ``SIMPLIFY_M``, because a trace of a raster cannot carry shape finer than
            the raster.  Raise it towards a full cell to halve the vertex count again, at
            roughly 0.015 of footprint IoU.
        min_area_m2: footprints smaller than this are dropped as tracing noise.

    Returns:
        A GeoJSON FeatureCollection.  Features are ordered shortest first, so a painter's
        algorithm that draws them in order leaves the tall ones on top.
    """
    heights = np.asarray(heights, dtype=np.float64)
    finite = np.isfinite(heights)
    if simplify_m is None:
        simplify_m = max(SIMPLIFY_M, 0.5 * cell_size_m)
    simplify_px = simplify_m / cell_size_m
    min_area_px = min_area_m2 / (cell_size_m ** 2)
    features = []

    if terrain is not None:
        terrain = np.asarray(terrain, dtype=np.float64)
        if terrain.shape != heights.shape:
            raise ValueError("terrain must match the shape of heights")

    def ground_under(outer) -> float:
        """Median ground elevation inside one traced ring, in the ring's own pixel frame.

        Taken before the polygon is moved into degrees, since the terrain raster is indexed by
        pixel.  The median rather than the mean because a footprint straddling a retaining wall
        has a bimodal ground beneath it and the mean would sit in the gap between the two levels,
        where there is no ground at all.
        """
        import cv2

        if terrain is None:
            return 0.0
        mask = np.zeros(heights.shape, np.uint8)
        cv2.fillPoly(mask, [np.round(outer).astype(np.int32)], 1)
        under = terrain[mask.astype(bool)]
        under = under[np.isfinite(under)]
        return float(np.median(under)) if under.size else 0.0

    def emit(mask, height, source):
        for outer, holes in _contours(mask):
            poly = _ring_to_polygon(outer, holes, simplify_px)
            if poly is None or poly.area < min_area_px:
                continue
            z = ground_under(outer)
            if bbox is not None:
                poly = _to_lonlat(poly, bbox, heights.shape)
            features.append(_feature(poly, height, source, z))

    if not finite.any():
        return {"type": "FeatureCollection", "features": []}

    if bands_m:
        # Slices are taken from the ground up, and each slice's mask is everything at least
        # that tall.  Drawn shortest first, the stack rebuilds the original steps; taken
        # individually, each ring is a footprint at a stated height, which is the OSM idiom.
        top = float(np.nanmax(heights))
        edges = np.arange(bands_m, top + bands_m, bands_m)
        for edge in edges:
            emit(finite & (heights >= edge - bands_m * 0.5), float(edge), "plate_band")
    else:
        import cv2

        count, labels = cv2.connectedComponents(finite.astype(np.uint8), connectivity=8)
        stat = {"median": np.median, "max": np.max,
                "p75": lambda v: np.percentile(v, 75)}[height_stat]
        for label in range(1, count):
            mask = labels == label
            values = heights[mask]
            values = values[np.isfinite(values)]
            if values.size == 0:
                continue
            emit(mask, float(stat(values)), "plate_" + height_stat)

    features.sort(key=lambda f: f["properties"]["height_m"])
    return {"type": "FeatureCollection", "features": features}


def polygons_to_heightmap(
    features: dict,
    shape,
    *,
    cell_size_m: float | None = None,
    bbox=None,
    background=np.nan,
) -> np.ndarray:
    """Footprints and heights back onto a grid, tallest winning where they overlap.

    The inverse of `heightmap_to_polygons` for the same grid and bbox.  Cells no footprint
    covers are left at ``background``, NaN by default, so the result carries the same "no
    building here" sentinel the plate rasters use rather than a floor of zeros.

    ``cell_size_m`` is accepted for symmetry and is not needed: the grid shape and the bbox
    already fix the scale.
    """
    from shapely.geometry import shape as to_shape

    rows, cols = shape
    grid = np.full((rows, cols), -np.inf, dtype=np.float64)

    if bbox is not None:
        from shapely.affinity import affine_transform
        north, south, east, west = (float(v) for v in bbox)
        sx = cols / (east - west)
        sy = rows / (north - south)

    for feat in features.get("features", []):
        geom = feat.get("geometry")
        if not geom:
            continue
        try:
            poly = to_shape(geom)
        except Exception:
            continue
        if bbox is not None:
            poly = affine_transform(poly, [sx, 0.0, 0.0, sy, -west * sx, -south * sy])
        height = float(feat.get("properties", {}).get("height_m") or 0.0)
        _burn(grid, poly, height)

    out = np.where(np.isfinite(grid) & (grid > -np.inf), grid, background)
    return out.astype(np.float32)


def _burn(grid: np.ndarray, poly, height: float) -> None:
    """Paint one polygon onto `grid` at `height`, keeping whichever value is taller.

    cv2's polygon fill is used rather than rasterio's because it needs no CRS and no transform
    and the geometry has already been brought into pixel coordinates.  Holes are filled black
    on a scratch mask after the exterior is filled white, which is how a courtyard stays out of
    its own building.
    """
    import cv2

    parts = poly.geoms if poly.geom_type == "MultiPolygon" else [poly]
    for part in parts:
        if part.is_empty:
            continue
        mask = np.zeros(grid.shape, np.uint8)
        exterior = np.round(np.asarray(part.exterior.coords)).astype(np.int32)
        cv2.fillPoly(mask, [exterior], 1)
        for interior in part.interiors:
            hole = np.round(np.asarray(interior.coords)).astype(np.int32)
            cv2.fillPoly(mask, [hole], 0)
        sel = mask.astype(bool)
        if sel.any():
            grid[sel] = np.maximum(grid[sel], height)


def roundtrip_report(heights: np.ndarray, rebuilt: np.ndarray, cell_size_m: float) -> dict:
    """What the conversion cost, measured against the plate it started from.

    Footprint agreement is an IoU over the two "is there a building here" masks.  Height error
    is taken only where both agree there is one, because a height difference at a cell one of
    them calls empty is a footprint error already counted.
    """
    a = np.isfinite(heights)
    b = np.isfinite(rebuilt)
    both = a & b
    err = np.abs(heights[both] - rebuilt[both]) if both.any() else np.array([0.0])
    return {
        "plate_px": int(a.sum()),
        "rebuilt_px": int(b.sum()),
        "iou": float(both.sum() / max((a | b).sum(), 1)),
        "area_ratio": float(b.sum() / max(a.sum(), 1)),
        "height_mae": float(err.mean()),
        "height_p95": float(np.percentile(err, 95)),
        "area_m2": float(a.sum() * cell_size_m ** 2),
    }


# ---------------------------------------------------------------------------
# The plate as a model: a terrain surface plus building geometry
# ---------------------------------------------------------------------------
#
# A plate render is one raster holding two things that behave nothing alike.  The terrain is
# smooth and slowly varying, so it survives heavy resampling and is described well by a coarse
# grid.  The buildings are the opposite -- discontinuous, and defined precisely by the edges the
# terrain does not have -- so resampling destroys them and polygons describe them exactly.
# Storing each in the form that suits it is what makes the pair smaller than the raster, and it
# is also what makes the plate analysable: after the split there is a ground surface to compare
# against a DEM and a building list to compare against OSM, neither of which the raster admits.
#
# The same split serves all three uses this module was asked for.  Registration wants the
# footprints alone.  DEM work wants the terrain alone.  Featurisation wants both, plus the
# measurement of what putting them back together loses.

# Terrain samples this far apart, in metres.  A hillside bends over hundreds of metres, so
# sampling it every twenty loses almost nothing while cutting the stored grid by two orders of
# magnitude at the resolutions these plates use.
DEM_STEP_M = 20.0


def terrain_to_grid(terrain: np.ndarray, *, cell_size_m: float,
                    step_m: float = DEM_STEP_M) -> dict:
    """A terrain raster as a coarse grid that can be expanded back.

    NaN outside the plate is a problem for any resampling, because a coarse cell straddling the
    edge would average real ground with nothing.  The surface is therefore filled outward from
    its own edge before sampling -- the value carried out is meaningless but it is only ever read
    back in places the plate does not cover.
    """
    import cv2

    terrain = np.asarray(terrain, dtype=np.float64)
    valid = np.isfinite(terrain)
    if not valid.any():
        return {"values": [], "shape": list(terrain.shape), "grid": [0, 0],
                "step_m": float(step_m)}

    # Nearest-neighbour fill: every empty cell takes the value of the closest real one. OpenCV
    # labels each cell with the index of its nearest zero-distance pixel in one pass, so the fill
    # costs a distance transform rather than an iteration. A coarse cell straddling the plate
    # edge then averages ground with more ground instead of ground with nothing.
    _, labels = cv2.distanceTransformWithLabels(
        (~valid).astype(np.uint8), cv2.DIST_L2, 3, labelType=cv2.DIST_LABEL_PIXEL)
    lookup = np.zeros(int(labels.max()) + 1, dtype=np.float64)
    lookup[labels[valid]] = terrain[valid]
    filled = lookup[labels]

    step_px = max(int(round(step_m / cell_size_m)), 1)
    rows = max(int(np.ceil(terrain.shape[0] / step_px)) + 1, 2)
    cols = max(int(np.ceil(terrain.shape[1] / step_px)) + 1, 2)
    coarse = cv2.resize(filled, (cols, rows), interpolation=cv2.INTER_AREA)
    return {"values": np.round(coarse, 2).ravel().tolist(),
            "shape": [int(terrain.shape[0]), int(terrain.shape[1])],
            "grid": [rows, cols], "step_m": float(step_m)}


def estimate_terrain(rendered: np.ndarray, built: np.ndarray, *, cell_size_m: float,
                     step_m: float = DEM_STEP_M, heights: np.ndarray | None = None) -> dict:
    """A terrain grid read off the render itself, at the cells no building covers.

    The obvious ground surface is the one the segmentation already produces -- the render minus
    its own white top-hat residual -- but that is a morphological envelope rather than a
    landscape.  An opening knocks the peaks off, which leaves a step wherever a structure begins
    and ends, and steps are the one thing a coarse grid cannot carry.

    Sampling the bare cells instead avoids the problem rather than fighting it.  Nothing has been
    subtracted from those cells, so the surface they describe is smooth; the cells under
    buildings are simply absent, and a gap between known ground is what interpolation is for.
    On the Alhambra this halves the error at every spacing worth using -- twenty-metre samples
    match what the top-hat ground needed five-metre samples to reach, with a sixteenth as many.

    A block median rather than a mean, because a block that is mostly roof still has a few open
    cells around the edges and the median reports those rather than averaging them with the roof.

    ``heights`` closes the remaining gap.  A built cell is not silent about its ground either --
    the segmented height is measured from that ground upward, so ``rendered - height`` reads it,
    weakly, wherever a building stands.  Bare cells still win where both exist; a block entirely
    under roof now has samples of its own rather than a neighbouring block's value.
    """
    import cv2

    rendered = np.asarray(rendered, dtype=np.float64)
    plate = np.isfinite(rendered)
    built = np.asarray(built, dtype=bool)
    bare = np.where(plate & ~built, rendered, np.nan)
    if heights is not None:
        base = rendered - np.asarray(heights, dtype=np.float64)
        bare = np.where(np.isfinite(bare), bare, np.where(plate & built, base, np.nan))
    if not np.isfinite(bare).any():
        bare = np.where(plate, rendered, np.nan)

    step_px = max(int(round(step_m / cell_size_m)), 1)
    rows = int(np.ceil(bare.shape[0] / step_px))
    cols = int(np.ceil(bare.shape[1] / step_px))
    pad = np.full((rows * step_px, cols * step_px), np.nan)
    pad[:bare.shape[0], :bare.shape[1]] = bare
    blocks = pad.reshape(rows, step_px, cols, step_px).transpose(0, 2, 1, 3)
    with warnings.catch_warnings():
        # Blocks wholly off the plate are empty by construction; they are filled just below.
        warnings.simplefilter("ignore", RuntimeWarning)
        coarse = np.nanmedian(blocks.reshape(rows, cols, -1), axis=2)

    # Blocks that saw no bare ground at all -- a courtyard-less block entirely under roof, or a
    # block off the plate -- take the nearest block that did.
    empty = ~np.isfinite(coarse)
    if empty.any() and not empty.all():
        _, labels = cv2.distanceTransformWithLabels(empty.astype(np.uint8), cv2.DIST_L2, 3,
                                                    labelType=cv2.DIST_LABEL_PIXEL)
        lookup = np.zeros(int(labels.max()) + 1, dtype=np.float64)
        lookup[labels[~empty]] = coarse[~empty]
        coarse = lookup[labels]
    coarse = np.nan_to_num(coarse, nan=0.0)

    return {"values": np.round(coarse, 2).ravel().tolist(),
            "shape": [int(rendered.shape[0]), int(rendered.shape[1])],
            "grid": [int(rows), int(cols)], "step_m": float(step_m)}


def grid_to_terrain(dem: dict, shape=None) -> np.ndarray:
    """The coarse grid expanded back to a full raster, bilinearly."""
    import cv2

    rows, cols = dem["grid"]
    if rows == 0 or cols == 0:
        return np.full(tuple(shape or dem["shape"]), np.nan, dtype=np.float32)
    coarse = np.asarray(dem["values"], dtype=np.float32).reshape(rows, cols)
    out_rows, out_cols = tuple(shape or dem["shape"])
    return cv2.resize(coarse, (out_cols, out_rows), interpolation=cv2.INTER_LINEAR)


def plate_to_model(rendered: np.ndarray, heights: np.ndarray, *, cell_size_m: float,
                   bbox=None, bands_m: float | None = 2.0,
                   dem_step_m: float = DEM_STEP_M, terrain: np.ndarray | None = None,
                   **kwargs) -> dict:
    """The whole plate as a terrain grid plus a building layer.

    Args:
        rendered: the plate as it came off the mesh, terrain and structures together, NaN off
            the plate.  This is ``stl_relief.npy``.
        heights: height above local ground per cell, NaN where there is no building.  This is
            ``stl_heightmap.npy``.
        cell_size_m: metres per pixel.
        bbox: ``(north, south, east, west)``; passed through to the footprints.
        bands_m: height banding for the footprints; see `heightmap_to_polygons`.
        dem_step_m: terrain sample spacing in metres.
        terrain: a ground surface to use instead of reading one off the render.  Supply it only
            when a better one is available than the bare cells give -- a real DEM, say.

    Returns:
        A dict with ``dem`` and ``buildings``, which `model_to_heightmap` turns back into a
        raster and `model_report` scores.  Both members are plain JSON types, so the model
        serialises as it stands.
    """
    built = np.isfinite(np.asarray(heights, dtype=np.float64))
    if terrain is None:
        dem = estimate_terrain(rendered, built, cell_size_m=cell_size_m, step_m=dem_step_m, heights=heights)
        ground = grid_to_terrain(dem, np.asarray(rendered).shape).astype(np.float64)
        ground = np.where(np.isfinite(rendered), ground, np.nan)
    else:
        dem = terrain_to_grid(terrain, cell_size_m=cell_size_m, step_m=dem_step_m)
        ground = np.asarray(terrain, dtype=np.float64)
    return {
        "dem": dem,
        "buildings": heightmap_to_polygons(heights, cell_size_m=cell_size_m, bbox=bbox,
                                           bands_m=bands_m, terrain=ground, **kwargs),
        "cell_size_m": float(cell_size_m),
        "bbox": list(bbox) if bbox is not None else None,
    }


def model_to_heightmap(model: dict, shape=None) -> np.ndarray:
    """Terrain and buildings recombined into one raster, as the plate was rendered.

    Buildings are added to the ground beneath them rather than replacing it, because
    ``height_m`` is measured from that ground; a footprint's roof is its own rise plus the
    hillside it stands on.
    """
    dem = model["dem"]
    shape = tuple(shape or dem["shape"])
    ground = grid_to_terrain(dem, shape).astype(np.float64)
    rise = polygons_to_heightmap(model["buildings"], shape,
                                 bbox=model.get("bbox"), background=0.0)
    return (ground + np.nan_to_num(rise, nan=0.0)).astype(np.float32)


def model_report(rendered: np.ndarray, rebuilt: np.ndarray, model: dict,
                 cell_size_m: float) -> dict:
    """What the split costs, in accuracy and in size.

    Error is reported over the plate only.  The size comparison counts the numbers each form
    needs -- one per cell for the raster, one per terrain sample plus two per polygon vertex and
    two per polygon for the model -- which is the honest comparison because neither form has been
    compressed further.
    """
    ok = np.isfinite(rendered)
    err = np.abs(rendered[ok] - rebuilt[ok]) if ok.any() else np.array([0.0])

    def count_coords(node):
        if isinstance(node[0], (int, float)):
            return 1
        return sum(count_coords(child) for child in node)

    verts = sum(count_coords(f["geometry"]["coordinates"])
                for f in model["buildings"]["features"])
    n_feats = len(model["buildings"]["features"])
    model_numbers = len(model["dem"]["values"]) + 2 * verts + 2 * n_feats
    return {
        "plate_px": int(ok.sum()),
        "mae_m": float(err.mean()),
        "p95_m": float(np.percentile(err, 95)),
        "max_m": float(err.max()),
        "footprints": n_feats,
        "vertices": int(verts),
        "dem_samples": len(model["dem"]["values"]),
        "raster_numbers": int(ok.sum()),
        "model_numbers": int(model_numbers),
        "compression": float(ok.sum() / max(model_numbers, 1)),
        "area_m2": float(ok.sum() * cell_size_m ** 2),
    }
