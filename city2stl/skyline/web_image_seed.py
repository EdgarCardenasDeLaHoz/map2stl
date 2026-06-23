"""city2stl.skyline.web_image_seed — Web-sourced skyline seeds.

Sources (tried in order, results merged and deduplicated, no API key needed):
  1. Wikipedia article lead image — the canonical skyline shot for each city.
  2. Wikimedia Commons text search — broader pool of skyline images.

Position comes from ``_KNOWN_VIEWPOINTS`` (curated lat/lon per city).
Heading comes from ``_KNOWN_HEADINGS`` or, when the caller supplies it,
from ``region_bbox_center`` (more accurate).

Usage (called by region_pdf.generate_region_report)
----------------------------------------------------
    from .web_image_seed import web_skyline_seeds
    web_seeds, web_image_cache = web_skyline_seeds(
        city_name=region_name,
        max_images=3,
        cache_dir=output_pdf.parent / "web_images",
        region_bbox_center=(bbox_lat, bbox_lon),
    )
    seeds.extend(web_seeds)
    # Pass web_image_cache to _seed_multiview_registration(..., web_image_cache=web_image_cache)
"""

from __future__ import annotations

import hashlib
import logging
import math
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import requests

if TYPE_CHECKING:
    from .region_types import SkylinePoint

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Curated viewpoints: where a photographer stands to capture the skyline.
# Used as the seed lat/lon when web images carry no GPS metadata.
# ---------------------------------------------------------------------------
_KNOWN_VIEWPOINTS: dict[str, tuple[float, float]] = {
    "cartagena":           (10.3932,  -75.4832),  # south bay, facing old city
    "chicago":             (41.8663,  -87.6069),  # Adler Planetarium pier
    "miami":               (25.7642,  -80.2086),  # Virginia Key
    "toronto":             (43.6187,  -79.3830),  # Toronto Islands
    "vancouver":           (49.3028, -123.0876),  # Lonsdale Quay
    "sydney":              (-33.8612, 151.2141),  # Mrs Macquarie's Chair
    "singapore":           ( 1.2781,  103.8618),  # Marina Bay south waterfront
    "rio_de_janeiro":      (-22.9531,  -43.1779), # Niterói waterfront
    "tel_aviv":            (32.0855,   34.7784),  # beachfront facing CBD
    "honolulu":            (21.3005, -157.8673),  # Aloha Tower waterfront
    "benidorm":            (38.5301,   -0.1290),  # south beach
    "boston":              (42.3736,  -71.0600),  # Cambridge side of Charles River
    "seattle":             (47.6300, -122.3543),  # Kerry Park, Queen Anne
    "melbourne":           (-37.8218, 144.9646),  # Southbank promenade
    "busan":               (35.1544,  129.1357),  # Gwangalli Beach
    "panama_city":         ( 8.9957,  -79.5141),  # Cinta Costera waterfront
    "hong_kong":           (22.2988,  114.1722),  # Tsim Sha Tsui waterfront
    "philadelphia":        (39.9552,  -75.1328),  # Camden NJ waterfront
    "philadelphia_pa_usa": (39.9552,  -75.1328),
}

# Bearing from each viewpoint toward the city's high-rise cluster.
# Used when region_bbox_center is not supplied by the caller.
_KNOWN_HEADINGS: dict[str, float] = {
    "cartagena":           295.0,  # south bay → old city (NW)
    "chicago":             307.0,  # Adler Pier → Loop (NW)
    "miami":                54.0,  # Virginia Key → downtown (NE)
    "toronto":              0.0,   # Islands → downtown (N)
    "vancouver":           236.0,  # Lonsdale → downtown (SW)
    "sydney":              225.0,  # Mrs Macquarie's → CBD (SW)
    "singapore":           326.0,  # Marina Bay south → CBD (NNW)
    "rio_de_janeiro":        5.0,  # Niterói → Centro (N)
    "tel_aviv":            125.0,  # beachfront → Azrieli towers (SE)
    "honolulu":             41.0,  # Aloha Tower → downtown (NE)
    "benidorm":              0.0,  # south beach → towers (N)
    "boston":              167.0,  # Cambridge → downtown (S)
    "seattle":             140.0,  # Kerry Park → downtown (SE)
    "melbourne":             0.0,  # Southbank → CBD (N)
    "busan":                60.0,  # Gwangalli → Marine City (NE)
    "panama_city":         270.0,  # Cinta Costera → towers (W)
    "hong_kong":           215.0,  # Tsim Sha Tsui → HK Island (SW)
    "philadelphia":        270.0,  # Camden → Center City (W)
    "philadelphia_pa_usa": 270.0,
}

