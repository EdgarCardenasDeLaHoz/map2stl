"""skyline._pano.capture — extracted from pano_registration.py (A2 split)."""
from __future__ import annotations
import json
import math
import os
import time
from contextlib import nullcontext
from pathlib import Path
import cv2
import numpy as np
from ..pipeline import (
    BuildingRecord,
    CapturedView,
    Viewpoint,
    _merge_silhouette_sources,
    _neural_sky_and_building_masks,
    aggregate_building_heights,
    augment_estimates_with_depth,
    detect_building_silhouettes,
    detect_buildings_from_mask,
    estimate_heights_from_registration,
    match_segments_to_buildings,
    osm_anchor_silhouettes,
    osm_sam_instance_silhouettes,
    register_view_to_osm,
)
from ..region_types import SeedViewRegistration, SkylinePoint, StitchedPanoResult
from ..region_config import (
    FLICKR_API_KEY as _FLICKR_API_KEY,
    _F_SKY1_ENABLED,
    _F_SKY11_1_ENABLED,
    _F_SKY12_ENABLED,
    _F_SKY5_ENABLED,
)
from ..region_data import _bearing_deg, _distance_m, _fetch_elevations
from ..streetview_io import _meta_location, _streetview_image, _streetview_metadata
from ..seed_selection import _screen_score_from_image
from ..region_render import _negative_seed_views, _registration_overlay

