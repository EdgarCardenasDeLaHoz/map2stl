"""Landmark buildings: listing and per-building overrides (F-LANDMARK §3, §5).

A *landmark* is a building worth more than an extruded footprint -- a cathedral,
a town hall, a castle -- or simply one of the tallest in the model. OSM tags
alone (``city2stl.roofs`` + Simple 3D Buildings parts) get many of them right;
where they do not, the user can replace one building, identified by its OSM id
(``osm_id``, e.g. ``"way/123"``, kept by ``city2stl.fetch``), with:

* ``{"kind": "mesh", "upload_id": ..., ...}`` -- an uploaded STL / OBJ / glTF
  placed on the footprint (:func:`mesh_solid`): its horizontal outline's minimum
  rotated rectangle is matched to the footprint's (principal axes + per-axis or
  uniform scale; the 180° ambiguity is settled by footprint overlap), then the
  optional ``rotation_deg`` / ``scale`` / ``offset_m`` are applied. Vertically
  the mesh keeps its own proportions at the model's horizontal scale converted
  with ``z_mm_per_m`` (``vertical="true"``, default) or is fitted to ``height_m``
  (``vertical="fit"``; the OSM height when not given). Its base sits on the
  highest ground under the footprint, with a skirt down to the lowest, like an
  extruded building. The file must be a closed solid: :func:`load_mesh` merges
  vertices and fills small holes, then rejects it with the reason if manifold3d
  still cannot read it as one.
* ``{"kind": "ndsm", "provider": "rediam_mdhn" | ... | "auto", "resolution_m": ...}``
  -- a surveyed height-above-ground raster (``city2stl.height.providers.survey``)
  clipped exactly to the footprint (:func:`ndsm_solid`): a TIN over the footprint
  outline plus the raster cell centres inside it, heights sampled bilinearly
  (gaps filled from the nearest measured cell), closed down to the skirt floor.
* ``{"kind": "height", "height_m": ..., "source": "<optional note>"}`` -- the OSM
  building as drawn, with its top at ``height_m`` metres above the ground
  (:func:`scale_heights`): every member's height, ``min_height`` and numeric
  ``roof:height`` is multiplied by ``height_m`` / the tallest member's height, so
  roof shapes and parts keep their proportions. For OSM ``height=10`` placeholders
  on towers whose real height is known (Alhambra: Torre de Comares ~45 m).

* ``{"kind": "reference", ...}`` -- like ``ndsm`` but from a stored, geo-referenced raster of a
  hand-modelled reference (the Alhambra, :func:`reference_landmarks`): the top follows the
  local terrain (``local_ground``), and an entry may carry a ``footprint_lonlat`` of its own
  when OSM has no building with an id for it (the towers and churches layers).

``kind == "osm"`` (or no entry) keeps the tag-driven building.

Resolution (file I/O, network) and geometry are split: :func:`resolve_overrides`
loads meshes and fetches rasters up front, so a bad file or a survey outage fails
the request with a clear message before any geometry is built;
:class:`LandmarkPlan` is then handed to ``city_model.build_on_terrain`` and swaps
the solids in while the buildings layer is built. Parts (``building:part``) and
other extrude-layer features lying mostly (≥ 50 %) inside an overridden footprint
are removed with it, so a replaced cathedral does not keep its OSM towers or a
second copy from the ``churches`` layer. A height override rescales those parts
instead of removing them; later layers (``towers``, ``churches``, whose features
carry no ``osm_id``) still lose their copies lying inside it, so a copy at the
old OSM height does not show through.

:func:`list_landmarks` is the Landmarks panel's list (``POST /api/cities/landmarks``).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import shapely
from rasterio.transform import Affine
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

Mesh = tuple[np.ndarray, np.ndarray]

#: A part or feature at least this share inside an overridden footprint goes with it.
REPLACE_OVERLAP = 0.5
#: The mesh base is sunk this far (mm) into the skirt so the union is one solid.
SEAT_OVERLAP_MM = 0.05
#: An nDSM covering less of the footprint than this is refused.
MIN_NDSM_COVERAGE = 0.3
#: Margin (m) added around a footprint when fetching its nDSM.
NDSM_MARGIN_M = 5.0
OVERRIDE_KINDS = ("osm", "mesh", "ndsm", "height", "reference")
#: A height override must be above 0 and below this (m).
MAX_HEIGHT_M = 1000.0
#: The height ``city_model`` extrudes a building without ``height_m`` to.
DEFAULT_HEIGHT_M = 10.0

WORSHIP_BUILDINGS = {"cathedral", "church", "chapel", "mosque", "temple", "synagogue", "shrine",
                     "basilica", "monastery"}
CIVIC_BUILDINGS = {"townhall", "government", "civic"}
HISTORIC = {"castle", "palace", "fort", "fortress", "citadel", "monastery"}
TOURISM = {"attraction", "museum"}


class LandmarkError(ValueError):
    """An override that cannot be applied (bad mesh, no survey data, bad spec)."""


# ---------------------------------------------------------------------------
# Listing
# ---------------------------------------------------------------------------

def _tag(props: dict, key: str) -> str:
    v = props.get(key)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    return str(v).strip().lower()


def landmark_category(props: dict) -> str | None:
    """``worship`` / ``civic`` / ``historic`` / ``attraction`` from the OSM tags, else None."""
    building = _tag(props, "building")
    if _tag(props, "amenity") == "place_of_worship" or building in WORSHIP_BUILDINGS:
        return "worship"
    if _tag(props, "amenity") == "townhall" or building in CIVIC_BUILDINGS:
        return "civic"
    if _tag(props, "historic") in HISTORIC or building in {"castle", "palace"}:
        return "historic"
    if _tag(props, "tourism") in TOURISM:
        return "attraction"
    return None


def _is_part(props: dict) -> bool:
    v = _tag(props, "building:part")
    return bool(v) and v not in ("no", "none", "nan")


def _height(props: dict) -> float | None:
    try:
        h = float(props.get("height_m"))
    except (TypeError, ValueError):
        return None
    return h if math.isfinite(h) else None


def _shape(feature: dict):
    """The feature's shapely geometry, or None when missing / unreadable."""
    try:
        g = shapely.geometry.shape(feature["geometry"]) if feature.get("geometry") else None
    except Exception:
        return None
    return g if g is not None and not g.is_empty else None


