import numpy as np
from PIL import Image
import re
import os

from skimage import io

import json
import glob
import logging

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tile-file registry — loaded lazily on first call to get_tile_files()
# so that importing this module does not crash when config.json is absent.
# ---------------------------------------------------------------------------

_tile_files: list | None = None


def get_tile_files() -> list:
    """Return the list of local SRTM .tif tile paths (loaded once, then cached)."""
    global _tile_files
    if _tile_files is not None:
        return _tile_files

    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'config.json')
    if not os.path.exists(config_path):
        config_path = os.path.join(os.getcwd(), 'strm2stl', 'config.json')

    if not os.path.exists(config_path):
        logger.warning(
            "geo2stl: config.json not found — local SRTM tiles unavailable. "
            "stitch_tiles_no_rasterio will return None."
        )
        _tile_files = []
        return _tile_files

    with open(config_path, 'r') as f:
        config = json.load(f)

    ocean_root = config.get("ocean_root", ".")
    _tile_files = glob.glob(os.path.join(ocean_root, "*.tif"))
    return _tile_files


# Keep module-level name for backward compatibility (used by server.py and notebooks).
# Accessing it triggers lazy load.
class _TileFilesProxy:
    """Lazy proxy for tile_files — evaluates on first attribute/iter access."""
    def __iter__(self):
        return iter(get_tile_files())
    def __len__(self):
        return len(get_tile_files())
    def __getitem__(self, idx):
        return get_tile_files()[idx]
    def __bool__(self):
        return bool(get_tile_files())


tile_files = _TileFilesProxy()


def parse_extent_from_filename(filename):
    match = re.search(r'n([-\d.]+)_s([-\d.]+)_w([-\d.]+)_e([-\d.]+)', filename)
    if match:
        parts = match.groups()
        # Remove trailing dots and convert to float
        try:
            n, s, w, e = [float(p.rstrip('.')) for p in parts]
        except ValueError:
            raise ValueError(f"Could not convert parts to float: {parts}")
        return (n, s, e, w)
    else:
        raise ValueError(f"Could not parse extent from filename: {filename}")


def intersect_bbox(bbox1, bbox2):
    N1, S1, E1, W1 = bbox1
    N2, S2, E2, W2 = bbox2
    N = min(N1, N2)
    S = max(S1, S2)
    E = min(E1, E2)
    W = max(W1, W2)
    if N > S and E > W:
        return (N, S, E, W)
    return None


def crop_tile_np(image_array, tile_bbox, crop_bbox):
    tile_N, tile_S, tile_E, tile_W = tile_bbox
    crop_N, crop_S, crop_E, crop_W = crop_bbox

    height, width = image_array.shape
    lat_per_pixel = (tile_N - tile_S) / height
    lon_per_pixel = (tile_E - tile_W) / width

    y1 = int((tile_N - crop_N) / lat_per_pixel)
    y2 = int((tile_N - crop_S) / lat_per_pixel)
    x1 = int((crop_W - tile_W) / lon_per_pixel)
    x2 = int((crop_E - tile_W) / lon_per_pixel)

    return image_array[y1:y2, x1:x2]


def stitch_tiles_no_rasterio(target_bbox):
    print("==== Stitching tiles ====")
    print(f"Target bounding box: {target_bbox}")

    rows = {}

    for fn in get_tile_files():
        tile_bbox = parse_extent_from_filename(os.path.basename(fn))

        intersection = intersect_bbox(tile_bbox, target_bbox)

        if not intersection:
            continue

        try:
            image_array = io.imread(fn)
        except Exception as e:
            print(f"WARNING: Failed to open {fn}: {e}")
            continue

        cropped = crop_tile_np(image_array, tile_bbox, intersection)
        row_key = intersection[0]
        rows.setdefault(row_key, []).append(
            (intersection[3], cropped))  # use W for sorting

    if not rows:
        print("==== No tiles matched ====")
        return None

    stitched_rows = []
    for N in sorted(rows.keys(), reverse=True):
        tiles = sorted(rows[N], key=lambda t: t[0])
        row = np.hstack([img for _, img in tiles])
        stitched_rows.append(row)

    final_image = np.vstack(stitched_rows)

    print("Finished stitching")
    return final_image


def proj_map_height(mat, NSEW):
    n, s, e, w = NSEW

    m1, n1 = mat.shape
    xv, yv = np.meshgrid(range(n1), range(m1))

    xv = ((xv/n1)-0.5) * (e-w)
    xv = np.deg2rad(xv)

    yv = ((1-yv/m1)-0.5) * (n-s)

    yv = np.deg2rad(yv)

    zv = np.cos(xv) * np.cos(yv)

    zv = zv * m1/(n-s) * 180/np.pi

    return zv


def proj_map_geo_to_2D(mat, NSEW, clip_out=True):

    NSEW = np.array(NSEW)

    lat = NSEW[[0, 1]]
    lon = NSEW[[2, 3]]

    m, n = mat.shape
    xv, yv = np.meshgrid(range(n), range(m))

    xc = (n-1)/2
    yc = (m-1)/2
    xv_c = (xv - xc).astype(int)
    yv_c = (yv - yc).astype(int)

    lat_v = np.linspace(lat[0], lat[1], m)
    lat_v = np.deg2rad(lat_v[:, None])
    xv_adj = xv_c * np.cos(lat_v)

    xv2 = (xv_adj + xc).astype(int)
    yv2 = (yv_c + yc).astype(int)

    mat_adj = mat*0.0
    mat_adj[:] = np.nan
    mat_adj[yv2, xv2] = mat[yv, xv]

    y1, y2 = np.min(yv2), np.max(yv2)
    x1, x2 = np.min(xv2), np.max(xv2)

    mat_adj = mat_adj[y1:y2, x1:x2]

    if clip_out:
        mat_adj = mat_adj[:, ~np.any(np.isnan(mat_adj), axis=0)]

    return mat_adj


def mat2coor(limits, matsize, index):
    [x1, x2, y1, y2] = index

    xs = np.array([x1, x2])
    xs = xs / matsize[0]
    xs = (xs * limits[1]) + limits[0]

    ys = np.array([y1, y2])
    ys = ys / matsize[1]
    ys = (ys * (limits[3]-limits[2])) + (limits[2])

    print(xs, ys)

    coor = [xs[0], xs[1], ys[0], ys[1]]

    return coor
