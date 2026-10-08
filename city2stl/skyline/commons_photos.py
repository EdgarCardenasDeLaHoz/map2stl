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
Web seeds (``find_skyline_photos``): located near the region, landscape (<= 3:1, see
``MAX_ASPECT``), then newest, compass present, widest; night photos are dropped after download
(``is_dark``). The photo pipeline screens more loosely (``screen``, ``pipeline_status``: user
review 2026-10-07): wide panoramas fit as cylindrical views, unlocated photos go to a placement
queue, low-light photos stay when their skyline is clear (``skyline_quality``). Network: the
Commons API only, with a descriptive User-Agent as Wikimedia asks; no key.
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
#: Camera may stand outside the region bbox (a ship offshore, a far shore), up to this far, km.
#: 8 km dropped Miami's "skyline from the ocean" (2020), a good offshore telephoto; 15 km
#: dropped Chicago's "panoramio (12)" (16.3 km, user review 2026-10-07: usable). The two
#: Miami photos the user agreed were too far sit at 29.9 and 36.3 km.
MAX_OUTSIDE_KM = 20.0
#: A recorded location this far outside is not the camera's but a wrong geotag (Cartagena,
#: Colombia's "Cartagena2011-Skyline-Habour" carries Cartagena, Spain: 7,237 km). Such a photo
#: is treated as unlocated (placement queue), not dropped.
WRONG_LOCATION_KM = 200.0
#: A stitched panorama is cylindrical: columns are linear in bearing. Its total field of view
#: is not in EXIF (the focal length is one frame's), so it is guessed from the aspect at this
#: vertical field of view and left free in the fit (``PANO_FOV_SPAN``).
PANO_VFOV_GUESS_DEG = 35.0
PANO_FOV_SPAN = 0.6
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
    """Why ``p`` cannot be a web seed (pinhole, located), or None when it can.

    The photo pipeline uses ``screen`` instead, which keeps unlocated and wide photos."""
    if p.lat is None or p.lon is None:
        return "no camera location"
    if _km_outside(p.lat, p.lon, bbox_nsew) > MAX_OUTSIDE_KM:
        return "camera far from region"
    if not p.width or not p.height or p.width / p.height > MAX_ASPECT:
        return "panorama wider than 3:1"
    if p.height > p.width:
        return "portrait"
    return None


#: ``screen`` statuses.
FIT = "fit"                 # has a usable location: straight to the camera fit
PLACEMENT = "placement"     # no (or a wrong) location: placement queue, then manual labels
REJECT = "reject"


def _get(p, k):
    return p.get(k) if isinstance(p, dict) else getattr(p, k, None)


def screen(p, bbox_nsew) -> tuple[str, str | None, str]:
    """``(status, reason, projection)`` for the photo pipeline; ``p`` is a ``CommonsPhoto`` or a
    ``profiles.json`` row.

    User review of 38 rejected photos (2026-10-07): 25 were usable, so only portraits and
    cameras 20-200 km out are rejected here. Unlocated photos (and wrong geotags) go to the
    placement queue; panoramas wider than ``MAX_ASPECT`` are fitted as cylindrical views (6 of 6
    were wrongly rejected). Night/quality is judged from pixels (``skyline_quality``).
    """
    w, h = _get(p, "width") or 0, _get(p, "height") or 0
    if not w or not h:
        return REJECT, "no image size", "pinhole"
    if h > w:
        return REJECT, "portrait", "pinhole"
    projection = "cylindrical" if w / h > MAX_ASPECT else "pinhole"
    lat, lon = _get(p, "lat"), _get(p, "lon")
    if lat is None or lon is None:
        return PLACEMENT, "no camera location", projection
    km = _km_outside(lat, lon, bbox_nsew)
    if km > WRONG_LOCATION_KM:
        return PLACEMENT, f"wrong geotag ({km:.0f} km away)", projection
    if km > MAX_OUTSIDE_KM:
        return REJECT, "camera far from region", projection
    return FIT, ("wide panorama, cylindrical" if projection == "cylindrical" else None), projection


#: FOV assumed for a located photo without EXIF focal length (a normal lens), refined over
#: +-``FREE_FOV_SPAN`` (log units). Before 2026-10-07 such photos never reached the fit.
DEFAULT_HFOV_DEG = 55.0
FREE_FOV_SPAN = 0.5
#: EXIF FOV is trusted to +-8 % (``skyline_match.locate``).
EXIF_FOV_SPAN = 0.08
#: Second try when the EXIF FOV fails the fit gate (crops keep the full frame's EXIF).
FOV_RETRY_SPAN = 0.4


def fit_prior(row) -> tuple[float, float, str]:
    """``(hfov_deg, fov_span, projection)`` the camera fit starts from for a ``profiles.json``
    row: EXIF FOV (narrow span) for a normal photo, a free FOV without EXIF, and a cylindrical
    guess for a wide panorama (its EXIF focal length is one frame's)."""
    w, h = _get(row, "width") or 1, _get(row, "height") or 1
    if w / h > MAX_ASPECT:
        return pano_hfov_guess(w, h), PANO_FOV_SPAN, "cylindrical"
    fov = _get(row, "hfov_deg")
    if fov:
        return float(fov), EXIF_FOV_SPAN, "pinhole"
    return DEFAULT_HFOV_DEG, FREE_FOV_SPAN, "pinhole"