_WIKIPEDIA_API   = "https://en.wikipedia.org/w/api.php"
_WIKIMEDIA_API   = "https://commons.wikimedia.org/w/api.php"
_UA              = "strm2stl/1.0 skyline-seed (github.com/strm2stl)"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bearing(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Initial bearing from (lat1,lon1) to (lat2,lon2) in degrees [0,360)."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lon2 - lon1)
    x = math.sin(dl) * math.cos(phi2)
    y = (math.cos(phi1) * math.sin(phi2)
         - math.sin(phi1) * math.cos(phi2) * math.cos(dl))
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def _url_cache_path(url: str, cache_dir: Path) -> Path:
    digest = hashlib.md5(url.encode()).hexdigest()[:12]
    return cache_dir / f"{digest}.jpg"


def _download_image(url: str, cache_dir: Path | None) -> np.ndarray | None:
    """Download URL → RGB numpy array via cv2; return None on failure."""
    try:
        import cv2  # noqa: PLC0415
    except ImportError:
        return None

    if cache_dir is not None:
        cache_path = _url_cache_path(url, cache_dir)
        if cache_path.exists():
            img = cv2.imdecode(
                np.frombuffer(cache_path.read_bytes(), dtype=np.uint8),
                cv2.IMREAD_COLOR,
            )
            if img is not None:
                return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    try:
        resp = requests.get(url, timeout=15, headers={"User-Agent": _UA})
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("web_image download failed %s: %s", url, exc)
        return None

    raw = np.frombuffer(resp.content, dtype=np.uint8)
    img = cv2.imdecode(raw, cv2.IMREAD_COLOR)
    if img is None:
        return None

    if cache_dir is not None:
        cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path.write_bytes(resp.content)

    return cv2.cvtColor(img, cv2.COLOR_BGR2RGB)


# ---------------------------------------------------------------------------
# Image sources
# ---------------------------------------------------------------------------

def _wikipedia_lead_image(city_name: str) -> list[dict]:
    """Return the Wikipedia article lead image for a city (0 or 1 result).

    Tries the plain city name first, then "<City> skyline".  The lead image
    on major city articles is almost always the canonical skyline shot.
    """
    display = city_name.replace("_", " ").title()
    for title in [display, f"{display} skyline"]:
        params = {
            "action": "query",
            "titles": title,
            "prop": "pageimages",
            "piprop": "thumbnail",
            "pithumbsize": 1280,
            "format": "json",
            "redirects": "1",
        }
        try:
            resp = requests.get(_WIKIPEDIA_API, params=params, timeout=10,
                                headers={"User-Agent": _UA})
            resp.raise_for_status()
            pages = resp.json().get("query", {}).get("pages", {})
            for page in pages.values():
                if page.get("pageid", -1) < 0:
                    continue  # article not found
                url = (page.get("thumbnail") or {}).get("source")
                if url:
                    return [{"url": url, "lat": None, "lon": None,
                             "title": page.get("title", title)}]
        except Exception as exc:
            logger.debug("Wikipedia lead image failed for %r: %s", title, exc)
    return []


def _wikimedia_search(city_name: str, max_photos: int = 3) -> list[dict]:
    """Search Wikimedia Commons for skyline images (no API key needed)."""
    params = {
        "action": "query",
        "list": "search",
        "srsearch": f"{city_name.replace('_', ' ')} skyline",
        "srnamespace": "6",
        "srlimit": max_photos * 4,
        "format": "json",
    }
    try:
        resp = requests.get(_WIKIMEDIA_API, params=params, timeout=10,
                            headers={"User-Agent": _UA})
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        logger.debug("Wikimedia search failed for %r: %s", city_name, exc)
        return []

    titles = [r["title"] for r in data.get("query", {}).get("search", [])]
    results = []
    for title in titles[:max_photos * 2]:
        info_params = {
            "action": "query",
            "titles": title,
            "prop": "imageinfo",
            "iiprop": "url",
            "iiurlwidth": "1280",
            "format": "json",
        }
        try:
            ir = requests.get(_WIKIMEDIA_API, params=info_params, timeout=8,
                              headers={"User-Agent": _UA})
            ir.raise_for_status()
            pages = ir.json().get("query", {}).get("pages", {})
            for page in pages.values():
                iinfo = (page.get("imageinfo") or [{}])[0]
                url = iinfo.get("thumburl") or iinfo.get("url")
                if url:
                    results.append({"url": url, "lat": None, "lon": None,
                                    "title": title})
        except Exception:
            pass
        if len(results) >= max_photos:
            break
    return results


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def web_skyline_seeds(
    city_name: str,
    max_images: int = 3,
    cache_dir: Path | None = None,
    region_bbox_center: tuple[float, float] | None = None,
    # kept for call-site compatibility; Flickr is no longer used
    flickr_api_key: str = "",
) -> tuple[list["SkylinePoint"], dict[str, np.ndarray]]:
    """Fetch web skyline images and return (SkylinePoint list, image cache).

    Parameters
    ----------
    city_name          : Region name matching ``_KNOWN_VIEWPOINTS`` keys
                         (e.g. "cartagena", "hong_kong").
    max_images         : Maximum number of seeds to return (default 3).
    cache_dir          : Directory for downloaded image disk cache.
    region_bbox_center : (lat, lon) of city centre; improves heading accuracy.
    flickr_api_key     : Ignored — retained so existing call sites don't break.
    """
    from .region_types import SkylinePoint  # noqa: PLC0415

    city_key = city_name.lower().replace(" ", "_")
    viewpoint = _KNOWN_VIEWPOINTS.get(city_key)

    # --- gather candidates from Wikipedia then Wikimedia, deduplicating ---
    candidates: list[dict] = []
    seen_urls: set[str] = set()

    def _add(items: list[dict]) -> None:
        for item in items:
            u = item.get("url") or ""
            if u and u in seen_urls:
                return
            if u:
                seen_urls.add(u)
            candidates.append(item)

    wiki_lead = _wikipedia_lead_image(city_name)
    _add(wiki_lead)
    logger.info("[web_seed] Wikipedia lead image: %d result(s) for %r",
                len(wiki_lead), city_name)

    commons_results = _wikimedia_search(city_name, max_images)
    _add(commons_results)
    logger.info("[web_seed] Wikimedia Commons: %d result(s) for %r",
                len(commons_results), city_name)

    print(f"[web_seed] {city_name!r}: {len(candidates)} total candidates "
          f"(Wikipedia + Wikimedia Commons)")

    if not candidates and viewpoint is None:
        logger.info("[web_seed] no images and no known viewpoint for %r", city_name)
        return [], {}

    # Ensure at least one seed when we have a known viewpoint but no images.
    if not candidates and viewpoint is not None:
        candidates = [{"url": None, "lat": viewpoint[0], "lon": viewpoint[1],
                       "title": f"{city_name} skyline (position-only fallback)"}]

    seeds: list[SkylinePoint] = []
    image_cache: dict[str, np.ndarray] = {}

    for idx, cand in enumerate(candidates):
        if len(seeds) >= max_images:
            break

        url = cand.get("url")
        img: np.ndarray | None = None
        if url:
            img = _download_image(url, cache_dir)
        if img is None:
            continue

        # Position: image GPS (Flickr) → known viewpoint → skip
        lat = cand.get("lat")
        lon = cand.get("lon")
        if lat is None or lon is None:
            if viewpoint is not None:
                lat, lon = viewpoint
            else:
                logger.debug("[web_seed] no location for %r, skipping", url)
                continue

        # Heading: bbox centre (accurate) → known heading table → rough N
        if region_bbox_center is not None:
            heading = _bearing(lat, lon, *region_bbox_center)
        elif city_key in _KNOWN_HEADINGS:
            heading = _KNOWN_HEADINGS[city_key]
        else:
            heading = 0.0  # due north — pipeline will correct via anchor recovery

        seed_name = f"web_{idx + 1}"
        seed = SkylinePoint(
            name=seed_name,
            lat=float(lat),
            lon=float(lon),
            heading=float(heading),
            source="web",
            score=0.8,
            fov=80.0,
            pitch=0.0,
            pano_id=None,
        )
        seeds.append(seed)
        image_cache[seed_name] = img
        print(
            f"[web_seed] {seed_name}: {cand.get('title', '')!r}  "
            f"lat={lat:.4f} lon={lon:.4f}  heading={heading:.0f}°"
        )

    return seeds, image_cache
