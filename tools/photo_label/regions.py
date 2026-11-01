"""The server's regions: `Config` (paths and cached state of one region), `Regions` (a Config per region of
regions.json, built on first use), the Google key lookup and the region-table loader."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from footprints import (
    _centroid,
    _round_coords,
    apply_heights,
    geojson_footprints,
    load_heights,
    overpass_to_geojson,
    tag_sources,
)
from paths import (
    ANNOTATIONS,
    DEFAULT_DEM,
    DEFAULT_OSM,
    DEFAULT_PHOTOS,
    MODEL_DIR,
    REGIONS_FILE,
    REPO_ROOT,
    TILES_CACHE_DIR,
    _abs,
    _photo_files,
)

KEY_VAR = "GOOGLE_MAPS_API_KEY"
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


class Config:
    """Paths and cached state of one region; tests build their own."""

    def __init__(self, photo_dir: Path, osm_path: Path, annotations_dir: Path, region: str = "granada",
                 api_key=_AUTO, dem_path=DEFAULT_DEM, model_dir=MODEL_DIR, photo_roots=None, view=None,
                 tiles_cache_dir=TILES_CACHE_DIR, landmarks=None, geoid_m=None, heights_path=None):
        self.heights_path = Path(heights_path) if heights_path else None  # pipeline heights, see load_heights
        self.heights_matched = 0
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
        # background-job state, one set per Config so regions (and tests) never share it
        self.screen_state: dict[str, dict] = {}   # region -> {"state", "done", "total", "flagged", "message"}, see screening
        self.screen_lock = threading.Lock()
        self.depth_state: dict[tuple[str, str], dict] = {}   # (region, photo) -> {"state", "error"}, see depth
        self.depth_lock = threading.Lock()

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

    def glb_path(self) -> str | None:
        """Path of the built city model (`/api/model.glb`), None until `api_model_info` has built one."""
        with self._model_lock:
            return self._model.get("glb_path")

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
                fc = (geojson_footprints(data) if data.get("type") == "FeatureCollection"
                      else tag_sources(overpass_to_geojson(data)))
                if self.heights_path and self.heights_path.exists():
                    self.heights_matched = apply_heights(fc, load_heights(self.heights_path))
                self._fc = fc
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


class UnknownRegion(ValueError):
    """The request names a region the server does not have (answered with a 400)."""


def load_regions(path: Path = REGIONS_FILE) -> dict[str, dict]:
    """The checked-in region table, else Granada's built-in defaults."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data:
            return data
    except (OSError, ValueError) as exc:
        print(f"warning: cannot read {path} ({type(exc).__name__}); using the Granada defaults", file=sys.stderr, flush=True)
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
            print(f"warning: default region {default!r} is not available; using {self.default!r}", file=sys.stderr, flush=True)

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
            print(f"warning: region {rid!r}: {msg}", file=sys.stderr, flush=True)
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
                "landmarks": spec.get("landmarks") or [], "geoid_m": spec.get("geoid_m"),
                "heights": _abs(spec["heights"]) if spec.get("heights") else None}

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
                             geoid_m=spec.get("geoid_m"), tiles_cache_dir=self.tiles_cache_dir,
                             heights_path=spec.get("heights"))
                self._cfgs[rid] = cfg
            return cfg

    @property
    def tiles(self) -> Config:
        return self.get(self.default)

    def listing(self) -> dict:
        return {"default": self.default,
                "regions": [{"id": rid, "label": spec["label"], "photos": len(_photo_files(self.get(rid)))}
                            for rid, spec in self._specs.items()]}


def build_regions(region: str, osm: str | None = None, dem: str | None = None,
                  photos: str | None = None) -> Regions:
    """The regions.json table; --osm/--dem/--photos replace those fields of the `region` entry."""
    specs = load_regions()
    if osm is not None or dem is not None or photos is not None:
        spec = dict(specs.get(region) or {"label": region, "osm": str(DEFAULT_OSM), "dem": str(DEFAULT_DEM),
                                          "photos": {"": str(DEFAULT_PHOTOS)}})
        if osm is not None:
            spec["osm"] = str(Path(osm).resolve())
        if dem is not None:
            spec["dem"] = str(Path(dem).resolve()) if dem else ""
        if photos is not None:
            spec["photos"] = {"": str(Path(photos).resolve())}
        specs[region] = spec
    return Regions(specs, region)
