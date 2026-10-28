"""Our city model in metres at its true place, as glTF, for drawing over Google 3D Tiles.

The export pipeline only ever builds in print millimetres: ``city2stl.city_model.build_on_terrain``
works on a heightfield in mm and a ``ModelScale``. There is no metric intermediate, but the mapping
is exact and invertible, so the model is built the normal way and then taken back to the world:

- horizontally, ``Terrain.unproject_xy`` turns model mm into lon/lat (the inverse of the affine
  ``Terrain.project`` that placed every feature);
- vertically, ``elev = elev_min_m + (z_mm - base_mm) / z_mm_per_m``; building heights use the same
  ``z_mm_per_m``, so roofs come back at their true metres too.

The lon/lat/height vertices are then put in a local east-north-up frame (metres, WGS84) at the
centre of the model's bbox, with heights orthometric (the DEM's datum). The browser lifts that
frame by the geoid undulation, which it measures live against the Google mesh; nothing from
the tiles comes here.

Inputs, read only:
- the terrain: the server's DEM (``--dem``), resampled to square metric pixels over the bbox
  (longest side ``GRID_PX``, as the reference-city export asks for);
- the city layers: the app's cached OSM payload that overlaps the DEM most (the payload a city
  export of that box would read: survey heights already applied, levels and low-default rules
  re-applied here as the export does on read), else the server's own footprints (``--osm``).

Differences from a real export (fine for judging heights by eye): no composite carve (rivers,
lakes), no roof-shape classification of untagged roofs, no landmark overrides or canopy, and the
DEM is the tool's GeoTIFF rather than the app's DEM pipeline.

The built model is kept as ``runs/photo_label_model/<region>_<key>.glb`` plus a ``.json`` with
its origin and bbox, so it is built once per DEM / payload / ``MODEL_VERSION``.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np

from geo2stl.geo import M_PER_DEG_SPHERE as M_PER_DEG_LAT

MODEL_VERSION = 1
GRID_PX = 1000              # longest side of the resampled terrain grid
MM_PER_PX = 1.0             # print scale the model is built at (reference-city export default)
BASE_MM = 5.0
MAX_SIDE_KM = 6.0           # the model box is clipped to this around its centre
GROUND_SAMPLES = 8          # per side: the browser samples the mesh here for the geoid offset
WGS84_A = 6378137.0
WGS84_F = 1 / 298.257223563
WGS84_E2 = WGS84_F * (2 - WGS84_F)

# glTF base colours per city layer (RGBA 0..255); anything else gets DEFAULT_COLOR.
LAYER_COLORS = {
    "terrain": (150, 160, 140, 255),
    "buildings": (235, 120, 60, 255),
    "churches": (220, 70, 70, 255),
    "towers": (220, 70, 70, 255),
    "fortifications": (190, 150, 90, 255),
    "walls": (190, 150, 90, 255),
    "roads": (90, 90, 95, 255),
    "railways": (70, 70, 70, 255),
    "green": (90, 170, 80, 255),
    "rivers": (60, 120, 220, 255),
}
DEFAULT_COLOR = (200, 200, 200, 255)


# --------------------------------------------------------------------------- geodesy

def lonlath_to_ecef(lon, lat, h) -> np.ndarray:
    """(N, 3) WGS84 earth-centred coordinates of lon/lat (degrees) and ellipsoidal height (m)."""
    lam, phi = np.radians(np.asarray(lon, float)), np.radians(np.asarray(lat, float))
    h = np.asarray(h, float)
    n = WGS84_A / np.sqrt(1 - WGS84_E2 * np.sin(phi) ** 2)
    return np.stack([(n + h) * np.cos(phi) * np.cos(lam),
                     (n + h) * np.cos(phi) * np.sin(lam),
                     (n * (1 - WGS84_E2) + h) * np.sin(phi)], axis=-1)


def enu_rotation(lon0: float, lat0: float) -> np.ndarray:
    """3x3 matrix whose rows are east, north, up at lon0/lat0, in earth-centred axes. The same
    frame as Cesium's ``Transforms.eastNorthUpToFixedFrame``."""
    lam, phi = math.radians(lon0), math.radians(lat0)
    return np.array([[-math.sin(lam), math.cos(lam), 0.0],
                     [-math.sin(phi) * math.cos(lam), -math.sin(phi) * math.sin(lam), math.cos(phi)],
                     [math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi)]])


