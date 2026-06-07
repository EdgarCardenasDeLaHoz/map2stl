"""Region-driven skyline screening and PDF report generation — the
orchestration layer of skyline. Calls into pipeline.py for the
math; this module handles I/O, seed selection, multi-pass registration,
and rendering.

Entry point
-----------
``run_region_pdf_report(region_name, output_pdf, seed_urls, ...)``
is invoked by ``scripts/08_region_skyline_pdf.py``. It produces one
PDF that doubles as the primary debug artefact for inspection.

Flow per region
---------------
1. Load region bbox from the strm2stl SQLite ``regions`` table.
2. Fetch OSM buildings + waterways via Overpass (cached if available).
3. Build ``BuildingRecord`` list with tagged heights, area, and DEM
   terrain elevation (open-meteo).
4. Resolve user-provided + auto-proposed seed locations:
   - ``_parse_streetview_url`` parses the URL's pano_id / lat / lon /
     heading / pitch / FOV
   - ``_propose_standoff_locations`` adds 8 dirs × 3 standoff radii
     positions weighted toward water-adjacent placements
5. Screen each candidate with a 1-image probe + a sky/contour quality
   gate (``_screen_score_from_image``).
6. ``_seed_multiview_registration`` is the core per-seed loop:
   - Resolve pano (pano_id-bound or location-fallback)
   - Capture 12-view spin (every 30°), SegFormer mask each one
   - Joint optimize the pano-to-geographic offset across all 12 views
     (3° coarse + 0.5° fine, view-weighted by observed-building cols)
   - Per-view register with ±8° around the seed anchor
   - ``estimate_heights_from_registration`` per view
   - Stitch per-view masks for the 360° pano-level result
7. ``aggregate_building_heights`` (in pipeline.py) groups by feature_id
   with outlier-seed downweighting.
8. ``_render_pdf`` writes the multi-page report.

Key state structures
--------------------
- ``RegionBBox``         — region polygon (N/S/E/W)
- ``SkylinePoint``       — candidate camera position + heading + source tag
- ``SeedViewRegistration`` — one frozen result per spin view (image overlay,
  registration metadata, matched-segment list, vertical band crop)
- ``StitchedPanoResult`` — one frozen result per seed for the pano pipeline

Hard dependencies beyond pipeline.py: ``requests`` (Street View Static API
+ Overpass + open-meteo elevations), ``matplotlib`` (PDF rendering),
``app.server.core.cache`` / ``app.server.core.db`` (OSM cache + region
table — production strm2stl integration points).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import time
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import cv2
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
import numpy as np
import requests
from matplotlib.backends.backend_pdf import PdfPages
from shapely.geometry import shape

from app.server.core.cache import osm_cache_key, read_osm_cache, write_osm_cache
from app.server.core.db import get_db, init_db
from city2stl.fetch import fetch_osm_data

from .pipeline import (
    BuildingRecord,
    CapturedView,
    Viewpoint,
    _building_height_from_tags,
    _load_env_file_if_present,
    _polygon_area_m2,
    aggregate_building_heights,
    augment_estimates_with_depth,
    _merge_silhouette_sources,
    _neural_sky_and_building_masks,
    detect_building_silhouettes,
    detect_buildings_from_mask,
    detect_skyline_contour,
    estimate_heights_from_registration,
    match_segments_to_buildings,
    osm_anchor_silhouettes,
    osm_sam_instance_silhouettes,
    register_view_to_osm,
)


# Feature flags + segment palette extracted to region_config.py (F-CLEAN14).
from .region_config import (  # noqa: E402,F401
    _F_SKY1_ENABLED,
    _F_SKY12_ENABLED,
    _F_SKY13_ENABLED,
    _F_SKY13_RADIUS_M,
    _F_SKY13_SAT_BG_ENABLED,
    _F_SKY5_ENABLED,
    _PHASE_C_ENABLED,
    _SEGMENT_PALETTE,
)


# Street View Static API I/O extracted to streetview_io.py (F-CLEAN14).
from .streetview_io import (  # noqa: E402,F401
    STREETVIEW_METADATA_URL,
    STREETVIEW_IMAGE_URL,
    _SV_IMAGE_CACHE_DIR,
    _default_streetview_image_size,
    _extract_pano_id,
    _is_no_imagery_placeholder,
    _meta_location,
    _parse_streetview_url,
    _resolve_api_key,
    _sign_streetview_url,
    _streetview_image,
    _streetview_metadata,
    _streetview_signing_enabled,
)

# Disk cache for Street View images — avoids repeat API charges on re-runs.
# Key = SHA-1 of the request params *without* the API key.



# Dataclasses extracted to region_types.py (F-CLEAN14, 2026-06-07). Re-imported
# here so ``from city2stl.skyline.region_pdf import RegionBBox`` etc. keep working.
from .region_types import (  # noqa: E402,F401
    RegionBBox,
    SkylinePoint,
    StitchedPanoResult,
    SeedViewRegistration,
)




# region/config/OSM data loaders extracted to region_data.py (F-CLEAN14).
from .region_data import (  # noqa: E402,F401
    _attach_building_terrain,
    _bearing_deg,
    _distance_m,
    _drop_buildings_in_water,
    _extract_high_rises,
    _feature_centroid,
    _feature_rings,
    _fetch_elevations,
    _load_osm_for_region,
    _load_region_bbox,
    _load_site_anchor_overrides,
    _load_site_drive_pano_recovery_anchor,
    _load_site_max_plausible_height_m,
    _load_site_negative_seeds,
    _load_site_pano_only_pdf,
    _load_site_render_pdf,
    _load_site_seed_urls,
    _load_site_use_cross_view_scoring,
    _load_site_use_pano_coastline_recovery,
    _load_site_use_satellite_footprints,
    _osm_to_building_records,
    _parse_height,
    _read_site_config,
)
































































# ---------------------------------------------------------------------------
# Geometry-driven viewpoint proposal
# ---------------------------------------------------------------------------


# Seed proposal / screening / auto-replace extracted to seed_selection.py (F-CLEAN14).
from .seed_selection import (  # noqa: E402,F401
    _SCREEN_CACHE_DIR,
    _auto_replace_bad_seeds,
    _propose_standoff_locations,
    _screen_locations,
    _screen_score_from_image,
    _screen_score_from_image_uncached,
)













# PDF rendering extracted to region_render.py (F-CLEAN14).
from .region_render import (  # noqa: E402,F401
    _StepTimer,
    _count_seg_flags,
    _draw_location_map,
    _draw_osm_coastline_overlay,
    _draw_view_minimap,
    _load_known_heights,
    _negative_seed_views,
    _registration_overlay,
    _render_pdf,
    _render_seed_view_page,
    _render_stitched_pano_page,
)






# ---------------------------------------------------------------------------
# F-CLEAN8 named helpers — split from _seed_multiview_registration.
# Each helper owns one responsibility; the orchestrator calls them in order.
# All existing logic is preserved verbatim — pure refactor, no behaviour change.
# ---------------------------------------------------------------------------



# Per-seed multi-view registration extracted to pano_registration.py (F-CLEAN14).
from .pano_registration import (  # noqa: E402,F401
    _capture_pano_views,
    _multires_pano_refine,
    _multires_sam_instances,
    _pano_sliding_window_split,
    _recover_anchor_offset,
    _recover_pano_heading,
    _register_views,
    _seed_multiview_registration,
    _smooth_matches_across_views,
    _smooth_pano_matches_against_views,
    _split_by_depth_discontinuity,
    _stitch_pano_composite,
)











































def run_region_pdf_report(
    region_name: str,
    output_pdf: Path,
    seed_urls: list[str] | None = None,
    explicit_api_key: str | None = None,
    *,
    trace=None,
    skip_pdf: bool = False,
) -> dict:
    # Wall-clock timing per pipeline step, surfaced in the HTML report so
    # contributors can see where a run spends its time without re-instrumenting
    # by hand. ``timer`` accumulates sub-steps (level=1) across loop iterations
    # — e.g. the 5 per-seed registration phases summed over all seeds.
    timer = _StepTimer()
    _timed = timer.timed

    api_key = _resolve_api_key(explicit_api_key)
    bbox = _load_region_bbox(region_name)
    with _timed("OSM fetch"):
        osm_data, osm_source = _load_osm_for_region(bbox)

    buildings = osm_data.get("buildings", {}).get("features") or []
    high_rises = _extract_high_rises(osm_data)
    building_records = _osm_to_building_records(osm_data)
    # F-SKY8: optionally enrich with Microsoft Buildings (satellite-derived)
    # polygons. Gated on per-site flag because the first run downloads
    # ~10–50 MB per quadkey tile; opt-in keeps default behaviour unchanged.
    if _load_site_use_satellite_footprints(region_name):
        from .satellite_footprints import (
            fetch_microsoft_buildings_for_bbox,
            merge_satellite_into_osm,
        )
        with _timed("F-SKY8 satellite footprints (fetch + merge)"):
            sat_polys = fetch_microsoft_buildings_for_bbox(
                (bbox.south, bbox.west, bbox.north, bbox.east))
            building_records = merge_satellite_into_osm(
                building_records, sat_polys)
    # Drop building records whose centroid sits inside an OSM water polygon.
    # Catches both raw-OSM noise and (more commonly) Microsoft-satellite
    # footprints that hallucinate piers/marinas as building rectangles —
    # those would otherwise project into seed views and compete in the
    # matcher as plausible candidates.
    with _timed("Drop buildings in water"):
        building_records = _drop_buildings_in_water(building_records, osm_data)
    # Per-building terrain attach removed (2026-05-28): it was a per-centroid
    # open-meteo call (~1040 round-trips for 83k buildings → ~31 min/run) for
    # near-zero value — Cartagena's buildings sit ~at ground level. Records
    # keep terrain_elev_m=0.0 (the BuildingRecord default), so downstream
    # height math just drops the terrain correction. Seed camera elevations
    # are still fetched separately (small, ~11 points) where they matter.

    # F-SKY10: fetch the per-region satellite image once and stash it for
    # the per-view matcher closure. Failures degrade gracefully — the
    # matcher just runs without the cross-view rerank.
    cross_view_state: dict | None = None
    if _load_site_use_cross_view_scoring(region_name):
        try:
            from .satellite_image import fetch_region_satellite
            with _timed("F-SKY10 cross-view satellite image"):
                sat_image, sat_project, sat_meta = fetch_region_satellite(
                    (bbox.south, bbox.west, bbox.north, bbox.east),
                    target_m_per_px=1.0,
                )
            print(
                f"[cross_view] satellite image loaded: zoom={sat_meta['zoom']} "
                f"shape={sat_meta['shape']} tiles={sat_meta['tiles_loaded']}/{sat_meta['tiles_total']}"
            )
            cross_view_state = {
                "sat_image": sat_image,
                "sat_project": sat_project,
                "sat_meta": sat_meta,
            }
        except Exception as e:
            print(f"[cross_view] satellite image fetch failed: {e}")
            cross_view_state = None

    # F-SKY11.1 Path B: optionally precompute coastline keypoints once
    # per region. The per-seed pano recovery sweep uses these to seed the
    # joint anchor optimizer. Reuses cross_view_state's satellite image
    # when present to avoid a duplicate fetch.
    pano_recovery_state: dict | None = None
    if _load_site_use_pano_coastline_recovery(region_name):
        _pano_precompute_t0 = time.perf_counter()
        try:
            # OSM coastline + water polygons — extracted at region scope
            # for both Phase B (verifier) and Phase C (primary).
            try:
                from .osm_water import (  # noqa: PLC0415
                    extract_coastline_features,
                    extract_water_features,
                    extract_green_features,
                )
                osm_coastline_features = extract_coastline_features(osm_data)
                osm_water_features = extract_water_features(osm_data)
                osm_green_features = extract_green_features(osm_data)
            except Exception as _e_osm_extract:
                print(f"[pano_recovery] OSM extraction failed: {_e_osm_extract}")
                osm_coastline_features = []
                osm_water_features = []
                osm_green_features = []

            # F-SKY13 Phase C: skip the satellite HSV path entirely.
            # OSM is the trusted keypoint source; the satellite mask
            # provides no information we need when Phase C is on.
            if _PHASE_C_ENABLED:
                if not osm_coastline_features:
                    print("[pano_recovery] Phase C: no OSM coastline "
                          "for this region — recovery skipped")
                else:
                    pano_recovery_state = {
                        "primary_source": "osm",
                        # Satellite fields are None in Phase C; legacy
                        # code paths must read them via .get() and
                        # tolerate absence.
                        "sat_water": None,
                        "sat_project": None,
                        "water_frac": None,
                        "osm_coastline_features": osm_coastline_features,
                        "osm_water_features": osm_water_features,
                        "osm_green_features": osm_green_features,
                        "drive_anchor": bool(
                            _load_site_drive_pano_recovery_anchor(region_name)),
                    }
                    print(f"[pano_recovery] Phase C ACTIVE  "
                          f"osm_coastline_features={len(osm_coastline_features)} "
                          "(OSM-primary, satellite HSV skipped)")
            else:
                # Legacy / Phase B path: satellite HSV drives recovery.
                from .satellite_image import fetch_region_satellite
                from .coastline_registration import (
                    detect_sat_water_mask, detect_coastline_keypoints,
                )
                if cross_view_state is not None:
                    sat_image = cross_view_state["sat_image"]
                    sat_project = cross_view_state["sat_project"]
                else:
                    sat_image, sat_project, _meta = fetch_region_satellite(
                        (bbox.south, bbox.west, bbox.north, bbox.east),
                        target_m_per_px=2.0,
                    )
                sat_water = detect_sat_water_mask(sat_image)
                water_frac = float(sat_water.mean())
                if water_frac < 0.02:
                    print(f"[pano_recovery] region has <2% water "
                          f"({water_frac:.1%}) — coastline recovery skipped")
                else:
                    pano_recovery_state = {
                        "primary_source": "satellite",
                        "sat_water": sat_water,
                        "sat_project": sat_project,
                        "water_frac": water_frac,
                        "osm_coastline_features": osm_coastline_features,
                        "osm_water_features": osm_water_features,
                        "osm_green_features": osm_green_features,
                        "drive_anchor": bool(
                            _load_site_drive_pano_recovery_anchor(region_name)),
                    }
                    print(f"[pano_recovery] region satellite water "
                          f"{water_frac:.1%} — keypoints will be computed "
                          "per-seed inside _seed_multiview_registration")
        except Exception as e:
            print(f"[pano_recovery] region precomputation failed: {e}")
            pano_recovery_state = None
        timer.record("F-SKY11/13 pano-recovery precompute",
                     time.perf_counter() - _pano_precompute_t0)

    merged_seed_urls = _load_site_seed_urls(region_name)
    for extra in seed_urls or []:
        extra_text = str(extra).strip()
        if extra_text and extra_text not in merged_seed_urls:
            merged_seed_urls.append(extra_text)

    seeds: list[SkylinePoint] = []
    for idx, url in enumerate(merged_seed_urls):
        parsed = _parse_streetview_url(url)
        if parsed is None:
            continue
        lat, lon, heading, fov, pitch, pano_id = parsed
        seeds.append(
            SkylinePoint(
                name=f"seed_{idx+1}",
                lat=lat,
                lon=lon,
                heading=heading,
                source="seed",
                score=1.0,
                fov=fov,
                pitch=pitch,
                pano_id=pano_id,
            )
        )

    # Generate geometry-driven auto-proposals from OSM tall-building cluster.
    # These are screened via Street View but NOT fed into multiview registration
    # unless the user explicitly promotes them to seed_urls in the sites JSON.
    auto_points = _propose_standoff_locations(bbox, high_rises, osm_data)

    # If no seeds were provided, use the top 3 auto-proposals as provisional
    # seeds so that cities without a sites/<region>.json still run end-to-end.
    if not seeds:
        provisional = auto_points[:3]
        seeds = [
            SkylinePoint(
                p.name, p.lat, p.lon, p.heading, "seed", 1.0, p.fov, p.pitch
            )
            for p in provisional
        ]
        # Remove from auto_points so they don't appear twice in screened.
        auto_points = auto_points[3:]

    # Screen seeds + auto-proposals together so the PDF map shows all candidates.
    all_points = seeds + auto_points
    with _timed("Screen locations (Street View)"):
        screened = _screen_locations(all_points, api_key)

    anchor_overrides = _load_site_anchor_overrides(region_name)
    negative_seeds = _load_site_negative_seeds(region_name)

    # Auto-replace bad seeds: when a user-supplied seed snaps to a
    # location with no clear skyline (Street View "no buildings in any
    # direction"), swap it with the best-scoring auto-proposal nearby.
    # Skips seeds listed in ``negative_seeds`` (intentionally bad — we'd
    # waste a productive auto-proposal slot) and seeds with a manual
    # ``anchor_offsets_deg`` (the override is location-specific; swapping
    # would silently invalidate the user's calibration).
    skip_replace = set(negative_seeds or ()) | set((anchor_overrides or {}).keys())
    with _timed("Auto-replace bad seeds"):
        seeds, seed_substitutions = _auto_replace_bad_seeds(
            seeds, auto_points, screened, skip_names=skip_replace)
    if seed_substitutions:
        for orig, repl in seed_substitutions:
            print(
                f"[auto_seed] {orig.name} ({orig.lat:.5f},{orig.lon:.5f} "
                f"hdg {orig.heading:.0f}deg) -> ({repl.lat:.5f},"
                f"{repl.lon:.5f} hdg {repl.heading:.0f}deg) "
                f"[auto-replaced; original seed failed screening]"
            )

    # Run auto-proposed standoff locations through the full pipeline in
    # ADDITION to the user-supplied seeds (skipping any consumed by the
    # auto-replace pass above and anything that failed screening). This
    # multiplies coverage on sites where user seeds are sparse — Chicago
    # ships with only 2 user URLs but ~6 auto-proposals were previously
    # used only as a swap pool.
    used_auto_names = {repl.name for (_, repl) in seed_substitutions}
    screened_by_name = {
        str((row.get("point") or row.get("requested_point")).name): row
        for row in screened
        if (row.get("point") or row.get("requested_point")) is not None
    }
    additional_seeds: list[SkylinePoint] = []
    for ap in auto_points:
        if ap.name in used_auto_names:
            continue
        scr = screened_by_name.get(ap.name)
        if scr is None or scr.get("coverage") == "rejected":
            continue
        additional_seeds.append(ap)
    if additional_seeds:
        print(
            f"[auto_seed] running {len(additional_seeds)} auto-proposals "
            f"through pipeline in addition to {len(seeds)} user seeds"
        )
        seeds = list(seeds) + additional_seeds

    max_plausible_height_m = _load_site_max_plausible_height_m(region_name)
    if anchor_overrides:
        print(f"[anchor_overrides] {anchor_overrides}")
    if negative_seeds:
        print(f"[negative_seeds] {sorted(negative_seeds)}")
    print(f"[max_plausible_height_m] {max_plausible_height_m:.0f}")
    with _timed("Multiview registration (per-seed)"):
        seed_views, building_heights, pano_results = _seed_multiview_registration(
            seeds, building_records, api_key,
            anchor_overrides=anchor_overrides,
            negative_seeds=negative_seeds,
            trace=trace,
            max_plausible_height_m=max_plausible_height_m,
            cross_view_state=cross_view_state,
            pano_recovery_state=pano_recovery_state,
            timer=timer,
        )

    # Load surveyed ground-truth heights from sites/<region>.json if present.
    known_heights = _load_known_heights(region_name, building_records)

    if not skip_pdf and _load_site_render_pdf(region_name):
        with _timed("Render PDF"):
            _render_pdf(
                output_pdf,
                bbox,
                osm_source,
                osm_data,
                buildings_count=len(buildings),
                high_rise_count=len(high_rises),
                screened=screened,
                seed_views=seed_views,
                building_heights=building_heights,
                building_records=building_records,
                known_heights=known_heights or None,
                pano_results=pano_results,
                pano_only=_load_site_pano_only_pdf(region_name),
            )

    # F-SKY15: parallel HTML diagnostic report. Default ON because the
    # cost is small (one PNG per seed via the existing minimap renderer);
    # set SKYLINE_CV_HTML_REPORT=0 to disable. Failures are swallowed
    # rather than allowed to break the PDF path — HTML is a diagnostic
    # tool, not the canonical output.
    if os.environ.get("SKYLINE_CV_HTML_REPORT", "1").strip().lower() in (
        "1", "true", "yes", "on"
    ):
        try:
            from .html_report import write_region_report  # noqa: PLC0415
            html_out_dir = output_pdf.parent / output_pdf.stem
            _html_t0 = time.perf_counter()
            write_region_report(
                html_out_dir,
                region_name=bbox.name,
                seed_views=seed_views,
                osm_data=osm_data,
                buildings_by_id={b.feature_id: b for b in building_records},
                building_heights=building_heights,
                step_timings=timer.rows,
                screened=screened,
                region_bbox=bbox,
                pano_results=pano_results,
            )
            print(f"[timing] Render HTML: {time.perf_counter() - _html_t0:.2f}s")
        except Exception as _html_e:
            print(f"[F-SKY15] HTML report failed: {_html_e}")

    good = len([r for r in screened if r["coverage"] == "good"])
    medium = len([r for r in screened if r["coverage"] == "medium"])
    weak = len([r for r in screened if r["coverage"] == "weak"])
    return {
        "region": bbox.name,
        "output_pdf": str(output_pdf),
        "osm_source": osm_source,
        "seed_urls_used": len(merged_seed_urls),
        "auto_proposals": len(auto_points),
        "buildings": len(buildings),
        "high_rises": len(high_rises),
        "building_records": len(building_records),
        "locations_screened": len(screened),
        "seed_registration_views": len(seed_views),
        "seed_extracted_buildings": len(building_heights),
        "known_heights_loaded": len(known_heights),
        "coverage": {"good": good, "medium": medium, "weak": weak},
    }