def _capture_pano_views(
    seed: "SkylinePoint",
    api_key: str,
    spin_headings: tuple[float, ...],
    is_photosphere: bool,
    timer: "_StepTimer | None" = None,
    web_image_cache: "dict[str, np.ndarray] | None" = None,
) -> tuple[list[dict], float, list[dict]]:
    """Pass 1: fetch every spin-view image, apply pitch correction if needed.

    Returns
    -------
    prefetch       : raw list of ``{"geo_heading", "image", ...}`` dicts for
                     ALL headings (including rejected views — pano-recovery needs
                     them). ``image`` is None when the fetch failed.
    effective_pitch: the pitch all views were captured at (may differ from
                     ``seed.pitch`` if pitch correction fired).
    cached_views   : subset of ``prefetch`` with images that passed the
                     per-view screening gate and are suitable for registration.
    """
    def _sub(label: str):
        return timer.timed(label, level=2) if timer is not None else nullcontext()

    # Web-image bypass: single pre-downloaded image, no Street View spin.
    # Seeds with source="web" carry their image in web_image_cache keyed by
    # seed.name; we skip the 12-view loop and create exactly one CapturedView.
    if web_image_cache is not None and seed.name in web_image_cache:
        img = web_image_cache[seed.name]
        sv_score, sv_label, _, _ = _screen_score_from_image(img, pitch=seed.pitch)
        prefetch_entry = {
            "geo_heading": seed.heading,
            "image": img,
            "sv_score": sv_score,
            "sv_label": sv_label,
        }
        prefetch = [prefetch_entry]
        cached_views_web: list[dict] = []
        if sv_label != "rejected":
            vp = Viewpoint(
                name=f"{seed.name}_{int(round(seed.heading)) % 360:03d}",
                query=seed.name,
                lat=seed.lat,
                lon=seed.lon,
                heading=seed.heading,
                pitch=seed.pitch,
                fov=seed.fov,
                image_width=img.shape[1],
                image_height=img.shape[0],
            )
            cap = CapturedView(
                viewpoint=vp, image_path=Path("."),
                metadata_path=Path("."), image=img,
            )
            cached_views_web.append({
                "geo_heading": seed.heading,
                "image": img,
                "cap": cap,
                "sv_score": sv_score,
                "sv_label": sv_label,
                "wide_reg": None,
                "wide_score": float("inf"),
            })
            print(f"[web_seed] {seed.name}: image {img.shape[1]}×{img.shape[0]} "
                  f"sv_label={sv_label} score={sv_score:.2f}")
        return prefetch, seed.pitch, cached_views_web

    # Pass 1a: fetch at original pitch and screen.
    prefetch: list[dict] = []
    any_needs_pitch_correction = False
    for geo_heading in spin_headings:
        with _sub("sv image fetch (cached/network)"):
            image = _streetview_image(
                api_key,
                seed.lat,
                seed.lon,
                geo_heading,
                fov=seed.fov,
                pitch=seed.pitch,
                pano_id=seed.pano_id,
                pano_only=is_photosphere,
            )
        if image is None:
            prefetch.append({"geo_heading": geo_heading, "image": None})
            continue
        with _sub("screen score (SegFormer)"):
            sv_score, sv_label, _, _tc = _screen_score_from_image(
                image, pitch=seed.pitch)
        if sv_label == "rejected":
            prefetch.append({
                "geo_heading": geo_heading,
                "image": image,
                "sv_score": 0.0,
                "sv_label": "rejected",
            })
            continue
        if seed.pitch < 8.0 and (seed.pitch < -8.0 or _tc):
            any_needs_pitch_correction = True
        prefetch.append({
            "geo_heading": geo_heading,
            "image": image,
            "sv_score": sv_score,
            "sv_label": sv_label,
        })

    # Pass 1b: pitch correction — re-fetch all views at corrected pitch.
    effective_pitch = seed.pitch
    n_original = sum(1 for e in prefetch if e["image"] is not None)
    if any_needs_pitch_correction and n_original > 0:
        corrected_pitch = min(seed.pitch + 12.0, 15.0)
        corrected_prefetch: list[dict] = []
        n_kept = 0
        for entry in prefetch:
            gh = entry["geo_heading"]
            if entry["image"] is None:
                corrected_prefetch.append({"geo_heading": gh, "image": None})
                continue
            with _sub("sv image fetch (cached/network)"):
                c_image = _streetview_image(
                    api_key, seed.lat, seed.lon, gh,
                    fov=seed.fov, pitch=corrected_pitch,
                    pano_id=seed.pano_id, pano_only=is_photosphere,
                )
            if c_image is None:
                corrected_prefetch.append({"geo_heading": gh, "image": None})
                continue
            with _sub("screen score (SegFormer)"):
                c_score, c_label, _, _ = _screen_score_from_image(
                    c_image, pitch=corrected_pitch)
            if c_label == "rejected":
                corrected_prefetch.append({
                    "geo_heading": gh,
                    "image": c_image,
                    "sv_score": 0.0,
                    "sv_label": "rejected",
                })
                continue
            corrected_prefetch.append({
                "geo_heading": gh,
                "image": c_image,
                "sv_score": c_score,
                "sv_label": c_label,
            })
            n_kept += 1
        if n_kept >= max(1, (n_original + 1) // 2):
            prefetch = corrected_prefetch
            effective_pitch = corrected_pitch
            print(
                f"[pano pitch] {seed.name} corrected to {effective_pitch:+.1f}° "
                f"({n_kept}/{n_original} views retained)")
        else:
            print(
                f"[pano pitch] {seed.name} keeping original {seed.pitch:+.1f}° "
                f"(corrected pitch retained only {n_kept}/{n_original})")

    # Build cached_views (screened views only) with Viewpoint + CapturedView.
    cached_views: list[dict] = []
    for entry in prefetch:
        image = entry["image"]
        if image is None:
            continue
        if entry.get("sv_label") == "rejected":
            continue
        geo_heading = entry["geo_heading"]
        vp = Viewpoint(
            name=f"{seed.name}_{int(round(geo_heading))%360:03d}",
            query=seed.name,
            lat=seed.lat,
            lon=seed.lon,
            heading=geo_heading,
            pitch=effective_pitch,
            fov=seed.fov,
            image_width=image.shape[1],
            image_height=image.shape[0],
        )
        cap = CapturedView(
            viewpoint=vp, image_path=Path("."),
            metadata_path=Path("."), image=image,
        )
        cached_views.append({
            "geo_heading": geo_heading,
            "image": image,
            "cap": cap,
            "sv_score": entry["sv_score"],
            "sv_label": entry["sv_label"],
            "wide_reg": None,
            "wide_score": float("inf"),
        })
    return prefetch, effective_pitch, cached_views


__all__ = [
    '_capture_pano_views',
]