def lonlath_to_enu(lon, lat, h, lon0: float, lat0: float, h0: float = 0.0) -> np.ndarray:
    """(N, 3) east, north, up metres of the points in the tangent frame at (lon0, lat0, h0)."""
    d = lonlath_to_ecef(lon, lat, h) - lonlath_to_ecef(lon0, lat0, h0)
    return d @ enu_rotation(lon0, lat0).T


# --------------------------------------------------------------------------- model mm -> world

def model_to_lonlath(vertices_mm: np.ndarray, bbox: dict, shape: tuple[int, int], scale) -> np.ndarray:
    """(N, 3) lon, lat, orthometric height of city-model vertices (mm), the exact inverse of
    the placement in ``build_on_terrain`` (``Terrain.project`` and ``ModelScale.z_mm``)."""
    from city2stl.city_model import Terrain

    v = np.asarray(vertices_mm, dtype=np.float64)
    ll = Terrain(np.zeros(shape), bbox, scale).unproject_xy(v[:, :2])
    elev = scale.elev_min_m + (v[:, 2] - scale.base_mm) / scale.z_mm_per_m
    return np.column_stack([ll, elev])


def model_to_enu(vertices_mm, bbox: dict, shape: tuple[int, int], scale,
                 lon0: float, lat0: float) -> np.ndarray:
    """City-model vertices (mm) as east, north, up metres at (lon0, lat0, height 0). Up is the
    orthometric height (the frame is lifted by the geoid undulation where it is drawn)."""
    llh = model_to_lonlath(vertices_mm, bbox, shape, scale)
    return lonlath_to_enu(llh[:, 0], llh[:, 1], llh[:, 2], lon0, lat0)


# --------------------------------------------------------------------------- inputs

def dem_bounds(dem) -> dict:
    """{north, south, east, west} of a ``photo_view.Dem`` (pixel edges)."""
    h, w = dem.z.shape
    lon0, lat0 = dem.t * (0, 0)
    lon1, lat1 = dem.t * (w, h)
    return {"north": max(lat0, lat1), "south": min(lat0, lat1),
            "east": max(lon0, lon1), "west": min(lon0, lon1)}


def _intersect(a: dict, b: dict) -> dict | None:
    out = {"north": min(a["north"], b["north"]), "south": max(a["south"], b["south"]),
           "east": min(a["east"], b["east"]), "west": max(a["west"], b["west"])}
    return out if out["north"] > out["south"] and out["east"] > out["west"] else None


def _area(b: dict) -> float:
    return (b["north"] - b["south"]) * (b["east"] - b["west"])


def clip_box(bbox: dict, max_side_km: float = MAX_SIDE_KM) -> dict:
    """``bbox`` cut to at most ``max_side_km`` per side around its centre."""
    lat = (bbox["north"] + bbox["south"]) / 2
    lon = (bbox["east"] + bbox["west"]) / 2
    half_lat = max_side_km * 500.0 / M_PER_DEG_LAT
    half_lon = half_lat / max(math.cos(math.radians(lat)), 1e-6)
    return {"north": min(bbox["north"], lat + half_lat), "south": max(bbox["south"], lat - half_lat),
            "east": min(bbox["east"], lon + half_lon), "west": max(bbox["west"], lon - half_lon)}


def find_city_payload(bounds: dict) -> tuple[str, dict] | None:
    """(cache key, bbox) of the app's cached OSM payload overlapping ``bounds`` the most; finer
    tolerance wins a tie. Reads only the cache's params sidecars."""
    from geo2stl.cache import list_osm_cache_params

    best = None
    for m in list_osm_cache_params():
        try:
            b = {k: float(m[k]) for k in ("north", "south", "east", "west")}
            tol = float(m.get("tol") or 0.0)
        except (KeyError, TypeError, ValueError):
            continue
        hit = _intersect(b, bounds)
        if hit is None:
            continue
        rank = (_area(hit), -tol)
        if best is None or rank > best[0]:
            best = (rank, m["key"], b)
    return None if best is None else (best[1], best[2])


def payload_layers(payload: dict) -> dict:
    """The FeatureCollections of a cached city payload, with the read-time height rules the
    export applies (``city_data.with_low_default_heights``, without importing the app)."""
    from city2stl.heights import apply_levels_heights, apply_low_default_heights

    layers = {k: v for k, v in payload.items()
              if isinstance(v, dict) and isinstance(v.get("features"), list)}
    if "buildings" in layers:
        fc, _ = apply_levels_heights(layers["buildings"])
        fc, _ = apply_low_default_heights(fc)
        layers["buildings"] = fc
    return layers


