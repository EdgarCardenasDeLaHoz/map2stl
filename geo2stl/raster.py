"""Raster-scale utilities shared by terrain and satellite layer flows."""

from __future__ import annotations

import math

from geo2stl.geo import bbox_size_m


def bbox_longer_side_m(north: float, south: float, east: float, west: float) -> float:
    """Return the longer bbox side in metres, clamped to at least 1m."""
    return max(*bbox_size_m({"north": north, "south": south, "east": east, "west": west}), 1.0)


def derive_sat_scale(north: float, south: float, east: float, west: float, dim: int) -> int:
    """Derive a metres-per-pixel fetch scale from bbox and target dimension."""
    return max(10, int(math.ceil(bbox_longer_side_m(north, south, east, west) / dim)))


def clamp_esa_scale(north: float, south: float, east: float, west: float, sat_scale: int) -> int:
    """Clamp ESA fetch scale to stay below EE response and pixel-dimension limits."""
    bbox_w_m, bbox_h_m = bbox_size_m({"north": north, "south": south, "east": east, "west": west})

    max_esa_px = 50_331_648 // 2
    est_px = (bbox_w_m / sat_scale) * (bbox_h_m / sat_scale)
    if est_px > max_esa_px:
        sat_scale = max(
            sat_scale,
            int(math.ceil(math.sqrt(bbox_w_m * bbox_h_m / max_esa_px))),
        )

    min_safe_dim = max(
        int(math.ceil(bbox_w_m / 32768)),
        int(math.ceil(bbox_h_m / 32768)),
        1,
    )
    return max(sat_scale, min_safe_dim)
