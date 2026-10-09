"""
Colombian cadastre floor counts as per-footprint building heights.

Not a raster ``HeightProvider``: like ``lidar_3dep_copc.footprint_heights`` it answers per
footprint. Colombian cadastres publish their cartography in the LADM-COL shape, and the
``Construccion`` layer carries every building polygon with its number of floors
(``total_piso``; ``numero_pisos`` in other LADM-COL releases). Height is floors x a storey
height set by zone, so it does not depend on imagery.

Datasets (``DATASETS``): Cartagena's district cadastre (the Área Metropolitana de Barranquilla
runs it since 2025-01-02), datos.gov.co ``d7hk-qg8h``: one 1.2 GB zip (shapefiles plus a GDB)
whose server accepts HTTP range requests. ``fetch_layer`` reads the zip's central directory and
then only the ``Construccion`` and ``Barrio`` shapefile members (~25 MB compressed), never the
whole archive. Other cities with the same schema (Bogotá, Medellín, Barranquilla) are one more
``CadastreDataset`` entry.

Cleaning (``clean``), measured on Cartagena (185,161 polygons, 2026-10-08):
  - ``altura_tot`` is ignored: 97 % of rows hold the 3.0 m default.
  - identical geometries are one building (11,465 duplicates, mostly propiedad-horizontal units
    repeating the tower footprint); the largest floor count among them is kept.
  - ``total_piso`` of 0 or ``MAX_FLOORS`` and more is missing (62 polygons read 50-123 floors).

Height (``height_m``): ``floors x storey_m(zone, floors) + roof_m``. Storey heights by zone are
the dataset's (``CadastreDataset.storey_m_by_zone``; Cartagena's colonial walled city and
Getsemaní are taller than the 3 m residential default) and towers over ``TOWER_FLOORS`` use the
skyline storey calibration. The choices and the validation behind them are in
``docs/decisions/building-heights.md`` (2026-10-08, cadastre).

Matching (``match_footprints``): each model footprint (OSM / Overture) takes the cadastre
polygon it overlaps most, kept when their IoU is at least ``MIN_IOU``.

Confidence (``confidence``): ``CONF_LOW`` for 1-5 floors (above Overture 0.60 and GBA 0.55),
lower above; over ``TOWER_FLOORS`` floors the cadastre is a reading only and drone, satellite and
facade-floor evidence decide (``defers``).

Licence: CC BY-SA 4.0 (``ATTRIBUTION``). Heights derived from it and published carry the
attribution and the share-alike terms.

Cached once per dataset through ``_cache.register_ttl`` (``NAMESPACE``, ``TTL_DAYS``: the
cadastre is republished about quarterly).
"""

from __future__ import annotations

import logging
import math
import struct
import tempfile
import zlib
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import requests

from ._cache import make_cache_key, register_ttl

logger = logging.getLogger(__name__)

NAMESPACE = "co_catastro"
TTL_DAYS = 90
register_ttl(NAMESPACE, TTL_DAYS)

#: Floor counts at or above this are data errors (Cartagena's tallest tower has 52 floors; 62
#: polygons read 50-123).
MAX_FLOORS = 50
#: A match needs at least this intersection-over-union between footprint and cadastre polygon.
MIN_IOU = 0.3
#: Residential storey (m): Colombian residential floor-to-floor.
DEFAULT_STOREY_M = 3.0
#: Over this many floors a building is a tower: the skyline calibration's storey
#: (``floor_bands``, Cartagena v9: 4.13 m from 6 samples) and the cadastre only as a reading.
TOWER_FLOORS = 10
TOWER_STOREY_M = 4.13
#: Roof allowance added once (parapet, roof slab, tanks): ``floor_bands.FLOOR_ROOF_M`` for towers;
#: less on low-rises, calibrated on Cartagena's OSM ``height`` tags.
ROOF_M = 1.0
TOWER_ROOF_M = 3.0
#: A commercial ground floor's extra height (taller shopfront storey); not applied by default.
COMMERCIAL_GROUND_M = 1.0
#: Confidence by floor band: 1-5 floors, 6-``TOWER_FLOORS``, towers.
CONF_LOW = 0.7
CONF_MID = 0.6
CONF_TOWER = 0.4
#: Propiedad horizontal: the floor count is often a unit's (Hotel Estelar, 52 floors, reads 1),
#: so it is kept as evidence only and never published (``publishable``).
CONF_PH = 0.1

