from __future__ import annotations

import math

import numpy as np


def bbox_longer_side_m(north: float, south: float, east: float, west: float) -> float:
    """Return the longer bbox side in metres, clamped to at least 1m."""
    mid_lat = (north + south) / 2.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    bbox_w_m = abs(east - west) * m_per_deg_lon
    bbox_h_m = abs(north - south) * 111_320.0
    return max(bbox_w_m, bbox_h_m, 1.0)


def derive_sat_scale(north: float, south: float, east: float, west: float, dim: int) -> int:
    """Derive a metres-per-pixel fetch scale from bbox and target dimension."""
    return max(10, int(math.ceil(bbox_longer_side_m(north, south, east, west) / dim)))


def clamp_esa_scale(north: float, south: float, east: float, west: float, sat_scale: int) -> int:
    """Clamp ESA fetch scale to stay below EE response and pixel-dimension limits."""
    bbox_w = abs(east - west)
    bbox_h = abs(north - south)
    mid_lat = (north + south) / 2.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(mid_lat))
    bbox_w_m = bbox_w * m_per_deg_lon
    bbox_h_m = bbox_h * 111_320.0

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