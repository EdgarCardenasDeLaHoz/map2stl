"""Skyline photos from Wikimedia Commons with the camera data they carry (F-WEB2).

Why: the best skyline shots are taken from the water, cruise ships, stations and rooftops,
not by Street View at street height, and Commons photos are free-licensed and often record
where the camera stood. Miami's skyline categories (2026-10-04): 312 files, 85 with a camera
location, 138 with a 35 mm-equivalent focal length, 18 with a compass direction. F-WEB1
(``web_image_seed``) guessed all of that (a curated viewpoint, the bearing to the bbox centre,
FOV 80 deg); this module reads it from the file.

    photos = find_skyline_photos("Miami", bbox_nsew, max_photos=5)
    for p in photos: p.title, p.lat, p.lon, p.heading_deg, p.hfov_deg, p.url, p.attribution

Search: the city's Commons categories whose name contains "skyline" (found by category
search, recursing only into "skyline" subcategories), or ``categories`` given explicitly.
Ranking: located near the region, landscape (<= 3:1, see ``MAX_ASPECT``), then newest, compass
present, widest; night photos are dropped after download (``is_dark``). Network: the Commons API only, with a
descriptive User-Agent as Wikimedia asks; no key.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field

import requests

logger = logging.getLogger(__name__)

API = "https://commons.wikimedia.org/w/api.php"
UA = "map2stl-skyline/0.2 (https://github.com/EdgarCardenasDeLaHoz/map2stl; research)"
TIMEOUT_S = 60
#: Full-frame (36 x 24 mm) diagonal: a 35 mm-equivalent focal length is defined on it.
FULL_FRAME_DIAG_MM = math.hypot(36.0, 24.0)
#: Width the photos are fetched at (Commons thumbnail); registration works in pixels.
FETCH_WIDTH = 2048
#: Photos wider than this aspect are stitched panoramas (cylindrical), skipped in v1.
MAX_ASPECT = 3.0
#: Camera may stand outside the region bbox (a ship offshore), up to this far, km. 8 km
#: dropped Miami's "skyline from the ocean" (2020), a good offshore telephoto.
MAX_OUTSIDE_KM = 15.0
#: Night skylines segment badly. Judged from the image (``is_dark``), not the EXIF hour:
#: camera clocks are often in the wrong time zone, and the hour rule dropped 8 of Miami's
#: 19 "night" photos that were broad daylight (2026-10-04 review). Measured on the top third:
#: luma alone called a deep-blue noon sky dark (Bridgemiami: luma 71, value 134), so dark is
#: value < DARK_SKY_VALUE, or luma < DARK_SKY_LUMA with value < DIM_SKY_VALUE (a storm or
#: lit-city night: luma 75, value 100). Miami: night 4-100 by value, day and dusk 128-221.
DARK_SKY_VALUE = 90.0
DARK_SKY_LUMA = 85.0
DIM_SKY_VALUE = 110.0
MAX_CATEGORIES = 40


@dataclass(frozen=True)
class CommonsPhoto:
    title: str
    url: str                      # thumbnail at FETCH_WIDTH (or the original if smaller)
    page_url: str
    width: int
    height: int
    lat: float | None
    lon: float | None
    heading_deg: float | None     # EXIF GPSImgDirection
    heading_ref: str | None       # "T" true north, "M" magnetic
    hfov_deg: float | None        # from FocalLengthIn35mmFilm
    taken: str | None             # EXIF DateTimeOriginal, "YYYY:MM:DD HH:MM:SS"
    author: str = ""
    licence: str = ""
    notes: list[str] = field(default_factory=list)

    @property
    def attribution(self) -> str:
        return f"{self.author or 'unknown author'}, {self.licence or 'licence unknown'}, {self.page_url}"

    @property
    def year(self) -> int | None:
        m = re.match(r"(\d{4})", self.taken or "")
        return int(m.group(1)) if m else None

    @property
    def hour(self) -> int | None:
        m = re.match(r"\d{4}:\d{2}:\d{2} (\d{2})", self.taken or "")
        return int(m.group(1)) if m else None


# --------------------------------------------------------------------------- parsing


def _num(v) -> float | None:
    """EXIF numbers arrive as numbers, "a/b" rationals or strings."""
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip()
    m = re.fullmatch(r"(-?\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", s)
    if m:
        den = float(m.group(2))
        return float(m.group(1)) / den if den else None
    try:
        return float(s)
    except ValueError:
        return None


def hfov_from_35mm(f35: float, width: int, height: int) -> float:
    """Horizontal field of view (deg) of a ``width`` x ``height`` image from its 35 mm-equivalent
    focal length. The equivalence is defined on the full-frame diagonal, so this holds for any
    aspect ratio and orientation."""
    half_diag = math.atan(FULL_FRAME_DIAG_MM / (2.0 * f35))
    return math.degrees(2.0 * math.atan(math.tan(half_diag) * width / math.hypot(width, height)))


def _metadata(ii: dict) -> dict:
    out = {}
    for m in ii.get("metadata") or []:
        if isinstance(m, dict) and "name" in m:
            out[m["name"]] = m.get("value")
    return out


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def photo_from_page(page: dict) -> CommonsPhoto | None:
    """A ``CommonsPhoto`` from one API page (prop=coordinates|imageinfo), or None if not a JPEG."""
    ii = (page.get("imageinfo") or [{}])[0]
    if ii.get("mime") != "image/jpeg":
        return None
    md = _metadata(ii)
    ext = ii.get("extmetadata") or {}
    w, h = int(ii.get("width") or 0), int(ii.get("height") or 0)
    co = page.get("coordinates") or []
    lat = lon = None
    if co:
        lat, lon = float(co[0]["lat"]), float(co[0]["lon"])
    f35 = _num(md.get("FocalLengthIn35mmFilm"))
    heading = _num(md.get("GPSImgDirection"))
    return CommonsPhoto(
        title=page["title"],
        url=ii.get("thumburl") or ii.get("url") or "",
        page_url=ii.get("descriptionurl") or "",
        width=w, height=h, lat=lat, lon=lon,
        heading_deg=None if heading is None else heading % 360.0,
        heading_ref=(str(md["GPSImgDirectionRef"]).strip().upper()[:1]
                     if md.get("GPSImgDirectionRef") else None),
        hfov_deg=hfov_from_35mm(f35, w, h) if f35 and w and h else None,
        taken=str(md["DateTimeOriginal"]) if md.get("DateTimeOriginal") else None,
        author=_strip_html((ext.get("Artist") or {}).get("value", "")),
        licence=(ext.get("LicenseShortName") or {}).get("value", ""),
    )


# --------------------------------------------------------------------------- API


def _query(session: requests.Session, **params) -> dict:
    params.update(format="json", formatversion=2)
    r = session.get(API, params=params, timeout=TIMEOUT_S)
    r.raise_for_status()
    return r.json()


def skyline_categories(session: requests.Session, city: str) -> list[str]:
    """Commons categories named like "<city> ... skyline(s)" (category search, ns 14)."""
    r = _query(session, action="query", list="search", srsearch=f"{city} skyline",
               srnamespace=14, srlimit=50)
    words = [w.lower() for w in re.findall(r"\w+", city)]
    out = []
    for s in r.get("query", {}).get("search", []):
        t = s["title"].lower()
        if "skyline" in t and all(w in t for w in words):
            out.append(s["title"])
    return out


def category_files(session: requests.Session, categories: list[str]) -> list[str]:
    """File titles in ``categories`` and their "skyline" subcategories."""
    todo, seen, files = list(categories), set(categories), []
    while todo:
        cat, cont = todo.pop(), {}
        while True:
            r = _query(session, action="query", list="categorymembers", cmtitle=cat,
                       cmtype="file|subcat", cmlimit=500, **cont)
            for m in r.get("query", {}).get("categorymembers", []):
                if m["ns"] == 14 and "skyline" in m["title"].lower() and m["title"] not in seen \
                        and len(seen) < MAX_CATEGORIES:
                    seen.add(m["title"])
                    todo.append(m["title"])
                elif m["ns"] == 6:
                    files.append(m["title"])
            if "continue" not in r:
                break
            cont = {"cmcontinue": r["continue"]["cmcontinue"]}
    return list(dict.fromkeys(files))


def describe_files(session: requests.Session, titles: list[str]) -> list[CommonsPhoto]:
    out = []
    for i in range(0, len(titles), 50):
        r = _query(session, action="query", titles="|".join(titles[i:i + 50]),
                   prop="coordinates|imageinfo", coprimary="all", colimit=500,
                   iiprop="url|size|mime|metadata|extmetadata", iiurlwidth=FETCH_WIDTH,
                   iiextmetadatafilter="Artist|LicenseShortName")
        for page in r.get("query", {}).get("pages", []):
            p = photo_from_page(page)
            if p is not None:
                out.append(p)
    return out


# --------------------------------------------------------------------------- ranking


def _km_outside(lat: float, lon: float, bbox_nsew) -> float:
    n, s, e, w = bbox_nsew
    dlat = max(s - lat, 0.0, lat - n) * 111.32
    dlon = max(w - lon, 0.0, lon - e) * 111.32 * math.cos(math.radians(lat))
    return math.hypot(dlat, dlon)


def usable(p: CommonsPhoto, bbox_nsew) -> str | None:
    """Why ``p`` cannot be a seed, or None when it can."""
    if p.lat is None or p.lon is None:
        return "no camera location"
    if _km_outside(p.lat, p.lon, bbox_nsew) > MAX_OUTSIDE_KM:
        return "camera far from region"
    if not p.width or not p.height or p.width / p.height > MAX_ASPECT:
        return "panorama wider than 3:1"
    if p.height > p.width:
        return "portrait"
    return None


def is_dark(img) -> bool:
    """True for a night or deep-dusk photo, from the top third of the image (mostly sky).

    ``img``: H x W x 3 uint8 RGB. Applied after download, since only pixels can tell.
    """
    import numpy as np

    top = np.asarray(img[: max(1, img.shape[0] // 3)], dtype=np.float32)
    luma = float(np.median(0.299 * top[..., 0] + 0.587 * top[..., 1] + 0.114 * top[..., 2]))
    value = float(np.median(top.max(axis=2)))
    return value < DARK_SKY_VALUE or (luma < DARK_SKY_LUMA and value < DIM_SKY_VALUE)


def rank_key(p: CommonsPhoto) -> tuple:
    """Newest first, then compass present, then field of view known, then widest."""
    return (-(p.year or 0), p.heading_deg is None, p.hfov_deg is None, -p.width)


def find_skyline_photos(city: str, bbox_nsew, max_photos: int = 5,
                        categories: list[str] | None = None,
                        session: requests.Session | None = None) -> list[CommonsPhoto]:
    """The best ``max_photos`` located skyline photos of ``city`` near ``bbox_nsew``."""
    if session is None:
        session = requests.Session()
        session.headers["User-Agent"] = UA  # Wikimedia refuses the python-requests default
    cats = categories or skyline_categories(session, city)
    if not cats:
        logger.info("[commons] no skyline categories for %r", city)
        return []
    photos = describe_files(session, category_files(session, cats))
    reasons: dict[str, int] = {}
    keep = []
    for p in photos:
        why = usable(p, bbox_nsew)
        if why:
            reasons[why] = reasons.get(why, 0) + 1
        else:
            keep.append(p)
    keep.sort(key=rank_key)
    logger.info("[commons] %s: %d categories, %d JPEGs, %d usable %s", city, len(cats),
                len(photos), len(keep), reasons)
    return keep[:max_photos]