def footprint_layers(fc: dict) -> dict:
    """A buildings layer from the server's footprints (``Config.footprints`` shape: ``height``
    and ``levels`` tags), heights by ``city2stl.heights.height_from_tags``."""
    from city2stl.heights import height_from_tags

    feats = []
    for f in fc.get("features", []):
        p = f.get("properties") or {}
        tags = {"height": p.get("height"), "building:levels": p.get("levels")}
        h, src = height_from_tags(tags)
        feats.append({"type": "Feature", "geometry": f["geometry"],
                      "properties": {"osm_id": str(p.get("id")), "name": p.get("name"),
                                     "building": p.get("building") or "yes",
                                     "height_m": h, "height_source": src,
                                     "building:levels": p.get("levels")}})
    return {"buildings": {"type": "FeatureCollection", "features": feats}}


def _fc_bounds(fc: dict) -> dict | None:
    xs, ys = [], []
    for f in fc.get("features", []):
        for x, y in f["geometry"]["coordinates"][0]:
            xs.append(x)
            ys.append(y)
    if not xs:
        return None
    return {"north": max(ys), "south": min(ys), "east": max(xs), "west": min(xs)}


def resample_dem(dem, bbox: dict, grid_px: int = GRID_PX) -> np.ndarray:
    """The DEM over ``bbox`` on a grid of about square metric pixels (longest side ``grid_px``),
    row 0 north, sampled at pixel centres in ``geo2stl.geo.GeoGrid``'s convention."""
    from geo2stl.geo import GeoGrid

    lat = (bbox["north"] + bbox["south"]) / 2
    w_m = (bbox["east"] - bbox["west"]) * M_PER_DEG_LAT * math.cos(math.radians(lat))
    h_m = (bbox["north"] - bbox["south"]) * M_PER_DEG_LAT
    res = max(w_m, h_m) / grid_px
    shape = (max(8, round(h_m / res)), max(8, round(w_m / res)))
    grid = GeoGrid(bbox, shape)
    cols, rows = np.meshgrid(np.arange(shape[1]), np.arange(shape[0]))
    lon, lat = grid.px_to_lonlat(cols, rows)
    return dem(lat, lon)


# --------------------------------------------------------------------------- build

def choose_inputs(dem, footprints_fc: dict | None) -> dict:
    """What the model is built from: ``{bbox, source, payload_key}``. Raises ValueError when
    nothing overlaps the DEM."""
    bounds = dem_bounds(dem)
    found = find_city_payload(bounds)
    if found is not None:
        key, b = found
        return {"bbox": clip_box(_intersect(b, bounds)), "source": "osm_cache", "payload_key": key}
    fb = _fc_bounds(footprints_fc or {})
    hit = _intersect(fb, bounds) if fb else None
    if hit is None:
        raise ValueError("no cached city payload and no footprints overlap the DEM")
    return {"bbox": clip_box(hit), "source": "footprints", "payload_key": None}


def build_model(dem, layers: dict, bbox: dict, grid_px: int = GRID_PX):
    """``(CityModel, dem shape)`` for ``layers`` on the DEM over ``bbox``: the export's terrain
    scale in true mode, unexaggerated, so the mm model inverts to metres exactly."""
    from city2stl.city_model import build_city_model

    z = resample_dem(dem, bbox, grid_px)
    if not np.isfinite(z).any():
        raise ValueError("the DEM has no data over the model box")
    model = build_city_model(z, bbox, layers, mm_per_px=MM_PER_PX, z_mode="true", exaggeration=1.0,
                             base_mm=BASE_MM, cache=False, level_ground=True)
    return model, z.shape


