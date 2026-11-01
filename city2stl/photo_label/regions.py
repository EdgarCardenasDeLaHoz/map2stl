"""Photo-label regions: a `Config` per region (OSM footprints, DEM, photo roots, annotation
folder) and the `Regions` registry read from `tools/photo_label/regions.json`.

Moved from `tools/photo_label/server.py` (F-UNIFY Phase 3) so the app's photo routes
(`app/server/core/photo_solve.py`) import the package instead of loading a tool server.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOL_DIR = REPO_ROOT / "tools" / "photo_label"

IMG_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


DEFAULT_OSM = REPO_ROOT / "cache" / "5d947d851a9f2b205205e503611a5df310e7e87e.json"


DEFAULT_DEM = REPO_ROOT / "cache" / "opentopo" / "d6eb6dd762c161a4afb3471521b53e14.tif"


DEFAULT_PHOTOS = REPO_ROOT / "runs" / "photo"


ANNOTATIONS = REPO_ROOT / "city2stl" / "skyline" / "sites" / "annotations"


REGIONS_FILE = TOOL_DIR / "regions.json"


KEY_VAR = "GOOGLE_MAPS_API_KEY"


MODEL_DIR = REPO_ROOT / "runs" / "photo_label_model"


TILES_CACHE_DIR = REPO_ROOT / "runs" / "photo_label_tiles"


_AUTO = object()


def load_api_key(env_file: Path | None = None) -> str:
    """The Maps key from the environment, else from `.env` (KEY=value lines). "" when absent."""
    key = os.environ.get(KEY_VAR, "").strip()
    if key:
        return key
    try:
        for line in (env_file or REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
            name, sep, val = line.partition("=")
            if sep and name.strip().removeprefix("export ").strip() == KEY_VAR:
                return val.strip().strip("\"'")
    except OSError:
        pass
    return ""


def _round_coords(fc: dict, nd: int = 6) -> dict:
    """A copy of a GeoJSON FeatureCollection with every coordinate rounded to `nd` decimals."""
    def rnd(c):
        return [round(x, nd) for x in c] if c and isinstance(c[0], (int, float)) else [rnd(x) for x in c]
    feats = []
    for ft in fc.get("features", []):
        g = ft.get("geometry")
        if isinstance(g, dict) and "coordinates" in g:
            g = {**g, "coordinates": rnd(g["coordinates"])}
        feats.append({**ft, "geometry": g})
    return {**fc, "features": feats}


class Config:
    """Paths and cached state of one region; tests build their own."""

    def __init__(self, photo_dir: Path, osm_path: Path, annotations_dir: Path, region: str = "granada",
                 api_key=_AUTO, dem_path=DEFAULT_DEM, model_dir=MODEL_DIR, photo_roots=None, view=None,
                 tiles_cache_dir=TILES_CACHE_DIR, landmarks=None, geoid_m=None):
        self.tiles_cache_dir = Path(tiles_cache_dir)
        self.photo_dir = Path(photo_dir)
        # key -> folder; an empty key adds no prefix to photo paths (and so to label file names)
        self.photo_roots = {k: Path(v) for k, v in (photo_roots or {"": photo_dir}).items()}
        self.osm_path = Path(osm_path)
        self.annotations_dir = Path(annotations_dir)
        self.region = region
        self.landmarks = list(landmarks or [])  # [{name, aliases, lat, lon, elev_m?}] from regions.json
        self.geoid_m = None if geoid_m is None else float(geoid_m)  # measured undulation; None = estimate per render
        self.view = view  # {lat, lon, ground_m} start of the maps, from regions.json; None = DEM centre
        self.dem_path = Path(dem_path) if dem_path else None
        self._dem = None
        self._fc: dict | None = None
        self._pts: list | None = None
        self._lock = threading.Lock()
        self._key = load_api_key() if api_key is _AUTO else (api_key or "")
        self.session = ""  # last Google tile session token seen in a tileset JSON
        self.model_dir = Path(model_dir)
        self._model: dict = {"status": "idle"}   # see api_model_info
        self._model_lock = threading.Lock()

    def footprints_bytes(self) -> tuple[bytes, bytes]:
        """(raw JSON, gzip of it) of footprints(), coordinates rounded to 6 decimals; built once."""
        with self._lock:
            cached = getattr(self, "_fc_bytes", None)
        if cached is None:
            import gzip
            raw = json.dumps(_round_coords(self.footprints()), separators=(",", ":")).encode("utf-8")
            cached = (raw, gzip.compress(raw, 6, mtime=0))
            with self._lock:
                self._fc_bytes = cached
        return cached

    @property
    def api_key(self) -> str:
        return self._key

    def dem(self):
        """`photo_view.Dem` of `dem_path`, loaded once; None when there is no DEM file."""
        with self._lock:
            if self._dem is None and self.dem_path and self.dem_path.is_file():
                from city2stl.skyline import photo_view
                self._dem = photo_view.Dem(self.dem_path)
            return self._dem

    def footprints(self) -> dict:
        with self._lock:
            if self._fc is None:
                data = json.loads(self.osm_path.read_text(encoding="utf-8"))
                self._fc = (geojson_footprints(data) if data.get("type") == "FeatureCollection"
                            else overpass_to_geojson(data))
            return self._fc

    def centroids(self) -> list[tuple[float, float, str]]:
        """(lat, lon, name) of named footprints."""
        fc = self.footprints()
        with self._lock:
            if self._pts is None:
                pts = []
                for f in fc["features"]:
                    name = f["properties"].get("name")
                    if name:
                        la, lo = _centroid(f["geometry"]["coordinates"][0])
                        pts.append((la, lo, name))
                self._pts = pts
            return self._pts


def start_view(cfg: Config) -> dict | None:
    """Start of the maps: the region's `view` from regions.json, else the DEM's centre and its
    orthometric ground height, else None (the page then keeps wherever the map already is)."""
    import numpy as np

    dem = cfg.dem()
    if cfg.view:
        return dict(cfg.view)
    if dem is None:
        return None
    h, w = dem.z.shape
    lon, lat = dem.t * (w / 2, h / 2)
    z = float(np.nanmedian(dem.z))
    return {"lat": float(lat), "lon": float(lon), "ground_m": z if np.isfinite(z) else 0.0}


def overpass_to_geojson(data: dict) -> dict:
    nodes = {e["id"]: (e["lon"], e["lat"]) for e in data.get("elements", []) if e.get("type") == "node"}
    feats = []
    for e in data.get("elements", []):
        tags = e.get("tags") or {}
        if e.get("type") != "way" or "building" not in tags:
            continue
        ring = [nodes[n] for n in e.get("nodes", []) if n in nodes]
        if len(ring) < 3:
            continue
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        feats.append({"type": "Feature",
                      "properties": {"id": e["id"], "name": tags.get("name"),
                                     "building": tags["building"], "height": tags.get("height"),
                                     "levels": tags.get("building:levels")},
                      "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}})
    # Building multipolygons (Palacio de Carlos V, many Albaicin blocks): one footprint per closed
    # outer ring, stitched from the member ways. Inner rings (courtyards) are left out.
    ways = {e["id"]: e.get("nodes", []) for e in data.get("elements", []) if e.get("type") == "way"}
    for e in data.get("elements", []):
        tags = e.get("tags") or {}
        if e.get("type") != "relation" or "building" not in tags:
            continue
        parts = [list(ways[m["ref"]]) for m in e.get("members", [])
                 if m.get("type") == "way" and m.get("role", "outer") in ("outer", "") and m.get("ref") in ways]
        for k, ids in enumerate(_stitch_rings(parts)):
            ring = [nodes[n] for n in ids if n in nodes]
            if len(ring) < 4:
                continue
            feats.append({"type": "Feature",
                          "properties": {"id": f"r{e['id']}" + (f"_{k}" if k else ""), "name": tags.get("name"),
                                         "building": tags["building"], "height": tags.get("height"),
                                         "levels": tags.get("building:levels")},
                          "geometry": {"type": "Polygon", "coordinates": [[list(p) for p in ring]]}})
    return {"type": "FeatureCollection", "features": feats}


def _stitch_rings(parts: list[list[int]]) -> list[list[int]]:
    """Join way node lists end to end into closed rings; an open chain left over is dropped."""
    rings, parts = [], [p for p in parts if len(p) >= 2]
    while parts:
        cur = parts.pop(0)
        while cur[0] != cur[-1]:
            for i, p in enumerate(parts):
                if p[0] == cur[-1]:
                    cur += p[1:]
                elif p[-1] == cur[-1]:
                    cur += p[-2::-1]
                elif p[-1] == cur[0]:
                    cur = p[:-1] + cur
                elif p[0] == cur[0]:
                    cur = p[:0:-1] + cur
                else:
                    continue
                parts.pop(i)
                break
            else:
                break
        if cur[0] == cur[-1] and len(cur) >= 4:
            rings.append(cur)
    return rings


def geojson_footprints(data: dict) -> dict:
    """Footprints from a pipeline `buildings.geojson` (runs/<city>/01_osm), in the Overpass shape.

    Only measured heights carry over: a `height_source` of "default" is the pipeline's filler and
    would hide the levels/default fallback in `terrain_solve.building_height`.
    """
    feats = []
    for i, f in enumerate(data.get("features", [])):
        g = f.get("geometry") or {}
        rings = ([g["coordinates"][0]] if g.get("type") == "Polygon" else
                 [p[0] for p in g["coordinates"]] if g.get("type") == "MultiPolygon" else [])
        pr = f.get("properties") or {}
        h = pr.get("height_m") if pr.get("height_source") not in (None, "default") else None
        for ring in rings:
            if len(ring) >= 4:
                feats.append({"type": "Feature",
                              "properties": {"id": pr.get("id", i), "name": pr.get("name"),
                                             "building": pr.get("building", "yes"), "height": h,
                                             "levels": pr.get("levels")},
                              "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


def _centroid(ring) -> tuple[float, float]:
    """(lat, lon) mean of the ring's vertices (good enough for a label tick)."""
    pts = ring[:-1] if len(ring) > 1 and ring[0] == ring[-1] else ring
    return (sum(p[1] for p in pts) / len(pts), sum(p[0] for p in pts) / len(pts))


