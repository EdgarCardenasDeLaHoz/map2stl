"""skyline._core.util — extracted from pipeline.py (A1 split)."""
from __future__ import annotations
from collections import OrderedDict as _OrderedDict

import logging
import math
import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

import cv2
import numpy as np
from scipy.ndimage import gaussian_filter1d, median_filter, uniform_filter1d
from scipy.optimize import linear_sum_assignment
from scipy.signal import find_peaks
from shapely.geometry import shape

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

def _load_env_file_if_present() -> None:
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except Exception:
        return

def _building_height_from_tags(properties: dict) -> tuple[float | None, str]:
    raw_height = properties.get("height")
    if raw_height is not None:
        try:
            digits = "".join(ch for ch in str(raw_height)
                             if (ch.isdigit() or ch == "."))
            if digits:
                return float(digits), "osm_tag"
        except Exception:
            pass

    raw_levels = properties.get("building:levels") or properties.get("levels")
    if raw_levels is not None:
        try:
            levels = float(str(raw_levels).split(";")[0])
            return max(3.0, levels * 3.4), "osm_levels"
        except Exception:
            pass

    return None, "default"

def _polygon_area_m2(coords: list[tuple[float, float]]) -> float:
    """Approximate polygon area in m^2 from lon/lat coordinates.

    Uses a local equirectangular projection around the polygon centroid.
    """
    if len(coords) < 4:
        return 0.0
    lons = np.asarray([p[0] for p in coords], dtype=np.float64)
    lats = np.asarray([p[1] for p in coords], dtype=np.float64)
    lon0 = float(np.mean(lons))
    lat0 = float(np.mean(lats))
    m_per_deg_lat = 110_540.0
    m_per_deg_lon = 111_320.0 * math.cos(math.radians(lat0))
    x = (lons - lon0) * m_per_deg_lon
    y = (lats - lat0) * m_per_deg_lat
    area = 0.5 * abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))
    return float(area)


__all__ = [
    '_load_env_file_if_present',
    '_building_height_from_tags',
    '_polygon_area_m2',
]