def list_landmarks(features: list[dict], tallest_n: int = 10) -> list[dict]:
    """Notable buildings in a buildings FeatureCollection's ``features``.

    Every tagged landmark (:func:`landmark_category`) plus the ``tallest_n``
    tallest other buildings. Parts are not listed on their own; they are counted
    under the outline containing them (``parts``) and contribute their roof
    shapes and heights. Each entry: ``osm_id`` (None for data fetched before ids
    were kept -- such a landmark cannot be overridden), ``index`` (feature index),
    ``name``, ``category``, ``height_m`` (the tallest of outline and parts),
    ``height_source``, ``parts``, ``roof_shapes``, ``area_m2``, ``centroid``
    [lon, lat] and ``bbox`` {north, south, east, west}.
    """
    geoms = [_shape(f) for f in features]
    props = [f.get("properties") or {} for f in features]
    part_idx = [k for k, p in enumerate(props) if _is_part(p) and geoms[k] is not None]
    part_pts = shapely.point_on_surface(np.asarray([geoms[k] for k in part_idx], dtype=object)) \
        if part_idx else np.empty(0, object)
    tree = shapely.STRtree(part_pts) if part_idx else None

    entries = []
    for k, (g, p) in enumerate(zip(geoms, props, strict=True)):
        if g is None or _is_part(p):
            continue
        members = [p]
        if tree is not None:
            members += [props[part_idx[j]] for j in tree.query(g, predicate="contains")]
        heights = [h for h in (_height(m) for m in members) if h is not None]
        lon0, lat0, lon1, lat1 = g.bounds
        c = g.centroid
        m_lat = 111_320.0
        m_lon = m_lat * math.cos(math.radians(c.y))
        entries.append({
            "osm_id": p.get("osm_id") or None,
            "index": k,
            "name": p.get("name") or None,
            "category": landmark_category(p),
            "building": p.get("building") or None,
            "height_m": round(max(heights), 1) if heights else None,
            "height_source": p.get("height_source") or None,
            "parts": len(members) - 1,
            "roof_shapes": sorted({_tag(m, "roof:shape") or "flat" for m in members}),
            "area_m2": round(float(shapely.area(g)) * m_lat * m_lon, 1),
            "centroid": [round(c.x, 7), round(c.y, 7)],
            "bbox": {"north": lat1, "south": lat0, "east": lon1, "west": lon0},
        })
    entries = _drop_idless_copies(entries, geoms)
    tagged = [e for e in entries if e["category"]]
    rest = sorted((e for e in entries if not e["category"] and e["height_m"] is not None),
                  key=lambda e: -e["height_m"])[:max(tallest_n, 0)]
    for e in rest:
        e["category"] = "tallest"
    order = {"worship": 0, "civic": 1, "historic": 2, "attraction": 3, "tallest": 4}
    return sorted(tagged + rest, key=lambda e: (order[e["category"]], -(e["height_m"] or 0)))


def _drop_idless_copies(entries: list[dict], geoms: list) -> list[dict]:
    """Drop entries without an OSM id lying mostly inside one that has one.

    The client's buildings collection also carries the ``churches`` /
    ``fortifications`` layers' copies of the same outlines (no ``osm_id``), which
    would list every cathedral twice.
    """
    with_id = [e for e in entries if e["osm_id"]]
    if not with_id or len(with_id) == len(entries):
        return entries
    tree = shapely.STRtree([geoms[e["index"]] for e in with_id])
    out = []
    for e in entries:
        if not e["osm_id"]:
            g = geoms[e["index"]]
            hits = tree.query(g, predicate="intersects")
            if any(shapely.area(shapely.intersection(g, tree.geometries[h]))
                   >= REPLACE_OVERLAP * shapely.area(g) for h in hits):
                continue
        out.append(e)
    return out


def landmark_features(features: list[dict], osm_id: str) -> list[dict]:
    """The features of one landmark: those with ``osm_id`` plus the parts inside them."""
    own = [f for f in features if (f.get("properties") or {}).get("osm_id") == osm_id
           and f.get("geometry")]
    if not own:
        return []
    fp = shapely.union_all([shapely.geometry.shape(f["geometry"]) for f in own])
    out = list(own)
    for f in features:
        p = f.get("properties") or {}
        if f in own or not f.get("geometry") or not _is_part(p):
            continue
        g = shapely.geometry.shape(f["geometry"])
        if g.area > 0 and g.intersection(fp).area >= REPLACE_OVERLAP * g.area:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Resolved overrides
# ---------------------------------------------------------------------------

@dataclass
class LandmarkOverride:
    """One override with its data loaded: ``mesh`` for kind "mesh", ``ndsm`` +
    ``transform`` (lon/lat Affine, row 0 north) for kind "ndsm"."""

    osm_id: str
    kind: str
    spec: dict = field(default_factory=dict)
    mesh: object | None = None          # trimesh.Trimesh, closed
    ndsm: np.ndarray | None = None
    transform: object | None = None     # rasterio.Affine
    source: str = ""
    height_m: float | None = None       # kind "height": the building's new top


