"""
terrain_session.py — Python object wrapping the full API_Terrain pipeline.

Usage:
    from terrain_session import TerrainSession

    # Explicit steps
    s = TerrainSession()
    s.start()
    s.select("WestAmerica")
    s.settings["split"]["split_rows"] = 4
    s.fetch_dem()
    s.show_dem()
    s.export_obj()
    s.verify()
    s.slice()
    s.stop()

    # Context manager + run_all
    with TerrainSession().start() as s:
        s.select("WestAmerica")
        s.run_all()
"""

from __future__ import annotations

import copy
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
import requests
from IPython.display import display

# Paths relative to this file (strm2stl/)
_STRM2STL_DIR = Path(__file__).parent
_UI_DIR = _STRM2STL_DIR / "ui"
_VENV_PYTHON = _STRM2STL_DIR.parent / ".venv" / "Scripts" / "python.exe"

_DEFAULT_SETTINGS: dict = {
    # ── Projection ────────────────────────────────────────────────────────
    # Applied to ALL layers (DEM, water mask, satellite, city raster).
    # projection: server-side warp applied to the raw lat/lon grid before returning.
    # maintain_dimensions: True = pad output to dim×dim; False = crop to valid data extent.
    # clip_nans: strip all-NaN edge rows/cols after projection — keeps result rectangular.
    "projection": {
        # "none"|"cosine"|"mercator"|"equal_area"|"equidistant"|"lambert"|"sinusoidal"
        "projection":          "none",
        "maintain_dimensions": False,
        "clip_nans":           True,
    },
    # ── DEM fetch ─────────────────────────────────────────────────────────
    # Sent to /api/terrain/dem and /api/export/*
    # water_dataset is read from the "water" group — set it there, not here.
    "dem": {
        "dim":                 800,
        "depth_scale":         0.5,
        "water_scale":         0.05,
        "subtract_water":      True,
        # "local"|"h5_local"|"SRTMGL1"|...
        "dem_source":          "local",
        # include ESA/JRC land-cover overlay in DEM response
        "show_sat":            False,
    },
    # ── 3-D model export ──────────────────────────────────────────────────
    # Sent to /api/export/stl|obj|obj/split
    # sea_level_cap: clamp ocean surfaces to z=0 (prevents deep-ocean trenches in mesh)
    # puzzle_z: None = auto (model_height + base_height + margin)
    "export": {
        "model_height":     30.0,
        "base_height":      10.0,
        "exaggeration":     1.0,
        "sea_level_cap":    False,
        "floor_val":        0.0,
        "engrave_label":    False,
        "label_text":       "",               # empty = use region name
        "contours":         False,
        "contour_interval": 100.0,            # metres between contour lines
        "contour_style":    "engraved",       # "engraved" | "embossed"
        "puzzle_z":         None,
    },
    # ── Puzzle split ──────────────────────────────────────────────────────
    # Sent to /api/export (format=obj_split)
    # include_border: add a raised lip border around each puzzle piece base
    "split": {
        "split_rows":     4,
        "split_cols":     4,
        "puzzle_m":       50,
        "puzzle_base_n":  10,
        "border_height":  1.0,
        "border_offset":  5.0,
        "include_border": True,
    },
    # ── Slicer ────────────────────────────────────────────────────────────
    # Sent to /api/export/slice
    "slicer": {
        "slicer_config": "maps_2025_part2.ini",
        "output_subdir": "gcode",
    },
    # ── Water mask ────────────────────────────────────────────────────────
    # Sent to /api/terrain/water-mask
    # sat_scale: Earth Engine resolution in metres/pixel (≥10, higher = faster/coarser)
    # dataset also controls the ESA/JRC land-cover overlay in /api/terrain/dem (show_sat=True)
    "water": {
        "sat_scale": 500,
        "dataset":   "esa",    # "esa" | "jrc"
    },
    # ── Satellite imagery ────────────────────────────────────────────────
    # Sent to /api/terrain/satellite (ESRI WMTS real photo tiles)
    # dim: pixel resolution of the returned JPEG; independent from dem.dim
    "satellite": {
        "dim": 800,
    },
    # ── City / OSM features ───────────────────────────────────────────────
    # Sent to /api/cities, /api/cities/raster, /api/cities/export3mf
    "city": {
        "layers":              ["buildings", "roads", "waterways"],
        # polygon simplification tolerance (metres)
        "simplify_tolerance":  0.5,
        "min_area":            5.0,    # minimum building area (m²) to include
        "building_scale":      0.5,    # mm per real metre for building heights
        "road_depression_m":   0.0,    # road surface depression (metres)
        "water_depression_m": -2.0,    # waterway depression (metres)
        "simplify_terrain":    True,   # reduce terrain triangle count in 3MF export
    },
    # ── View / display ────────────────────────────────────────────────────
    # Not sent to any API — used by show_dem() and local visualisation only
    "view": {
        "colormap":               "terrain",
        "rescale_min":            None,    # override elevation min for colour scaling
        "rescale_max":            None,    # override elevation max for colour scaling
        "gridlines_show":         False,
        "gridlines_count":        5,
        "elevation_curve":        None,    # named remap curve
        "elevation_curve_points": None,    # [[x, y], ...] custom curve points
    },
}

_VALID_PROJECTIONS = frozenset({
    "none", "cosine", "mercator", "equal_area",
    "equidistant", "lambert", "sinusoidal",
})
_VALID_DEM_SOURCES = frozenset({
    "local", "h5_local",
    "SRTMGL1", "SRTMGL3", "AW3D30", "COP30", "COP90", "SRTM15Plus",
})
_KNOWN_COLORMAPS = frozenset({
    "terrain", "viridis", "plasma", "magma", "inferno",
    "cividis", "gray", "ocean", "hot", "RdBu",
})


