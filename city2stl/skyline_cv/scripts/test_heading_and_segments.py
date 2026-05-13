#!/usr/bin/env python3
"""Isolated tests for heading-offset fix and building-segmentation quality.

Run from the strm2stl/ directory:
    python city2stl/skyline_cv/scripts/test_heading_and_segments.py

Produces PNGs in city2stl/skyline_cv/runs/heading_test/ so you can visually
confirm that:
  1. The placeholder detection correctly rejects the dead seed panos.
  2. Location-based fallback finds a live road pano near each seed.
  3. The geographic heading offset correctly aligns each captured view with
     its geographic compass direction (label on image matches scene content).
  4. detect_building_silhouettes outlines individual buildings clearly.

No PDF generation is done — quick, cheap, no side-effects on the main run.
"""

from __future__ import annotations

import math
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cv2
import numpy as np
import requests

from city2stl.skyline_cv.pipeline import (
    _load_env_file_if_present,
    detect_building_silhouettes,
    detect_skyline_contour,
)

OUT = ROOT / "city2stl" / "skyline_cv" / "runs" / "heading_test"
OUT.mkdir(parents=True, exist_ok=True)

STREETVIEW_IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"
STREETVIEW_META_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"

# Seed definitions from cartagena.json (native heading = URL heading)
SEEDS = [
    {
        "name": "seed_1",
        "lat": 10.4069333,
        "lon": -75.5559,
        "native_heading": 321.21,
        "fov": 75.0,
        "pitch": -5.81,
        "pano_id": "CIHM0ogKEICAgIDagoD5Mg",
    },
    {
        "name": "seed_2",
        "lat": 10.3924431,
        "lon": -75.5551967,
        "native_heading": 20.84,
        "fov": 75.0,
        "pitch": -0.63,
        "pano_id": "CIHM0ogKEICAgIChjeiXtgE",
    },
]

# Geographic headings to test (should show the same variety of directions
# regardless of which pano is used — road pano or Photo Sphere)
GEO_HEADINGS_TO_TEST = [0.0, 90.0, 180.0, 270.0, 321.0]  # N, E, S, W, NW(native)


def _ch_spread(img: np.ndarray) -> float:
    if img.ndim != 3 or img.shape[2] < 3:
        return 0.0
    return float(np.std([img[:, :, c].mean() for c in range(3)]))


def _is_placeholder(img: np.ndarray) -> bool:
    if img is None or img.size == 0:
        return True
    s = float(img.std())
    m = float(img.mean())
    if s < 20.0 and m > 210.0:
        return True
    if img.ndim == 3 and img.shape[2] >= 3:
        if _ch_spread(img) < 3.0 and s < 32.0:
            return True
    return False


def _fetch_image(api_key: str, lat: float, lon: float, heading: float,
                 fov: float, pitch: float, pano_id: str | None = None,
                 radius: int = 200) -> np.ndarray | None:
    params: dict = {
        "heading": heading, "pitch": pitch, "fov": fov,
        "size": "960x540", "key": api_key,
    }
    if pano_id:
        params["pano"] = pano_id
    else:
        params["location"] = f"{lat},{lon}"
        params["radius"] = radius

    r = requests.get(STREETVIEW_IMAGE_URL, params=params, timeout=40)
    if r.status_code != 200:
        return None
    arr = np.frombuffer(r.content, dtype=np.uint8)
    img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
    if img is None:
        return None
    rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    return rgb


def _fetch_road_pano(api_key: str, lat: float, lon: float, fov: float,
                     pitch: float) -> tuple[str | None, float, float]:
    """Return (pano_id, actual_lat, actual_lon) of nearest road pano."""
    for radius in (200, 500, 1500):
        params = {"location": f"{lat},{lon}", "radius": radius,
                  "key": api_key, "source": "outdoor"}
        r = requests.get(STREETVIEW_META_URL, params=params, timeout=20)
        if r.status_code != 200:
            continue
        meta = r.json()
        if meta.get("status") != "OK":
            continue
        loc = meta.get("location", {})
        snap_lat = float(loc.get("lat", lat))
        snap_lon = float(loc.get("lng", lon))
        pano = str(meta.get("pano_id") or "") or None
        if pano:
            return pano, snap_lat, snap_lon
    return None, lat, lon


def _draw_compass_label(img: np.ndarray, text: str) -> np.ndarray:
    out = img.copy()
    cv2.putText(out, text, (12, 38), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (0, 0, 0), 5, cv2.LINE_AA)
    cv2.putText(out, text, (12, 38), cv2.FONT_HERSHEY_SIMPLEX,
                1.2, (255, 255, 255), 2, cv2.LINE_AA)
    return out