_TIMEOUT_S = 120


@dataclass(frozen=True)
class CadastreDataset:
    """One LADM-COL cadastre release on a Socrata portal."""
    name: str
    domain: str
    dataset_id: str
    #: path prefix of the shapefile members inside the zip
    member_prefix: str
    #: (north, south, east, west) the cadastre covers (the district)
    bbox: tuple[float, float, float, float]
    attribution: str
    licence: str = "CC BY-SA 4.0"
    layer: str = "Construccion"
    floors_fields: tuple[str, ...] = ("total_piso", "numero_pisos", "pisos")
    zone_layer: str | None = "Barrio"
    zone_field: str = "nombre"
    #: storey height (m) by zone name (upper case, no accents); others get DEFAULT_STOREY_M
    storey_m_by_zone: dict[str, float] = field(default_factory=dict)

    def blob_url(self) -> str:
        """The zip's download URL (the dataset's blob id, read from the Socrata view)."""
        meta = requests.get(f"https://{self.domain}/api/views/{self.dataset_id}.json",
                            timeout=_TIMEOUT_S)
        meta.raise_for_status()
        blob = meta.json()["blobId"]
        return f"https://{self.domain}/api/views/{self.dataset_id}/files/{blob}?download=true"


#: Colonial storeys: the walled city (Centro, San Diego) and Getsemaní. See the module docstring
#: and the decision entry for the calibration.
COLONIAL_STOREY_M = 4.5

DATASETS: dict[str, CadastreDataset] = {
    "cartagena": CadastreDataset(
        name="cartagena",
        domain="www.datos.gov.co",
        dataset_id="d7hk-qg8h",
        member_prefix="SHP_Catastro_AMB_Cartagena/",
        bbox=(10.62, 10.15, -75.25, -75.80),
        attribution=("Catastro Distrital de Cartagena de Indias – Área Metropolitana de "
                     "Barranquilla (AMB), datos.gov.co d7hk-qg8h, CC BY-SA 4.0"),
        storey_m_by_zone={"CENTRO": COLONIAL_STOREY_M, "SAN DIEGO": COLONIAL_STOREY_M,
                          "GETSEMANI": COLONIAL_STOREY_M},
    ),
}


def dataset_for(bbox) -> CadastreDataset | None:
    """The dataset whose district contains the centre of *bbox* = (north, south, east, west)."""
    north, south, east, west = bbox
    lat, lon = (north + south) / 2, (east + west) / 2
    for ds in DATASETS.values():
        n, s, e, w = ds.bbox
        if s <= lat <= n and w <= lon <= e:
            return ds
    return None


def covers(bbox) -> bool:
    return dataset_for(bbox) is not None


# ---------------------------------------------------------------------------
# Remote zip members by HTTP range
# ---------------------------------------------------------------------------

def _range(session, url: str, start: int, end: int) -> bytes:
    r = session.get(url, headers={"Range": f"bytes={start}-{end}"}, timeout=_TIMEOUT_S)
    r.raise_for_status()
    if r.status_code != 206:
        raise RuntimeError(f"server ignored the range request ({r.status_code}): {url}")
    return r.content


def _zip_size(session, url: str) -> int:
    r = session.get(url, headers={"Range": "bytes=0-0"}, timeout=_TIMEOUT_S)
    r.raise_for_status()
    return int(r.headers["Content-Range"].rsplit("/", 1)[1])


