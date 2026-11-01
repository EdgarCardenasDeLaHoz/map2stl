"""What the Commons stages share: the region cache folder, profile files, retrying fetch."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import numpy as np
import requests

from city2stl.skyline import commons_photos as cp
from city2stl.skyline.local_dirs import local_dir

#: ``city2stl/skyline`` (holds ``sites/`` and ``runs/``)
ROOT = Path(__file__).resolve().parents[1]


def cache_dir(region: str) -> Path:
    """``<commons_cache>/<region>``: profiles, pairs, groups, embeddings, thumbnails."""
    return local_dir("commons_cache") / region


def new_session() -> requests.Session:
    S = requests.Session()
    S.headers["User-Agent"] = cp.UA
    return S


def get_with_retry(S: requests.Session, url: str, timeout: int = 180, tries: int = 5) -> requests.Response:
    """GET, waiting 4 s, 8 s... after each 429 (Commons rate limit); the last response is returned."""
    for a in range(tries):
        r = S.get(url, timeout=timeout)
        if r.status_code != 429:
            break
        time.sleep(4 * (a + 1))
    return r


def thumb_url(url: str, width: int = 500) -> str:
    """A Commons thumbnail url at ``width`` (only standard widths are served; 512 is refused)."""
    return re.sub(r"/(\d+)px-", f"/{width}px-", url)


def load_site(region: str) -> dict:
    return json.loads((ROOT / "sites" / f"{region}.json").read_text(encoding="utf-8-sig"))


def site_bbox(region: str) -> tuple[float, float, float, float]:
    """The region's ``(north, south, east, west)`` from its site file."""
    site = load_site(region)
    return (site["north"], site["south"], site["east"], site["west"])


def read_profiles(region: str) -> list[dict]:
    """``profiles.json`` rows (``[]`` before the first run); rows without ``px`` have no outline."""
    f = cache_dir(region) / "profiles.json"
    return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []


def outlined(region: str) -> list[dict]:
    """The rows that have an outline (``px``): what every later stage works on."""
    return [m for m in read_profiles(region) if "px" in m]


def load_profiles(region: str) -> tuple[list[dict], dict[str, np.ndarray]]:
    d = cache_dir(region)
    meta = read_profiles(region)
    arrs = dict(np.load(d / "profiles.npz")) if (d / "profiles.npz").exists() else {}
    return meta, arrs


def save_profiles(d: Path, meta: list[dict], arrs: dict[str, np.ndarray]) -> None:
    (d / "profiles.json").write_text(json.dumps(meta, indent=0), encoding="utf-8")
    np.savez_compressed(d / "profiles.npz", **arrs)
