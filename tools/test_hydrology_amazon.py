"""Single Amazon-bbox cold test for the new vectorized rasterizer."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.session.terrain_session import TerrainSession

s = TerrainSession(port=9001)
s.bbox = {"north": 15, "south": -19, "east": -45, "west": -85}
s.settings["dem"]["dim"] = 300
s.settings["hydrology"]["source"] = "hydrorivers"
s.settings["hydrology"]["min_order"] = 3
t0 = time.perf_counter()
s.fetch_hydrology()
print(f"\nAmazon cold: {time.perf_counter() - t0:.1f}s")
