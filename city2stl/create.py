"""
city2stl/create.py — High-level mesh assembly helpers.

Provides functions to convert OSM building GeoDataFrames, DEM arrays,
and bounding-box metadata into vertex/face arrays suitable for STL export.

Note: this module is research/notebook code and is not part of the active
FastAPI application (that pipeline lives in ui/core/). These helpers remain
here for offline experimentation via the Jupyter notebooks in strm2stl/notebooks/.
"""

from __future__ import annotations

import numpy as np
from shapely.geometry import polygon as shapely_polygon

from .buildings import get_polygons, triangulate_prism as _tri_prism
from .dem2stl import reposition_dem as _reposition
from .osm2stl import perimeter_to_walls as np2stl  # noqa: F401 – re-export for notebook compat

from numpy2stl import array_to_mesh
import numpy2stl.simplify as simp


# ---------------------------------------------------------------------------
# Public mesh builders
# ---------------------------------------------------------------------------

def get_building_model(gdf, scale: float):
    """
    Convert an OSM building GeoDataFrame into a (vertices, faces) mesh.

    Parameters
    ----------
    gdf :   GeoDataFrame with building polygon geometries
    scale : vertical scale factor applied to z-coordinates

    Returns
    -------
    vertices : ndarray, shape (N, 3)
    faces    : ndarray of face index triples
    """
    building_poly = get_polygons(gdf)
    tris = _triangulate_prism(building_poly)

    vertices, faces = _vertices_to_index(tris)
    vertices[:, [1, 0]] = vertices[:, [0, 1]] * 1000
    vertices[:, 2] = vertices[:, 2] * scale
    return vertices, faces


def get_landspace_model(data: np.ndarray, bounds_NW=None, scale: float = 1, simplify: bool = True):
    """
    Convert a 2-D elevation array into a terrain mesh.

    Parameters
    ----------
    data      : 2-D float array of elevation values
    bounds_NW : optional [[N0,N1],[W0,W1]] geographic bounds for reprojection
    scale     : vertical scale factor
    simplify  : whether to run mesh simplification

    Returns
    -------
    vertices : ndarray, shape (N, 3)
    faces    : ndarray of face index triples
    """
    vx, fs = array_to_mesh(data)
    vx = vx.astype(float)

    if bounds_NW is not None:
        im_lims = ((0, data.shape[0]), (0, data.shape[1]))
        vx = _reposition(vx, im_lims, bounds_NW)
        vx[:, [0, 1]] = vx[:, [0, 1]] * 1000

    if simplify:
        fs = simp.simplify_mesh_surfaces(vx, fs)

    vx[:, 2] = vx[:, 2] * scale
    return vx, fs


def get_bounds_model(gdf, scale: float):
    """
    Build a rectangular prism mesh that encloses the bounding box of *gdf*.

    Returns
    -------
    vertices : ndarray, shape (N, 3)
    faces    : ndarray of face index triples
    """
    centroids = np.array([[g.centroid.x, g.centroid.y] for g in gdf["geometry"]])
    Nc, Wc = centroids.T
    c, d, a, b = Nc.max(), Nc.min(), Wc.max(), Wc.min()

    prism = {
        'z1': 100,
        'z0': 0,
        'points': np.array([[c, a], [c, b], [d, b], [d, a], [c, a]]).T,
    }
    tris = _triangulate_prism([prism])
    vertices, faces = _vertices_to_index(tris)
    vertices[:, [1, 0]] = vertices[:, [0, 1]]
    vertices[:, 2] = vertices[:, 2] * scale
    vertices = vertices * 1000
    return vertices, faces


# ---------------------------------------------------------------------------
# Bounding-box helpers
# ---------------------------------------------------------------------------

def get_bbox(gdf) -> list:
    """
    Return [[south, north], [west, east]] geographic bounds for *gdf*.
    """
    centroids = np.array([[g.centroid.x, g.centroid.y] for g in gdf["geometry"]])
    Nc, Wc = centroids.T
    c, d, a, b = Nc.max(), Nc.min(), Wc.max(), Wc.min()
    return [[a, b], [d, c]]