def _overlay_segments(img: np.ndarray, contour: np.ndarray,
                       silhouettes: list[dict]) -> np.ndarray:
    out = img.copy()
    # Draw contour
    for x in range(1, contour.size):
        y1, y2 = int(contour[x - 1]), int(contour[x])
        if np.isfinite(contour[x - 1]) and np.isfinite(contour[x]):
            cv2.line(out, (x - 1, y1), (x, y2), (0, 220, 255), 2)

    palette = [
        (255, 80, 80), (80, 200, 255), (180, 255, 80),
        (255, 180, 60), (200, 120, 255), (255, 240, 100),
    ]
    for i, seg in enumerate(silhouettes):
        color = palette[i % len(palette)]
        cv2.rectangle(out,
                      (seg["x_left"], seg["top_y"]),
                      (seg["x_right"], seg["base_y"]),
                      color, 2)
        # Mark peak
        cv2.circle(out, (seg["peak_x"], seg["top_y"]), 5, color, -1)
        # Label
        cv2.putText(out, f"B{i+1}", (seg["x_left"] + 4, max(20, seg["top_y"] - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
    return out


def _save_bgr(path: Path, img_rgb: np.ndarray) -> None:
    cv2.imwrite(str(path), cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR))


def test_placeholder_detection() -> None:
    print("\n=== Test 1: Placeholder detection ===")
    _load_env_file_if_present()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_API_KEY")
    if not api_key:
        print("  SKIP — no API key")
        return

    for seed in SEEDS:
        img = _fetch_image(api_key, seed["lat"], seed["lon"], 0.0,
                           seed["fov"], seed["pitch"], pano_id=seed["pano_id"])
        placeholder = _is_placeholder(img)
        spread = _ch_spread(img) if img is not None else float("nan")
        mean = float(img.mean()) if img is not None else float("nan")
        std = float(img.std()) if img is not None else float("nan")
        status = "PLACEHOLDER (will fall-back)" if placeholder else "LIVE"
        print(f"  {seed['name']} pano_id direct:  {status}")
        print(f"    mean={mean:.1f}  std={std:.1f}  ch_spread={spread:.2f}")


def test_heading_offset() -> None:
    print("\n=== Test 2: Heading offset (geographic) ===")
    _load_env_file_if_present()
    api_key = os.environ.get("GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_API_KEY")
    if not api_key:
        print("  SKIP — no API key")
        return

    for seed in SEEDS:
        print(f"\n  --- {seed['name']} (native_heading={seed['native_heading']:.1f}°) ---")

        # Find nearest road pano (Photo Sphere fallback)
        road_pano, road_lat, road_lon = _fetch_road_pano(
            api_key, seed["lat"], seed["lon"], seed["fov"], seed["pitch"])
        if road_pano is None:
            print("  No road pano found near seed, skipping.")
            continue
        print(f"  Road pano: {road_pano}  at ({road_lat:.5f}, {road_lon:.5f})")

        # Check if the URL pano itself is still alive
        seed_img = _fetch_image(api_key, seed["lat"], seed["lon"], 0.0,
                                 seed["fov"], seed["pitch"], pano_id=seed["pano_id"])
        use_photosphere = (seed_img is not None and not _is_placeholder(seed_img))
        if use_photosphere:
            print(f"  Using Photo Sphere pano (alive).  Heading offset = {seed['native_heading']:.1f}°")
        else:
            print(f"  Photo Sphere dead → using road pano (no heading offset).")

        for geo_h in GEO_HEADINGS_TO_TEST:
            if use_photosphere:
                api_h = (geo_h - seed["native_heading"] + 360.0) % 360.0
                img = _fetch_image(api_key, seed["lat"], seed["lon"], api_h,
                                   seed["fov"], seed["pitch"], pano_id=seed["pano_id"])
            else:
                img = _fetch_image(api_key, road_lat, road_lon, geo_h,
                                   seed["fov"], seed["pitch"], pano_id=road_pano)

            if img is None or _is_placeholder(img):
                print(f"    geo={geo_h:5.1f}°  → FAILED/PLACEHOLDER")
                continue

            contour, sky_mask = detect_skyline_contour(img)
            segs = detect_building_silhouettes(contour, img)
            annotated = _overlay_segments(img, contour, segs)
            label = f"geo={geo_h:.0f}deg  n_segs={len(segs)}"
            annotated = _draw_compass_label(annotated, label)

            fname = f"{seed['name']}_geo{int(geo_h):03d}.png"
            _save_bgr(OUT / fname, annotated)
            sky_frac = float(sky_mask.mean()) / 255.0
            print(f"    geo={geo_h:5.1f}°  api_h={api_h if use_photosphere else geo_h:5.1f}°  "
                  f"n_segs={len(segs):2d}  sky={sky_frac:.2f}  → {fname}")


def main() -> None:
    test_placeholder_detection()
    test_heading_offset()
    print(f"\nImages saved to: {OUT}")
    print("Review the PNGs — geographic heading labels should match scene content.")


if __name__ == "__main__":
    main()
