"""Pick Street View panoramas around Granada landmarks and fetch one image each (F-PANOVIEW).

    python tools/eval/pano_manifest.py [--fetch] [--out runs/panoview/granada]

Without ``--fetch`` only the free Street View *metadata* API runs (it picks the nearest
Google-captured pano, not a user sphere, to each stand point). With ``--fetch`` one Static
Street View image (640x640, fov 90, pitch 10) is downloaded per view: paid, capped at
``MAX_IMAGES`` (user-approved 2026-10-20, about $0.10), never refetched when the file exists.

The key is read from ``GOOGLE_MAPS_API_KEY`` (env, else ``map2stl/.env``); it is never printed,
logged or stored: the manifest and metadata cache hold no URL.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
OUT_DEFAULT = REPO / "runs" / "panoview" / "granada"
MAX_IMAGES = 6
SIZE, FOV, PITCH = 640, 90, 10
META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
IMG_URL = "https://maps.googleapis.com/maps/api/streetview"

#: name -> (landmark lat, lon, [stand points lat, lon tried in order], note)
CATHEDRAL = (37.17660, -3.59911)
ALCAZABA = (37.17713, -3.59180)
CARLOS_V = (37.17715, -3.58960)
VIEWS = {
    "cathedral_pasiegas": (CATHEDRAL, [(37.17600, -3.59960), (37.17590, -3.59900), (37.17610, -3.60010)],
                           "Plaza de las Pasiegas, tower side"),
    "cathedral_granvia": (CATHEDRAL, [(37.17770, -3.59970), (37.17760, -3.59900), (37.17790, -3.60000)],
                          "Gran Via side"),
    "alhambra_aljibes": (ALCAZABA, [(37.17690, -3.59060), (37.17700, -3.59020), (37.17680, -3.59100)],
                         "Plaza de los Aljibes facing the Alcazaba"),
    "alhambra_san_nicolas": (CARLOS_V, [(37.18120, -3.59270), (37.18130, -3.59300), (37.18110, -3.59240)],
                             "Mirador de San Nicolas looking at the Alhambra"),
    "plaza_nueva": (ALCAZABA, [(37.17690, -3.59580), (37.17680, -3.59620), (37.17700, -3.59540)],
                    "Plaza Nueva, toward the Alhambra hill"),
    "puerta_real": (CATHEDRAL, [(37.17270, -3.59940), (37.17250, -3.59960), (37.17290, -3.59920)],
                    "Puerta Real, toward the Cathedral"),
}


def api_key() -> str:
    key = os.environ.get("GOOGLE_MAPS_API_KEY")
    if not key:
        env = REPO / ".env"
        if env.is_file():
            m = re.search(r"^\s*GOOGLE_MAPS_API_KEY\s*=\s*['\"]?([^'\"\r\n]+)", env.read_text(encoding="utf-8"), re.M)
            key = m.group(1).strip() if m else None
    if not key:
        raise SystemExit("GOOGLE_MAPS_API_KEY not set (env or map2stl/.env)")
    return key


def bearing(lat1, lon1, lat2, lon2) -> float:
    """Compass bearing in degrees (0 = north, clockwise) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _get(url: str, params: dict, key: str) -> bytes:
    q = urllib.parse.urlencode({**params, "key": key})
    try:
        with urllib.request.urlopen(f"{url}?{q}", timeout=30) as r:
            return r.read()
    except Exception as exc:  # noqa: BLE001 - never let the key reach a traceback
        raise RuntimeError(str(exc).replace(key, "<key>")) from None


def is_google(meta: dict) -> bool:
    """Google-captured panos carry "(c) 2026 Google"; user spheres carry the photographer."""
    return str(meta.get("copyright", "")).strip().lower().endswith("google")


def pick_pano(candidates: list[tuple[float, float]], key: str, cache: dict) -> dict | None:
    """First Google-captured pano the free metadata API finds near the stand points."""
    for lat, lon in candidates:
        ck = f"{lat:.5f},{lon:.5f}"
        if ck not in cache:
            raw = _get(META_URL, {"location": f"{lat},{lon}", "radius": 40, "source": "outdoor"}, key)
            cache[ck] = json.loads(raw)
        meta = cache[ck]
        if meta.get("status") == "OK" and is_google(meta):
            return meta
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", type=Path, default=OUT_DEFAULT)
    ap.add_argument("--fetch", action="store_true", help="download the (paid) images")
    a = ap.parse_args()
    out: Path = a.out
    (out / "images").mkdir(parents=True, exist_ok=True)
    key = api_key()
    mpath = out / "metadata_cache.json"
    cache = json.loads(mpath.read_text(encoding="utf-8")) if mpath.is_file() else {}
    mf = out / "manifest.json"
    old = json.loads(mf.read_text(encoding="utf-8")) if mf.is_file() else {"paid_requests": 0, "views": []}
    done = {v["name"]: v for v in old["views"]}
    paid = int(old.get("paid_requests", 0))
    views = []
    for n, (name, (target, stands, note)) in enumerate(VIEWS.items(), 1):
        if name in done and (out / done[name]["file"]).is_file():
            views.append(done[name])
            continue
        meta = pick_pano(stands, key, cache)
        mpath.write_text(json.dumps(cache, indent=1), encoding="utf-8")
        if meta is None:
            print(f"{name}: no Google pano near the stand points")
            continue
        loc = meta["location"]
        heading = round(bearing(loc["lat"], loc["lng"], *target), 1)
        rec = {"name": name, "note": note, "pano_id": meta["pano_id"], "lat": loc["lat"], "lon": loc["lng"],
               "heading": heading, "pitch": PITCH, "fov": FOV, "date": meta.get("date"),
               "copyright": meta.get("copyright"), "target_lat": target[0], "target_lon": target[1],
               "file": f"images/{n}_{name}.jpg"}
        if a.fetch:
            if paid >= MAX_IMAGES:
                print(f"{name}: image cap of {MAX_IMAGES} reached, not fetched")
            else:
                img = _get(IMG_URL, {"size": f"{SIZE}x{SIZE}", "pano": meta["pano_id"], "heading": heading,
                                     "pitch": PITCH, "fov": FOV}, key)
                (out / rec["file"]).write_bytes(img)
                paid += 1
        views.append(rec)
        print(f"{name}: pano {rec['pano_id'][:8]}.. {rec['date']} heading {heading}")
        mf.write_text(json.dumps({"paid_requests": paid, "views": views}, indent=2), encoding="utf-8")
    mf.write_text(json.dumps({"paid_requests": paid, "views": views}, indent=2), encoding="utf-8")
    print(f"paid image requests so far: {paid}/{MAX_IMAGES}")


if __name__ == "__main__":
    sys.exit(main())