def zip_directory(session, url: str) -> dict[str, tuple[int, int, int, int]]:
    """``{name: (method, compressed size, size, local header offset)}`` of a remote zip, read
    from its central directory (zip64 sizes and offsets included)."""
    size = _zip_size(session, url)
    tail = _range(session, url, max(0, size - 65536 - 22), size - 1)
    i = tail.rfind(b"PK\x05\x06")
    if i < 0:
        raise RuntimeError("no zip end-of-central-directory record")
    cd_size, cd_off = struct.unpack("<II", tail[i + 12:i + 20])
    j = tail.rfind(b"PK\x06\x06")
    if j >= 0:
        cd_size, cd_off = struct.unpack("<QQ", tail[j + 40:j + 56])
    cd = _range(session, url, cd_off, cd_off + cd_size - 1)
    out = {}
    p = 0
    while p < len(cd) and cd[p:p + 4] == b"PK\x01\x02":
        method = struct.unpack("<H", cd[p + 10:p + 12])[0]
        csize, usize = struct.unpack("<II", cd[p + 20:p + 28])
        nlen, elen, clen = struct.unpack("<HHH", cd[p + 28:p + 34])
        loff = struct.unpack("<I", cd[p + 42:p + 46])[0]
        name = cd[p + 46:p + 46 + nlen].decode("utf-8", "replace")
        extra = cd[p + 46 + nlen:p + 46 + nlen + elen]
        k = 0
        while k + 4 <= len(extra):
            hid, hl = struct.unpack("<HH", extra[k:k + 4])
            if hid == 1:            # zip64: the 64-bit values of the fields that read 0xFFFFFFFF
                vals, q = extra[k + 4:k + 4 + hl], 0
                fields = [usize, csize, loff]
                for n, cur in enumerate(fields):
                    if cur == 0xFFFFFFFF and q + 8 <= len(vals):
                        fields[n] = struct.unpack("<Q", vals[q:q + 8])[0]
                        q += 8
                usize, csize, loff = fields
            k += 4 + hl
        out[name] = (method, csize, usize, loff)
        p += 46 + nlen + elen + clen
    return out


def read_member(session, url: str, entry: tuple[int, int, int, int]) -> bytes:
    """One zip member's bytes, decompressed (stored or deflate)."""
    method, csize, usize, loff = entry
    hdr = _range(session, url, loff, loff + 29)
    nlen, elen = struct.unpack("<HH", hdr[26:30])
    start = loff + 30 + nlen + elen
    raw = _range(session, url, start, start + csize - 1) if csize else b""
    if method == 0:
        data = raw
    elif method == 8:
        data = zlib.decompressobj(-15).decompress(raw)
    else:
        raise RuntimeError(f"unsupported zip compression method {method}")
    if len(data) != usize:
        raise RuntimeError(f"member size {len(data)} != {usize}")
    return data


_SHP_PARTS = (".shp", ".shx", ".dbf", ".prj", ".cpg")


def fetch_shapefile(ds: CadastreDataset, layer: str, out_dir: Path, url: str | None = None,
                    session=None, directory: dict | None = None) -> Path:
    """Write *layer*'s shapefile members from the dataset zip into *out_dir*; returns the .shp."""
    session = session or requests.Session()
    url = url or ds.blob_url()
    directory = directory or zip_directory(session, url)
    out_dir.mkdir(parents=True, exist_ok=True)
    for ext in _SHP_PARTS:
        name = f"{ds.member_prefix}{layer}{ext}"
        if name not in directory:
            if ext in (".cpg",):
                continue
            raise KeyError(f"{name} not in the {ds.dataset_id} zip")
        (out_dir / f"{layer}{ext}").write_bytes(read_member(session, url, directory[name]))
    return out_dir / f"{layer}.shp"


# ---------------------------------------------------------------------------
# Layer: read, clean, cache
# ---------------------------------------------------------------------------

def _norm_zone(name) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode()
    return " ".join(s.upper().split())


