"""discover_city_seeds.py — auto-generate skyline site configs from vantage
points using the Street View metadata API.

For each city we declare:
  - ``bbox``    : (north, south, east, west) OSM fetch window
  - ``target``  : (lat, lon) of the tower cluster (skyline centre)
  - ``vantages``: list of (lat, lon) waterfront / elevated viewpoints

For every vantage we (1) compute the heading = bearing(vantage -> target) so
the pano looks AT the skyline, (2) query the Street View metadata API to snap
to the nearest REAL pano (returns a valid pano_id + exact location), and (3)
emit a parseable Google Maps Street View URL into ``sites/<city>.json``.

This is the "propose pano locations -> pull -> filter" discovery loop: panos
with no Street View within ``radius_m`` (ZERO_RESULTS — e.g. a vantage that
fell in open water) are skipped, so only real, reachable seeds are written.

Run:  python -m city2stl.skyline.scripts.discover_city_seeds
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import requests

from city2stl.skyline.streetview_io import (
    _resolve_api_key,
    _streetview_metadata,
)

_SITES_DIR = Path(__file__).resolve().parent.parent / "sites"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


def _geocode(name: str, key: str) -> tuple[float, float] | None:
    """Resolve a place name (e.g. a search-derived skyline viewpoint) to
    (lat, lon) via the Google Geocoding API. Lets vantages be specified by
    NAME — the output of a "best [city] skyline viewpoint" web search —
    instead of hand-typed coordinates."""
    try:
        r = requests.get(_GEOCODE_URL, params={"address": name, "key": key},
                         timeout=20).json()
    except Exception:  # noqa: BLE001
        return None
    if r.get("status") != "OK" or not r.get("results"):
        return None
    loc = r["results"][0]["geometry"]["location"]
    return float(loc["lat"]), float(loc["lng"])


def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial great-circle bearing (deg, 0=N) from point 1 to point 2."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(p2)
    x = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(y, x)) + 360.0) % 360.0


# city -> {bbox: (N,S,E,W), target: (lat,lon) tower cluster,
#          vantages: [(lat,lon), ...] waterfront/elevated viewpoints,
#          max_h: plausible-height cap}
CITIES: dict[str, dict] = {
    "toronto": {
        "bbox": (43.680, 43.610, -79.340, -79.420), "max_h": 360,
        "target": (43.6455, -79.3810),
        # search-curated skyline viewpoints (geocoded)
        "vantages": ["Riverdale Park East, Toronto",
                     "Polson Pier, Toronto",
                     "Olympic Island, Toronto"]},
    "vancouver": {
        "bbox": (49.310, 49.270, -123.100, -123.160), "max_h": 250,
        "target": (49.2855, -123.1180),
        "vantages": [(49.2760, -123.1330), (49.3010, -123.1410),
                     (49.2735, -123.1530)]},
    "sydney": {
        "bbox": (-33.835, -33.880, 151.235, 151.190), "max_h": 320,
        "target": (-33.8650, 151.2070),
        "vantages": ["Mrs Macquaries Chair, Sydney",
                     "Blues Point Reserve, Sydney",
                     "Cremorne Point, Sydney"]},
    "singapore": {
        "bbox": (1.300, 1.265, 103.875, 103.840), "max_h": 300,
        "target": (1.2845, 103.8590),  # Marina Bay / CBD towers
        "vantages": ["Merlion Park, Singapore",
                     "Marina Barrage, Singapore",
                     "Esplanade Park, Singapore"]},
    "rio_de_janeiro": {
        "bbox": (-22.890, -22.955, -43.160, -43.200), "max_h": 160,
        "target": (-22.9100, -43.1750),
        "vantages": [(-22.9230, -43.1680), (-22.9480, -43.1800),
                     (-22.8980, -43.1700)]},
    "tel_aviv": {
        "bbox": (32.100, 32.060, 34.790, 34.755), "max_h": 240,
        "target": (32.0810, 34.7720),
        "vantages": [(32.0850, 34.7650), (32.0930, 34.7720),
                     (32.0720, 34.7640)]},
    "honolulu": {
        "bbox": (21.300, 21.275, -157.825, -157.865), "max_h": 130,
        "target": (21.2900, -157.8480),
        "vantages": [(21.2870, -157.8530), (21.2790, -157.8330),
                     (21.2950, -157.8580)]},
    "benidorm": {
        "bbox": (38.550, 38.515, -0.105, -0.160), "max_h": 200,
        "target": (38.5390, -0.1280),
        "vantages": ["Mirador del Castillo, Benidorm",
                     "Playa de Levante, Benidorm",
                     "Playa de Poniente, Benidorm"]},
    "boston": {
        "bbox": (42.380, 42.345, -71.030, -71.075), "max_h": 250,
        "target": (42.3560, -71.0560),
        "vantages": [(42.3690, -71.0380), (42.3540, -71.0440),
                     (42.3760, -71.0570)]},
    "seattle": {
        "bbox": (47.625, 47.585, -122.310, -122.380), "max_h": 290,
        "target": (47.6060, -122.3330),
        "vantages": [(47.5860, -122.3760), (47.6210, -122.3490),
                     (47.5970, -122.3160)]},
    "melbourne": {
        "bbox": (-37.805, -37.835, 144.975, 144.945), "max_h": 300,
        "target": (-37.8180, 144.9620),
        "vantages": [(-37.8235, 144.9560), (-37.8210, 144.9460),
                     (-37.8290, 144.9700)]},
    "busan": {
        "bbox": (35.175, 35.145, 129.180, 129.110), "max_h": 300,
        "target": (35.1565, 129.1450),
        "vantages": [(35.1530, 129.1180), (35.1600, 129.1700),
                     (35.1480, 129.1300)]},
    "panama_city": {
        "bbox": (9.020, 8.930, -79.450, -79.560), "max_h": 300,
        "target": (8.9850, -79.5150),
        "vantages": [(8.9760, -79.5199), (8.9830, -79.5320),
                     (8.9120, -79.5350)]},
    "hong_kong": {
        "bbox": (22.305, 22.270, 114.185, 114.140), "max_h": 480,
        "target": (22.2810, 114.1580),  # HK Island Central skyline
        "vantages": ["Tsim Sha Tsui Promenade, Hong Kong",
                     "Avenue of Stars, Hong Kong",
                     "West Kowloon Art Park, Hong Kong"]},
}


def _seed_url(lat: float, lon: float, heading: float, pano_id: str) -> str:
    return (f"https://www.google.com/maps/@{lat:.7f},{lon:.7f},3a,75y,"
            f"{heading:.2f}h,90t/data=!3m6!1e1!3m4!1s{pano_id}!2e0")


def main() -> None:
    key = _resolve_api_key()
    for city, spec in CITIES.items():
        n, s, e, w = spec["bbox"]
        tlat, tlon = spec["target"]
        urls: list[str] = []
        for vantage in spec["vantages"]:
            # A vantage may be (lat, lon) OR a place-name string (a
            # search-derived viewpoint) that we geocode on the fly.
            if isinstance(vantage, str):
                geo = _geocode(vantage, key)
                if geo is None:
                    print(f"  {city}: geocode failed for '{vantage}'")
                    continue
                vlat, vlon = geo
            else:
                vlat, vlon = vantage
            hd = _bearing(vlat, vlon, tlat, tlon)
            try:
                m = _streetview_metadata(key, vlat, vlon, hd, radius_m=350)
            except Exception as exc:  # noqa: BLE001
                print(f"  {city}: metadata error at {vlat},{vlon}: {exc}")
                continue
            if str(m.get("status")) != "OK":
                print(f"  {city}: no pano at {vlat},{vlon} "
                      f"({m.get('status')})")
                continue
            loc = m.get("location") or {}
            plat = float(loc.get("lat", vlat))
            plon = float(loc.get("lng", vlon))
            pano = str(m.get("pano_id") or "")
            if not pano:
                continue
            urls.append(_seed_url(plat, plon, hd, pano))
        cfg = {
            "name": city,
            "north": n, "south": s, "east": e, "west": w,
            "max_plausible_height_m": spec["max_h"],
            "use_satellite_footprints": False,
            "use_cross_view_scoring": True,
            "use_pano_coastline_recovery": True,
            "drive_pano_recovery_anchor": True,
            "pano_only_pdf": True,
            "seed_urls": urls,
            "anchor_offsets_deg": {},
            "negative_seeds": [],
        }
        out = _SITES_DIR / f"{city}.json"
        out.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        print(f"{city}: {len(urls)}/{len(spec['vantages'])} seeds -> {out.name}")


if __name__ == "__main__":
    main()