def override_height_m(spec: dict) -> float:
    """A height override's ``height_m`` as a float; LandmarkError unless 0 < h < MAX_HEIGHT_M."""
    try:
        h = float(spec.get("height_m"))
    except (TypeError, ValueError):
        raise LandmarkError("a height override needs height_m (metres above the ground)") from None
    if not math.isfinite(h) or not 0 < h < MAX_HEIGHT_M:
        raise LandmarkError(f"height_m must be above 0 and below {MAX_HEIGHT_M:g} m (got {h:g})")
    return h


def load_mesh(path: str | Path):
    """A trimesh from STL / OBJ / glTF, made a closed solid or refused (LandmarkError).

    Vertices are merged (glTF and OBJ split them at UV / normal seams), degenerate
    and duplicate faces dropped, small holes filled and normals made consistent.
    """
    import trimesh
    from numpy2stl.io.readers import load_trimesh
    from numpy2stl.processing.boolean import to_manifold

    try:
        mesh = load_trimesh(str(path))
    except Exception as exc:
        raise LandmarkError(f"cannot read {Path(path).name}: {exc}") from exc
    mesh = trimesh.Trimesh(mesh.vertices, mesh.faces, process=True)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    mesh.update_faces(mesh.nondegenerate_faces())
    mesh.update_faces(mesh.unique_faces())
    mesh.remove_unreferenced_vertices()
    if not len(mesh.faces):
        raise LandmarkError(f"{Path(path).name} has no faces")
    if not mesh.is_watertight:
        trimesh.repair.fill_holes(mesh)
    trimesh.repair.fix_normals(mesh)
    solid = to_manifold(mesh.vertices, mesh.faces, strict=False)
    import manifold3d as mf
    if solid.status() != mf.Error.NoError:
        edges = mesh.edges_sorted
        _, counts = np.unique(edges, axis=0, return_counts=True)
        open_edges = int((counts == 1).sum())
        over = int((counts > 2).sum())
        raise LandmarkError(
            f"{Path(path).name} is not a closed solid ({open_edges} open edge(s), {over} edge(s) "
            f"shared by more than two faces, manifold3d: {solid.status().name}); export it "
            f"watertight (e.g. the slicer's 'repair' / Blender's 3D-Print toolbox) and upload again")
    if mesh.volume < 0:
        mesh.invert()
    return mesh


def _footprint_bbox(features: list[dict], margin_m: float) -> tuple[float, float, float, float]:
    geoms = [shapely.geometry.shape(f["geometry"]) for f in features if f.get("geometry")]
    w, s, e, n = shapely.total_bounds(np.asarray(geoms, dtype=object))
    dlat = margin_m / 111_320.0
    dlon = dlat / max(math.cos(math.radians((n + s) / 2)), 1e-6)
    return n + dlat, s - dlat, e + dlon, w - dlon


def resolve_overrides(specs: dict | None, features: list[dict], *,
                      mesh_path: Callable[[str], Path] | None = None,
                      ndsm_fetch: Callable | None = None) -> dict[str, LandmarkOverride]:
    """Load every override's data. ``specs``: ``{osm_id: {"kind": ..., ...}}``.

    ``mesh_path(upload_id) -> Path`` finds an uploaded mesh; ``ndsm_fetch(provider,
    bbox, resolution_m)`` defaults to ``city2stl.height.providers.survey.ndsm_for_bbox``.
    Raises :class:`LandmarkError` naming the landmark on the first failure.
    """
    out: dict[str, LandmarkOverride] = {}
    for oid, spec in (specs or {}).items():
        spec = dict(spec or {})
        kind = str(spec.get("kind") or "osm")
        if kind not in OVERRIDE_KINDS:
            raise LandmarkError(f"landmark {oid}: unknown override kind {kind!r}")
        if kind == "osm":
            continue
        if kind == "height":
            try:
                spec["height_m"] = override_height_m(spec)
            except LandmarkError as exc:
                raise LandmarkError(f"landmark {oid}: {exc}") from exc
        if kind == "height":
            # Needs no footprint data of its own: a building the city data lacks is
            # reported as missing by LandmarkPlan.take instead of failing the build.
            out[oid] = LandmarkOverride(oid, kind, spec, source=str(spec.get("source") or kind),
                                        height_m=spec["height_m"])
            continue
        feats = landmark_features(features, oid)
        if not feats:
            raise LandmarkError(f"landmark {oid}: no building with this OSM id in the city data")
        try:
            if kind == "mesh":
                if not spec.get("upload_id") or mesh_path is None:
                    raise LandmarkError("no uploaded mesh (upload_id)")
                path = Path(mesh_path(str(spec["upload_id"])))
                spec.setdefault("format", path.suffix.lstrip(".").lower())
                mesh = load_mesh(path)
                out[oid] = LandmarkOverride(oid, kind, spec, mesh=mesh, source="mesh")
            else:
                out[oid] = _resolve_ndsm(oid, spec, feats, ndsm_fetch)
        except LandmarkError as exc:
            raise LandmarkError(f"landmark {oid}: {exc}") from exc
        except Exception as exc:  # survey endpoint / unknown upload
            raise LandmarkError(f"landmark {oid}: {exc}") from exc
    return out