@dataclass
class CadastreLayer:
    """The cleaned ``Construccion`` layer in WGS84: one row per distinct footprint."""
    geometry: np.ndarray          # shapely Polygons / MultiPolygons, EPSG:4326
    floors: np.ndarray            # float, NaN when missing (0 or >= MAX_FLOORS)
    raw_floors: np.ndarray        # int, as published
    basements: np.ndarray         # int (total_sota)
    mezzanines: np.ndarray        # int (total_meza)
    zone: np.ndarray              # int index into zones, -1 outside every zone polygon
    zones: list[str]
    codigo: np.ndarray            # bytes, the 30-digit predial number
    n_duplicates: int = 0
    attribution: str = ""

    def __len__(self) -> int:
        return len(self.floors)

    def zone_name(self, i: int) -> str | None:
        z = int(self.zone[i])
        return self.zones[z] if z >= 0 else None


def clean(floors_raw, geoms) -> tuple[np.ndarray, np.ndarray, int]:
    """``(keep, floors, n_duplicates)``: one row per distinct geometry (the largest valid floor
    count among duplicates), floors 0 or ``MAX_FLOORS`` and over as NaN."""
    import shapely

    raw = np.asarray(floors_raw, dtype=float)
    floors = np.where((raw <= 0) | (raw >= MAX_FLOORS) | ~np.isfinite(raw), np.nan, raw)
    wkb = shapely.to_wkb(shapely.normalize(np.asarray(geoms, dtype=object)), output_dimension=2)
    first: dict[bytes, int] = {}
    keep = np.ones(len(raw), dtype=bool)
    for i, w in enumerate(wkb):
        j = first.setdefault(w, i)
        if j != i:
            keep[i] = False
            if np.isfinite(floors[i]) and not (floors[j] >= floors[i]):
                floors[j] = floors[i]
    return keep, floors, int((~keep).sum())


def _read_layer_files(ds: CadastreDataset, workdir: Path) -> CadastreLayer:
    import geopandas as gpd
    import shapely

    shp = fetch_shapefile(ds, ds.layer, workdir, url=_URLS.get(ds.name),
                          session=_SESSION.get(ds.name), directory=_DIRS.get(ds.name))
    gdf = gpd.read_file(shp)
    ffield = next((f for f in ds.floors_fields if f in gdf.columns), None)
    if ffield is None:
        raise KeyError(f"no floors field ({ds.floors_fields}) in {ds.layer}")
    gdf = gdf[gdf.geometry.notna() & ~gdf.geometry.is_empty].reset_index(drop=True)
    gdf = gdf.to_crs(4326)
    geoms = gdf.geometry.values
    geoms = np.asarray(shapely.make_valid(np.asarray(geoms, dtype=object)), dtype=object)
    raw = gdf[ffield].fillna(0).astype(int).to_numpy()
    keep, floors, ndup = clean(raw, geoms)
    gdf = gdf[keep].reset_index(drop=True)
    geoms, floors, raw = geoms[keep], floors[keep], raw[keep]

    zones: list[str] = []
    zone = np.full(len(gdf), -1, dtype=np.int32)
    if ds.zone_layer:
        zshp = fetch_shapefile(ds, ds.zone_layer, workdir, url=_URLS.get(ds.name),
                               session=_SESSION.get(ds.name), directory=_DIRS.get(ds.name))
        zg = gpd.read_file(zshp).to_crs(4326)
        zones = [_norm_zone(n) for n in zg[ds.zone_field]]
        tree = shapely.STRtree(np.asarray(shapely.make_valid(zg.geometry.values), dtype=object))
        pts = shapely.point_on_surface(geoms)
        pi, zi = tree.query(pts, predicate="within")
        zone[pi] = zi
    col = lambda c: (gdf[c].fillna(0).astype(int).to_numpy() if c in gdf.columns  # noqa: E731
                     else np.zeros(len(gdf), dtype=int))
    codigo = (gdf["codigo"].astype(str).to_numpy().astype("S30") if "codigo" in gdf.columns
              else np.zeros(len(gdf), dtype="S30"))
    return CadastreLayer(geometry=geoms, floors=floors, raw_floors=raw,
                         basements=col("total_sota"), mezzanines=col("total_meza"),
                         zone=zone, zones=zones, codigo=codigo, n_duplicates=ndup,
                         attribution=ds.attribution)


# Optional per-dataset overrides (tests, or a caller that already resolved the blob).
_URLS: dict[str, str] = {}
_SESSION: dict[str, object] = {}
_DIRS: dict[str, dict] = {}

