"""/api/model/info and /api/model.glb: build our city model of a region in the background (`model_glb`) and
report its state."""
from __future__ import annotations

import threading
import traceback
from typing import TYPE_CHECKING

import model_glb

if TYPE_CHECKING:
    from regions import Config

def _build_model(cfg: Config) -> None:
    """Background thread body of :func:`api_model_info`."""
    try:
        dem = cfg.dem()
        if dem is None:
            raise ValueError("the model needs a terrain DEM (--dem)")
        meta = model_glb.build_or_load(dem, cfg.dem_path, cfg.region, cfg.model_dir,
                                       footprints=cfg.footprints, osm_path=cfg.osm_path)
        state = {**meta, "status": "ready"}
    except Exception as exc:  # noqa: BLE001 - reported to the page
        traceback.print_exc()
        state = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    with cfg._model_lock:
        cfg._model = state


def api_model_info(cfg: Config, rebuild: bool = False) -> dict:
    """State of the region's model; starts building it (in a thread) when not built yet, or
    again after an error when ``rebuild``. The glb path stays on the server."""
    with cfg._model_lock:
        if cfg._model["status"] == "idle" or (rebuild and cfg._model["status"] == "error"):
            cfg._model = {"status": "building"}
            threading.Thread(target=_build_model, args=(cfg,), daemon=True).start()
        state = dict(cfg._model)
    state.pop("glb_path", None)
    return state
