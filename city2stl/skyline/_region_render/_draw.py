"""skyline region_render — split (A3) (_draw)."""
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
from ..pipeline import BuildingRecord
from ..region_types import RegionBBox, SeedViewRegistration, SkylinePoint
from ..region_data import _distance_m, _feature_rings
from ..region_config import (
    _F_SKY13_ENABLED,
    _F_SKY13_RADIUS_M,
    _F_SKY13_SAT_BG_ENABLED,
    _SEGMENT_PALETTE,
)

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
    reason: str | None = None,
) -> list["SeedViewRegistration"]:
    """Build minimal view rows for a negative / bad seed WITHOUT analysis.

    Used for two cases, both kept frames-only as labelled bad examples
    (viewable in the report, useful for mining negatives / training a
    fast pre-screen later) with every analysis step skipped:
      * a negative seed declared in ``negative_seeds`` (known-bad skyline);
      * a pano auto-REJECTED by the coverage screen (``reason`` set, e.g.
        "low building coverage 3%").
    No heights, no matched segments, no masks.
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
                negative_reason=reason,
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


__all__ = [
    '_draw_location_map',
    '_registration_overlay',
    '_negative_seed_views',
    '_draw_osm_coastline_overlay',
    '_draw_view_minimap',
]