_COORD_SCALE = 1e7          # cached coordinates are int32 at 1e-7 deg (~1 cm)


def _cache_key(ds: CadastreDataset) -> str:
    return make_cache_key(NAMESPACE, *ds.bbox, {"dataset": ds.dataset_id, "layer": ds.layer,
                                                "v": 1})


def _to_arrays(layer: CadastreLayer) -> tuple[dict, dict]:
    import shapely

    multi = np.array([g if g.geom_type == "MultiPolygon" else shapely.MultiPolygon(
        [g] if g.geom_type == "Polygon" else [p for p in getattr(g, "geoms", [])
                                              if p.geom_type == "Polygon"])
        for g in layer.geometry], dtype=object)
    _t, coords, (ring_off, poly_off, multi_off) = shapely.to_ragged_array(multi)
    arrays = {
        "coords": np.round(coords * _COORD_SCALE).astype(np.int32),
        "ring_off": ring_off.astype(np.int64), "poly_off": poly_off.astype(np.int64),
        "multi_off": multi_off.astype(np.int64),
        "floors": layer.floors.astype(np.float32), "raw_floors": layer.raw_floors.astype(np.int16),
        "basements": layer.basements.astype(np.int16),
        "mezzanines": layer.mezzanines.astype(np.int16),
        "zone": layer.zone.astype(np.int32), "codigo": layer.codigo,
    }
    meta = {"zones": layer.zones, "n_duplicates": layer.n_duplicates,
            "attribution": layer.attribution}
    return arrays, meta


def _from_arrays(arrays: dict, meta: dict) -> CadastreLayer:
    import shapely

    coords = arrays["coords"].astype(np.float64) / _COORD_SCALE
    geoms = shapely.from_ragged_array(
        shapely.GeometryType.MULTIPOLYGON, coords,
        (arrays["ring_off"], arrays["poly_off"], arrays["multi_off"]))
    geoms = np.array([g.geoms[0] if len(g.geoms) == 1 else g for g in geoms], dtype=object)
    return CadastreLayer(
        geometry=geoms, floors=arrays["floors"].astype(float),
        raw_floors=arrays["raw_floors"].astype(int), basements=arrays["basements"].astype(int),
        mezzanines=arrays["mezzanines"].astype(int), zone=arrays["zone"].astype(int),
        zones=list(meta.get("zones") or []), codigo=arrays["codigo"],
        n_duplicates=int(meta.get("n_duplicates") or 0),
        attribution=str(meta.get("attribution") or ""))


_LAYERS: dict[str, CadastreLayer] = {}


def fetch_layer(ds: CadastreDataset | str = "cartagena", refresh: bool = False) -> CadastreLayer:
    """The cleaned layer of *ds* (memory, then the disk cache, then range reads of the zip)."""
    from geo2stl.cache import read_array_cache, write_array_cache

    ds = DATASETS[ds] if isinstance(ds, str) else ds
    if not refresh and ds.name in _LAYERS:
        return _LAYERS[ds.name]
    key = _cache_key(ds)
    hit = None if refresh else read_array_cache(NAMESPACE, key)
    if hit is not None:
        layer = _from_arrays(*hit)
    else:
        logger.info("[co_catastro] fetching %s %s by range requests", ds.dataset_id, ds.layer)
        with tempfile.TemporaryDirectory(prefix="co_catastro_") as tmp:
            layer = _read_layer_files(ds, Path(tmp))
        arrays, meta = _to_arrays(layer)
        write_array_cache(NAMESPACE, key, arrays, meta, keep_dtype=True)
    _LAYERS[ds.name] = layer
    return layer


# ---------------------------------------------------------------------------
# Heights
# ---------------------------------------------------------------------------

def storey_m(ds: CadastreDataset, zone: str | None, floors: float) -> float:
    """Storey height for a building of *floors* in *zone*."""
    if floors > TOWER_FLOORS:
        return TOWER_STOREY_M
    return ds.storey_m_by_zone.get(_norm_zone(zone), DEFAULT_STOREY_M) if zone \
        else DEFAULT_STOREY_M