def model_glb(model, bbox: dict, shape: tuple[int, int], lon0: float, lat0: float) -> bytes:
    """glTF binary of the model's layers in east-north-up metres at (lon0, lat0), Z up (the
    browser loads it with ``upAxis: Z, forwardAxis: X``, so no axis swap is applied). One mesh
    per layer with a flat base colour and no normals; the page shades it."""
    import trimesh

    scene = trimesh.Scene()
    parts = model.parts or {"model": model.merged}
    for name, mesh in parts.items():
        if mesh is None or not len(mesh.faces):
            continue
        v = model_to_enu(mesh.vertices, bbox, shape, model.scale, lon0, lat0)
        m = trimesh.Trimesh(v.astype(np.float32), np.asarray(mesh.faces), process=False)
        m.merge_vertices()
        rgba = LAYER_COLORS.get(name, DEFAULT_COLOR)
        mat = trimesh.visual.material.PBRMaterial(name=name, baseColorFactor=rgba,
                                                  metallicFactor=0.0, roughnessFactor=1.0)
        m.visual = trimesh.visual.TextureVisuals(material=mat)
        scene.add_geometry(m, geom_name=name)
    return trimesh.exchange.gltf.export_glb(scene, include_normals=False)


def ground_samples(dem, bbox: dict, n: int = GROUND_SAMPLES) -> list[list[float]]:
    """[[lon, lat, dem_h], ...] on an n x n grid inside ``bbox``: points where the browser reads
    the mesh height to estimate the geoid offset (low percentile of mesh - DEM)."""
    out = []
    for i in range(n):
        for j in range(n):
            lat = bbox["south"] + (i + 0.5) / n * (bbox["north"] - bbox["south"])
            lon = bbox["west"] + (j + 0.5) / n * (bbox["east"] - bbox["west"])
            z = float(dem(lat, lon))
            if math.isfinite(z):
                out.append([round(lon, 7), round(lat, 7), round(z, 2)])
    return out


def cache_key(dem_path, inputs: dict, osm_path=None, grid_px: int = GRID_PX) -> str:
    """Key of a built model: DEM file and mtime, the chosen inputs, grid size, ``MODEL_VERSION``."""
    parts = [str(MODEL_VERSION), str(grid_px), json.dumps(inputs, sort_keys=True)]
    for p in (dem_path, osm_path if inputs.get("source") == "footprints" else None):
        if p:
            p = Path(p)
            parts += [str(p.resolve()), str(p.stat().st_mtime_ns if p.exists() else 0)]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def build_or_load(dem, dem_path, region: str, out_dir: Path, footprints=None, osm_path=None,
                  log=print, grid_px: int = GRID_PX) -> dict:
    """Metadata of the region's model (built and written under ``out_dir`` on the first call):
    ``{glb_path, origin: {lon, lat}, bbox, source, payload_key, faces, glb_bytes, build_s,
    ground: [[lon, lat, dem_h], ...]}``.
    ``footprints`` is a callable returning the server's FeatureCollection (fallback source)."""
    fc = footprints() if callable(footprints) else footprints
    inputs = choose_inputs(dem, fc)
    key = cache_key(dem_path, inputs, osm_path, grid_px)
    out_dir = Path(out_dir)
    glb, meta_path = out_dir / f"{region}_{key}.glb", out_dir / f"{region}_{key}.json"
    if glb.is_file() and meta_path.is_file():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return {**meta, "glb_path": str(glb)}
    t0 = time.time()
    if inputs["source"] == "osm_cache":
        from geo2stl.cache import read_osm_cache
        payload = read_osm_cache(inputs["payload_key"], allow_stale=True)
        if not payload:
            raise ValueError(f"cached city payload {inputs['payload_key']} could not be read")
        layers = payload_layers(payload)
    else:
        layers = footprint_layers(fc)
    bbox = inputs["bbox"]
    log(f"model: building {region} from {inputs['source']} over {bbox}")
    model, shape = build_model(dem, layers, bbox, grid_px)
    lon0 = (bbox["east"] + bbox["west"]) / 2
    lat0 = (bbox["north"] + bbox["south"]) / 2
    raw = model_glb(model, bbox, shape, lon0, lat0)
    out_dir.mkdir(parents=True, exist_ok=True)
    glb.write_bytes(raw)
    meta = {"origin": {"lon": lon0, "lat": lat0}, "bbox": bbox, "source": inputs["source"],
            "payload_key": inputs["payload_key"], "faces": int(len(model.merged.faces)),
            "glb_bytes": len(raw), "build_s": round(time.time() - t0, 1),
            "layers": sorted(k for k, m in (model.parts or {}).items() if m is not None and len(m.faces)),
            "scale": model.scale.describe(), "model_version": MODEL_VERSION,
            "ground": ground_samples(dem, bbox)}
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    log(f"model: {meta['faces']} faces, {len(raw) / 1e6:.1f} MB glb in {meta['build_s']} s")
    return {**meta, "glb_path": str(glb)}
