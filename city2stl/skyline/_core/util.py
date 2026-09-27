"""skyline._core.util — extracted from pipeline.py (A1 split)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import numpy as np

from geo2stl.geo import M_PER_DEG_LAT, m_per_deg_lon

# F-CLEAN14: the F-SKY12 depth except-branches reference ``logger`` but the
# module never defined one (latent NameError, only reachable on a depth-module
# failure). Defined here so those branches log instead of crashing.
logger = logging.getLogger(__name__)

def _find_env_file() -> Path | None:
    """Return the nearest ``.env`` at or above this module, or None.

    This was a fixed ``parents[2]`` index, which reached the project root while
    the module lived directly in ``skyline/``. The F-CLEAN14 split moved it into
    ``skyline/_core/``, so the same index began resolving to ``city2stl/.env``
    and the real key file one level higher was never read — every region run
    then died with "Google Maps API key not found". Walking upward survives
    further moves.
    """
    for parent in Path(__file__).resolve().parents[:6]:
        candidate = parent / ".env"
        if candidate.exists():
            return candidate
    return None


def _load_env_file_if_present() -> None:
    env_path = _find_env_file()
    if env_path is None:
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
    x = (lons - lon0) * m_per_deg_lon(lat0)
    y = (lats - lat0) * M_PER_DEG_LAT
    area = 0.5 * abs(np.dot(x[:-1], y[1:]) - np.dot(x[1:], y[:-1]))
    return float(area)


__all__ = [
    '_load_env_file_if_present',
    '_polygon_area_m2',
]