def _inside(lon: float, lat: float, ring) -> bool:
    c = False
    j = len(ring) - 1
    for i in range(len(ring)):
        xi, yi = ring[i]
        xj, yj = ring[j]
        if (yi > lat) != (yj > lat) and lon < (xj - xi) * (lat - yi) / (yj - yi) + xi:
            c = not c
        j = i
    return c


def name_at(fc: dict, lat: float, lon: float) -> str | None:
    for f in fc["features"]:
        name = f["properties"].get("name")
        if not name:
            continue
        ring = f["geometry"]["coordinates"][0]
        xs = [p[0] for p in ring]
        ys = [p[1] for p in ring]
        if min(xs) <= lon <= max(xs) and min(ys) <= lat <= max(ys) and _inside(lon, lat, ring):
            return name
    return None


class UnknownRegion(ValueError):
    """The request names a region the server does not have (answered with a 400)."""


def _abs(p) -> Path:
    p = Path(p)
    return p if p.is_absolute() else REPO_ROOT / p


def load_regions(path: Path = REGIONS_FILE) -> dict[str, dict]:
    """The checked-in region table, else Granada's built-in defaults."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError) as exc:
        print(f"warning: cannot read {path} ({type(exc).__name__}); using the Granada defaults", flush=True)
    return {"granada": {"label": "Granada", "osm": str(DEFAULT_OSM), "dem": str(DEFAULT_DEM),
                        "photos": {"": str(DEFAULT_PHOTOS)}}}


class Regions:
    """The server's regions: a Config per region, built on first use and kept.

    The Google key and the tile session are shared: every Config gets the same key, and the tile
    proxy always works through `tiles`, the default region's Config, whatever region the page shows.
    """

    def __init__(self, specs: dict[str, dict], default: str, annotations_dir: Path = ANNOTATIONS,
                 api_key=_AUTO, model_dir=MODEL_DIR, tiles_cache_dir=TILES_CACHE_DIR):
        self.tiles_cache_dir = Path(tiles_cache_dir)
        self._key = load_api_key() if api_key is _AUTO else (api_key or "")
        self._annotations = Path(annotations_dir)
        self._model_dir = Path(model_dir)
        self._lock = threading.Lock()
        self._cfgs: dict[str, Config] = {}
        self._specs: dict[str, dict] = {}
        for rid, spec in specs.items():
            ok = self._validate(rid, spec)
            if ok is not None:
                self._specs[rid] = ok
        if not self._specs:
            raise ValueError("no usable region (every region in regions.json has a missing path)")
        self.default = default if default in self._specs else next(iter(self._specs))
        if default != self.default:
            print(f"warning: default region {default!r} is not available; using {self.default!r}", flush=True)

    @classmethod
    def single(cls, cfg: Config) -> Regions:
        """A registry holding one ready-made Config (tests, embedding)."""
        self = cls.__new__(cls)
        self._key, self._annotations, self._model_dir = cfg.api_key, cfg.annotations_dir, cfg.model_dir
        self.tiles_cache_dir = cfg.tiles_cache_dir
        self._lock = threading.Lock()
        self._cfgs = {cfg.region: cfg}
        self._specs = {cfg.region: {"label": cfg.region}}
        self.default = cfg.region
        return self

    @staticmethod
    def _validate(rid: str, spec: dict) -> dict | None:
        """The spec with absolute paths, or None (warned) when its OSM or DEM file is missing.
        A missing photo folder is dropped from the region, not the region."""
        def warn(msg):
            print(f"warning: region {rid!r}: {msg}", flush=True)
        osm = _abs(spec["osm"]) if spec.get("osm") else None
        if osm is None or not osm.is_file():
            warn(f"skipped, OSM file missing ({spec.get('osm')})")
            return None
        dem = _abs(spec["dem"]) if spec.get("dem") else None
        if dem is not None and not dem.is_file():
            warn(f"skipped, DEM missing ({spec['dem']})")
            return None
        roots = {}
        for key, folder in (spec.get("photos") or {}).items():
            if _abs(folder).is_dir():
                roots[key] = _abs(folder)
            else:
                warn(f"photo folder {key!r} missing ({folder}), left out")
        return {"label": spec.get("label") or rid, "osm": osm, "dem": dem, "photos": roots, "view": spec.get("view"),
                "landmarks": spec.get("landmarks") or [], "geoid_m": spec.get("geoid_m")}

    def ids(self) -> list[str]:
        return list(self._specs)

    def get(self, rid: str | None) -> Config:
        """The region's Config (the default when `rid` is empty); UnknownRegion for any other id."""
        rid = rid or self.default
        with self._lock:
            cfg = self._cfgs.get(rid)
            if cfg is None:
                spec = self._specs.get(rid)
                if spec is None:
                    raise UnknownRegion(f"unknown region: {rid}")
                roots = spec["photos"]
                cfg = Config(next(iter(roots.values()), DEFAULT_PHOTOS), spec["osm"], self._annotations, rid,
                             api_key=self._key, dem_path=spec["dem"], model_dir=self._model_dir,
                             photo_roots=roots, view=spec["view"], landmarks=spec.get("landmarks"),
                             geoid_m=spec.get("geoid_m"), tiles_cache_dir=self.tiles_cache_dir)
                self._cfgs[rid] = cfg
            return cfg

    @property
    def tiles(self) -> Config:
        return self.get(self.default)

    def listing(self) -> dict:
        from .photos import photo_files
        return {"default": self.default,
                "regions": [{"id": rid, "label": spec["label"], "photos": len(photo_files(self.get(rid)))}
                            for rid, spec in self._specs.items()]}