def _resolve_ndsm(oid: str, spec: dict, feats: list[dict], fetch) -> LandmarkOverride:
    from city2stl.height.providers import survey

    fetch = fetch or survey.ndsm_for_bbox
    bbox = _footprint_bbox(feats, NDSM_MARGIN_M)
    provider = str(spec.get("provider") or "auto")
    names = ([p["name"] for p in survey.available_for_bbox(bbox) if p["available"]]
             if provider == "auto" else [provider])
    if not names:
        raise LandmarkError("no surveyed nDSM source covers this building")
    res = spec.get("resolution_m")
    for name in names:
        got = fetch(name, bbox, float(res) if res else None)
        if got is not None:
            arr, transform = got
            return LandmarkOverride(oid, "ndsm", spec, ndsm=np.asarray(arr, np.float32),
                                    transform=transform, source=name)
    raise LandmarkError(f"{' / '.join(names)} has no data over this building")


def coerce_overrides(overrides: dict | None) -> dict[str, LandmarkOverride]:
    """``{osm_id: LandmarkOverride | dict}`` -> resolved overrides (dicts must carry
    ``mesh`` or ``ndsm`` + ``transform`` already loaded, or a valid ``height_m``)."""
    out = {}
    for oid, ov in (overrides or {}).items():
        if isinstance(ov, LandmarkOverride):
            out[oid] = ov
        elif isinstance(ov, dict) and ov.get("kind") == "height":
            out[oid] = LandmarkOverride(oid, "height", ov, source=str(ov.get("source") or "height"),
                                        height_m=override_height_m(ov))
        elif isinstance(ov, dict) and ov.get("kind") in ("mesh", "ndsm", "reference"):
            out[oid] = LandmarkOverride(oid, ov["kind"], ov, mesh=ov.get("mesh"),
                                        ndsm=ov.get("ndsm"), transform=ov.get("transform"),
                                        source=str(ov.get("source") or ov["kind"]))
    return out


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def _rect_frame(geom) -> tuple[np.ndarray, float, float, float]:
    """(centre, angle of the long side, long, short) of a geometry's min rotated rectangle."""
    rect = shapely.minimum_rotated_rectangle(geom)
    c = np.asarray(rect.exterior.coords)[:4]
    e0, e1 = c[1] - c[0], c[2] - c[1]
    l0, l1 = float(np.hypot(*e0)), float(np.hypot(*e1))
    long_e, long_l, short_l = (e0, l0, l1) if l0 >= l1 else (e1, l1, l0)
    return c.mean(axis=0), math.atan2(long_e[1], long_e[0]), long_l, short_l


def _rot(a: float) -> np.ndarray:
    return np.array([[math.cos(a), -math.sin(a)], [math.sin(a), math.cos(a)]])


def fit_mesh_xy(xy: np.ndarray, footprint: Polygon, *, fit: str = "rectangle",
                rotation_deg: float = 0.0, scale: float = 1.0,
                offset_mm: tuple[float, float] = (0.0, 0.0)) -> tuple[np.ndarray, float]:
    """Mesh plan coordinates -> model mm on ``footprint``; returns (xy_mm, mm per mesh unit).

    Principal axes: the long side of the mesh outline's minimum rotated rectangle
    is turned onto the footprint's, and the two sides scaled to the footprint's
    (``fit="rectangle"``) or by their geometric mean (``"uniform"``). Of the two
    orientations that match (0° / 180°) the one overlapping the footprint most
    wins. Then ``rotation_deg`` (counter-clockwise) and ``scale`` about the
    footprint centre, and ``offset_mm``.
    """
    hull = shapely.MultiPoint(xy).convex_hull
    if hull.area <= 0:
        raise LandmarkError("the mesh has no plan area (flat or a line seen from above)")
    cm, am, lm, sm = _rect_frame(hull)
    cf, af, lf, sf = _rect_frame(footprint)
    if min(lm, sm) <= 0 or min(lf, sf) <= 0:
        raise LandmarkError("degenerate footprint or mesh outline")
    kl, ks = lf / lm, sf / sm
    if fit == "uniform":
        kl = ks = math.sqrt(kl * ks)
    best, best_iou = None, -1.0
    for flip in (0.0, math.pi):
        m = _rot(af) @ np.diag([kl, ks]) @ _rot(-(am + flip))
        cand = (xy - cm) @ m.T + cf
        h = shapely.MultiPoint(cand).convex_hull
        iou = h.intersection(footprint).area / max(h.union(footprint).area, 1e-12)
        if iou > best_iou:
            best, best_iou = cand, iou
    out = (best - cf) @ (_rot(math.radians(rotation_deg)) * scale).T + cf + np.asarray(offset_mm)
    return out, math.sqrt(kl * ks) * scale


