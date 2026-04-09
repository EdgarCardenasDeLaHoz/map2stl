import math
import os

import numpy as np
import cv2
import matplotlib.pyplot as plt

from skimage import filters

from geo2stl.geo2stl import stitch_tiles_no_rasterio
from geo2stl import sat2stl as s2s

from city2stl import create
from city2stl.dem2stl import proj_map_geo_to_2D

from numpy2stl import array_to_mesh, rescale, write3MF
import numpy2stl.puzzle as puzzle
import numpy2stl.boolean as boolean

from notebooks import figure as figs


def render_model(target_bbox, dim,
                 depth_scale=1.0, water_scale=0.1,
                 model_height=30, model_base=5,
                 name="test", out_dir="."):

    (N, S, E, W) = target_bbox
    bbox = [W, E, S, N]

    im = make_dem_image(target_bbox, dim=dim,
                        depth_scale=depth_scale,
                        water_scale=water_scale,
                        subtract_water=False)

    figs.plot_data(im, bbox=bbox, close=True)

    im2 = rescale(im, height=model_height, base=model_base, smooth=None)
    models = create_dem_model(im2)
    out_dir2 = os.path.join(out_dir, name)
    os.makedirs(out_dir2, exist_ok=True)
    filename = os.path.join(out_dir2, name + ".3mf")
    write3MF(filename, models)


    
def create_dem_model(im, cut=True):

    width = im.shape
    model = create.get_landspace_model(im, None, 1, simplify=False)

    if cut:
        puzzle_model = puzzle.make_puzzle_model(width, b=200, m=50, base_n=10)
        models = boolean.cut_puzzle_pieces_manifold(model, puzzle_model)
        base_models = puzzle.make_base_border(width, b=200, m=50, base_n=10, height=1, offset_dist=5)
        models = models | base_models
    else:
        models = {"DEM": model}

    return models


def make_dem_image(target_bbox, dim=600,
                   depth_scale=1.0, water_scale=0.1, sat_scale=200,
                   height=30, base=5,
                   subtract_water=True):

    (N, S, E, W) = target_bbox
    im = stitch_tiles_no_rasterio(target_bbox) * 1.0
    im[im < 0] = im[im < 0] * depth_scale

    w, h = im.shape

    if subtract_water:
        sat = s2s.fetch_bbox_image(N, S, E, W, scale=sat_scale, dataset="esa")
        sat = sat.clip(0, 100)
        water = 1.0 * ((sat == 80) | (sat == 0))
        water = filters.median(water, np.ones((3, 3)))
        water = cv2.resize(water, (h, w), interpolation=cv2.INTER_LINEAR)
        water = water * im.ptp() * water_scale
        im = im - water

        plt.figure()
        plt.imshow(water)
    else:
        im[im > 0] = im[im > 0] + im.ptp() * water_scale

    im = proj_map_geo_to_2D(im, np.array((N, S, E, W)))
    im = im[:, ~np.any(np.isnan(im), axis=0)]

    im = rescale(im, height=height, base=base, clip=[.01, 99.99], smooth=3)
    im = im.round(1)
    im = im[::-1]

    return im
