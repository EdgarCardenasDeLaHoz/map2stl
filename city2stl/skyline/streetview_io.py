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
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import cv2
import numpy as np
import requests

from ._core.util import _load_env_file_if_present

STREETVIEW_METADATA_URL = "https://maps.googleapis.com/maps/api/streetview/metadata"
STREETVIEW_IMAGE_URL = "https://maps.googleapis.com/maps/api/streetview"
_SV_IMAGE_CACHE_DIR = Path(__file__).parent / "runs" / "image_cache"

# Retry/backoff policy for the image endpoint. Previously any non-200 was
# treated as "no imagery available" and returned None with no retry and no
# log line, so a rate-limit burst or a single 502 permanently removed views
# from a run and looked identical to a genuinely blank location. Only these
# statuses are worth retrying; 400/403/404 are terminal and mean the request
# or the key is wrong, or the pano really does not exist.
_SV_RETRY_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_SV_MAX_ATTEMPTS = 3
_SV_BACKOFF_S = 1.5

# Negative cache: how long a confirmed "no imagery here" answer is trusted.
# Google does add coverage, but not on a timescale that justifies re-asking
# for every missing pano on every run — a region with sparse coverage spent
# most of its wall clock re-confirming absences it already knew about.
_SV_NEGATIVE_TTL_S = 30 * 86400


def _negative_cache_path(cache_key: str) -> Path:
    return _SV_IMAGE_CACHE_DIR / f"{cache_key}.none"


def _negative_cache_hit(cache_key: str) -> bool:
    """True when this exact request was confirmed to have no imagery recently."""
    path = _negative_cache_path(cache_key)
    try:
        if not path.exists():
            return False
        if time.time() - path.stat().st_mtime > _SV_NEGATIVE_TTL_S:
            path.unlink(missing_ok=True)
            return False
        return True
    except Exception:
        return False


def _write_negative_cache(cache_key: str) -> None:
    """Record that this request has no imagery. Never raises."""
    try:
        _SV_IMAGE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _negative_cache_path(cache_key).write_text("", encoding="utf-8")
    except Exception:
        pass


def _streetview_get(url: str) -> tuple[bytes | None, bool]:
    """GET a signed Street View URL. Returns ``(content, definitely_absent)``.

    ``definitely_absent`` is True only for a terminal 404 — the caller may
    then write a negative-cache marker. A transient failure (retryable status,
    or a network exception, which previously propagated out of the fetch and
    aborted the whole region run) returns ``(None, False)`` after logging, so
    the view is skipped for this run but re-tried on the next one.
    """
    last = ""
    for attempt in range(_SV_MAX_ATTEMPTS):
        try:
            r = requests.get(_sign_streetview_url(url), timeout=40)
        except Exception as exc:
            last = f"{type(exc).__name__}: {exc}"
        else:
            if r.status_code == 200:
                return r.content, False
            if r.status_code == 404:
                return None, True
            if r.status_code not in _SV_RETRY_STATUS:
                print(f"[streetview] HTTP {r.status_code}, not retrying")
                return None, False
            last = f"HTTP {r.status_code}"
        if attempt < _SV_MAX_ATTEMPTS - 1:
            time.sleep(_SV_BACKOFF_S * (2 ** attempt))
    print(f"[streetview] giving up after {_SV_MAX_ATTEMPTS} attempts ({last})")
    return None, False


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

    Without URL signing, Google's Static API caps each dimension at 640, so
    the historical 960×540 default delivers 640×540 — byte-identical to what
    a 640×540 request returns (measured: mean abs diff 0.00). The excess
    width is discarded, not cropped, so ``fov`` applies to the delivered 640
    px and ``_focal_length_px`` — which reads the decoded array width — is
    correct. The clamp costs angular resolution, never accuracy.

    ``SKYLINE_SV_TALL_FRAME=1`` requests 640×640 instead. The extra 100 rows
    are genuine additional coverage, not a vertical squash: the centre 540
    rows of a 640×640 fetch match the 640×540 fetch to within JPEG noise
    (2.78) while a resize does not (16.89). Same focal length, vertical FOV
    70.6° → 80°. That headroom matters because the roof pixel *is* the
    measurement — a roofline running off the top of frame yields no height
    at all. Opt-in rather than default only because image size is part of
    the cache key, so flipping it re-fetches the whole on-disk image cache
    (~4.6k images) and breaks like-for-like comparison against earlier runs.

    With URL signing enabled the cap rises to 2048×2048 and 1280×720 is a
    real resolution bump. Signed requests get their own cache keys, so they
    don't collide with the unsigned-default cache files.
    """
    if _streetview_signing_enabled():
        return 1280, 720
    if os.environ.get("SKYLINE_SV_TALL_FRAME", "").strip().lower() in (
            "1", "true", "yes", "on"):
        return 640, 640
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

    Only a truly flat grey frame counts (a rejected frame gets a permanent negative-cache
    marker, so a false positive loses a real view for good):
    1. Near-uniform frame: std < 6 (any brightness).
    2. The bright text placeholder: no colour anywhere (95th percentile of per-pixel
       max-min channel spread <= 4), bright (mean > 150), and one grey level covering
       >= 50 % of the frame (the flat background around the text).
    Real dusk, fog and haze views are dark or low-contrast but keep per-pixel colour
    noise (chroma p95 20-33 on the 7 Chicago wickerSKY views the old channel-mean rule
    rejected) and no dominant grey level, so they pass.
    """
    if img is None or img.size == 0:
        return True
    s = float(img.std())
    if s < 6.0:
        return True
    if img.ndim != 3 or img.shape[2] < 3:
        return False
    sub = img[::4, ::4, :3].astype(np.int16)
    if float(sub.mean()) <= 150.0:
        return False
    chroma = sub.max(axis=2) - sub.min(axis=2)
    if float(np.percentile(chroma, 95)) > 4.0:
        return False
    grey = (sub.mean(axis=2) / 4.0).astype(np.int32).ravel()
    dominant = float(np.bincount(grey).max()) / float(grey.size)
    return dominant >= 0.5

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

        if _negative_cache_hit(cache_key):
            return None

        url = f"{STREETVIEW_IMAGE_URL}?{urlencode(params)}"
        content, definitely_absent = _streetview_get(url)
        if content is None:
            if definitely_absent:
                _write_negative_cache(cache_key)
            return None
        arr = np.frombuffer(content, dtype=np.uint8)
        img = cv2.imdecode(arr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        if _is_no_imagery_placeholder(rgb):
            _write_negative_cache(cache_key)
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
