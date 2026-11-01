"""Background screening of a region's photos (/api/photos/screen): runs `screen.py` and flags non-street-level
photos. Job state lives on the region's `Config`."""
from __future__ import annotations

import threading
import traceback
from pathlib import Path
from typing import TYPE_CHECKING

import screen as photo_screen
from paths import _photo_files, photo_rel
from photos import _set_flag, load_flags
from seeds import pano_seed
from solve import _camera

if TYPE_CHECKING:
    from regions import Config

SCREEN_CPU_MAX = 10   # more photos than this needing SegFormer without a GPU: aspect rule only


def _screen_region(cfg: Config) -> None:
    """Background thread body of :func:`api_photos_screen`: screen every photo of the region
    lacking `<stem>_screen.json` (flagged ones too) and flag the ones that fail a rule."""
    st = cfg.screen_state[cfg.region]
    gpu = False
    try:
        rels = {p: photo_rel(k, r, p) for k, r, p in _photo_files(cfg)}
        todo = [p for p in rels if not photo_screen.screen_path(p).is_file()]
        poses = {p: _pose_geometry(p, cfg) for p in todo}     # path -> (known pose, measure_pose output)
        with cfg.screen_lock:
            st["total"] = len(todo)
        try:
            import torch
            gpu = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 - no torch means no SegFormer either
            gpu = False
        # the aspect rule is free; only the rest needs SegFormer
        pending = []
        for p in todo:
            try:
                res = photo_screen.screen_photo(p, labels=False, geometry=poses[p][1], known_pose=poses[p][0])
            except Exception:  # noqa: BLE001 - an unreadable photo is skipped
                res = None
            if res is None:
                pending.append(p)
            else:
                _screen_flag(cfg, st, rels[p], res)
        if pending and not gpu:
            if len(pending) > SCREEN_CPU_MAX:
                with cfg.screen_lock:
                    st["message"] = f"no CUDA GPU: {len(pending)} photos screened by aspect only"
                pending = []
        if pending:
            from city2stl.resources import wait_for_gpu
            if gpu:
                wait_for_gpu(2)
            for p in pending:
                try:
                    res = photo_screen.screen_photo(p, geometry=poses[p][1], known_pose=poses[p][0])
                except Exception:  # noqa: BLE001
                    traceback.print_exc()
                    res = None
                if res is not None:
                    _screen_flag(cfg, st, rels[p], res)
        with cfg.screen_lock:
            st["state"] = "done"
    except Exception as exc:  # noqa: BLE001 - reported to the page
        traceback.print_exc()
        with cfg.screen_lock:
            st.update(state="error", message=f"{type(exc).__name__}: {exc}")
    finally:
        if gpu:
            try:
                from city2stl.resources import free_gpu_cache
                free_gpu_cache()
            except Exception:  # noqa: BLE001
                pass


def _pose_geometry(p: Path, cfg: Config) -> tuple[bool, dict | None]:
    """(known pose?, `photo_screen.measure_pose` of it). A known pose exempts the photo only from the
    aspect rule; its camera height and nearest visible footprint feed the geometry rules. The geometry is
    None for a cylindrical strip, without a DEM, or when projecting fails."""
    try:
        c = pano_seed(p, cfg)
    except Exception:  # noqa: BLE001 - unknown pose: screen it like any photo
        return False, None
    if c is None:
        return False, None
    dem = cfg.dem()
    if c.get("projection") == "pano_cylindrical" or dem is None:
        return True, None
    try:
        k = min(1.0, 640 / max(c["width"], c["height"]))      # project at screening size
        cam = _camera({**c, "f": c["f"] * k}, round(c["width"] * k), round(c["height"] * k))
        return True, photo_screen.measure_pose(dem, cfg.footprints(), cam)
    except Exception:  # noqa: BLE001
        traceback.print_exc()
        return True, None


def _screen_flag(cfg: Config, st: dict, rel: str, res: dict) -> None:
    """Count one screened photo; flag it when the fresh result has a reason (a photo already
    flagged keeps its flag and reason)."""
    if res.get("reason") and rel not in load_flags(cfg):
        _set_flag(cfg, rel, True, res["reason"], keep=True)
        with cfg.screen_lock:
            st["flagged"].append(rel)
    with cfg.screen_lock:
        st["done"] += 1


def api_photos_screen(cfg: Config, body: dict) -> dict:
    """POST /api/photos/screen: start the region's screening job unless one is running."""
    with cfg.screen_lock:
        if cfg.screen_state.get(cfg.region, {}).get("state") == "running":
            return {"ok": True, "state": "running"}
        cfg.screen_state[cfg.region] = {"state": "running", "done": 0, "total": 0,
                                     "flagged": [], "message": ""}
    threading.Thread(target=_screen_region, args=(cfg,), daemon=True).start()
    return {"ok": True, "state": "running"}


def api_photos_screen_state(cfg: Config) -> dict:
    with cfg.screen_lock:
        st = dict(cfg.screen_state.get(cfg.region) or {"state": "idle", "done": 0, "total": 0,
                                                    "flagged": [], "message": ""})
        st["flagged"] = list(st["flagged"])
    return {"ok": True, **st}
