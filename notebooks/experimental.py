import os
import sys

import trimesh
import glob

import numpy as np
import pandas as pd

import osmnx as ox
import cv2

from shapely.geometry import Polygon
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union

import numba
import matplotlib.pyplot as plt

from skimage import io, filters, morphology, transform

from numpy2stl import numpy2stl as n2s
from geo2stl import geo2stl as g2s
from geo2stl import sat2stl as s2s

from city2stl import create
import city2stl.dem2stl as d2s

from typing import Optional, Tuple, Any


def fill_building_heights(gdf: Any) -> Any:
    """
    Fill missing building heights in a GeoDataFrame.

    Args:
        gdf: GeoDataFrame containing building geometries and attributes.

    Returns:
        Updated GeoDataFrame with filled height values.
    """

    cut_off = 50

    if 'building:levels' not in gdf.columns:
        gdf['building:levels'] = 3
    else:
        gdf['building:levels'] = pd.to_numeric(
            gdf['building:levels'], errors='coerce').fillna(3)

    gdf['building_m'] = gdf['building:levels'] * 4.0

    if 'height' not in gdf.columns:
        gdf['height'] = gdf['building_m']

    gdf['height'] = gdf['height'].fillna(gdf['building_m'])
    gdf['height'] = pd.to_numeric(gdf['height'], errors='coerce').fillna(10)

    H = gdf['height'].values
    bx = H < cut_off
    x = H[bx]
    x = x.round(1).clip(cut_off)
    H[bx] = x
    gdf['height'] = H

    return gdf


def add_building_z(gdf: Any, im: Optional[np.ndarray], coor_lims: Tuple[float, ...]) -> Any:
    """
    Add Z coordinates to buildings based on base height from DEM.

    Args:
        gdf: GeoDataFrame with building data.
        im: DEM image array, or None.
        coor_lims: Coordinate limits.

    Returns:
        Updated GeoDataFrame with z0 and z1 columns.
    """

    if im is not None:
        H = create.get_base_height(gdf, im, coor_lims) + 1
    else:
        H = 0

    base = 0
    gdf["z0"] = base
    gdf["z1"] = base + gdf["height"] + H

    return gdf


def reduce_buildings(gdf: Any, TOLERANCE_M: float = 1, a: int = 1) -> Any:
    """
    Reduce and simplify building geometries for 3D modeling.

    Args:
        gdf: GeoDataFrame with building polygons.
        TOLERANCE_M: Tolerance for simplification in meters.
        a: Scaling factor for buffering.

    Returns:
        Optimized GeoDataFrame with simplified geometries.
    """
    print("Reducing buildings...")

    gdf = gdf.to_crs(epsg=3857)
    # 1. Group by height and dissolve (vectorized merge)
    # This combines adjacent polygons of the same height automatically
    gdf_dissolved = gdf.dissolve(by='height').reset_index()

    # 3. Morphological Cleanup (Fixing jagged edges/slivers)
    gdf_dissolved['geometry'] = (
        gdf_dissolved.geometry
        .buffer(TOLERANCE_M*a, join_style=2)
        .buffer(-TOLERANCE_M*a, join_style=2)
    )
    # 2. Simplify the geometry
    # preserve_topology=True keeps polygons from disappearing entirely
    gdf_dissolved['geometry'] = gdf_dissolved.geometry.simplify(
        TOLERANCE_M, preserve_topology=True
    )
    # 4. Explode MultiPolygons back into individual rows
    # This is the cleaner way to handle the 'hasattr(geoms)' logic
    gdf_optimized = gdf_dissolved.explode(index_parts=False)

    # 5. Final Filter: Remove any null or empty geometries created by simplification
    gdf_optimized = gdf_optimized[~gdf_optimized.is_empty &
                                  gdf_optimized.is_valid]

    gdf_optimized = gdf_optimized.to_crs(epsg=4326)

    return gdf_optimized


def gdf_to_vertices(gdf):

    if gdf.crs != "EPSG:4326":
        gdf = gdf.to_crs(epsg=4326)
    gdf["z0"] = 0
    gdf["z1"] = gdf["z0"] + gdf["height"]

    building_poly = create.get_polygons(gdf)

    tris = create.triangulate_prism(building_poly)

    vertices, faces = n2s.vertices_to_index(tris)
    vertices[:, [1, 0]] = vertices[:, [0, 1]]*1000
    return vertices, faces


@numba.njit
def point_in_triangle_edge(p, a, b, c):
    e0 = (p[0]-a[0])*(b[1]-a[1]) - (p[1]-a[1])*(b[0]-a[0])
    e1 = (p[0]-b[0])*(c[1]-b[1]) - (p[1]-b[1])*(c[0]-b[0])
    e2 = (p[0]-c[0])*(a[1]-c[1]) - (p[1]-c[1])*(a[0]-c[0])
    return (e0 >= 0 and e1 >= 0 and e2 >= 0) or (e0 <= 0 and e1 <= 0 and e2 <= 0)


