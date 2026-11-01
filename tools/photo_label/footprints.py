"""OSM building footprints: Overpass JSON or GeoJSON to a footprint collection, source tags, pipeline heights and
point-in-ring lookups. Pure functions, no server state."""
from __future__ import annotations

import json
import math
from pathlib import Path

import terrain_solve as ts


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
        src = pr.get("height_source")
        h = pr.get("height_m") if src not in (None, "default") else None
        src = ("osm_levels" if src == "osm_levels" else "osm_tag") if h is not None else "default"
        for ring in rings:
            if len(ring) >= 4:
                feats.append({"type": "Feature",
                              "properties": {"id": pr.get("id", i), "name": pr.get("name"),
                                             "building": pr.get("building", "yes"), "height": h,
                                             "levels": pr.get("levels"), "height_source": src},
                              "geometry": {"type": "Polygon", "coordinates": [ring]}})
    return {"type": "FeatureCollection", "features": feats}


WITHHELD_RATIO = 1.5    # measured / published at or above this (and WITHHELD_MIN_M more) = withheld
WITHHELD_MIN_M = 10.0


def tag_sources(fc: dict) -> dict:
    """`height_source` of Overpass footprints in place: osm_tag (a `height` tag), osm_levels, default."""
    for f in fc["features"]:
        pr = f["properties"]
        if "height_source" not in pr:
            h, lv = ts._num(pr.get("height")), ts._num(pr.get("levels"))
            pr["height_source"] = "osm_tag" if h and h > 0 else "osm_levels" if lv and lv > 0 else "default"
    return fc


def load_heights(path) -> list[dict]:
    """The pipeline's per-building heights from `path`: a seed export folder (every
    `seed_*/heights.json`, `{name: {height_m, published_m, tier, name, lat, lon}}`) or one such
    file. A building seen by several seeds becomes one entry: the median `measured_m` (the seed's
    `height_m`) and `published_m`, the best tier. [] when nothing is there."""
    import statistics
    p = Path(path)
    files = [p] if p.is_file() else sorted(p.glob("seed_*/heights.json")) + sorted(p.glob("heights.json"))
    seen: dict[str, list[dict]] = {}
    for fp in files:
        try:
            d = json.loads(fp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for k, v in (d.items() if isinstance(d, dict) else []):
            if isinstance(v, dict) and v.get("lat") is not None and v.get("lon") is not None:
                seen.setdefault(str(v.get("name") or k), []).append(v)
    rank = {"corroborated": 3, "measured": 2, "prior": 0}
    out = []
    for name, vs in seen.items():
        def med(key, vs=vs):
            xs = [float(v[key]) for v in vs if v.get(key) is not None]
            return statistics.median(xs) if xs else None
        out.append({"name": name, "lat": med("lat"), "lon": med("lon"), "measured_m": med("height_m"),
                    "published_m": med("published_m"),
                    "tier": max((v.get("tier") for v in vs), key=lambda t: rank.get(t, 1), default=None),
                    "n_seeds": len(vs)})
    return out


def apply_heights(fc: dict, entries: list[dict]) -> int:
    """Merge `load_heights` entries onto the footprints in place; returns how many footprints matched.

    An entry belongs to the footprint containing its lat/lon (the seed names, b0564, do not
    index the pipeline's footprints), else the one whose bounding box is nearest, within 12 m. Each matched footprint gets
    `height` (drawn: OSM tag, then published, then levels; a footprint with none keeps no height and
    `terrain_solve.building_height` falls back to 9 m), `height_source` (osm_tag / osm_levels /
    published / measured_withheld / default), `tier`, and `measured_m` / `published_m` when they
    differ from the drawn height. measured_withheld: the measured height is at least WITHHELD_RATIO x
    the published one and WITHHELD_MIN_M above it, i.e. the pipeline kept a tall building low."""
    feats = fc["features"]
    grid: dict[tuple[int, int], list[int]] = {}
    boxes = []
    for i, f in enumerate(feats):
        ring = f["geometry"]["coordinates"][0]
        xs, ys = [q[0] for q in ring], [q[1] for q in ring]
        boxes.append((min(xs), min(ys), max(xs), max(ys)))
        for gx in range(math.floor(min(xs) * 1000), math.floor(max(xs) * 1000) + 1):
            for gy in range(math.floor(min(ys) * 1000), math.floor(max(ys) * 1000) + 1):
                grid.setdefault((gx, gy), []).append(i)
    hit_by: dict[int, list[dict]] = {}
    for e in entries:
        lat, lon = e["lat"], e["lon"]
        cx, cy = math.floor(lon * 1000), math.floor(lat * 1000)
        hit = next((i for i in grid.get((cx, cy), [])
                    if boxes[i][0] <= lon <= boxes[i][2] and boxes[i][1] <= lat <= boxes[i][3]
                    and _inside(lon, lat, feats[i]["geometry"]["coordinates"][0])), None)
        if hit is None:                                         # nearest bounding box within 12 m
            kx, best = 111_320.0 * math.cos(math.radians(lat)), 12.0
            for gx in (-1, 0, 1):
                for gy in (-1, 0, 1):
                    for i in grid.get((cx + gx, cy + gy), []):
                        x0, y0, x1, y1 = boxes[i]               # distance to the footprint's bounding box
                        d = math.hypot((max(x0 - lon, 0.0, lon - x1)) * kx, (max(y0 - lat, 0.0, lat - y1)) * 111_320.0)
                        if d < best:
                            best, hit = d, i
        if hit is not None:
            hit_by.setdefault(hit, []).append(e)
    n = 0
    for i, es in hit_by.items():
        pr = feats[i]["properties"]
        pub = [e["published_m"] for e in es if e.get("published_m")]
        mea = [e["measured_m"] for e in es if e.get("measured_m")]
        published = max(pub) if pub else (max(mea) if mea else None)
        measured = max(mea) if mea else published
        src = pr.get("height_source") or "default"
        drawn = ts._num(pr.get("height")) if src in ("osm_tag", "osm_levels") else None
        if src != "osm_tag" and published:
            drawn, src = published, "published"
        if drawn is None:
            continue
        if src == "published" and measured and measured >= WITHHELD_RATIO * published \
                and measured - published >= WITHHELD_MIN_M:
            src = "measured_withheld"
        pr["height"], pr["height_source"], pr["tier"] = round(drawn, 1), src, es[0].get("tier")
        if measured and abs(measured - drawn) > 0.5:
            pr["measured_m"] = round(measured, 1)
        if published and abs(published - drawn) > 0.5:
            pr["published_m"] = round(published, 1)
        n += 1
    return n


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