def height_m(ds: CadastreDataset, floors: float, zone: str | None = None,
             commercial: bool = False) -> float | None:
    """``floors x storey + roof`` in metres (plus ``COMMERCIAL_GROUND_M`` for a commercial ground
    floor), None for a missing floor count. Nothing passes ``commercial`` yet: the use is in the R2
    table, not the layer, and on Cartagena's shadows it made no measurable difference."""
    if floors is None or not math.isfinite(floors) or floors <= 0:
        return None
    roof = TOWER_ROOF_M if floors > TOWER_FLOORS else ROOF_M
    extra = COMMERCIAL_GROUND_M if commercial and floors <= TOWER_FLOORS else 0.0
    return float(floors) * storey_m(ds, zone, floors) + roof + extra


def confidence(floors: float, ph: bool = False) -> float:
    """``CONF_LOW`` for 1-5 floors, ``CONF_MID`` to ``TOWER_FLOORS``, ``CONF_TOWER`` above;
    ``CONF_PH`` for a propiedad-horizontal predio (``is_ph``), whatever its count."""
    if ph:
        return CONF_PH
    if floors <= 5:
        return CONF_LOW
    if floors <= TOWER_FLOORS:
        return CONF_MID
    return CONF_TOWER


def publishable(floors: float, ph: bool) -> bool:
    """Whether a match may publish its height: a valid count up to ``TOWER_FLOORS`` on a predio
    that is not propiedad horizontal. Towers defer to image evidence; PH counts are unreliable."""
    return (not ph) and floors is not None and math.isfinite(floors) and 1 <= floors <= TOWER_FLOORS


def defers(floors: float) -> bool:
    """Whether image evidence (drone, satellite, facade floors) decides instead of the cadastre:
    towers over ``TOWER_FLOORS`` floors."""
    return floors > TOWER_FLOORS


@dataclass(frozen=True)
class CadastreMatch:
    """The cadastre reading of one model footprint."""
    floors: int                   # max over the constituent polygons
    height_m: float
    conf: float
    iou: float                    # best of: largest-overlap polygon, union of constituents
    zone: str | None
    storey_m: float
    index: int                    # CadastreLayer row that sets ``floors``
    codigo: str = ""
    floors_best: int = 0          # floors of the largest-overlap polygon alone
    iou_best: float = 0.0
    n_parts: int = 1              # constituent polygons
    ph: bool = False              # propiedad horizontal (predial condition digit 9)

    @property
    def defers(self) -> bool:
        return defers(self.floors)

    @property
    def publishable(self) -> bool:
        return publishable(self.floors, self.ph)


#: A cadastre polygon is part of a footprint when at least this share of it lies inside.
PART_INSIDE = 0.5
#: ... and it covers at least this share of the footprint (not a sliver).
PART_MIN_COVER = 0.05


def _local_xy(lat0: float):
    k = 111320.0
    c = math.cos(math.radians(lat0))

    def f(x, y, z=None):
        return (np.asarray(x) * k * c, np.asarray(y) * k)
    return f


def is_ph(codigo: str) -> bool:
    """Propiedad horizontal: digit 22 of the 30-digit predial number (condition) is 9. Its
    ``total_piso`` is unreliable (a unit's floors on several Cartagena towers)."""
    return len(codigo) >= 22 and codigo[21] == "9"


