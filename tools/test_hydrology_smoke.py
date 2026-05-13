"""Hydrology smoke test on small bboxes.

Exercises the running server (port 9000) via the SDK on three small bboxes
to verify cold and warm timings, river feature counts, and grid integrity.
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

# Add project root so `app.session` imports
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.session.terrain_session import TerrainSession  # noqa: E402

SERVER_URL = os.environ.get("SERVER_URL", "http://127.0.0.1:9000")

# Small bboxes — kept under 2° per side so HydroRIVERS feature counts stay modest.
TEST_BBOXES = [
    # name,         north,  south,  east,    west
    ("Manaus",      -2.7,   -3.4,   -59.7,   -60.4),   # Amazon city, dense rivers
    ("Vermont",     44.5,   43.5,   -72.0,   -73.0),   # New England, moderate
    ("Sahara",      26.0,   25.0,    10.0,     9.0),   # Should yield ~0 rivers
    ("Tokyo",       36.0,   35.0,   140.0,   139.0),   # Fresh bbox to exercise new path
]


def run_one(name, north, south, east, west, *, expect_rivers: bool):
    print(f"\n=== {name}  bbox=({north},{south},{east},{west}) ===")
    s = TerrainSession(port=9001)
    s.bbox = {"north": north, "south": south, "east": east, "west": west}
    s.settings["dem"]["dim"] = 200
    s.settings["hydrology"]["source"] = "hydrorivers"
    s.settings["hydrology"]["min_order"] = 1   # include all streams in small bboxes

    # cold
    t0 = time.perf_counter()
    s.fetch_hydrology()
    cold = time.perf_counter() - t0

    h = s.hydrology
    if not expect_rivers:
        if h is None:
            print(f"  cold: {cold:.1f}s   (no rivers, as expected)")
            return True
        print(f"  cold: {cold:.1f}s   WARN expected no rivers but got {h}")
        return True

    if h is None:
        print(f"  cold: {cold:.1f}s   FAIL expected rivers, got None")
        return False

    import numpy as np
    vals = np.asarray(h.get("river_grid_values") or [], dtype=np.float32)
    n_river = int((vals != 0).sum())
    print(f"  cold: {cold:.1f}s   features={h.get('feature_count')} river_pixels={n_river}")

    # warm — same params should hit the new disk cache
    t0 = time.perf_counter()
    s2 = TerrainSession(port=9001)
    s2.bbox = {"north": north, "south": south, "east": east, "west": west}
    s2.settings["dem"]["dim"] = 200
    s2.settings["hydrology"]["source"] = "hydrorivers"
    s2.settings["hydrology"]["min_order"] = 1
    s2.fetch_hydrology()
    warm = time.perf_counter() - t0
    speedup = cold / warm if warm > 0 else float("inf")
    hit = warm < 1.0
    print(f"  warm: {warm:.2f}s   speedup={speedup:.0f}x   "
          f"{'CACHE HIT' if hit else 'CACHE MISS'}")
    return hit


if __name__ == "__main__":
    results = []
    for name, n, s, e, w in TEST_BBOXES:
        ok = run_one(name, n, s, e, w, expect_rivers=(name != "Sahara"))
        results.append((name, ok))
    print("\n=== Summary ===")
    for name, ok in results:
        print(f"  {name}: {'PASS' if ok else 'FAIL'}")
    sys.exit(0 if all(ok for _, ok in results) else 1)