def _kill_tree(proc) -> None:
    """Kill a process and all its children. Accepts Popen or psutil.Process."""
    try:
        import psutil
        parent = proc if isinstance(
            proc, psutil.Process) else psutil.Process(proc.pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except Exception:
        pass


class TerrainSession:
    """Wraps the strm2stl HTTP API as a single Python object."""

    def __init__(self, port: int = 9000):
        self._port = port
        self._base = f"http://127.0.0.1:{port}"
        self._server_proc: Optional[subprocess.Popen] = None

        self.region_name: Optional[str] = None
        self.bbox: dict = {}
        self.settings: dict = copy.deepcopy(_DEFAULT_SETTINGS)
        self.dem: Optional[dict] = None
        self.obj_path: Optional[Path] = None
        # binary water mask response dict
        self.water_mask: Optional[dict] = None
        # raw ESA land-cover class response dict
        self.esa_landcover: Optional[dict] = None
        self.satellite: Optional[str] = None
        self.city_data: Optional[dict] = None
        self.city_raster: Optional[dict] = None

    # ------------------------------------------------------------------ #
    # Server lifecycle                                                      #
    # ------------------------------------------------------------------ #

    def start(self) -> "TerrainSession":
        """Launch the uvicorn server and wait until it responds.

        Kills any existing process already bound to the port before starting,
        so stale servers from previous sessions don't intercept requests.
        """
        try:
            import psutil
            for conn in psutil.net_connections():
                if conn.laddr.port == self._port and conn.status == "LISTEN":
                    stale = psutil.Process(conn.pid)
                    print(
                        f"Killing stale server on port {self._port} (PID {conn.pid}, {stale.exe()})")
                    _kill_tree(stale)
                    time.sleep(0.5)
        except Exception:
            pass

        python_exe = str(
            _VENV_PYTHON) if _VENV_PYTHON.exists() else sys.executable
        self._server_proc = subprocess.Popen(
            [python_exe, "-m", "uvicorn", "server:app",
             "--host", "127.0.0.1", "--port", str(self._port)],
            cwd=str(_UI_DIR),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        for _ in range(20):
            try:
                requests.get(f"{self._base}/api/regions", timeout=1)
                print(
                    f"Server running (PID {self._server_proc.pid}, python: {python_exe})")
                return self
            except Exception:
                time.sleep(0.5)
        print("Warning: server may not be ready yet")
        return self

    def stop(self) -> None:
        """Kill the server process and all its children."""
        if self._server_proc is not None:
            _kill_tree(self._server_proc)
            try:
                self._server_proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
            self._server_proc = None
            print("Server stopped.")

    def __enter__(self) -> "TerrainSession":
        return self

    def __exit__(self, *_) -> None:
        self.stop()

    # ------------------------------------------------------------------ #
    # Region selection                                                      #
    # ------------------------------------------------------------------ #

    def server_settings(self) -> dict:
        """GET /api/settings — return server-authoritative configuration.

        Includes available projections, DEM sources, water datasets,
        slicer config files, and valid numeric ranges for DEM parameters.
        """
        r = requests.get(f"{self._base}/api/settings", timeout=10)
        r.raise_for_status()
        data = r.json()
        print(
            f"Projections  : {[p['id'] for p in data.get('projections', [])]}")
        print(
            f"DEM sources  : {[s['id'] for s in data.get('dem_sources', [])]}")
        print(
            f"Water datasets: {[w['id'] for w in data.get('water_datasets', [])]}")
        print(f"Slicer configs: {data.get('slicer_configs', [])}")
        return data

    def regions(self, filter_col: Optional[str] = None,
                filter_val: Optional[str] = None) -> pd.DataFrame:
        """List all saved regions as a DataFrame. Optionally filter by column value."""
        resp = requests.get(f"{self._base}/api/regions")
        resp.raise_for_status()
        raw = resp.json()["regions"]
        df = pd.DataFrame([{
            "name":      r["name"],
            "continent": r.get("continent"),
            "source":    r.get("source"),
            "city":      r.get("city"),
            "north": r["north"], "south": r["south"],
            "east":  r["east"],  "west":  r["west"],
        } for r in raw])
        if filter_col and filter_val:
            df = df[df[filter_col] == filter_val].reset_index(drop=True)
        display(df)
        print(f"Showing {len(df)} regions")
        return df

    def select(self, name: str) -> "TerrainSession":
        """Select a region by name. Loads bbox and merges saved settings with defaults."""
        resp = requests.get(f"{self._base}/api/regions")
        resp.raise_for_status()
        raw = resp.json()["regions"]
        region = next((r for r in raw if r["name"] == name), None)
        if region is None:
            raise ValueError(f"Region '{name}' not found")

        self.region_name = name
        self.bbox = {k: region[k] for k in ("north", "south", "east", "west")}

        saved_resp = requests.get(f"{self._base}/api/regions/{name}/settings")
        saved = saved_resp.json().get("settings", {}) if saved_resp.ok else {}

        self.settings = copy.deepcopy(_DEFAULT_SETTINGS)
        # Overlay saved region settings into the correct nested group.
        # API uses flat keys (e.g. "dem_source", "model_height") — find the group by key name.
        for api_key, val in saved.items():
            for group in ("dem", "export", "split", "slicer", "water", "satellite", "city", "view"):
                if api_key in self.settings[group]:
                    self.settings[group][api_key] = val
                    break

        print(f"Region : {name}")
        print(f"BBox   : {self.bbox}")
        return self

    def create_region(self, name: str, north: float, south: float,
                      east: float, west: float,
                      description: Optional[str] = None,
                      continent: Optional[str] = None,
                      source: Optional[str] = None) -> "TerrainSession":
        """POST /api/regions — create a new named region in the database.

        Also selects the new region (sets self.region_name and self.bbox).
        """
        payload = {
            "name": name,
            "north": north, "south": south,
            "east": east,   "west": west,
            "description": description,
            "continent":   continent,
            "source":      source,
        }
        r = requests.post(f"{self._base}/api/regions", json=payload)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        self.region_name = name
        self.bbox = {"north": north, "south": south,
                     "east": east, "west": west}
        print(f"Created region: {name}")
        return self

    def update_region(self, north: Optional[float] = None, south: Optional[float] = None,
                      east: Optional[float] = None, west: Optional[float] = None,
                      description: Optional[str] = None,
                      continent: Optional[str] = None,
                      source: Optional[str] = None) -> "TerrainSession":
        """PUT /api/regions/{name} — update the current region's metadata or bbox.

        Any argument left as None keeps the existing value from self.bbox.
        """
        if not self.region_name:
            raise RuntimeError("Call select() or create_region() first")
        payload = {
            "name":        self.region_name,
            "north":       north if north is not None else self.bbox["north"],
            "south":       south if south is not None else self.bbox["south"],
            "east":        east if east is not None else self.bbox["east"],
            "west":        west if west is not None else self.bbox["west"],
            "description": description,
            "continent":   continent,
            "source":      source,
        }
        r = requests.put(
            f"{self._base}/api/regions/{self.region_name}", json=payload)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        # Reflect any bbox changes locally
        self.bbox = {
            "north": payload["north"], "south": payload["south"],
            "east":  payload["east"],  "west":  payload["west"],
        }
        print(f"Updated region: {self.region_name}")
        return self

    def delete_region(self, name: Optional[str] = None) -> "TerrainSession":
        """DELETE /api/regions/{name} — remove a region from the database.

        Defaults to the currently selected region. Clears selection if it
        matches the deleted region.
        """
        target = name or self.region_name
        if not target:
            raise RuntimeError("Provide a region name or call select() first")
        r = requests.delete(f"{self._base}/api/regions/{target}")
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        print(f"Deleted region: {target}")
        if target == self.region_name:
            self.region_name = None
            self.bbox = {}
        return self

    def save_settings(self) -> "TerrainSession":
        """PUT /api/regions/{name}/settings — persist current settings to the database.

        The saved keys are the flat DEM and view settings that the web UI
        understands (dim, depth_scale, water_scale, subtract_water, dem_source,
        projection, colormap, sat_scale).  Export/split/slicer/city settings are
        session-only and are not persisted here.
        """
        if not self.region_name:
            raise RuntimeError("Call select() or create_region() first")
        d = self.settings["dem"]
        p = self.settings["projection"]
        v = self.settings["view"]
        w = self.settings["water"]
        payload = {
            "dim":            d.get("dim"),
            "depth_scale":    d.get("depth_scale"),
            "water_scale":    d.get("water_scale"),
            "subtract_water": d.get("subtract_water"),
            "dem_source":     d.get("dem_source"),
            "projection":     p.get("projection"),
            "colormap":       v.get("colormap"),
            "sat_scale":      w.get("sat_scale"),
            "rescale_min":    v.get("rescale_min"),
            "rescale_max":    v.get("rescale_max"),
            "gridlines_show": v.get("gridlines_show"),
            "gridlines_count": v.get("gridlines_count"),
            "elevation_curve": v.get("elevation_curve"),
            "elevation_curve_points": v.get("elevation_curve_points"),
        }
        # Strip None values — server treats absent keys as "unchanged"
        payload = {k: v for k, v in payload.items() if v is not None}
        r = requests.put(f"{self._base}/api/regions/{self.region_name}/settings",
                         json=payload)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        print(f"Settings saved for: {self.region_name}")
        return self

    # ------------------------------------------------------------------ #
    # Settings display                                                      #
    # ------------------------------------------------------------------ #

    def settings_table(self) -> None:
        """Display current settings grouped by category."""
        for group, vals in self.settings.items():
            if not isinstance(vals, dict):
                print(f"── {group} ── (unexpected value: {vals!r})")
                continue
            print(f"── {group} ──")
            display(pd.DataFrame(
                {"value": {k: str(v) if isinstance(
                    v, (list, dict)) else v for k, v in vals.items()}}
            ))

    def _validate_settings(self) -> None:
        """Raise ValueError for invalid settings; print warnings for soft issues."""
        import warnings
        p = self.settings["projection"]
        d = self.settings["dem"]
        e = self.settings["export"]
        sp = self.settings["split"]
        w = self.settings["water"]
        sat = self.settings["satellite"]
        c = self.settings["city"]
        v = self.settings["view"]
        errors = []

        # ── Enum constraints (hard errors) ──────────────────────────────────
        def _check_enum(val, key, valid_set, label):
            if val is not None and val not in valid_set:
                errors.append(
                    f"  settings['{key}'] = {val!r} is not recognised.\n"
                    f"    {label}: {sorted(valid_set)}"
                )

        _check_enum(p.get("projection"),    "projection.projection",
                    _VALID_PROJECTIONS,  "valid projections")
        _check_enum(d.get("dem_source"),    "dem.dem_source",
                    _VALID_DEM_SOURCES,  "valid dem_source values")
        _check_enum(w.get("dataset"),       "water.dataset",     {
                    "esa", "jrc"},       "valid dataset values")
        _check_enum(e.get("contour_style"), "export.contour_style", {
                    "engraved", "embossed"}, "valid contour_style values")

        # ── satellite.dim range ──────────────────────────────────────────────
        sat_dim = sat.get("dim")
        if sat_dim is not None and isinstance(sat_dim, (int, float)) and not (1 <= sat_dim <= 4000):
            errors.append(
                f"  settings['satellite']['dim'] = {sat_dim!r} must be between 1 and 4000")

        # ── Colormap (soft warning — any matplotlib name is technically valid) ──
        cm = v.get("colormap")
        if cm is not None and cm not in _KNOWN_COLORMAPS:
            warnings.warn(
                f"settings['view']['colormap'] = {cm!r} is not in the known list "
                f"{sorted(_KNOWN_COLORMAPS)}. It will work if it is a valid "
                f"matplotlib colormap name, but may not render correctly in the UI.",
                stacklevel=3,
            )

        # ── Positive float constraints ───────────────────────────────────────
        for group_key, pairs in (
            ("dem",    [("dim", d), ("depth_scale", d)]),
            ("export", [("model_height", e), ("base_height", e), ("exaggeration", e),
                        ("contour_interval", e)]),
            ("split",  [("puzzle_m", sp), ("puzzle_base_n", sp),
                        ("border_height", sp), ("border_offset", sp)]),
            ("city",   [("simplify_tolerance", c),
             ("min_area", c), ("building_scale", c)]),
        ):
            for key, src in pairs:
                val = src.get(key)
                if val is not None and (not isinstance(val, (int, float)) or val <= 0):
                    errors.append(
                        f"  settings['{group_key}']['{key}'] = {val!r} must be a positive number")

        # ── dim range ────────────────────────────────────────────────────────
        dim = d.get("dim")
        if dim is not None and isinstance(dim, (int, float)) and not (1 <= dim <= 2000):
            errors.append(
                f"  settings['dem']['dim'] = {dim!r} must be between 1 and 2000")

        # ── Non-negative floats ──────────────────────────────────────────────
        for key, src, group_key in (
            ("water_scale", d, "dem"),
            ("floor_val",   e, "export"),
        ):
            val = src.get(key)
            if val is not None and (not isinstance(val, (int, float)) or val < 0):
                errors.append(
                    f"  settings['{group_key}']['{key}'] = {val!r} must be a non-negative number")

        # ── sat_scale: integer ≥ 10 ──────────────────────────────────────────
        ss = w.get("sat_scale")
        if ss is not None and (not isinstance(ss, int) or ss < 10):
            errors.append(
                f"  settings['water']['sat_scale'] = {ss!r} must be an integer ≥ 10")

        # ── Integer constraints ──────────────────────────────────────────────
        for key in ("split_rows", "split_cols"):
            val = sp.get(key)
            if val is not None and (not isinstance(val, int) or val < 1):
                errors.append(
                    f"  settings['split']['{key}'] = {val!r} must be an integer ≥ 1")

        # ── Bool constraints ─────────────────────────────────────────────────
        for key, src, group_key in (
            ("maintain_dimensions", p,  "projection"),
            ("clip_nans",          p,  "projection"),
            ("subtract_water",     d,  "dem"),
            ("show_sat",           d,  "dem"),
            ("sea_level_cap",      e,  "export"),
            ("engrave_label",      e,  "export"),
            ("contours",           e,  "export"),
            ("include_border",     sp, "split"),
            ("simplify_terrain",   c,  "city"),
        ):
            val = src.get(key)
            if val is not None and not isinstance(val, bool):
                errors.append(
                    f"  settings['{group_key}']['{key}'] = {val!r} must be True or False")

        # ── city layers: list of known strings ───────────────────────────────
        layers = c.get("layers")
        _valid_layers = {"buildings", "roads", "waterways"}
        if layers is not None:
            if not isinstance(layers, list):
                errors.append(
                    f"  settings['city']['layers'] = {layers!r} must be a list")
            else:
                bad = [x for x in layers if x not in _valid_layers]
                if bad:
                    errors.append(f"  settings['city']['layers'] contains unknown layers: {bad}. "
                                  f"Valid: {sorted(_valid_layers)}")

        if errors:
            raise ValueError("Invalid settings:\n" + "\n".join(errors))

    # ------------------------------------------------------------------ #
    # Pipeline steps                                                        #
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    # Layer helpers                                                         #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _rescale_layer(arr: np.ndarray, max_dim: int,
                       categorical: bool = False) -> np.ndarray:
        """Downscale *arr* so its longer axis ≤ max_dim (no-op if already smaller).

        Parameters
        ----------
        categorical : bool
            Use nearest-neighbour interpolation instead of area averaging.
            Must be True for integer class-label arrays (e.g. ESA land-cover)
            so that class IDs are not blended into non-existent intermediate values.
        """
        import cv2 as _cv2
        h, w = arr.shape[:2]
        longest = max(h, w)
        if longest <= max_dim:
            return arr
        scale = max_dim / longest
        new_w = max(1, int(round(w * scale)))
        new_h = max(1, int(round(h * scale)))
        interp = _cv2.INTER_NEAREST if categorical else _cv2.INTER_AREA
        if arr.ndim == 2:
            return _cv2.resize(arr.astype(np.float32), (new_w, new_h),
                               interpolation=interp).astype(arr.dtype)
        # RGB / multi-channel
        return _cv2.resize(arr, (new_w, new_h), interpolation=interp)

    @staticmethod
    def _colorize_dem(arr: np.ndarray) -> np.ndarray:
        """Convert a float elevation array to uint8 RGB.

        Above sea level: terrain colormap (green lowlands → brown → white peaks).
        Below sea level (< 0): remapped to blue shades (deep = dark blue).
        Returns shape (H, W, 3) uint8.
        """
        import matplotlib.cm as cm
        h, w = arr.shape
        out = np.zeros((h, w, 3), dtype=np.uint8)

        # ── Above sea level: terrain colormap over [0, max] ──────────────
        land = arr.copy()
        land_mask = land >= 0
        if land_mask.any():
            lo, hi = 0.0, float(land[land_mask].max()) or 1.0
            t = np.clip((land - lo) / (hi - lo), 0, 1)
            rgba = (cm.terrain(t) * 255).astype(np.uint8)
            out[land_mask] = rgba[land_mask, :3]

        # ── Below sea level: blue channel, intensity ∝ depth ─────────────
        sea_mask = ~land_mask
        if sea_mask.any():
            depth = np.abs(arr)
            max_depth = float(depth[sea_mask].max()) or 1.0
            t = np.clip(depth / max_depth, 0, 1)
            # dark navy (0,0,80) → bright blue (30,144,255) as depth decreases
            r_ch = (30 * (1 - t)).astype(np.uint8)
            g_ch = (144 * (1 - t)).astype(np.uint8)
            b_ch = (80 + 175 * (1 - t)).astype(np.uint8)
            out[sea_mask, 0] = r_ch[sea_mask]
            out[sea_mask, 1] = g_ch[sea_mask]
            out[sea_mask, 2] = b_ch[sea_mask]

        return out

    @staticmethod
    def _colorize_esa(arr: np.ndarray) -> np.ndarray:
        """Map ESA WorldCover class values to semantic RGB colors.

        ESA classes:
          10 = Tree cover        → forest green
          20 = Shrubland         → olive green
          30 = Grassland         → light green
          40 = Cropland          → yellow-green
          50 = Built-up          → grey
          60 = Bare/sparse veg   → tan/brown
          70 = Snow/ice          → white
          80 = Permanent water   → blue
          90 = Herbaceous wetland→ teal
          95 = Mangroves         → dark green
         100 = Moss/lichen       → pale green
           0 = No data           → black

        Returns shape (H, W, 3) uint8.
        """
        CLASS_COLORS = {
            0:   (0,   0,   0),      # no data → black
            10:  (34,  139, 34),   # tree cover → forest green
            20:  (107, 142, 35),   # shrubland → olive
            30:  (144, 238, 144),  # grassland → light green
            40:  (210, 180, 140),  # cropland → tan/wheat
            50:  (128, 128, 128),  # built-up → grey
            60:  (205, 175, 130),  # bare/sparse → sandy light brown
            70:  (240, 248, 255),  # snow/ice → alice blue-white
            80:  (30,  144, 255),  # water → dodger blue
            90:  (0,   206, 209),  # wetland → dark turquoise
            95:  (0,   100, 0),    # mangroves → dark green
            100: (188, 214, 182),  # moss → pale green
        }
        h, w = arr.shape
        out = np.zeros((h, w, 3), dtype=np.uint8)
        for cls, rgb in CLASS_COLORS.items():
            mask = (arr == cls)
            if mask.any():
                out[mask] = rgb
        # Any unmapped class → purple as a flag
        mapped = np.zeros((h, w), dtype=bool)
        for cls in CLASS_COLORS:
            mapped |= (arr == cls)
        out[~mapped] = (180, 0, 180)
        return out

    def _apply_projection(self, arr: np.ndarray) -> np.ndarray:
        """Project a 2-D array to match the already-projected DEM shape.

        project_coordinates() is designed for the DEM grid and clips NaN columns
        at the projected boundary.  Applying it directly to water/ESA rasters
        (which have different input shapes) produces empty outputs.

        Instead we resize the layer to the DEM's projected shape using
        nearest-neighbour (for categorical arrays) or bilinear interpolation.
        This keeps all layers pixel-aligned with the DEM without running
        each one through the full projection pipeline.

        Returns the array unchanged when projection is 'none' or DEM not yet fetched.
        """
        proj = self.settings["projection"]["projection"]
        if proj == "none":
            return arr
        if self.dem is None:
            # DEM not fetched yet — can't determine target shape; return as-is
            return arr
        import cv2 as _cv2
        target_h, target_w = self.dem["dimensions"]
        if arr.shape == (target_h, target_w):
            return arr
        # Categorical (integer class labels) → nearest-neighbour to preserve IDs.
        # Continuous (float masks, elevation) → area/linear interpolation.
        # quick heuristic: few unique values = categorical
        unique = np.unique(arr[:10, :10])
        is_categorical = len(unique) <= 20 and np.all(arr == arr.astype(int))
        interp = _cv2.INTER_NEAREST if is_categorical else _cv2.INTER_LINEAR
        return _cv2.resize(arr.astype(np.float32), (target_w, target_h),
                           interpolation=interp)

    def fetch_dem(self) -> "TerrainSession":
        """POST /api/terrain/dem — fetch and store the processed DEM."""
        if not self.bbox:
            raise RuntimeError("Call select() before fetch_dem()")
        self._validate_settings()
        payload = {
            **self.bbox,
            **self.settings["dem"],
            **self.settings["projection"],
            "water_dataset": self.settings["water"]["dataset"],
        }
        print("Fetching DEM…")
        r = requests.post(f"{self._base}/api/terrain/dem",
                          json=payload, timeout=120)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        self.dem = r.json()
        d = self.dem
        print(f"min={d['min_elevation']:.1f} m  max={d['max_elevation']:.1f} m  "
              f"mean={d['mean_elevation']:.1f} m  shape={d['dimensions']}")
        return self

    def show_dem(self) -> None:
        """Display the DEM as a matplotlib figure.

        Elevation values are converted from metres to pixels using the
        geographic scale of the bbox: metres-per-pixel = bbox extent in metres
        / dim. Dividing elevation (m) by metres-per-pixel gives a z value in
        pixels, so profile heights in viz.plot_data are correctly proportioned
        relative to the image dimensions.
        """
        if self.dem is None:
            raise RuntimeError("Call fetch_dem() first")
        # ensure viz.py is importable
        if str(_STRM2STL_DIR) not in sys.path:
            sys.path.insert(0, str(_STRM2STL_DIR))
        from viz import plot_data
        H, W = self.dem["dimensions"]
        grid = np.array(self.dem["dem_values"]).reshape(
            H, W)  # server returns north-up (row 0 = north)

        # Compute metres-per-pixel from the bbox geographic extent.
        # Use the mean latitude for the longitude→metre conversion.
        lat_c = (self.bbox["north"] + self.bbox["south"]) / 2.0
        metres_per_deg_lat = 111_320.0
        metres_per_deg_lon = 111_320.0 * np.cos(np.radians(lat_c))
        lat_span_m = abs(self.bbox["north"] -
                         self.bbox["south"]) * metres_per_deg_lat
        lon_span_m = abs(self.bbox["east"] -
                         self.bbox["west"]) * metres_per_deg_lon
        # Use the longer axis so the scale matches the larger image dimension
        m_per_px = max(lat_span_m, lon_span_m) / self.settings["dem"]["dim"]

        # Convert elevation metres → pixels
        if m_per_px > 0:
            grid = grid / m_per_px

        bbox_list = [self.bbox["west"], self.bbox["south"],
                     self.bbox["east"], self.bbox["north"]]
        plot_data(grid, name=self.region_name, bbox=bbox_list,
                  colormap=self.settings["view"]["colormap"])

    def show_water_mask(self) -> None:
        """Display the binary water mask as a matplotlib figure.

        Call fetch_water_mask() first.
        """
        if self.water_mask is None:
            raise RuntimeError("Call fetch_water_mask() first")
        import matplotlib.pyplot as plt

        h, w = self.water_mask["water_mask_dimensions"]
        mask = np.array(
            self.water_mask["water_mask_values"], dtype=np.float32).reshape(h, w)
        pct = self.water_mask.get("water_percentage", 0)
        ext = [self.bbox["west"], self.bbox["east"],
               self.bbox["south"], self.bbox["north"]]

        fig, ax = plt.subplots(figsize=(8, 8))
        ax.imshow(mask, cmap="Blues", vmin=0, vmax=1,
                  origin="upper", extent=ext, aspect="equal")
        ax.set_title(
            f"{self.region_name} — Water mask ({pct:.1f}% water)  {w}x{h} px")
        ax.axis("off")
        plt.tight_layout()
        plt.show()

    def show_esa_landcover(self) -> None:
        """Display the ESA WorldCover land-cover classification raster with semantic colors.

        Call fetch_esa_landcover() first (or fetch_water_mask(), which also populates
        self.esa_landcover since both come from the same endpoint).
        """
        if self.esa_landcover is None:
            raise RuntimeError("Call fetch_esa_landcover() first")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches

        h, w = self.esa_landcover["esa_dimensions"]
        esa_raw = np.array(
            self.esa_landcover["esa_values"], dtype=np.float32).reshape(h, w)
        rgb = self._colorize_esa(esa_raw)
        ext = [self.bbox["west"], self.bbox["east"],
               self.bbox["south"], self.bbox["north"]]

        CLASS_LABELS = {
            10: ("Tree cover",      (34,  139, 34)),
            20: ("Shrubland",       (107, 142, 35)),
            30: ("Grassland",       (144, 238, 144)),
            40: ("Cropland",        (210, 180, 140)),
            50: ("Built-up",        (128, 128, 128)),
            60: ("Bare/sparse",     (205, 175, 130)),
            70: ("Snow/ice",        (240, 248, 255)),
            80: ("Water",           (30,  144, 255)),
            90: ("Wetland",         (0,   206, 209)),
            95: ("Mangroves",       (0,   100, 0)),
            100: ("Moss/lichen",    (188, 214, 182)),
        }
        present = sorted({int(v) for v in np.unique(
            esa_raw) if int(v) in CLASS_LABELS})
        patches = [
            mpatches.Patch(color=np.array(
                CLASS_LABELS[c][1]) / 255.0, label=CLASS_LABELS[c][0])
            for c in present
        ]

        fig, ax = plt.subplots(figsize=(9, 8))
        ax.imshow(rgb, origin="upper", extent=ext, aspect="equal")
        ax.set_title(f"{self.region_name} — ESA land-cover  {w}×{h} px")
        ax.axis("off")
        if patches:
            ax.legend(handles=patches, loc="lower right", fontsize=7,
                      framealpha=0.8, ncol=2)
        plt.tight_layout()
        plt.show()

    def show_satellite(self) -> None:
        """Display the satellite imagery as a matplotlib figure.

        Call fetch_satellite() first.
        """
        if self.satellite is None:
            raise RuntimeError("Call fetch_satellite() first")
        import base64
        import matplotlib.pyplot as plt
        from PIL import Image
        from io import BytesIO

        img_bytes = base64.b64decode(self.satellite)
        img = Image.open(BytesIO(img_bytes))
        ext = [self.bbox["west"], self.bbox["east"],
               self.bbox["south"], self.bbox["north"]]

        fig, ax = plt.subplots(figsize=(8, 8))
        # Satellite tiles are stitched north-to-south: row 0 = north — no flip needed
        ax.imshow(img, origin="upper", extent=ext, aspect="equal")
        ax.set_title(f"{self.region_name} — Satellite")
        ax.axis("off")
        plt.tight_layout()
        plt.show()

    def show_city(self) -> None:
        """Display the city raster layers as a single composite RGB image.

        Layers are blended over a dark background using fixed semantic colours:
          waterways → blue, roads → light grey, walls → purple, buildings → red/orange.
        Call fetch_cities() then composite_city_raster() first.
        """
        if self.city_raster is None:
            print(
                "No city raster available — skipping show_city() (bbox too large or fetch_cities() not called).")
            return
        import matplotlib.pyplot as plt

        h = self.city_raster["height"]
        w = self.city_raster["width"]

        # Layer draw order (back→front) and their RGB colours (0-255)
        LAYER_COLORS = {
            "waterways": (30,  144, 255),   # dodger blue
            "roads":     (180, 180, 180),   # light grey
            "walls":     (160,  80, 200),   # purple
            "buildings": (220,  80,  40),   # red-orange
        }

        # Start with a dark background
        composite = np.zeros((h, w, 3), dtype=np.float32)
        legend_patches = []

        for lname, color in LAYER_COLORS.items():
            if lname not in self.city_raster:
                continue
            mask = np.array(
                self.city_raster[lname], dtype=np.float32).reshape(h, w)
            # Alpha-composite: where mask > 0, blend colour over existing pixels
            alpha = np.clip(mask, 0, 1)[:, :, np.newaxis]
            layer_rgb = np.array(color, dtype=np.float32)[
                np.newaxis, np.newaxis, :]
            composite = composite * (1 - alpha) + layer_rgb * alpha

            import matplotlib.patches as mpatches
            legend_patches.append(
                mpatches.Patch(color=tuple(c / 255 for c in color), label=lname.capitalize()))

        ext = [self.bbox["west"], self.bbox["east"],
               self.bbox["south"], self.bbox["north"]]
        fig, ax = plt.subplots(figsize=(7, 7))
        ax.imshow((composite / 255.0).clip(0, 1),
                  origin="upper", extent=ext, aspect="equal")
        ax.set_title(f"{self.region_name} — City layers")
        if legend_patches:
            ax.legend(handles=legend_patches, loc="lower right", fontsize=8,
                      framealpha=0.8)
        ax.axis("off")
        plt.tight_layout()
        plt.show()

    def check_alignment(
        self,
        upsample_factor: int = 10,
        target_dim: int = 512,
        aspect_tol: float = 0.05,
        min_shift_px: float = 2.0,
    ) -> dict:
        """Check spatial alignment of all fetched layers against the DEM.

        Builds Sobel edge maps from each layer, runs phase_cross_correlation
        against the DEM edge map, then measures NCC similarity before and after
        applying the suggested integer-pixel shift.  Shifts smaller than
        *min_shift_px* (Euclidean magnitude) are treated as negligible and
        reported as not applied.

        Parameters
        ----------
        upsample_factor : int
            Sub-pixel precision of phase_cross_correlation (default 10 → 0.1 px).
        target_dim : int
            Longer-axis size to rescale all layers to before registration.
        aspect_tol : float
            Maximum allowed fractional difference in aspect ratio between layers
            before raising a warning (default 0.05 = 5 %).
        min_shift_px : float
            Euclidean magnitude threshold in scaled pixels below which a suggested
            shift is considered negligible and not applied (default 2.0 px).

        Returns
        -------
        dict  keyed by layer name →
            {
              "shift_raw":    [dy, dx],   # sub-pixel shift from phase_cross_correlation
              "shift_int":    [dy, dx],   # rounded to nearest integer pixel
              "magnitude":    float,      # Euclidean magnitude of shift_int
              "applied":      bool,       # False if magnitude < min_shift_px
              "ncc_before":   float,      # NCC similarity of edge maps before shift  [-1, 1]
              "ncc_after":    float,      # NCC similarity of edge maps after shift   [-1, 1]
              "ncc_gain":     float,      # ncc_after - ncc_before (positive = improvement)
              "phasediff":    float,
            }
        """
        import base64
        from io import BytesIO
        import matplotlib.pyplot as plt
        from PIL import Image
        from skimage.registration import phase_cross_correlation
        from skimage.transform import resize as sk_resize

        if self.dem is None:
            raise RuntimeError(
                "Call fetch_dem() first — DEM is the registration reference.")

        # ── 1. Extract north-up semantic RGB arrays from each layer ─────────
        # Using semantic colorization as the shared representation:
        #  - DEM:        terrain colormap (green→brown→white) + sub-zero→blue
        #  - Water mask: blue where water (1), grey-white where land (0)
        #  - ESA:        semantic class colors (water=blue, trees=green, etc.)
        #  - Satellite:  raw RGB (already in color space)
        #  - City:       composite heat map (roads/buildings → warm tones)
        # All layers are RGB (H,W,3) float32 in [0,255] before normalization.

        def _dem_rgb() -> np.ndarray:
            H, W = self.dem["dimensions"]
            elev = np.array(self.dem["dem_values"],
                            dtype=np.float32).reshape(H, W)
            return self._colorize_dem(elev).astype(np.float32)

        def _water_rgb() -> Optional[np.ndarray]:
            if self.water_mask is None:
                return None
            h, w = self.water_mask["water_mask_dimensions"]
            mask = np.array(self.water_mask["water_mask_values"],
                            dtype=np.float32).reshape(h, w)
            # Binary: water=1 → dodger blue (bright), land=0 → dark green.
            # High contrast at coastlines correlates with the DEM sea-level boundary.
            rgb = np.zeros((h, w, 3), dtype=np.float32)
            rgb[mask >= 0.5] = [30, 144, 255]   # water → blue
            rgb[mask < 0.5] = [34,  85,  34]   # land  → dark green
            return rgb

        def _esa_rgb() -> Optional[np.ndarray]:
            if self.esa_landcover is None:
                return None
            h, w = self.esa_landcover["esa_dimensions"]
            esa = np.array(self.esa_landcover["esa_values"],
                           dtype=np.float32).reshape(h, w)
            return self._colorize_esa(esa).astype(np.float32)

        def _satellite_rgb() -> Optional[np.ndarray]:
            if self.satellite is None:
                return None
            img = Image.open(
                BytesIO(base64.b64decode(self.satellite))).convert("RGB")
            return np.array(img, dtype=np.float32)

        def _city_rgb() -> Optional[np.ndarray]:
            if self.city_raster is None:
                return None
            h = self.city_raster["height"]
            w = self.city_raster["width"]
            # Same semantic colours as show_city, blended back→front
            LAYER_COLORS = {
                "waterways": (30,  144, 255),
                "roads":     (180, 180, 180),
                "walls":     (160,  80, 200),
                "buildings": (220,  80,  40),
            }
            rgb = np.zeros((h, w, 3), dtype=np.float32)
            for lname, color in LAYER_COLORS.items():
                if lname not in self.city_raster:
                    continue
                mask = np.array(self.city_raster[lname],
                                dtype=np.float32).reshape(h, w)
                alpha = np.clip(mask, 0, 1)[:, :, np.newaxis]
                layer_rgb = np.array(color, dtype=np.float32)[
                    np.newaxis, np.newaxis, :]
                rgb = rgb * (1 - alpha) + layer_rgb * alpha
            return rgb

        layers: dict[str, np.ndarray] = {"dem": _dem_rgb()}
        for name, fn in (("water_mask", _water_rgb),
                         ("esa",        _esa_rgb),
                         ("satellite",  _satellite_rgb),
                         ("city",       _city_rgb)):
            arr = fn()
            if arr is not None:
                layers[name] = arr

        if len(layers) == 1:
            print("Only DEM available — nothing to register against.")
            return {}

        # ── 2. Check aspect ratios (degree-based W/H) ───────────────────────
        lon_range = self.bbox["east"] - self.bbox["west"]
        lat_range = self.bbox["north"] - self.bbox["south"]
        geo_aspect = lon_range / lat_range  # W/H in degrees

        def _aspect(arr: np.ndarray) -> float:
            # W/H works for both 2-D and 3-D
            return arr.shape[1] / arr.shape[0]

        aspect_ok = True
        for name, arr in layers.items():
            a = _aspect(arr)
            diff = abs(a - geo_aspect) / geo_aspect
            status = "OK" if diff <= aspect_tol else "MISMATCH"
            if diff > aspect_tol and name != "dem":
                aspect_ok = False
            marker = "[reference geo]" if name == "dem" else f"[{status}]"
            print(f"  {name:12s}  {arr.shape[1]:5d}×{arr.shape[0]:4d} px  "
                  f"aspect={a:.4f}  Δ={diff*100:.1f}%  {marker}")
        print(
            f"  {'(expected)':12s}  {'geo W/H':>10s}  aspect={geo_aspect:.4f}  [geographic]")
        if not aspect_ok:
            print("WARNING: aspect ratio mismatch — layer may cover a different extent "
                  "or projection was applied to DEM but not other layers.")

        # ── 3. Rescale all layers to target_dim on the longer axis ──────────
        def _scale_rgb(arr: np.ndarray) -> np.ndarray:
            h, w = arr.shape[:2]
            if w >= h:
                new_w, new_h = target_dim, max(
                    1, int(round(target_dim * h / w)))
            else:
                new_h, new_w = target_dim, max(
                    1, int(round(target_dim * w / h)))
            if arr.ndim == 2:
                return sk_resize(arr, (new_h, new_w),
                                 anti_aliasing=True, preserve_range=True).astype(np.float32)
            # RGB: resize each channel
            return sk_resize(arr, (new_h, new_w, arr.shape[2]),
                             anti_aliasing=True, preserve_range=True).astype(np.float32)

        scaled = {name: _scale_rgb(arr) for name, arr in layers.items()}
        ref = scaled["dem"]

        # ── 4. Match all layers to the reference spatial shape (pad or crop) ─
        def _match_shape(arr: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
            # Crop to target first (handles layers larger than ref)
            arr = arr[:target_h, :target_w]
            ph = target_h - arr.shape[0]
            pw = target_w - arr.shape[1]
            if arr.ndim == 2:
                return np.pad(arr, ((0, max(0, ph)), (0, max(0, pw))), mode="constant")
            return np.pad(arr, ((0, max(0, ph)), (0, max(0, pw)), (0, 0)), mode="constant")

        ref_h, ref_w = ref.shape[:2]
        padded = {name: _match_shape(arr, ref_h, ref_w)
                  for name, arr in scaled.items()}

        # ── 5. Convert each RGB layer to an edge map for registration ────────
        # phase_cross_correlation needs *shared structural signal* across layers.
        # Raw pixel values differ completely (elevation ≠ satellite texture ≠ ESA
        # class IDs), but edges (coastlines, ridge lines, building outlines) ARE
        # shared.  Strategy:
        #   a) RGB → luminance  (perceptual weights)
        #   b) Sobel gradient magnitude  → emphasises boundaries
        #   c) Gaussian blur  → reduce noise
        #   d) zero-mean / unit-std normalisation so amplitudes match
        import cv2 as _cv2

        def _to_edge_map(arr: np.ndarray) -> np.ndarray:
            """RGB (H,W,3) float → normalised Sobel edge magnitude (H,W) float32."""
            if arr.ndim == 3:
                # Perceptual luminance
                luma = (arr[:, :, 0] * 0.299 +
                        arr[:, :, 1] * 0.587 +
                        arr[:, :, 2] * 0.114)
            else:
                luma = arr.astype(np.float32)
            # Normalise to [0, 255] so Sobel scale is consistent across layers
            lo, hi = luma.min(), luma.max()
            if hi > lo:
                luma = (luma - lo) / (hi - lo) * 255.0
            luma8 = luma.astype(np.float32)
            # Sobel in x and y
            sx = _cv2.Sobel(luma8, _cv2.CV_32F, 1, 0, ksize=3)
            sy = _cv2.Sobel(luma8, _cv2.CV_32F, 0, 1, ksize=3)
            mag = np.sqrt(sx ** 2 + sy ** 2)
            # Mild Gaussian blur to suppress 1-px noise
            mag = _cv2.GaussianBlur(mag, (5, 5), sigmaX=1.0)
            # Zero-mean / unit-std
            std = mag.std()
            if std > 0:
                mag = (mag - mag.mean()) / std
            else:
                mag = mag - mag.mean()
            return mag.astype(np.float32)

        edges = {name: _to_edge_map(arr) for name, arr in padded.items()}

        # ── 6. Run phase_cross_correlation pairwise vs DEM ───────────────────
        results: dict = {}
        other_names = [n for n in edges if n != "dem"]
        n_other = len(other_names)

        fig, axes = plt.subplots(2, n_other + 1,
                                 figsize=(4 * (n_other + 1), 8))
        if n_other == 0:
            axes = axes.reshape(2, 1)
        axes = np.array(axes)

        # Top row: colorized RGB previews
        dem_rgb_prev = (padded["dem"] / 255.0).clip(0, 1)
        axes[0, 0].imshow(dem_rgb_prev, origin="upper")
        axes[0, 0].set_title("DEM (colorized)", fontsize=8)
        axes[0, 0].axis("off")

        # Bottom row: edge maps used for registration
        axes[1, 0].imshow(edges["dem"], cmap="gray", origin="upper")
        axes[1, 0].set_title("DEM (edges for reg.)", fontsize=8)
        axes[1, 0].axis("off")

        def _ncc_rgb(a: np.ndarray, b: np.ndarray) -> float:
            """Normalised cross-correlation averaged across RGB channels.

            Correlating each channel independently then averaging gives more
            signal than collapsing to luminance first — colour differences
            between classes (blue water vs green land vs brown bare) each
            contribute a separate correlation term.
            """
            a = a.astype(np.float32)
            b = b.astype(np.float32)
            scores = []
            for c in range(a.shape[2] if a.ndim == 3 else 1):
                ac = a[:, :, c] if a.ndim == 3 else a
                bc = b[:, :, c] if b.ndim == 3 else b
                ac = ac - ac.mean()
                bc = bc - bc.mean()
                denom = np.sqrt((ac ** 2).sum() * (bc ** 2).sum())
                scores.append(float(np.sum(ac * bc) / denom)
                              if denom > 1e-9 else 0.0)
            return float(np.mean(scores))

        ref_edge = edges["dem"]
        # Use satellite as ground truth for NCC if available — it's the actual
        # photo of the ground so colour correlation against it is most meaningful.
        # Fall back to DEM colorization if satellite wasn't fetched.
        ncc_ref_rgb = padded.get("satellite", padded["dem"])
        ncc_ref_name = "satellite" if "satellite" in padded else "dem"

        # Satellite is already the NCC reference — skip registering it against
        # itself (would always give shift=0, ncc=1.0, which is trivially true).
        registration_names = [n for n in other_names if n != ncc_ref_name]

        for col, name in enumerate(other_names, start=1):
            # Satellite used as NCC reference — report ncc=1 trivially, no shift
            if name == ncc_ref_name:
                results[name] = {
                    "shift_raw":  [0.0, 0.0],
                    "shift_int":  [0, 0],
                    "magnitude":  0.0,
                    "applied":    False,
                    "ncc_before": 1.0,
                    "ncc_after":  1.0,
                    "ncc_gain":   0.0,
                    "phasediff":  0.0,
                    "note":       "NCC reference — not registered against itself",
                }
                label = "NCC reference\nncc=1.000 (self)"
                rgb_prev = (padded[name] / 255.0).clip(0, 1)
                axes[0, col].imshow(rgb_prev, origin="upper")
                axes[0, col].set_title(f"{name}\n{label}", fontsize=8)
                axes[0, col].axis("off")
                axes[1, col].imshow(edges[name], cmap="gray", origin="upper")
                axes[1, col].set_title(f"{name} (edges)", fontsize=8)
                axes[1, col].axis("off")
                continue

            # Check edge coverage — layers with very few edges (near-uniform,
            # e.g. almost-no-water masks) produce garbage shifts from noise peaks.
            edge_coverage = float((np.abs(edges[name]) > 0.5).mean())
            # For water_mask, also guard on actual water percentage — a mask that
            # is 99% land has almost no coastline edges so any detected shift is noise.
            if name == "water_mask" and self.water_mask is not None:
                water_pct = self.water_mask.get("water_percentage", 50.0)
                # Require at least 5% water AND 5% land to have meaningful coastline edges
                feature_pct = min(water_pct, 100.0 - water_pct)
                low_coverage = edge_coverage < 0.02 or feature_pct < 5.0
            else:
                low_coverage = edge_coverage < 0.02  # < 2% edge pixels

            # Note: phase_cross_correlation 'error' is deprecated in skimage >= 0.20
            # and always returns 1.0.  Use shift + phasediff only.
            shift, _error, phasediff = phase_cross_correlation(
                ref_edge, edges[name], upsample_factor=upsample_factor)

            # Round to integer pixels — sub-pixel shifts can't be applied to
            # discrete rasters and tiny fractional values add noise.
            shift_int = np.round(shift).astype(int)
            dy_i, dx_i = int(shift_int[0]), int(shift_int[1])
            magnitude = float(np.sqrt(dy_i ** 2 + dx_i ** 2))

            # NCC on full RGB vs satellite (or DEM if no satellite)
            ncc_before = _ncc_rgb(ncc_ref_rgb, padded[name])

            # Skip applying shift if: magnitude < threshold OR layer has too few
            # edges (correlation result is unreliable noise).
            if magnitude >= min_shift_px and not low_coverage:
                shifted_rgb = np.roll(padded[name], (dy_i, dx_i), axis=(0, 1))
                applied = True
            else:
                shifted_rgb = padded[name]
                applied = False

            ncc_after = _ncc_rgb(ncc_ref_rgb, shifted_rgb)

            results[name] = {
                "shift_raw":  shift.tolist(),
                "shift_int":  [dy_i, dx_i],
                "magnitude":  magnitude,
                "applied":    applied,
                "ncc_before": ncc_before,
                "ncc_after":  ncc_after,
                "ncc_gain":      ncc_after - ncc_before,
                "phasediff":     float(phasediff),
                "edge_coverage": edge_coverage,
                "low_coverage":  low_coverage,
            }

            if low_coverage:
                status = f"skip (low edges {edge_coverage*100:.1f}%)"
            elif applied:
                status = f"shift=({dy_i:+d},{dx_i:+d})px"
            else:
                status = f"no shift (<{min_shift_px:.0f}px)"
            label = f"{status}\nncc {ncc_before:.3f}→{ncc_after:.3f}"

            rgb_prev = (padded[name] / 255.0).clip(0, 1)
            axes[0, col].imshow(rgb_prev, origin="upper")
            axes[0, col].set_title(f"{name}\n{label}", fontsize=8)
            axes[0, col].axis("off")

            axes[1, col].imshow(edges[name], cmap="gray", origin="upper")
            axes[1, col].set_title(
                f"{name} (edges  {edge_coverage*100:.1f}%)", fontsize=8)
            axes[1, col].axis("off")

        fig.suptitle(
            f"{self.region_name} — Layer alignment (target {target_dim} px)", fontsize=10)
        plt.tight_layout()
        plt.show()

        print(
            f"\nAlignment results  (shift via DEM edges, NCC vs {ncc_ref_name}, min_shift={min_shift_px}px):")
        print(f"  {'layer':12s}  {'shift(dy,dx)':>14s}  {'mag':>5s}  {'applied':>7s}  "
              f"{'ncc_before':>10s}  {'ncc_after':>9s}  {'gain':>6s}  {'note'}")
        for name, r in results.items():
            dy, dx = r["shift_int"]
            note = r.get("note", "")
            if not note and r.get("low_coverage"):
                note = f"low edges ({r['edge_coverage']*100:.1f}%) — shift unreliable"
            print(f"  {name:12s}  ({dy:+4d},{dx:+4d}) px  "
                  f"{r['magnitude']:5.1f}  {'yes' if r['applied'] else 'no':>7s}  "
                  f"{r['ncc_before']:10.4f}  {r['ncc_after']:9.4f}  {r['ncc_gain']:+.4f}"
                  + (f"  [{note}]" if note else ""))

        return results

    def _fetch_water_endpoint(self) -> dict:
        """Call /api/terrain/water-mask and return the raw response dict.

        Shared by fetch_water_mask() and fetch_esa_landcover(). Second call
        hits the server-side cache so both methods can be called cheaply.

        sat_scale is computed dynamically to target approximately dem.dim pixels
        on the longer axis of the bbox, then clamped to the user's sat_scale
        setting (never coarser than requested) and ESA's 10 m/px native floor.
        """
        # Compute the m/px needed to land approximately at dem.dim resolution
        north = self.bbox["north"]
        south = self.bbox["south"]
        east = self.bbox["east"]
        west = self.bbox["west"]
        mid_lat = (north + south) / 2.0
        bbox_w_m = abs(east - west) * 111000.0 * \
            math.cos(math.radians(mid_lat))
        bbox_h_m = abs(north - south) * 111000.0
        longer_m = max(bbox_w_m, bbox_h_m)
        dim = self.settings["dem"]["dim"]
        # m/px to hit dim pixels on longer axis; floor at 10 (ESA native resolution)
        scale_for_dim = max(10, int(longer_m / dim))
        # Never go coarser than the user's sat_scale setting
        sat_scale = min(scale_for_dim, self.settings["water"]["sat_scale"])
        params = {**self.bbox, "sat_scale": sat_scale,
                  "dataset": self.settings["water"]["dataset"]}
        r = requests.get(f"{self._base}/api/terrain/water-mask",
                         params=params, timeout=120)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        return r.json()

    def fetch_water_mask(self, max_display_dim: int = 1000) -> "TerrainSession":
        """GET /api/terrain/water-mask — fetch binary water mask (0 = land, 1 = water).

        Result stored on self.water_mask. Also populates self.esa_landcover since
        both come from the same endpoint (second call is free from cache).
        Configure via settings['water'] (sat_scale, dataset).

        Parameters
        ----------
        max_display_dim : int
            Cap the stored array's longer axis to this many pixels (default 1000).
            The server may return very high-res masks for large regions; this keeps
            memory and display time reasonable.
        """
        if not self.bbox:
            raise RuntimeError("Call select() before fetch_water_mask()")
        print("Fetching water mask…")
        data = self._fetch_water_endpoint()

        pct = data.get("water_percentage", 0.0)

        # ── Binary mask ──────────────────────────────────────────────────
        h, w = data["water_mask_dimensions"]
        mask_arr = np.array(data["water_mask_values"],
                            dtype=np.float32).reshape(h, w)

        if self.settings["projection"]["projection"] != "none":
            mask_arr = self._apply_projection(mask_arr)
            print(
                f"  → projected to {mask_arr.shape[1]}×{mask_arr.shape[0]} px")

        mask_arr = self._rescale_layer(mask_arr, max_display_dim)
        h, w = mask_arr.shape

        self.water_mask = {
            "water_mask_values":     mask_arr.ravel().tolist(),
            "water_mask_dimensions": [h, w],
            "water_pixels":          data.get("water_pixels", 0),
            "total_pixels":          data.get("total_pixels", 0),
            "water_percentage":      pct,
            "from_cache":            data.get("from_cache", False),
        }

        # ── ESA land-cover (stash raw; rescaled on fetch_esa_landcover) ─
        self.esa_landcover = {
            "esa_values":     data["esa_values"],
            "esa_dimensions": data["esa_dimensions"],
            "from_cache":     data.get("from_cache", False),
            "_rescaled":      False,
        }

        print(f"Water coverage: {pct:.1f}%  |  grid: {w}×{h} px")
        return self

    def fetch_esa_landcover(self, max_display_dim: int = 1000) -> "TerrainSession":
        """GET /api/terrain/water-mask — fetch ESA WorldCover land-cover class raster.

        Returns raw ESA class values (10=tree cover, 20=shrub, 30=grass, 40=crop,
        50=built-up, 60=bare, 70=snow, 80=water, 90=wetland, 95=mangrove, 100=moss).
        Result stored on self.esa_landcover.

        If fetch_water_mask() was already called the raw data is already cached on
        self.esa_landcover — this method just applies projection + rescaling.
        Configure via settings['water'] (sat_scale, dataset).

        Parameters
        ----------
        max_display_dim : int
            Cap the stored array's longer axis to this many pixels (default 1000).
        """
        if not self.bbox:
            raise RuntimeError("Call select() before fetch_esa_landcover()")

        if self.esa_landcover is None:
            print("Fetching ESA land-cover…")
            data = self._fetch_water_endpoint()
            self.esa_landcover = {
                "esa_values":     data["esa_values"],
                "esa_dimensions": data["esa_dimensions"],
                "from_cache":     data.get("from_cache", False),
                "_rescaled":      False,
            }
            # Also populate water_mask if not yet done
            if self.water_mask is None:
                h0, w0 = data["water_mask_dimensions"]
                m0 = np.array(data["water_mask_values"],
                              dtype=np.float32).reshape(h0, w0)
                m0 = self._rescale_layer(m0, max_display_dim)
                h0, w0 = m0.shape
                self.water_mask = {
                    "water_mask_values":     m0.ravel().tolist(),
                    "water_mask_dimensions": [h0, w0],
                    "water_pixels":          data.get("water_pixels", 0),
                    "total_pixels":          data.get("total_pixels", 0),
                    "water_percentage":      data.get("water_percentage", 0.0),
                    "from_cache":            data.get("from_cache", False),
                }

        # Apply projection + rescale if not yet done
        if not self.esa_landcover.get("_rescaled", False):
            h, w = self.esa_landcover["esa_dimensions"]
            esa_arr = np.array(
                self.esa_landcover["esa_values"], dtype=np.float32).reshape(h, w)

            if self.settings["projection"]["projection"] != "none":
                esa_arr = self._apply_projection(esa_arr)
                print(
                    f"  → projected to {esa_arr.shape[1]}×{esa_arr.shape[0]} px")

            esa_arr = self._rescale_layer(
                esa_arr, max_display_dim, categorical=True)
            h, w = esa_arr.shape
            self.esa_landcover["esa_values"] = esa_arr.ravel().tolist()
            self.esa_landcover["esa_dimensions"] = [h, w]
            self.esa_landcover["_rescaled"] = True

        h, w = self.esa_landcover["esa_dimensions"]
        print(f"ESA land-cover: {w}×{h} px")
        return self

    def fetch_satellite(self) -> "TerrainSession":
        """GET /api/terrain/satellite — fetch base64-encoded JPEG satellite image for bbox.

        Result stored on self.satellite (base64 string). Uses settings['dem']['dim'] for resolution.
        """
        if not self.bbox:
            raise RuntimeError("Call select() before fetch_satellite()")
        params = {**self.bbox, "dim": self.settings["satellite"]["dim"]}
        print("Fetching satellite image…")
        r = requests.get(f"{self._base}/api/terrain/satellite",
                         params=params, timeout=300)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        self.satellite = r.json()["image"]
        print(
            f"Satellite image received ({len(self.satellite) // 1024} KB base64)")

        # Apply projection per-channel so satellite aligns with the projected DEM
        if self.settings["projection"]["projection"] != "none":
            import base64
            from PIL import Image
            from io import BytesIO
            img_bytes = base64.b64decode(self.satellite)
            img = np.array(Image.open(BytesIO(img_bytes)).convert(
                "RGB"), dtype=np.float32)
            # Project each RGB channel independently
            r_ch = self._apply_projection(img[:, :, 0])
            g_ch = self._apply_projection(img[:, :, 1])
            b_ch = self._apply_projection(img[:, :, 2])
            projected = np.stack([r_ch, g_ch, b_ch], axis=2)
            # NaN fills from projection → black (0)
            projected = np.nan_to_num(projected, nan=0.0).clip(
                0, 255).astype(np.uint8)
            buf = BytesIO()
            Image.fromarray(projected).save(buf, format="JPEG", quality=85)
            self.satellite = base64.b64encode(buf.getvalue()).decode()
            print(f"  → projected to {projected.shape[1]}×{projected.shape[0]} px "
                  f"({len(self.satellite) // 1024} KB)")

        return self

    def merge_dem(self, layers: list) -> "TerrainSession":
        """POST /api/dem/merge — composite multiple elevation/mask layers into one DEM.

        Each layer is a dict matching MergeLayerSpec:
          {
            "source":     "local" | "h5_local" | "SRTMGL1" | ...,
            "dim":        300,
            "blend_mode": "base" | "replace" | "blend" | "rivers" | "max" | "min",
            "weight":     1.0,
            "processing": {   # all optional
              "smooth_sigma": 0.0,
              "sharpen": False,
              "clip_min": None, "clip_max": None,
              "normalize": False, "invert": False,
              "extract_rivers": False, "river_max_width_px": 8,
            },
            "label": None,
          }

        Result is stored as self.dem (same shape as fetch_dem() output) so it can
        be used directly with export_obj() and show_dem().
        """
        if not self.bbox:
            raise RuntimeError("Call select() before merge_dem()")
        payload = {
            "bbox":   self.bbox,
            "dim":    self.settings["dem"]["dim"],
            "layers": layers,
        }
        print(f"Merging {len(layers)} DEM layer(s)…")
        r = requests.post(f"{self._base}/api/dem/merge",
                          json=payload, timeout=300)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        self.dem = r.json()
        d = self.dem
        print(f"min={d['min_elevation']:.1f} m  max={d['max_elevation']:.1f} m  "
              f"mean={d['mean_elevation']:.1f} m  shape={d['dimensions']}  "
              f"layers={d.get('layer_count', len(layers))}")
        return self

    def fetch_cities(self) -> "TerrainSession":
        """POST /api/cities — fetch OSM building/road/waterway data for bbox.

        Result stored on self.city_data. Configure via settings['city'].
        """
        if not self.bbox:
            raise RuntimeError("Call select() before fetch_cities()")
        payload = {
            **self.bbox,
            "layers":             self.settings["city"]["layers"],
            "simplify_tolerance": self.settings["city"]["simplify_tolerance"],
            "min_area":           self.settings["city"]["min_area"],
        }
        print("Fetching OSM city data…")
        r = requests.post(f"{self._base}/api/cities",
                          json=payload, timeout=120)
        if r.status_code == 400:
            try:
                msg = r.json().get("error", r.text)
            except Exception:
                msg = r.text
            print(f"WARNING: {msg}")
            print(
                "city_data not populated — use a city-scale region (≤10 km diagonal) to fetch OSM features.")
            self.city_data = None
            return self
        r.raise_for_status()
        self.city_data = r.json()
        n_buildings = len(self.city_data.get(
            "buildings", {}).get("features", []))
        n_roads = len(self.city_data.get("roads",     {}).get("features", []))
        n_waterways = len(self.city_data.get(
            "waterways", {}).get("features", []))
        print(
            f"Fetched {n_buildings} buildings, {n_roads} roads, {n_waterways} waterways")
        return self

    def _export_payload(self, fmt: str) -> dict:
        """Build the unified /api/export request body."""
        exp = copy.copy(self.settings["export"])
        if not exp["label_text"]:
            exp["label_text"] = self.region_name or "terrain"
        return {
            **self.bbox,
            "format": fmt,
            "name":   self.region_name,
            "dem":    self.settings["dem"],
            "export": exp,
            "split":  self.settings["split"],
        }

    def export_obj(self) -> "TerrainSession":
        """POST /api/export (format=obj_split) — generate puzzle OBJ and save to output/.

        fetch_dem() is no longer required before this call — the export endpoint
        derives the DEM from settings, using the disk cache if available.
        """
        payload = self._export_payload("obj_split")
        rows, cols = self.settings["split"]["split_rows"], self.settings["split"]["split_cols"]
        print(f"Generating {rows}x{cols} puzzle split OBJ…")
        r = requests.post(f"{self._base}/api/export",
                          json=payload, timeout=300)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()

        output_dir = _STRM2STL_DIR / "output"
        output_dir.mkdir(exist_ok=True)
        # Prefer the server-supplied filename from Content-Disposition
        cd = r.headers.get("Content-Disposition", "")
        if "filename=" in cd:
            filename = cd.split("filename=")[-1].strip().strip('"')
        else:
            filename = f"{self.region_name}_puzzle_{rows}x{cols}.obj"
        self.obj_path = output_dir / filename
        self.obj_path.write_bytes(r.content)
        print(f"Saved: {self.obj_path}  ({len(r.content) / 1024:.1f} KB)")
        return self

    def verify(self) -> dict:
        """GET /api/export/obj/verify — run mesh health checks and print a table."""
        if not self.region_name:
            raise RuntimeError("Call select() first")
        r = requests.get(f"{self._base}/api/export/obj/verify",
                         params={"name": self.region_name})
        r.raise_for_status()
        info = r.json()

        terrain_pieces = [p for p in info["pieces"]
                          if not p["name"].startswith("Base")]
        base_pieces = [p for p in info["pieces"]
                       if p["name"].startswith("Base")]

        def _print_pieces(pieces):
            for p in pieces:
                wt = "watertight" if p["watertight"] else "HOLES"
                vol = "valid_vol" if p["valid_volume"] else "INVALID_VOL"
                wnd = "" if p["winding_consistent"] else " WINDING!"
                z_ok = "" if abs(
                    p["z_min"]) < 0.001 else f" FLOAT(z_min={p['z_min']})"
                print(
                    f"  {p['name']:<40}  "
                    f"v={p['vertex_count']:>6} f={p['face_count']:>6}  "
                    f"z=[{p['z_min']:.3f},{p['z_max']:.3f}]  "
                    f"holes={p['holes']} nm={p['non_manifold']}  "
                    f"{wt} {vol}{wnd}{z_ok}"
                )

        print(f"Total objects: {info['total']}\n")
        print(
            f"── Terrain pieces ({len(terrain_pieces)}) ──────────────────────────")
        _print_pieces(terrain_pieces)
        print(
            f"\n── Base border pieces ({len(base_pieces)}) ─────────────────────────")
        _print_pieces(base_pieces)
        return info

    def inspect_obj(self) -> dict:
        """GET /api/export/obj/inspect — return object names and piece counts from saved OBJ.

        Lighter than verify() — no mesh health checks, just a fast parse of object names.
        """
        if not self.region_name:
            raise RuntimeError("Call select() first")
        r = requests.get(f"{self._base}/api/export/obj/inspect",
                         params={"name": self.region_name})
        r.raise_for_status()
        info = r.json()
        print(f"Total objects: {info['total']}  "
              f"({info['terrain_count']} terrain + {info['base_count']} base)")
        return info

    def cache_status(self) -> dict:
        """GET /api/cache — return cache stats (file count, size, recent files)."""
        r = requests.get(f"{self._base}/api/cache", timeout=10)
        r.raise_for_status()
        data = r.json()
        print(
            f"Cache: {data['total_cached_files']} files, {data['total_size_mb']:.1f} MB")
        return data

    def clear_cache(self) -> dict:
        """DELETE /api/cache — clear all cached files from the server disk cache."""
        r = requests.delete(f"{self._base}/api/cache", timeout=30)
        r.raise_for_status()
        data = r.json()
        total = sum(c.get("files_deleted", 0) for c in data.get("cleared", []))
        print(f"Cache cleared: {total} files deleted")
        return data

    def composite_city_raster(self, width: Optional[int] = None,
                              height: Optional[int] = None) -> "TerrainSession":
        """POST /api/composite/city-raster — rasterize OSM city data from disk cache.

        Faster than fetch_cities() + cities/raster because it reads the OSM cache
        directly and returns separate normalized arrays per layer (buildings, roads,
        waterways, walls) for client-side weight application.

        Requires fetch_cities() to have been called first (populates the OSM disk cache).
        Result stored on self.city_raster.

        width/height default to settings['dem']['dim'] if not specified.
        """
        if not self.bbox:
            raise RuntimeError("Call select() before composite_city_raster()")
        if self.city_data is None:
            print(
                "Skipping composite_city_raster() — no city data (bbox too large or fetch_cities() not called).")
            return self
        dim = self.settings["dem"]["dim"]
        payload = {
            **self.bbox,
            "width":  width or dim,
            "height": height or dim,
        }
        r = requests.post(f"{self._base}/api/composite/city-raster",
                          json=payload, timeout=60)
        if not r.ok:
            print(f"ERROR {r.status_code}: {r.text}")
        r.raise_for_status()
        self.city_raster = r.json()
        print(f"City raster: {self.city_raster['width']}×{self.city_raster['height']} px, "
              f"layers: buildings, roads, waterways, walls")

        # Apply projection so city layers align with the projected DEM
        if self.settings["projection"]["projection"] != "none":
            layer_names = ["buildings", "roads", "waterways", "walls"]
            h = self.city_raster["height"]
            w = self.city_raster["width"]
            for name in layer_names:
                if name not in self.city_raster:
                    continue
                arr = np.array(
                    self.city_raster[name], dtype=np.float32).reshape(h, w)
                arr = self._apply_projection(arr)
                self.city_raster[name] = arr.ravel().tolist()
            self.city_raster["height"] = arr.shape[0]
            self.city_raster["width"] = arr.shape[1]
            print(f"  → projected to {arr.shape[1]}×{arr.shape[0]} px")

        return self

    def slice(self) -> dict:
        """POST /api/export/slice — slice all terrain+base pairs with PrusaSlicer.

        Configure via settings['slicer']['slicer_config'] and settings['slicer']['output_subdir'].
        """
        if not self.region_name:
            raise RuntimeError("Call select() first")
        n_pairs = self.settings["split"]["split_rows"] * \
            self.settings["split"]["split_cols"]
        slicer_config = self.settings["slicer"]["slicer_config"]
        print(f"Slicing {n_pairs} terrain+base pairs with {slicer_config} …")
        payload = {
            "name":          self.region_name,
            "slicer_config": slicer_config,
            "output_subdir": self.settings["slicer"]["output_subdir"],
        }
        r = requests.post(f"{self._base}/api/export/slice",
                          json=payload, timeout=600)
        r.raise_for_status()
        result = r.json()

        print(f"Sliced : {result['sliced']} / {n_pairs} pairs")
        for fname in result["gcode_files"]:
            print(f"  {fname}")
        if result["errors"]:
            print(f"\nErrors ({len(result['errors'])}):")
            for e in result["errors"]:
                print(f"  pair {e['pair']} ({e['terrain']} + {e['base']}): "
                      f"{e['stderr'][:200]}")
        return result

    def run_all(self) -> "TerrainSession":
        """Run the full pipeline: fetch_dem → export_obj → verify → slice.

        Configure slicer via settings['slicer']['slicer_config'] before calling.
        """
        self.fetch_dem()
        self.show_dem()
        self.export_obj()
        self.verify()
        self.slice()
        return self