@numba.njit
def rasterize_triangle_max_edge(depth_map, tri_xy, tri_z):
    h, w = depth_map.shape

    # bounding box
    xmin = max(int(np.floor(np.min(tri_xy[:, 0]))), 0)
    xmax = min(int(np.ceil(np.max(tri_xy[:, 0]))), w-1)
    ymin = max(int(np.floor(np.min(tri_xy[:, 1]))), 0)
    ymax = min(int(np.ceil(np.max(tri_xy[:, 1]))), h-1)

    a = tri_xy[0]
    b = tri_xy[1]
    c = tri_xy[2]

    zmax = np.max(tri_z)  # approximate Z per pixel

    for y in range(ymin, ymax+1):
        for x in range(xmin, xmax+1):
            if point_in_triangle_edge(np.array([x, y]), a, b, c):
                if np.isnan(depth_map[y, x]) or zmax > depth_map[y, x]:
                    depth_map[y, x] = zmax


def check_triangle(tri_xy, tri_z, MIN_Z=0, MIN_PIXEL_AREA=0.1, GRID_SIZE=1000):

    # 1. Z threshold
    if tri_z.max() < MIN_Z:
        return False

    # 2. Degenerate triangle (area)
    area = abs(
        (tri_xy[1, 0]-tri_xy[0, 0])*(tri_xy[2, 1]-tri_xy[0, 1]) -
        (tri_xy[2, 0]-tri_xy[0, 0])*(tri_xy[1, 1]-tri_xy[0, 1])
    )
    if area < 1e-6:
        return False

    # 3. Too small on screen
    if area < MIN_PIXEL_AREA:
        return False

    # 4. Off-screen
    if tri_xy[:, 0].max() < 0 or tri_xy[:, 0].min() >= GRID_SIZE or \
            tri_xy[:, 1].max() < 0 or tri_xy[:, 1].min() >= GRID_SIZE:
        return False

    return True


######################################

def normalize(imx):

    l1, l2, l3 = np.percentile(imx.ravel()[::10], [1, 50, 99])

    imx = imx - l2
    imx[imx < 0] = imx[imx < 0] / np.maximum(l1, l2-1)
    imx[imx > 0] = imx[imx > 0] / l3
    # imx = imx.clip(-5,5)
    return imx


def resize_geo_aspect(im, NSEW):
    lat = NSEW[[0, 1]]
    lon = NSEW[[2, 3]]

    lon_adj = lon * np.cos(np.deg2rad(lat).mean())
    ratio = (np.diff(lon_adj) / np.diff(lat))[0]
    shp = np.array(im.shape)[[1, 0]]
    shp = np.array([1, 1/ratio]) * 1000
    sz = tuple((shp).astype(int))
    im = cv2.resize(im, sz)

    return im


def resize_max(im, max_size=1000):
    """
    Resize an image to fit within a maximum size while maintaining aspect ratio.

    Parameters:
    - im: Input image as a 2D numpy array.
    - max_size: Maximum size for the longest dimnsion of the image.

    Returns:
    - Resized image as a 2D numpy array.
    """
    height, width = im.shape
    scale = max_size / max(height, width)
    new_size = (int(width * scale), int(height * scale))
    resized_im = cv2.resize(im, new_size, interpolation=cv2.INTER_LINEAR)
    return resized_im

################### Write to file ##########################


def rescale(im, max_size=600, height=20, base=10, clip=None, smooth=None):

    im = resize_max(im, max_size=max_size)

    if smooth is not None:
        im = filters.median(im, np.ones((smooth, smooth)))

    if clip is not None:
        if len(clip) == 1:
            clip = [clip, 100-clip]

        lo, hi = np.percentile(im.ravel(), clip)
        im = im.clip(lo, hi)

    im = im - im.min()

    im = im / im.ptp() * height
    im = im + base
    return im


def subtract_water(dem, aquatic, height=0.2, ocean_level=1):

    dem = dem.copy()
    im_a = aquatic.copy().astype(np.uint8)

    dem = resize_max(dem, max_size=1200)
    im_a = filters.median(im_a, np.ones((3, 3)))
    im_a = cv2.resize(im_a,
                      (dem.shape[1], dem.shape[0]),
                      interpolation=cv2.INTER_LINEAR).astype(int)

    im_a = (im_a / 100)
    im_a = (im_a).clip(0, 1)
    im_a[dem < 0] = ocean_level
    dem = dem - im_a*np.ptp(dem.ravel())*height

    return dem


def get_border(nsew, country, shape):

    bounds, bbox = o2s.get_boundries_osmnx(nsew, country)
    boundry = bounds[-2]

    coorlims = (nsew[np.array([0, 1])], nsew[np.array([2, 3])])
    imlims = ((0, shape[1]), (shape[0], 0))
    r, c = create.coor2im(coorlims, imlims, boundry.pts.T)

    im_lines = np.zeros(shape, dtype=np.uint8)

    for i in range(len(r)-1):
        rr, cc, val = line_aa(int(round(r[i])), int(
            round(c[i])), int(round(r[i+1])), int(round(c[i+1])))
        im_lines[rr, cc] = val*255

    im_lines = morphology.binary_dilation(im_lines, morphology.disk(2))

    return im_lines