def mesh_solid(ov: LandmarkOverride, footprint: Polygon, terrain, z_floor: float,
               ground_hi: float, z_per_m: float, osm_height_m: float | None) -> Mesh:
    """The override mesh placed on ``footprint`` (model mm), unioned with its skirt."""
    import manifold3d as mf
    from numpy2stl.core.extrude import prism
    from numpy2stl.processing.boolean import from_manifold, to_manifold

    spec = ov.spec
    v = np.asarray(ov.mesh.vertices, dtype=np.float64)
    up = str(spec.get("up_axis") or "auto").lower()
    if up == "auto":
        up = "y" if str(spec.get("format") or "").lower() in ("glb", "gltf") else "z"
    if up == "y":                       # glTF: Y up -> Z up (a proper rotation)
        v = np.column_stack([v[:, 0], -v[:, 2], v[:, 1]])
    mm_per_m = terrain.mm_per_m
    off = np.asarray(spec.get("offset_m") or (0.0, 0.0), dtype=np.float64)[:2] * mm_per_m
    xy, mm_per_unit = fit_mesh_xy(v[:, :2], footprint, fit=str(spec.get("fit") or "rectangle"),
                                  rotation_deg=float(spec.get("rotation_deg") or 0.0),
                                  scale=float(spec.get("scale") or 1.0), offset_mm=tuple(off))
    z = v[:, 2] - v[:, 2].min()
    if str(spec.get("vertical") or "true") == "fit":
        target = float(spec.get("height_m") or osm_height_m or 0.0)
        if target <= 0 or z.max() <= 0:
            raise LandmarkError("vertical fit needs height_m (or an OSM height) and a mesh with height")
        z = z / z.max() * target * z_per_m
    else:
        z = z * (mm_per_unit / mm_per_m) * z_per_m      # mesh units -> metres -> model mm
    placed = np.column_stack([xy, z + ground_hi - SEAT_OVERLAP_MM])
    body = to_manifold(placed, np.asarray(ov.mesh.faces), strict=False)
    if body.status() != mf.Error.NoError:
        raise LandmarkError(f"placed mesh is not manifold ({body.status().name})")
    skirt = prism(footprint, z_floor, ground_hi) if ground_hi - z_floor > 1e-6 else None
    solid = body + to_manifold(*skirt) if skirt is not None else body
    x0, y0, x1, y1 = terrain.rect.bounds
    clip = mf.Manifold.cube([x1 - x0, y1 - y0, float(placed[:, 2].max()) + 10.0]).translate([x0, y0, -1.0])
    solid = solid ^ clip
    if solid.is_empty():
        raise LandmarkError("the placed mesh lies outside the model")
    return from_manifold(solid)


def _fill_nearest(a: np.ndarray) -> np.ndarray:
    from scipy.ndimage import distance_transform_edt

    bad = ~np.isfinite(a)
    if not bad.any():
        return a
    idx = distance_transform_edt(bad, return_distances=False, return_indices=True)
    return a[tuple(idx)]


def _crop(ov: LandmarkOverride, bounds: tuple, terrain):
    """``(arr, transform, k, off)``: the raster window around ``bounds`` (model mm).

    A stored regional raster is large, so everything below works on this window; ``k`` /
    ``off`` map lon/lat to model mm (``terrain._lonlat_affine``).
    """
    arr = np.asarray(ov.ndsm, dtype=np.float64)
    t = ov.transform
    k, off = terrain._lonlat_affine()
    x0, y0, x1, y1 = bounds
    lons = (np.array([x0, x1]) - off[0]) / k[0]
    lats = (np.array([y0, y1]) - off[1]) / k[1]
    cs, rs = ~t * (lons, lats)
    c0, c1 = max(int(np.floor(min(cs))) - 2, 0), min(int(np.ceil(max(cs))) + 2, arr.shape[1])
    r0, r1 = max(int(np.floor(min(rs))) - 2, 0), min(int(np.ceil(max(rs))) + 2, arr.shape[0])
    if c1 > c0 and r1 > r0 and (r1 - r0, c1 - c0) != arr.shape:
        arr = arr[r0:r1, c0:c1]
        t = t @ Affine.translation(c0, r0)
    return arr, t, k, off


def _cell_centres(t, k, off, h: int, w: int) -> np.ndarray:
    cols, rows = np.meshgrid(np.arange(w) + 0.5, np.arange(h) + 0.5)
    lon, lat = t * (cols.ravel(), rows.ravel())
    return np.column_stack([lon, lat]) * k + off


def _open_m(ov: LandmarkOverride) -> float:
    """The unit's open-ground cutoff (m): its spec's ``open_m``, else :data:`REFERENCE_OPEN_M`."""
    return float(ov.spec.get("open_m", REFERENCE_OPEN_M))


def _open_cells(arr: np.ndarray, t, k, off, open_m: float | None = None):
    """Polygon (model mm) of the reference cells below ``open_m`` (default
    :data:`REFERENCE_OPEN_M`), or None.

    One-cell specks are dropped (3x3 opening) so a thin gap in a roof does not cut a sliver.
    """
    from scipy.ndimage import binary_opening

    if open_m is None:
        open_m = REFERENCE_OPEN_M
    low = np.isfinite(arr) & (arr < open_m)
    low = binary_opening(low, structure=np.ones((3, 3), bool))
    if not low.any():
        return None
    r, c = np.nonzero(low)
    lo = np.column_stack(t * (c, r)) * k + off
    hi = np.column_stack(t * (c + 1, r + 1)) * k + off
    boxes = shapely.box(np.minimum(lo[:, 0], hi[:, 0]), np.minimum(lo[:, 1], hi[:, 1]),
                        np.maximum(lo[:, 0], hi[:, 0]), np.maximum(lo[:, 1], hi[:, 1]))
    return shapely.union_all(boxes)


def reference_support(ov: LandmarkOverride, poly: Polygon, terrain) -> tuple[float, float]:
    """``(median_m, covered)`` of the reference over ``poly`` (model mm).

    ``covered`` is the share of the footprint's cells above the unit's open cutoff
    (:data:`REFERENCE_OPEN_M` unless its spec sets ``open_m``);
    ``median_m`` their median height (NaN when none). A footprint smaller than a cell reads
    the cell under its representative point.
    """
    arr, t, k, off = _crop(ov, poly.bounds, terrain)
    h, w = arr.shape
    if h == 0 or w == 0:
        return float("nan"), 0.0
    centres = _cell_centres(t, k, off, h, w)
    inside = shapely.contains_xy(poly, centres[:, 0], centres[:, 1])
    vals = arr.ravel()[inside]
    if not len(vals):
        pt = shapely.get_coordinates(poly.representative_point())[0]
        d = np.hypot(centres[:, 0] - pt[0], centres[:, 1] - pt[1])
        vals = arr.ravel()[[int(np.argmin(d))]]
    solid = np.isfinite(vals) & (vals >= _open_m(ov))
    covered = float(solid.mean())
    return (float(np.median(vals[solid])) if solid.any() else float("nan")), covered


