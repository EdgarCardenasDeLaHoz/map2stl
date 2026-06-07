"""city2stl.skyline.region_render - PDF report rendering for skyline.

Split out of region_pdf.py (F-CLEAN14, 2026-06-07). All matplotlib/PdfPages
page builders, minimap + overlay drawing, the region location map, negative-seed
view construction, and the _StepTimer. Pure rendering/diagnostics; consumes the
results produced by pano_registration. region_pdf re-imports these and
run_region_pdf_report calls them.
"""

from __future__ import annotations

import json
import logging
import math
import time
from contextlib import contextmanager
from pathlib import Path

import cv2
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
from matplotlib.backends.backend_pdf import PdfPages

from .pipeline import BuildingRecord
from .region_types import RegionBBox, SeedViewRegistration, SkylinePoint
from .region_data import _distance_m, _feature_rings
from .region_config import (
    _F_SKY13_ENABLED,
    _F_SKY13_RADIUS_M,
    _F_SKY13_SAT_BG_ENABLED,
    _SEGMENT_PALETTE,
)

# F-CLEAN14: the original region_pdf.py referenced ``logger`` in two
# osm_water-unavailable branches without ever defining it (latent NameError,
# never hit because osm_water is always importable). Defined here so those
# branches log instead of crashing.
logger = logging.getLogger(__name__)


def _draw_location_map(ax, bbox: RegionBBox, screened: list[dict], osm_data: dict) -> None:
    ax.set_title("Skyline Request Location Screen")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.plot([bbox.west, bbox.east, bbox.east, bbox.west, bbox.west],
            [bbox.south, bbox.south, bbox.north, bbox.north, bbox.south],
            "k-", linewidth=1.2, label="Region")

    # Draw OSM building footprints for context — PatchCollection for speed.
    from matplotlib.collections import PatchCollection  # noqa: PLC0415
    _ctx_polys: list[mpatches.Polygon] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        for ring in _feature_rings(feat):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            _ctx_polys.append(mpatches.Polygon(
                np.column_stack([xs, ys]), closed=True))
    if _ctx_polys:
        ax.add_collection(PatchCollection(
            _ctx_polys,
            facecolor=(0.75, 0.75, 0.75, 0.18),
            edgecolor=(0.45, 0.45, 0.45, 0.25),
            linewidths=0.3,
            match_original=False,
        ))

    for row in screened:
        p: SkylinePoint = row["point"]
        color = "tab:green" if row["coverage"] == "good" else (
            "tab:orange" if row["coverage"] == "medium" else "tab:red")
        marker = "*" if p.source == "seed" else "o"
        size = 140 if p.source == "seed" else 60
        ax.scatter([p.lon], [p.lat], c=color, s=size, marker=marker, alpha=0.9)
        ax.text(p.lon, p.lat, f" {p.name}", fontsize=8)

        # Draw heading vector to show camera angle used for screening
        span = max(bbox.east - bbox.west, bbox.north - bbox.south)
        vec = span * 0.04
        theta = math.radians(p.heading)
        dlon = vec * math.sin(theta)
        dlat = vec * math.cos(theta)
        ax.arrow(
            p.lon,
            p.lat,
            dlon,
            dlat,
            width=span * 0.0008,
            head_width=span * 0.006,
            head_length=span * 0.008,
            length_includes_head=True,
            color=color,
            alpha=0.8,
        )
    ax.grid(alpha=0.25)

def _registration_overlay(
    image: np.ndarray,
    registration: dict,
    matched_segments: list[dict] | None = None,
) -> np.ndarray:
    out = image.copy()
    contour = np.asarray(registration.get("contour"), dtype=np.float32)
    if contour.size:
        for x in range(1, contour.size):
            y1 = int(contour[x - 1])
            y2 = int(contour[x])
            cv2.line(out, (x - 1, y1), (x, y2), (0, 220, 255), 2)

    if matched_segments:
        for i, seg in enumerate(matched_segments):
            # Use the seed-level index when present (set by
            # ``_register_views._assign_seed_index``) so a building keeps
            # the same badge number across every view of the seed in
            # which it appears. Fall back to the per-view list position
            # for older callers that don't populate ``seed_index``.
            badge_n = int(seg.get("seed_index", i + 1))
            color = _SEGMENT_PALETTE[(badge_n - 1) % len(_SEGMENT_PALETTE)]
            xL, xR = int(seg["x_left"]), int(seg["x_right"])
            top_y, base_y = int(seg["top_y"]), int(seg["base_y"])
            cv2.rectangle(out, (xL, top_y), (xR, base_y), color, 2)
            badge_x = xL + 2
            badge_y = max(18, top_y + 2)
            cv2.circle(out, (badge_x + 9, badge_y + 7), 11, color, -1)
            cv2.circle(out, (badge_x + 9, badge_y + 7),
                       11, (0, 0, 0), 1, cv2.LINE_AA)
            cv2.putText(
                out, str(badge_n), (badge_x + (3 if badge_n < 10 else 0),
                                    badge_y + 12),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
            )
            # Feature_id text labels removed (run #37): the seed-stable
            # badge number is the canonical link between image and
            # minimap, and the text labels (b0117, b0218, …) overlapped
            # badly when bboxes clustered, obscuring the badge numbers
            # themselves. Hover-tooltip-style labels would belong in an
            # interactive viewer, not the static PNG overlay.

    for proj in registration.get("projections", []):
        x = int(round(float(proj.get("x_px", 0))))
        if 0 <= x < out.shape[1] and contour.size:
            y = int(round(float(contour[x])))
            cv2.circle(out, (x, y), 3, (255, 80, 80), -1)
    return out

def _negative_seed_views(
    seed: "SkylinePoint",
    cached_views: list[dict],
) -> list["SeedViewRegistration"]:
    """Build minimal view rows for a negative seed WITHOUT running analysis.

    A negative seed (declared in ``negative_seeds``) is a known-bad skyline —
    running registration / silhouette / matching on it is wasted work. We keep
    the captured frames as labelled ``is_negative`` examples (viewable in the
    report, useful for mining more negatives later) but skip every analysis
    step. No heights, no matched segments, no masks.
    """
    rows: list[SeedViewRegistration] = []
    for cv in cached_views:
        cap = cv["cap"]
        rows.append(
            SeedViewRegistration(
                seed_name=seed.name,
                seed_lat=seed.lat,
                seed_lon=seed.lon,
                heading=cv["geo_heading"],
                fov=seed.fov,
                registration_score=float("inf"),
                best_offset=0.0,
                estimates_count=0,
                image=cv["image"],
                matched_segments=[],
                is_aerial=cap.viewpoint.pitch < -8.0,
                is_negative=True,
            )
        )
    return rows

