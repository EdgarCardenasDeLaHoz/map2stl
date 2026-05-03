import math
import os

import numpy as np
import matplotlib.pyplot as plt

from geo2stl.dem import make_dem_image as _geo_make_dem_image

from city2stl import create

from numpy2stl import rescale, write3MF
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
    """Fetch a print-ready DEM array for *target_bbox*.

    Delegates data fetching, water subtraction, and projection to
    :func:`geo2stl.dem.make_dem_image`, then rescales to print height/base
    and flips the array to match numpy2stl's expected row orientation.
    """
    im = _geo_make_dem_image(
        target_bbox,
        dim=dim,
        depth_scale=depth_scale,
        sat_scale=sat_scale,
        water_scale=water_scale,
        subtract_water=subtract_water,
        projection="cosine",
        clip_nans=True,
    )

    im = rescale(im, height=height, base=base, clip=[0.01, 99.99], smooth=3)
    im = im.round(1)
    im = im[::-1]
    return im