def match_footprints(footprints: dict, layer: CadastreLayer | None = None,
                     ds: CadastreDataset | str = "cartagena",
                     min_iou: float = MIN_IOU) -> dict:
    """``{id: CadastreMatch}`` for the model *footprints* ``{id: shapely Polygon (lon, lat)}``.

    The footprint's parts are the cadastre polygon it overlaps most plus every polygon lying
    mostly inside it (``PART_INSIDE``, ``PART_MIN_COVER``): one OSM building is often a main
    house plus annexes, or a podium plus its tower, in the cadastre. The match is kept when the
    largest-overlap polygon or the union of the parts has an IoU of at least *min_iou* with the
    footprint; its floors are the parts' largest valid count (a building's height is its top).
    """
    import shapely
    from shapely.ops import transform, unary_union

    ds = DATASETS[ds] if isinstance(ds, str) else ds
    layer = layer or fetch_layer(ds)
    if not footprints:
        return {}
    ids = list(footprints)
    polys = [footprints[i] for i in ids]
    lat0 = float(np.mean([p.centroid.y for p in polys]))
    to_m = _local_xy(lat0)
    tree = shapely.STRtree(layer.geometry)
    fp_idx, cad_idx = tree.query(np.asarray(polys, dtype=object), predicate="intersects")
    cands: dict[int, list[int]] = {}
    for fi, ci in zip(fp_idx.tolist(), cad_idx.tolist(), strict=True):
        cands.setdefault(fi, []).append(ci)
    out = {}
    for fi, cis in cands.items():
        a = transform(to_m, polys[fi])
        if a.area <= 0:
            continue
        parts, best, best_inter = [], -1, 0.0
        for ci in cis:
            b = transform(to_m, layer.geometry[ci])
            try:
                inter = a.intersection(b).area
            except Exception:
                continue
            if inter > best_inter:
                best, best_inter = ci, inter
            if b.area > 0 and inter >= PART_INSIDE * b.area and inter >= PART_MIN_COVER * a.area:
                parts.append((ci, b))
        if best < 0:
            continue
        bb = transform(to_m, layer.geometry[best])
        iou_best = best_inter / max(a.union(bb).area, 1e-9)
        if best not in [ci for ci, _ in parts]:
            parts.append((best, bb))
        try:
            u = unary_union([b for _, b in parts])
            iou_union = a.intersection(u).area / max(a.union(u).area, 1e-9)
        except Exception:
            iou_union = 0.0
        iou = max(iou_best, iou_union)
        valid = [(layer.floors[ci], ci) for ci, _ in parts if np.isfinite(layer.floors[ci])]
        fb = layer.floors[best]
        # the main polygon's count is an error (0 or >= MAX_FLOORS): the building's floors are
        # unknown, and its annexes' counts would understate it (a 63-floor PH tower kept 2)
        if iou < min_iou or not valid or not np.isfinite(fb):
            continue
        fl, ci = max(valid)
        zone = layer.zone_name(ci)
        cod = _codigo(layer, ci)
        ph = is_ph(cod) or is_ph(_codigo(layer, best))
        out[ids[fi]] = CadastreMatch(
            floors=int(fl), height_m=float(height_m(ds, fl, zone)), conf=confidence(fl, ph),
            iou=float(iou), zone=zone, storey_m=storey_m(ds, zone, fl), index=int(ci),
            codigo=cod, floors_best=int(fb), iou_best=float(iou_best), n_parts=len(parts), ph=ph)
    return out


def _codigo(layer: CadastreLayer, i: int) -> str:
    c = layer.codigo[i]
    return c.decode() if isinstance(c, bytes) else str(c)


def footprint_heights(polygons: dict, bbox, ds: CadastreDataset | str | None = None,
                      min_iou: float = MIN_IOU) -> tuple[dict, dict]:
    """``({id: height_m}, stats)`` for *polygons* ``{id: shapely Polygon}`` in *bbox*: the
    ``lidar_3dep_copc.footprint_heights`` contract. Only ``publishable`` matches get a height
    (no PH predio, no tower); ``stats["matches"]`` holds every ``CadastreMatch`` per id."""
    ds = (DATASETS[ds] if isinstance(ds, str) else ds) or dataset_for(bbox)
    if ds is None:
        return {}, {"dataset": None}
    matches = match_footprints(polygons, ds=ds, min_iou=min_iou)
    heights = {k: m.height_m for k, m in matches.items() if m.publishable}
    stats = {"dataset": ds.dataset_id, "attribution": ds.attribution, "licence": ds.licence,
             "footprints": len(polygons), "matched": len(matches), "publishable": len(heights),
             "ph": sum(m.ph for m in matches.values()), "matches": matches}
    return heights, stats
