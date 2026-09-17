"""
core/tile_store.py — the local SRTM/GEBCO GeoTIFF store behind the `local` DEM source.

`geo2stl.tiles.get_tile_files()` globs ``<ocean_root>/*.tif`` from config.json and
caches the result for the life of the process. When that directory is missing —
the drive was unplugged, the folder moved, config.json was copied from another
machine — the glob returns nothing, `stitch_tiles_no_rasterio` returns None, and
the caller substitutes an array of zeros. The request still succeeds, so the only
visible symptom is a perfectly flat map.

This module is the single place that answers "does the `local` source actually
have data?", so the sources list, the diagnostics panel, and the Keys panel all
agree, and the single place that repoints the store without a server restart.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# app/server/core/tile_store.py -> app/server/core -> app/server -> app -> strm2stl
CONFIG_PATH = Path(__file__).resolve().parents[3] / "config.json"


def _read_config() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        logger.exception("config.json could not be parsed")
        return {}


def configured_path() -> str | None:
    """The `ocean_root` value in config.json, or None if it is unset."""
    value = _read_config().get("ocean_root")
    return value or None


def tile_count() -> int:
    """How many .tif tiles the `local` source can currently see.

    Asks geo2stl rather than globbing here, so the number reported is the one
    the fetch will actually use, cache and all.
    """
    try:
        from geo2stl.tiles import get_tile_files
        return len(get_tile_files())
    except Exception:
        # An unreadable store is an unavailable store; that is what the caller
        # needs to know, and the traceback belongs in the log, not the response.
        logger.exception("could not enumerate the local SRTM tile store")
        return 0


def status() -> dict:
    """Everything the UI needs to explain the state of the `local` source."""
    path = configured_path()
    resolved = Path(path).expanduser() if path else None
    exists = bool(resolved and resolved.is_dir())
    count = tile_count() if exists else 0
    if not path:
        note = "No tile folder is configured. Set one in the Keys panel."
    elif not exists:
        note = f"The configured folder does not exist: {path}"
    elif count == 0:
        note = f"No .tif tiles in {path}"
    else:
        note = f"{count} .tif tiles in {path}"
    return {
        "path": path,
        "exists": exists,
        "tile_count": count,
        "available": count > 0,
        "note": note,
    }


def _initial_dir() -> str | None:
    """The deepest existing folder at or above the configured path.

    A picker that opens at the drive root when the saved path is merely stale is
    a picker that makes the user navigate from scratch; walking up to the nearest
    surviving ancestor usually lands within a click or two of the tiles.
    """
    path = configured_path()
    if not path:
        return None
    candidate = Path(path).expanduser()
    for folder in [candidate, *candidate.parents]:
        if folder.is_dir():
            return str(folder)
    return None


def pick_folder() -> dict:
    """Open a native folder dialog on the machine running the server.

    The browser cannot hand back an absolute path — `webkitdirectory` yields
    names relative to the chosen folder and nothing more — so the picker has to
    live server-side. That is sound here only because this is a localhost desktop
    app: the server and the browser are the same machine. Headless installs have
    no display for a dialog, which is reported rather than hung on.
    """
    try:
        import tkinter
        from tkinter import filedialog
    except Exception as exc:
        return {"supported": False, "error": f"No folder dialog available: {exc}"}

    try:
        root = tkinter.Tk()
        root.withdraw()
        # Without this the dialog can open behind the browser window, which looks
        # exactly like the button doing nothing.
        root.attributes("-topmost", True)
        try:
            chosen = filedialog.askdirectory(
                title="Select the folder holding the SRTM .tif tiles",
                initialdir=_initial_dir() or "",
                parent=root,
            )
        finally:
            root.destroy()
    except Exception as exc:
        logger.exception("folder dialog failed")
        return {"supported": False, "error": f"Could not open a folder dialog: {exc}"}

    if not chosen:
        return {"supported": True, "cancelled": True}
    return {"supported": True, "cancelled": False, "path": str(Path(chosen))}


def _reset_geo2stl_cache() -> None:
    """Drop geo2stl's memoized tile list so the next fetch re-globs.

    The list is loaded once into a module global; without this, repointing the
    folder would need a server restart to take effect.
    """
    try:
        from geo2stl import tiles as _tiles
        _tiles._tile_files = None
    except Exception:
        logger.exception("could not reset the geo2stl tile cache")


def set_path(raw_path: str) -> dict:
    """Point `ocean_root` at a new folder and report what is there.

    Writes config.json only when the folder exists and holds at least one .tif,
    so a typo cannot silently replace a working configuration with a broken one.
    Returns the same shape as `status()`, plus `saved`.
    """
    candidate = Path(raw_path.strip().strip('"')).expanduser()
    if not candidate.exists():
        return {"saved": False, "error": f"No such folder: {candidate}"}
    if not candidate.is_dir():
        return {"saved": False, "error": f"Not a folder: {candidate}"}

    found = sorted(candidate.glob("*.tif"))
    if not found:
        # Tiles are often one level down (an extracted archive keeps its own
        # folder), so point at the subfolder rather than rejecting outright.
        nested = {p.parent for p in candidate.glob("*/*.tif")}
        hint = ""
        if nested:
            hint = " Tiles were found in: " + ", ".join(str(p) for p in sorted(nested)[:3])
        return {"saved": False, "error": f"No .tif files directly in {candidate}.{hint}"}

    cfg = _read_config()
    cfg["ocean_root"] = str(candidate).replace("\\", "/") + "/"
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2))
    _reset_geo2stl_cache()
    logger.info("Local SRTM tile store repointed to %s (%d tiles)", candidate, len(found))

    result = status()
    result["saved"] = True
    return result