def _draw_osm_coastline_overlay(
    ax,
    seed_lon: float,
    seed_lat: float,
    mlon: float,
    mlat: float,
    osm_data: dict,
    radius_m: float = 1000.0,
    *,
    satellite_bg: bool = False,
    pano_projected_coastline: "list[tuple[float, float]] | None" = None,
    pano_projected_vegetation: "list[tuple[float, float]] | None" = None,
    pano_osm_iou: float | None = None,
    pano_osm_n_keypoints: int | None = None,
) -> None:
    """F-SKY13: draw OSM coastline + 1 km consideration window on a minimap.

    OSM ``natural=coastline`` linestrings within ``radius_m`` of the seed are
    drawn as a solid blue line (the primary coastline ground truth). A dashed
    grey circle marks the consideration window. Optional water polygons (bay/
    ocean/sea) get a faint blue fill so the user can immediately see which
    side of the coastline is wet.

    When ``satellite_bg=True`` an ESRI satellite tile covering the 1 km
    window is fetched (or loaded from the on-disk cache populated by
    ``city2stl.skyline.satellite_image.fetch_region_satellite``) and
    rendered as a faint underlay so the OSM features sit on top of real
    imagery. Reuses the existing satellite-tile primitive — does not add a
    second fetch path. Network failures degrade gracefully (no background
    drawn).

    No-op when the OSM ``waterways`` layer is empty or contains no features
    within the radius — keeps inland seeds clean.
    """
    try:
        from city2stl.skyline.osm_water import (  # noqa: PLC0415
            clip_to_radius,
            extract_coastline_features,
            extract_water_features,
            extract_green_features,
        )
    except ImportError as exc:
        logger.warning("F-SKY13 osm_water module unavailable: %s", exc)
        return

    # ── Optional satellite-image background (under everything) ─────────────
    # Computed before the no-op short-circuit so the background shows even
    # on inland seeds (where there's no coastline but the imagery is still
    # informative).
    if satellite_bg:
        try:
            from city2stl.skyline.satellite_image import (  # noqa: PLC0415
                fetch_region_satellite,
            )
            # Build a 1 km bbox around the seed. mlon/mlat are lon/lat→m
            # scale factors; invert to get degrees per metre.
            dlon = radius_m / mlon
            dlat = radius_m / mlat
            sat_bbox = (
                seed_lat - dlat,  # south
                seed_lon - dlon,  # west
                seed_lat + dlat,  # north
                seed_lon + dlon,  # east
            )
            sat_img, _proj, _meta = fetch_region_satellite(
                sat_bbox, target_m_per_px=2.0,
            )
            ax.imshow(
                sat_img,
                extent=(sat_bbox[1], sat_bbox[3], sat_bbox[0], sat_bbox[2]),
                origin="upper",
                alpha=0.55,
                zorder=0,
                interpolation="bilinear",
            )
        except Exception as exc:
            logger.info(
                "F-SKY13 satellite background unavailable: %s", exc
            )

    seed_lonlat = (seed_lon, seed_lat)
    coastline = clip_to_radius(
        extract_coastline_features(osm_data), seed_lonlat, radius_m
    )
    water = clip_to_radius(
        extract_water_features(osm_data), seed_lonlat, radius_m
    )

    # F-SKY18: OSM green polygons (parks/grass/forest) as faint green fill,
    # the visual ground truth the pano green dots should land on.
    green = clip_to_radius(extract_green_features(osm_data), seed_lonlat, radius_m)
    if green:
        from matplotlib.collections import PatchCollection  # noqa: PLC0415
        green_polys: list[mpatches.Polygon] = []
        for feat in green:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if geom.get("type") == "Polygon":
                rings = [coords[0]] if coords else []
            elif geom.get("type") == "MultiPolygon":
                rings = [poly[0] for poly in coords if poly]
            else:
                rings = []
            for ring in rings:
                if len(ring) < 3:
                    continue
                xs = [float(p[0]) for p in ring]
                ys = [float(p[1]) for p in ring]
                green_polys.append(mpatches.Polygon(
                    np.column_stack([xs, ys]), closed=True))
        if green_polys:
            ax.add_collection(PatchCollection(
                green_polys,
                facecolor=(0.45, 0.78, 0.45, 0.22),
                edgecolor=(0.20, 0.55, 0.25, 0.40),
                linewidths=0.4,
                zorder=1.3,
                match_original=False,
            ))

    if not coastline and not water and not green:
        return  # inland seed with no greenery either — nothing to draw

    # ── Water-area fill (faint blue, sits between grey footprints and coastline)
    if water:
        from matplotlib.collections import PatchCollection  # noqa: PLC0415
        water_polys: list[mpatches.Polygon] = []
        for feat in water:
            geom = feat.get("geometry") or {}
            coords = geom.get("coordinates") or []
            if geom.get("type") == "Polygon":
                rings = [coords[0]] if coords else []
            elif geom.get("type") == "MultiPolygon":
                rings = [poly[0] for poly in coords if poly]
            else:
                rings = []
            for ring in rings:
                if len(ring) < 3:
                    continue
                xs = [float(p[0]) for p in ring]
                ys = [float(p[1]) for p in ring]
                water_polys.append(mpatches.Polygon(
                    np.column_stack([xs, ys]), closed=True))
        if water_polys:
            ax.add_collection(PatchCollection(
                water_polys,
                facecolor=(0.45, 0.65, 0.85, 0.18),
                edgecolor=(0.20, 0.40, 0.65, 0.30),
                linewidths=0.4,
                zorder=1.5,
                match_original=False,
            ))

    # ── Coastline linestrings — solid blue, the trusted reference
    for feat in coastline:
        geom = feat.get("geometry") or {}
        gtype = geom.get("type")
        coords = geom.get("coordinates") or []
        if gtype == "LineString":
            lines = [coords]
        elif gtype == "MultiLineString":
            lines = coords
        else:
            continue
        for line in lines:
            if len(line) < 2:
                continue
            xs = [float(p[0]) for p in line]
            ys = [float(p[1]) for p in line]
            ax.plot(
                xs, ys,
                color=(0.10, 0.30, 0.70),
                linewidth=1.6,
                alpha=0.85,
                zorder=3.0,
                label="OSM coastline" if "_osm_coastline_label" not in ax.__dict__ else None,
            )
            ax.__dict__["_osm_coastline_label"] = True

    # ── 1 km consideration window (dashed grey circle around the seed) ──
    # Convert metres to degree offsets via the cached lat-scaled factors.
    # 100-point polyline closes the loop visually without using matplotlib's
    # Circle patch (which doesn't respect equal-aspect well at these scales).
    n_pts = 100
    angles = np.linspace(0.0, 2.0 * math.pi, n_pts, dtype=np.float64)
    radius_lon = radius_m / mlon
    radius_lat = radius_m / mlat
    circle_x = seed_lon + radius_lon * np.cos(angles)
    circle_y = seed_lat + radius_lat * np.sin(angles)
    ax.plot(
        circle_x, circle_y,
        color=(0.35, 0.35, 0.35, 0.65),
        linewidth=0.9,
        linestyle="--",
        zorder=2.5,
    )

    # ── F-SKY13 Phase B: pano-projected coastline (where the pano "sees"
    # water meeting land, projected back to lon/lat). Each entry is a
    # discrete sea-level point at the apparent water-top of one pano column.
    # Drawn as scattered dots rather than a connected polyline because
    # adjacent columns may sample different distances (e.g. when a pier
    # juts out into the bay) and a polyline would zig-zag awkwardly.
    if pano_projected_coastline:
        xs = [p[0] for p in pano_projected_coastline]
        ys = [p[1] for p in pano_projected_coastline]
        # Bright sky-blue dots — high contrast against the tan building
        # footprints and visually distinct from the navy (0.10,0.30,0.70)
        # OSM coastline LINE, so "do the pano dots fall on the OSM line?"
        # is an easy eyeball check.
        ax.scatter(
            xs, ys,
            s=6,
            color=(0.10, 0.60, 0.95),
            alpha=0.85,
            zorder=3.5,
            edgecolors="none",
            label="pano-projected coastline",
        )

    # F-SKY18: pano-projected vegetation base points, depth-snapped to OSM
    # green polygon boundaries. Bright green dots — should land on the faint
    # green OSM polygon fill drawn above.
    if pano_projected_vegetation:
        gx = [p[0] for p in pano_projected_vegetation]
        gy = [p[1] for p in pano_projected_vegetation]
        ax.scatter(
            gx, gy,
            s=6,
            color=(0.10, 0.75, 0.20),
            alpha=0.85,
            zorder=3.5,
            edgecolors="none",
            label="pano-projected vegetation",
        )

    # ── F-SKY13 Phase B: pano↔OSM IoU score as a small annotation in the
    # top-left corner of the minimap. Lets the user see the registration
    # confidence number alongside the visual coastline agreement check.
    if pano_osm_iou is not None:
        n_kp = pano_osm_n_keypoints or 0
        score_txt = f"pano↔OSM IoU: {pano_osm_iou:.2f}   ({n_kp} keypoints)"
        ax.text(
            0.02, 0.98, score_txt,
            transform=ax.transAxes,
            fontsize=8,
            fontweight="bold",
            verticalalignment="top",
            color=(0.10, 0.30, 0.70),
            bbox=dict(
                boxstyle="round,pad=0.3",
                facecolor=(1.0, 1.0, 1.0, 0.85),
                edgecolor=(0.10, 0.30, 0.70, 0.6),
                linewidth=0.6,
            ),
            zorder=10,
        )