def ndsm_solid(ov: LandmarkOverride, footprint: Polygon, terrain, z_floor: float,
               ground_hi: float, z_per_m: float, min_height_mm: float,
               exclude=None) -> list[Mesh]:
    """A solid whose top is the nDSM clipped exactly to ``footprint`` (model mm).

    For a ``reference`` override the cells below the unit's open cutoff (courtyards) are
    cut out of the solid, and ``exclude`` (kept OSM buildings) is subtracted from it.
    """
    from numpy2stl.core.extrude import close_surface, orient_ccw
    from scipy.ndimage import map_coordinates

    from city2stl.city_model import triangulate_polygon

    arr, t, k, off = _crop(ov, footprint.bounds, terrain)
    h, w = arr.shape
    # Cell centres in model mm, and the footprint's coverage by measured cells.
    centres = _cell_centres(t, k, off, h, w)
    inside = shapely.contains_xy(footprint, centres[:, 0], centres[:, 1])
    if not inside.any():
        # Footprint smaller than a cell: sample at its outline only.
        coverage = float(np.isfinite(arr).mean())
    else:
        coverage = float(np.isfinite(arr.ravel()[inside]).mean())
    if coverage < MIN_NDSM_COVERAGE:
        raise LandmarkError(f"{ov.source}: measured heights cover only {coverage:.0%} of the "
                            f"footprint (need {MIN_NDSM_COVERAGE:.0%})")
    cut = []
    if ov.kind == "reference":
        opened = _open_cells(arr, t, k, off, _open_m(ov))
        if opened is not None:
            cut.append(opened)
    if exclude is not None and not exclude.is_empty:
        cut.append(exclude)
    if cut:
        footprint = shapely.make_valid(footprint.difference(shapely.union_all(cut)))
        parts = [g for g in shapely.get_parts(footprint)
                 if g.geom_type == "Polygon" and not g.is_empty]
        if not parts:
            raise LandmarkError(f"{ov.source}: nothing left of the footprint after cutting "
                                "courtyards and kept OSM buildings")
        footprint = shapely.union_all(parts)
        inside = shapely.contains_xy(footprint, centres[:, 0], centres[:, 1])
    filled = _fill_nearest(arr)
    local = bool(ov.spec.get("local_ground"))
    cell_mm = float(np.mean(np.abs([t.a * k[0], t.e * k[1]])))
    out: list[Mesh] = []
    for poly in shapely.get_parts(footprint):
        poly = shapely.segmentize(poly, max(cell_mm, 0.05))
        tri = triangulate_polygon(poly, centres[inside] if inside.any() else None, 0.25 * cell_mm)
        if tri is None:
            continue
        v2, f = tri
        ll = terrain.unproject_xy(v2)
        c, r = ~t * (ll[:, 0], ll[:, 1])
        hm = map_coordinates(filled, [np.asarray(r) - 0.5, np.asarray(c) - 0.5], order=1, mode="nearest")
        base = terrain.sample(v2[:, 0], v2[:, 1]) if local else ground_hi
        top = np.column_stack([v2, base + np.maximum(hm * z_per_m, min_height_mm)])
        f = orient_ccw(top, f)
        out.append(close_surface(top, f, lambda xy: np.full(len(xy), z_floor)))
    if not out:
        raise LandmarkError("footprint could not be triangulated")
    return out


def scale_heights(props: dict, k: float) -> dict:
    """A copy of one feature's properties with every height scaled by ``k``.

    ``height_m`` (``DEFAULT_HEIGHT_M`` when missing, as ``city_model`` extrudes
    it), the base (``min_height`` / ``building:min_level``, written back as
    ``min_height``) and a numeric ``roof:height``. ``height_source`` becomes
    ``"landmark"``.
    """
    from city2stl.heights import min_height_from_tags, parse_length_m

    out = dict(props)
    out["height_m"] = (_height(props) or DEFAULT_HEIGHT_M) * k
    base = min_height_from_tags(props)
    if base > 0:
        out["min_height"] = base * k
        out.pop("building:min_level", None)
    roof = parse_length_m(props.get("roof:height"))
    if roof is not None:
        out["roof:height"] = roof * k
    out["height_source"] = "landmark"
    return out


# ---------------------------------------------------------------------------
# Plan: swap overridden buildings while build_on_terrain builds its layers
# ---------------------------------------------------------------------------

