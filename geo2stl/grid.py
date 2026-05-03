from __future__ import annotations

import numpy as np


def proj_map_height(mat, nsew):
    north, south, east, west = nsew

    rows, cols = mat.shape
    xv, yv = np.meshgrid(range(cols), range(rows))

    xv = ((xv / cols) - 0.5) * (east - west)
    xv = np.deg2rad(xv)

    yv = ((1 - yv / rows) - 0.5) * (north - south)
    yv = np.deg2rad(yv)

    zv = np.cos(xv) * np.cos(yv)
    zv = zv * rows / (north - south) * 180 / np.pi
    return zv


def mat2coor(limits, matsize, index):
    x1, x2, y1, y2 = index

    xs = np.array([x1, x2])
    xs = xs / matsize[0]
    xs = (xs * limits[1]) + limits[0]

    ys = np.array([y1, y2])
    ys = ys / matsize[1]
    ys = (ys * (limits[3] - limits[2])) + limits[2]

    print(xs, ys)
    return [xs[0], xs[1], ys[0], ys[1]]