def pipeline_status(row, bbox_nsew) -> tuple[str, str | None]:
    """``(status, reason)`` of a ``profiles.json`` row for the photo pipeline: ``FIT`` (camera
    fit from its location), ``PLACEMENT`` (placement queue: EXIF + lead-gate search, else
    manual labels) or ``REJECT``. Combines ``screen``, the outline cache and the clarity
    gate."""
    status, reason, _proj = screen(row, bbox_nsew)
    if status == REJECT:
        return status, reason
    if "px" not in row:
        return REJECT, row.get("skip") or "no outline"
    why = quality_reject(row.get("quality"), row.get("sky_value"))
    if why:
        return REJECT, why
    return status, reason


def pano_hfov_guess(width: int, height: int) -> float:
    """Total horizontal FOV (deg) guessed for a cylindrical panorama from its aspect."""
    return float(min(360.0, PANO_VFOV_GUESS_DEG * width / max(height, 1)))


#: ``skyline_quality`` gate, for low-light photos only (sky value below ``LOW_LIGHT_SKY_VALUE``).
#: User review 2026-10-07 of six "night" rejects: "decide on quality". The three the user
#: rejected are the low-light ones (sky value 94-158) and score 0.02-0.11 (hazy dusk, glare,
#: skyline half lost); the three usable ones were bright (194-207, EXIF-hour "night") and score
#: 0.12-0.28. Day photos are not gated on it: 23 % of all outlines score < 0.11 in haze, and no
#: review says they are bad (the fit's coverage and misfit gates judge them).
MIN_SKYLINE_QUALITY = 0.12
LOW_LIGHT_SKY_VALUE = 170.0
#: Rows above and below the skyline compared for its edge strength (at 1600 px image width).
EDGE_BAND_PX = 6


def skyline_quality(img, y_top, prof_height: int) -> dict:
    """How clearly the skyline stands out, from the image and its outline (``y_top`` per column
    of a ``prof_height``-row frame; ``img`` may be any size, H x W x 3 uint8).

    - ``edge``: median over outline columns of |sky luma - building luma| in thin bands just
      above and below the outline, /255 (a lit night tower on a black sky is a strong edge, a
      silhouette lost in dusk haze a weak one);
    - ``coverage``: share of columns with an outline;
    - ``score`` = edge x coverage.
    Replaces the night rule (``is_dark``): the user's review said decide on quality, not time.
    """
    import numpy as np

    img = np.asarray(img)
    H, W = img.shape[:2]
    y = np.asarray(y_top, dtype=float)
    luma = (0.299 * img[..., 0] + 0.587 * img[..., 1] + 0.114 * img[..., 2]).astype(np.float32)
    cols = np.clip(((np.arange(len(y)) + 0.5) * W / len(y)).astype(int), 0, W - 1)
    s = H / float(prof_height)
    band = max(2, int(round(EDGE_BAND_PX * W / 1600.0)))
    ok = np.isfinite(y)
    steps = []
    for c, yy in zip(cols[ok], y[ok] * s, strict=True):
        r = int(round(yy))
        a0, b1 = r - band - 1, r + band + 2
        if a0 < 0 or b1 > H:
            continue
        steps.append(abs(float(luma[a0:r - 1, c].mean()) - float(luma[r + 2:b1, c].mean())))
    coverage = float(ok.mean()) if len(y) else 0.0
    edge = float(np.median(steps)) / 255.0 if steps else 0.0
    return {"edge": round(edge, 4), "coverage": round(coverage, 4),
            "score": round(edge * coverage, 4)}


def sky_value(img) -> float:
    """Median brightest channel of the top third (mostly sky); see ``is_dark``."""
    import numpy as np

    top = np.asarray(img[: max(1, img.shape[0] // 3)])
    return float(np.median(top.max(axis=2)))


def quality_reject(q: dict | None, sky: float | None) -> str | None:
    """Why the photo's skyline is too unclear to fit, or None. Replaces the night rule: a
    low-light photo (night, dusk, glare) is kept when its skyline still stands out."""
    if q is None or sky is None or sky >= LOW_LIGHT_SKY_VALUE:
        return None
    if q.get("score", 0.0) >= MIN_SKYLINE_QUALITY:
        return None
    return f"unclear low-light skyline (quality {q.get('score', 0.0):.2f})"


def is_dark(img) -> bool:
    """True for a night or deep-dusk photo, from the top third of the image (mostly sky).

    ``img``: H x W x 3 uint8 RGB. Applied after download, since only pixels can tell. The photo
    pipeline no longer drops dark photos on this (``skyline_quality`` decides); the web seeds
    still use it.
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