class LandmarkPlan:
    """Overrides for one ``build_on_terrain`` run, applied layer by layer.

    ``take`` is called by ``city_model.build_layer`` for each extrude layer with
    that layer's model-mm polygons. On the buildings layer it pulls out every
    overridden building (by ``osm_id``) plus the parts / features lying mostly
    inside it, and returns the override's solids in their place; a failing
    override leaves the OSM building as it was and is reported. A height
    override keeps those features, rescaled (:func:`scale_heights`), and adds no
    solid. Later extrude layers lose features lying mostly inside an overridden
    footprint.
    """

    def __init__(self, overrides: dict):
        self.overrides = coerce_overrides(overrides)
        self.footprints: dict[str, Polygon] = {}
        self.report: dict[str, dict] = {}

    def __bool__(self) -> bool:
        return bool(self.overrides)

    def _inside(self, polys: list[Polygon], fps: list) -> np.ndarray:
        if not fps or not polys:
            return np.zeros(len(polys), bool)
        u = shapely.union_all(fps)
        arr = np.asarray(polys, dtype=object)
        area = shapely.area(arr)
        return shapely.area(shapely.intersection(arr, u)) >= REPLACE_OVERLAP * np.maximum(area, 1e-12)

    @staticmethod
    def _kept_osm(ov: LandmarkOverride, polys: list, props: list, mine: np.ndarray,
                  own: set, terrain) -> list[int]:
        """Indices of OSM features inside a reference outline that stay in the model.

        The reference is a blurry ~1 m-cell copy, so an OSM building is kept when it is
        taller than the reference over its footprint by more than :data:`KEEP_OSM_ABOVE_M`,
        or when the reference has solid cells over less than :data:`KEEP_OSM_MIN_COVER` of it.
        The outline's own features (``own``) and ``building:part`` features (they belong to
        the swapped building) are never kept.
        """
        kept = []
        for j in np.flatnonzero(mine):
            if int(j) in own or _is_part(props[j]):
                continue
            median, covered = reference_support(ov, polys[j], terrain)
            h = _height(props[j])
            if covered < KEEP_OSM_MIN_COVER or (h is not None and h > median + KEEP_OSM_ABOVE_M):
                kept.append(int(j))
        return kept

    def take(self, layer: str, polys: list[Polygon], props: list[dict], terrain,
             style) -> tuple[list[Polygon], list[dict], list[Mesh]]:
        if layer != "buildings":
            drop = self._inside(polys, list(self.footprints.values()))
            return ([p for p, d in zip(polys, drop, strict=True) if not d],
                    [p for p, d in zip(props, drop, strict=True) if not d], [])
        groups: dict[str, list[int]] = {}
        for k, pr in enumerate(props):
            oid = str(pr.get("osm_id") or "")
            if oid in self.overrides:
                groups.setdefault(oid, []).append(k)
        free: dict[str, Polygon] = {}      # overrides that bring their own footprint
        for oid, ov in self.overrides.items():
            if oid in groups:
                continue
            ring = ov.spec.get("footprint_lonlat")
            if ring:
                lonlat = shapely.make_valid(Polygon(ring[0], ring[1:]))
                free[oid] = terrain.project(np.array([lonlat], dtype=object))[0]
                groups[oid] = []
            else:
                self.report[oid] = {"kind": ov.kind, "status": "missing",
                                    "reason": "no building with this OSM id in the model"}
        solids: list[Mesh] = []
        props = list(props)
        removed = np.zeros(len(polys), bool)
        z_per_m = terrain.scale.z_mm_per_m * style.height_scale
        for oid, ks in groups.items():
            ov = self.overrides[oid]
            fp = free[oid] if oid in free else shapely.union_all([polys[k] for k in ks])
            mine = self._inside(polys, [fp]) & ~removed
            mine[ks] = True
            lo, hi = terrain.ranges_under(list(shapely.get_parts(fp)))
            z_floor, ground_hi = max(float(lo.min()) - 0.2, 0.0), float(hi.max())
            heights = [h for h in (_height(props[k]) for k in np.flatnonzero(mine)) if h is not None]
            if ov.kind == "height":
                old = max(heights) if heights else DEFAULT_HEIGHT_M
                for k in np.flatnonzero(mine):
                    props[k] = scale_heights(props[k], ov.height_m / old)
                self.footprints[oid] = fp
                self.report[oid] = {"kind": ov.kind, "source": ov.source, "status": "applied",
                                    "height_m": round(ov.height_m, 2), "osm_height_m": round(old, 2),
                                    "scaled_features": int(mine.sum())}
                continue
            kept: list[int] = []
            if ov.kind == "reference":
                kept = self._kept_osm(ov, polys, props, mine, set(ks), terrain)
                mine[kept] = False
            exclude = shapely.union_all([polys[j] for j in kept]) if kept else None
            try:
                if ov.kind == "mesh":
                    got = [mesh_solid(ov, fp, terrain, z_floor, ground_hi, z_per_m,
                                      max(heights) if heights else None)]
                else:
                    got = ndsm_solid(ov, fp, terrain, z_floor, ground_hi, z_per_m,
                                     style.min_height_mm, exclude=exclude)
            except LandmarkError as exc:
                logger.warning("landmark %s override not applied: %s", oid, exc)
                self.report[oid] = {"kind": ov.kind, "status": "failed", "reason": str(exc)}
                continue
            solids.extend(got)
            removed |= mine
            self.footprints[oid] = fp
            self.report[oid] = {"kind": ov.kind, "source": ov.source, "status": "applied",
                                "replaced_features": int(mine.sum()),
                                "kept_osm_features": len(kept),
                                "faces": int(sum(len(f) for _, f in got))}
        keep = ~removed
        return ([p for p, kk in zip(polys, keep, strict=True) if kk],
                [p for p, kk in zip(props, keep, strict=True) if kk], solids)


# ---------------------------------------------------------------------------
# Reference landmark: the Alhambra (F-DETAIL D4)
# ---------------------------------------------------------------------------

#: Stored by ``tools/landmarks/build_reference_raster.py``.
REFERENCE_DIR = Path(__file__).resolve().parents[1] / "data" / "landmarks"
#: Depinch floor (m): a reference cell never drops below this above ground, so the top never
#: collapses onto the terrain and pinches the solid (the rule only swaps buildings >= 1 m tall).
REFERENCE_MIN_M = 1.0
#: Raw reference height (m) below which a cell is open ground (a courtyard, a plaza), cut out
#: of the swapped solid instead of being floored to :data:`REFERENCE_MIN_M`.
REFERENCE_OPEN_M = 2.0
#: An OSM building under a reference outline stays when its height beats the reference's
#: median over its footprint by more than this (m) ...
KEEP_OSM_ABOVE_M = 1.5
#: ... or when the reference has solid cells over less than this share of its footprint.
KEEP_OSM_MIN_COVER = 0.5
#: Bump when the reference solid's geometry changes: part of each override's ``spec``, so it
#: is in ``model_cache.overrides_digest`` and stale cached solids rebuild.
REFERENCE_SOLID_VERSION = 3