def get_bounds_(gdf, im_shape: tuple, coor_lims: list) -> tuple:
    """
    Return (pixel_bounds, geo_bounds) for *gdf* projected onto an image.

    Parameters
    ----------
    gdf       : GeoDataFrame
    im_shape  : (height, width) of the target image
    coor_lims : [[N0, N1], [W0, W1]] geographic coordinate limits

    Returns
    -------
    pixel_bounds : (Nx, Sx, Ex, Wx) in pixel space
    geo_bounds   : [[S, N], [W, E]]
    """
    centroids = np.array([[g.centroid.x, g.centroid.y] for g in gdf["geometry"]])
    Nc, Wc = centroids.T
    c, d, a, b = Nc.max(), Nc.min(), Wc.max(), Wc.min()
    geo_bounds = [[a, b], [d, c]]

    im_lims = np.array(((0, im_shape[0]), (0, im_shape[1])))
    Yc, Xc = coor2im(coor_lims, im_lims, centroids)
    pixel_bounds = (Yc.max(), Yc.min(), Xc.max(), Xc.min())
    return pixel_bounds, geo_bounds


def get_base_height(gdf, im: np.ndarray, coor_lims: list) -> np.ndarray:
    """
    Sample elevation values from *im* at the centroid of each feature in *gdf*.

    Returns
    -------
    H : 1-D array of sampled elevation values, one per feature
    """
    centroids = np.array([[g.centroid.x, g.centroid.y] for g in gdf["geometry"]])
    im_lims = np.array([(0, im.shape[0]), (0, im.shape[1])])
    Nc, Wc = coor2im(coor_lims, im_lims, centroids)
    Nc = Nc.clip(0, im.shape[0] - 1)
    Wc = Wc.clip(0, im.shape[1] - 1)
    return im[Nc, Wc]


def get_bounds(gdf, im: np.ndarray, coor_lims: list) -> tuple:
    """
    Return (Nx, Sx, Ex, Wx) pixel-space bounds of *gdf* projected onto *im*.
    """
    centroids = np.array([[g.centroid.x, g.centroid.y] for g in gdf["geometry"]])
    im_lims = np.array([(0, im.shape[0]), (0, im.shape[1])])
    Nc, Wc = coor2im(coor_lims, im_lims, centroids)
    return Nc.max(), Nc.min(), Wc.max(), Wc.min()


# ---------------------------------------------------------------------------
# Coordinate conversion
# ---------------------------------------------------------------------------

def coor2im(coor_lims, im_lims, xy_list: np.ndarray, asint: bool = True):
    """
    Map geographic (lon, lat) coordinates to image pixel indices.

    Parameters
    ----------
    coor_lims : [[N0, N1], [W0, W1]]   geographic range
    im_lims   : [[X0, X1], [Y0, Y1]]   pixel range
    xy_list   : (M, 2) array of [lon, lat] coordinates
    asint     : round to integer pixel indices (default True)

    Returns
    -------
    row_indices, col_indices : two 1-D arrays
    """
    N0, N1 = coor_lims[0]
    W0, W1 = coor_lims[1]
    X0, X1 = im_lims[0]
    Y0, Y1 = im_lims[1]

    rows = _linear_map(N0, N1, X0, X1, xy_list[:, 1])
    cols = _linear_map(W0, W1, Y0, Y1, xy_list[:, 0])

    if asint:
        return rows.astype(int), cols.astype(int)
    return rows, cols


def crop_image_bounds(im: np.ndarray, bounds: tuple, scale: float = 0.2) -> np.ndarray:
    """
    Crop *im* to pixel *bounds* and downscale by *scale*.

    Parameters
    ----------
    im     : source image array
    bounds : (Nx, Sx, Ex, Wx) pixel limits
    scale  : resize factor (default 0.2 → 20 % of original size)
    """
    from skimage import transform as sktr
    Nx, Sx, Ex, Wx = bounds
    data = im[int(Sx):int(Nx), int(Wx):int(Ex)].astype(float)
    out_shape = (np.array(data.shape) * scale).astype(int)
    return sktr.resize(data, out_shape)


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def polygon_to_perimeter(poly):
    """
    Decompose a Shapely Polygon into vertices and perimeter index arrays.

    Collinear vertices (interior angle ~180°) are removed from the perimeter
    to reduce the triangle count before ear-clip triangulation.

    Returns
    -------
    verts      : (N, 2) float array of all vertex coordinates
    perimeters : list of index arrays, one per ring (exterior + holes)
    """
    poly = shapely_polygon.orient(poly)
    verts, peri = [], []
    n_v = 0

    exterior = np.array(poly.exterior.coords)[:-1]   # drop closing duplicate
    verts.extend(exterior)
    peri.append(np.arange(len(exterior) + n_v))
    n_v = len(exterior)

    for ring in poly.interiors:
        pts = np.array(ring.coords[:-1])
        verts.extend(pts)
        peri.append(np.arange(len(pts)) + n_v)
        n_v += len(pts)

    verts = np.array(verts)

    perimeters = []
    for line_idx in peri:
        line = verts[line_idx]
        angles = _perimeter_angles(line)
        simplified = line_idx[(angles < 179) | (angles > 181)]
        perimeters.append(simplified)

    return verts, perimeters


