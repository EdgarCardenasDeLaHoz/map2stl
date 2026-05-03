from __future__ import annotations

import glob
import json
import logging
import os
import re

import numpy as np
from skimage import io

logger = logging.getLogger(__name__)

_tile_files: list | None = None


def get_tile_files() -> list:
    """Return the list of local SRTM ``.tif`` tiles, loaded lazily once."""
    global _tile_files
    if _tile_files is not None:
        return _tile_files

    config_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.json")
    if not os.path.exists(config_path):
        config_path = os.path.join(os.getcwd(), "strm2stl", "config.json")

    if not os.path.exists(config_path):
        logger.warning(
            "geo2stl.tiles: config.json not found — local SRTM tiles unavailable. "
            "stitch_tiles_no_rasterio will return None."
        )
        _tile_files = []
        return _tile_files

    with open(config_path, "r") as handle:
        config = json.load(handle)

    ocean_root = config.get("ocean_root", ".")
    _tile_files = glob.glob(os.path.join(ocean_root, "*.tif"))
    return _tile_files


class TileFilesProxy:
    """Lazy proxy for ``tile_files`` to preserve the old iterable surface."""

    def __iter__(self):
        return iter(get_tile_files())

    def __len__(self):
        return len(get_tile_files())

    def __getitem__(self, idx):
        return get_tile_files()[idx]

    def __bool__(self):
        return bool(get_tile_files())


tile_files = TileFilesProxy()


def parse_extent_from_filename(filename):
    match = re.search(r"n([-\d.]+)_s([-\d.]+)_w([-\d.]+)_e([-\d.]+)", filename)
    if not match:
        raise ValueError(f"Could not parse extent from filename: {filename}")
    parts = match.groups()
    try:
        n, s, w, e = [float(part.rstrip(".")) for part in parts]
    except ValueError as exc:
        raise ValueError(f"Could not convert parts to float: {parts}") from exc
    return (n, s, e, w)


def intersect_bbox(bbox1, bbox2):
    n1, s1, e1, w1 = bbox1
    n2, s2, e2, w2 = bbox2
    north = min(n1, n2)
    south = max(s1, s2)
    east = min(e1, e2)
    west = max(w1, w2)
    if north > south and east > west:
        return (north, south, east, west)
    return None


def crop_tile_np(image_array, tile_bbox, crop_bbox):
    tile_north, tile_south, tile_east, tile_west = tile_bbox
    crop_north, crop_south, crop_east, crop_west = crop_bbox

    height, width = image_array.shape
    lat_per_pixel = (tile_north - tile_south) / height
    lon_per_pixel = (tile_east - tile_west) / width

    y1 = int((tile_north - crop_north) / lat_per_pixel)
    y2 = int((tile_north - crop_south) / lat_per_pixel)
    x1 = int((crop_west - tile_west) / lon_per_pixel)
    x2 = int((crop_east - tile_west) / lon_per_pixel)

    return image_array[y1:y2, x1:x2]


def stitch_tiles_no_rasterio(target_bbox):
    print("==== Stitching tiles ====")
    print(f"Target bounding box: {target_bbox}")

    rows = {}
    for filename in get_tile_files():
        tile_bbox = parse_extent_from_filename(os.path.basename(filename))
        intersection = intersect_bbox(tile_bbox, target_bbox)
        if not intersection:
            continue

        try:
            image_array = io.imread(filename)
        except Exception as exc:
            print(f"WARNING: Failed to open {filename}: {exc}")
            continue

        cropped = crop_tile_np(image_array, tile_bbox, intersection)
        row_key = intersection[0]
        rows.setdefault(row_key, []).append((intersection[3], cropped))

    if not rows:
        print("==== No tiles matched ====")
        return None

    stitched_rows = []
    for north in sorted(rows.keys(), reverse=True):
        tiles = sorted(rows[north], key=lambda tile: tile[0])
        stitched_rows.append(np.hstack([image for _, image in tiles]))

    final_image = np.vstack(stitched_rows)
    print("Finished stitching")
    return final_image