def load_reference(directory: Path | str | None = None) -> tuple[np.ndarray, object, dict, dict]:
    """``(height_m, transform, meta, rule)`` from the stored raster and swap rule.

    ``height_m`` is float32 metres above ground (NaN = no data), ``transform`` the lon/lat
    Affine (row 0 north); ``meta`` / ``rule`` are the two JSON documents' contents.
    """
    import json

    d = Path(directory) if directory else REFERENCE_DIR
    with np.load(d / "alhambra_reference.npz") as z:
        cm = z["height_cm"]
        meta = json.loads(str(z["meta"]))
    h = np.where(cm == 65535, np.nan, cm / 100.0).astype(np.float32)
    with open(d / "alhambra_reference_rule.json", encoding="utf-8") as fh:
        rule = json.load(fh)
    return h, Affine(*meta["transform_affine"]), meta, rule


def _depinch(h: np.ndarray, open_m: float = REFERENCE_OPEN_M) -> np.ndarray:
    """Heightfield depinching: fill one-cell gaps from their neighbours and floor at
    :data:`REFERENCE_MIN_M`, so no cell sits on the ground inside a roof.

    Cells whose raw height is under ``open_m`` (default :data:`REFERENCE_OPEN_M`) are open
    ground: they are set to 0 (not floored), which ``ndsm_solid`` cuts out of the solid. Gap
    filling copies only solid neighbours, so a courtyard does not grow roofs."""
    from scipy.ndimage import maximum_filter

    out = np.array(h, dtype=np.float32)
    gap = ~np.isfinite(out)
    if gap.any():
        near = maximum_filter(np.where(gap | (out < open_m), -np.inf, out), size=3)
        fill = gap & np.isfinite(near)
        out[fill] = near[fill]
    solid = np.maximum(out, REFERENCE_MIN_M)
    return np.where(np.isfinite(out), np.where(out < open_m, 0.0, solid), np.nan
                    ).astype(np.float32)


def reference_landmarks(bbox: dict, *, enabled: bool = False, raster=None, rule: dict | None = None,
                        ) -> tuple[dict, dict]:
    """The Alhambra swap as ``(overrides, stats)`` for ``build_on_terrain(landmark_overrides=...)``.

    Off (``enabled`` false), outside the raster's bbox, or nothing to swap: ``({}, stats)``,
    so the model is unchanged. Otherwise one ``"reference"`` ``LandmarkOverride`` per rule unit: keyed by
    its OSM id, or ``ref:<label>`` with a ``footprint_lonlat`` for the ones OSM carries only in
    the towers / churches layers. Units share one depinched raster (``ndsm_solid`` crops it), except one with an
    ``open_m`` in the rule: it gets its own cutoff and its own depinch.

    ``raster`` is ``(height_m, transform)`` (tests pass a tiny synthetic one) and ``rule`` the
    ``alhambra_reference_rule.json`` structure; both default to the stored files. ``bbox`` has
    ``north`` / ``south`` / ``east`` / ``west``.
    """
    stats = {"enabled": bool(enabled), "units": 0, "labels": 0, "reason": ""}
    if not enabled:
        stats["reason"] = "off"
        return {}, stats
    if raster is None or rule is None:
        try:
            h, t, _meta, stored = load_reference()
        except FileNotFoundError:
            stats["reason"] = "reference raster not built (tools/landmarks/build_reference_raster.py)"
            return {}, stats
        raster = (h, t) if raster is None else raster
        rule = stored if rule is None else rule
    height, t = raster
    rows, cols = np.shape(height)
    lon_w, lat_n = t * (0, 0)
    lon_e, lat_s = t * (cols, rows)
    if (min(lon_w, lon_e) > bbox["east"] or max(lon_w, lon_e) < bbox["west"]
            or min(lat_s, lat_n) > bbox["north"] or max(lat_s, lat_n) < bbox["south"]):
        stats["reason"] = "outside the raster"
        return {}, stats
    raw = np.asarray(height)
    ndsm = _depinch(raw)
    source = str(rule.get("source") or "reference raster")
    out: dict = {}
    for u in rule["units"]:
        key = u.get("osm_id") or u["key"]
        spec = {"kind": "reference", "source": source, "local_ground": True,
                "solid_version": REFERENCE_SOLID_VERSION}
        if not u.get("osm_id"):
            ring = u.get("footprint")
            cx, cy = u.get("centroid") or (None, None)
            if not ring or cx is None or not (bbox["west"] <= cx <= bbox["east"]
                                              and bbox["south"] <= cy <= bbox["north"]):
                continue
            spec["footprint_lonlat"] = ring
        unit_ndsm = ndsm
        if u.get("open_m") is not None:
            # Per-unit cutoff: depinch the raw raster again with it (the shared one has already
            # floored the heights between the global and the unit's cutoff).
            spec["open_m"] = float(u["open_m"])
            unit_ndsm = _depinch(raw, spec["open_m"])
        out[key] = LandmarkOverride(key, "reference", spec, ndsm=unit_ndsm, transform=t, source=source)
        stats["labels"] += len(u["labels"])
    stats["units"] = len(out)
    return out, stats