def polygon_to_prism(polygons, heights, base_val: float = 0) -> list:
    """
    Extrude a list of Shapely Polygons into 3-D prism triangle arrays.

    Parameters
    ----------
    polygons  : iterable of Shapely Polygon objects
    heights   : iterable of roof heights (one per polygon)
    base_val  : floor z-coordinate (default 0)

    Returns
    -------
    list of triangle arrays (one ndarray per polygon that succeeded)
    """
    all_triangles = []
    for n, poly in enumerate(polygons):
        verts, peri = polygon_to_perimeter(poly)
        verts = np.concatenate((verts, np.zeros((len(verts), 1))), axis=1)
        verts[:, 2] = heights[n]
        try:
            _, faces = _simplify_surface(verts, peri)
        except Exception:
            continue
        top_tris = verts[faces]
        all_triangles.append(top_tris)
        wall_tris = _perimeter_to_walls(verts, peri, floor_val=base_val)
        all_triangles.append(wall_tris)

    return all_triangles


def shapely_to_buildings(shp_poly, z0: float = 1, z1: float = 39, polygons: list | None = None) -> list:
    """
    Convert a Shapely MultiPolygon into a list of building prism dicts.

    Each dict has keys: roof_height, base_height, points (2×N coord array).
    """
    if polygons is None:
        polygons = []
    for poly in shp_poly.geoms:
        polygons.append({
            'roof_height': z1,
            'base_height': z0,
            'points': np.array(poly.exterior.coords).T,
        })
    return polygons


def _triangulate_prism(polygons: list) -> np.ndarray:
    """
    Triangulate a list of prism dicts (each with z1, z0, points).

    Returns concatenated triangle array.
    """
    from .osm2stl import polygon_to_prism as _p2p
    triangles = []
    for p in polygons:
        vert = np.array(p['points']).T
        if np.isclose(vert[0], vert[-1]).all():
            vert = vert[:-1]
        zdim = np.zeros((len(vert), 1)) + p['z1']
        vert = np.concatenate([vert, zdim], axis=1)
        tri = _p2p(vert, base_val=p['z0'])
        triangles.append(tri)
    return np.concatenate(triangles)


def boundry_to_poly(geo_poly) -> list:
    """
    Wrap a Shapely Polygon exterior as a single prism dict at z=0 / z=-30.
    """
    pts = np.array(geo_poly.exterior.coords).T
    return [{'points': pts, 'roof_height': 0, 'base_height': -30}]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _linear_map(low_in: float, high_in: float, low_out: float, high_out: float,
                qx: np.ndarray) -> np.ndarray:
    """Linearly map values from [low_in, high_in] to [low_out, high_out]."""
    return (qx - low_in) / (high_in - low_in) * (high_out - low_out) + low_out


def _perimeter_angles(line: np.ndarray) -> np.ndarray:
    """
    Compute interior angles (in degrees) at each vertex of a closed polygon.
    Used to detect and remove collinear vertices before ear-clip triangulation.
    """
    n = len(line)
    prev_pts = np.roll(line, 1, axis=0)
    next_pts = np.roll(line, -1, axis=0)
    v1 = prev_pts - line
    v2 = next_pts - line
    cos_a = (v1 * v2).sum(axis=1) / (
        np.linalg.norm(v1, axis=1) * np.linalg.norm(v2, axis=1) + 1e-12
    )
    return np.degrees(np.arccos(np.clip(cos_a, -1, 1)))
