"""city2stl.skyline.streetview_io - Google Street View Static API I/O.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). URL parsing/signing,
metadata + image fetch (with the on-disk image cache), no-imagery detection,
and API-key resolution. No OSM/region data, no rendering. region_pdf
re-imports these.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import cv2
import numpy as np
import requests

from .pipeline import _load_env_file_if_present


STREETVIEW_METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
STREETVIEW_IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"
_SV_IMAGE_CACHE_DIR = Path(__file__).parent / "runs" / "image_cache"

def _resolve_api_key(explicit_key: str | None = None) -> str:
    _load_env_file_if_present()
    key = explicit_key or os.environ.get(
        "GOOGLE_MAPS_API_KEY") or os.environ.get("GOOGLE_STREETVIEW_API_KEY")
    if not key:
        raise RuntimeError(
            "Google Maps API key not found. Set GOOGLE_MAPS_API_KEY or pass --api-key")
    return key

def _extract_pano_id(url: str) -> str | None:
    """Pull the pano id out of a Google Maps Street View URL.

    Google encodes the pano id in the data segment as `1s<panoid>!2e10`
    (e.g. `1sCIHM0ogKEICAgIDagoD5Mg!2e10!3e11`). The pano id is the slug
    between `!1s` and the next `!`.
    """
    if not url:
        return None
    marker = "!1s"
    idx = url.find(marker)
    if idx < 0:
        return None
    start = idx + len(marker)
    end = url.find("!", start)
    if end < 0:
        end = len(url)
    pano = url[start:end].strip()
    return pano or None

def _parse_streetview_url(url: str) -> tuple[float, float, float, float, float, str | None] | None:
    """Parse a Google Maps Street View URL.

    Returns (lat, lon, heading, fov, pitch, pano_id) where pano_id may be None
    if the URL doesn't carry one.

    Google Street View URL format after `@`:
      lat,lon,3a,<FOV>y,<HEADING>h,<TILT>t
    where TILT is 90° at the horizon (>90 looks down). We expose `pitch` in the
    Street View Static API convention (positive = up), so pitch = 90 - tilt.
    """
    text = url.strip()
    if not text:
        return None

    pano_id = _extract_pano_id(text)

    if "@" in text:
        try:
            segment = text.split("@", 1)[1].split("/", 1)[0]
            parts = segment.split(",")
            lat = float(parts[0])
            lon = float(parts[1])
            heading = 0.0
            fov = 80.0
            pitch = 0.0
            for p in parts[2:]:
                if not p:
                    continue
                tag = p[-1]
                try:
                    val = float(p[:-1])
                except ValueError:
                    continue
                if tag == "h":
                    heading = val
                elif tag == "y":
                    fov = val
                elif tag == "t":
                    pitch = 90.0 - val
            return lat, lon, heading % 360.0, fov, pitch, pano_id
        except Exception:
            pass

    try:
        parsed = urlparse(text)
        qs = parse_qs(parsed.query)
        lat = float(qs.get("lat", [""])[0])
        lon = float(qs.get("lon", [""])[0])
        heading = float(qs.get("heading", ["0"])[0]) % 360.0
        fov = float(qs.get("fov", ["80"])[0])
        pitch = float(qs.get("pitch", ["0"])[0])
        return lat, lon, heading, fov, pitch, pano_id
    except Exception:
        return None

def _streetview_signing_enabled() -> bool:
    return bool(os.environ.get("GOOGLE_MAPS_SIGN_SECRET", "").strip())

def _default_streetview_image_size() -> tuple[int, int]:
    """Default (width, height) for spin-view fetches.

    Without URL signing, Google's Static API caps unsigned requests at
    640×640 — a request for 1280×720 silently delivers 640×540 (height
    honoured, width clamped). We keep the historical 960×540 default in
    the unsigned path to preserve the existing on-disk image cache:
    swapping to 640×540 would invalidate ~120 cached images per region.

    With URL signing enabled the cap rises to 2048×2048 and 1280×720
    becomes a meaningful resolution bump. Signed requests get their own
    cache keys (size is part of the cache hash), so they don't collide
    with the unsigned-default cache files.
    """
    if _streetview_signing_enabled():
        return 1280, 720
    return 960, 540

def _sign_streetview_url(url: str) -> str:
    """Append a Google Maps URL signature when ``GOOGLE_MAPS_SIGN_SECRET``
    is set. Returns the URL unchanged when the env var is missing.

    Unsigned Street View Static API requests are capped by Google at
    640×640 image dimensions, regardless of what we ask for — so a 960×540
    request silently delivers 640×540. Signed requests can go up to
    2048×2048, the actual leverage for clearer Cartagena imagery.

    The signing secret is the URL-safe base64 string from Google Cloud
    Console → APIs & Services → Credentials → URL signing secret. The
    signature itself is NOT part of the local cache key (see _do_get),
    so rotating secrets does not invalidate the on-disk image cache.
    """
    secret = os.environ.get("GOOGLE_MAPS_SIGN_SECRET", "").strip()
    if not secret:
        return url
    parsed = urlparse(url)
    path_and_query = parsed.path + ("?" + parsed.query if parsed.query else "")
    try:
        key = base64.urlsafe_b64decode(secret)
    except Exception:
        # Malformed secret — emit unsigned URL rather than crashing the run.
        return url
    sig = hmac.new(key, path_and_query.encode("utf-8"), hashlib.sha1)
    encoded_sig = base64.urlsafe_b64encode(sig.digest()).decode()
    sep = "&" if parsed.query else "?"
    return f"{url}{sep}signature={encoded_sig}"

def _streetview_metadata(
    api_key: str,
    lat: float,
    lon: float,
    heading: float,
    fov: float = 80.0,
    pitch: float = 0.0,
    width: int = 640,
    height: int = 360,
    pano_id: str | None = None,
    radius_m: int = 200,
) -> dict:
    """Fetch Street View metadata.

    When ``pano_id`` is given, try that exact pano first. User-contributed
    Photo Sphere pano IDs from interactive maps URLs are not in the Static
    API's database and return ``ZERO_RESULTS``; we then fall back to
    ``location=lat,lon`` with an enlarged ``radius_m`` so the API can snap
    to the nearest official road pano.
    """
    base = {
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "size": f"{width}x{height}",
        "key": api_key,
    }
    if pano_id:
        params = {**base, "pano": pano_id}
        url = f"{STREETVIEW_METADATA_URL}?{urlencode(params)}"
        r = requests.get(_sign_streetview_url(url), timeout=30)
        r.raise_for_status()
        meta = r.json()
        if str(meta.get("status")) == "OK":
            return meta
        # fall through to location-based lookup

    params = {**base, "location": f"{lat},{lon}",
              "radius": radius_m, "source": "outdoor"}
    url = f"{STREETVIEW_METADATA_URL}?{urlencode(params)}"
    r = requests.get(_sign_streetview_url(url), timeout=30)
    r.raise_for_status()
    return r.json()

def _is_no_imagery_placeholder(img: np.ndarray) -> bool:
    """Detect Google's gray 'Sorry, we have no imagery here' placeholder.

    Two known placeholder styles are caught:
    1. Classic near-uniform bright-gray frame (mean > 210, std < 20).
    2. Current-style placeholder: mean ≈ 187, std ≈ 26 but nearly perfectly
       grayscale (per-channel mean spread < 3.0).  Real Street View imagery
       always has meaningful colour variation (spread typically 8–40+).
       A std cap of < 32 prevents misclassifying real low-contrast dawn/dusk
       shots that happen to be near-monochrome.
    """
    if img is None or img.size == 0:
        return True
    s = float(img.std())
    m = float(img.mean())
    # Style 1: original near-uniform bright gray
    if s < 20.0 and m > 210.0:
        return True
    # Style 2: current placeholder — monochromatic but lower mean
    if img.ndim == 3 and img.shape[2] >= 3:
        ch_means = [float(img[:, :, c].mean()) for c in range(3)]
        ch_spread = float(np.std(ch_means))
        if ch_spread < 3.0 and s < 32.0:
            return True
    return False

def _streetview_image(
    api_key: str,
    lat: float,
    lon: float,
    heading: float,
    fov: float = 80.0,
    pitch: float = 0.0,
    width: int | None = None,
    height: int | None = None,
    pano_id: str | None = None,
    radius_m: int = 200,
    pano_only: bool = False,
) -> np.ndarray | None:
    """Capture a Street View image. Returns None when no real imagery is
    available (including when the API returns its gray no-imagery placeholder).

    When *pano_only* is True and a pano_id is supplied, only the pano-id-based
    fetch is attempted — the location fallback is skipped.  Use this to probe
    whether a specific pano actually renders without silently obtaining a
    nearby road pano instead.
    """
    if width is None or height is None:
        dw, dh = _default_streetview_image_size()
        if width is None:
            width = dw
        if height is None:
            height = dh
    base = {
        "heading": heading,
        "pitch": pitch,
        "fov": fov,
        "size": f"{width}x{height}",
        "key": api_key,
    }

    def _do_get(params: dict) -> np.ndarray | None:
        # Build a stable cache key from params without the API key. The
        # signature (added by _sign_streetview_url) is deliberately NOT
        # part of the cache key — the same logical request signed with a
        # rotated secret returns the same image bytes.
        cache_params = {k: v for k, v in params.items() if k != "key"}
        cache_key = hashlib.sha1(
            json.dumps(cache_params, sort_keys=True).encode()
        ).hexdigest()
        _SV_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_path = _SV_IMAGE_CACHE_DIR / f"{cache_key}.png"

        if cache_path.exists():
            img = cv2.imread(str(cache_path), cv2.IMREAD_COLOR)
            if img is not None:
                return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

        url = f"{STREETVIEW_IMAGE_URL}?{urlencode(params)}"
        r = requests.get(_sign_streetview_url(url), timeout=40)
        if r.status_code != 200:
            return None
        arr = np.frombuffer(r.content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if _is_no_imagery_placeholder(rgb):
            return None
        # Persist to disk — BGR for cv2.imwrite.
        cv2.imwrite(str(cache_path), img)
        return rgb

    if pano_id:
        img = _do_get({**base, "pano": pano_id})
        if img is not None:
            return img
        # pano_id didn't render — stop here when caller only wants the pano.
        # IMPORTANT: callers that have applied a Photo-Sphere heading offset
        # (api_heading = geo_heading - seed.heading) MUST pass pano_only=True.
        # Falling back to a road pano at the same lat/lon and applying that
        # offset on top is the source of the cone-vs-image mismatch the user
        # observed — the labeled heading and the actual image disagree by the
        # URL's `h` value.
        if pano_only:
            return None
        # Otherwise try progressively larger location-based radii.
    for r in (radius_m, max(radius_m, 500), max(radius_m, 1500)):
        img = _do_get({**base, "location": f"{lat},{lon}",
                      "radius": r, "source": "outdoor"})
        if img is not None:
            return img
    return None

def _meta_location(meta: dict) -> tuple[float, float] | None:
    """Extract (lat, lon) of the actual pano the API returned, if available."""
    loc = meta.get("location") if isinstance(meta, dict) else None
    if isinstance(loc, dict):
        try:
            return float(loc["lat"]), float(loc["lng"])
        except Exception:
            return None
    return None