def _draw_view_minimap(
    ax,
    seed_lat: float,
    seed_lon: float,
    heading_deg: float,
    fov_deg: float,
    osm_data: dict,
    matched_segments: list[dict],
    buildings_by_id: dict | None = None,
    image_width: int = 960,
    radius_m: float = 1500.0,
    *,
    pano_osm_iou: float | None = None,
    pano_osm_n_keypoints: int | None = None,
    pano_projected_coastline: "list[tuple[float, float]] | None" = None,
    pano_projected_vegetation: "list[tuple[float, float]] | None" = None,
    osm_green_features: "list[dict] | None" = None,
    satellite_bg: "bool | None" = None,
) -> None:
    """Draw an OSM-footprint mini-map centered on the seed, with matched
    buildings colored to match their skyline segment.

    Each detected segment gets:
    - its **footprint polygon** filled in the segment color (so the user can
      visually verify "image-box N corresponds to the building at footprint N
      on the map");
    - a dashed bearing line from the seed toward that footprint;
    - a numbered label at the footprint centroid matching the legend.

    ``buildings_by_id`` maps the deterministic feature_id (e.g. "b0142") to
    the matched BuildingRecord — used to draw the polygon in the segment
    colour, since the raw OSM feature list uses a different id encoding.
    """
    mlat = 110_540.0
    mlon = 111_320.0 * math.cos(math.radians(seed_lat))
    dlat = radius_m / mlat
    dlon = radius_m / mlon

    # Map deterministic feature_id → badge_index. Prefer the seed-level
    # index (stable across views) when present so the same building gets
    # the same badge number/colour in every minimap. Fall back to the
    # per-view list position when ``seed_index`` is absent (older callers).
    matched_ids = {
        seg["matched_projection"]["feature_id"]: int(seg.get("seed_index", i + 1)) - 1
        for i, seg in enumerate(matched_segments)
        if seg.get("matched_projection")
    }

    # ── Pass 1: all OSM footprints in light grey (context) ──────────────────
    # PatchCollection is ~50× faster than per-polygon add_patch when drawing
    # 3000+ context polygons per view; matplotlib batches the path drawing.
    from matplotlib.collections import PatchCollection  # noqa: PLC0415
    _grey_polys: list[mpatches.Polygon] = []
    for feat in osm_data.get("buildings", {}).get("features") or []:
        for ring in _feature_rings(feat):
            xs = [p[0] for p in ring]
            ys = [p[1] for p in ring]
            _grey_polys.append(mpatches.Polygon(
                np.column_stack([xs, ys]), closed=True))
    if _grey_polys:
        ax.add_collection(PatchCollection(
            _grey_polys,
            facecolor=(0.7, 0.7, 0.7, 0.25),
            edgecolor=(0.4, 0.4, 0.4, 0.4),
            linewidths=0.3,
            zorder=1,
            match_original=False,
        ))

    # ── Pass 1b: F-SKY8 satellite (ms_buildings) polygons as dashed grey ────
    # The grey-context layer above only renders the raw OSM features. After
    # F-SKY8 the matcher works against a *merged* record set; without a
    # second context pass the satellite-only polygons (~half the inventory on
    # Cartagena) are invisible until they get matched. Drawing them with a
    # dashed edge keeps the OSM/MS distinction visible at a glance.
    if buildings_by_id is not None:
        _sat_polys: list[mpatches.Polygon] = []
        for fid, b in buildings_by_id.items():
            if not str(fid).startswith("ms_"):
                continue
            geom = getattr(b, "geometry", None)
            if geom is None:
                continue
            try:
                xs_b, ys_b = geom.exterior.xy
            except Exception:
                continue
            _sat_polys.append(mpatches.Polygon(
                np.column_stack([list(xs_b), list(ys_b)]), closed=True))
        if _sat_polys:
            ax.add_collection(PatchCollection(
                _sat_polys,
                facecolor=(0.85, 0.78, 0.55, 0.20),
                edgecolor=(0.55, 0.40, 0.10, 0.55),
                linewidths=0.4,
                linestyle="--",
                zorder=1.2,
                match_original=False,
            ))

    # ── Pass 2: matched footprints in segment colours (drawn on top) ───────
    # We use buildings_by_id which keys polygons by the same deterministic id
    # that the segment matches reference; the raw OSM feature list uses a
    # different id encoding (raw index pre-sort) and cannot be matched here.
    matched_footprint_centroids: dict[int, tuple[float, float]] = {}
    if buildings_by_id is not None:
        for fid, seg_idx in matched_ids.items():
            b = buildings_by_id.get(fid)
            if b is None:
                continue
            geom = getattr(b, "geometry", None)
            if geom is None:
                continue
            try:
                xs, ys = geom.exterior.xy
            except Exception:
                continue
            color = tuple(
                c / 255.0 for c in _SEGMENT_PALETTE[seg_idx % len(_SEGMENT_PALETTE)])
            # Satellite-sourced matches get a dashed edge so the user can
            # spot at a glance which matches are F-SKY8 fill-ins (OSM gap
            # candidates) vs OSM-anchored matches. Fill stays the segment
            # colour either way.
            is_sat = str(fid).startswith("ms_")
            poly = mpatches.Polygon(
                np.column_stack([list(xs), list(ys)]),
                closed=True,
                facecolor=(*color, 0.85),
                edgecolor=(*color, 1.0),
                linewidth=1.4,
                linestyle="--" if is_sat else "-",
                zorder=3,
            )
            ax.add_patch(poly)
            matched_footprint_centroids[seg_idx] = (
                float(b.centroid_lon), float(b.centroid_lat))

    # F-SKY6 Part 2: "considered but lost" candidates. The matcher's
    # match_diagnostics field records the top-3 candidates per segment.
    # Drawing the ones that didn't win as faint orange dots makes it
    # immediately visible whether OSM has buildings in an unmatched
    # x-band (orange dot present → matcher rejection issue) versus an
    # OSM data gap (no orange dots → no OSM building to match).
    if buildings_by_id is not None:
        considered_fids: set[str] = set()
        for seg in matched_segments:
            for d in seg.get("match_diagnostics", []):
                fid = d.get("feature_id")
                if fid and fid not in matched_ids:
                    considered_fids.add(fid)
        if considered_fids:
            xs_c: list[float] = []
            ys_c: list[float] = []
            for fid in considered_fids:
                b = buildings_by_id.get(fid)
                if b is None:
                    continue
                xs_c.append(float(b.centroid_lon))
                ys_c.append(float(b.centroid_lat))
            if xs_c:
                ax.scatter(
                    xs_c, ys_c,
                    s=14, c="orange", marker="o",
                    edgecolors=(0.7, 0.4, 0.0), linewidth=0.5,
                    alpha=0.7, zorder=2.5,
                    label="considered but lost",
                )

    # ── Per-segment bearing lines ─────────────────────────────────────────
    # Draw a dashed colored line from the seed toward each detected building
    # segment so the image boxes can be cross-referenced with the map.
    # Bearing source preference:
    #   1. seg["true_bearing_deg"]  (set by pano matcher — already geographic)
    #   2. heading + angle_off via inverse pinhole on peak_x (per-view path)
    # The fallback path uses the per-view image's intrinsic FOV; pano callers
    # avoid it by supplying true_bearing_deg directly.
    half_fov_tan = (
        math.tan(math.radians(fov_deg / 2.0))
        if 0.0 < fov_deg < 180.0 else None
    )
    line_len = radius_m * 0.70
    for i, seg in enumerate(matched_segments):
        # Same seed-level badge logic as the image overlay so the minimap
        # badge number and colour match the box badge for the same OSM
        # building across every view of this seed.
        badge_n = int(seg.get("seed_index", i + 1))
        color_rgb = _SEGMENT_PALETTE[(badge_n - 1) % len(_SEGMENT_PALETTE)]
        color = tuple(cv / 255.0 for cv in color_rgb)

        # Pano matcher supplies a precomputed bearing; respect it if present.
        explicit_bearing = seg.get("true_bearing_deg")
        if explicit_bearing is not None:
            seg_bearing = float(explicit_bearing) % 360.0
        elif half_fov_tan is not None:
            peak_x = seg.get("peak_x", seg.get("mid_x", image_width // 2))
            norm_x = (peak_x - image_width / 2.0) / (image_width / 2.0)
            angle_off = math.degrees(math.atan(norm_x * half_fov_tan))
            seg_bearing = (heading_deg + angle_off) % 360.0
        else:
            # No bearing source — skip the ray; still draw the centroid label.
            seg_bearing = None

        if seg_bearing is not None:
            seg_theta = math.radians(seg_bearing)
            ldx = line_len * math.sin(seg_theta) / mlon
            ldy = line_len * math.cos(seg_theta) / mlat
            ax.plot(
                [seed_lon, seed_lon + ldx],
                [seed_lat, seed_lat + ldy],
                color=color,
                linewidth=1.2,
                alpha=0.7,
                linestyle="--",
                zorder=2,
            )
        else:
            ldx = ldy = 0.0
        centroid = matched_footprint_centroids.get(badge_n - 1)
        if centroid is not None:
            lx, ly = centroid
        else:
            lx = seed_lon + ldx * 0.65
            ly = seed_lat + ldy * 0.65
        ax.annotate(
            str(badge_n),
            xy=(lx, ly),
            color="black",
            fontsize=9,
            fontweight="bold",
            ha="center",
            va="center",
            bbox=dict(boxstyle="circle,pad=0.15", facecolor=color,
                      edgecolor="black", linewidth=0.6, alpha=0.95),
            zorder=6,
        )

    # F-SKY13: OSM-coastline + 1 km consideration window overlay. OSM is the
    # primary coastline ground truth (the satellite HSV detector in
    # coastline_registration.py is unreliable — see feedback memory). Drawn
    # underneath the camera marker but on top of the building polygons, so
    # the user can verify pano↔OSM coastline agreement visually.
    if _F_SKY13_ENABLED:
        _draw_osm_coastline_overlay(
            ax,
            seed_lon=seed_lon,
            seed_lat=seed_lat,
            mlon=mlon,
            mlat=mlat,
            osm_data=osm_data,
            radius_m=_F_SKY13_RADIUS_M,
            satellite_bg=(
                _F_SKY13_SAT_BG_ENABLED if satellite_bg is None
                else bool(satellite_bg)),
            pano_projected_coastline=pano_projected_coastline,
            pano_projected_vegetation=pano_projected_vegetation,
            pano_osm_iou=pano_osm_iou,
            pano_osm_n_keypoints=pano_osm_n_keypoints,
        )

    # Camera marker drawn now; the FOV cone is drawn AFTER auto-zoom so
    # we can scale it to the actual axis span (it previously used the
    # default radius_m=1500 which overshot tight auto-zoomed panels by
    # 5×, dominating the visual).
    ax.scatter([seed_lon], [seed_lat], c="red", s=80, marker="*", zorder=5)

    # Auto-zoom: prefer the bbox of matched-footprint polygons + the seed
    # position, expanded by a 100 m margin. Falls back to the fixed ±radius_m
    # window when no footprints are matched. Previously the fixed 1500 m
    # window left most of the minimap empty when matches clustered tightly,
    # making the badge numbers and polygons hard to read.
    bbox_lons: list[float] = [seed_lon]
    bbox_lats: list[float] = [seed_lat]
    if buildings_by_id is not None:
        for fid in matched_ids.keys():
            b = buildings_by_id.get(fid)
            if b is None:
                continue
            geom = getattr(b, "geometry", None)
            if geom is None:
                continue
            try:
                xs, ys = geom.exterior.xy
                bbox_lons.extend(float(x) for x in xs)
                bbox_lats.extend(float(y) for y in ys)
            except Exception:
                continue

    use_auto_zoom = len(bbox_lons) > 1  # more than just the seed
    if use_auto_zoom:
        margin_lon = 100.0 / mlon
        margin_lat = 100.0 / mlat
        x0 = min(bbox_lons) - margin_lon
        x1 = max(bbox_lons) + margin_lon
        y0 = min(bbox_lats) - margin_lat
        y1 = max(bbox_lats) + margin_lat
        # Enforce a minimum window (~300 m) so a single tight cluster doesn't
        # zoom in to a degenerate few-metre frame.
        min_span_lon = 300.0 / mlon
        min_span_lat = 300.0 / mlat
        if x1 - x0 < min_span_lon:
            cx = (x0 + x1) * 0.5
            x0, x1 = cx - min_span_lon * 0.5, cx + min_span_lon * 0.5
        if y1 - y0 < min_span_lat:
            cy = (y0 + y1) * 0.5
            y0, y1 = cy - min_span_lat * 0.5, cy + min_span_lat * 0.5
        # Equal aspect: enlarge the smaller dimension to match the larger.
        # Avoids matplotlib stretching the figure.
        dx = x1 - x0
        dy = y1 - y0
        # Both in metres for fair comparison
        dx_m = dx * mlon
        dy_m = dy * mlat
        if dx_m > dy_m:
            extra_lat = (dx_m - dy_m) / mlat * 0.5
            y0 -= extra_lat
            y1 += extra_lat
        else:
            extra_lon = (dy_m - dx_m) / mlon * 0.5
            x0 -= extra_lon
            x1 += extra_lon
        ax.set_xlim(x0, x1)
        ax.set_ylim(y0, y1)
        radius_label = (
            f"~{int(max((x1-x0)*mlon, (y1-y0)*mlat) * 0.5)} m radius (auto)"
        )
    else:
        ax.set_xlim(seed_lon - dlon, seed_lon + dlon)
        ax.set_ylim(seed_lat - dlat, seed_lat + dlat)
        radius_label = f"radius {int(radius_m)}m"

    ax.set_aspect("equal", adjustable="box")
    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")
    ax.grid(alpha=0.2)
    ax.set_title(f"Footprints in view ({radius_label})")

    # Draw FOV cone AFTER axes are set so we can size it to the actual
    # axis span (max half-span in metres × 0.7 so the cone reaches but
    # doesn't escape the panel). Pano callers pass fov_deg=360 to mean
    # "no single direction" — skip the cone there. Also skip when the
    # view has fewer than 3 matched segments: the cone implies a
    # confident orientation, but with 0–2 matches the heading is
    # essentially unverified by the registration, and a prominent cone
    # is misleading — just the camera star is honest.
    if 0.0 < fov_deg < 180.0 and len(matched_segments) >= 3:
        xlim = ax.get_xlim()
        ylim = ax.get_ylim()
        half_span_m = max(
            (xlim[1] - xlim[0]) * mlon,
            (ylim[1] - ylim[0]) * mlat,
        ) * 0.5
        cone_len = max(50.0, half_span_m * 0.7)
        half = math.radians(fov_deg * 0.5)
        theta = math.radians(heading_deg)
        for sign in (-1.0, 1.0):
            angle = theta + sign * half
            dx = cone_len * math.sin(angle) / mlon
            dy = cone_len * math.cos(angle) / mlat
            ax.plot(
                [seed_lon, seed_lon + dx], [seed_lat, seed_lat + dy],
                "r-", linewidth=0.8, alpha=0.6,
            )
        arrow_dx = cone_len * 0.5 * math.sin(theta) / mlon
        arrow_dy = cone_len * 0.5 * math.cos(theta) / mlat
        # Arrow head + body sized off the actual axis span. The default
        # `width` in matplotlib's ax.arrow is 0.001 (in DATA UNITS) which
        # at this lat/lon is ~100 m — produced a huge red bar across the
        # minimap. We size everything relative to the actual axis span.
        head_w = (xlim[1] - xlim[0]) * 0.025
        head_h = (ylim[1] - ylim[0]) * 0.03
        body_w = (xlim[1] - xlim[0]) * 0.005  # thin body, ~half the head
        ax.arrow(
            seed_lon, seed_lat, arrow_dx, arrow_dy,
            width=body_w,
            head_width=head_w, head_length=head_h,
            length_includes_head=True, color="red", alpha=0.7, zorder=4,
        )
        # Restore the axis limits — arrow drawing can expand them.
        ax.set_xlim(xlim)
        ax.set_ylim(ylim)

def _render_stitched_pano_page(
    pdf,
    pr: "StitchedPanoResult",
    osm_data: dict,
    buildings_by_id: dict | None = None,
    seed_views: list[SeedViewRegistration] | None = None,
) -> None:
    """Render one comparison page per seed: stitched 360° pano with segment
    overlays on top, summary stats below (per-view vs pano numbers), and a
    minimap with all pano-matched footprints colored.

    The pano image is full-width with all 12 stitched crops laid horizontally.
    Each detected segment gets a colored bounding box + numbered badge so the
    user can pair segments to footprints on the minimap (same convention as
    the per-view pages).
    """
    fig = plt.figure(figsize=(14, 8.5))
    fig.suptitle(
        f"Stitched 360° pano — {pr.seed_name}  "
        f"anchor_offset={pr.anchor_offset_deg:+.1f}°  "
        f"segments={pr.n_segments}  matched={pr.n_matched}  "
        f"buildings_in_view={pr.n_buildings_in_view}",
        fontsize=12,
    )

    # Top half: pano image with segment overlays. Crop vertically to building
    # band if available so we don't waste space on water/sky.
    pano_img = pr.pano_image
    band = pr.band_y
    if band is not None:
        y0, y1 = band
        H = pano_img.shape[0]
        y0 = max(0, min(H - 1, int(y0)))
        y1 = max(y0 + 30, min(H, int(y1) + 1))
        if y1 - y0 > 60:
            pano_img = pano_img[y0:y1, :, ...]
    # Burn segments onto a copy so we don't mutate the cached array.
    overlay = pano_img.copy()
    for i, seg in enumerate(pr.matched_segments):
        if seg.get("matched_projection") is None:
            continue
        color = _SEGMENT_PALETTE[i % len(_SEGMENT_PALETTE)]
        xL, xR = int(seg["x_left"]), int(seg["x_right"])
        top_y, base_y = int(seg["top_y"]), int(seg["base_y"])
        if band is not None:
            top_y -= band[0]
            base_y -= band[0]
        top_y = max(0, top_y)
        base_y = min(overlay.shape[0] - 1, base_y)
        if base_y <= top_y or xR <= xL:
            continue
        cv2.rectangle(overlay, (xL, top_y), (xR, base_y), color, 2)
        cv2.circle(overlay, (xL + 11, max(15, top_y + 7)), 11, color, -1)
        cv2.circle(overlay, (xL + 11, max(15, top_y + 7)), 11, (0, 0, 0), 1)
        cv2.putText(
            overlay, str(i + 1),
            (xL + (4 if i + 1 < 10 else 1), max(20, top_y + 12)),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA,
        )

    ax_pano = fig.add_axes([0.03, 0.50, 0.94, 0.38])
    ax_pano.imshow(overlay)
    # Heading-tick overlay along the bottom: every 30° draw a vertical
    # marker + compass label so we can verify visually that the stitching's
    # column-to-heading map matches the depicted scene. (If e.g. column
    # marked "180°" doesn't show buildings the user expects in the south
    # direction, that pinpoints a heading-mapping bug.)
    headings_per_col = getattr(pr, "headings_per_col", None)
    if headings_per_col is not None and headings_per_col.size > 0:
        h_img = overlay.shape[0]
        labeled_cardinals = [
            (0.0, "N"), (45.0, "NE"), (90.0, "E"), (135.0, "SE"),
            (180.0, "S"), (225.0, "SW"), (270.0, "W"), (315.0, "NW"),
        ]
        for hdg, name in labeled_cardinals:
            # Find the column closest to this compass heading
            diffs = np.abs(((headings_per_col - hdg + 180.0) % 360.0) - 180.0)
            if diffs.min() > 8.0:
                continue
            col = int(np.argmin(diffs))
            ax_pano.axvline(col, color="yellow", linewidth=0.5, alpha=0.7)
            ax_pano.text(
                col, h_img * 0.97, f"{name} {hdg:.0f}°",
                color="black", fontsize=8, ha="center", va="bottom",
                bbox=dict(boxstyle="round,pad=0.15", fc="yellow",
                          ec="black", alpha=0.85, lw=0.5),
            )
    ax_pano.axis("off")
    ax_pano.set_title(
        "Stitched pano — mask-stitched per-view masks (yellow ticks = compass headings)",
        fontsize=10,
    )

    # Bottom-left: pano-matched-footprint minimap.
    ax_map = fig.add_axes([0.04, 0.06, 0.45, 0.36])
    _draw_view_minimap(
        ax_map,
        pr.seed_lat,
        pr.seed_lon,
        # Use 0° heading and 360° FOV stand-in: we just want all matched
        # footprints visible. The minimap's per-segment bearing rays will
        # still go to the right places because they're computed from the
        # actual matched-footprint centroids when available.
        heading_deg=0.0,
        fov_deg=360.0,
        osm_data=osm_data,
        matched_segments=pr.matched_segments,
        buildings_by_id=buildings_by_id,
        image_width=pr.pano_image.shape[1],
    )

    # Bottom-right: side-by-side comparison vs per-view aggregate.
    ax_cmp = fig.add_axes([0.52, 0.06, 0.45, 0.36])
    ax_cmp.axis("off")
    per_view_for_seed = (
        [sv for sv in (seed_views or []) if sv.seed_name == pr.seed_name]
    )
    n_view_segments = sum(len(sv.matched_segments) for sv in per_view_for_seed)
    n_view_matched = sum(
        1 for sv in per_view_for_seed
        for seg in sv.matched_segments
        if seg.get("matched_projection")
    )
    matched_pano_ids = {
        seg["matched_projection"]["feature_id"]
        for seg in pr.matched_segments
        if seg.get("matched_projection")
    }
    matched_view_ids = {
        seg["matched_projection"]["feature_id"]
        for sv in per_view_for_seed
        for seg in sv.matched_segments
        if seg.get("matched_projection")
    }
    overlap_ids = matched_pano_ids & matched_view_ids
    pano_only = matched_pano_ids - matched_view_ids
    view_only = matched_view_ids - matched_pano_ids
    cmp_lines = [
        f"PANO vs PER-VIEW (this seed only)",
        "",
        f"{'metric':<28}{'pano':>10}{'per-view':>12}",
        f"{'-'*52}",
        f"{'segments detected':<28}{pr.n_segments:>10d}{n_view_segments:>12d}",
        f"{'segments with OSM match':<28}{pr.n_matched:>10d}{n_view_matched:>12d}",
        f"{'distinct buildings matched':<28}{len(matched_pano_ids):>10d}{len(matched_view_ids):>12d}",
        "",
        f"  overlap (both methods agree):  {len(overlap_ids):>4d}",
        f"  pano only:                     {len(pano_only):>4d}",
        f"  per-view only:                 {len(view_only):>4d}",
    ]
    ax_cmp.text(
        0.02, 0.98, "\n".join(cmp_lines),
        family="monospace", fontsize=9, va="top", transform=ax_cmp.transAxes,
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)

def _count_seg_flags(matched_segments: list[dict]) -> tuple[int, int, int]:
    """Tally (F=outside_FOV, B=not_closest, P=implausible_vs_proxy) flags
    across a view's matched segments. Used by the per-view PDF header.
    Extracted so the rendering path doesn't carry an inline 20-line
    loop just to compute three counts.
    """
    n_fov_fail = n_not_closest = n_implausible = 0
    for seg in matched_segments:
        if seg.get("matched_projection") is None:
            continue
        if not bool(seg.get("bearing_in_fov", True)):
            n_fov_fail += 1
        if not bool(seg.get("is_closest_in_bin", True)):
            n_not_closest += 1
        proxy_h = float(seg.get("height_proxy_m", 0.0))
        pred_h = seg.get("predicted_height_m", float("nan"))
        if (
            proxy_h >= 3.0
            and isinstance(pred_h, float)
            and np.isfinite(pred_h)
            and (pred_h > proxy_h * 5.0 or pred_h < proxy_h * 0.2)
        ):
            n_implausible += 1
    return n_fov_fail, n_not_closest, n_implausible

def _render_seed_view_page(
    pdf,
    sv: SeedViewRegistration,
    osm_data: dict,
    buildings_by_id: dict | None = None,
) -> None:
    """Render one per-view PDF page.

    F-SKY7 layout (replaces F-SKY4 cyan-overlay + bottom legend table):
      - Top-left: Street View image with skyline-segment overlays.
      - Bottom-left: SegFormer building mask in its own panel (faint
        photo background + cyan mask) — direct side-by-side comparison
        with the segment panel above lets you spot "mask present but
        no segment" failures at a glance.
      - Right: minimap (full height) — taller than before since the
        bottom legend table was removed.

    Flag counts (F / B / P) that used to live in the legend table now
    appear in the figure title.
    """
    fig = plt.figure(figsize=(14, 8.5))

    # ── Title: pose + per-flag counts ─────────────────────────────────────
    aerial_tag = "  [AERIAL — height skipped]" if sv.is_aerial else ""
    if getattr(sv, "is_negative", False):
        aerial_tag += "  [NEGATIVE EXAMPLE — estimates excluded]"
    effective_heading = (sv.heading + sv.best_offset) % 360.0
    n_fov_fail, n_not_closest, n_implausible = _count_seg_flags(
        sv.matched_segments)
    n_total = len(sv.matched_segments)
    flags_str = (
        f"  flags={n_fov_fail}F/{n_not_closest}B/{n_implausible}P of {n_total}"
        if n_total else ""
    )
    # View-level cross-checks (set by _seed_multiview_registration):
    #   bearing_delta_deg per match → median + MAD across the view
    #   multi_building_candidate per match → count of wide/cluster segments
    deltas = [
        float(s["bearing_delta_deg"]) for s in sv.matched_segments
        if s.get("matched_projection") is not None
        and s.get("bearing_delta_deg") is not None
        and np.isfinite(s.get("bearing_delta_deg", float("nan")))
    ]
    heading_str = ""
    if len(deltas) >= 3:
        med = float(np.median(deltas))
        mad = float(np.median([abs(d - med) for d in deltas]))
        heading_str = f"  Δh̃={med:+.1f}°(MAD={mad:.0f}°)"
    n_wide = sum(
        1 for s in sv.matched_segments
        if s.get("matched_projection") is not None
        and s.get("multi_building_candidate")
    )
    wide_str = f"  multi-bldg={n_wide}" if n_wide else ""
    n_corrected = sum(
        1 for s in sv.matched_segments
        if s.get("match_corrected")
    )
    corr_str = f"  ✎{n_corrected}" if n_corrected else ""
    # F-CLEAN5: surface the F-SKY10 cross-view score in the per-view header.
    # Each matched segment's `match_diagnostics[0]` carries the per-candidate
    # scoring breakdown including `cv` when the cross-view scorer ran.
    # We aggregate as mean+min across matched segments — the mean gives a
    # whole-view "how well does the cross-view signal AGREE with this match
    # set" indicator; the min surfaces single suspect matches without
    # forcing the user to dig into per-segment metadata.
    cv_vals = []
    for _s in sv.matched_segments:
        if _s.get("matched_projection") is None:
            continue
        _d = (_s.get("match_diagnostics") or [{}])[0]
        if "cv" in _d:
            try:
                cv_vals.append(float(_d["cv"]))
            except (TypeError, ValueError):
                continue
    cv_str = (
        f"  cv̄={float(np.mean(cv_vals)):.2f}/min={float(np.min(cv_vals)):.2f}"
        if cv_vals else ""
    )
    fig.suptitle(
        f"Seed view — {sv.seed_name}  "
        f"effective_heading={effective_heading:.1f}°  "
        f"(api={sv.heading:.0f}° + offset={sv.best_offset:+.1f}°)  "
        f"reg_score={sv.registration_score:.2f}px  iou={sv.iou:.2f}  "
        f"segments={n_total}  "
        f"estimates={sv.estimates_count}"
        f"{flags_str}{heading_str}{wide_str}{corr_str}{cv_str}"
        f"{aerial_tag}",
        fontsize=11,
    )
    # Decoder for the F/B/P + new diagnostics shorthand in the title.
    if n_total and (
        n_fov_fail or n_not_closest or n_implausible
        or len(deltas) >= 3 or n_wide or n_corrected
    ):
        fig.text(
            0.5, 0.955,
            "F = matched bearing outside FOV   "
            "B = matched building not closest in column bin   "
            "P = predicted height implausible vs sqrt-area proxy   "
            "Δh̃ = median(true-bearing − effective-heading) across matches  "
            "(non-zero ⇒ heading bias)   "
            "multi-bldg = wide segment that spans multiple OSM polygons   "
            "✎N = N matches corrected by post-hoc rescue   "
            "cv̄/min = mean/min F-SKY10 cross-view score across matched segments",
            ha="center", va="top", fontsize=7, color="0.35",
        )

    # ── Crop the displayed image to the building band ────────────────────
    # Views looking across water otherwise devote 60-70% of the panel to
    # empty sea/sky. Segment x_left/x_right/top_y/base_y stay in original-
    # image coords so overlays still register against the crop.
    display_img = sv.image
    crop_y0 = crop_y1 = None
    if sv.band_y is not None:
        y0, y1 = sv.band_y
        h_img = sv.image.shape[0]
        y0 = max(0, min(h_img - 1, int(y0)))
        y1 = max(y0 + 30, min(h_img, int(y1) + 1))
        if y1 - y0 > 60:
            display_img = sv.image[y0:y1, :, ...]
            crop_y0, crop_y1 = y0, y1

    # ── Top-left: image + skyline segment overlays ────────────────────────
    ax_img = fig.add_axes([0.03, 0.56, 0.55, 0.34])
    ax_img.imshow(display_img)
    ax_img.axis("off")
    img_title = "Street View + skyline segments"
    if crop_y0 is not None:
        img_title += f"  (cropped to band y={crop_y0}..{crop_y1})"
    ax_img.set_title(img_title, fontsize=10)

    # ── Mid-left: SegFormer building mask on its own ─────────────────────
    ax_mask = fig.add_axes([0.03, 0.21, 0.55, 0.32])
    if sv.building_mask is not None:
        mask_disp = sv.building_mask
        if crop_y0 is not None:
            mask_disp = mask_disp[crop_y0:crop_y1, :]
        # Faint greyscale photo as background context so you can see what
        # the mask is covering, then the mask as a cyan layer on top.
        ax_mask.imshow(display_img, alpha=0.35)
        rgba = np.zeros((*mask_disp.shape, 4), dtype=np.float32)
        rgba[..., 1] = 0.85
        rgba[..., 2] = 1.0
        rgba[..., 3] = np.where(mask_disp, 0.65, 0.0)
        ax_mask.imshow(rgba)
        ax_mask.set_title(
            "SegFormer building mask (cyan = building class)",
            fontsize=10,
        )
    else:
        ax_mask.text(
            0.5, 0.5, "(no SegFormer mask available)",
            ha="center", va="center", transform=ax_mask.transAxes,
            fontsize=10, color="grey",
        )
        ax_mask.set_title("SegFormer building mask", fontsize=10)
    ax_mask.axis("off")

    # ── Bottom-left: column-coverage strip with peak markers ──────────────
    # The 1-D signal `detect_building_silhouettes` works on. Visible peaks
    # (kept = match; unmatched = no match) plotted as vertical bars so the
    # user can tell apart "silhouette detector missed the tower" (no bar
    # above a tall mask column) from "matcher rejected the peak" (bar
    # present but unmatched). Critical for diagnosing seed_5-style cases.
    ax_strip = fig.add_axes([0.03, 0.05, 0.55, 0.13])
    if sv.building_mask is not None:
        coverage = sv.building_mask.astype(np.float32).mean(axis=0)
        xs = np.arange(coverage.size)
        ax_strip.fill_between(xs, 0.0, coverage,
                              color=(0.20, 0.55, 0.75, 0.45), linewidth=0)
        ax_strip.plot(xs, coverage, color=(0.10, 0.30, 0.55, 0.9),
                      linewidth=0.7)
        for seg in sv.matched_segments:
            try:
                peak_x = int(seg.get(
                    "peak_x",
                    0.5 * (float(seg.get("x_left", 0))
                           + float(seg.get("x_right", 0))),
                ))
            except Exception:
                continue
            if peak_x < 0 or peak_x >= coverage.size:
                continue
            matched = seg.get("matched_projection") is not None
            color = (0.0, 0.65, 0.0, 0.95) if matched else (
                0.85, 0.40, 0.0, 0.95)
            ax_strip.axvline(peak_x, color=color, linewidth=1.0)
        ax_strip.set_xlim(0, max(1, coverage.size - 1))
        ax_strip.set_ylim(0, 1.0)
        ax_strip.set_xlabel("column x (px)", fontsize=8)
        ax_strip.set_ylabel("mask coverage", fontsize=8)
        ax_strip.tick_params(labelsize=7)
        ax_strip.grid(alpha=0.20)
        ax_strip.set_title(
            "Per-column building mask coverage  "
            "(green = matched peak, orange = unmatched peak)",
            fontsize=9,
        )
    else:
        ax_strip.axis("off")

    # ── Right: minimap, full page height ─────────────────────────────────
    ax_map = fig.add_axes([0.62, 0.05, 0.35, 0.85])
    # Use heading + best_offset for the minimap arrow because building
    # projections were computed with this effective heading during
    # registration.
    _draw_view_minimap(
        ax_map,
        sv.seed_lat,
        sv.seed_lon,
        sv.heading + sv.best_offset,
        sv.fov,
        osm_data,
        sv.matched_segments,
        buildings_by_id=buildings_by_id,
        image_width=sv.image.shape[1],
        pano_osm_iou=sv.pano_osm_iou,
        pano_osm_n_keypoints=sv.pano_osm_n_keypoints,
        pano_projected_coastline=sv.pano_projected_coastline,
    )

    pdf.savefig(fig, bbox_inches="tight")
    plt.close(fig)

def _load_known_heights(
    region_name: str,
    buildings: list[BuildingRecord],
    match_radius_m: float = 60.0,
) -> list[dict]:
    """Load surveyed building heights from sites/<region>.json.

    Reads the ``known_heights_m`` dict (keyed by building name, values with
    lat/lon/height_m/floors) and matches each entry to the closest
    BuildingRecord within ``match_radius_m``.  Used for CTBUH-style
    ground-truth validation separate from crowd-sourced OSM tags.

    Returns a list of dicts with keys:
      name, ctbuh_m, floors, lat, lon, matched_id, matched_dist_m.
    """
    cfg = Path(__file__).resolve().parent / \
        "sites" / f"{region_name.lower()}.json"
    if not cfg.exists():
        return []
    try:
        data = json.loads(cfg.read_text(encoding="utf-8"))
    except Exception:
        return []
    raw = data.get("known_heights_m")
    if not isinstance(raw, dict):
        return []

    out: list[dict] = []
    for bname, info in raw.items():
        if bname.startswith("_"):
            continue
        try:
            klat = float(info["lat"])
            klon = float(info["lon"])
            ctbuh_m = float(info["height_m"])
            floors = int(info.get("floors", 0))
        except Exception:
            continue
        best_id: str | None = None
        best_dist = float("inf")
        for b in buildings:
            d = _distance_m(klat, klon, b.centroid_lat, b.centroid_lon)
            if d < best_dist:
                best_dist = d
                best_id = b.feature_id
        out.append({
            "name": bname,
            "ctbuh_m": ctbuh_m,
            "floors": floors,
            "lat": klat,
            "lon": klon,
            "matched_id": best_id if best_dist <= match_radius_m else None,
            "matched_dist_m": best_dist,
        })
    return out

def _render_pdf(
    out_pdf: Path,
    bbox: RegionBBox,
    osm_source: str,
    osm_data: dict,
    buildings_count: int,
    high_rise_count: int,
    screened: list[dict],
    seed_views: list[SeedViewRegistration],
    building_heights: list[dict],
    building_records: list[BuildingRecord],
    known_heights: list[dict] | None = None,
    pano_results: list["StitchedPanoResult"] | None = None,
    pano_only: bool = False,
) -> None:
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with PdfPages(out_pdf) as pdf:
        # Page 1: summary
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle(
            f"Skyline Region Report — {bbox.name}", fontsize=18, fontweight="bold")
        ax = fig.add_subplot(111)
        ax.axis("off")
        n_aerial_views = sum(1 for sv in seed_views if sv.is_aerial)
        lines = [
            f"Region: {bbox.name}",
            f"BBox: N {bbox.north:.5f}  S {bbox.south:.5f}  E {bbox.east:.5f}  W {bbox.west:.5f}",
            f"OSM source: {osm_source}",
            f"Buildings in dataset: {buildings_count}",
            f"High-rise candidates: {high_rise_count}",
            f"StreetView-screened points: {len(screened)}",
            f"Seed multiview registrations: {len(seed_views)}  (aerial/skipped: {n_aerial_views})",
            f"Extracted buildings from seed registrations: {len(building_heights)}",
            "",
            "Coverage screening rule:",
            "- good: skyline score >= 0.30",
            "- medium: 0.15 <= score < 0.30",
            "- weak: score < 0.15",
            "- rejected: failed sky-fraction or contour-range gate",
            "- aerial: elevated camera detected, height estimation skipped",
        ]
        top = [r for r in screened if r["status"] == "OK"][:8]
        lines.append("")
        lines.append("Top screened viewpoints:")
        for row in top:
            p: SkylinePoint = row["point"]
            lines.append(
                f"- {p.name} ({p.source})  lat={p.lat:.6f}, lon={p.lon:.6f}, heading={p.heading:.1f}  "
                f"score={row['screen_score']:.2f}  coverage={row['coverage']}"
            )
        ax.text(0.03, 0.96, "\n".join(lines), va="top",
                ha="left", family="monospace", fontsize=10)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Page 2: map screen
        fig, ax = plt.subplots(figsize=(11, 8.5))
        _draw_location_map(ax, bbox, screened, osm_data)
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Point screenshots — consolidated into one (or a few) montage pages.
        # Previously rendered one full-page screenshot per screened location,
        # which inflated the PDF and added ~2 s/page of matplotlib draw time.
        # The same images appear in detail on the seed-view registration
        # pages later, so the screenshots are only useful as a quick at-a-
        # glance "which locations got chosen" overview.
        ok_rows = [r for r in screened
                   if r["status"] == "OK" and r["image"] is not None]
        if ok_rows:
            tiles_per_page = 6  # 2 rows × 3 cols
            for page_start in range(0, len(ok_rows), tiles_per_page):
                page_rows = ok_rows[page_start: page_start + tiles_per_page]
                fig = plt.figure(figsize=(11, 8.5))
                fig.suptitle(
                    f"Screened locations  "
                    f"({page_start + 1}–{page_start + len(page_rows)} of {len(ok_rows)})",
                    fontsize=14,
                )
                for tile_idx, row in enumerate(page_rows):
                    p: SkylinePoint = row["point"]
                    ax = fig.add_subplot(2, 3, tile_idx + 1)
                    ax.imshow(row["image"])
                    ax.axis("off")
                    ax.set_title(
                        f"{p.name}\n"
                        f"{p.source}  cov={row['coverage']}  "
                        f"score={row['screen_score']:.2f}\n"
                        f"hdg={p.heading:.0f}°  lat={p.lat:.4f}  lon={p.lon:.4f}",
                        fontsize=7,
                    )
                fig.tight_layout(rect=[0, 0, 1, 0.95])
                pdf.savefig(fig, bbox_inches="tight")
                plt.close(fig)

        # Seed registration pages: image with segments + matched-footprint mini-map
        # buildings_by_id lets the mini-map draw the actual matched footprint
        # polygons in the segment colours (rather than just bearing rays).
        # Skipped entirely under `pano_only_pdf` — these are the dominant
        # contributor to PDF file size (~26 pages × ~1 MB each on Cartagena),
        # and the per-seed stitched pano page below carries the same info
        # at lower fidelity for compact-orientation reviews.
        buildings_by_id = {b.feature_id: b for b in building_records}
        if not pano_only:
            for sv in seed_views:
                _render_seed_view_page(
                    pdf, sv, osm_data, buildings_by_id=buildings_by_id)

        # Stitched-pano comparison pages — one per seed that produced a
        # stitched result. Lets you compare the 360° pano-level segmentation
        # against the per-view results on the preceding pages. Skip seeds
        # whose stitched pano detected no segments and matched nothing:
        # the page is pure noise (an empty pano image + zero-rows table)
        # and just inflates the PDF page count.
        if pano_results:
            for pr in pano_results:
                if pr.n_segments == 0 and pr.n_matched == 0:
                    continue
                _render_stitched_pano_page(
                    pdf, pr, osm_data, buildings_by_id=buildings_by_id,
                    seed_views=seed_views)

        # Extracted heights summary page — text-only per-building dump.
        # Skipped under `pano_only`: the same data lives in the HTML
        # report (html_report.py) where tables are first-class; the PDF
        # is the orientation / visual artefact, not the data store.
        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle("Seed-Derived Building Heights",
                     fontsize=16, fontweight="bold")
        ax = fig.add_subplot(111)
        ax.axis("off")
        cross_seed = [r for r in building_heights if r.get("n_seeds", 1) >= 2]
        single_seed = [r for r in building_heights if r.get("n_seeds", 1) < 2]

        # Cross-seed disagreement: the same OSM building seen from multiple
        # seeds should produce similar heights. Large spread = matcher is
        # picking different physical structures in different seeds (or one
        # seed's heading is off). This is our headline "are matches even
        # pointing at the same building?" sanity metric.
        cs_disagreements = np.asarray(
            [r.get("seed_disagreement_m", 0.0) for r in cross_seed], dtype=np.float32)
        cs_lines = [
            "Cross-seed agreement metric (lower is better — same building seen from different",
            "seeds should produce similar heights):",
        ]
        if cs_disagreements.size:
            cs_lines.extend([
                f"  n cross-seed buildings : {cs_disagreements.size}",
                f"  median disagreement    : {float(np.median(cs_disagreements)):6.1f} m",
                f"  p75 disagreement       : {float(np.percentile(cs_disagreements, 75)):6.1f} m",
                f"  p90 disagreement       : {float(np.percentile(cs_disagreements, 90)):6.1f} m",
                f"  max disagreement       : {float(np.max(cs_disagreements)):6.1f} m",
            ])
        else:
            cs_lines.append("  (no buildings seen from >= 2 seeds yet)")

        lines = [
            f"Buildings with aggregated estimates: {len(building_heights)}",
            f"  - cross-seed (>=2 distinct seeds): {len(cross_seed)}",
            f"  - single-seed only: {len(single_seed)}",
            "",
            *cs_lines,
            "",
            "Top cross-seed extracted heights (sorted by lowest disagreement first):",
        ]

        # Sort cross-seed buildings by disagreement (best matches first)
        cross_seed_sorted = sorted(
            cross_seed,
            key=lambda r: (r.get("seed_disagreement_m",
                           0.0), -r.get("n_seeds", 0)),
        )
        for row in cross_seed_sorted[:30]:
            per_seed = row.get("per_seed_median_m", {})
            per_seed_str = ", ".join(
                f"{s}:{h:.1f}" for s, h in sorted(per_seed.items())
            )
            lines.append(
                f"- {row['name']}: med={row['median_height_m']:5.1f}m "
                f"weighted={row['weighted_height_m']:5.1f}m "
                f"n_seeds={row['n_seeds']} n_views={row['n_views']:2d} "
                f"disagree={row.get('seed_disagreement_m', 0.0):5.1f}m "
                f"[{per_seed_str}]"
            )
        ax.text(0.03, 0.96, "\n".join(lines), va="top",
                ha="left", family="monospace", fontsize=8)
        if not pano_only:
            pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # Residuals page: predicted vs OSM-tagged height
        tagged_by_id = {
            b.feature_id: b for b in building_records if b.height_tag_m is not None
        }
        pairs: list[tuple[str, float, float, int, int, float]] = []
        for row in building_heights:
            tag = tagged_by_id.get(row["feature_id"])
            if tag is None:
                continue
            pairs.append(
                (
                    row["name"],
                    float(tag.height_tag_m or 0.0),
                    float(row["weighted_height_m"]),
                    int(row.get("n_seeds", 0)),
                    int(row.get("n_views", 0)),
                    float(row.get("mean_confidence", 0.0)),
                )
            )

        fig = plt.figure(figsize=(11, 8.5))
        fig.suptitle("Validation: Predicted vs OSM-tagged Heights",
                     fontsize=16, fontweight="bold")
        if pairs:
            tagged_h = np.array([p[1] for p in pairs], dtype=np.float32)
            pred_h = np.array([p[2] for p in pairs], dtype=np.float32)
            n_seeds_arr = np.array([p[3] for p in pairs], dtype=np.int32)
            resid = pred_h - tagged_h
            mae = float(np.mean(np.abs(resid)))
            rmse = float(np.sqrt(np.mean(resid * resid)))
            bias = float(np.median(resid))

            ax_scatter = fig.add_axes([0.08, 0.42, 0.55, 0.45])
            colors = np.where(n_seeds_arr >= 2, "tab:blue", "tab:orange")
            ax_scatter.scatter(tagged_h, pred_h, c=colors, alpha=0.7, s=24)
            lim = float(max(tagged_h.max(), pred_h.max()) * 1.05)
            ax_scatter.plot([0, lim], [0, lim], "k--",
                            linewidth=0.8, alpha=0.5)
            ax_scatter.set_xlabel("OSM-tagged height (m)")
            ax_scatter.set_ylabel("Predicted weighted height (m)")
            ax_scatter.set_xlim(0, lim)
            ax_scatter.set_ylim(0, lim)
            ax_scatter.set_title(
                f"n={len(pairs)} tagged buildings (blue=cross-seed, orange=single-seed)")
            ax_scatter.grid(alpha=0.25)

            ax_hist = fig.add_axes([0.70, 0.42, 0.25, 0.45])
            ax_hist.hist(resid, bins=20, color="tab:gray", edgecolor="black")
            ax_hist.axvline(0.0, color="black", linewidth=0.8)
            ax_hist.set_title("Residual (pred − tag)")
            ax_hist.set_xlabel("m")

            cs_mask = n_seeds_arr >= 2
            ss_mask = ~cs_mask

            def _stats(mask):
                if not mask.any():
                    return None
                r = resid[mask]
                return (
                    float(np.mean(np.abs(r))),
                    float(np.sqrt(np.mean(r * r))),
                    float(np.median(r)),
                    int(mask.sum()),
                )

            cs = _stats(cs_mask)
            ss = _stats(ss_mask)
            stats_lines = [
                f"All           n={len(pairs):3d}  MAE={mae:6.2f}m  RMSE={rmse:6.2f}m  bias={bias:+6.2f}m",
            ]
            if cs is not None:
                stats_lines.append(
                    f"Cross-seed    n={cs[3]:3d}  MAE={cs[0]:6.2f}m  RMSE={cs[1]:6.2f}m  bias={cs[2]:+6.2f}m   <-- trust this"
                )
            if ss is not None:
                stats_lines.append(
                    f"Single-seed   n={ss[3]:3d}  MAE={ss[0]:6.2f}m  RMSE={ss[1]:6.2f}m  bias={ss[2]:+6.2f}m   (unreliable)"
                )
            if not pano_only:
                # Per-building "worst residuals" listing — table content
                # belongs in the HTML report, not the orientation PDF.
                stats_lines += ["", "Worst residuals:"]
                order = np.argsort(-np.abs(resid))[:10]
                for i in order:
                    name, t, p, ns, nv, _ = pairs[int(i)]
                    stats_lines.append(
                        f"  {name[:24]:<24}  tag={t:5.1f}  pred={p:5.1f}  Δ={p-t:+5.1f}  seeds={ns}"
                    )
            fig.text(
                0.08,
                0.34,
                "\n".join(stats_lines),
                family="monospace",
                fontsize=9,
                va="top",
            )
        else:
            fig.text(
                0.5,
                0.5,
                "No buildings with both OSM-tagged height and a prediction.",
                ha="center",
                va="center",
                fontsize=12,
            )
        pdf.savefig(fig, bbox_inches="tight")
        plt.close(fig)

        # ── Diagnostic: matched-segment width histogram ──────────────────────
        # A bimodal distribution of matched-segment widths is the headline
        # signal that SegFormer is over-merging adjacent towers into one
        # silhouette (one wide segment that wins a match instead of N
        # narrow segments matching N OSM polygons). The per-seed bar to the
        # right shows matched count vs OSM markers projected into the FOV
        # cone — a seed leaving many markers unmatched is a candidate for
        # an `anchor_offsets_deg` override or extra Voronoi splitting.
        widths_px: list[float] = []
        ms_widths_px: list[float] = []
        per_seed_matched: dict[str, int] = {}
        per_seed_in_cone: dict[str, int] = {}
        for sv in seed_views:
            if getattr(sv, "is_negative", False) or sv.is_aerial:
                continue
            seed = sv.seed_name
            per_seed_matched.setdefault(seed, 0)
            per_seed_in_cone.setdefault(seed, 0)
            for seg in sv.matched_segments:
                m = seg.get("matched_projection")
                if m is None:
                    continue
                try:
                    w = float(seg.get("x_right", 0.0)) - float(seg.get("x_left", 0.0))
                except Exception:
                    w = 0.0
                if w > 0:
                    widths_px.append(w)
                    if str(m.get("feature_id", "")).startswith("ms_"):
                        ms_widths_px.append(w)
                per_seed_matched[seed] += 1
            # Markers projected into the FOV cone — match_diagnostics holds
            # every candidate the matcher considered; that's the OSM/MS pool
            # for this view. Use a set of feature_ids to avoid double-counting
            # the same building across segments.
            in_cone_ids: set[str] = set()
            for seg in sv.matched_segments:
                for d in seg.get("match_diagnostics", []) or []:
                    fid = d.get("feature_id")
                    if fid:
                        in_cone_ids.add(str(fid))
                m = seg.get("matched_projection")
                if m and m.get("feature_id"):
                    in_cone_ids.add(str(m["feature_id"]))
            per_seed_in_cone[seed] = max(per_seed_in_cone[seed], len(in_cone_ids))

        if widths_px:
            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(
                "Matcher Coverage Diagnostic — segment widths + per-seed yield",
                fontsize=14, fontweight="bold",
            )

            ax_h = fig.add_axes([0.07, 0.12, 0.55, 0.72])
            arr = np.asarray(widths_px, dtype=np.float32)
            ax_h.hist(arr, bins=24, color=(0.45, 0.55, 0.75, 0.85),
                      edgecolor="black", label="OSM-matched")
            if ms_widths_px:
                ax_h.hist(np.asarray(ms_widths_px, dtype=np.float32),
                          bins=24, color=(0.85, 0.65, 0.20, 0.6),
                          edgecolor="black", label="ms_buildings-matched")
            p50 = float(np.median(arr))
            p75 = float(np.percentile(arr, 75))
            over_thr = 2.0 * p75
            ax_h.axvline(p50, color="green", linestyle="--",
                         linewidth=1.0, label=f"median={p50:.0f}px")
            ax_h.axvline(p75, color="orange", linestyle=":",
                         linewidth=1.0, label=f"p75={p75:.0f}px")
            ax_h.axvline(over_thr, color="red", linestyle="--",
                         linewidth=1.0, label=f"over-merge ≥{over_thr:.0f}px")
            ax_h.set_xlabel("Matched segment width (px)")
            ax_h.set_ylabel("Count")
            ax_h.set_title(
                f"n={arr.size} matched segments  "
                f"({int((arr >= over_thr).sum())} suspected over-merges)",
                fontsize=10,
            )
            ax_h.legend(fontsize=8, loc="upper right")
            ax_h.grid(alpha=0.25)

            ax_b = fig.add_axes([0.68, 0.12, 0.28, 0.72])
            seeds = sorted(per_seed_matched.keys())
            x = np.arange(len(seeds))
            matched_vals = [per_seed_matched[s] for s in seeds]
            cone_vals = [per_seed_in_cone[s] for s in seeds]
            bar_w = 0.4
            ax_b.bar(x - bar_w / 2, cone_vals, width=bar_w,
                     color=(0.65, 0.65, 0.65, 0.85),
                     edgecolor="black", label="in FOV cone")
            ax_b.bar(x + bar_w / 2, matched_vals, width=bar_w,
                     color=(0.30, 0.50, 0.75, 0.95),
                     edgecolor="black", label="matched")
            ax_b.set_xticks(x)
            ax_b.set_xticklabels(seeds, rotation=45, ha="right", fontsize=8)
            ax_b.set_ylabel("Count")
            ax_b.set_title("Per-seed matcher yield", fontsize=10)
            ax_b.legend(fontsize=8)
            ax_b.grid(axis="y", alpha=0.25)

            fig.text(
                0.07, 0.04,
                "Read: a bimodal width histogram = SegFormer over-merging adjacent towers "
                "(one wide segment that wins one match instead of N narrow segments matching "
                "N buildings). A low matched/in-cone ratio for a seed flags that seed as a "
                "candidate for an anchor_offsets_deg override or extra Voronoi splitting.",
                fontsize=8, color="0.30", wrap=True,
            )
            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

        # ── CTBUH / ground-truth validation page ─────────────────────────────
        # Only rendered when the sites/<region>.json contains known_heights_m.
        if known_heights:
            brec_by_id = {b.feature_id: b for b in building_records}
            pred_by_id = {
                row["feature_id"]: float(row["weighted_height_m"])
                for row in building_heights
            }

            fig = plt.figure(figsize=(11, 8.5))
            fig.suptitle(
                "Ground-Truth Validation — Surveyed Building Heights",
                fontsize=16, fontweight="bold",
            )

            matched = [
                kh for kh in known_heights if kh["matched_id"] is not None]
            unmatched = [
                kh for kh in known_heights if kh["matched_id"] is None]

            hdr = (
                f"{'Building':<32} {'Survey m':>9} {'Floors':>7} "
                f"{'OSM tag':>8} {'Pred m':>8} {chr(0x394)+' m':>8} {'Dist m':>8}"
            )
            sep = "-" * 86
            lines = [
                f"Known heights loaded: {len(known_heights)}  "
                f"(OSM-matched: {len(matched)}, unmatched: {len(unmatched)})",
                "",
                hdr,
                sep,
            ]
            # (ctbuh_m, pred_m, name)
            pairs: list[tuple[float, float, str]] = []
            for kh in known_heights:
                fid = kh["matched_id"]
                ctbuh_m = kh["ctbuh_m"]
                floors_s = str(kh["floors"]) if kh["floors"] else "-"
                dist_m = kh["matched_dist_m"]
                osm_h_s = "-"
                pred_s = "-"
                delta_s = "-"
                if fid is not None:
                    brec = brec_by_id.get(fid)
                    if brec and brec.height_tag_m is not None:
                        osm_h_s = f"{brec.height_tag_m:.1f}"
                    if fid in pred_by_id:
                        pred_m = pred_by_id[fid]
                        pred_s = f"{pred_m:.1f}"
                        delta_s = f"{pred_m - ctbuh_m:+.1f}"
                        pairs.append((ctbuh_m, pred_m, kh["name"]))
                no_match = "  ⚠ no OSM footprint" if fid is None else ""
                lines.append(
                    f"{kh['name'][:32]:<32} {ctbuh_m:>9.1f} {floors_s:>7} "
                    f"{osm_h_s:>8} {pred_s:>8} {delta_s:>8} {dist_m:>8.0f}{no_match}"
                )

            if not pano_only:
                # Per-building survey-vs-pred table — dropped under
                # pano_only since the same info is rendered as a sortable
                # HTML table by html_report.py.
                ax_t = fig.add_axes([0.03, 0.40, 0.94, 0.52])
                ax_t.axis("off")
                ax_t.text(
                    0.0, 1.0, "\n".join(lines),
                    va="top", ha="left", family="monospace", fontsize=8.5,
                )

            if len(pairs) >= 2:
                ctbuh_arr = np.array([p[0] for p in pairs])
                pred_arr = np.array([p[1] for p in pairs])
                mae = float(np.mean(np.abs(pred_arr - ctbuh_arr)))
                rmse = float(np.sqrt(np.mean((pred_arr - ctbuh_arr) ** 2)))
                bias = float(np.median(pred_arr - ctbuh_arr))

                ax_s = fig.add_axes([0.06, 0.05, 0.46, 0.32])
                ax_s.scatter(ctbuh_arr, pred_arr, c="tab:blue",
                             s=60, alpha=0.85, zorder=3)
                lim = float(max(ctbuh_arr.max(), pred_arr.max()) * 1.10)
                ax_s.plot([0, lim], [0, lim], "k--", linewidth=0.8, alpha=0.5)
                for (ct, pr, bname) in pairs:
                    ax_s.annotate(
                        bname[:14], (ct, pr), fontsize=7,
                        xytext=(3, 3), textcoords="offset points",
                    )
                ax_s.set_xlabel("Surveyed height (m)")
                ax_s.set_ylabel("Predicted height (m)")
                ax_s.set_xlim(0, lim)
                ax_s.set_ylim(0, lim)
                ax_s.set_title(f"Surveyed vs Predicted (n={len(pairs)})")
                ax_s.grid(alpha=0.25)

                ax_m = fig.add_axes([0.58, 0.05, 0.38, 0.32])
                ax_m.axis("off")
                ax_m.text(
                    0.02, 0.95,
                    f"n = {len(pairs)}\n"
                    f"MAE  = {mae:.1f} m\n"
                    f"RMSE = {rmse:.1f} m\n"
                    f"bias = {bias:+.1f} m\n\n"
                    "Source: surveyed / CTBUH verified heights\n"
                    "Unmatched = building not found in OSM\n"
                    "Pred = pipeline weighted estimate",
                    va="top", ha="left", family="monospace", fontsize=10,
                    transform=ax_m.transAxes,
                )
            else:
                fig.text(
                    0.5, 0.22,
                    "No predictions yet for surveyed buildings.\n"
                    "Run with seed URLs from good vantage points to populate.",
                    ha="center", va="center", fontsize=12,
                )

            pdf.savefig(fig, bbox_inches="tight")
            plt.close(fig)

    # Post-process: stamp "Page X / N" on every page. Done after PdfPages
    # closes the file so the total count is exact. PyMuPDF is the only
    # cleanly-available editor in this stack; falls back silently if not
    # installed.
    try:
        import fitz  # noqa: PLC0415
    except Exception:
        return
    try:
        doc = fitz.open(out_pdf)
        total = doc.page_count
        for i, page in enumerate(doc, start=1):
            rect = page.rect
            text = f"Page {i} / {total}"
            # Bottom-right corner, small monospace, with a thin white
            # background box so it's legible over any underlying plot.
            page.insert_text(
                fitz.Point(rect.width - 80, rect.height - 12),
                text,
                fontsize=8, fontname="helv",
                color=(0.25, 0.25, 0.25),
                render_mode=0,
            )
        doc.saveIncr()
        doc.close()
    except Exception:
        # Page numbering is cosmetic; never let it break the report.
        pass

class _StepTimer:
    """Accumulating wall-clock timer for pipeline steps.

    Each ``timed(label, level)`` block records its duration. Repeated calls
    with the same (label, level) **sum** — so a sub-step run once per seed
    reports the total across all seeds rather than N separate rows. ``level``
    drives indentation in the HTML timing table (0 = top-level step, 1 =
    sub-step). First-seen order is preserved.
    """

    def __init__(self) -> None:
        self._order: list[tuple[str, int]] = []
        self._totals: dict[tuple[str, int], float] = {}

    def _register(self, label: str, level: int) -> tuple[str, int]:
        """Reserve the row's position in render order. Called at *enter* so a
        parent step is listed above its children, even though the parent's
        duration is finalised after the children's records arrive."""
        key = (label, level)
        if key not in self._totals:
            self._order.append(key)
            self._totals[key] = 0.0
        return key

    def record(self, label: str, dt: float, level: int = 0) -> None:
        """Add a pre-measured duration (for blocks that can't use ``timed``,
        e.g. a large try/except where wrapping would force a re-indent)."""
        key = self._register(label, level)
        self._totals[key] += dt
        print(f"[timing]{'  ' * level} {label}: +{dt:.2f}s")

    @contextmanager
    def timed(self, label: str, level: int = 0):
        # Register order on *enter* so parents precede their children in the
        # rendered table; finalise the duration on *exit*.
        key = self._register(label, level)
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = time.perf_counter() - t0
            self._totals[key] += dt
            print(f"[timing]{'  ' * level} {label}: +{dt:.2f}s")

    @property
    def rows(self) -> list[tuple[str, float, int]]:
        return [(lbl, self._totals[(lbl, lvl)], lvl) for (lbl, lvl) in self._order]
