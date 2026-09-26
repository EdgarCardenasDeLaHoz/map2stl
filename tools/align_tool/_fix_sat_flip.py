"""Flip already-exported satellite rasters to the row 0 = south convention.

export_align_data.fetch_sat used to return the ESRI image unflipped, so every
sat.png on disk is mirrored north-south against the OSM and STL layers it is
meant to overlay. Rather than re-running the whole pipeline (minutes per city,
plus a network fetch), flip the existing PNGs in place and rebuild
align_data.js from what is on disk.

Idempotent only in the sense that running it twice undoes the fix -- run once.
"""
import base64
import json
from pathlib import Path

import cv2

DATA = Path(__file__).resolve().parent / "data"
IMAGES = ("osm_buildings", "osm_water", "sat", "stl_heightmap", "stl_mask")


def data_uri(path: Path) -> str:
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode("ascii")


merged = {}
for d in sorted(p for p in DATA.iterdir() if p.is_dir()):
    meta_path = d / "meta.json"
    sat_path = d / "sat.png"
    if not meta_path.exists():
        continue
    if sat_path.exists():
        img = cv2.imread(str(sat_path), cv2.IMREAD_COLOR)
        cv2.imwrite(str(sat_path), img[::-1])
        print(f"{d.name}: sat.png flipped")
    merged[d.name] = {
        "meta": json.loads(meta_path.read_text()),
        "images": {k: data_uri(d / f"{k}.png") for k in IMAGES if (d / f"{k}.png").exists()},
    }

js_path = DATA / "align_data.js"
js = "window.ALIGN_DATA = " + json.dumps(merged) + ";\n"
js_path.write_text(js)
print(f"Wrote {js_path} ({len(js)/1e6:.1f} MB, {len(merged)} cities)")
